"""The bank standard, statically: `lint` reads a bank's run.py with ast (it never imports it),
its CORPUS.jsonl, CODEBOOK.md and RELEASE.json, and returns every Refusal it finds;
`self_check` adds what only an import can see, the grader mutation.

The rules, their limits and the constants they use live in trimwrit/bank.py (RULES, Refusal,
MIN_CASES, HOLDOUT_MIN, MUTATION_SAMPLE, ...). A constant run.py assigns from an expression that
is not a literal (MODEL = producer.DEFAULT_MODEL) cannot be read statically: lint skips the check
that needs its value, and `self_check` (or bank.main) checks the resolved value.

Standard library only, Python 3.9 syntax.
"""
import ast
import contextlib
import datetime
import importlib.util
import itertools
import os
import re
import sys

CONSTANTS = ("MODEL", "EFFORT", "BAR", "LABEL_SOURCE", "FIELDS", "WAIVE", "JOBS")
CANONICAL = ("id", "input", "expected", "why", "split", "label_source")
REQUIRED = ("id", "input", "expected", "why")
SPLIT_VALUES = ("dev", "holdout", "test")
FAMILIES = ("opus", "sonnet", "haiku", "fable")
FORBIDDEN_CALLS = ("write_release", "release_doc", "run_path")
WAIVER_DAYS = 90
DATED_BAR_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2}):.*\bbar\b", re.IGNORECASE | re.MULTILINE)
SECTION2_RE = re.compile(r"^##\s*2\b.*$", re.MULTILINE)
NEXT_SECTION_RE = re.compile(r"^##\s", re.MULTILINE)
RUN_FILE = "run.py"
# what a guard fix tells run.py to hold: the imports it needs, then the guard
GUARD = ("'import sys' and 'from trimwrit import bank' (or the project's own import binding "
         "`bank`) at the top, and at the end 'if __name__ == \"__main__\": "
         "sys.exit(bank.main(__file__))'")
UPPER_SUFFIX = "_max"
MODULE_PREFIX = "_trimwrit_bank_"  # the unique name load_module gives a run.py


def bar_op(metric):
    """'<=' for an upper bound (a metric named *_max), '>=' for every other BAR metric."""
    return "<=" if str(metric).endswith(UPPER_SUFFIX) else ">="
CODEBOOK_FILE = "CODEBOOK.md"


class _Sentinel(object):
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name

    def __bool__(self):
        return False


ABSENT = _Sentinel("ABSENT")            # run.py assigns no such constant at top level
NOT_LITERAL = _Sentinel("NOT_LITERAL")  # run.py assigns it from an expression ast cannot read

_counter = itertools.count()


def _bank():
    from . import bank  # noqa: PLC0415  (lazy: bank.py imports this module at its end)
    return bank


def _today(today):
    if today is None:
        return datetime.datetime.now(datetime.timezone.utc).date()
    if isinstance(today, datetime.datetime):
        return today.date()
    if isinstance(today, str):
        return datetime.date.fromisoformat(today)
    return today


# ------------------------------------------------------------------ reading run.py

def _parse(bank_dir):
    """(tree, error): the ast of run.py, or None and why it cannot be read."""
    path = os.path.join(str(bank_dir), RUN_FILE)
    if not os.path.isfile(path):
        return None, "no run.py"
    try:
        with open(path, encoding="utf-8") as fh:
            return ast.parse(fh.read(), filename=path), None
    except (OSError, ValueError, SyntaxError) as exc:
        return None, "run.py does not parse ({}: {})".format(type(exc).__name__, exc)


def _declared_from(tree):
    out = {name: ABSENT for name in CONSTANTS}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and t.id in out:
                try:
                    out[t.id] = ast.literal_eval(value)
                except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                    out[t.id] = NOT_LITERAL
    return out


def declared(bank_dir):
    """{name: value} of the constants run.py assigns at top level (CONSTANTS), read by ast:
    the literal value, NOT_LITERAL for an expression, ABSENT when not assigned (and for every
    name when run.py is missing or does not parse)."""
    tree, _ = _parse(bank_dir)
    if tree is None:
        return {name: ABSENT for name in CONSTANTS}
    return _declared_from(tree)


