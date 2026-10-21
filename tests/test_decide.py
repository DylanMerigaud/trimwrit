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


def test_a_round_with_no_measured_run_stops_the_invocation(tmp_path):
    # An outage (every claude call failing, a usage limit) used to run every case to the
    # 30-run cap: the week was spent and the cap with it. A round where no started session
    # was measured, across all cases, stops the invocation instead.
    cs = [_case(tmp_path, n) for n in ("0001-refused", "0002-refused")]
    calls = []
    states = _run(tmp_path, cs, _always(NOTHING), calls)
    assert states.stopped == decide.STOPPED_UNMEASURED
    assert len(calls) == 1
    for state in states.values():
        assert state["verdict"]["verdict"] == verdict.UNDECIDED
        assert state["counts"]["attempted"] == {"with": 3, "without": 3}
        assert state["counts"]["measured"] == {"with": 0, "without": 0}
        assert state["status"] == decide.STOPPED
        # The sessions were started, so they are spent against the budget all the same.
        assert state["spent"] == 6


def test_a_round_that_measured_something_does_not_stop(tmp_path):
    cs = [_case(tmp_path, n) for n in ("0001-helps", "0002-refused")]
    calls = []
    states = _run(tmp_path, cs, lambda c, a: NOTHING if "refused" in c.path else _helps(c, a),
                  calls, max_runs=6)
    assert states.stopped is None
    assert len(calls) == 2


def _indexed_batcher(outcome, calls):
    """`outcome(case, arm, run_index)` says how one run ends."""
    def run_batch(cases, n, start_by_case):
        calls.append(([c.path for c in cases], n, dict(start_by_case)))
        out = {}
        for c in cases:
            start = start_by_case.get(c.path, 0)
            for arm in (runner.ARM_WITH, runner.ARM_WITHOUT):
                out.setdefault(c.path, {})[arm] = [
                    _result(c, arm, start + i, outcome(c, arm, start + i)) for i in range(n)]
        return out
    return run_batch


def test_max_runs_caps_measured_runs_not_attempted_ones(tmp_path):
    # Every third run is unmeasured: the cap of 6 is reached at 6 MEASURED runs per arm, after
    # 9 attempted, and the unmeasured ones are still spent.
    case = _case(tmp_path, "0001-flaky")
    calls = []
    files = {case.path: ({"CLAUDE.md": "rule"}, {"CLAUDE.md": ""})}
    state = decide.run_until_decided(
        [case], files, {case.path: "k"}, str(tmp_path / "history.jsonl"),
        _indexed_batcher(lambda c, a, i: NOTHING if i % 3 == 0 else PASS, calls), 1.0,
        max_runs=6)[case.path]
    assert state["counts"]["measured"] == {"with": 6, "without": 6}
    assert state["counts"]["attempted"] == {"with": 9, "without": 9}
    assert state["spent"] == 18
    assert state["status"] == "ran"
    assert [n for _, n, _ in calls] == [3, 3, 2, 1]
    assert [s[case.path] for _, _, s in calls] == [0, 3, 6, 8]


def test_the_last_batch_is_partial_when_the_cap_is_not_a_multiple(tmp_path):
    case = _case(tmp_path, "0001-flat")
    calls = []
    state = _run(tmp_path, [case], _always(PASS), calls, max_runs=4)[case.path]
    assert [n for _, n, _ in calls] == [3, 1]
    assert state["counts"]["attempted"] == {"with": 4, "without": 4}


def test_a_key_whose_unmeasured_runs_reach_the_cap_is_unmeasurable(tmp_path):
    # One broken case among working ones: it runs while the others measure, and once its
    # unmeasured runs reach the cap in an arm it is unmeasurable, not started again.
    broken, working = _case(tmp_path, "0001-broken"), _case(tmp_path, "0002-working")
    calls = []
    states = _run(tmp_path, [broken, working],
                  lambda c, a: NOTHING if "broken" in c.path else PASS, calls, max_runs=6)
    assert states.stopped is None
    assert states[broken.path]["status"] == decide.UNMEASURABLE
    assert states[broken.path]["counts"]["attempted"] == {"with": 6, "without": 6}
    assert states[working.path]["status"] == "ran"
    assert states[working.path]["counts"]["measured"] == {"with": 6, "without": 6}


