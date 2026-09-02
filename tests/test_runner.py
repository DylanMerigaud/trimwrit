"""Grader and ablation logic, with no model call anywhere.

The runner's subprocess layer is exercised by actually running the dogfood suite; what is worth
unit testing is the part that decides PASS or FAIL, because a grader that is wrong in the same
direction as the model is invisible in an end to end run.
"""
import os

from trimwrit import runner
from trimwrit.cases import Case

EM = chr(0x2014)


def _case(graders):
    return Case("/tmp/x", {"name": "x"}, "prompt", graders)


def _result(text="", tools=None, files=None):
    return runner.Result(_case([]), runner.ARM_WITH, 0, text, tools or [], files or {})


def test_regex_not_contains():
    spec = {"type": "regex", "name": "g", "pattern": "[\\u2014]", "match": "not_contains"}
    assert runner.grade_regex(spec, _result("clean, prose"))[0]
    assert not runner.grade_regex(spec, _result("dirty" + EM + "prose"))[0]


def test_regex_contains_and_count():
    assert runner.grade_regex({"pattern": "cat", "match": "contains"}, _result("a cat"))[0]
    assert runner.grade_regex({"pattern": "cat", "match": "count:2"}, _result("cat cat"))[0]
    assert not runner.grade_regex({"pattern": "cat", "match": "count:2"}, _result("cat"))[0]


def test_regex_flags_are_honoured():
    spec = {"pattern": "CAT", "match": "contains", "flags": "i"}
    assert runner.grade_regex(spec, _result("a cat"))[0]
    assert not runner.grade_regex({"pattern": "CAT", "match": "contains"}, _result("a cat"))[0]


def test_anchoring_a_pattern_to_the_end_of_the_message():
    # This is how the decision-last rule is checked mechanically rather than by a judge.
    spec = {"pattern": "recommend.{0,40}$", "match": "contains", "flags": "is"}
    assert runner.grade_regex(spec, _result("long body\n\nI recommend option two."))[0]
    assert not runner.grade_regex(
        spec, _result("I recommend option two." + "\nmore detail" * 20))[0]


def test_tool_used_min_and_max():
    res = _result(tools=[{"name": "Bash", "input": {"command": "pytest"}}])
    assert runner.grade_tool_used({"tool": "Bash"}, res)[0]
    assert not runner.grade_tool_used({"tool": "Write"}, res)[0]
    assert runner.grade_tool_used({"tool": "Write", "min": 0, "max": 0}, res)[0]
    assert runner.grade_tool_used({"tool": "Bash", "input_match": "pytest"}, res)[0]
    assert not runner.grade_tool_used({"tool": "Bash", "input_match": "npm"}, res)[0]


def test_tool_order():
    res = _result(tools=[{"name": "Read"}, {"name": "Write"}])
    assert runner.grade_tool_order({"before": "Read", "after": "Write"}, res)[0]
    assert not runner.grade_tool_order({"before": "Write", "after": "Read"}, res)[0]
    assert not runner.grade_tool_order({"before": "Read", "after": "Bash"}, res)[0]


def test_file_exists():
    res = _result(files={"out/report.json": "{}"})
    assert runner.grade_file_exists({"path": "out/*.json"}, res)[0]
    assert not runner.grade_file_exists({"path": "out/*.txt"}, res)[0]
    assert runner.grade_file_exists({"path": "out/*.txt", "exists": False}, res)[0]


def test_an_unsupported_grader_fails_rather_than_passes():
    # A grader this runner cannot score is a hole in the measurement, and a hole must look
    # like a failure. Anything else ships a green suite with a third of its graders dead.
    case = _case([{"type": "baseline", "name": "b", "baseline_file": "x.txt"}])
    res = runner.apply_graders(case, _result("anything"))
    assert res.grades[0]["passed"] is False
    assert "not supported" in res.grades[0]["detail"]


def test_an_llm_grader_with_no_judge_fails_rather_than_passes():
    case = _case([{"type": "llm", "name": "j", "criteria": "is it good"}])
    res = runner.apply_graders(case, _result("anything"), judge=None)
    assert res.grades[0]["passed"] is False


def test_an_llm_grader_uses_the_judge():
    case = _case([{"type": "llm", "name": "j", "criteria": "is it good"}])
    res = runner.apply_graders(case, _result("anything"), judge=lambda c, t: True)
    assert res.grades[0]["passed"] is True


def test_a_malformed_grader_fails_and_says_so():
    case = _case([{"type": "regex", "name": "g"}])  # no pattern
    res = runner.apply_graders(case, _result("x"))
    assert res.grades[0]["passed"] is False
    assert "malformed" in res.grades[0]["detail"]