def _func_name(call):
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _is_main_guard(node):
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    t = node.test
    if len(t.ops) != 1 or not isinstance(t.ops[0], ast.Eq):
        return False
    sides = [t.left, t.comparators[0]]

    def is_name(n):
        return isinstance(n, ast.Name) and n.id == "__name__"

    def is_main(n):
        return isinstance(n, ast.Constant) and n.value == "__main__"
    return (is_name(sides[0]) and is_main(sides[1])) or (is_main(sides[0]) and is_name(sides[1]))


def _calls_bank_main(node):
    """True when `node` calls bank.main(...) or trimwrit.bank.main(...)."""
    for sub in ast.walk(node):
        if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "main"):
            continue
        v = sub.func.value
        if isinstance(v, ast.Name) and v.id == "bank":
            return True
        if (isinstance(v, ast.Attribute) and v.attr == "bank" and isinstance(v.value, ast.Name)
                and v.value.id == "trimwrit"):
            return True
    return False


def _top_defs(tree):
    return {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _param_names(fn):
    a = fn.args
    return [x.arg for x in list(getattr(a, "posonlyargs", [])) + a.args + a.kwonlyargs]


# ------------------------------------------------------------------ the corpus

def _canonical(row, fields):
    case = dict(row)
    for canon, key in (fields or {}).items():
        if canon != key and key in row:
            case[canon] = row[key]
    return case


def resolve_case(row, fields, split=None, label_source=None):
    """The case a hook receives: the corpus `row` plus its canonical keys (id, input, expected,
    why, split, label_source) copied from the row keys `fields` names, `split` set to the
    resolved split when given, and `label_source` set to the bank's LABEL_SOURCE when the row
    carries none and one is given."""
    case = _canonical(row, fields if isinstance(fields, dict) else None)
    if split is not None:
        case["split"] = split
    if label_source is not None and not case.get("label_source"):
        case["label_source"] = label_source
    return case


def _has_word(text, word):
    """True when `word` stands in `text` as a word ("opus" in "an opus pass", not in "corpus")."""
    return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text) is not None


def _lacks(case, key):
    if key not in case or case[key] is None:
        return True
    return key in ("id", "why") and case[key] == ""


# ------------------------------------------------------------------ the checks

def _fmt_bar_values(value):
    forms = []
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        forms.append(repr(float(value)))
        if isinstance(value, int):
            forms.append(str(value))
    return forms


def _section2(text):
    m = SECTION2_RE.search(text)
    if not m:
        return text
    rest = text[m.end():]
    nxt = NEXT_SECTION_RE.search(rest)
    return rest[:nxt.start()] if nxt else rest


def _bar_problems(bar):
    """[str] of what keeps `bar` from being a standard BAR."""
    if not isinstance(bar, dict) or not bar:
        return ["BAR is {!r}, not a non-empty dict {{metric: threshold}}".format(bar)]
    out = []
    for k, v in bar.items():
        if k == "n_min":
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                out.append("BAR n_min is {!r}, not an int >= 1".format(v))
        elif (not isinstance(k, str) or isinstance(v, bool) or not isinstance(v, (int, float))
              or not 0 <= v <= 1):
            out.append("BAR {!r}: {!r} is not a threshold in [0, 1]".format(k, v))
    if not [k for k in bar if k != "n_min"]:
        out.append("BAR names no metric besides n_min")
    return out


