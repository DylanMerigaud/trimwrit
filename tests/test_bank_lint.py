"""The static half of the bank standard (trimwrit/bank_lint.py) on what test_bank_standard.py
does not pin: FIELDS, non literal constants, explicit splits, the codebook section 2, the inputs
sha, the default mutation, the waivers and the release checks. No model call."""
import datetime
import json
import os
import textwrap

import pytest

from trimwrit import bank, bank_lint

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY = datetime.date(2026, 11, 14)

RUN = '''import os, sys
sys.path.insert(0, {root!r})
from trimwrit import bank
{head}
def answer({answer_args}):
    return "yes" if case["input"]["x"] % 2 else "no"
def grade(case, got):
    return got == case["expected"]
{tail}
if __name__ == "__main__":
    sys.exit(bank.main(__file__))
'''

HEAD = ("MODEL = None\nEFFORT = None\nBAR = {'accuracy': 0.9}\n"
        "LABEL_SOURCE = \"the door's own record\"")

BOOK = ("# b\n\n## 1. The cases\nParity.\n\n## 2. The bar\naccuracy >= 0.9\n\n"
        "## 3. Changes\n- 2026-11-01: laid out.\n")


def make(tmp_path, name="b", rows=None, head=HEAD, answer_args="case, model=MODEL, effort=EFFORT",
         tail="", book=BOOK, n=40):
    d = tmp_path / name
    d.mkdir()
    if rows is None:
        rows = [{"id": "c%02d" % i, "input": {"x": i}, "expected": "yes" if i % 2 else "no",
                 "why": "parity"} for i in range(n)]
    (d / "CORPUS.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    if book is not None:
        (d / "CODEBOOK.md").write_text(book)
    (d / "run.py").write_text(RUN.format(root=ROOT, head=head, answer_args=answer_args,
                                         tail=textwrap.dedent(tail)))
    return d


def rules(items):
    return {r.rule for r in items}


def test_the_reexports_are_the_lint_module():
    assert bank.lint is bank_lint.lint and bank.self_check is bank_lint.self_check
    assert bank.declared is bank_lint.declared and bank.resolve_case is bank_lint.resolve_case
    assert bank.load_module is bank_lint.load_module


def test_a_refusal_names_a_known_rule_and_prints_its_fix():
    r = bank.Refusal("no-holdout", "/x", "3 holdout", "add split")
    assert isinstance(r, bank.BankError)
    assert str(r) == "/x: no-holdout: 3 holdout (fix: add split)"
    assert r.as_dict() == {"refused": "bank-standard", "rule": "no-holdout", "bank": "/x",
                           "what": "3 holdout", "fix": "add split"}
    with pytest.raises(ValueError):
        bank.Refusal("not-a-rule", "/x", "w", "f")
    assert bank.EXIT_REFUSED == 65 and set(bank.WAIVABLE_IN_BANK) <= set(bank.RULES)


def test_full_model_ids():
    for ok in ("claude-sonnet-5-5", "claude-opus-4-8", "claude-fable-5"):
        assert bank.FULL_ID_RE.match(ok), ok
    for bad in ("opus", "sonnet", "haiku", "fable", "claude-opus", "Claude-opus-5"):
        assert not bank.FULL_ID_RE.match(bad), bad


def test_fields_map_a_corpus_with_other_keys(tmp_path):
    rows = [{"key": "c%02d" % i, "text": {"x": i}, "label": "yes" if i % 2 else "no",
             "reason": "parity"} for i in range(40)]
    head = HEAD + "\nFIELDS = {'id': 'key', 'input': 'text', 'expected': 'label', 'why': 'reason'}"
    d = make(tmp_path, rows=rows, head=head)
    assert bank.lint(d, today=TODAY) == []
    assert bank.self_check(d, today=TODAY) == []
    bare = make(tmp_path, name="bare", rows=rows)
    assert "corpus-untyped" in rules(bank.lint(bare, today=TODAY))


def test_resolve_case_adds_the_canonical_keys():
    row = {"key": "a", "text": "hi", "label": "x"}
    case = bank.resolve_case(row, {"id": "key", "input": "text", "expected": "label"}, "dev",
                             label_source="human")
    assert case["id"] == "a" and case["input"] == "hi" and case["expected"] == "x"
    assert case["split"] == "dev" and case["label_source"] == "human"
    assert case["key"] == "a" and "id" not in row


def test_declared_reads_literals_and_marks_the_rest(tmp_path):
    head = ("import producer_stub\nMODEL = producer_stub.DEFAULT_MODEL\nEFFORT = 'high'\n"
            "BAR = {'accuracy': 0.9}\nJOBS = 8")
    d = make(tmp_path, head=head)
    got = bank.declared(d)
    assert got["MODEL"] is bank_lint.NOT_LITERAL
    assert got["EFFORT"] == "high" and got["JOBS"] == 8 and got["BAR"] == {"accuracy": 0.9}
    assert got["LABEL_SOURCE"] is bank_lint.ABSENT and got["WAIVE"] is bank_lint.ABSENT


def test_a_non_literal_model_skips_the_id_check_but_needs_a_model_parameter(tmp_path):
    head = ("import producer_stub\nMODEL = producer_stub.DEFAULT_MODEL\nEFFORT = 'medium'\n"
            "BAR = {'accuracy': 0.9}\nLABEL_SOURCE = 'a human'")
    d = make(tmp_path, head=head.replace("producer_stub", "stub_alias_model"))
    (d / "stub_alias_model.py").write_text("DEFAULT_MODEL = 'opus'\n")
    assert "model-not-pinned" not in rules(bank.lint(d, today=TODAY))
    # the import resolves it, and the alias is refused there
    hits = [r for r in bank.self_check(d, today=TODAY) if r.rule == "model-not-pinned"]
    assert hits and "'opus'" in hits[0].what
    two = make(tmp_path, name="two", head=head, answer_args="case")
    (two / "producer_stub.py").write_text("DEFAULT_MODEL = 'claude-sonnet-5-5'\n")
    hits = [r for r in bank.lint(two, today=TODAY) if r.rule == "model-not-pinned"]
    assert hits and "`model`" in hits[0].what


def test_model_absent_and_effort_rules(tmp_path):
    d = make(tmp_path, head="EFFORT = None\nBAR = {'accuracy': 0.9}\nLABEL_SOURCE = 'h'")
    assert any("MODEL" in r.what for r in bank.lint(d, today=TODAY)
               if r.rule == "model-not-pinned")
    two = make(tmp_path, name="two", head="MODEL = None\nEFFORT = 'high'\n"
               "BAR = {'accuracy': 0.9}\nLABEL_SOURCE = 'h'")
    assert "model-not-pinned" in rules(bank.lint(two, today=TODAY))
    three = make(tmp_path, name="three", head="MODEL = 'claude-sonnet-5-5'\n"
                 "EFFORT = 'model-default'\nBAR = {'accuracy': 0.9}\nLABEL_SOURCE = 'h'")
    assert "model-not-pinned" not in rules(bank.lint(three, today=TODAY))


def test_splits_read_test_as_holdout_and_derive_the_rest():
    cases = [{"id": "a", "split": "test"}, {"id": "b", "split": "dev"},
             {"id": "c", "split": "holdout"}] + [{"id": "x%d" % i} for i in range(8)]
    sp = bank.splits(cases)
    assert sp["a"] == "holdout" and sp["b"] == "dev" and sp["c"] == "holdout"
    derived = [sp["x%d" % i] for i in range(8)]
    assert derived.count("holdout") == 2 and derived.count("dev") == 6


def test_an_explicit_test_split_counts_as_holdout(tmp_path):
    rows = [{"id": "c%02d" % i, "input": {"x": i}, "expected": "yes" if i % 2 else "no",
             "why": "parity", "split": "test" if i < 8 else "dev"} for i in range(40)]
    d = make(tmp_path, rows=rows)
    assert "no-holdout" not in rules(bank.lint(d, today=TODAY))
    rows[0]["split"] = "train"
    two = make(tmp_path, name="two", rows=rows)
    assert "corpus-untyped" in rules(bank.lint(two, today=TODAY))


def test_a_codebook_whose_section_2_lacks_the_bar_is_refused(tmp_path):
    book = ("# b\n\n## 2. The bar\nsomething else\n\n## 3. Changes\n"
            "- 2026-11-01: accuracy >= 0.9 at first.\n")
    d = make(tmp_path, book=book)
    hits = [r for r in bank.lint(d, today=TODAY) if r.rule == "bar-moved"]
    assert hits and "accuracy >= 0.9" in hits[0].fix
    assert "bar-moved" in rules(bank.lint(make(tmp_path, name="none", book=None), today=TODAY))
    whole = make(tmp_path, name="int", head=HEAD.replace("0.9", "1"),
                 book=BOOK.replace("0.9", "1"))
    assert "bar-moved" not in rules(bank.lint(whole, today=TODAY))


def test_inputs_sha_ignores_tests_and_follows_the_corpus(tmp_path):
    d = make(tmp_path)
    first = bank.inputs_sha(d)
    for name in ("test_more.py", "more_test.py", "conftest.py"):
        (d / name).write_text("x = 1\n")
    assert bank.inputs_sha(d) == first
    with open(str(d / "CORPUS.jsonl"), "a") as fh:
        fh.write(json.dumps({"id": "z", "input": {"x": 1}, "expected": "yes", "why": "w"}) + "\n")
    assert bank.inputs_sha(d) != first
    assert len(first) == 12


def test_bar_sha_is_order_free():
    assert bank.bar_sha({"a": 0.9, "n_min": 3}) == bank.bar_sha({"n_min": 3, "a": 0.9})
    assert bank.bar_sha({"a": 0.9}) != bank.bar_sha({"a": 0.8})


def test_default_mutation_per_type():
    dm = bank.default_mutation
    assert dm({}, "no", ["yes", "no", "maybe", "yes"]) == "yes"
    assert dm({}, "b", ["a", "c", "b"]) == "a"          # tie: smallest JSON
    assert dm({}, "x", ["x", "x"]) == ""
    assert dm({}, "", [""]) == "mutated"
    assert dm({}, True, [True]) is False
    assert dm({}, 3, [3]) == 4
    assert dm({}, 0.5, [0.5]) == 1.5
    assert dm({}, None, [None]) == "mutated"
    assert dm({}, [1], [[1]]) == []
    assert dm({}, [], [[]]) == ["mutated"]
    assert dm({}, {"a": "x", "b": False}, [{"a": "x", "b": False}]) == {"a": "", "b": True}


def test_a_mutate_hook_and_an_ideal_hook_are_used(tmp_path):
    loose = 'def grade(case, got):\n    return got in ("yes", "no")\n'
    d = make(tmp_path)
    (d / "run.py").write_text((d / "run.py").read_text().replace(
        'def grade(case, got):\n    return got == case["expected"]\n', loose))
    assert "grader-cannot-fail" in rules(bank.self_check(d, today=TODAY))
    edit = (d / "run.py").read_text().replace(
        "if __name__", "def mutate(case, ideal):\n    return 'other'\nif __name__")
    (d / "run.py").write_text(edit)
    assert "grader-cannot-fail" not in rules(bank.self_check(d, today=TODAY))
    two = make(tmp_path, name="two", tail="def ideal(case):\n    return 'wrong'\n")
    hits = [r for r in bank.self_check(two, today=TODAY) if r.rule == "grader-cannot-fail"]
    assert hits and "refuses the expected answer" in hits[0].what


def test_a_run_py_that_raises_on_import_is_refused(tmp_path):
    d = make(tmp_path, tail="raise RuntimeError('boom')\n")
    hits = [r for r in bank.self_check(d, today=TODAY) if r.rule == "bespoke-runner"]
    assert hits and "boom" in hits[0].what


def test_load_module_gives_a_fresh_module_each_time(tmp_path):
    d = make(tmp_path)
    a = bank.load_module(str(d / "run.py"))
    b2 = bank.load_module(str(d / "run.py"))
    assert a is not b2 and a.__name__ != b2.__name__
    assert a.grade({"expected": "yes"}, "yes") is True


def test_waivers_hold_for_90_days_and_name_waivable_rules_only(tmp_path):
    waive = "\nWAIVE = {'corpus-too-small': {'since': '2026-08-16', 'why': 'rare'}}"
    d = make(tmp_path, n=36, head=HEAD + waive)
    assert bank.lint(d, today=TODAY) == []                       # nothing to waive is fine
    small = make(tmp_path, name="small", n=20, head=HEAD + waive)
    assert "corpus-too-small" not in rules(bank.lint(small, today=TODAY))   # day 90 holds
    late = bank.lint(small, today=TODAY + datetime.timedelta(days=1))
    assert any(r.rule == "corpus-too-small" and "expired" in r.what for r in late)
    bad = make(tmp_path, name="bad", n=20,
               head=HEAD + "\nWAIVE = {'corpus-too-small': {'since': 'soon', 'why': 'x'}}")
    assert any("ISO date" in r.what for r in bank.lint(bad, today=TODAY))
    empty = make(tmp_path, name="empty", n=20,
                 head=HEAD + "\nWAIVE = {'corpus-too-small': {'since': '2026-11-14', 'why': ''}}")
    assert any("why is empty" in r.what for r in bank.lint(empty, today=TODAY))
    unknown = make(tmp_path, name="unknown", head=HEAD + "\nWAIVE = {'whatever': {}}")
    assert any("not a rule" in r.what for r in bank.lint(unknown, today=TODAY))


def _schema2(d, **kw):
    doc = {"schema": 2, "bar": {"accuracy": 0.9}, "bar_sha": bank.bar_sha({"accuracy": 0.9}),
           "inputs_sha": bank.inputs_sha(d), "at": "2026-11-10T09:00:00+00:00"}
    doc.update(kw)
    (d / "RELEASE.json").write_text(json.dumps(doc))


def test_the_release_checks_read_a_schema_2_release(tmp_path):
    d = make(tmp_path)
    _schema2(d)
    assert bank.lint(d, today=TODAY) == []
    (d / "RELEASE.json").unlink()
    _schema2(d, bar={"accuracy": 0.95}, bar_sha=bank.bar_sha({"accuracy": 0.95}))
    assert "bar-moved" in rules(bank.lint(d, today=TODAY))
    with open(str(d / "CODEBOOK.md"), "a") as fh:
        fh.write("- 2026-11-09: bar changed from 0.95 to 0.9, because old.\n")
    assert "bar-moved" in rules(bank.lint(d, today=TODAY))      # dated before the release
    with open(str(d / "CODEBOOK.md"), "a") as fh:
        fh.write("- 2026-11-12: Bar changed from 0.95 to 0.9, because the test.\n")
    found = rules(bank.lint(d, today=TODAY))
    assert "bar-moved" not in found
    assert "corpus-changed-since-release" in found              # the codebook is an input