def test_score_is_weighted():
    case = _case([{"type": "regex", "name": "a", "pattern": "yes", "match": "contains",
                   "weight": 3},
                  {"type": "regex", "name": "b", "pattern": "no", "match": "contains",
                   "weight": 1}])
    res = runner.apply_graders(case, _result("yes"))
    assert res.score == 0.75
    assert not res.passed


def test_a_run_that_errored_scores_zero():
    res = runner.Result(_case([]), runner.ARM_WITH, 0, "", [], {}, error="timed out")
    assert res.score == 0.0 and not res.passed
    # But it is UNMEASURED, not a failure the grader actually saw: an API safeguard refusing
    # the prompt says nothing about whether the rule held, and summarise() below keeps it out
    # of the mean instead of folding it in as a zero.
    assert res.unmeasured


def test_an_empty_final_text_is_unmeasured():
    res = _result("")
    assert res.unmeasured


def test_a_run_with_real_text_and_no_error_is_measured():
    assert not _result("some real answer").unmeasured


def test_summarise_means_over_measured_runs_only():
    case = _case([{"type": "regex", "name": "a", "pattern": "yes", "match": "contains"}])
    measured = _result("yes")
    runner.apply_graders(case, measured)
    unmeasured = runner.Result(case, runner.ARM_WITH, 1, "", [], {}, error="timed out")
    s = runner.summarise({runner.ARM_WITH: [measured, unmeasured]})
    assert s["with"] == 1.0
    assert s["unmeasured"]["with"] == 1


def test_summarise_is_none_when_every_run_of_an_arm_is_unmeasured():
    case = _case([])
    both_unmeasured = [runner.Result(case, runner.ARM_WITH, 0, "", [], {}, error="boom"),
                       runner.Result(case, runner.ARM_WITH, 1, "", [], {})]
    s = runner.summarise({runner.ARM_WITH: both_unmeasured, runner.ARM_WITHOUT: [_result("no")]})
    assert s["with"] is None
    assert s["delta"] is None
    assert s["unmeasured"]["with"] == 2
    assert s["unmeasured_detail"]["with"] == "boom"


def test_summarise_reports_the_delta():
    good, bad = _result("yes"), _result("no")
    case = _case([{"type": "regex", "name": "a", "pattern": "yes", "match": "contains"}])
    runner.apply_graders(case, good)
    runner.apply_graders(case, bad)
    s = runner.summarise({runner.ARM_WITH: [good], runner.ARM_WITHOUT: [bad]})
    assert s["with"] == 1.0 and s["without"] == 0.0 and s["delta"] == 1.0


def test_the_baseline_is_isolated_from_the_operators_own_harness():
    # Without this the `without` arm still carries ~/.claude/CLAUDE.md and every delta is
    # measured against a contaminated baseline. Measured, not assumed: see runner.ISOLATE_ARGS.
    args = runner.ISOLATE_ARGS
    i = args.index("--setting-sources")
    assert args[i + 1] == "project,local"


def test_parse_stream_picks_the_final_text_and_the_tool_calls():
    raw = "\n".join([
        '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash",'
        '"input":{"command":"ls"}}]}}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"first"}]}}',
        'not json at all',
        '{"type":"result","result":"the final answer"}',
    ])
    final, tools = runner._parse_stream(raw)
    assert final == "the final answer"
    assert tools == [{"name": "Bash", "input": {"command": "ls"}}]


# ---------------------------------------------------------------- evidence


def test_write_evidence_writes_the_final_text_and_the_grades(tmp_path):
    case = Case(str(tmp_path / "evals" / "0001-x"), {"name": "0001-x"}, "prompt", [])
    res = runner.Result(case, runner.ARM_WITH, 0, "a clean answer", [], {})
    res.grades = [{"name": "g", "type": "regex", "passed": True, "detail": "0 hit(s)"}]
    path = runner.write_evidence(res, str(tmp_path / "evals"))
    assert path == str(tmp_path / "evals" / "results" / "runs" / "0001-x" / "with-0.md")
    text = open(path, encoding="utf-8").read()
    assert "a clean answer" in text
    assert "g: PASS 0 hit(s)" in text
    assert "unmeasured: false" in text


def test_write_evidence_does_not_refuse_a_bad_dash_in_the_models_own_answer():
    # The whole point of this file is to show what the model actually produced, including the
    # exact violation a case exists to catch. Gating it the same way `text.write_text` gates
    # this repo's own copy would refuse to write the one piece of evidence that matters most.
    import tempfile

    case = Case("/tmp/0001-x", {"name": "0001-x"}, "prompt", [])
    res = runner.Result(case, runner.ARM_WITH, 0, "dirty" + EM + "answer", [], {})
    with tempfile.TemporaryDirectory() as d:
        path = runner.write_evidence(res, d)
        assert EM in open(path, encoding="utf-8").read()


