"""End to end through the command line, with no model call. `run` is covered by the dogfood
suite, which needs a real Claude; everything else is exercised here the way a skill calls it."""
import os

from trimwrit.cli import main

EM = chr(0x2014)


def _run(tmp_path, *argv):
    return main(["--root", str(tmp_path)] + list(argv))


def test_the_whole_loop(tmp_path, capsys):
    assert _run(tmp_path, "log", "you used an em dash again", "--tag", "em-dash",
                "--session", "s1") == 0
    assert _run(tmp_path, "log", "another em dash in the commit", "--tag", "em-dash",
                "--session", "s2") == 0
    _run(tmp_path, "pending")
    assert "em-dash" in capsys.readouterr().out

    assert _run(tmp_path, "case", "0002", "--title", "no em dash",
                "--prompt", "Write two flowing paragraphs with some rhythm.",
                "--forbid", "[" + EM + "]") == 0
    assert os.path.isdir(tmp_path / "evals" / "0002-no-em-dash")

    assert _run(tmp_path, "integrate", "0002", "--target", "CLAUDE.md",
                "--incident", "a post went out with three of them",
                "--rule", "Never use an em dash.") == 0
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "trimwrit: R0002 case 0002" in text and "Never use an em dash." in text

    _run(tmp_path, "pending")
    assert "nothing pending" in capsys.readouterr().out


def test_integrate_refuses_before_a_case_exists(tmp_path, capsys):
    _run(tmp_path, "log", "do not do that", "--tag", "t")
    assert _run(tmp_path, "integrate", "0001", "--rule", "the rule") == 2
    assert "no case yet" in capsys.readouterr().err


def test_case_refuses_with_no_grader(tmp_path, capsys):
    _run(tmp_path, "log", "do not do that", "--tag", "t")
    assert _run(tmp_path, "case", "0001", "--prompt", "replay it") == 2
    assert "can never fail" in capsys.readouterr().err


def test_case_refuses_with_no_prompt(tmp_path, capsys):
    _run(tmp_path, "log", "do not do that", "--tag", "t")
    assert _run(tmp_path, "case", "0001", "--forbid", "x") == 2
    assert "REPLAYS the situation" in capsys.readouterr().err


def test_a_judge_only_case_is_written_but_warned_about(tmp_path, capsys):
    _run(tmp_path, "log", "the decision was buried", "--tag", "d")
    assert _run(tmp_path, "case", "0001", "--prompt", "replay", "--judge", "is it short") == 0
    assert "only a judge" in capsys.readouterr().out


# ---------------------------------------------------------------- --must-match NAME= routing
#
# The incident: --must-match landed on every regex grader in the case (case wide), so a case
# mixing --forbid and --require could not be written through the CLI at all, a forbid grader
# needs a BAD sample as its proof and a require grader needs a GOOD one, and one case wide list
# cannot be both. The fix reads an optional `NAME=` prefix off each --must-match/--must-not-match
# value: the grader's own `name`, or the shorthand `forbid`/`require` for every grader of that
# kind. No `=` before the first space keeps the old case wide meaning.

def test_must_match_prefix_targets_one_grader_by_shorthand(tmp_path):
    from trimwrit import cases

    _run(tmp_path, "log", "mixes a forbid and a require sample", "--tag", "mix")
    assert _run(tmp_path, "case", "0001", "--title", "mixed sample case", "--prompt", "replay",
               "--forbid", "bad answer", "--require", "good answer",
               "--must-match", "forbid=this is a bad answer",
               "--must-match", "require=this is a good answer") == 0

    d = tmp_path / "evals" / "0001-mixed-sample-case"
    c = cases.load_case(str(d))
    forbid_g = next(g for g in c.graders if g["match"] == "not_contains")
    require_g = next(g for g in c.graders if g["match"] == "contains")
    assert forbid_g["must_match"] == ["this is a bad answer"]
    assert require_g["must_match"] == ["this is a good answer"]

    from trimwrit.cli import main
    assert main(["--root", str(tmp_path), "check"]) == 0


def test_must_match_prefix_targets_one_grader_by_name(tmp_path):
    from trimwrit import cases

    _run(tmp_path, "log", "named grader target", "--tag", "named")
    assert _run(tmp_path, "case", "0001", "--prompt", "replay",
               "--forbid", "bad answer",
               "--must-match", "forbids-named=this is a bad answer") == 0
    d = tmp_path / "evals" / "0001-named-grader-target"
    c = cases.load_case(str(d))
    assert c.graders[0]["must_match"] == ["this is a bad answer"]


