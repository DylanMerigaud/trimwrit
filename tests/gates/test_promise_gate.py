"""The promise-gate plugin, run the way Claude Code runs it: a payload on stdin, --home on argv.
Ports growth-cockpit's test_stop_promise_gate.py and the source's self-test matrix."""
import importlib.util
import json
import os
import re
import sys

import pytest

from gates._hook import ROOT, eval_cases, plugin, rows, run_py, write_config

NAME = "promise-gate"
SCRIPT = "promise_gate.py"


def load_gate():
    path = os.path.join(plugin(NAME), "scripts", SCRIPT)
    sys.path.insert(0, plugin(NAME))
    try:
        spec = importlib.util.spec_from_file_location("promise_gate_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(plugin(NAME))
    return mod


gate = load_gate()
CFG = {"target_patterns_extra": [], "promise_patterns_extra": [], "reason_suffix": ""}
PROMISE = "Trois morts de plus. Je continue sur le lot suivant."


def make_transcript(tmp_path, final_text, name="t.jsonl"):
    out = [{"type": "user", "message": {"content": "go"}},
           {"type": "assistant", "message": {"content": [{"type": "text", "text": final_text}]}}]
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in out), encoding="utf-8")
    return str(path)


def stop(path, session, active=False, event="Stop", message=None):
    p = {"hook_event_name": event, "stop_hook_active": active, "session_id": session,
         "transcript_path": path}
    if message is not None:
        p["last_assistant_message"] = message
    return p


def prompt(tmp_path, session, text):
    return run_py(NAME, SCRIPT, {"hook_event_name": "UserPromptSubmit", "session_id": session,
                                 "prompt": text}, tmp_path, "--prompt")


def blocked(r):
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout.strip())
    assert body["decision"] == "block"
    return body["reason"]


TARGETS = [
    "je vais dormir. ne t'arrete pas avant d'avoir identifié 5+ candidats fiables",
    "FAIT TOUT PUTIN TROUVEM OI DES BON CANDIDATS T'ARRETE PAS",
    "understoud? never stop untill results",
    "pourquoi tu fais pas tout le pool d'un coup?",
    "go autonome putin de merde",
    "Do not stop until all 24 candidates are scored.",
]
NOT_TARGETS = ["pk tu t'es arrete", "ok stop, on reprend demain", "daily", "recap",
               "c'est pas a toi de juger l'heure. go b.", "tu peux tuer la session"]
PROMISES = [
    PROMISE,
    "Running in background and will land results here and not stopping.",
    "Twenty dead, one survivor. I'll pick it up with the next batch.",
    # How the bare model ended the manuscript case on 2026-10-05 (Opus 5.5, no plugin), none of
    # which the first pattern set caught.
    "*End of Chapter One. Reply \"continue\" for Chapter Two: the first exposed hull.*",
    "**Status: Chapters 1 and 2 complete. Continuing with Chapter 3: the dry riverbed.**",
    "A one-line note is all I need. I'm continuing in sequence and holding the voice.",
    "*End of Chapter One. Chapter Two (The lock-keeper's house, her father's papers) follows.*",
    "*Chapter Two, \"The Lock-Keeper's House,\" continues in /manuscript/02.md.*",
    "Ch. 2 comes next, then onward in order.",
    "Chapter Two follows in the next message.",
    "Tell me which, and I\u2019ll draft the remaining tickets.",
]
FINISHED = [
    "24/24 traités et 2 gardés et pool épuisé.",
    "Rien en vol. tu peux tuer la session",
    "Le lot suivant est dans le pool, traite: 12 morts, 2 gardés, pool vide.",
    "All 12 chapters are drafted above. The manuscript follows the outline as written.",
    "The steps are as follows: open the lock, read the gauge, log the stage.",
    "Chapter Twelve closes the book; every chapter exists for your 8am edit.",
]


@pytest.mark.parametrize("text", TARGETS)
def test_a_target_arms(text):
    assert gate.target_in(text, CFG)


@pytest.mark.parametrize("text", NOT_TARGETS)
def test_talk_about_stopping_is_no_target(text):
    assert not gate.target_in(text, CFG)


@pytest.mark.parametrize("text", PROMISES)
def test_a_promise_is_found(text):
    assert gate.promise_in(text, CFG)


@pytest.mark.parametrize("text", FINISHED)
def test_a_finished_turn_is_no_promise(text):
    assert not gate.promise_in(text, CFG)


def test_only_the_tail_counts():
    assert not gate.promise_in("je continue " + "x" * 5000 + " pool épuisé, 24/24.", CFG)


def test_public_functions_load_their_own_settings_when_cfg_is_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    gate.trace.configure(["x", "--home", str(tmp_path)])
    try:
        assert gate.promise_in(PROMISE) == "Je continue"
        assert gate.target_in("never stop") is True
        assert gate.promise_in("Pool épuisé.") is None
    finally:
        gate.trace._HOME = None
        gate.trace._SETTINGS = None


