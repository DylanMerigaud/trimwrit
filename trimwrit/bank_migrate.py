"""Moving a bank onto the standard: `migrate`, the codemod of a scaffold shaped run.py, and
`parity`, the replay that shows the old runner and bank.main agree on the same answers.

THE CODEMOD. A run.py laid out by `trimwrit bank new` before the standard holds its own runner:
a top level `def main` parsing argparse flags, writing RUNS/ and RELEASE.json, and a
`if __name__ == "__main__": sys.exit(main())`. `migrate` removes those (and `import argparse`
when nothing else uses it), makes sure of `import sys`, names LABEL_SOURCE when given, sets
EFFORT to None in a deterministic bank (MODEL = None), and appends the canonical tail
`sys.exit(bank.main(__file__))`. A model bank with no EFFORT gets EFFORT = "model-default" (what
`claude -p` runs without --effort) when no python file of the bank names an effort; otherwise
EFFORT stays absent and its refusal is left for a person. It works on the source text by line ranges (ast lineno and
end_lineno): every other line, comments included, stays byte identical.

ROW FIELDS (2026-11-25, growth-cockpit autofix bank-migrate:career/recruitroast-evals, 18 banks
hand_look'd on one cause). The old main() built each row itself (`rows.append({"id": ..., "got": ...,
"right": ..., "expected_pass": case["expected"]["pass"]})`), and a score() read the extra fields off
it; bank.main writes only id, split, got, right, cached, usage and copies the keys grade() returns
besides "right", so score() died on a KeyError. The codemod finds every row field a kept function
reads that main() wrote, and when its value is built from `case` and the module's own names it
appends a grade() that returns bank.Graded(<the bank's grade>, {field: value}) (truthy exactly when
right, so a test's `assert run.grade(...)` still reads a bool). A field built from anything else
main() computed (a loop counter, the answer) is a bespoke-runner refusal naming it: left to a person.

A TWO PART ANSWER (2026-11-26, growth-cockpit autofix career/recruitermail-evals, nine banks of one
shape). answer() returns (verdict, failed checks) and the old main() split them (`got, fails =
answer(case)`), kept the checks in the row and graded the verdict. bank.main hands grade() the whole
return and keeps it as the row's got. When every return of answer() is a two member tuple and the old
main() does nothing else with the two (one unpack, `grade(case, got)`, got and the checks written to
the row as they are, no kept function reading the row's got), the codemod appends a grade() that
splits them: the bank's own grade reads the verdict and the row keeps the checks under the old key.
A bare verdict (a test, the self check) is graded as it is. Any other unpacking stays a refusal.

ONE AT A TIME (same day, same banks). The old main() answered its cases one after the other,
bank.main runs 4 threads: five of the nine doors load their producer module on first use, and on
threads up to 348 of 349 cases raised on a half loaded module. A deterministic bank (MODEL = None)
with no JOBS gets JOBS = 1.

N (same day, same banks). The standard runner writes `n` itself and refuses a score() returning it:
a score() whose `n` is len(rows) is wrapped to leave it out.

THE PARITY REPLAY. After the migrated bank ran once on bank.main, `parity` loads the OLD run.py
(a copy kept with `trimwrit bank migrate --legacy-out`), feeds it the answers the new run
recorded instead of asking the producer again, captures the release it would have written, and
compares `passed`, `n` and every BAR metric with the new schema 2 RELEASE.json. Nothing the old
runner writes through the kit reaches the bank (the kit's writers are swapped for the time of the
replay, whatever name the runner reaches them by): its rows go to a temp dir and its release is
captured. A runner that opens RELEASE.json itself is not stopped.

THE REHEARSAL (2026-11-26, growth-cockpit autofix bank-migrate:career/outreach-draft-evals, the
fifth shape the codemod did not foresee, found each time after a paid run). `rehearse` runs BEFORE
the first model call of a migration: the answers the old runner recorded (the rows of the release's
run) are fed to the OLD runner and to bank.main on the NEW run.py, and the two releases are
compared value by value (every key the old runner wrote, bar the envelope's own stamps) with each
case's verdict. A crash, a refusal of the standard, a lost key or a changed value is a miss: the
migration stops there and the old runner keeps serving. Nothing is written in the bank and no model
is called by the rehearsal itself (answer() is replaced; a caller that wants a producer's other
calls refused too runs it in a process whose model door is shut).

Standard library only, Python 3.9 syntax.
"""
import ast
import builtins
import contextlib
import io
import itertools
import json
import os
import re
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