def test_must_match_with_no_prefix_still_applies_case_wide(tmp_path):
    # A value with no `=` before its first space keeps today's meaning: nothing already
    # written to call this changes.
    from trimwrit import cases

    _run(tmp_path, "log", "unchanged case wide behaviour", "--tag", "unchanged")
    assert _run(tmp_path, "case", "0001", "--prompt", "replay",
               "--forbid", "bad answer",
               "--must-match", "a bad answer right here") == 0
    d = tmp_path / "evals" / "0001-unchanged-case-wide-behaviour"
    c = cases.load_case(str(d))
    assert c.graders[0]["must_match"] == ["a bad answer right here"]


def test_must_match_unknown_target_is_refused(tmp_path, capsys):
    _run(tmp_path, "log", "typo in the grader name", "--tag", "typo")
    assert _run(tmp_path, "case", "0001", "--prompt", "replay", "--forbid", "bad answer",
               "--must-match", "no-such-grader=text") == 2
    assert "no-such-grader" in capsys.readouterr().err


def test_prune_finds_the_orphan_and_changes_nothing_without_apply(tmp_path, capsys):
    _run(tmp_path, "log", "x", "--tag", "t")
    (tmp_path / "CLAUDE.md").write_text(
        "<!-- trimwrit: R0099 case 0099, 2026-08-23: an old incident -->\nan orphan rule\n",
        encoding="utf-8")
    assert _run(tmp_path, "prune") == 0
    out = capsys.readouterr().out
    assert "orphan" in out and "nothing was changed" in out
    assert "an orphan rule" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")


def test_prune_apply_deletes_and_records_the_removal(tmp_path, capsys):
    from trimwrit import ledger
    (tmp_path / "CLAUDE.md").write_text(
        "<!-- trimwrit: R0099 case 0099, 2026-08-23: an old incident -->\nan orphan rule\n",
        encoding="utf-8")
    assert _run(tmp_path, "prune", "--apply") == 0
    assert "an orphan rule" not in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    removals = [r for r in ledger.rows(root=str(tmp_path)) if r.get("kind") == "removal"]
    assert [r["rule"] for r in removals] == ["R0099"]
    assert removals[0]["reason"]


def test_a_bad_dash_anywhere_is_refused_with_exit_2(tmp_path, capsys):
    assert _run(tmp_path, "log", "you wrote a" + EM + "dash") == 2
    assert "refused" in capsys.readouterr().err


def test_run_refuses_when_the_ablation_would_be_meaningless(tmp_path, capsys):
    _run(tmp_path, "log", "x", "--tag", "t")
    _run(tmp_path, "case", "0001", "--prompt", "p", "--forbid", "z")
    assert _run(tmp_path, "run", "--target", "does-not-exist.md") == 2
    assert "identical to the `without`" in capsys.readouterr().err


def test_results_with_no_value_still_loads_the_inert_finding(tmp_path, capsys):
    """`--results` with no value stores the empty string, which is falsy. A truthiness test
    here skipped the whole inert branch and printed "nothing to prune" over a rule whose case
    scored the same in both arms."""
    import json

    from trimwrit import cases

    evals = tmp_path / "evals"
    cases.write_case("0001", "some case", "p", [cases.forbid_grader("x", name="g")],
                     evals_dir=str(evals))
    (tmp_path / "CLAUDE.md").write_text(
        "<!-- trimwrit: R0001 case 0001, 2026-08-23: an incident -->\nthe rule\n",
        encoding="utf-8")
    (evals / "results").mkdir(parents=True, exist_ok=True)
    (evals / "results" / "latest.json").write_text(
        json.dumps({"0001-some-case": {"with": 1.0, "without": 1.0, "delta": 0.0}}),
        encoding="utf-8")

    assert _run(tmp_path, "prune", "--results") == 0
    out = capsys.readouterr().out
    assert "inert" in out
    assert "1.00 with R0001" in out
    assert "nothing to prune" not in out


