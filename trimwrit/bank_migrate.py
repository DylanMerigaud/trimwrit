"""Moving a bank onto the standard: `migrate`, the codemod of a scaffold shaped run.py, and
`parity`, the replay that shows the old runner and bank.main agree on the same answers.

THE CODEMOD. A run.py laid out by `trimwrit bank new` before the standard holds its own runner:
a top level `def main` parsing argparse flags, writing RUNS/ and RELEASE.json, and a
`if __name__ == "__main__": sys.exit(main())`. `migrate` removes those (and `import argparse`
when nothing else uses it), makes sure of `import sys`, names LABEL_SOURCE when given, sets
EFFORT to None in a deterministic bank (MODEL = None), and appends the canonical tail
`sys.exit(bank.main(__file__))`. It works on the source text by line ranges (ast lineno and
end_lineno): every other line, comments included, stays byte identical.

THE PARITY REPLAY. After the migrated bank ran once on bank.main, `parity` loads the OLD run.py
(a copy kept with `trimwrit bank migrate --legacy-out`), feeds it the answers the new run
recorded instead of asking the producer again, captures the release it would have written, and
compares `passed`, `n` and every BAR metric with the new schema 2 RELEASE.json. Nothing the old
runner writes through the kit reaches the bank (the kit's writers are swapped for the time of the
replay, whatever name the runner reaches them by): its rows go to a temp dir and its release is
captured. A runner that opens RELEASE.json itself is not stopped.

Standard library only, Python 3.9 syntax.
"""
import ast
import contextlib
import io
import itertools
import json
import os
import shutil
import sys
import tempfile

from . import bank_lint

TAIL = '\n\n\nif __name__ == "__main__":\n    sys.exit(bank.main(__file__))\n'
GUARD_LABEL = 'if __name__ == "__main__"'
TOLERANCE = 1e-9
COPY_IGNORE = ("RUNS", "__pycache__", ".pytest_cache")

_counter = itertools.count()


def _bank():
    from . import bank  # noqa: PLC0415  (lazy: bank.py imports this module at its end)
    return bank


# ------------------------------------------------------------------ migrate

def _read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def _start(node):
    """The first line (1 based) of a top level statement, its decorators included."""
    lines = [node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]
    return min(lines)


def _alias_text(a):
    return a.name + (" as " + a.asname if a.asname else "")


def _names_used(tree, skip):
    """The names a Load of the tree reads outside the line ranges `skip` [(first, last)]."""
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and not any(a <= node.lineno <= b for a, b in skip):
            used.add(node.id)
    return used


class _Edits(object):
    """Line edits on a source split in lines: removals, replacements, insertions after a line.
    Lines are 1 based as ast gives them; 0 means before the first line."""

    def __init__(self, lines):
        self.lines = lines
        self.removed = set()
        self.replace = {}   # first line -> (last line, text)
        self.after = {}     # line -> [text]

    def remove(self, first, last, blanks_before=False):
        for i in range(first, last + 1):
            self.removed.add(i)
        i = first - 1
        while blanks_before and i >= 1 and (not self.lines[i - 1].strip() or i in self.removed):
            self.removed.add(i)
            i -= 1

    def put(self, first, last, text):
        self.replace[first] = (last, text)
        for i in range(first + 1, last + 1):
            self.removed.add(i)

    def insert(self, after, text):
        self.after.setdefault(after, []).append(text)

    def text(self):
        out = list(self.after.get(0, []))
        for no, line in enumerate(self.lines, 1):
            if no in self.replace:
                out.append(self.replace[no][1])
            elif no not in self.removed:
                out.append(line)
            out.extend(self.after.get(no, []))
        return "".join(out)