ROW_STANDARD = frozenset(("id", "split", "got", "right", "cached", "usage", "error"))
HOOKS = frozenset(("main", "answer", "grade"))
GRADE_WRAP = "_grade_verdict"
SCORE_WRAP = "_score_with_n"


def _module_names(tree):
    """The names bound at the top level of the module (defs, classes, assignments, imports)."""
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            names.update((a.asname or a.name.split(".")[0]) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update((a.asname or a.name) for a in node.names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                names.update(n.id for n in ast.walk(t) if isinstance(n, ast.Name))
    return names


def _slice_key(node):
    """The constant a Subscript indexes with (a str), else None; Python 3.8 wraps it in Index."""
    k = node.slice
    if hasattr(ast, "Index") and isinstance(k, getattr(ast, "Index")):
        k = k.value
    return k.value if isinstance(k, ast.Constant) else None


def _keys_read(tree):
    """The string keys the top level functions other than main, answer and grade read off a row:
    `r["k"]`, `r.get("k")`, `r.pop("k")` (what score() and its helpers do)."""
    keys = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name in HOOKS:
            continue
        for n in ast.walk(node):
            if isinstance(n, ast.Subscript):
                k = _slice_key(n)
                if isinstance(k, str):
                    keys.add(k)
            elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr in ("get", "pop", "setdefault") and n.args
                  and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                keys.add(n.args[0].value)
    return keys


def _own_nodes(func):
    """The nodes of a function body, those of a nested function, lambda or class left out."""
    stack = list(func.body)
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                                 ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(node))


def _calls(node, name):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == name]


def _is_name(node, name):
    return isinstance(node, ast.Name) and node.id == name


def _is_pair(node):
    return (isinstance(node, ast.Tuple) and len(node.elts) == 2
            and not any(isinstance(e, ast.Starred) for e in node.elts))


def _is_answer_of_case(node):
    return (isinstance(node, ast.Call) and _is_name(node.func, "answer") and not node.keywords
            and len(node.args) == 1 and _is_name(node.args[0], "case"))


def _unpacked_names(assign):
    """(first, second) of `a, b = answer(case)`, or of the same behind an offline arm
    (`a, b = (x, y) if flag else answer(case)`), else None."""
    t = assign.targets
    if len(t) != 1 or not _is_pair(t[0]) or not all(isinstance(e, ast.Name) for e in t[0].elts):
        return None
    value = assign.value
    if isinstance(value, ast.IfExp):
        arms = [value.body, value.orelse]
        asked = [a for a in arms if _is_answer_of_case(a)]
        if len(asked) != 1 or not all(_is_pair(a) for a in arms if a is not asked[0]):
            return None
    elif not _is_answer_of_case(value):
        return None
    return t[0].elts[0].id, t[0].elts[1].id


def _row_literals(main):
    """[{key: value node}] of the dict literals of main() with a "right" key: its rows."""
    out = []
    for node in ast.walk(main):
        if isinstance(node, ast.Dict):
            row = {k.value: v for k, v in zip(node.keys, node.values)
                   if isinstance(k, ast.Constant)}
            if "right" in row:
                out.append(row)
    return out


def _two_part(tree):
    """The row key the old main() kept the second member of answer()'s return under, when the
    bank is the two part shape the module docstring says, else None."""
    defs = bank_lint._top_defs(tree)
    main, answer = defs.get("main"), defs.get("answer")
    if main is None or answer is None:
        return None
    returns = [n for n in _own_nodes(answer) if isinstance(n, ast.Return)]
    if not returns or not all(_is_pair(r.value) for r in returns):
        return None
    unpacks = [_unpacked_names(n) for n in ast.walk(main)
               if isinstance(n, ast.Assign) and _calls(n.value, "answer")]
    if len(unpacks) != 1 or unpacks[0] is None or len(_calls(main, "answer")) != 1:
        return None
    verdict, checks = unpacks[0]
    stores = [n.id for n in ast.walk(main)
              if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]
    if verdict == checks or stores.count(verdict) != 1 or stores.count(checks) != 1:
        return None

    def graded(call):
        return (len(call.args) == 2 and not call.keywords and _is_name(call.args[0], "case")
                and _is_name(call.args[1], verdict))
    grades = _calls(main, "grade")
    rows = _row_literals(main)
    if not rows or not grades or not all(graded(c) for c in grades):
        return None
    kept = set()
    for row in rows:
        right = row["right"]
        if isinstance(right, ast.Call) and _is_name(right.func, "bool") and len(right.args) == 1:
            right = right.args[0]
        keys = [k for k, v in row.items() if _is_name(v, checks)]
        if "got" not in row or not _is_name(row["got"], verdict) or len(keys) != 1 \
                or not (isinstance(right, ast.Call) and _is_name(right.func, "grade")):
            return None
        kept.add(keys[0])
    if len(kept) != 1 or kept & ROW_STANDARD or "got" in _keys_read(tree):
        return None
    return kept.pop()