def test_write_evidence_is_overwritten_by_the_next_run_of_the_same_slot(tmp_path):
    case = Case(str(tmp_path / "0001-x"), {"name": "0001-x"}, "prompt", [])
    r1 = runner.Result(case, runner.ARM_WITH, 0, "first", [], {})
    r2 = runner.Result(case, runner.ARM_WITH, 0, "second", [], {})
    runner.write_evidence(r1, str(tmp_path))
    path = runner.write_evidence(r2, str(tmp_path))
    text = open(path, encoding="utf-8").read()
    assert "second" in text and "first" not in text


def test_write_evidence_reports_an_error_and_no_final_text(tmp_path):
    case = Case(str(tmp_path / "0001-x"), {"name": "0001-x"}, "prompt", [])
    res = runner.Result(case, runner.ARM_WITH, 0, "", [], {}, error="timed out after 60s")
    path = runner.write_evidence(res, str(tmp_path))
    text = open(path, encoding="utf-8").read()
    assert "error: timed out after 60s" in text
    assert "unmeasured: true" in text


# ---------------------------------------------------------------- jobs / run_many


def test_run_many_with_one_job_matches_run_case_shape(tmp_path):
    claude = _fake_claude(tmp_path)
    case = _write_case(tmp_path, "0001-a")
    per_arm = runner.run_case(case, {}, arms=(runner.ARM_WITH,), runs=2, claude=claude, jobs=1)
    assert len(per_arm[runner.ARM_WITH]) == 2


def test_run_many_multiple_jobs_matches_a_single_job_byte_for_byte(tmp_path):
    claude = _fake_claude(tmp_path)
    cases = [_write_case(tmp_path, "000{}-a".format(i)) for i in range(1, 4)]
    rule_files = {"CLAUDE.md": "the rule\n"}

    def as_texts(out):
        return {path: {arm: [r.final_text for r in rs] for arm, rs in arms.items()}
               for path, arms in out.items()}

    sequential = runner.run_many(cases, rule_files, arms=(runner.ARM_WITH, runner.ARM_WITHOUT),
                                 runs=2, claude=claude, jobs=1)
    parallel = runner.run_many(cases, rule_files, arms=(runner.ARM_WITH, runner.ARM_WITHOUT),
                               runs=2, claude=claude, jobs=4)
    assert as_texts(sequential) == as_texts(parallel)


def _fake_claude(tmp_path):
    """Reads whether CLAUDE.md was seeded into its own cwd (the ablation) and answers
    differently, so a parallel run has something genuine, and deterministic, to compare against
    a sequential one."""
    path = tmp_path / "fake-claude.py"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "clean = os.path.exists('CLAUDE.md')\n"
        "text = 'a clean answer' if clean else 'a dirty answer'\n"
        "print(json.dumps({'type': 'result', 'result': text}))\n",
        encoding="utf-8")
    os.chmod(path, 0o755)
    return str(path)


def _write_case(tmp_path, name):
    from trimwrit import cases as cases_mod

    d = cases_mod.write_case(name.split("-")[0], name.split("-", 1)[1], "say something",
                             [cases_mod.forbid_grader("nothing-to-forbid", name="g")],
                             evals_dir=str(tmp_path / "evals"))
    return cases_mod.load_case(d)


# ---------------------------------------------------------------- 0.3.1, from the first real replay


def test_an_api_refusal_returned_as_text_is_unmeasured():
    """The API's own safeguards answer with RESULT TEXT and exit 0, not with an error. Seen on
    2026-09-01 on a base64 payload case: three bare runs returned the refusal text and the
    first `unmeasured` scored them 0.5 against a grader that requires an answer block, which
    read in the table as the harness earning its place on a case the model never saw."""
    from trimwrit.runner import Result
    refusal = ("API Error: Opus 5's safeguards flagged this message (https://example/aup). "
               "Try rephrasing the request in a new session or change your model.")
    assert Result(None, "without", 0, refusal, [], {}).unmeasured
    # Only at the start of the text: a model that QUOTES the phrase while answering measured.
    quoting = "The message you got looks like an 'API Error: safeguards' page, ignore it."
    assert not Result(None, "without", 0, quoting, [], {}).unmeasured


def test_the_isolation_removes_every_mcp_server():
    """A bare-arm run of a prompt injection case called the operator's live Gmail connector to
    verify a forged quote (2026-09-01). Denied by the permission layer, which is luck. An eval
    run must not be able to reach a system outside its scratch directory, and
    `--setting-sources` alone does not remove a user-level MCP server."""
    from trimwrit.runner import ISOLATE_ARGS
    assert "--strict-mcp-config" in ISOLATE_ARGS
    assert "--mcp-config" not in ISOLATE_ARGS, (
        "with no --mcp-config, --strict-mcp-config means zero servers; naming one reopens it")