def test_a_rule_can_cite_several_cases(tmp_path, capsys):
    """One rule, two cases. The first real use of this tool hit this within the hour: a ban on a
    character needs a case that replays it in prose and one that replays it in a commit message,
    and a marker holding one id makes the second show up forever as unused."""
    from trimwrit import cases, integrate

    evals = tmp_path / "evals"
    for cid, title in (("0001", "in prose"), ("0002", "in a commit")):
        cases.write_case(cid, title, "p", [cases.forbid_grader("x", name="g")],
                         evals_dir=str(evals))
    _run(tmp_path, "log", "you did it again", "--tag", "t", "--session", "a")
    _run(tmp_path, "log", "and again elsewhere", "--tag", "t", "--session", "b")
    assert _run(tmp_path, "integrate", "0002", "--case", "0001+0002",
                "--incident", "an incident", "--rule", "the rule") == 0

    meta = integrate.read_rules("CLAUDE.md", root=str(tmp_path))["R0002"]
    assert meta["case"] == "0001+0002"
    assert integrate.case_list(meta["case"]) == ["0001", "0002"]

    _run(tmp_path, "prune")
    out = capsys.readouterr().out
    assert "unused-case" not in out and "orphan" not in out


def test_a_rule_citing_two_cases_is_an_orphan_only_when_both_are_gone(tmp_path):
    from trimwrit import cases, integrate, prune

    evals = tmp_path / "evals"
    cases.write_case("0001", "survivor", "p", [cases.forbid_grader("x", name="g")],
                     evals_dir=str(evals))
    integrate.write_rule("CLAUDE.md", "R1", "0001+0009", "2026-08-23", "i", "the rule",
                         root=str(tmp_path))
    assert prune.orphans(["CLAUDE.md"], str(evals), str(tmp_path)) == []

    integrate.write_rule("CLAUDE.md", "R2", "0008+0009", "2026-08-23", "i", "another",
                         root=str(tmp_path))
    assert [f.rule for f in prune.orphans(["CLAUDE.md"], str(evals), str(tmp_path))] == ["R2"]


def test_prune_prints_the_viz_hint_after_nothing_to_prune(tmp_path, capsys):
    _run(tmp_path, "log", "x", "--tag", "t")
    assert _run(tmp_path, "prune") == 0
    out = capsys.readouterr().out
    assert "nothing to prune" in out
    assert "viz: run 'trimwrit viz --target CLAUDE.md' to see this as a canvas" in out


def test_prune_prints_the_viz_hint_after_a_findings_table(tmp_path, capsys):
    (tmp_path / "CLAUDE.md").write_text(
        "<!-- trimwrit: R0099 case 0099, 2026-08-23: an old incident -->\nan orphan rule\n",
        encoding="utf-8")
    assert _run(tmp_path, "prune") == 0
    out = capsys.readouterr().out
    assert "orphan" in out and "nothing was changed" in out
    assert "viz: run 'trimwrit viz --target CLAUDE.md' to see this as a canvas" in out


def _write_fake_claude(tmp_path):
    """A stand in for `claude -p` with no model call and no network: it reads whether the rule
    file was seeded into its own working directory (that IS the ablation, see runner.run_once)
    and answers differently depending on it, so a real `trimwrit run` end to end has something
    genuine to measure without needing the real binary."""
    path = tmp_path / "fake-claude.py"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "clean = os.path.exists('CLAUDE.md')\n"
        "text = 'a clean answer' if clean else 'a dirty answer with the bad word'\n"
        "print(json.dumps({'type': 'result', 'result': text}))\n",
        encoding="utf-8")
    os.chmod(path, 0o755)
    return str(path)


def test_run_prints_the_viz_hint_after_its_table(tmp_path, capsys):
    from trimwrit import cases

    cases.write_case("0001", "bad word case", "say something",
                     [cases.forbid_grader("bad word", name="g")],
                     evals_dir=str(tmp_path / "evals"))
    (tmp_path / "CLAUDE.md").write_text("the rule\n", encoding="utf-8")
    claude = _write_fake_claude(tmp_path)

    rc = _run(tmp_path, "run", "--target", "CLAUDE.md", "--runs", "1", "--claude", claude)
    out = capsys.readouterr().out
    assert rc == 0
    assert "earns its place" in out
    assert "viz: run 'trimwrit viz --target CLAUDE.md' to see this as a canvas" in out