def test_an_unmeasurable_key_is_not_started_again_until_it_changes(tmp_path):
    case = _case(tmp_path, "0001-broken")
    _history(tmp_path, _row("k", (0, 0), (0, 0), (30, 30)))
    calls = []
    state = _run(tmp_path, [case], _helps, calls, keys={case.path: "k"})[case.path]
    assert calls == []
    assert state["status"] == decide.UNMEASURABLE and state["spent"] == 0
    calls = []
    state = _run(tmp_path, [case], _helps, calls, keys={case.path: "k2"})[case.path]
    assert state["verdict"]["verdict"] == verdict.KEEP and calls


def test_on_round_gets_each_round_s_runs_once(tmp_path):
    # History is written after every round (cli), so a killed invocation keeps the rounds it
    # finished; each call carries that round's runs only, so summing rows never counts twice.
    case = _case(tmp_path, "0001-helps")
    rounds = []

    def on_round(ran, states):
        rounds.append({p: {a: len(rs) for a, rs in per_arm.items()} for p, per_arm in ran.items()})
        assert states[case.path]["verdict"] is not None

    files = {case.path: ({"CLAUDE.md": "rule"}, {"CLAUDE.md": ""})}
    state = decide.run_until_decided([case], files, {case.path: "k"},
                                     str(tmp_path / "history.jsonl"), _batcher(_helps, []), 1.0,
                                     on_round=on_round)[case.path]
    assert rounds == [{case.path: {"with": 3, "without": 3}}] * 2
    assert state["verdict"]["verdict"] == verdict.KEEP


def test_a_killed_invocation_keeps_the_rounds_it_wrote(tmp_path):
    case = _case(tmp_path, "0001-helps")
    history = str(tmp_path / "history.jsonl")
    files = {case.path: ({"CLAUDE.md": "rule"}, {"CLAUDE.md": ""})}
    inner = _batcher(_helps, [])
    seen = []

    def dies_second(cases, n, start_by_case):
        seen.append(n)
        if len(seen) == 2:
            raise KeyboardInterrupt
        return inner(cases, n, start_by_case)

    def on_round(ran, states):
        with open(history, "a", encoding="utf-8") as fh:
            for path, per_arm in ran.items():
                row = {"key": "k"}
                row.update(decide.count(per_arm, 1.0))
                fh.write(json.dumps(row) + "\n")

    with pytest.raises(KeyboardInterrupt):
        decide.run_until_decided([case], files, {case.path: "k"}, history, dies_second, 1.0,
                                 on_round=on_round)
    assert decide.prior_counts(history, "k")["attempted"] == {"with": 3, "without": 3}
    state = decide.run_until_decided([case], files, {case.path: "k"}, history, inner, 1.0,
                                     on_round=on_round)[case.path]
    assert state["verdict"]["verdict"] == verdict.KEEP
    assert decide.prior_counts(history, "k")["attempted"] == {"with": 6, "without": 6}


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


def test_the_evidence_key_moves_with_the_sandbox_note(tmp_path):
    # A run told it sits in a scratch directory is a different experiment from one that is not.
    case = _case(tmp_path, "0001-a")
    base = _key(case)
    case.meta["sandbox_note"] = True
    assert _key(case) != base


def test_in_rule_mode_the_key_hashes_the_citing_blocks_not_the_files(tmp_path):
    case = _case(tmp_path, "0001-a")

    def key(files, rules):
        return decide.evidence_key(case, {"CLAUDE.md": files}, {"CLAUDE.md": "x"},
                                   "claude-opus-5-5", "claude-sonnet-5-5", 1.0, "rule",
                                   rules_sha256=rules)
    base = key("intro\nthe rule", {"R1": "a" * 64})
    assert key("intro, edited\nthe rule", {"R1": "a" * 64}) == base
    assert key("intro\nthe rule", {"R1": "b" * 64}) != base
    # Without block hashes (file mode), the files are what the key covers.
    assert key("intro\nthe rule", None) != key("intro, edited\nthe rule", None)