def _score_returns_n(tree):
    """True when score() returns a dict whose "n" is len() of its rows, the key bank.main writes
    itself, and only main() calls score() (so nothing kept reads that key off it)."""
    defs = bank_lint._top_defs(tree)
    score = defs.get("score")
    if score is None or not score.args.args:
        return False
    rows = score.args.args[0].arg

    def length(node):
        return (isinstance(node, ast.Call) and _is_name(node.func, "len")
                and len(node.args) == 1 and _is_name(node.args[0], rows))
    bound = {}
    for node in _own_nodes(score):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    bound.setdefault(t.id, []).append(node.value)
    values = [v for node in _own_nodes(score) if isinstance(node, ast.Dict)
              for k, v in zip(node.keys, node.values)
              if isinstance(k, ast.Constant) and k.value == "n"]
    if not values or not all(
            length(v) or (isinstance(v, ast.Name) and len(bound.get(v.id, ())) == 1
                          and length(bound[v.id][0])) for v in values):
        return False
    return not any(_calls(node, "score") for node in tree.body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name not in ("main", "score"))


def _row_fields(tree, old, checks=None):
    """([(key, source text of its value)], [problems]): the row fields the old main() wrote that
    a function kept by the standard runner (score(), a helper of it) reads, and that the standard
    runner would not write: it writes id, split, got, right, cached, usage, and copies the rest
    from what grade() returns. A field whose value is built from `case` and the module's own
    names alone can ride grade() (the value does not depend on the answer); a field built from
    anything else main() computed (the answer's second member, a loop counter) cannot, and is a
    problem: a person writes that bank by hand. A row dict is any dict literal of main() with a
    "right" key (`rows.append({...})`, `row = {...}`, `rows = [{...} for ...]`)."""
    main = _bank_lint_defs(tree).get("main")
    if main is None:
        return [], []
    read = _keys_read(tree) - ROW_STANDARD - {checks}
    stored = [] if checks is None or not _grade_called_elsewhere(tree) else [
        "a function kept by the standard runner calls grade(), whose return would turn into a "
        "verdict dict"]
    if not read:
        return [], stored
    known = _module_names(tree) | set(dir(builtins)) | {"case"}
    found = {}
    problems = []
    for node in ast.walk(main):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value if isinstance(k, ast.Constant) else None for k in node.keys]
        if "right" not in keys:
            continue
        for k, v in zip(keys, node.values):
            if k in read:
                src = ast.get_source_segment(old, v)
                free = sorted({n.id for n in ast.walk(v) if isinstance(n, ast.Name)} - known)
                if src is None or free:
                    problems.append("score() reads the row field {!r}, which main() builds from "
                                    "{}: the standard runner cannot rebuild it from the case"
                                    .format(k, ", ".join(free) or "an expression it cannot read"))
                elif found.get(k, (None, src))[1] != src:
                    problems.append("main() builds the row field {!r} two ways".format(k))
                else:
                    found[k] = (k, src)
    for k in sorted(read):
        if k not in found and not any("{!r}".format(k) in p for p in problems):
            if _row_key_written(main, k):
                problems.append("score() reads the row field {!r}, which main() writes outside "
                                "a row literal".format(k))
    if stored or (found and _grade_called_elsewhere(tree)):
        problems.append("a function kept by the standard runner calls grade(), whose return "
                        "would turn into a verdict dict")
    return [found[k] for k in sorted(found)], problems