def test_run_prints_the_viz_hint_even_with_no_ablation(tmp_path, capsys):
    from trimwrit import cases

    cases.write_case("0001", "bad word case", "say something",
                     [cases.forbid_grader("bad word", name="g")],
                     evals_dir=str(tmp_path / "evals"))
    (tmp_path / "CLAUDE.md").write_text("the rule\n", encoding="utf-8")
    claude = _write_fake_claude(tmp_path)

    rc = _run(tmp_path, "run", "--target", "CLAUDE.md", "--runs", "1", "--claude", claude,
             "--no-ablation")
    out = capsys.readouterr().out
    assert rc == 0
    assert "viz: run 'trimwrit viz --target CLAUDE.md' to see this as a canvas" in out


def test_re_integrating_an_already_promoted_rule_is_not_a_failure(tmp_path, capsys):
    from trimwrit import cases

    cases.write_case("0001", "a case", "p", [cases.forbid_grader("x", name="g")],
                     evals_dir=str(tmp_path / "evals"))
    _run(tmp_path, "log", "one", "--tag", "t", "--session", "a")
    _run(tmp_path, "log", "two", "--tag", "t", "--session", "b")
    assert _run(tmp_path, "integrate", "0002", "--case", "0001",
                "--incident", "i", "--rule", "first wording") == 0
    # Rewriting the wording, or adding a case, must exit 0 and must not look broken.
    assert _run(tmp_path, "integrate", "0002", "--case", "0001",
                "--incident", "i", "--rule", "second wording") == 0
    assert "NOT promoted" not in capsys.readouterr().err


# ==================================================================== trimwrit check


def test_check_reports_a_pattern_that_does_not_compile(tmp_path, capsys):
    from trimwrit import cases

    cases.write_case("0001", "broken", "p",
                     [{"type": "regex", "name": "g", "pattern": "[", "match": "contains"}],
                     evals_dir=str(tmp_path / "evals"))
    assert _run(tmp_path, "check") == 1
    out = capsys.readouterr().out
    assert "0001-broken / g" in out and "does not compile" in out


def test_check_a_clean_case_exits_0(tmp_path, capsys):
    from trimwrit import cases

    cases.write_case("0001", "clean", "p",
                     [cases.forbid_grader("bad word", name="g", must_match=["a bad word here"],
                                          must_not_match=["nothing wrong"])],
                     evals_dir=str(tmp_path / "evals"))
    assert _run(tmp_path, "check") == 0
    out = capsys.readouterr().out
    assert "1 case(s), 1 grader(s) checked, 0 failure(s)" in out


def test_check_an_unproven_grader_is_a_warning_not_a_failure_by_default(tmp_path, capsys):
    from trimwrit import cases

    cases.write_case("0001", "unproven", "p", [cases.forbid_grader("x", name="g")],
                     evals_dir=str(tmp_path / "evals"))
    assert _run(tmp_path, "check") == 0
    out = capsys.readouterr().out
    assert "1 unproven grader(s), run with --strict to refuse them." in out


def test_check_strict_refuses_the_unproven_grader(tmp_path, capsys):
    from trimwrit import cases

    cases.write_case("0001", "unproven", "p", [cases.forbid_grader("x", name="g")],
                     evals_dir=str(tmp_path / "evals"))
    assert _run(tmp_path, "check", "--strict") == 1
    out = capsys.readouterr().out
    assert "unproven grader: nothing shows this pattern can fire" in out


def test_check_json_reports_counts_and_lists(tmp_path, capsys):
    from trimwrit import cases

    cases.write_case("0001", "unproven", "p", [cases.forbid_grader("x", name="g")],
                     evals_dir=str(tmp_path / "evals"))
    assert _run(tmp_path, "check", "--json") == 0
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["cases"] == 1 and payload["graders"] == 1
    assert payload["failures"] == []
    assert len(payload["unproven"]) == 1


# ==================================================================== run + check gate


def _write_no_final_text_claude(tmp_path):
    """Always answers with no final text at all, the way an API safeguard refusing a prompt
    looks from the outside. Used to exercise the unmeasured path with no real model call."""
    path = tmp_path / "empty-claude.py"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'type': 'result', 'result': ''}))\n",
        encoding="utf-8")
    os.chmod(path, 0o755)
    return str(path)