def _migrated(old, label_source):
    """(new text, removed labels) of the run.py source `old`; BankError when it does not parse
    or lacks answer() or grade()."""
    b = _bank()
    try:
        tree = ast.parse(old)
    except (SyntaxError, ValueError) as exc:
        raise b.BankError("run.py does not parse ({}: {})".format(type(exc).__name__, exc))
    defs = bank_lint._top_defs(tree)
    for hook in ("answer", "grade"):
        if hook not in defs:
            raise b.BankError("run.py defines no top level {}(): migrate reads a scaffold "
                              "shaped run.py, write the standard shape by hand".format(hook))
    nl = "\r\n" if "\r\n" in old else "\n"
    lines = old.splitlines(True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += nl

    def rest_of(node):
        """What follows `node` on its last line (a trailing comment, the line ending)."""
        tail = lines[node.end_lineno - 1][node.end_col_offset:]
        return tail if tail.strip() else nl
    ed = _Edits(lines)
    labels = []   # (line, label) in source order
    blocks = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main":
            blocks.append((_start(node), node.end_lineno, "def main"))
        elif bank_lint._is_main_guard(node):
            blocks.append((node.lineno, node.end_lineno, GUARD_LABEL))
    skip = [(a, z) for a, z, _ in blocks]
    used = _names_used(tree, skip)
    for node in tree.body:
        if isinstance(node, ast.Import):
            parse = [a for a in node.names if a.name.split(".")[0] == "argparse"]
            if not parse or any((a.asname or a.name.split(".")[0]) in used for a in parse):
                continue
            rest = [a for a in node.names if a not in parse]
            if rest:
                ed.put(node.lineno, node.end_lineno,
                       "import " + ", ".join(_alias_text(a) for a in rest) + rest_of(node))
            else:
                ed.remove(node.lineno, node.end_lineno)
            labels.append((node.lineno, "import argparse"))
        elif (isinstance(node, ast.ImportFrom) and node.module
              and node.module.split(".")[0] == "argparse" and node.level == 0):
            if any((a.asname or a.name) in used for a in node.names):
                continue
            ed.remove(node.lineno, node.end_lineno)
            labels.append((node.lineno, "from argparse import"))
    for first, last, label in blocks:
        ed.remove(first, last, blanks_before=True)
        labels.append((first, label))
    values = bank_lint._declared_from(tree)
    assigns = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    assigns[t.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigns[node.target.id] = node
    effort = values["EFFORT"]
    if (values["MODEL"] is None and "EFFORT" in assigns and effort is not None
            and effort is not bank_lint.ABSENT and effort is not bank_lint.NOT_LITERAL):
        node = assigns["EFFORT"]
        ed.put(node.lineno, node.end_lineno, "EFFORT = None" + rest_of(node))
        labels.append((node.lineno, "EFFORT = {!r} (MODEL is None)".format(effort)))
    has_sys = any(isinstance(n, ast.Import) and any(a.name == "sys" and not a.asname
                                                    for a in n.names) for n in tree.body)
    if not has_sys:
        ed.insert(_import_anchor(tree, lines), "import sys" + nl)
    if label_source is not None and "LABEL_SOURCE" not in assigns:
        line = "LABEL_SOURCE = {!r}".format(label_source) + nl
        if "BAR" in assigns:
            ed.insert(assigns["BAR"].end_lineno, line)
        else:
            ed.insert(_start(defs["answer"]) - 1, line + nl + nl)
    new = ed.text().rstrip() + TAIL.replace("\n", nl)
    return new, [label for _, label in sorted(labels)]


def _import_anchor(tree, lines):
    """The line after which `import sys` goes: the end of the last plain import of the leading
    imports, else the end of the module docstring, else below the leading comment lines (a
    shebang, a coding line), else 0 (the top)."""
    anchor = 0
    while anchor < len(lines) and lines[anchor].startswith("#"):
        anchor += 1
    comments = anchor
    for i, node in enumerate(tree.body):
        if i == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            anchor = node.end_lineno
            continue
        if isinstance(node, ast.Import):
            anchor = node.end_lineno
        elif isinstance(node, ast.ImportFrom) and node.module == "__future__":
            anchor = node.end_lineno
        elif not isinstance(node, ast.ImportFrom):
            break
    return max(anchor, comments)


def _main_references(text):
    """The lines of `text` that still read the name `main` (the removed runner)."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    return sorted({n.lineno for n in ast.walk(tree)
                   if isinstance(n, ast.Name) and n.id == "main"})


def _swap(value, old, new):
    return value.replace(old, new) if isinstance(value, str) else value


def _lint_text(bank_dir, new):
    """[as_dict] of bank.lint on the bank as it would stand with run.py = `new`, read on a
    temp copy (RUNS and caches left out), the paths given back as the bank's."""
    b = _bank()
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, os.path.basename(os.path.abspath(bank_dir)) or "bank")
        shutil.copytree(bank_dir, copy, symlinks=True,
                        ignore=shutil.ignore_patterns(*COPY_IGNORE))
        with open(os.path.join(copy, bank_lint.RUN_FILE), "w", encoding="utf-8",
                  newline="") as fh:
            fh.write(new)
        out = []
        for r in b.lint(copy):
            out.append({k: _swap(v, copy, bank_dir) for k, v in r.as_dict().items()})
        return out


def migrate(bank_dir, label_source=None, write=False):
    """The codemod of the scaffold shaped run.py of `bank_dir` into the standard shape (the
    module docstring says what it removes and adds). Returns {"bank", "lines_before",
    "lines_after", "removed": [labels, in source order], "old_text", "new_text",
    "refusals_left": [as_dict of bank.lint on the new text]}. With write=True, writes run.py.
    BankError when run.py is missing, does not parse, or has no answer() or grade()."""
    b = _bank()
    bank_dir = str(bank_dir)
    path = os.path.join(bank_dir, bank_lint.RUN_FILE)
    try:
        old = _read(path)
    except OSError as exc:
        raise b.BankError("{}: no run.py to migrate ({})".format(bank_dir, exc))
    try:
        new, removed = _migrated(old, label_source)
    except b.BankError as exc:
        raise b.BankError("{}: {}".format(path, exc))
    if write:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)
        left = [r.as_dict() for r in b.lint(bank_dir)]
    else:
        left = _lint_text(bank_dir, new)
    for line in _main_references(new):
        left.append(b.Refusal(
            "bespoke-runner", bank_dir,
            "main is still referenced at line {} of the migrated run.py".format(line),
            "remove that use of main() from {}: bank.main(__file__) is the whole runner"
            .format(path)).as_dict())
    return {"bank": bank_dir, "lines_before": len(old.splitlines()),
            "lines_after": len(new.splitlines()), "removed": removed, "old_text": old,
            "new_text": new, "refusals_left": left}