def _main_shape_problems(tree):
    """What the old main() did around answer() and score() that bank.main does not: a score()
    that asks for more than `rows` (bank.main calls score(rows)), and an answer() whose return
    main() unpacked (`got, fails = answer(case)`: bank.main hands grade() the WHOLE return, so a
    grade written for the first member reads the tuple and grades every case wrong)."""
    out = []
    defs = bank_lint._top_defs(tree)
    score = defs.get("score")
    if score is not None:
        a = score.args
        required = [x.arg for x in list(getattr(a, "posonlyargs", [])) + a.args][
            :len(getattr(a, "posonlyargs", [])) + len(a.args) - len(a.defaults)]
        required += [x.arg for x, d in zip(a.kwonlyargs, a.kw_defaults) if d is None]
        if len(required) > 1:
            out.append("score() takes {} besides rows: bank.main calls score(rows)"
                       .format(", ".join(required[1:])))
    main = defs.get("main")
    if _two_part(tree) is not None:
        main = None     # the codemod's grade() splits the two parts as this main() did
    for node in ast.walk(main) if main is not None else []:
        if isinstance(node, ast.Assign) and any(isinstance(t, (ast.Tuple, ast.List))
                                                for t in node.targets) \
                and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                        and n.func.id == "answer" for n in ast.walk(node.value)):
            out.append("main() unpacks the return of answer() at line {}: bank.main hands grade() "
                       "the whole return".format(node.lineno))
    return out


def _row_key_written(main, key):
    """True when main() stores `key` into something with a subscript (`row["k"] = ...`)."""
    for n in ast.walk(main):
        if isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store):
            if _slice_key(n) == key:
                return True
    return False


def _grade_called_elsewhere(tree):
    """True when a top level function other than main and grade uses grade()'s return as more
    than a truth value. Graded is truthy exactly when right, so `bool(grade(...))`, `not grade(...)`,
    an if, assert, `and`/`or` operand or comprehension filter read the same as before; a call
    stored, compared, indexed or returned would see the dict."""
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                or node.name in ("main", "grade"):
            continue
        parent = {}
        for p in ast.walk(node):
            for c in ast.iter_child_nodes(p):
                parent[c] = p
        for n in ast.walk(node):
            if not (isinstance(n, ast.Name) and n.id == "grade"):
                continue
            call = parent.get(n)
            if not (isinstance(call, ast.Call) and call.func is n):
                return True
            up = parent.get(call)
            truthy = (
                (isinstance(up, ast.Call) and isinstance(up.func, ast.Name)
                 and up.func.id == "bool" and call in up.args)
                or (isinstance(up, ast.UnaryOp) and isinstance(up.op, ast.Not))
                or (isinstance(up, (ast.If, ast.IfExp, ast.While, ast.Assert)) and up.test is call)
                or isinstance(up, ast.BoolOp)
                or (isinstance(up, ast.comprehension) and call in up.ifs))
            if not truthy:
                return True
    return False


def _bank_lint_defs(tree):
    return bank_lint._top_defs(tree)


def _grade_wrapper(fields, nl, checks=None):
    """The grade() the standard runner copies into each row: the bank's own verdict, then the
    row fields the old main() wrote and score() reads. bank.Graded stays truthy exactly when the
    verdict is, so the bank's tests read it as they read the bool. With `checks` (the row key of
    a two part answer's second member), it splits answer()'s return first."""
    pairs = ["{!r}: {}".format(k, src) for k, src in fields]
    lines = ["", "", "{} = grade".format(GRADE_WRAP), "", "",
             "def grade(case, got):"]
    if checks is None:
        lines += [
            '    """The standard runner copies what grade returns into the row: the verdict of',
            "    the bank's own grade, then the row fields score() reads, which the old main()",
            '    wrote (carried over by trimwrit.bank_migrate)."""',
            "    return bank.Graded({}(case, got), {{{}}})".format(GRADE_WRAP, ", ".join(pairs))]
    else:
        pairs.append("{!r}: _checks".format(checks))
        lines += [
            '    """answer() returns two parts, the verdict and the failed checks: the bank\'s',
            "    own grade reads the verdict, as the old main() did, and the row keeps the checks",
            "    and the fields score() reads. A bare verdict (a test, the self check) is graded",
            '    as it is (carried over by trimwrit.bank_migrate)."""',
            "    two = (isinstance(got, (list, tuple)) and len(got) == 2",
            "           and isinstance(got[1], (list, tuple)))",
            "    _verdict, _checks = got if two else (got, [])",
            "    return bank.Graded({}(case, _verdict),".format(GRADE_WRAP),
            "                       {{{}}})".format(", ".join(pairs))]
    return nl.join(lines) + nl