def test_run_refuses_a_case_whose_must_match_example_does_not_match(tmp_path, capsys):
    from trimwrit import cases

    cases.write_case(
        "0001", "bad proof", "say something",
        [cases.forbid_grader("bad word", name="g", must_match=["this never matches"])],
        evals_dir=str(tmp_path / "evals"))
    (tmp_path / "CLAUDE.md").write_text("the rule\n", encoding="utf-8")
    claude = _write_fake_claude(tmp_path)

    rc = _run(tmp_path, "run", "--target", "CLAUDE.md", "--runs", "1", "--claude", claude)
    out = capsys.readouterr().out
    assert rc == 0  # a check failure does not fail the exit code by itself
    assert "UNCHECKED, grader failed its own proof" in out
    assert "must_match failed" in out
    assert "1 case(s) unmeasured, they are not passes." in out


def test_run_check_failure_shows_up_in_json_as_unmeasured_with_the_reason(tmp_path, capsys):
    from trimwrit import cases

    cases.write_case(
        "0001", "bad proof", "say something",
        [cases.forbid_grader("bad word", name="g", must_match=["this never matches"])],
        evals_dir=str(tmp_path / "evals"))
    (tmp_path / "CLAUDE.md").write_text("the rule\n", encoding="utf-8")
    claude = _write_fake_claude(tmp_path)

    _run(tmp_path, "run", "--target", "CLAUDE.md", "--runs", "1", "--claude", claude, "--json")
    import json
    payload = json.loads(capsys.readouterr().out)
    summary = payload["cases"][0]["summary"]
    assert summary["with"] is None
    assert "must_match failed" in summary["unchecked_reason"]
    assert summary["runs"]["with"] == 0  # never actually run


def test_run_with_correct_examples_runs_normally(tmp_path, capsys):
    from trimwrit import cases

    cases.write_case(
        "0001", "good proof", "say something",
        [cases.forbid_grader("bad word", name="g", must_match=["a bad word here"],
                             must_not_match=["a clean answer"])],
        evals_dir=str(tmp_path / "evals"))
    (tmp_path / "CLAUDE.md").write_text("the rule\n", encoding="utf-8")
    claude = _write_fake_claude(tmp_path)

    rc = _run(tmp_path, "run", "--target", "CLAUDE.md", "--runs", "1", "--claude", claude)
    out = capsys.readouterr().out
    assert rc == 0
    assert "UNCHECKED" not in out
    assert "earns its place" in out


# ==================================================================== evidence


def test_run_writes_evidence_for_every_run(tmp_path, capsys):
    from trimwrit import cases

    cases.write_case("0001", "bad word case", "say something",
                     [cases.forbid_grader("bad word", name="g")],
                     evals_dir=str(tmp_path / "evals"))
    (tmp_path / "CLAUDE.md").write_text("the rule\n", encoding="utf-8")
    claude = _write_fake_claude(tmp_path)

    _run(tmp_path, "run", "--target", "CLAUDE.md", "--runs", "1", "--claude", claude)
    out = capsys.readouterr().out
    assert "evidence written to" in out
    with_evidence = tmp_path / "evals" / "results" / "runs" / "0001-bad-word-case" / "with-0.md"
    without_evidence = (tmp_path / "evals" / "results" / "runs" / "0001-bad-word-case" /
                        "without-0.md")
    assert with_evidence.exists() and without_evidence.exists()
    assert "a clean answer" in with_evidence.read_text(encoding="utf-8")
    assert "a dirty answer with the bad word" in without_evidence.read_text(encoding="utf-8")


def test_run_no_save_skips_evidence(tmp_path, capsys):
    from trimwrit import cases

    cases.write_case("0001", "bad word case", "say something",
                     [cases.forbid_grader("bad word", name="g")],
                     evals_dir=str(tmp_path / "evals"))
    (tmp_path / "CLAUDE.md").write_text("the rule\n", encoding="utf-8")
    claude = _write_fake_claude(tmp_path)

    _run(tmp_path, "run", "--target", "CLAUDE.md", "--runs", "1", "--claude", claude, "--no-save")
    out = capsys.readouterr().out
    assert "evidence written to" not in out
    assert not (tmp_path / "evals" / "results" / "runs").exists()


# ==================================================================== unmeasured


