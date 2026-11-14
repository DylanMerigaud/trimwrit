"""The bank standard: one refusal test per hole, written before the implementation.
No test calls a real model or the network."""
import contextlib
import datetime
import io
import json
import math
import os
import textwrap

import pytest

from trimwrit import bank

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_ID = "claude-sonnet-5-5"
NOTE = ('bank.note_usage({"model": model, "input_tokens": 10, "output_tokens": 5, '
        '"cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.001})')
MODEL_BODY = NOTE + '\nreturn "yes" if case["input"]["x"] % 2 else "no"'

SCHEMA2_KEYS = ("schema", "passed", "at", "n", "bar", "model", "bank_sha", "run", "effort",
                "inputs_sha", "bar_sha", "bar_since", "usage", "wall_s", "repeats", "splits",
                "engine", "standard")


def today():
    return datetime.datetime.now(datetime.timezone.utc).date()


def standard_bank(tmp_path, *, n=40, model=None, effort=None, bar=None, run_body=None, rows=None,
                  codebook_extra="", label_source="the door's own record (deterministic)"):
    d = tmp_path / "std-bank"
    d.mkdir()
    if rows is None:
        rows = [{"id": "c%02d" % i, "input": {"x": i}, "expected": "yes" if i % 2 else "no",
                 "why": "parity of x"} for i in range(n)]
    (d / "CORPUS.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    bar_v = bar or {"accuracy": 0.9}
    bar_lines = "".join("%s >= %r\n" % (k, float(v)) for k, v in bar_v.items())
    (d / "CODEBOOK.md").write_text(
        "# std\n\n## 0. Why\nA test bank.\n\n## 1. The cases\nParity.\n\n## 2. The bar\n"
        + bar_lines + "\n## 3. Changes\n- 2026-11-01: laid out.\n" + codebook_extra)
    body = run_body or 'return "yes" if case["input"]["x"] % 2 else "no"'
    body = textwrap.indent(textwrap.dedent(body), "    ")
    (d / "run.py").write_text(
        "import os, sys\n"
        "sys.path.insert(0, %r)\n"
        "from trimwrit import bank\n"
        "MODEL = %r\nEFFORT = %r\nBAR = %r\nLABEL_SOURCE = %r\n"
        "def answer(case, model=MODEL, effort=EFFORT):\n%s\n"
        "def grade(case, got):\n    return got == case[\"expected\"]\n"
        "if __name__ == \"__main__\":\n    sys.exit(bank.main(__file__))\n"
        % (ROOT, model, effort, bar_v, label_source, body))
    return d


def model_bank(tmp_path, **kw):
    kw.setdefault("model", MODEL_ID)
    kw.setdefault("effort", "medium")
    kw.setdefault("run_body", MODEL_BODY)
    return standard_bank(tmp_path, **kw)


def run_main(d, *argv):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = bank.main(str(d / "run.py"), argv=list(argv))
    refusals = []
    for line in err.getvalue().splitlines():
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict) and "refused" in doc:
            refusals.append(doc)
    return code, refusals


def rules(items):
    return {getattr(r, "rule", None) or r["rule"] for r in items}


def release(d):
    return json.loads((d / "RELEASE.json").read_text())


def edit(d, name, old, new):
    p = d / name
    text = p.read_text()
    assert old in text
    p.write_text(text.replace(old, new))


def test_bar_moved_after_a_run_is_refused_until_a_dated_codebook_line(tmp_path):
    d = standard_bank(tmp_path)
    code, _ = run_main(d)
    assert code == 0
    assert release(d)["schema"] == 2
    before = (d / "RELEASE.json").read_bytes()
    edit(d, "run.py", "{'accuracy': 0.9}", "{'accuracy': 0.8}")
    edit(d, "CODEBOOK.md", "accuracy >= 0.9", "accuracy >= 0.8")
    code, refusals = run_main(d)
    assert code == 65
    assert "bar-moved" in rules(refusals)
    assert (d / "RELEASE.json").read_bytes() == before
    with open(str(d / "CODEBOOK.md"), "a") as f:
        f.write("- %s: bar changed from 0.9 to 0.8, because the test says so.\n" % today().isoformat())
    code, _ = run_main(d)
    assert code == 0
    doc = release(d)
    assert doc["bar"] == {"accuracy": 0.8}
    assert doc["bar_since"] == today().isoformat()


def test_a_grader_that_cannot_fail_is_refused(tmp_path):
    d = standard_bank(tmp_path)
    edit(d, "run.py", 'return got == case["expected"]', "return True")
    assert "grader-cannot-fail" in rules(bank.self_check(d))
    code, refusals = run_main(d)
    assert code == 65
    assert "grader-cannot-fail" in rules(refusals)
    assert not (d / "RELEASE.json").exists()


