"""The explicit-subagent-model plugin, run the way Claude Code runs it."""
import json
import os

from gates._hook import plugin, rows, run_py, write_config

NAME = "explicit-subagent-model"
SCRIPT = "explicit_subagent_model.py"


def call(tool, tool_input):
    return {"hook_event_name": "PreToolUse", "session_id": "t", "tool_name": tool,
            "tool_input": tool_input}


def test_agent_without_a_model_is_refused(tmp_path):
    r = run_py(NAME, SCRIPT, call("Agent", {"prompt": "x"}), tmp_path)
    assert r.returncode == 2
    assert "model" in r.stderr and r.stdout == ""


def test_empty_or_blank_model_is_refused(tmp_path):
    for model in ("", "  "):
        r = run_py(NAME, SCRIPT, call("Agent", {"prompt": "x", "model": model}), tmp_path)
        assert r.returncode == 2, model


def test_task_without_a_model_is_refused(tmp_path):
    r = run_py(NAME, SCRIPT, call("Task", {"prompt": "x"}), tmp_path)
    assert r.returncode == 2 and "model" in r.stderr


def test_named_subagent_type_does_not_exempt(tmp_path):
    r = run_py(NAME, SCRIPT, call("Agent", {"subagent_type": "Explore", "prompt": "x"}), tmp_path)
    assert r.returncode == 2


def test_fork_is_allowed(tmp_path):
    r = run_py(NAME, SCRIPT, call("Agent", {"subagent_type": "fork", "prompt": "x"}), tmp_path)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_explicit_model_is_allowed(tmp_path):
    r = run_py(NAME, SCRIPT, call("Agent", {"prompt": "x", "model": "sonnet"}), tmp_path)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_other_tools_are_ignored(tmp_path):
    r = run_py(NAME, SCRIPT, call("Bash", {"command": "ls"}), tmp_path)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_configured_guidance_is_appended_to_the_reason(tmp_path):
    write_config(tmp_path, {NAME: {"guidance": "Use haiku for sweeps."}})
    r = run_py(NAME, SCRIPT, call("Agent", {"prompt": "x"}), tmp_path)
    assert r.returncode == 2
    assert r.stderr.strip().endswith("Pass model explicitly. Use haiku for sweeps.")
    (tmp_path / "empty").mkdir()
    plain = run_py(NAME, SCRIPT, call("Agent", {"prompt": "x"}), tmp_path / "empty")
    assert plain.stderr.strip().endswith("Pass model explicitly.")


def test_a_malformed_payload_passes_and_leaves_a_crash_row(tmp_path):
    r = run_py(NAME, SCRIPT, "not json", tmp_path)
    assert r.returncode == 0
    assert "UNGUARDED" in r.stdout
    got = rows(os.path.join(str(tmp_path), "hook-health.jsonl"))
    assert [(x["hook"], x["outcome"]) for x in got] == [("agent-model-explicit.py", "crash")]


# require_effort: the opt-in effort half of the gate

EFFORT_SENTENCE = ("This Agent call names no effort, so the subagent would run at whatever "
                   "effort its model defaults to. Pass effort explicitly (low, medium, high, "
                   "xhigh, max).")
MODEL_SENTENCE = ("This Agent call names no model, so the subagent would inherit the session's "
                  "/model. Pass model explicitly.")


def test_effort_is_not_required_by_default(tmp_path):
    r = run_py(NAME, SCRIPT, call("Agent", {"prompt": "x", "model": "sonnet"}), tmp_path)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_missing_effort_is_refused_when_required(tmp_path):
    write_config(tmp_path, {NAME: {"require_effort": True}})
    r = run_py(NAME, SCRIPT, call("Agent", {"prompt": "x", "model": "sonnet"}), tmp_path)
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr.strip() == EFFORT_SENTENCE


def test_model_and_effort_pass_when_required(tmp_path):
    write_config(tmp_path, {NAME: {"require_effort": True}})
    r = run_py(NAME, SCRIPT, call("Agent", {"prompt": "x", "model": "sonnet", "effort": "high"}),
               tmp_path)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_blank_effort_is_refused_when_required(tmp_path):
    write_config(tmp_path, {NAME: {"require_effort": True}})
    for effort in ("", "   "):
        r = run_py(NAME, SCRIPT, call("Agent", {"prompt": "x", "model": "sonnet",
                                                "effort": effort}), tmp_path)
        assert r.returncode == 2, effort


def test_task_without_effort_is_refused_when_required(tmp_path):
    write_config(tmp_path, {NAME: {"require_effort": True}})
    r = run_py(NAME, SCRIPT, call("Task", {"prompt": "x", "model": "haiku"}), tmp_path)
    assert r.returncode == 2 and "effort" in r.stderr


def test_fork_passes_when_effort_is_required(tmp_path):
    write_config(tmp_path, {NAME: {"require_effort": True}})
    r = run_py(NAME, SCRIPT, call("Agent", {"subagent_type": "fork", "prompt": "x"}), tmp_path)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_both_missing_gives_both_sentences_and_guidance_once(tmp_path):
    write_config(tmp_path, {NAME: {"require_effort": True, "guidance": "Use haiku for sweeps."}})
    r = run_py(NAME, SCRIPT, call("Agent", {"prompt": "x"}), tmp_path)
    assert r.returncode == 2
    assert r.stderr.strip() == " ".join([MODEL_SENTENCE, EFFORT_SENTENCE, "Use haiku for sweeps."])
    assert r.stderr.count("Use haiku for sweeps.") == 1