def test_arming_round_trip_on_a_fake_session(tmp_path):
    gate.trace.configure(["x", "--home", str(tmp_path)])
    try:
        gate.set_armed("fake", "ne t'arrete pas avant 5 candidats")
        assert gate.armed_excerpt("fake") == "ne t'arrete pas avant 5 candidats"
        gate.set_armed("fake", None)
        assert gate.armed_excerpt("fake") is None
    finally:
        gate.trace._HOME = None
        gate.trace._SETTINGS = None


def test_unarmed_session_is_never_held(tmp_path):
    path = make_transcript(tmp_path, PROMISE)
    r = run_py(NAME, SCRIPT, stop(path, "u"), tmp_path)
    assert (r.returncode, r.stdout.strip()) == (0, "")


def test_prompt_with_a_target_arms_and_a_promise_blocks_until_the_chain_cap(tmp_path):
    r = prompt(tmp_path, "s", "je vais dormir. ne t'arrete pas avant d'avoir identifié 5+ candidats")
    assert (r.returncode, r.stdout.strip()) == (0, "")
    path = make_transcript(tmp_path, "Vingt morts, un survivant. Je continue sur le lot suivant.")
    outs = [run_py(NAME, SCRIPT, stop(path, "s", active=i > 0), tmp_path).stdout.strip()
            for i in range(4)]
    for out in outs[:3]:
        body = json.loads(out)
        assert body["decision"] == "block"
        assert "Je continue" in body["reason"] and "ne t'arrete pas" in body["reason"]
        assert body["reason"].endswith("(promise-gate)")
    capped = json.loads(outs[3])
    assert "decision" not in capped and "did not clear it" in capped["systemMessage"]
    got = rows(os.path.join(str(tmp_path), "hook-health.jsonl"))
    assert any(x["hook"] == "stop_promise_gate.py" and x["outcome"] == "cap" for x in got)
    refusals = rows(os.path.join(str(tmp_path), "door-refusals.jsonl"))
    assert [(x["door"], x["event"], x["reason_class"]) for x in refusals] == \
        [("stop_promise_gate", "Stop", "promise_ending")] * 3


def test_armed_session_with_a_finished_turn_is_silent(tmp_path):
    prompt(tmp_path, "a", "never stop until results")
    path = make_transcript(tmp_path, "24/24 traités, 2 gardés, pool épuisé. Rien en vol.")
    r = run_py(NAME, SCRIPT, stop(path, "a"), tmp_path)
    assert (r.returncode, r.stdout.strip()) == (0, "")


def test_a_question_about_stopping_does_not_arm(tmp_path):
    prompt(tmp_path, "q", "pk tu t'es arrete")
    path = make_transcript(tmp_path, PROMISE)
    assert run_py(NAME, SCRIPT, stop(path, "q"), tmp_path).stdout.strip() == ""


def test_subagent_stop_is_ignored_and_a_re_answer_is_checked(tmp_path):
    prompt(tmp_path, "r", "fait tout")
    path = make_transcript(tmp_path, PROMISE)
    sub = run_py(NAME, SCRIPT, stop(path, "r", event="SubagentStop"), tmp_path)
    assert (sub.returncode, sub.stdout.strip()) == (0, "")
    blocked(run_py(NAME, SCRIPT, stop(path, "r", active=True), tmp_path))


def test_the_old_cockpit_escape_variable_changes_nothing(tmp_path):
    prompt(tmp_path, "e", "fait tout")
    path = make_transcript(tmp_path, PROMISE)
    r = run_py(NAME, SCRIPT, stop(path, "e"), tmp_path, env={"COCKPIT_NO_STOP_GATE": "1"})
    blocked(r)


def test_last_assistant_message_wins_over_a_lagging_transcript(tmp_path):
    prompt(tmp_path, "lag", "fait tout")
    path = make_transcript(tmp_path, "Pool épuisé.")
    blocked(run_py(NAME, SCRIPT, stop(path, "lag", message=PROMISE), tmp_path))


def test_arm_disarm_print_use_the_session_from_the_environment(tmp_path):
    env = {"CLAUDE_SESSION_ID": "hand"}
    assert "not armed" in run_py(NAME, SCRIPT, "", tmp_path, "--print", env=env).stdout
    run_py(NAME, SCRIPT, "", tmp_path, "--arm", "never stop", env=env)
    assert 'ARMED on "never stop"' in run_py(NAME, SCRIPT, "", tmp_path, "--print", env=env).stdout
    path = make_transcript(tmp_path, PROMISE)
    blocked(run_py(NAME, SCRIPT, stop(path, "hand"), tmp_path))
    run_py(NAME, SCRIPT, "", tmp_path, "--disarm", env=env)
    assert "not armed" in run_py(NAME, SCRIPT, "", tmp_path, "--print", env=env).stdout


def test_promise_patterns_extra_adds_an_alternative(tmp_path):
    prompt(tmp_path, "x", "never stop")
    path = make_transcript(tmp_path, "Nine done, to be resumed after the break.")
    assert run_py(NAME, SCRIPT, stop(path, "x"), tmp_path).stdout.strip() == ""
    write_config(tmp_path, {NAME: {"promise_patterns_extra": ["\\bto\\s+be\\s+resumed\\b"]}})
    reason = blocked(run_py(NAME, SCRIPT, stop(path, "x"), tmp_path))
    assert "to be resumed" in reason