# ------------------------------------------------------------------ parity

class _KitProxy(object):
    """The kit as the old runner sees it during a replay: every name of trimwrit.bank, except
    the writers (their release captured, their rows sent to a temp dir) and the probes (none)."""

    def __init__(self, real, tmp, captured, release_doc):
        self._real = real
        self._tmp = tmp
        self._captured = captured
        self._release_doc = release_doc

    def __getattr__(self, name):
        return getattr(self._real, name)

    def release_doc(self, bank, **kw):
        doc = self._release_doc(bank, **kw)
        self._captured.append(doc)
        return doc

    def write_release(self, bank, **kw):
        return self.release_doc(bank, **kw)

    def run_path(self, bank, stamp, ext=".jsonl"):
        return os.path.join(self._tmp, "{}{}".format(stamp, ext))

    def smaller(self, model):
        return None

    def cheaper_effort(self, effort):
        return None


def _id_key(value):
    return json.dumps(value, sort_keys=True, default=repr)


def _replay(bank_dir, legacy_run, answers):
    """(captured release doc or None, error text or None, stderr of the old runner)."""
    b = _bank()
    run_file = os.path.join(os.path.abspath(bank_dir), bank_lint.RUN_FILE)
    captured = []
    out, err = io.StringIO(), io.StringIO()

    def lookup(case, *args, **kwargs):
        key = _id_key(case.get("id"))
        if key not in answers:
            raise b.BankError("the new run recorded no answer for case {!r}".format(
                case.get("id")))
        return answers[key]

    with tempfile.TemporaryDirectory() as tmp, \
            bank_lint.isolated_imports(os.path.abspath(bank_dir)), \
            _kit_patched(b, tmp, captured) as proxy:
        sys.path.insert(0, os.path.abspath(bank_dir))
        name = "{}legacy_{}_{}".format(bank_lint.MODULE_PREFIX, b.sha(str(legacy_run)),
                                       next(_counter))
        module = type(sys)(name)
        # the old runner reads its bank through HERE = dirname(__file__): it is replayed as
        # the bank's run.py wherever its copy lives
        module.__file__ = run_file
        sys.modules[name] = module
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = compile(_read(str(legacy_run)), str(legacy_run), "exec")
                try:
                    exec(code, module.__dict__)  # noqa: S102
                except SystemExit as exc:
                    return None, "the old runner exited at import (SystemExit: {!r}){}".format(
                        exc.code, _last_line(err)), err.getvalue()
                module.answer = lookup
                module.bank = proxy
                try:
                    module.main([])
                except SystemExit:
                    pass
        except Exception as exc:  # noqa: BLE001  (an old runner that raises is a parity miss)
            return None, "the old runner raised {}: {}".format(type(exc).__name__, exc), \
                err.getvalue()
    if not captured:
        return None, "the old runner wrote no release (no write_release or release_doc call)" \
            + _last_line(err), err.getvalue()
    return captured[-1], None, err.getvalue()