class _Lint(object):
    def __init__(self, bank_dir, today, resolved):
        self.bank_dir = str(bank_dir)
        self.run = os.path.join(self.bank_dir, RUN_FILE)
        self.today = _today(today)
        self.resolved = resolved or {}
        self.out = []
        self.cases = None      # canonical rows when the corpus reads and FIELDS is known
        self.typed = False     # True when every row carries its canonical keys

    def refuse(self, rule, what, fix):
        self.out.append(_bank().Refusal(rule, self.bank_dir, what, fix))

    # --- run.py

    def check_runner(self, tree, err):
        rel = self.run
        if tree is None:
            self.refuse("bespoke-runner", err,
                        "write {} declaring MODEL, EFFORT, BAR, LABEL_SOURCE, answer(case, "
                        "model=MODEL, effort=EFFORT), grade(case, got), with {} "
                        "(trimwrit bank new lays one out)".format(rel, GUARD))
            return None
        defs = _top_defs(tree)
        if "main" in defs:
            self.refuse("bespoke-runner",
                        "run.py defines its own main() at line {}".format(defs["main"].lineno),
                        "delete def main from {} and give it {}".format(rel, GUARD))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "argparse"
                                                    for a in node.names):
                self.refuse("bespoke-runner",
                            "run.py imports argparse at line {}".format(node.lineno),
                            "delete the argparse import from {}: bank.main(__file__) parses "
                            "the flags".format(rel))
            elif (isinstance(node, ast.ImportFrom) and node.module
                  and node.module.split(".")[0] == "argparse"):
                self.refuse("bespoke-runner",
                            "run.py imports from argparse at line {}".format(node.lineno),
                            "delete the argparse import from {}: bank.main(__file__) parses "
                            "the flags".format(rel))
            elif isinstance(node, ast.Call) and _func_name(node) in FORBIDDEN_CALLS:
                self.refuse("bespoke-runner",
                            "run.py calls {}() at line {}".format(_func_name(node), node.lineno),
                            "delete the {}() call from {}: bank.main(__file__) writes the runs "
                            "and the release".format(_func_name(node), rel))
        guards = [n for n in tree.body if _is_main_guard(n)]
        if not any(_calls_bank_main(g) for g in guards):
            self.refuse("bespoke-runner",
                        "run.py has no top level 'if __name__ == \"__main__\":' calling "
                        "bank.main(...)",
                        "give {} {}".format(rel, GUARD))
        for hook, line in (("answer", "def answer(case, model=MODEL, effort=EFFORT):"),
                           ("grade", "def grade(case, got):")):
            if hook not in defs:
                self.refuse("bespoke-runner", "run.py defines no top level {}()".format(hook),
                            "add '{}' to {}".format(line, rel))
        return defs

    def value(self, values, name):
        v = values[name]
        if v is NOT_LITERAL and name in self.resolved:
            return self.resolved[name]
        return v

    def check_model(self, values, defs):
        rel = self.run
        model = self.value(values, "MODEL")
        effort = self.value(values, "EFFORT")
        allowed = _bank().EFFORTS + ("model-default",)
        if model is ABSENT:
            self.refuse("model-not-pinned", "run.py declares no MODEL",
                        "add 'MODEL = None' to {} for a deterministic bank, or 'MODEL = "
                        "\"<full model id, e.g. claude-sonnet-5-5>\"'".format(rel))
            return
        if model is not NOT_LITERAL and model is not None:
            if not isinstance(model, str) or not _bank().FULL_ID_RE.match(model) \
                    or "latest" in model:
                self.refuse("model-not-pinned",
                            "MODEL {!r} is not a full model id".format(model),
                            "set 'MODEL = \"<full model id, e.g. claude-sonnet-5-5>\"' in {}: "
                            "an alias or a latest pointer moves under the bank".format(rel))
            if effort is not NOT_LITERAL and (effort is ABSENT or effort is None
                                              or effort not in allowed):
                self.refuse("model-not-pinned",
                            "EFFORT {} with MODEL {!r}".format(
                                "absent" if effort is ABSENT else repr(effort), model),
                            "set 'EFFORT = \"<one of {}>\"' in {}".format(
                                ", ".join(allowed), rel))
        if model is None and effort is not ABSENT and effort is not None \
                and effort is not NOT_LITERAL:
            self.refuse("model-not-pinned",
                        "EFFORT {!r} with MODEL None: a deterministic bank has no effort"
                        .format(effort),
                        "set 'EFFORT = None' in {}".format(rel))
        if model is not None and defs is not None and "answer" in defs \
                and "model" not in _param_names(defs["answer"]):
            self.refuse("model-not-pinned",
                        "answer() takes no `model` parameter, so the probes cannot change it",
                        "write 'def answer(case, model=MODEL, effort=EFFORT):' in {} and call "
                        "the producer with that model".format(rel))

    # --- the corpus

    def check_corpus(self, values):
        b = _bank()
        path = os.path.join(self.bank_dir, b.CORPUS)
        fix_rows = "give every line of {} the keys id, input, expected and why (or map them " \
                   "with FIELDS in {})".format(path, self.run)
        if not os.path.isfile(path):
            self.refuse("corpus-untyped", "no CORPUS.jsonl",
                        "write {} with one JSON object per line: id, input, expected, why (or "
                        "map another corpus's keys with FIELDS in {})".format(path, self.run))
            return
        try:
            rows = b.load(path)
        except (OSError, b.BankError) as exc:
            self.refuse("corpus-untyped", "CORPUS.jsonl cannot be read ({})".format(exc),
                        "write {} with one JSON object per line: id, input, expected, why"
                        .format(path))
            return
        n = len(rows)
        if n < b.MIN_CASES:
            self.refuse("corpus-too-small",
                        "{} cases, the standard needs {}".format(n, b.MIN_CASES),
                        "add {} labeled cases to {}, or add 'WAIVE = {{\"corpus-too-small\": "
                        "{{\"since\": \"{}\", \"why\": \"<reason>\"}}}}' to {}".format(
                            b.MIN_CASES - n, path, self.today.isoformat(), self.run))
        fields = self.value(values, "FIELDS")
        if fields is NOT_LITERAL:
            return
        if fields is ABSENT or fields is None:
            fields = {}
        if not isinstance(fields, dict) or any(
                k not in CANONICAL or not isinstance(v, str) for k, v in fields.items()):
            self.refuse("corpus-untyped",
                        "FIELDS {!r} is not {{canonical: row key}} over {}".format(
                            fields, ", ".join(CANONICAL)),
                        "set FIELDS in {} to a dict whose keys are among {} and whose values "
                        "are row keys".format(self.run, ", ".join(CANONICAL)))
            return
        cases = [_canonical(r, fields) for r in rows]
        self.typed = True
        missing = {}
        for i, c in enumerate(cases, 1):
            for key in REQUIRED:
                if _lacks(c, key):
                    missing.setdefault(key, []).append(str(c.get("id", "line {}".format(i))))
        for key, ids in sorted(missing.items()):
            self.typed = False
            self.refuse("corpus-untyped",
                        "{} rows carry no `{}` (first: {})".format(len(ids), key,
                                                                  ", ".join(ids[:5])),
                        fix_rows)
        seen, dups = set(), []
        for c in cases:
            k = repr(c.get("id"))
            if k in seen and c.get("id") not in dups:
                dups.append(c.get("id"))
            seen.add(k)
        if dups:
            self.typed = False
            self.refuse("corpus-untyped",
                        "duplicate ids: {}".format(", ".join(str(x) for x in dups[:5])),
                        "give each line of {} its own id".format(path))
        bad = [str(c.get("id")) for c in cases
               if "split" in c and c["split"] is not None and c["split"] not in SPLIT_VALUES]
        if bad:
            self.typed = False
            self.refuse("corpus-untyped",
                        "{} rows carry a split outside dev, holdout, test (first: {})".format(
                            len(bad), ", ".join(bad[:5])),
                        "set `split` to dev, holdout or test (or drop it) on those lines of {}"
                        .format(path))
        self.cases = cases
        self.check_labels(values, cases)
        self.check_holdout(cases)

    def check_labels(self, values, cases):
        default = self.value(values, "LABEL_SOURCE")
        model = self.value(values, "MODEL")
        if default is NOT_LITERAL:
            return
        if default is ABSENT or not isinstance(default, str) or not default.strip():
            default = None
        unlabeled = [str(c.get("id")) for c in cases
                     if not (isinstance(c.get("label_source"), str) and c["label_source"].strip())]
        if unlabeled and default is None:
            self.refuse("label-by-judged-model",
                        "{} rows carry no label source and run.py declares no LABEL_SOURCE "
                        "(first: {})".format(len(unlabeled), ", ".join(unlabeled[:5])),
                        "add 'LABEL_SOURCE = \"<who labeled the rows>\"' to {}".format(self.run))
        if not isinstance(model, str) or not model:
            return
        family = next((f for f in FAMILIES if _has_word(model.lower(), f)), None)
        sources = {}
        for c in cases:
            src = c.get("label_source")
            if not (isinstance(src, str) and src.strip()):
                src = default
            if src is None:
                continue
            sources.setdefault(src, []).append(str(c.get("id")))
        for src, ids in sorted(sources.items()):
            low = src.lower()
            hit = model.lower() if model.lower() in low else (
                family if family and _has_word(low, family) else None)
            if hit:
                where = ("LABEL_SOURCE in " + self.run) if src == default else (
                    "label_source on those lines of CORPUS.jsonl")
                self.refuse("label-by-judged-model",
                            "label source {!r} names the judged model ({}) on {} rows".format(
                                src, hit, len(ids)),
                            "relabel those rows by a human or another model family and set {} "
                            "to that source".format(where))

    def check_holdout(self, cases):
        b = _bank()
        n = len(cases)
        sp = b.splits(cases)
        held = sum(1 for c in cases if sp.get(c.get("id")) == "holdout")
        need = max(b.HOLDOUT_MIN, -(-n // 5))
        if held < need:
            self.refuse("no-holdout",
                        "{} holdout cases of {}, the standard needs {}".format(held, n, need),
                        "set \"split\": \"holdout\" on {} more lines of {} (or drop `split` "
                        "and let bank.splits derive a quarter), or add 'WAIVE = {{\"no-holdout\": "
                        "{{\"since\": \"{}\", \"why\": \"<reason>\"}}}}' to {}".format(
                            need - held, os.path.join(self.bank_dir, b.CORPUS),
                            self.today.isoformat(), self.run))

    # --- the bar and the release

    def check_bar(self, values):
        b = _bank()
        bar = self.value(values, "BAR")
        book_path = os.path.join(self.bank_dir, CODEBOOK_FILE)
        if bar is NOT_LITERAL:
            return
        if bar is ABSENT:
            self.refuse("bar-moved", "run.py declares no BAR",
                        "add 'BAR = {{\"accuracy\": 0.9}}' (your bar) to {} and the line "
                        "'accuracy >= 0.9' to {} section 2".format(self.run, book_path))
            return
        problems = _bar_problems(bar)
        for p in problems:
            self.refuse("bar-moved", p,
                        "set BAR in {} to {{metric: threshold in [0, 1]}} (n_min: an int >= 1)"
                        .format(self.run))
        if problems:
            return
        try:
            with open(book_path, encoding="utf-8") as fh:
                book = fh.read()
        except OSError:
            self.refuse("bar-moved", "no CODEBOOK.md: the bar is declared nowhere a reader sees",
                        "write {} with a section '## 2. The bar' holding {}".format(
                            book_path, ", ".join("'{} {} {}'".format(k, bar_op(k), repr(float(v)))
                                                 for k, v in bar.items() if k != "n_min")))
            return
        section = _section2(book)
        for k, v in bar.items():
            if k == "n_min":
                continue
            forms = _fmt_bar_values(v)
            op = bar_op(k)
            if not any("{} {} {}".format(k, op, f) in section for f in forms):
                self.refuse("bar-moved",
                            "BAR says {} {} {} but CODEBOOK.md section 2 does not".format(
                                k, op, forms[0]),
                            "write the line '{} {} {}' in {} section 2, and a dated line "
                            "'- {}: bar changed from ... to ..., because ...' under section 3 "
                            "if it moved".format(k, op, forms[0], book_path,
                                                 self.today.isoformat()))
        doc = b.read_release(self.bank_dir)
        if doc is None or doc.get("schema") != 2 or not isinstance(doc.get("bar_sha"), str):
            return
        if b.bar_sha(bar) == doc["bar_sha"]:
            return
        if _dated_bar_line(book, doc.get("at")):
            return
        self.refuse("bar-moved",
                    "BAR {!r} differs from the released bar {!r} and CODEBOOK.md has no dated "
                    "bar line since {}".format(bar, doc.get("bar"), str(doc.get("at"))[:10]),
                    "add a line '- {}: bar changed from {!r} to {!r}, because ...' under {} "
                    "section 3, then rerun python3 {}".format(
                        self.today.isoformat(), doc.get("bar"), bar, book_path, self.run))

    def check_release(self):
        b = _bank()
        doc = b.read_release(self.bank_dir)
        if doc is None or doc.get("schema") != 2:
            return
        now = b.inputs_sha(self.bank_dir)
        if doc.get("inputs_sha") != now:
            self.refuse("corpus-changed-since-release",
                        "the inputs changed since RELEASE.json (released {}, now {})".format(
                            doc.get("inputs_sha"), now),
                        "rerun python3 {} to grade the inputs as they stand".format(self.run))

    # --- the waivers

    def apply_waivers(self, values):
        b = _bank()
        waive = self.value(values, "WAIVE")
        if waive is ABSENT or waive is NOT_LITERAL or waive is None:
            return
        fix_drop = "remove that entry from WAIVE in {}".format(self.run)
        if not isinstance(waive, dict):
            self.refuse("bespoke-runner", "WAIVE is {!r}, not a dict".format(waive),
                        "set 'WAIVE = {{\"corpus-too-small\": {{\"since\": \"{}\", \"why\": "
                        "\"<reason>\"}}}}' in {}, or remove it".format(
                            self.today.isoformat(), self.run))
            return
        holds = set()
        extra = []
        for rule, entry in waive.items():
            if rule not in b.RULES:
                extra.append(b.Refusal(
                    "bespoke-runner", self.bank_dir,
                    "WAIVE names {!r}, which is not a rule of the standard".format(rule),
                    fix_drop))
                continue
            if rule not in b.WAIVABLE_IN_BANK:
                extra.append(b.Refusal(
                    rule, self.bank_dir,
                    "WAIVE names {}, which is not waivable in a bank (only {})".format(
                        rule, ", ".join(b.WAIVABLE_IN_BANK)),
                    fix_drop + " and fix the bank"))
                continue
            why_not = _waiver_problem(entry, self.today)
            if why_not:
                extra.append(b.Refusal(
                    rule, self.bank_dir, "the WAIVE of {} does not hold: {}".format(rule, why_not),
                    "fix the bank and remove that entry from WAIVE in {}, or renew it with "
                    "'\"since\": \"{}\"' and a non-empty why".format(self.run,
                                                                    self.today.isoformat())))
                continue
            holds.add(rule)
        self.out = [r for r in self.out if r.rule not in holds] + extra


def _waiver_problem(entry, today):
    if not isinstance(entry, dict):
        return "{!r} is not {{\"since\": date, \"why\": reason}}".format(entry)
    try:
        since = datetime.date.fromisoformat(entry.get("since"))
    except (TypeError, ValueError):
        return "since {!r} is not an ISO date".format(entry.get("since"))
    why = entry.get("why")
    if not isinstance(why, str) or not why.strip():
        return "why is empty"
    if since > today:
        return "since {} is after today ({}): date a waiver the day it is granted".format(
            since.isoformat(), today.isoformat())
    end = since + datetime.timedelta(days=WAIVER_DAYS)
    if today > end:
        return "since {} expired on {} ({} days)".format(since.isoformat(), end.isoformat(),
                                                        WAIVER_DAYS)
    return None


def _dated_bar_line(book, at):
    """True when `book` holds a dated line mentioning the bar dated on or after `at`'s day."""
    try:
        since = datetime.date.fromisoformat(str(at)[:10])
    except (TypeError, ValueError):
        since = None
    for m in DATED_BAR_RE.finditer(book):
        try:
            d = datetime.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if since is None or d >= since:
            return True
    return False


def _run_lint(bank_dir, today=None, resolved=None):
    L = _Lint(bank_dir, today, resolved)
    tree, err = _parse(bank_dir)
    defs = L.check_runner(tree, err)
    values = _declared_from(tree) if tree is not None else {n: ABSENT for n in CONSTANTS}
    if tree is not None:
        L.check_model(values, defs)
    L.check_corpus(values)
    if tree is not None:
        L.check_bar(values)
    L.check_release()
    if tree is not None:
        L.apply_waivers(values)
    return L


def lint(bank_dir, today=None, resolved=None):
    """[Refusal] of every hole the bank at `bank_dir` shows statically (the rules of
    trimwrit/bank.py), [] when none. Imports nothing of the bank. `today` (a date or ISO string,
    default the UTC date) dates the waivers. `resolved` ({constant: value}) stands in for a
    constant run.py assigns from a non literal expression, as self_check and bank.main resolve
    it by import."""
    return _run_lint(bank_dir, today, resolved).out


# ------------------------------------------------------------------ the self check

def load_module(run_file):
    """Import `run_file` (a bank's run.py) by path under a fresh unique module name, the bank's
    directory first on sys.path, and return the module. Whatever the import raises propagates."""
    run_file = os.path.abspath(str(run_file))
    here = os.path.dirname(run_file)
    if not sys.path or sys.path[0] != here:
        sys.path.insert(0, here)
    b = _bank()
    name = "{}{}_{}".format(MODULE_PREFIX, b.sha(run_file), next(_counter))
    spec = importlib.util.spec_from_file_location(name, run_file)
    if spec is None or spec.loader is None:
        raise ImportError("cannot import {}".format(run_file))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        # compiled from the source, never through __pycache__: a run.py edited within the same
        # second at the same size (a bar 0.9 -> 0.8) would otherwise import its stale bytecode
        with open(run_file, "rb") as fh:
            code = compile(fh.read(), run_file, "exec")
        exec(code, module.__dict__)  # noqa: S102
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _right(result):
    if isinstance(result, dict):
        return result.get("right") is True
    return bool(result)


def _under(path, dirs):
    try:
        real = os.path.realpath(path)
    except (TypeError, ValueError):
        return False
    for d in dirs:
        try:
            if os.path.commonpath([real, d]) == d:
                return True
        except ValueError:  # another drive, or a mix of absolute and relative
            continue
    return False


@contextlib.contextmanager
def isolated_imports(*dirs):
    """Run the body with sys.path restored afterwards and the bank's own modules forgotten: a
    module the body imports is dropped from sys.modules when its file lies under one of `dirs`
    (a bank's directory: its run.py and the siblings it imports by bare name, grading.py), or
    when it is a run.py loaded by load_module (its unique name). Every other module the body
    imports stays (a stdlib or C extension module popped and re-imported later, numpy, can
    misbehave), so the next bank of the process gets its own siblings and the shared ones."""
    path = list(sys.path)
    names = set(sys.modules)
    real = [os.path.realpath(str(d)) for d in dirs]
    try:
        yield
    finally:
        sys.path[:] = path
        for name in [n for n in list(sys.modules) if n not in names]:
            mod = sys.modules.get(name)
            f = getattr(mod, "__file__", None)
            if name.startswith(MODULE_PREFIX) or (isinstance(f, str) and _under(f, real)):
                sys.modules.pop(name, None)


def import_refusal(bank_dir, exc):
    """The bespoke-runner Refusal of a run.py whose import raised `exc`."""
    run_file = os.path.join(str(bank_dir), RUN_FILE)
    return _bank().Refusal("bespoke-runner", str(bank_dir),
                           "run.py raises on import ({}: {})".format(type(exc).__name__, exc),
                           "make {} importable without side effects: move work under "
                           "answer() or the __main__ guard".format(run_file))


def self_check(bank_dir, today=None, module=None):
    """lint(bank_dir) plus what an import sees: run.py imported (`load_module` inside
    `isolated_imports`, or `module` when given), the constants lint could not read checked at
    their resolved values, then the grader mutation on up to MUTATION_SAMPLE cases
    (bank.probe_sample order): grade(case, ideal(case)) must be right, grade(case, mutate(case,
    ideal)) wrong (default ideal: the case's expected; default mutate: bank.default_mutation)."""
    bank_dir = str(bank_dir)
    run_file = os.path.join(bank_dir, RUN_FILE)
    if module is not None:
        return _self_check(bank_dir, today, module)
    if not os.path.isfile(run_file):
        return lint(bank_dir, today)
    with isolated_imports(bank_dir):
        try:
            module = load_module(run_file)
        except Exception as exc:  # noqa: BLE001  (an import that raises is a refusal, not a crash)
            return lint(bank_dir, today) + [import_refusal(bank_dir, exc)]
        return _self_check(bank_dir, today, module)


def _self_check(bank_dir, today, module):
    b = _bank()
    run_file = os.path.join(bank_dir, RUN_FILE)
    vals = declared(bank_dir)
    resolved = {k: getattr(module, k) for k, v in vals.items()
                if v is NOT_LITERAL and hasattr(module, k)}
    L = _run_lint(bank_dir, today, resolved)
    out = L.out
    grade = getattr(module, "grade", None)
    if not callable(grade) or not L.typed or not L.cases:
        return out
    fields = resolved.get("FIELDS", vals["FIELDS"])
    fields = fields if isinstance(fields, dict) else {}
    label = resolved.get("LABEL_SOURCE", vals["LABEL_SOURCE"])
    label = label if isinstance(label, str) else None
    rows = b.load(os.path.join(bank_dir, b.CORPUS))
    canon = [_canonical(r, fields) for r in rows]
    sp = b.splits(canon)
    cases = [resolve_case(r, fields, sp.get(c.get("id")), label) for r, c in zip(rows, canon)]
    ideal_fn = getattr(module, "ideal", None)
    mutate_fn = getattr(module, "mutate", None)

    def ideal_of(case):
        return ideal_fn(case) if callable(ideal_fn) else case["expected"]

    ideals = None
    fix_ideal = ("make grade(case, case['expected']) return True in {}, or define ideal(case) "
                 "returning the right answer".format(run_file))
    fix_wrong = ("make grade() compare got with case['expected'], or define mutate(case, ideal) "
                 "returning a wrong answer the grader must refuse")
    for case in b.probe_sample(cases, b.MUTATION_SAMPLE):
        cid = case.get("id")
        try:
            good = ideal_of(case)
            ok = _right(grade(case, good))
            err = ""
        except Exception as exc:  # noqa: BLE001
            ok, err = False, " ({}: {})".format(type(exc).__name__, exc)
        if not ok:
            out.append(b.Refusal("grader-cannot-fail", bank_dir,
                                 "the grader refuses the expected answer of {}{}".format(cid, err),
                                 fix_ideal))
            continue
        try:
            if callable(mutate_fn):
                wrong = mutate_fn(case, good)
            else:
                if ideals is None:
                    ideals = [ideal_of(c) for c in cases]
                wrong = b.default_mutation(case, good, ideals)
        except Exception as exc:  # noqa: BLE001
            out.append(b.Refusal("grader-cannot-fail", bank_dir,
                                 "no wrong answer could be made for {} ({}: {})".format(
                                     cid, type(exc).__name__, exc), fix_wrong))
            continue
        try:
            accepted = _right(grade(case, wrong))
        except Exception:  # noqa: BLE001  (a grader that raises on a wrong answer refuses it)
            accepted = False
        if accepted:
            out.append(b.Refusal("grader-cannot-fail", bank_dir,
                                 "the grader accepts a wrong answer on {}: {}".format(
                                     cid, repr(wrong)[:80]), fix_wrong))
    return out
