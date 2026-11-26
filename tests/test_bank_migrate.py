"""trimwrit/bank_migrate.py: the codemod that turns a scaffold shaped run.py into the standard
shape, and the parity replay of the old runner on the new run's answers. No model call: the
banks here are deterministic."""
import contextlib
import io
import json
import os

import pytest

from trimwrit import bank
from trimwrit.cli import main as cli

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAISE = '    raise NotImplementedError("call the producer on case[\'input\']")'
PARITY = '    return "yes" if case["input"]["x"] % 2 else "no"'

# the RUN template of trimwrit/bank.py before the standard (commit d7680ba), kept verbatim: the
# shape of every bank `trimwrit bank new` laid out until then
OLD_RUN = '''#!/usr/bin/env python3
"""run.py: grade {judges_lc} on {bank_rel}/CORPUS.jsonl against the bar of CODEBOOK.md section 2;
writes RUNS/<stamp>.jsonl and RELEASE.json (the RELEASE envelope of trimwrit/bank.py).

    python3 {bank_rel}/run.py [--offline] [--no-probe] [--probe-sample N]

--offline grades each case's `expected` as if the producer had answered it: it checks the grader
and the corpus, never the producer.

When MODEL is a top-tier model (bank.SMALLER), a live run then asks ONE smaller model the same
question on a deterministic sample of the cases (--probe-sample, default 20): its rows go to
RUNS/<stamp>.probe.jsonl, its summary under `downshift` in RELEASE.json, one line to stderr.

When EFFORT is above medium (bank.EFFORT_LADDER), a live run also asks the SAME model one effort
lower on the same sampled cases: its rows go to RUNS/<stamp>.effort-probe.jsonl, its summary under
`downshift.effort` in RELEASE.json, one line to stderr. No probe ever changes `passed`.
--no-probe skips both.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
{path_block}
{import_block}

MODEL = {model_literal}
# the effort the producer runs at (low, medium, high, xhigh): above medium, the effort probe asks
# MODEL one rung lower too
EFFORT = {effort_literal}
BAR = {{"{bar_metric}": {bar_value}}}


def answer(case, model=MODEL, effort=EFFORT):
    """The producer's answer on one case, from `model` at `effort`. Call the producer's own
    function here, the same code path production runs, with `model` in place of its model id and
    `effort` in place of its effort: the downshift probe passes a smaller model
    (bank.smaller(MODEL)), the effort probe a cheaper effort (bank.cheaper_effort(EFFORT)), to see
    whether either would hold the bar. A
    model call runs lean (claude -p --bare or --safe-mode): a one-shot that loads the whole
    harness costs ten times the tokens for the same answer."""
    raise NotImplementedError("call the producer on case['input']")


def grade(case, got):
    """True when `got` is the right answer for `case`. Deterministic where possible."""
    return got == case["expected"]


def score(rows):
    n = len(rows)
    right = sum(1 for r in rows if r["right"])
    return {{"{bar_metric}": round(right / n, 4) if n else None, "right": right}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\\n")[0])
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--no-probe", action="store_true",
                    help="skip the downshift probes (smaller model, cheaper effort)")
    ap.add_argument("--probe-sample", type=int, default=bank.PROBE_SAMPLE,
                    help="cases the downshift probe asks (default %(default)s)")
    args = ap.parse_args(argv)
    cases = bank.corpus(HERE)
    stamp = bank.run_stamp()
    rows = []
    for case in cases:
        got = case["expected"] if args.offline else answer(case)
        rows.append({{"id": case["id"], "got": got, "right": bool(grade(case, got))}})
    s = score(rows)
    passed = bool(cases) and all(s[k] is not None and s[k] >= v for k, v in BAR.items())
    if args.offline:
        print(json.dumps(dict(s, n=len(rows), passed=passed), sort_keys=True))
        return 0 if passed else 1
    with open(bank.run_path(HERE, stamp), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\\n")
    extra = dict(s)
    if not args.no_probe and bank.smaller(MODEL) is not None:
        probe_rows = []
        extra["downshift"] = bank.probe(cases, rows, answer, grade, MODEL,
                                        sample=args.probe_sample, out=probe_rows)
        with open(bank.run_path(HERE, stamp, bank.PROBE_EXT), "w", encoding="utf-8") as fh:
            for r in probe_rows:
                fh.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\\n")
        print(bank.probe_line(extra["downshift"]), file=sys.stderr)
    if not args.no_probe and bank.cheaper_effort(EFFORT) is not None:
        effort_rows = []
        eff = bank.probe_effort(cases, rows, answer, grade, MODEL, EFFORT,
                                sample=args.probe_sample, out=effort_rows)
        if eff is not None:
            extra.setdefault("downshift", {{}})["effort"] = eff
            with open(bank.run_path(HERE, stamp, bank.PROBE_EFFORT_EXT), "w",
                      encoding="utf-8") as fh:
                for r in effort_rows:
                    fh.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\\n")
            print(bank.probe_effort_line(eff), file=sys.stderr)
    doc = bank.write_release(HERE, passed=passed, n=len(rows), bar=BAR, model=MODEL, run=stamp,
                             extra=extra)
    print(json.dumps(doc, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
'''