def _score_wrapper(nl):
    """The score() of a bank whose own returns `n`, the key bank.main writes itself."""
    lines = ["", "", "{} = score".format(SCORE_WRAP), "", "",
             "def score(rows):",
             '    """The standard runner writes n itself and refuses a score() that returns it:',
             "    the bank's own score without that key (carried over by",
             '    trimwrit.bank_migrate)."""',
             '    return {{k: v for k, v in {}(rows).items() if k != "n"}}'.format(SCORE_WRAP)]
    return nl.join(lines) + nl


def _migrated(old, label_source, effort_named=True):
    """(new text, removed labels, added labels) of the run.py source `old`; BankError when it
    does not parse or lacks answer() or grade(). A model bank with no EFFORT gets
    EFFORT = "model-default" unless `effort_named` (a python file of the bank names an effort:
    then the CLI default is not what runs, and EFFORT stays absent for a person to name)."""
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
    # a module only the removed runner read (json, a second os) is left unused by the removal:
    # it goes with it, the repository's own ruff would refuse it (F401)
    orphaned = _names_used(tree, []) - used - {"sys", "bank"}
    for node in tree.body:
        if isinstance(node, ast.Import):
            parse = [a for a in node.names if a.name.split(".")[0] == "argparse"
                     or (a.asname or a.name.split(".")[0]) in orphaned]
            if not parse or any((a.asname or a.name.split(".")[0]) in used for a in parse):
                continue
            rest = [a for a in node.names if a not in parse]
            if rest:
                ed.put(node.lineno, node.end_lineno,
                       "import " + ", ".join(_alias_text(a) for a in rest) + rest_of(node))
            else:
                ed.remove(node.lineno, node.end_lineno)
            labels.append((node.lineno, "import " + ", ".join(_alias_text(a) for a in parse)))
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
    added = []
    model = values["MODEL"]
    if (model is not None and model is not bank_lint.ABSENT and "MODEL" in assigns
            and "EFFORT" not in assigns and not effort_named):
        ed.insert(assigns["MODEL"].end_lineno, 'EFFORT = "model-default"' + nl)
        added.append('EFFORT = "model-default"')
    if model is None and "MODEL" in assigns and "JOBS" not in assigns:
        ed.insert(assigns["MODEL"].end_lineno, "JOBS = 1" + nl)
        added.append("JOBS = 1")
    has_sys = any(isinstance(n, ast.Import) and any(a.name == "sys" and not a.asname
                                                    for a in n.names) for n in tree.body)
    if not has_sys:
        ed.insert(_import_anchor(tree, lines), "import sys" + nl)
        added.append("import sys")
    if label_source is not None and "LABEL_SOURCE" not in assigns:
        line = "LABEL_SOURCE = {!r}".format(label_source) + nl
        added.append(line.strip())
        if "BAR" in assigns:
            ed.insert(assigns["BAR"].end_lineno, line)
        else:
            ed.insert(_start(defs["answer"]) - 1, line + nl + nl)
    checks = _two_part(tree)
    fields, problems = _row_fields(tree, old, checks)
    wrappers = []
    if (fields or checks is not None) and not problems:
        wrappers.append(_grade_wrapper(fields, nl, checks))
        if checks is not None:
            added.append("grade() reads the verdict of answer()'s two parts and keeps " + checks)
        if fields:
            added.append("grade() carries the row field{} {}".format(
                "s" if len(fields) > 1 else "", ", ".join(k for k, _ in fields)))
    if _score_returns_n(tree):
        wrappers.append(_score_wrapper(nl))
        added.append("score() leaves n to the standard runner")
    wrapper = "".join(wrappers)
    new = ed.text().rstrip() + (nl if wrapper else "") + wrapper.rstrip() + TAIL.replace("\n", nl)
    return new, [label for _, label in sorted(labels)], added


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