def test_target_patterns_extra_arms(tmp_path):
    write_config(tmp_path, {NAME: {"target_patterns_extra": ["\\bkeep\\s+at\\s+it\\b"]}})
    prompt(tmp_path, "y", "please keep at it")
    path = make_transcript(tmp_path, PROMISE)
    blocked(run_py(NAME, SCRIPT, stop(path, "y"), tmp_path))


def test_an_extra_with_a_comment_cannot_swallow_the_base_alternatives():
    cfg = dict(CFG, promise_patterns_extra=["x # a comment"])
    assert gate.promise_in("Je continue.", cfg) == "Je continue"
    assert gate.promise_in("zzz x", cfg) == "x"


@pytest.mark.parametrize("section", [
    {"promise_patterns_extra": ["("]},
    {"target_patterns_extra": ["("]},
    {"promise_patterns_extra": "not a list"},
    {"target_patterns_extra": [1]},
    {"reason_suffixes": "x"},
])
def test_a_bad_extra_or_key_is_a_crash_row_and_exit_0(tmp_path, section):
    write_config(tmp_path, {NAME: section})
    path = make_transcript(tmp_path, "Pool épuisé.")
    r = run_py(NAME, SCRIPT, stop(path, "bad"), tmp_path)
    assert r.returncode == 0
    assert "UNGUARDED" in json.loads(r.stdout)["systemMessage"]
    got = rows(os.path.join(str(tmp_path), "hook-health.jsonl"))
    assert [(x["hook"], x["event"], x["outcome"]) for x in got] == \
        [("stop_promise_gate.py", "Stop", "crash")]


def test_a_bad_extra_on_the_prompt_entry_is_a_crash_too(tmp_path):
    write_config(tmp_path, {NAME: {"target_patterns_extra": ["("]}})
    r = prompt(tmp_path, "bp", "never stop")
    assert r.returncode == 0 and "UNGUARDED" in json.loads(r.stdout)["systemMessage"]


def test_reason_suffix_ends_the_block_reason(tmp_path):
    write_config(tmp_path, {NAME: {"reason_suffix": "See docs/batches.md."}})
    prompt(tmp_path, "sfx", "never stop")
    path = make_transcript(tmp_path, PROMISE)
    reason = blocked(run_py(NAME, SCRIPT, stop(path, "sfx"), tmp_path))
    assert reason.endswith("(promise-gate) See docs/batches.md.")


# ---- the eval case: the grader must separate the plugin arm from the baseline arm -------------
def graders():
    sys.path.insert(0, ROOT)
    found = eval_cases(NAME)
    assert [os.path.basename(c.path) for c in found] == ["turn-ends-on-a-promise"]
    return {g["name"]: g for g in found[0].graders}, found[0]


def test_eval_graders_are_proven():
    from trimwrit import check
    _specs, case = graders()
    assert [p for p in check.check_case(case)] == []


def test_eval_prompt_opens_with_a_target_that_arms_the_gate():
    _specs, case = graders()
    first = case.prompt.strip().splitlines()[0]
    assert gate.target_in(first, CFG), first


def test_forbid_grader_carries_every_alternative_of_the_gate():
    # The grader is the gate's PROMISE_SRC written for a non-verbose regex: comments dropped,
    # spaces removed, an apostrophe as \x27. A new alternative in the gate that the grader lacks
    # would score a refused promise as a pass.
    pattern = graders()[0]["forbids-turn-ends-on-a-promise"]["pattern"]
    for line in gate.PROMISE_SRC.strip().split("\n"):
        alt = re.split(r"\s{2,}#", line.strip().lstrip("|").strip())[0]
        alt = alt.replace(" ", "").replace("'", "\\x27")
        assert alt in pattern, alt


def forbid():
    return re.compile(graders()[0]["forbids-turn-ends-on-a-promise"]["pattern"], re.I)


@pytest.mark.parametrize("text", PROMISES + [
    "I will keep going with 13 to 24.",
    "Scored 9 to 16.\nI will\n continue.",
    "Scored 9 to 16.  Je   continue  sur le lot suivant.",
    "Next\nbatch is running in  the  background.",
    "x " * 300 + "I'll report back later.",
    "Scored 9 to 16. " + "Details follow. " * 20 + "To be continued.",
])
def test_forbid_grader_hits_what_the_gate_refuses(text):
    assert gate.promise_in(text, CFG), text
    assert forbid().search(text), text


@pytest.mark.parametrize("text", FINISHED + [
    "I will not continue past the pool: it is empty. 24/24 scored.",
    'Earlier I wrote "next batch" and then did it. ' + "x " * 400 + "24/24 scored, pool exhausted.",
])
def test_forbid_grader_spares_what_the_gate_allows(text):
    assert not gate.promise_in(text, CFG), text
    assert not forbid().search(text), text