def test_labels_written_by_the_judged_model_are_refused(tmp_path):
    d = model_bank(tmp_path, label_source="labeled by claude-sonnet-5-5 on 2026-11-01")
    assert "label-by-judged-model" in rules(bank.lint(d))
    other = tmp_path / "two"
    other.mkdir()
    d2 = model_bank(other, label_source="a sonnet pass over the drafts")
    assert "label-by-judged-model" in rules(bank.lint(d2))
    third = tmp_path / "three"
    third.mkdir()
    d3 = model_bank(third, label_source="Dylan's own tag in the ledger")
    assert "label-by-judged-model" not in rules(bank.lint(d3))


def test_a_bank_with_no_holdout_is_refused(tmp_path):
    rows = [{"id": "c%02d" % i, "input": {"x": i}, "expected": "yes" if i % 2 else "no",
             "why": "parity of x", "split": "dev"} for i in range(40)]
    d = standard_bank(tmp_path, rows=rows)
    assert "no-holdout" in rules(bank.lint(d))
    other = tmp_path / "two"
    other.mkdir()
    d2 = standard_bank(other)
    assert "no-holdout" not in rules(bank.lint(d2))
    cases = bank.corpus(str(d2))
    first = bank.splits(cases)
    holdout = sorted(i for i, s in first.items() if s == "holdout")
    assert len(holdout) == math.ceil(40 / 4) == 10
    again = bank.splits(bank.corpus(str(d2)))
    assert sorted(i for i, s in again.items() if s == "holdout") == holdout


def test_a_tiny_corpus_is_refused_unless_a_dated_waiver_holds(tmp_path):
    d = standard_bank(tmp_path, n=10)
    found = rules(bank.lint(d))
    assert "corpus-too-small" in found and "no-holdout" in found

    def waive(since, extra=""):
        edit(d, "run.py", "def answer(", "WAIVE = {%s\"corpus-too-small\": {\"since\": %r, "
             "\"why\": \"a rare judgment\"}, \"no-holdout\": {\"since\": %r, \"why\": \"same\"}}\n"
             "def answer(" % (extra, since, since))

    waive(today().isoformat())
    found = rules(bank.lint(d))
    assert "corpus-too-small" not in found and "no-holdout" not in found
    d2 = tmp_path / "old"
    d2.mkdir()
    d2 = standard_bank(d2, n=10)
    old = (today() - datetime.timedelta(days=91)).isoformat()
    edit(d2, "run.py", "def answer(", "WAIVE = {\"corpus-too-small\": {\"since\": %r, "
         "\"why\": \"a rare judgment\"}, \"no-holdout\": {\"since\": %r, \"why\": \"same\"}}\n"
         "def answer(" % (old, old))
    found = rules(bank.lint(d2))
    assert "corpus-too-small" in found and "no-holdout" in found
    d3 = tmp_path / "unwaivable"
    d3.mkdir()
    d3 = standard_bank(d3)
    edit(d3, "run.py", "def answer(", "WAIVE = {\"grader-cannot-fail\": {\"since\": %r, "
         "\"why\": \"nope\"}}\ndef answer(" % today().isoformat())
    hits = [r for r in bank.lint(d3) if r.rule == "grader-cannot-fail"]
    assert hits and "not waivable" in hits[0].what


def test_a_bespoke_runner_is_refused(tmp_path):
    d = standard_bank(tmp_path)
    (d / "run.py").write_text(
        "import argparse\n"
        "from trimwrit import bank\n"
        "MODEL = None\nEFFORT = None\nBAR = {'accuracy': 0.9}\nLABEL_SOURCE = 'own'\n"
        "def answer(case):\n    return 'yes'\n"
        "def grade(case, got):\n    return got == case['expected']\n"
        "def main(argv=None):\n"
        "    argparse.ArgumentParser().parse_args(argv)\n"
        "    bank.write_release('.', passed=True, n=1, bar={}, model=None)\n")
    found = [r for r in bank.lint(d) if r.rule == "bespoke-runner"]
    assert found
    assert all(r.fix for r in found)


def test_a_corpus_edited_after_its_release_voids_the_release(tmp_path):
    d = standard_bank(tmp_path)
    code, _ = run_main(d)
    assert code == 0
    assert bank.released(str(d)) is True
    (d / "test_extra.py").write_text("def test_x():\n    pass\n")
    assert bank.released(str(d)) is True
    with open(str(d / "CORPUS.jsonl"), "a") as f:
        f.write(json.dumps({"id": "c99", "input": {"x": 99}, "expected": "yes",
                            "why": "parity of x"}) + "\n")
    assert bank.released(str(d)) is False
    assert "corpus-changed-since-release" in rules(bank.lint(d))


