"""The sequential loop of `run --decide`, with no subprocess: `run_batch` is injected and answers
synthetic `runner.Result`s whose `score` and `unmeasured` come from the real properties, so a
pass here is a pass by the same arithmetic a real run uses."""
import json

import pytest

from trimwrit import decide, runner, verdict
from trimwrit.cases import Case

PASS, FAIL, NOTHING = "pass", "fail", "unmeasured"


def _case(tmp_path, name):
    d = tmp_path / "evals" / name
    d.mkdir(parents=True)
    (d / "prompt.md").write_text("---\nname: {}\n---\nreplay it\n".format(name), encoding="utf-8")
    return Case(str(d), {"name": name}, "replay it", [])


def _result(case, arm, index, outcome):
    if outcome == NOTHING:
        return runner.Result(case, arm, index, "", [], {})
    res = runner.Result(case, arm, index, "an answer", [], {})
    res.grades.append({"name": "g", "type": "regex", "passed": outcome == PASS, "weight": 1.0})
    return res


def _batcher(outcome, calls):
    """`outcome(case, arm)` says how every run of that case and arm ends."""
    def run_batch(cases, n, start_by_case):
        calls.append(([c.path for c in cases], n, dict(start_by_case)))
        out = {}
        for c in cases:
            start = start_by_case.get(c.path, 0)
            for arm in (runner.ARM_WITH, runner.ARM_WITHOUT):
                out.setdefault(c.path, {})[arm] = [
                    _result(c, arm, start + i, outcome(c, arm)) for i in range(n)]
        return out
    return run_batch


def _helps(case, arm):
    return PASS if arm == runner.ARM_WITH else FAIL


def _always(outcome):
    return lambda case, arm: outcome


def _run(tmp_path, cases, outcome, calls, keys=None, **kw):
    keys = keys or {c.path: "key-" + c.path for c in cases}
    files = {c.path: ({"CLAUDE.md": "rule"}, {"CLAUDE.md": ""}) for c in cases}
    return decide.run_until_decided(cases, files, keys, str(tmp_path / "history.jsonl"),
                                    _batcher(outcome, calls), 1.0, **kw)


def _row(key, passes, measured, attempted):
    return {"key": key, "passes": {"with": passes[0], "without": passes[1]},
            "measured": {"with": measured[0], "without": measured[1]},
            "attempted": {"with": attempted[0], "without": attempted[1]}}


