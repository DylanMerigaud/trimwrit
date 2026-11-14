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
    assert res["removed"] == ["import argparse", "EFFORT = 'medium' (MODEL is None)",
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
