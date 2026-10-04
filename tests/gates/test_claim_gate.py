"""The claim-gate plugin, run the way Claude Code runs it: a payload on stdin, --home on argv.
Ports growth-cockpit's test_stop_claim_gate.py and the source's self-test matrix."""
import importlib.util
import json
import os
import sys

import pytest

from gates._hook import ROOT, plugin, rows, run_py, write_config

NAME = "claim-gate"
SCRIPT = "claim_gate.py"


def load_gate():
    path = os.path.join(plugin(NAME), "scripts", SCRIPT)
    sys.path.insert(0, plugin(NAME))
    try:
        spec = importlib.util.spec_from_file_location("claim_gate_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(plugin(NAME))
    return mod


gate = load_gate()
CFG = {"claims_extra": {}, "receipts_extra": {}, "void_extra": [], "reason_suffix": ""}


def make_transcript(tmp_path, final_text, commands=(), errored=(), previous_turn=False,
                    name="t.jsonl"):
    out = [{"type": "user", "message": {"content": "ship it"}}]
    for i, cmd in enumerate(commands):
        out.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t%d" % i, "name": "Bash", "input": {"command": cmd}}]}})
        out.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t%d" % i, "content": "ok",
             "is_error": i in errored}]}})
    if previous_turn:
        out.insert(3, {"type": "user", "message": {"content": "status?"}})
    out.append({"type": "assistant", "message": {"content": [
        {"type": "text", "text": final_text}]}})
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in out), encoding="utf-8")
    return str(path)


def stop(path, session, active=False, event="Stop", message=None):
    p = {"hook_event_name": event, "stop_hook_active": active, "session_id": session,
         "transcript_path": path}
    if message is not None:
        p["last_assistant_message"] = message
    return p


def go(tmp_path, text, commands, session, **kw):
    path = make_transcript(tmp_path, text, commands)
    return run_py(NAME, SCRIPT, stop(path, session, **kw), tmp_path)


def blocked(r):
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout.strip())
    assert body["decision"] == "block"
    return body["reason"]


CLAIMED = [
    ("Done. I pushed the branch to main and the tests pass.", {"pushed", "tests"}),
    ("J'ai envoye le mail a Marc.", {"sent"}),
    ("Tout est deploye et j'ai verifie la page.", {"verified"}),
    ("Merged to main. 12 passed.", {"pushed", "tests"}),
    ("Les tests passent.", {"tests"}),
]
CLEAN = [
    "I did not push anything, the tests were not run.",
    "I'll push once the tests pass.",
    "Voulez-vous que je pousse sur main ?",
    "The mail sent on 09-20 bounced.",
    "> I pushed it yesterday\nNothing else.",
    "Le plan est dans le doc. Rien n'est envoye.",
    "Run `git push` to ship it.",
]


@pytest.mark.parametrize("text,kinds", CLAIMED)
def test_claims_in_finds_each_claim(text, kinds):
    assert kinds <= set(gate.claims_in(text, CFG))


@pytest.mark.parametrize("text", CLEAN)
def test_claims_in_ignores_negations_futures_questions_quotes_and_code(text):
    assert gate.claims_in(text, CFG) == {}


def test_public_functions_load_their_own_settings_when_cfg_is_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    gate.trace.configure(["x", "--home", str(tmp_path)])
    try:
        assert set(gate.claims_in("I pushed it.")) == {"pushed"}
        assert gate.claims_without_receipt("I pushed it.") is True
        assert gate.claims_without_receipt("Nothing is pushed.") is False
    finally:
        gate.trace._HOME = None
        gate.trace._SETTINGS = None


PUSH = [("Bash", "git push origin HEAD:main")]
TEST = [("Bash", "python3 -m pytest tests -q")]


@pytest.mark.parametrize("text,calls,missing", [
    ("I pushed it.", PUSH, False),
    ("I pushed it.", TEST, True),
    ("I pushed it.", [], True),
    ("Tests pass.", TEST, False),
    ("Tests pass.", PUSH, True),
    ("I verified it.", [("Read", "x")], False),
    ("I verified it.", [("Write", "x")], True),
    ("I pushed it.", [("Agent", "do it")], False),
    ("I sent it.", [("SendMessage", "x")], False),
    ("It is deployed.", [("Bash", "vercel deploy --prod")], False),
    ("It is deployed.", TEST, True),
])
def test_receipt_matrix(text, calls, missing):
    assert bool(gate.judge_turn(text, calls, CFG)) is missing


def test_removed_cockpit_tokens_are_no_receipt_by_default():
    assert gate.judge_turn("I pushed it.", [("Bash", "land_on_main")], CFG)
    assert gate.judge_turn("Tests pass.", [("Bash", "python3 run.py")], CFG)
    assert gate.judge_turn("It is deployed.", [("Bash", "hub_sync")], CFG)
    assert gate.claims_in("hub_sync ran", CFG) == {}


def test_transcript_round_trip_errored_push_and_previous_turn(tmp_path, capsys):
    cmd = ["git push origin HEAD"]
    path = make_transcript(tmp_path, "I pushed it.", cmd, errored=(0,))
    rows_ = gate.read_rows(path)
    assert gate.judge_turn("I pushed it.", gate.turn_calls(rows_), CFG), "errored push"
    path = make_transcript(tmp_path, "I pushed it.", cmd)
    assert not gate.judge_turn("I pushed it.", gate.turn_calls(gate.read_rows(path)), CFG)
    path = make_transcript(tmp_path, "I pushed it.", cmd, previous_turn=True)
    assert gate.judge_turn("I pushed it.", gate.turn_calls(gate.read_rows(path)), CFG), \
        "a previous turn is no receipt"