def old_bank(tmp_path, name="old-evals", model=None, effort="medium", grade_upper=False):
    """A bank laid out by the old scaffold, its answer() filled, 40 parity cases. The old
    scaffold wrote EFFORT 'medium' for a deterministic bank too."""
    d = tmp_path / name
    d.mkdir()
    rows = [{"id": "c%02d" % i, "input": {"x": i}, "expected": "yes" if i % 2 else "no",
             "why": "parity of x"} for i in range(40)]
    (d / "CORPUS.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    files = bank.plan("old", "Judge parity", "accuracy >= 0.9", name)
    (d / "CODEBOOK.md").write_text(files[name + "/CODEBOOK.md"])
    text = OLD_RUN.format(judges_lc="judge parity", bank_rel=name,
                          path_block="sys.path.insert(0, {!r})".format(ROOT),
                          import_block=bank.DEFAULT_IMPORT, model_literal=repr(model),
                          effort_literal=repr(effort), bar_metric="accuracy", bar_value=0.9)
    text = text.replace(RAISE, PARITY)
    if grade_upper:
        text = text.replace('    return got == case["expected"]',
                            '    return str(got).upper() == case["expected"]')
    (d / "run.py").write_text(text)
    return d


def run_main(d, *argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = bank.main(str(d / "run.py"), argv=list(argv))
    return code, out.getvalue(), err.getvalue()


def rules(dicts):
    return sorted({r["rule"] for r in dicts})


LABEL = "the parity of x, computed"


# ------------------------------------------------------------------ migrate

def test_migrate_reads_the_old_scaffold_into_the_standard_shape(tmp_path):
    d = old_bank(tmp_path)
    before = (d / "run.py").read_text()
    assert "bespoke-runner" in {r.rule for r in bank.lint(str(d))}
    res = bank.migrate(str(d), label_source=LABEL)
    assert (d / "run.py").read_text() == before, "write=False writes nothing"
    assert res["bank"] == str(d) and res["old_text"] == before
    new = res["new_text"]
    assert "def main" not in new and "argparse" not in new
    assert new.endswith('\n\nif __name__ == "__main__":\n    sys.exit(bank.main(__file__))\n')
    assert "LABEL_SOURCE = 'the parity of x, computed'" in new
    assert new.index("BAR = ") < new.index("LABEL_SOURCE = ") < new.index("def answer")
    assert "EFFORT = None" in new, "a deterministic bank carries no effort"
    assert res["removed"] == ["import argparse", "import json",
                              "EFFORT = 'medium' (MODEL is None)",
                              "def main", 'if __name__ == "__main__"']
    assert res["lines_before"] == len(before.splitlines())
    assert res["lines_after"] == len(new.splitlines()) < res["lines_before"]
    assert res["refusals_left"] == []
    # every other line is kept byte identical, in order: the docstring, comments, score()
    kept = iter(new.splitlines())
    for line in before.splitlines()[:before.splitlines().index("import argparse")]:
        assert line in kept, line
    score = before[before.index("def score(rows):"):before.index("def main(")].rstrip()
    assert score in new
    assert "# the effort the producer runs at" in new


def test_migrate_does_not_count_the_old_release_as_a_runner_left(tmp_path):
    """The bank's schema 1 RELEASE.json is the old runner's: the migrated run writes the schema 2
    one, so it is no refusal left of the codemod (0.16.2 listed it and a caller saw every scaffold
    bank as unmigratable)."""
    import json as _json
    d = old_bank(tmp_path)
    (d / "RELEASE.json").write_text(_json.dumps({"schema": 1, "passed": True, "n": 40,
                                                  "at": "2026-11-01T00:00:00+00:00"}))
    assert any("did not write it" in r.what for r in bank.lint(str(d)))
    for write in (False, True):
        res = bank.migrate(str(d), label_source=LABEL, write=write)
        assert not [r for r in res["refusals_left"] if "did not write it" in r["what"]], write


def test_a_migrated_bank_runs_on_bank_main_and_replays_the_old_runner_to_parity(tmp_path):
    d = old_bank(tmp_path)
    legacy = tmp_path / "legacy_run.py"
    legacy.write_text((d / "run.py").read_text())
    res = bank.migrate(str(d), label_source=LABEL, write=True)
    assert (d / "run.py").read_text() == res["new_text"]
    assert not [r for r in bank.lint(str(d)) if r.rule == "bespoke-runner"]
    code, _, err = run_main(d)
    assert code == 0, err
    doc = bank.read_release(str(d))
    assert doc["schema"] == 2 and doc["passed"] is True and doc["accuracy"] == 1.0
    p = bank.parity(str(d), str(legacy))
    assert p["parity"] is True and p["diffs"] == []
    assert p["old"] == {"passed": True, "n": 40, "accuracy": 1.0} == p["new"]
    assert os.listdir(str(d / "RUNS")) == [doc["run"] + ".jsonl"], \
        "the old runner's rows go to a temp dir, never the bank"
    assert bank.read_release(str(d)) == doc, "the old runner's release is captured, not written"


def test_parity_misses_when_the_old_grader_differs(tmp_path):
    d = old_bank(tmp_path)
    upper = old_bank(tmp_path, name="upper-evals", grade_upper=True) / "run.py"
    bank.migrate(str(d), label_source=LABEL, write=True)
    assert run_main(d)[0] == 0
    p = bank.parity(str(d), str(upper))
    assert p["parity"] is False
    assert p["old"]["accuracy"] == 0.0 and p["old"]["passed"] is False
    assert any("accuracy" in x for x in p["diffs"]) and any("passed" in x for x in p["diffs"])


def test_parity_of_an_old_runner_that_raises_names_the_exception(tmp_path):
    d = old_bank(tmp_path)
    legacy = tmp_path / "legacy_run.py"
    legacy.write_text((d / "run.py").read_text().replace(
        "    cases = bank.corpus(HERE)", "    raise RuntimeError('the old loop broke')"))
    bank.migrate(str(d), label_source=LABEL, write=True)
    assert run_main(d)[0] == 0
    p = bank.parity(str(d), str(legacy))
    assert p["parity"] is False and any("the old loop broke" in x for x in p["diffs"])


def test_parity_reads_given_rows_and_needs_a_schema_2_release(tmp_path):
    d = old_bank(tmp_path)
    legacy = tmp_path / "legacy_run.py"
    legacy.write_text((d / "run.py").read_text())
    bank.migrate(str(d), label_source=LABEL, write=True)
    with pytest.raises(bank.BankError, match="RELEASE.json"):
        bank.parity(str(d), str(legacy))
    assert run_main(d)[0] == 0
    rows = tmp_path / "rows.jsonl"
    rows.write_text("".join(json.dumps({"id": "c%02d" % i, "got": "no"}) + "\n"
                            for i in range(40)))
    p = bank.parity(str(d), str(legacy), rows_path=str(rows))
    assert p["parity"] is False and p["old"]["accuracy"] == 0.5


def test_migrate_keeps_argparse_used_elsewhere_and_adds_import_sys(tmp_path):
    d = old_bank(tmp_path)
    text = (d / "run.py").read_text()
    text = text.replace("import sys\n", "").replace(
        "def grade(case, got):",
        "def flags():\n    # a helper of its own\n    return argparse.Namespace()\n\n\n"
        "def grade(case, got):")
    (d / "run.py").write_text(text)
    res = bank.migrate(str(d))
    assert "import argparse" in res["new_text"] and "import argparse" not in res["removed"]
    assert "\nimport sys\n" in res["new_text"] and "# a helper of its own" in res["new_text"]
    assert "LABEL_SOURCE" not in res["new_text"], "no label source given, none written"
    left = rules(res["refusals_left"])
    assert "bespoke-runner" in left and "label-by-judged-model" in left
    assert all(r["bank"] == str(d) for r in res["refusals_left"]), "paths name the bank"


@pytest.mark.parametrize("text, why", [("def answer(:\n", "does not parse"),
                                       ("def answer(case):\n    return 1\n", "grade"),
                                       ("def grade(case, got):\n    return 1\n", "answer")])
def test_migrate_refuses_a_run_py_it_cannot_read(tmp_path, text, why):
    d = tmp_path / "x-evals"
    d.mkdir()
    (d / "run.py").write_text(text)
    with pytest.raises(bank.BankError, match=why):
        bank.migrate(str(d))
    assert (d / "run.py").read_text() == text


# ------------------------------------------------------------------ the commands

def test_the_migrate_and_parity_commands(tmp_path, capsys):
    d = old_bank(tmp_path)
    before = (d / "run.py").read_text()
    legacy = tmp_path / "keep" / "run_legacy.py"
    assert cli(["--root", str(tmp_path), "bank", "migrate", "old-evals"]) == 0
    out = capsys.readouterr().out
    assert "-def main(argv=None):" in out and (d / "run.py").read_text() == before
    assert cli(["--root", str(tmp_path), "bank", "migrate", "old-evals", "--write",
                "--label-source", LABEL, "--legacy-out", str(legacy), "--json"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["refusals_left"] == [] and "old_text" not in res and "new_text" not in res
    assert legacy.read_text() == before and (d / "run.py").read_text() != before
    assert run_main(d)[0] == 0
    capsys.readouterr()
    assert cli(["--root", str(tmp_path), "bank", "parity", "old-evals", "--legacy",
                str(legacy)]) == 0
    assert "parity" in capsys.readouterr().out
    upper = old_bank(tmp_path, name="upper-evals", grade_upper=True) / "run.py"
    assert cli(["--root", str(tmp_path), "bank", "parity", "old-evals", "--legacy",
                str(upper), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["parity"] is False


# ------------------------------------------------------------------ review T4 fixes

def _plain(d, text):
    d.mkdir()
    (d / "run.py").write_bytes(text.encode("utf-8"))
    return d


def test_import_sys_goes_below_a_shebang_and_a_coding_line(tmp_path):
    d = _plain(tmp_path / "x-evals", "#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n"
               "from trimwrit import bank\n\ndef answer(case):\n    return 1\n\n\n"
               "def grade(case, got):\n    return got == 1\n")
    new = bank.migrate(str(d))["new_text"]
    assert new.startswith("#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\nimport sys\n"), new


def test_migrate_keeps_a_trailing_comment_and_the_line_ending(tmp_path):
    src = ("import argparse, json  # the flags and the rows\nimport sys\nfrom trimwrit import bank\n"
           "\n\ndef answer(case):\n    return 1\n\n\ndef grade(case, got):\n    return got == 1\n"
           "\n\ndef main():\n    return 0\n\n\nif __name__ == \"__main__\":\n    sys.exit(main())\n")
    new = bank.migrate(str(_plain(tmp_path / "lf-evals", src)))["new_text"]
    assert new.startswith("import json  # the flags and the rows\n")
    crlf = bank.migrate(str(_plain(tmp_path / "crlf-evals", src.replace("\n", "\r\n"))))
    assert "\n" not in crlf["new_text"].replace("\r\n", "")
    assert crlf["new_text"].endswith('sys.exit(bank.main(__file__))\r\n')


def test_a_remaining_reference_to_main_is_reported(tmp_path):
    d = old_bank(tmp_path)
    (d / "run.py").write_text((d / "run.py").read_text().replace(
        "def grade(case, got):", "def cli():\n    return main()\n\n\ndef grade(case, got):"))
    res = bank.migrate(str(d), label_source=LABEL)
    hits = [r for r in res["refusals_left"] if "main is still referenced at line" in r["what"]]
    assert hits and hits[0]["rule"] == "bespoke-runner"


def test_a_runner_reaching_the_kit_by_another_name_writes_nothing_in_the_bank(tmp_path):
    """M-3: `import trimwrit.bank as kit` and `from trimwrit.bank import write_release` reach
    the same capture."""
    d = old_bank(tmp_path)
    legacy = tmp_path / "legacy_run.py"
    legacy.write_text((d / "run.py").read_text()
                      .replace("from trimwrit import bank  # noqa: E402",
                               "from trimwrit import bank  # noqa: E402\n"
                               "import trimwrit.bank as kit  # noqa: E402\n"
                               "from trimwrit.bank import run_path  # noqa: E402")
                      .replace("doc = bank.write_release(", "doc = kit.write_release(")
                      .replace("open(bank.run_path(HERE, stamp)", "open(run_path(HERE, stamp)"))
    bank.migrate(str(d), label_source=LABEL, write=True)
    assert run_main(d)[0] == 0
    doc = bank.read_release(str(d))
    p = bank.parity(str(d), str(legacy))
    assert p["parity"] is True, p
    assert bank.read_release(str(d)) == doc and os.listdir(str(d / "RUNS")) == [
        doc["run"] + ".jsonl"]
    assert bank.write_release.__module__ == "trimwrit.bank", "the kit is restored"


def test_a_legacy_exit_at_import_and_a_silent_miss_say_why(tmp_path):
    d = old_bank(tmp_path)
    bank.migrate(str(d), label_source=LABEL, write=True)
    assert run_main(d)[0] == 0
    old = old_bank(tmp_path, name="exit-evals") / "run.py"
    exits = tmp_path / "exits.py"
    exits.write_text(old.read_text().replace("HERE = os.path", "sys.exit(3)\nHERE = os.path"))
    p = bank.parity(str(d), str(exits))
    assert p["parity"] is False and any("SystemExit" in x for x in p["diffs"])
    quiet = tmp_path / "quiet.py"
    quiet.write_text(old.read_text().replace(
        "    cases = bank.corpus(HERE)",
        "    print('refused: the old door is shut', file=sys.stderr)\n    return 2"))
    p = bank.parity(str(d), str(quiet))
    assert p["parity"] is False
    assert any("wrote no release" in x and "the old door is shut" in x for x in p["diffs"])


# ------------------------------------------------------------------ final review fixes

MODEL_RUN = ("import argparse\nimport sys\nfrom trimwrit import bank\n\n"
             "MODEL = 'claude-sonnet-5-5'\nBAR = {'accuracy': 0.9}\nLABEL_SOURCE = 'a person'\n"
             "\n\ndef answer(case, model=MODEL):\n    return 'yes'\n\n\n"
             "def grade(case, got):\n    return got == case['expected']\n\n\n"
             "def main(argv=None):\n    return 0\n\n\n"
             "if __name__ == \"__main__\":\n    sys.exit(main())\n")


def test_a_model_bank_with_no_effort_gets_the_cli_default_when_nothing_names_one(tmp_path):
    d = _plain(tmp_path / "m-evals", MODEL_RUN)
    (d / "test_m.py").write_text("EFFORT_SEEN = 'effort' # tests never count\n")
    res = bank.migrate(str(d))
    assert "MODEL = 'claude-sonnet-5-5'\nEFFORT = \"model-default\"\n" in res["new_text"]
    assert "model-not-pinned" not in rules(res["refusals_left"])


def test_a_model_bank_whose_code_names_an_effort_keeps_effort_absent(tmp_path):
    d = _plain(tmp_path / "m-evals", MODEL_RUN)
    (d / "producer.py").write_text("def call(prompt, effort='high'):\n    return prompt\n")
    res = bank.migrate(str(d))
    assert "EFFORT" not in res["new_text"]
    hits = [r for r in res["refusals_left"] if r["rule"] == "model-not-pinned"]
    assert hits and "EFFORT absent" in hits[0]["what"]


# ------------------------------------------------------------------ row fields the old main() wrote

ROW_LINE = 'rows.append({"id": case["id"], "got": got, "right": bool(grade(case, got))})'
SCORE_LINE = 'right = sum(1 for r in rows if r["right"])'
SCORE_RET = 'return {"accuracy": round(right / n, 4) if n else None, "right": right}'


def fielded(tmp_path, row_src, score_src, name="fielded-evals"):
    """The old scaffold bank with its main() row and its score() replaced: the row fields a
    score() reads that only the old main() wrote (the shape of 18 banks, 2026-11-25)."""
    d = old_bank(tmp_path, name=name)
    text = (d / "run.py").read_text()
    assert ROW_LINE in text and SCORE_LINE in text
    text = text.replace(ROW_LINE, row_src).replace(SCORE_LINE, score_src)
    (d / "run.py").write_text(text)
    return d


def migrate_and_run(d):
    legacy = d.parent / (d.name + "_legacy.py")
    legacy.write_text((d / "run.py").read_text())
    res = bank.migrate(str(d), label_source=LABEL, write=True)
    code, _, err = run_main(d)
    return res, legacy, code, err


def test_a_row_field_score_reads_rides_grade_and_the_rows_keep_it(tmp_path):
    """The failure (autofix bank-migrate:career/recruitroast-evals, KeyError 'expected_pass' in
    score()): the old main() wrote expected_pass into the row, bank.main does not, so score()
    died. The codemod now returns the field from grade(), which bank.main copies into the row."""
    d = fielded(tmp_path,
                'rows.append({"id": case["id"], "got": got, "right": bool(grade(case, got)),\n'
                '                         "expected_pass": case["expected"] == "yes"})',
                'bad = [r for r in rows if not r["expected_pass"]]\n'
                '    right = sum(1 for r in rows if r["right"]) - len(bad) + len(bad)')
    res, legacy, code, err = migrate_and_run(d)
    assert res["refusals_left"] == [] and code == 0, err
    assert "grade() carries the row field expected_pass" in res["added"]
    run = [json.loads(x) for x in (d / "RUNS" / (bank.read_release(str(d))["run"] + ".jsonl"))
           .read_text().splitlines()]
    assert {r["id"]: r["expected_pass"] for r in run} == {r["id"]: r["id"] and
                                                         (int(r["id"][1:]) % 2 == 1) for r in run}
    p = bank.parity(str(d), str(legacy))
    assert p["parity"] is True and p["diffs"] == []


def test_every_field_shape_of_the_old_rows_is_carried(tmp_path):
    """A subscript chain, a .get(), a multi line dict with several fields, a module level name."""
    d = fielded(tmp_path,
                'rows.append({"id": case["id"], "kind": case.get("why"),\n'
                '                         "expected": case["expected"], "got": got,\n'
                '                         "tag": TAG + "!", "right": bool(grade(case, got))})',
                'by_kind = {r["kind"]: r["expected"] for r in rows}\n'
                '    assert len(by_kind) == 1 and all(r["tag"] == "t!" for r in rows)\n'
                '    right = sum(1 for r in rows if r["right"])')
    (d / "run.py").write_text((d / "run.py").read_text().replace("\nMODEL =", '\nTAG = "t"\nMODEL =', 1))
    res, legacy, code, err = migrate_and_run(d)
    assert res["refusals_left"] == [] and code == 0, err
    assert "grade() carries the row fields expected, kind, tag" in res["added"]
    assert bank.parity(str(d), str(legacy))["parity"] is True


def test_the_carried_grade_stays_truthy_only_when_the_verdict_is(tmp_path):
    d = fielded(tmp_path,
                'rows.append({"id": case["id"], "got": got, "right": bool(grade(case, got)),\n'
                '                         "expected_pass": case["expected"] == "yes"})',
                'bad = [r for r in rows if not r["expected_pass"]]\n    right = len(rows) - len(bad) + len(bad)')
    bank.migrate(str(d), label_source=LABEL, write=True)
    import importlib.util
    spec = importlib.util.spec_from_file_location("fielded_run", str(d / "run.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    case = {"id": "c01", "input": {"x": 1}, "expected": "yes"}
    assert mod.grade(case, "yes") and not mod.grade(case, "no"), "a test's assert/not reads bool"
    assert mod.grade(case, "no") == {"right": False, "expected_pass": True}
    assert mod.grade(case, "yes")["right"] is True


def test_a_field_score_does_not_read_is_left_out(tmp_path):
    d = fielded(tmp_path,
                'rows.append({"id": case["id"], "got": got, "extra": case["why"],\n'
                '                         "right": bool(grade(case, got))})',
                SCORE_LINE)
    res = bank.migrate(str(d), label_source=LABEL)
    assert "_grade_verdict" not in res["new_text"] and res["refusals_left"] == []


def test_a_field_built_from_the_answer_is_left_to_a_person(tmp_path):
    """score() reads `fails`, which main() got from answer()'s second member: the case alone
    cannot rebuild it, so the codemod refuses it by name instead of migrating a bank that dies."""
    d = fielded(tmp_path,
                'rows.append({"id": case["id"], "got": got, "fails": [got],\n'
                '                         "right": bool(grade(case, got))})',
                'bad = [r for r in rows if r["fails"]]\n    right = len(rows) - len(bad) + len(bad)')
    res = bank.migrate(str(d), label_source=LABEL)
    left = [r for r in res["refusals_left"] if "'fails'" in r["what"]]
    assert left and left[0]["rule"] == "bespoke-runner" and "got" in left[0]["what"]
    assert "_grade_verdict" not in res["new_text"]


def test_a_grade_that_returns_a_dict_keeps_its_own_keys(tmp_path):
    d = fielded(tmp_path,
                'rows.append({"id": case["id"], "got": got, "right": bool(grade(case, got)),\n'
                '                         "expected_pass": case["expected"] == "yes"})',
                'bad = [r for r in rows if not r["expected_pass"]]\n    right = len(rows) - len(bad) + len(bad)')
    text = (d / "run.py").read_text().replace(
        '    return got == case["expected"]',
        '    return {"right": got == case["expected"], "note": "n"}')
    (d / "run.py").write_text(text)
    bank.migrate(str(d), label_source=LABEL, write=True)
    import importlib.util
    spec = importlib.util.spec_from_file_location("fielded_run2", str(d / "run.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    got = mod.grade({"id": "c", "expected": "yes"}, "yes")
    assert got == {"right": True, "note": "n", "expected_pass": True}


def test_a_module_only_the_removed_main_read_goes_with_it_and_a_used_one_stays(tmp_path):
    """json was read by main() alone: left behind it is an F401 the repository's ruff refuses
    (growth-cockpit career/recruitroast-evals/run.py, 2026-11-25). os feeds HERE, so it stays."""
    d = old_bank(tmp_path)
    new = bank.migrate(str(d))["new_text"]
    assert "import json" not in new and "import os\n" in new and "import sys\n" in new
    import ast
    ast.parse(new)


def test_a_helper_that_asks_grade_for_a_truth_value_does_not_block_the_migration(tmp_path):
    """recycle-evals production_cases() reads `bool(grade(case, answer(case)))`: Graded is truthy
    exactly when right, so it migrates; a helper that compares or stores the return is refused."""
    row = ('rows.append({"id": case["id"], "got": got, "right": bool(grade(case, got)),\n'
           '                         "expected_pass": case["expected"] == "yes"})')
    score = 'bad = [r for r in rows if not r["expected_pass"]]\n    right = len(rows) - len(bad) + len(bad)'
    ok = fielded(tmp_path, row, score, name="truthy-evals")
    (ok / "run.py").write_text((ok / "run.py").read_text().replace(
        "def score(rows):", "def helper(case):\n    return bool(grade(case, 'yes')) and not grade(case, 'no')\n\n\n"
        "def score(rows):"))
    assert bank.migrate(str(ok), label_source=LABEL)["refusals_left"] == []
    bad = fielded(tmp_path, row, score, name="stored-evals")
    (bad / "run.py").write_text((bad / "run.py").read_text().replace(
        "def score(rows):", "def helper(case):\n    return grade(case, 'yes') is True\n\n\n"
        "def score(rows):"))
    assert any("grade()" in r["what"] for r in bank.migrate(str(bad), label_source=LABEL)["refusals_left"])


def test_a_main_that_unpacked_answer_or_a_score_that_wants_the_cases_is_left_to_a_person(tmp_path):
    """recruitermail-evals: `got, fails = answer(case)` (migrated, every case graded wrong);
    betbuild, msgeval, oss: `score(rows, cases)` (TypeError). Both are named, never migrated."""
    row = ('rows.append({"id": case["id"], "got": got, "right": bool(grade(case, got))})')
    d = fielded(tmp_path, row, SCORE_LINE, name="unpack-evals")
    t = (d / "run.py").read_text().replace("got = case[\"expected\"] if args.offline else answer(case)",
                                           "got, fails = case[\"expected\"], [] if args.offline else answer(case)")
    (d / "run.py").write_text(t)
    assert any("unpacks the return of answer()" in r["what"]
               for r in bank.migrate(str(d), label_source=LABEL)["refusals_left"])
    c = fielded(tmp_path, row, SCORE_LINE, name="cases-evals")
    (c / "run.py").write_text((c / "run.py").read_text().replace("def score(rows):", "def score(rows, cases):")
                              .replace("s = score(rows)", "s = score(rows, cases)"))
    assert any("score() takes cases" in r["what"]
               for r in bank.migrate(str(c), label_source=LABEL)["refusals_left"])


def test_graded_equals_a_bool_by_its_verdict_and_a_dict_as_a_dict():
    g = bank.Graded(True, {"expected_pass": False})
    assert g == True and not (g == False) and g != False and g == {"right": True, "expected_pass": False}  # noqa: E712
    assert bank.Graded(False) == False and bank.Graded({"right": False, "n": 1}) == {"right": False, "n": 1}  # noqa: E712
    assert bool(bank.Graded(0)) is False and bool(bank.Graded("x")) is True


# ------------------------------------------------------------------ the two part answer

TWO_ANSWER = ('    verdict = "yes" if case["input"]["x"] % 2 else "no"\n'
              '    return verdict, [] if verdict == "yes" else ["even"]')
TWO_UNPACK = 'got, fails = (case["expected"], []) if args.offline else answer(case)'
TWO_ROW = ('rows.append({"id": case["id"], "kind": case["why"], "got": got, "fails": fails,\n'
           '                     "right": bool(grade(case, got))})')
TWO_SCORE = ('by_kind = {r["kind"]: r["right"] for r in rows}\n'
             '    assert by_kind\n'
             '    right = sum(1 for r in rows if r["right"])')


def two_part(tmp_path, name="two-evals", row=TWO_ROW, unpack=TWO_UNPACK, score=TWO_SCORE,
             ret=SCORE_RET, answer=TWO_ANSWER):
    """The scaffold bank whose answer() returns (verdict, failed checks) and whose old main()
    split them (the shape of nine growth-cockpit cold mail door banks)."""
    d = fielded(tmp_path, row, score, name=name)
    text = (d / "run.py").read_text()
    old_unpack = 'got = case["expected"] if args.offline else answer(case)'
    assert old_unpack in text and PARITY in text and SCORE_RET in text
    text = (text.replace(old_unpack, unpack).replace(PARITY, answer).replace(SCORE_RET, ret))
    (d / "run.py").write_text(text)
    return d


def load_run(d):
    import importlib.util
    spec = importlib.util.spec_from_file_location("two_run_" + d.name.replace("-", "_"),
                                                  str(d / "run.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_two_part_answer_is_split_and_the_rows_keep_the_failed_checks(tmp_path):
    d = two_part(tmp_path)
    res, legacy, code, err = migrate_and_run(d)
    assert res["refusals_left"] == [] and code == 0, err
    assert "grade() reads the verdict of answer()'s two parts and keeps fails" in res["added"]
    rel = bank.read_release(str(d))
    rows = [json.loads(x) for x in (d / "RUNS" / (rel["run"] + ".jsonl")).read_text().splitlines()]
    assert rows and all(r["right"] for r in rows), rows
    assert {tuple(r["fails"]) for r in rows} == {(), ("even",)}
    assert all(r["kind"] for r in rows)
    p = bank.parity(str(d), str(legacy))
    assert p["parity"] is True and p["diffs"] == []
    mod = load_run(d)
    case = {"id": "c", "why": "w", "input": {"x": 1}, "expected": "yes"}
    assert mod.grade(case, "yes") and not mod.grade(case, "no"), "a bare verdict still grades"
    assert mod.grade(case, mod.answer(case)) and mod.grade(case, ["yes", []])
    assert not mod.grade(case, ["no", ["even"]])


def test_a_migrated_deterministic_bank_answers_one_case_at_a_time(tmp_path):
    """The old main() was serial: a door that loads its producer on first use raised on threads."""
    d = two_part(tmp_path)
    res = bank.migrate(str(d), label_source=LABEL)
    assert "JOBS = 1" in res["added"] and "\nJOBS = 1\n" in res["new_text"]
    m = _plain(tmp_path / "m-evals", MODEL_RUN)
    assert "JOBS" not in bank.migrate(str(m))["new_text"], "a model bank keeps the threads"
    (d / "run.py").write_text((d / "run.py").read_text().replace(
        "\nMODEL =", "\nJOBS = 3\nMODEL =", 1))
    assert "JOBS = 1" not in bank.migrate(str(d), label_source=LABEL)["new_text"]


def test_a_two_part_answer_score_may_read_the_failed_checks(tmp_path):
    d = two_part(tmp_path, score='assert all(isinstance(r["fails"], list) for r in rows)\n'
                                 '    right = sum(1 for r in rows if r["right"])')
    res, legacy, code, err = migrate_and_run(d)
    assert res["refusals_left"] == [] and code == 0, err
    assert bank.parity(str(d), str(legacy))["parity"] is True


def test_a_score_that_returns_n_leaves_it_to_the_runner(tmp_path):
    """bank.main owns `n` and refuses a score() returning it (seven of the nine banks)."""
    d = two_part(tmp_path, ret=SCORE_RET[:-1] + ', "n": n}')
    text = (d / "run.py").read_text()
    assert "extra = dict(s)" in text
    (d / "run.py").write_text(text.replace(
        "extra = dict(s)", 'extra = {k: v for k, v in s.items() if k != "n"}'))
    res, legacy, code, err = migrate_and_run(d)
    assert res["refusals_left"] == [] and code == 0, err
    assert "score() leaves n to the standard runner" in res["added"]
    assert bank.read_release(str(d))["n"] > 0
    p = bank.parity(str(d), str(legacy))
    assert p["parity"] is True, p


@pytest.mark.parametrize("change", [
    # score() reads got off the row, which now holds both parts
    {"score": 'right = sum(1 for r in rows if r["got"] and r["right"])'},
    # main() rewrites the verdict before grading it
    {"unpack": TWO_UNPACK + "\n        got = got.lower()"},
    # main() keeps something other than the second member itself
    {"row": TWO_ROW.replace('"fails": fails', '"fails": len(fails)')},
    # main() grades something else than the verdict
    {"row": TWO_ROW.replace("grade(case, got)", "grade(case, (got, fails))")},
    # answer() does not always return two parts
    {"answer": '    if not case:\n        return None\n' + TWO_ANSWER},
])
def test_any_other_unpacking_is_still_left_to_a_person(tmp_path, change):
    d = two_part(tmp_path, **change)
    res = bank.migrate(str(d), label_source=LABEL)
    assert any("unpacks the return of answer()" in r["what"] for r in res["refusals_left"])
    assert "_verdict, _checks" not in res["new_text"]


# ------------------------------------------------------------------ the rehearsal

def rehearsed(tmp_path, change_old=lambda t: t, change_new=lambda t: t, **kw):
    """The old bank after one run of its OLD runner (the recorded rows), rehearsed against the
    codemod's new text; `change_old` edits the old runner first, `change_new` the new one."""
    d = old_bank(tmp_path, **kw)
    (d / "run.py").write_text(change_old((d / "run.py").read_text()))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert bank.load_module(str(d / "run.py")).main(["--no-probe"]) == 0
    legacy = tmp_path / "legacy_run.py"
    legacy.write_text((d / "run.py").read_text())
    new = change_new(bank.migrate(str(d), label_source=LABEL)["new_text"])
    before = tree_bytes(d)
    res = bank.rehearse(str(d), new, str(legacy))
    assert tree_bytes(d) == before, "a rehearsal writes nothing in the bank"
    return res


def tree_bytes(d):
    out = {}
    for dirpath, dirs, files in os.walk(str(d)):
        dirs[:] = [x for x in dirs if x != "__pycache__"]
        for f in files:
            with open(os.path.join(dirpath, f), "rb") as fh:
                out[os.path.join(dirpath, f)] = fh.read()
    return out


def test_a_rehearsal_of_a_faithful_migration_is_ok(tmp_path):
    res = rehearsed(tmp_path)
    assert res == {"result": "ok", "cases": 40, "diffs": []}


def test_a_rehearsal_names_the_crash_of_a_score_that_reads_a_row_key_the_runner_drops(tmp_path):
    res = rehearsed(tmp_path, change_new=lambda t: t.replace(
        'right = sum(1 for r in rows if r["right"])',
        'right = sum(1 for r in rows if r["right"] and r["kind"])'))
    assert res["result"] == "miss" and "KeyError" in res["diffs"][0], res


def test_a_rehearsal_names_a_result_the_new_release_loses(tmp_path):
    res = rehearsed(tmp_path, change_old=lambda t: t.replace(
        "    extra = dict(s)\n", "    extra = dict(s, gate_sha='abc', by={'a': 1})\n"))
    assert res["result"] == "miss"
    assert "lost: by" in res["diffs"] and "lost: gate_sha" in res["diffs"], res


def test_a_rehearsal_names_a_value_that_changed(tmp_path):
    res = rehearsed(tmp_path, change_new=lambda t: t.replace(
        'return got == case["expected"]', 'return got == "yes"'))
    assert res["result"] == "miss"
    assert any(x.startswith("accuracy: old 1.0") for x in res["diffs"]), res


def test_a_rehearsal_gives_back_an_answer_the_old_rows_stored_field_by_field(tmp_path):
    def old(t):
        t = t.replace(PARITY, '    return {"v": "yes" if case["input"]["x"] % 2 else "no", '
                              '"why": ["x"]}')
        t = t.replace('return got == case["expected"]',
                      'return (got["v"] if isinstance(got, dict) else got) == case["expected"]')
        t = t.replace('got = case["expected"] if args.offline else answer(case)',
                      'got = {"v": case["expected"]} if args.offline else answer(case)')
        return t.replace('"got": got, "right"', '"got": got["v"], "why": got.get("why") or [], '
                                                '"right"')
    res = rehearsed(tmp_path, change_old=old)
    assert res["result"] == "ok", res


def test_a_rehearsal_with_nothing_recorded_says_so(tmp_path):
    d = old_bank(tmp_path)
    legacy = tmp_path / "legacy_run.py"
    legacy.write_text((d / "run.py").read_text())
    res = bank.rehearse(str(d), bank.migrate(str(d))["new_text"], str(legacy))
    assert res["result"] == "no-answers" and res["cases"] == 0


def test_an_old_runner_that_cannot_replay_says_nothing_about_the_new_one(tmp_path):
    d = old_bank(tmp_path)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert bank.load_module(str(d / "run.py")).main(["--no-probe"]) == 0
    new = bank.migrate(str(d), label_source=LABEL)["new_text"]
    legacy = tmp_path / "legacy_run.py"
    legacy.write_text((d / "run.py").read_text().replace(
        "    extra = dict(s)\n", "    raise TypeError('old')\n"))
    res = bank.rehearse(str(d), new, str(legacy))
    assert res["result"] == "no-baseline" and "TypeError" in res["diffs"][0], res


def test_a_rehearsal_meets_a_refused_call_before_the_paid_run_does(tmp_path):
    # growth-cockpit 2026-11-26: a converted score() read a grade field of every row, and the
    # first refused call of a paid run crashed it after the other calls were paid
    res = rehearsed(tmp_path, change_new=lambda t: t.replace(
        "def score(rows):\n", "def score(rows):\n    assert all(r['got'] is not None for r in rows)\n"))
    assert res["result"] == "miss" and res["diffs"][0].startswith("with one case unanswered"), res
