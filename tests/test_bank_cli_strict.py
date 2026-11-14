"""`trimwrit bank check --strict` and bank.check_strict: every bank of a tree against the
standard and the project's waiver list (.trimwrit/bank-waivers.json), which only shrinks. No
model call: the banks here are deterministic, their --self-check runs offline."""
import json
import os

from trimwrit import bank
from trimwrit.cli import main as cli

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOK = ("# b\n\n## 1. The cases\nParity.\n\n## 2. The bar\naccuracy >= 0.9\n\n"
        "## 3. Changes\n- 2026-11-01: laid out.\n")
RUN = ("import os, sys\nsys.path.insert(0, {root!r})\nfrom trimwrit import bank\n"
       "MODEL = None\nEFFORT = None\nBAR = {{'accuracy': 0.9}}\n"
       "LABEL_SOURCE = \"the door's own record\"\n"
       "def answer(case):\n    return 'yes' if case['input']['x'] % 2 else 'no'\n"
       "def grade(case, got):\n    return {grade}\n{extra}"
       "if __name__ == \"__main__\":\n    sys.exit(bank.main(__file__))\n")


def make(parent, name, n=40, grade="got == case['expected']", extra=""):
    d = parent / name
    d.mkdir(parents=True)
    rows = [{"id": "c%02d" % i, "input": {"x": i}, "expected": "yes" if i % 2 else "no",
             "why": "parity"} for i in range(n)]
    (d / "CORPUS.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (d / "CODEBOOK.md").write_text(BOOK)
    (d / "run.py").write_text(RUN.format(root=ROOT, grade=grade, extra=extra))
    return d


def tree(tmp_path, waivers=None):
    """proj/career holding good-evals (on the standard), small-evals (10 cases: too small, no
    holdout), bad-evals (its own main) and drafts/notes (a CODEBOOK.md, not a bank)."""
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    career = root / "career"
    make(career, "good-evals")
    make(career, "small-evals", n=10)
    make(career, "bad-evals", extra="def main():\n    pass\n")
    (career / "drafts" / "notes").mkdir(parents=True)
    (career / "drafts" / "notes" / "CODEBOOK.md").write_text("# a draft\n")
    (career / "good-evals" / "RUNS").mkdir()
    (career / "good-evals" / "RUNS" / "CODEBOOK.md").write_text("never a bank\n")
    if waivers is not None:
        write(root, waivers)
    return root


def write(root, waivers):
    (root / ".trimwrit").mkdir(exist_ok=True)
    (root / ".trimwrit" / "bank-waivers.json").write_text(json.dumps(waivers))


WAIVERS = {"since": "2026-11-14", "why": "migration",
           "banks": {"career/small-evals": ["corpus-too-small", "label-by-judged-model",
                                            "no-holdout"]},
           "exclude": ["career/drafts"]}


def strict(root, *argv):
    return cli(["--root", str(root), "bank", "check", "--strict", "--dir", "career"] + list(argv))


def test_one_standard_one_waived_one_failing_bank_exits_1_with_the_counts(tmp_path, capsys):
    root = tree(tmp_path, WAIVERS)
    assert strict(root) == 1
    out = capsys.readouterr().out
    assert "FAIL  career/bad-evals  bespoke-runner: run.py defines its own main()" in out
    assert "(fix: delete def main from" in out
    assert ("1 on the standard, 1 waived (2 rules), 1 failing, 0 custom runners, 0 hand look, "
            "1 excluded, 1 may shrink") in out
    assert "may shrink  career/small-evals  label-by-judged-model" in out
    res = bank.check_strict(str(root))
    st = {rel: e["status"] for rel, e in res["banks"].items()}
    assert st == {"career/good-evals": "standard", "career/small-evals": "waived",
                  "career/bad-evals": "failing", "career/drafts": "excluded"}
    assert res["banks"]["career/small-evals"]["waived"] == ["corpus-too-small", "no-holdout"]
    assert res["banks"]["career/small-evals"]["may_shrink"] == ["label-by-judged-model"]
    assert res["counts"]["failing"] == 1 and res["counts"]["banks"] == 3
    assert res["waiver_length"] == 3 and res["waived_banks"] == 1


def test_the_failing_bank_waived_or_a_custom_runner_exits_0(tmp_path, capsys):
    w = json.loads(json.dumps(WAIVERS))
    w["banks"]["career/bad-evals"] = ["bespoke-runner"]
    root = tree(tmp_path, w)
    assert strict(root) == 0
    w["banks"].pop("career/bad-evals")
    w["custom_runners"] = {"career/bad-evals": "its own loop, kept"}
    write(root, w)
    assert strict(root) == 0
    assert "1 custom runners" in capsys.readouterr().out
    res = bank.check_strict(str(root))
    assert res["banks"]["career/bad-evals"]["status"] == "standard"
    assert res["counts"]["custom_runners"] == 1


def test_the_json_flag_prints_the_result(tmp_path, capsys):
    root = tree(tmp_path, WAIVERS)
    assert strict(root, "--json") == 1
    res = json.loads(capsys.readouterr().out)
    assert res["counts"]["failing"] == 1 and "career/bad-evals" in res["banks"]


def test_write_waivers_refuses_an_existing_list_and_needs_a_why(tmp_path, capsys):
    root = tree(tmp_path, WAIVERS)
    before = (root / ".trimwrit" / "bank-waivers.json").read_text()
    assert strict(root, "--write-waivers", "--why", "again") == 2
    assert ("the waiver list only shrinks: use --shrink-waivers or remove entries by hand"
            in capsys.readouterr().err)
    assert (root / ".trimwrit" / "bank-waivers.json").read_text() == before
    os.remove(str(root / ".trimwrit" / "bank-waivers.json"))
    assert strict(root, "--write-waivers") == 2
    assert not (root / ".trimwrit" / "bank-waivers.json").exists()


def test_write_waivers_records_the_refusals_of_today_then_the_check_holds(tmp_path):
    root = tree(tmp_path)
    assert strict(root, "--write-waivers", "--why", "the standard landed") == 0
    text = (root / ".trimwrit" / "bank-waivers.json").read_text()
    doc = json.loads(text)
    assert text == json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=True) + "\n"
    assert doc["why"] == "the standard landed" and doc["since"]
    assert doc["banks"] == {"career/bad-evals": ["bespoke-runner"],
                            "career/drafts/notes": ["bespoke-runner", "corpus-untyped"],
                            "career/small-evals": ["corpus-too-small", "no-holdout"]}
    assert strict(root) == 0