def _last_line(err):
    lines = err.getvalue().strip().splitlines()
    return "; its last stderr line: {}".format(lines[-1][:200]) if lines else ""


KIT_PATCHED = ("write_release", "release_doc", "run_path", "smaller", "cheaper_effort")


@contextlib.contextmanager
def _kit_patched(b, tmp, captured):
    """The proxy, with the real kit's writers and probes swapped for the proxy's for the time of
    the replay: a runner reaching the kit by another name (import trimwrit.bank as kit, from
    trimwrit.bank import write_release) writes nothing in the bank either."""
    saved = {name: getattr(b, name) for name in KIT_PATCHED}
    proxy = _KitProxy(b, tmp, captured, saved["release_doc"])
    try:
        for name in KIT_PATCHED:
            setattr(b, name, getattr(proxy, name))
        yield proxy
    finally:
        for name, fn in saved.items():
            setattr(b, name, fn)


def _same(a, c):
    if a is None or c is None:
        return a is None and c is None
    if isinstance(a, bool) or isinstance(c, bool):
        return a is c
    if isinstance(a, (int, float)) and isinstance(c, (int, float)):
        return abs(float(a) - float(c)) <= TOLERANCE
    return a == c


def parity(bank_dir, legacy_run, rows_path=None):
    """Replay the OLD runner `legacy_run` (a path) on the answers of the bank's last release
    run (rows of `rows_path`, default RUNS/<release run>.jsonl: id -> got) and compare it with
    the schema 2 RELEASE.json: parity is True when `passed`, `n` and every BAR metric (not n_min)
    agree (numbers within 1e-9, None equal None). Returns {"parity", "old": {passed, n,
    metrics}, "new": {...}, "diffs": [str]}. BankError when the bank has no schema 2 release or
    the rows do not read."""
    b = _bank()
    bank_dir = str(bank_dir)
    doc = b.read_release(bank_dir)
    if doc is None or doc.get("schema") != 2:
        raise b.BankError("{}: no schema 2 RELEASE.json: run python3 {} first".format(
            bank_dir, os.path.join(bank_dir, bank_lint.RUN_FILE)))
    if rows_path is None:
        if not doc.get("run"):
            raise b.BankError("{}: RELEASE.json names no run".format(bank_dir))
        rows_path = os.path.join(bank_dir, b.RUNS, doc["run"] + ".jsonl")
    try:
        rows = b.load(str(rows_path))
    except OSError as exc:
        raise b.BankError("{}: the rows do not read ({})".format(rows_path, exc))
    answers = {_id_key(r.get("id")): r.get("got") for r in rows}
    bar = doc.get("bar") if isinstance(doc.get("bar"), dict) else {}
    metrics = [k for k in bar if k != "n_min"]
    new = {"passed": doc.get("passed"), "n": doc.get("n")}
    new.update({m: doc.get(m) for m in metrics})
    old_doc, error, _ = _replay(bank_dir, legacy_run, answers)
    if error:
        return {"parity": False, "old": {}, "new": new, "diffs": [error]}
    old = {"passed": old_doc.get("passed"), "n": old_doc.get("n")}
    old.update({m: old_doc.get(m) for m in metrics})
    diffs = ["{}: old {!r}, new {!r}".format(k, old[k], new[k])
             for k in new if not _same(old[k], new[k])]
    return {"parity": not diffs, "old": old, "new": new, "diffs": diffs}