def test_push_claim_with_no_push_is_blocked_with_a_witness_row(tmp_path):
    r = go(tmp_path, "Done. I pushed the fix to main.", ["python3 -m pytest -q"], "a")
    reason = blocked(r)
    assert "pushed" in reason and reason.endswith("(claim-gate)")
    got = rows(os.path.join(str(tmp_path), "door-refusals.jsonl"))
    assert [(x["door"], x["event"], x["reason_class"]) for x in got] == \
        [("stop_claim_gate", "Stop", "claim_without_receipt")]


def test_push_claim_with_a_push_passes(tmp_path):
    r = go(tmp_path, "Done. I pushed the fix to main.", ["git push origin HEAD:main"], "b")
    assert (r.returncode, r.stdout.strip()) == (0, "")


def test_tests_claim_needs_a_test_run(tmp_path):
    blocked(go(tmp_path, "All good, tests pass.", ["git status"], "c"))
    r = go(tmp_path, "All good, tests pass.", ["python3 -m pytest -q tests"], "d")
    assert r.stdout.strip() == ""


def test_no_claim_no_block(tmp_path):
    r = go(tmp_path, "Edited the script. Nothing is pushed or run yet.", [], "e")
    assert (r.returncode, r.stdout.strip()) == (0, "")


def test_wrong_event_is_ignored(tmp_path):
    path = make_transcript(tmp_path, "I pushed it.")
    r = run_py(NAME, SCRIPT, stop(path, "f", event="SubagentStop"), tmp_path)
    assert (r.returncode, r.stdout.strip()) == (0, "")


def test_last_assistant_message_wins_over_a_lagging_transcript(tmp_path):
    path = make_transcript(tmp_path, "Nothing yet.")
    blocked(run_py(NAME, SCRIPT, stop(path, "lag", message="I pushed it."), tmp_path))


def test_chain_cap_lets_go(tmp_path):
    path = make_transcript(tmp_path, "I pushed it.")
    outs = [run_py(NAME, SCRIPT, stop(path, "g", active=i > 0), tmp_path).stdout.strip()
            for i in range(4)]
    for out in outs[:3]:
        assert json.loads(out)["decision"] == "block"
    assert "decision" not in json.loads(outs[3])
    assert "systemMessage" in json.loads(outs[3])


def test_receipts_extra_in_the_home_config_makes_a_custom_command_a_receipt(tmp_path):
    cmd = ["land_on_main"]
    text = "Done, pushed to main."
    blocked(go(tmp_path, text, cmd, "x1"))
    write_config(tmp_path, {NAME: {"receipts_extra": {"pushed": ["\\bland_on_main\\b"]}}})
    r = go(tmp_path, text, cmd, "x2")
    assert (r.returncode, r.stdout.strip()) == (0, "")
    blocked(go(tmp_path, text, ["git status"], "x3"))


def test_claims_extra_and_void_extra_add_alternatives(tmp_path):
    write_config(tmp_path, {NAME: {"claims_extra": {"sent": ["\\bshipped\\s+out\\b"]},
                                   "void_extra": ["\\bdraft\\b"]}})
    blocked(go(tmp_path, "The invoice shipped out.", [], "y1"))
    r = go(tmp_path, "The invoice shipped out as a draft.", [], "y2")
    assert r.stdout.strip() == ""


@pytest.mark.parametrize("section", [
    {"claims_extra": {"nope": ["x"]}},
    {"receipts_extra": {"nope": ["x"]}},
    {"claims_extra": {"sent": ["("]}},
    {"void_extra": ["("]},
    {"claims_extra": {"sent": "not a list"}},
])
def test_a_bad_extra_is_a_crash_row_and_exit_0(tmp_path, section):
    write_config(tmp_path, {NAME: section})
    r = go(tmp_path, "I pushed it.", [], "bad")
    assert r.returncode == 0
    assert "UNGUARDED" in json.loads(r.stdout)["systemMessage"]
    got = rows(os.path.join(str(tmp_path), "hook-health.jsonl"))
    assert [(x["hook"], x["event"], x["outcome"]) for x in got] == \
        [("stop_claim_gate.py", "Stop", "crash")]


def test_a_broken_config_key_is_a_crash_not_a_block(tmp_path):
    write_config(tmp_path, {NAME: {"reason_suffixes": "x"}})
    r = go(tmp_path, "I pushed it.", [], "bad2")
    assert r.returncode == 0 and "UNGUARDED" in json.loads(r.stdout)["systemMessage"]


def test_reason_suffix_ends_the_block_reason(tmp_path):
    write_config(tmp_path, {NAME: {"reason_suffix": "See docs/claims.md."}})
    reason = blocked(go(tmp_path, "I pushed it.", [], "sfx"))
    assert reason.endswith("(claim-gate) See docs/claims.md.")


def test_check_prints_what_it_would_say(tmp_path):
    path = make_transcript(tmp_path, "I pushed it.", ["python3 -m pytest -q"])
    r = run_py(NAME, SCRIPT, "", tmp_path, "--check", path)
    assert r.returncode == 0
    assert list(json.loads(r.stdout)) == ["pushed"]
    path = make_transcript(tmp_path, "I pushed it.", ["git push"], name="ok.jsonl")
    assert run_py(NAME, SCRIPT, "", tmp_path, "--check", path).stdout.strip() == \
        "no unproven claim"


def test_eval_grader_is_proven():
    sys.path.insert(0, ROOT)
    from trimwrit import cases, check
    found = cases.discover(os.path.join(plugin(NAME), "evals"))
    assert [os.path.basename(c.path) for c in found] == ["claim-without-receipt"]
    assert [p for c in found for p in check.check_case(c)] == []