def test_the_case_hash_skips_dotfiles(tmp_path):
    # A stray .DS_Store (or any dot path) in a case directory is not a new experiment.
    case = _case(tmp_path, "0001-a")
    base = _key(case)
    d = tmp_path / "evals" / "0001-a"
    (d / ".DS_Store").write_bytes(b"finder")
    (d / ".cache").mkdir()
    (d / ".cache" / "x").write_text("x", encoding="utf-8")
    (d / "graders").mkdir()
    (d / "graders" / ".hidden").write_text("h", encoding="utf-8")
    assert _key(case) == base
    (d / "graders" / "g.md").write_text("a grader", encoding="utf-8")
    assert _key(case) != base


# ---------------------------------------------------------------- 0.9.3, an outage is not the case's fault


def _history_writer(history):
    """The rows the CLI writes after a round: that round's counts, flagged when it stopped the
    invocation (states.stopped is set before on_round on that round)."""
    def on_round(ran, states):
        with open(history, "a", encoding="utf-8") as fh:
            for path, per_arm in ran.items():
                row = {"key": states[path]["key"]}
                row.update(decide.count(per_arm, 1.0))
                if states.stopped:
                    row["stopped"] = states.stopped
                fh.write(json.dumps(row) + "\n")
    return on_round


def test_ten_outage_invocations_in_a_row_leave_every_case_open(tmp_path):
    # The scheduler retries a deferred cycle every 75 minutes: a day-long quota outage is about
    # 19 invocations, each stopped by a round that measured nothing. None of those rounds is
    # the case's fault, so none counts toward the unmeasurable cap.
    cs = [_case(tmp_path, n) for n in ("0001-a", "0002-b")]
    history = str(tmp_path / "history.jsonl")
    files = {c.path: ({"CLAUDE.md": "rule"}, {"CLAUDE.md": ""}) for c in cs}
    keys = {c.path: "key-" + c.path for c in cs}
    for _ in range(10):
        states = decide.run_until_decided(cs, files, keys, history,
                                          _batcher(_always(NOTHING), []), 1.0,
                                          on_round=_history_writer(history))
        assert states.stopped == decide.STOPPED_UNMEASURED
        for c in cs:
            assert states[c.path]["status"] == decide.STOPPED
    for c in cs:
        assert decide.prior_counts(history, keys[c.path])["attempted"] == {"with": 30,
                                                                          "without": 30}
    calls = []
    states = decide.run_until_decided(cs, files, keys, history, _batcher(_helps, calls), 1.0,
                                      on_round=_history_writer(history))
    assert calls, "an outage closed the cases for good"
    assert all(states[c.path]["verdict"]["verdict"] == verdict.KEEP for c in cs)


def test_the_stopping_round_never_makes_a_case_unmeasurable_within_the_invocation(tmp_path):
    # 27 unmeasured runs per arm already counted (in rounds that measured something elsewhere);
    # a dead round brings the attempted unmeasured to 30, yet the case stays open.
    case = _case(tmp_path, "0001-a")
    _history(tmp_path, _row("k", (0, 0), (0, 0), (27, 27)))
    state = _run(tmp_path, [case], _always(NOTHING), [], keys={case.path: "k"})
    assert state.stopped == decide.STOPPED_UNMEASURED
    assert state[case.path]["status"] == decide.STOPPED


def test_rows_of_a_stopping_round_do_not_count_toward_unmeasurable(tmp_path):
    case = _case(tmp_path, "0001-a")
    flagged = dict(_row("k", (0, 0), (0, 0), (30, 30)), stopped="unmeasured")
    _history(tmp_path, flagged)
    calls = []
    state = _run(tmp_path, [case], _helps, calls, keys={case.path: "k"})[case.path]
    assert calls and state["verdict"]["verdict"] == verdict.KEEP
    # attempted keeps every run, so the evidence files of the next round do not overwrite them
    assert calls[0][2] == {case.path: 30}
