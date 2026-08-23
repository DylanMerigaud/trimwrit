"""Grader and ablation logic, with no model call anywhere.

The runner's subprocess layer is exercised by actually running the dogfood suite; what is worth
unit testing is the part that decides PASS or FAIL, because a grader that is wrong in the same
direction as the model is invisible in an end to end run.
"""
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
    assert runner.ISOLATE_ARGS == ("--setting-sources", "project,local")


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