def _names_effort(path):
    """True when the file at `path` holds the word effort (any case)."""
    try:
        return re.search(r"\beffort\b", _read(path), re.IGNORECASE) is not None
    except (OSError, ValueError):
        return False


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
            if getattr(r, "stale_release", False):
                continue  # the old runner's release: the migrated run writes the schema 2 one
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
    effort_named = any(_names_effort(os.path.join(bank_dir, f))
                       for f in bank_lint._bank_python_files(bank_dir))
    try:
        new, removed, added = _migrated(old, label_source, effort_named)
    except b.BankError as exc:
        raise b.BankError("{}: {}".format(path, exc))
    if write:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)
        left = [r.as_dict() for r in b.lint(bank_dir) if not getattr(r, "stale_release", False)]
    else:
        left = _lint_text(bank_dir, new)
    tree_old = ast.parse(old)
    for problem in _main_shape_problems(tree_old) + _row_fields(tree_old, old,
                                                                _two_part(tree_old))[1]:
        left.append(b.Refusal(
            "bespoke-runner", bank_dir, problem,
            "write {} by hand on the standard shape: grade() returns the field in its dict"
            .format(path)).as_dict())
    for line in _main_references(new):
        left.append(b.Refusal(
            "bespoke-runner", bank_dir,
            "main is still referenced at line {} of the migrated run.py".format(line),
            "remove that use of main() from {}: bank.main(__file__) is the whole runner"
            .format(path)).as_dict())
    return {"bank": bank_dir, "lines_before": len(old.splitlines()),
            "lines_after": len(new.splitlines()), "removed": removed, "added": added,
            "old_text": old,
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


def _replay(bank_dir, legacy_run, answers, kept_rows=None):
    """(captured release doc or None, error text or None, stderr of the old runner). With
    `kept_rows` (a list), the rows the old runner wrote are appended to it."""
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
        if kept_rows is not None:
            kept_rows.extend(_rows_under(b, tmp))
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


# ------------------------------------------------------------------ the rehearsal

# the keys of a release that are the run's own stamps or the standard's own record, never a result
# the old runner measured
FAILED_CALL = "rehearsal: this call was refused"
STAMPS = frozenset(("schema", "at", "run", "bank_sha", "model", "effort", "inputs_sha", "bar_sha",
                    "bar_since", "usage", "repeats", "splits", "engine", "standard", "wall_s",
                    "downshift", "waived", "engine_log"))
MAX_DIFFS = 12


def _rows_under(b, tmp):
    """The rows of the run files written under `tmp` (probe rows left out)."""
    rows = []
    for name in sorted(os.listdir(tmp)):
        if name.endswith(".jsonl") and name.count(".") == 1:
            rows.extend(b.load(os.path.join(tmp, name)))
    return rows


def _recorded_rows(b, bank_dir):
    """(rows of the release's run, None) or (None, why there are none)."""
    doc = b.read_release(bank_dir)
    run = str((doc or {}).get("run") or "")
    if not run:
        return None, "RELEASE.json names no run"
    for path in (os.path.join(bank_dir, b.RUNS, run + ".jsonl"),
                 os.path.join(bank_dir, b.RUNS, run), os.path.join(bank_dir, run)):
        if os.path.isfile(path):
            try:
                rows = b.load(path)
            except (OSError, ValueError) as exc:
                return None, "the rows of run {} do not read ({})".format(run, exc)
            return (rows, None) if rows else (None, "run {} holds no row".format(run))
    return None, "the rows of run {} are not on disk".format(run)


def _answer_assign(tree):
    """(names, answer variable shape) of the old runner's `x = answer(case)` or
    `a, b = answer(case)`, an offline arm allowed beside it: a tuple of one or two names, else
    None."""
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                or node.name in ("answer", "grade"):
            continue
        for n in ast.walk(node):
            if not (isinstance(n, ast.Assign) and len(n.targets) == 1):
                continue
            values = [n.value.body, n.value.orelse] if isinstance(n.value, ast.IfExp) \
                else [n.value]
            if not any(isinstance(v, ast.Call) and _is_name(v.func, "answer") for v in values):
                continue
            t = n.targets[0]
            if isinstance(t, ast.Name):
                return (t.id,)
            if _is_pair(t) and all(isinstance(e, ast.Name) for e in t.elts):
                return (t.elts[0].id, t.elts[1].id)
    return None


def _field_of(node, name):
    """"f" when `node` reads field f of the variable `name`: name["f"], name.get("f"), either
    one followed by `or <default>`."""
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        node = node.values[0]
    if isinstance(node, ast.Subscript) and _is_name(node.value, name):
        key = _slice_key(node)
        return key if isinstance(key, str) else None
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and _is_name(node.func.value, name) and node.args
            and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
        return node.args[0].value
    return None


def _answer_rebuilder(old_text):
    """row -> (True, what answer() returned) or (False, None): the old runner's own row literal
    read backwards. Two names unpacked from answer() come back as a pair of the row keys that
    store them; one name stored whole comes back as that key; fields of it stored one by one come
    back as a dict of them; with none of these, the row's "got"."""
    tree = ast.parse(old_text)
    names = _answer_assign(tree)
    literals = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            lit = {k.value: v for k, v in zip(node.keys, node.values)
                   if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if "right" in lit:
                literals.append(lit)

    def stored(name):
        for lit in literals:
            keys = [k for k, v in lit.items() if _is_name(v, name)]
            if keys:
                return "got" if "got" in keys else keys[0]
        return None

    keys, fields = None, {}
    if names and len(names) == 2:
        keys = [stored(n) for n in names]
        if None in keys:
            return lambda row: (False, None)
    elif names:
        keys = [stored(names[0])]
        if keys[0] is None:
            keys = None
            for lit in literals:
                for k, v in lit.items():
                    f = _field_of(v, names[0])
                    if f is not None:
                        fields.setdefault(f, k)

    def rebuild(row):
        want = keys if keys else list(fields.values()) or ["got"]
        if any(k not in row for k in want):
            return False, None
        if keys and len(keys) == 2:
            return True, (row[keys[0]], row[keys[1]])
        if keys:
            return True, row[keys[0]]
        if fields:
            return True, {f: row[k] for f, k in fields.items()}
        return True, row["got"]
    return rebuild


@contextlib.contextmanager
def _patched(obj, **names):
    saved = {n: getattr(obj, n) for n in names}
    try:
        for n, value in names.items():
            setattr(obj, n, value)
        yield
    finally:
        for n, value in saved.items():
            setattr(obj, n, value)


def _run_new(bank_dir, new_text, answers, failing=None):
    """(captured release doc or None, its rows, error text or None) of bank.main on the new
    run.py with answer() replaced by the recorded answers: nothing is written in the bank. The
    case whose id key is `failing` raises instead of answering, as a call a provider refuses."""
    from . import bank_run  # noqa: PLC0415
    b = _bank()
    run_file = os.path.join(os.path.abspath(bank_dir), bank_lint.RUN_FILE)
    captured, out, err = [], io.StringIO(), io.StringIO()
    rows = []

    def capture(_bank_dir, doc):
        captured.append(doc)
        return doc

    with tempfile.TemporaryDirectory() as tmp, \
            bank_lint.isolated_imports(os.path.abspath(bank_dir)), \
            _patched(b, _write_release_file=capture,
                     run_path=lambda _d, stamp, ext=".jsonl": os.path.join(tmp, stamp + ext)), \
            _patched(bank_lint, self_check=lambda *a, **k: [], lint=lambda *a, **k: []), \
            _patched(bank_run._Cache, lookup=lambda self, case: (False, None, None),
                     store=lambda self, key, got: None):
        sys.path.insert(0, os.path.abspath(bank_dir))
        name = "{}rehearse_{}_{}".format(bank_lint.MODULE_PREFIX, b.sha(run_file),
                                         next(_counter))
        module = type(sys)(name)
        module.__file__ = run_file
        sys.modules[name] = module
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                exec(compile(new_text, run_file, "exec"), module.__dict__)  # noqa: S102
                model = module.__dict__.get("MODEL")

                def recorded(case, *args, **kwargs):
                    if failing is not None and _id_key(case.get("id")) == failing:
                        raise RuntimeError(FAILED_CALL)
                    if model:
                        b.note_usage({"model": model})
                    return answers[_id_key(case.get("id"))]

                module.answer = recorded
                code = bank_run.main(run_file, ["--no-probe", "--jobs", "1"], module=module)
        except SystemExit as exc:
            return None, [], "the new runner exited (SystemExit: {!r}){}".format(
                exc.code, _last_line(err))
        except Exception as exc:  # noqa: BLE001  (a crash is the miss the rehearsal is for)
            return None, [], "the new runner raised {}: {}".format(type(exc).__name__, exc)
        rows = _rows_under(b, tmp)
    if not captured:
        return None, rows, "the new runner wrote no release (exit {}){}".format(
            code, _last_line(err))
    return captured[-1], rows, None


def _value_diffs(path, old, new, out):
    """Append to `out` one line per value of `old` that `new` lost or changed (numbers within
    TOLERANCE; a key only `new` holds is no loss)."""
    if isinstance(old, dict) and isinstance(new, dict):
        for k in sorted(old):
            where = "{}.{}".format(path, k) if path else k
            if k not in new:
                out.append("lost: {}".format(where))
            else:
                _value_diffs(where, old[k], new[k], out)
    elif isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        for i, (a, c) in enumerate(zip(old, new)):
            _value_diffs("{}[{}]".format(path, i), a, c, out)
    elif not _same(old, new):
        out.append("{}: old {}, new {}".format(path, repr(old)[:80], repr(new)[:80]))


def _plain(value):
    return json.loads(json.dumps(value, default=repr))


def rehearse(bank_dir, new_text, legacy_run):
    """Replay the recorded answers of `bank_dir` through the OLD runner (`legacy_run`, a path)
    and through bank.main on `new_text`, before any migration pays a call. Returns {"result",
    "cases", "diffs"}: result "ok" (every value the old runner wrote is there, unchanged),
    "miss" (the new runner crashes, also with one case left unanswered, is refused, or loses or
    changes a value: `diffs` names them),
    "no-baseline" (the old runner cannot be replayed, so nothing says anything about the new
    one) or "no-answers" (nothing recorded to replay: `diffs` says why). Only the cases that have a recorded answer
    are replayed, by both runners."""
    b = _bank()
    bank_dir = str(bank_dir)

    def verdict(result, n, diffs):
        return {"result": result, "cases": n, "diffs": [str(d)[:300] for d in diffs][:MAX_DIFFS]}

    recorded, why = _recorded_rows(b, bank_dir)
    if recorded is None:
        return verdict("no-answers", 0, [why])
    rebuild = _answer_rebuilder(_read(str(legacy_run)))
    by_id = {}
    for row in recorded:
        known, got = rebuild(row)
        if known and not row.get("error"):
            by_id[_id_key(row.get("id"))] = got
    real_corpus = b.corpus
    cases = [c for c in real_corpus(bank_dir) if _id_key(c.get("id")) in by_id]
    if not cases:
        return verdict("no-answers", 0, ["no case of the corpus has a recorded answer the old "
                                         "runner's rows give back"])
    with _patched(b, corpus=lambda _d: [dict(c) for c in cases]):
        old_rows = []
        old_doc, error, _ = _replay(bank_dir, legacy_run, by_id, kept_rows=old_rows)
        if error:
            # the old runner cannot even replay: nothing to compare the new one with
            return verdict("no-baseline", len(cases), [error])
        new_doc, new_rows, error = _run_new(bank_dir, new_text, by_id)
        if not error and len(cases) > 1:
            # a recorded run holds answers only, a paid one meets refused calls, and the row of
            # a refused call holds no field of the grade: score() must live through it
            canary = b.probe_sample(cases, 1)[0].get("id")
            failing = next(c for c in reversed(cases) if c.get("id") != canary)
            error = _run_new(bank_dir, new_text, by_id, failing=_id_key(failing.get("id")))[2]
            if error:
                error = "with one case unanswered, " + error
    if error:
        return verdict("miss", len(cases), [error])
    old = {k: v for k, v in _plain(old_doc).items() if k not in STAMPS}
    diffs = []
    _value_diffs("", old, _plain(new_doc), diffs)
    new_right = {_id_key(r.get("id")): r.get("right") for r in new_rows}
    for r in old_rows:
        key = _id_key(r.get("id"))
        if "right" in r and bool(r["right"]) != bool(new_right.get(key)):
            diffs.append("case {}: right was {}, new {}".format(r.get("id"), r["right"],
                                                                 new_right.get(key)))
    return verdict("miss" if diffs else "ok", len(cases), diffs)