def test_a_model_that_is_not_pinned_is_refused(tmp_path):
    d = model_bank(tmp_path, model="opus", effort="medium")
    assert "model-not-pinned" in rules(bank.lint(d))
    two = tmp_path / "two"
    two.mkdir()
    d2 = model_bank(two, model=MODEL_ID, effort=None)
    assert "model-not-pinned" in rules(bank.lint(d2))
    three = tmp_path / "three"
    three.mkdir()
    d3 = standard_bank(three, model=None, run_body=MODEL_BODY.replace(": model,", ": %r," % MODEL_ID))
    code, refusals = run_main(d3)
    assert code == 65
    assert "model-not-pinned" in rules(refusals)
    assert not (d3 / "RELEASE.json").exists()


def test_a_run_with_no_cost_record_writes_no_release(tmp_path):
    d = model_bank(tmp_path, run_body='return "yes" if case["input"]["x"] % 2 else "no"')
    code, refusals = run_main(d)
    assert code == 65
    assert "cost-not-recorded" in rules(refusals)
    assert not (d / "RELEASE.json").exists()
    two = tmp_path / "two"
    two.mkdir()
    d2 = model_bank(two)
    code, _ = run_main(d2)
    assert code == 0
    usage = release(d2)["usage"]
    assert usage["calls"] == 40 + 8
    assert usage["cost_usd"] == pytest.approx(round(48 * 0.001, 6))
    assert usage["models"] == [MODEL_ID]


def test_one_run_cannot_decide_alone(tmp_path):
    d = standard_bank(tmp_path)
    code, refusals = run_main(d, "--repeats", "0")
    assert code == 65
    assert "no-repeat" in rules(refusals)
    code, _ = run_main(d)
    assert code == 0
    rep = release(d)["repeats"]
    assert rep["k"] == 1 and rep["sample"] == 8 and rep["flips"] == 0
    two = tmp_path / "two"
    two.mkdir()
    flip = ('calls = globals().setdefault("_CALLS", {})\n'
            'calls[case["id"]] = calls.get(case["id"], 0) + 1\n'
            'right = "yes" if case["input"]["x"] % 2 else "no"\n'
            'if calls[case["id"]] > 1:\n    return "no" if right == "yes" else "yes"\n'
            'return right')
    d2 = standard_bank(two, run_body=flip)
    code, refusals = run_main(d2)
    assert code == 65
    assert "no-repeat" in rules(refusals)
    doc = release(d)
    del doc["repeats"]
    assert bank.envelope_problems(doc)


def test_an_untyped_corpus_row_is_refused(tmp_path):
    rows = [{"id": "c%02d" % i, "input": {"x": i}, "expected": "yes" if i % 2 else "no",
             "why": "parity of x"} for i in range(40)]
    del rows[3]["why"]
    d = standard_bank(tmp_path, rows=rows)
    assert "corpus-untyped" in rules(bank.lint(d))
    two = tmp_path / "two"
    two.mkdir()
    dup = [dict(r) for r in rows]
    dup[3]["why"] = "parity of x"
    dup[4]["id"] = dup[5]["id"]
    d2 = standard_bank(two, rows=dup)
    assert "corpus-untyped" in rules(bank.lint(d2))


def _refusal_banks(tmp_path):
    out = []
    specs = [
        lambda p: model_bank(p, label_source="labeled by claude-sonnet-5-5"),
        lambda p: standard_bank(p, rows=[{"id": "c%02d" % i, "input": {"x": i}, "expected": "no",
                                          "why": "w", "split": "dev"} for i in range(40)]),
        lambda p: standard_bank(p, n=10),
        lambda p: _bespoke(p),
        lambda p: standard_bank(p, rows=[{"id": "a", "input": {}, "expected": "x"}] * 40),
    ]
    for i, spec in enumerate(specs):
        sub = tmp_path / ("b%d" % i)
        sub.mkdir()
        out.append(spec(sub))
    return out


def _bespoke(p):
    d = standard_bank(p)
    (d / "run.py").write_text("import argparse\nMODEL = None\nEFFORT = None\n"
                              "BAR = {'accuracy': 0.9}\nLABEL_SOURCE = 'own'\n"
                              "def main(argv=None):\n    return 0\n")
    return d


def test_every_refusal_names_its_fix(tmp_path):
    found = []
    for d in _refusal_banks(tmp_path):
        found.extend(bank.lint(d))
    assert len(found) >= 5
    for r in found:
        doc = r.as_dict()
        assert set(doc) == {"refused", "rule", "bank", "what", "fix"}
        assert doc["refused"] == "bank-standard"
        assert doc["rule"] in bank.RULES
        assert doc["fix"].strip()


def test_a_standard_bank_passes_clean(tmp_path):
    d = standard_bank(tmp_path)
    assert bank.lint(d) == []
    assert bank.self_check(d) == []
    code, refusals = run_main(d)
    assert code == 0 and refusals == []
    doc = release(d)
    assert doc["schema"] == 2
    for key in SCHEMA2_KEYS:
        assert key in doc, key
    assert bank.envelope_problems(doc) == []
    assert bank.released(str(d)) is True