def test_run_reports_unmeasured_without_failing_by_default(tmp_path, capsys):
    from trimwrit import cases

    cases.write_case("0001", "no text case", "say something",
                     [cases.forbid_grader("bad word", name="g")],
                     evals_dir=str(tmp_path / "evals"))
    (tmp_path / "CLAUDE.md").write_text("the rule\n", encoding="utf-8")
    claude = _write_no_final_text_claude(tmp_path)

    rc = _run(tmp_path, "run", "--target", "CLAUDE.md", "--runs", "1", "--claude", claude)
    out = capsys.readouterr().out
    assert rc == 0
    assert "UNMEASURED" in out
    assert "no final text" in out
    assert "1 case(s) unmeasured, they are not passes." in out


def test_run_fail_on_unmeasured_flips_the_exit_code(tmp_path, capsys):
    from trimwrit import cases

    cases.write_case("0001", "no text case", "say something",
                     [cases.forbid_grader("bad word", name="g")],
                     evals_dir=str(tmp_path / "evals"))
    (tmp_path / "CLAUDE.md").write_text("the rule\n", encoding="utf-8")
    claude = _write_no_final_text_claude(tmp_path)

    rc = _run(tmp_path, "run", "--target", "CLAUDE.md", "--runs", "1", "--claude", claude,
             "--fail-on-unmeasured")
    assert rc == 1


# ==================================================================== history


def test_run_appends_one_history_line_per_invocation(tmp_path, capsys):
    from trimwrit import cases

    cases.write_case("0001", "bad word case", "say something",
                     [cases.forbid_grader("bad word", name="g")],
                     evals_dir=str(tmp_path / "evals"))
    (tmp_path / "CLAUDE.md").write_text("the rule\n", encoding="utf-8")
    claude = _write_fake_claude(tmp_path)

    _run(tmp_path, "run", "--target", "CLAUDE.md", "--runs", "1", "--claude", claude)
    capsys.readouterr()
    _run(tmp_path, "run", "--target", "CLAUDE.md", "--runs", "1", "--claude", claude)
    capsys.readouterr()

    history = tmp_path / "evals" / "results" / "history.jsonl"
    lines = history.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    import json
    row = json.loads(lines[0])
    assert row["case"] == "0001-bad-word-case"
    assert row["target"] == ["CLAUDE.md"]
    assert row["with"] == 1.0 and row["without"] == 0.0 and row["delta"] == 1.0
    assert row["runs"] == 1
    assert row["claude"] == claude


def test_history_sha_changes_when_the_rule_file_changes(tmp_path, capsys):
    import hashlib
    import json

    from trimwrit import cases

    cases.write_case("0001", "bad word case", "say something",
                     [cases.forbid_grader("bad word", name="g")],
                     evals_dir=str(tmp_path / "evals"))
    claude = _write_fake_claude(tmp_path)

    (tmp_path / "CLAUDE.md").write_text("rule version A\n", encoding="utf-8")
    _run(tmp_path, "run", "--target", "CLAUDE.md", "--runs", "1", "--claude", claude)
    capsys.readouterr()

    (tmp_path / "CLAUDE.md").write_text("rule version B, much longer\n", encoding="utf-8")
    _run(tmp_path, "run", "--target", "CLAUDE.md", "--runs", "1", "--claude", claude)
    capsys.readouterr()

    lines = (tmp_path / "evals" / "results" / "history.jsonl").read_text(
        encoding="utf-8").splitlines()
    shas = [json.loads(line)["target_sha256"]["CLAUDE.md"] for line in lines]
    assert shas[0] != shas[1]
    assert shas[1] == hashlib.sha256("rule version B, much longer\n".encode("utf-8")).hexdigest()


# ==================================================================== --jobs


def test_run_jobs_4_matches_jobs_1_byte_for_byte(tmp_path, capsys):
    from trimwrit import cases

    for i in range(1, 4):
        cases.write_case("000{}".format(i), "case {}".format(i), "say something",
                         [cases.forbid_grader("bad word", name="g")],
                         evals_dir=str(tmp_path / "evals"))
    (tmp_path / "CLAUDE.md").write_text("the rule\n", encoding="utf-8")
    claude = _write_fake_claude(tmp_path)

    rc1 = _run(tmp_path, "run", "--target", "CLAUDE.md", "--runs", "2", "--claude", claude,
              "--json", "--jobs", "1")
    payload_1 = capsys.readouterr().out

    rc4 = _run(tmp_path, "run", "--target", "CLAUDE.md", "--runs", "2", "--claude", claude,
              "--json", "--jobs", "4")
    payload_4 = capsys.readouterr().out

    assert rc1 == rc4 == 0
    assert payload_1 == payload_4