def _history(tmp_path, *rows):
    with open(str(tmp_path / "history.jsonl"), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")


def test_a_rule_that_helps_is_kept_after_two_batches_and_twelve_sessions(tmp_path):
    case = _case(tmp_path, "0001-helps")
    calls = []
    state = _run(tmp_path, [case], _helps, calls)[case.path]
    assert state["verdict"]["verdict"] == verdict.KEEP
    assert state["status"] == "ran"
    assert state["spent"] == 12
    assert state["counts"]["passes"] == {"with": 6, "without": 0}
    assert [n for _, n, _ in calls] == [3, 3]
    assert calls[1][2] == {case.path: 3}  # the second batch writes with-3.md, not with-0.md
    assert len(state["results"][runner.ARM_WITH]) == 6


def test_a_case_passing_everywhere_is_inert_at_eighteen_runs_per_arm(tmp_path):
    case = _case(tmp_path, "0001-flat")
    state = _run(tmp_path, [case], _always(PASS), [])[case.path]
    assert state["verdict"]["verdict"] == verdict.INERT
    assert state["counts"]["attempted"] == {"with": 18, "without": 18}
    assert state["spent"] == 36


def test_a_case_never_measured_stays_undecided_and_stops_at_thirty_runs(tmp_path):
    case = _case(tmp_path, "0001-refused")
    calls = []
    state = _run(tmp_path, [case], _always(NOTHING), calls)[case.path]
    assert state["verdict"]["verdict"] == verdict.UNDECIDED
    assert state["counts"]["attempted"] == {"with": 30, "without": 30}
    assert state["counts"]["measured"] == {"with": 0, "without": 0}
    assert state["status"] == "ran"
    assert len(calls) == 10


def test_the_last_batch_is_partial_when_the_cap_is_not_a_multiple(tmp_path):
    case = _case(tmp_path, "0001-refused")
    calls = []
    state = _run(tmp_path, [case], _always(NOTHING), calls, max_runs=4)[case.path]
    assert [n for _, n, _ in calls] == [3, 1]
    assert state["counts"]["attempted"] == {"with": 4, "without": 4}


def test_prior_rows_with_the_same_key_are_summed_and_a_decided_key_is_not_run(tmp_path):
    case = _case(tmp_path, "0001-helps")
    _history(tmp_path, _row("k", (3, 0), (3, 3), (3, 3)), _row("k", (3, 0), (3, 3), (3, 3)))
    calls = []
    state = _run(tmp_path, [case], _helps, calls, keys={case.path: "k"})[case.path]
    assert calls == []
    assert state["status"] == "decided-earlier"
    assert state["verdict"]["verdict"] == verdict.KEEP
    assert state["prior"]["passes"] == {"with": 6, "without": 0}
    assert state["spent"] == 0 and state["results"] == {"with": [], "without": []}


def test_a_row_with_another_key_or_no_counts_is_ignored(tmp_path):
    case = _case(tmp_path, "0001-helps")
    _history(tmp_path, _row("k", (3, 0), (3, 3), (3, 3)),
             _row("other", (0, 30), (30, 30), (30, 30)),
             "not json at all", {"key": "k", "with": 1.0})
    calls = []
    state = _run(tmp_path, [case], _helps, calls, keys={case.path: "k"})[case.path]
    assert state["prior"]["attempted"] == {"with": 3, "without": 3}
    assert len(calls) == 1  # 3 earlier plus 3 now is the 6 runs a keep needs
    assert state["verdict"]["verdict"] == verdict.KEEP
    assert state["counts"]["passes"] == {"with": 6, "without": 0}
    assert state["spent"] == 6


def test_a_key_already_at_the_cap_is_exhausted_and_not_run(tmp_path):
    case = _case(tmp_path, "0001-noisy")
    _history(tmp_path, _row("k", (15, 15), (30, 30), (30, 30)))
    calls = []
    state = _run(tmp_path, [case], _helps, calls, keys={case.path: "k"})[case.path]
    assert calls == []
    assert state["status"] == "exhausted-earlier"
    assert state["verdict"]["verdict"] == verdict.UNDECIDED


def test_prior_counts_of_an_absent_file_are_zero(tmp_path):
    out = decide.prior_counts(str(tmp_path / "nope.jsonl"), "k")
    zero = {"with": 0, "without": 0}
    assert out == {"passes": zero, "measured": zero, "attempted": zero}


def test_a_budget_of_twelve_runs_the_first_two_cases_in_name_order(tmp_path):
    cs = [_case(tmp_path, n) for n in ("0003-c", "0001-a", "0002-b")]
    calls = []
    states = _run(tmp_path, cs, _helps, calls, budget=12)
    by_name = {s.split("/")[-1]: st for s, st in states.items()}
    assert [sorted(p.split("/")[-1] for p in paths) for paths, _, _ in calls] == [
        ["0001-a", "0002-b"]]
    assert by_name["0003-c"]["status"] == "budget" and by_name["0003-c"]["spent"] == 0
    assert by_name["0001-a"]["spent"] == by_name["0002-b"]["spent"] == 6
    assert by_name["0001-a"]["status"] == "budget"
    assert by_name["0001-a"]["verdict"]["verdict"] == verdict.UNDECIDED


def test_a_budget_under_one_batch_of_one_case_is_refused(tmp_path):
    case = _case(tmp_path, "0001-a")
    with pytest.raises(ValueError):
        _run(tmp_path, [case], _helps, [], budget=5)


def _key(case, rule="the rule", model="claude-opus-5-5"):
    return decide.evidence_key(case, {"CLAUDE.md": "intro\n" + rule}, {"CLAUDE.md": "intro\n"},
                               model, "claude-sonnet-5-5", 1.0, "rule")


def test_the_evidence_key_moves_with_the_rule_the_case_and_the_model(tmp_path):
    case = _case(tmp_path, "0001-a")
    base = _key(case)
    assert _key(case) == base and len(base) == 64
    assert _key(case, rule="the rule!") != base
    assert _key(case, model="claude-sonnet-5-5") != base
    prompt = tmp_path / "evals" / "0001-a" / "prompt.md"
    prompt.write_text(prompt.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert _key(case) != base


def test_files_sha256_hashes_both_arms():
    out = decide.files_sha256({"CLAUDE.md": "a"}, {})
    assert out == {"with": {"CLAUDE.md": decide.sha256_text("a")}, "without": {}}


def test_a_batch_that_returns_no_run_closes_the_case_instead_of_looping(tmp_path):
    case = _case(tmp_path, "0001-a")
    calls = []

    def empty(cases, n, start_by_case):
        calls.append(n)
        return {}

    files = {case.path: ({}, {})}
    state = decide.run_until_decided([case], files, {case.path: "k"},
                                     str(tmp_path / "history.jsonl"), empty, 1.0)[case.path]
    assert calls == [3]
    assert state["status"] == "ran" and state["verdict"]["verdict"] == verdict.UNDECIDED
