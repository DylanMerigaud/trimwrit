"""The explicit-subagent-model plugin, run the way Claude Code runs it."""
import os

from gates._hook import rows, run_py, write_config

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