def test_shrink_waivers_removes_a_rule_no_longer_triggered(tmp_path, capsys):
    w = json.loads(json.dumps(WAIVERS))
    w["banks"]["career/good-evals"] = ["no-holdout"]
    w["banks"]["career/gone-evals"] = ["corpus-too-small"]
    root = tree(tmp_path, w)
    assert strict(root, "--shrink-waivers") == 1   # bad-evals still fails
    out = capsys.readouterr().out
    assert "shrunk  career/small-evals  label-by-judged-model" in out
    assert "waiver list: 5 -> 2 rules" in out
    doc = json.loads((root / ".trimwrit" / "bank-waivers.json").read_text())
    assert doc["banks"] == {"career/small-evals": ["corpus-too-small", "no-holdout"]}
    assert doc["exclude"] == ["career/drafts"] and doc["why"] == "migration"


def test_a_bank_with_no_entry_runs_its_self_check(tmp_path):
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    make(root / "career", "lax-evals", grade="True")
    res = bank.check_strict(str(root))
    e = res["banks"]["career/lax-evals"]
    assert e["status"] == "failing" and "grader-cannot-fail" in {r["rule"] for r in e["refusals"]}
    res = bank.check_strict(str(root), self_check=False)
    assert res["banks"]["career/lax-evals"]["status"] == "standard"
    res = bank.check_strict(str(root), banks=["career/lax-evals"], self_check=False)
    assert list(res["banks"]) == ["career/lax-evals"]


def test_discovery_reads_evals_dirs_markers_and_nested_banks(tmp_path):
    root = tmp_path / "proj"
    c = root / "career"
    (c / "named-evals").mkdir(parents=True)            # no corpus file of that name
    (c / "named-evals" / "cases.jsonl").write_text("{}\n")
    (c / "post-evals" / "icp" / "reframe").mkdir(parents=True)
    (c / "post-evals" / "CORPUS.jsonl").write_text("")
    (c / "post-evals" / "icp" / "reframe" / "RELEASE.json").write_text("{}")
    (c / "post-evals" / "other-evals").mkdir()         # inside a bank, holds nothing
    (c / "plain").mkdir()
    (c / ".hidden-evals").mkdir()
    banks, excluded = bank.discover(str(root), "career")
    assert banks == ["career/named-evals", "career/post-evals",
                     "career/post-evals/icp/reframe"] and excluded == []
    refusals = bank.lint(str(c / "named-evals"))
    assert any(r.rule == "corpus-untyped" and r.what == "no CORPUS.jsonl" for r in refusals)