def test_missing_model_alone_keeps_its_sentence_when_effort_is_required(tmp_path):
    write_config(tmp_path, {NAME: {"require_effort": True}})
    r = run_py(NAME, SCRIPT, call("Agent", {"prompt": "x", "effort": "low"}), tmp_path)
    assert r.returncode == 2 and r.stderr.strip() == MODEL_SENTENCE


# require_subagent_type: the opt-in subagent_type half of the gate

TYPE_SENTENCE = ("This Agent call names no subagent_type, so it would run as general-purpose with "
                 "every CLAUDE.md loaded (about 50 to 60k tokens before its first word). Pass "
                 "subagent_type explicitly: lean when the brief carries what the agent needs, "
                 "general-purpose only when it must apply a repository's whole doctrine.")


def test_subagent_type_is_not_required_by_default(tmp_path):
    r = run_py(NAME, SCRIPT, call("Agent", {"prompt": "x", "model": "sonnet"}), tmp_path)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_missing_and_blank_subagent_type_are_refused_when_required(tmp_path):
    write_config(tmp_path, {NAME: {"require_subagent_type": True}})
    for ti in ({"prompt": "x", "model": "sonnet"},
               {"prompt": "x", "model": "sonnet", "subagent_type": ""},
               {"prompt": "x", "model": "sonnet", "subagent_type": "  "}):
        r = run_py(NAME, SCRIPT, call("Agent", ti), tmp_path)
        assert r.returncode == 2 and r.stdout == "", ti
        assert r.stderr.strip() == TYPE_SENTENCE, ti


def test_explicit_subagent_type_passes_when_required(tmp_path):
    write_config(tmp_path, {NAME: {"require_subagent_type": True}})
    for st in ("lean", "general-purpose"):
        r = run_py(NAME, SCRIPT, call("Agent", {"prompt": "x", "model": "sonnet",
                                                "subagent_type": st}), tmp_path)
        assert (r.returncode, r.stdout, r.stderr) == (0, "", ""), st


def test_fork_passes_when_subagent_type_is_required(tmp_path):
    write_config(tmp_path, {NAME: {"require_subagent_type": True}})
    r = run_py(NAME, SCRIPT, call("Agent", {"subagent_type": "fork", "prompt": "x"}), tmp_path)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_all_three_missing_gives_three_sentences_in_order_and_guidance_once(tmp_path):
    write_config(tmp_path, {NAME: {"require_effort": True, "require_subagent_type": True,
                                   "guidance": "Use haiku for sweeps."}})
    r = run_py(NAME, SCRIPT, call("Agent", {"prompt": "x"}), tmp_path)
    assert r.returncode == 2
    assert r.stderr.strip() == " ".join([MODEL_SENTENCE, EFFORT_SENTENCE, TYPE_SENTENCE,
                                         "Use haiku for sweeps."])
    assert r.stderr.count("Use haiku for sweeps.") == 1


# route_card: the SessionStart routing card

CARD_SCRIPT = "route_card.py"


def start(source="startup"):
    return {"hook_event_name": "SessionStart", "session_id": "t", "source": source}


def test_route_card_is_printed_as_session_start_context(tmp_path):
    card = "Route: haiku for sweeps, opus for design.\nAlways pass effort."
    write_config(tmp_path, {NAME: {"route_card": card}})
    for source in ("startup", "clear", "compact"):
        r = run_py(NAME, CARD_SCRIPT, start(source), tmp_path)
        assert r.returncode == 0 and r.stderr == "", source
        assert json.loads(r.stdout) == {"hookSpecificOutput": {
            "hookEventName": "SessionStart", "additionalContext": card}}
        assert len(r.stdout.strip().splitlines()) == 1


def test_route_card_prints_nothing_when_empty_or_absent(tmp_path):
    r = run_py(NAME, CARD_SCRIPT, start(), tmp_path)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    write_config(tmp_path, {NAME: {"route_card": ""}})
    r = run_py(NAME, CARD_SCRIPT, start(), tmp_path)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_route_card_is_wired_on_the_three_session_start_sources():
    with open(os.path.join(plugin(NAME), "hooks", "hooks.json"), encoding="utf-8") as fh:
        hooks = json.load(fh)["hooks"]
    assert hooks["SessionStart"][0]["matcher"] == "startup|clear|compact"
    assert "route_card.py" in hooks["SessionStart"][0]["hooks"][0]["command"]


def test_a_crashing_route_card_passes_and_leaves_a_crash_row(tmp_path):
    r = run_py(NAME, CARD_SCRIPT, "not json", tmp_path)
    assert r.returncode == 0 and "UNGUARDED" in r.stdout
    got = rows(os.path.join(str(tmp_path), "hook-health.jsonl"))
    assert [(x["hook"], x["outcome"]) for x in got] == [("route-card.py", "crash")]
