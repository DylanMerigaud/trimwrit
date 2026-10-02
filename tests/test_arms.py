import json
import os
import stat
import subprocess

import pytest

from trimwrit import arms

DASH = chr(0x2014)


def test_ablate_lines_removes_exactly_the_ranges():
    text = "".join("line {}\n".format(i) for i in range(1, 11))
    out = arms.ablate_lines(text, [(2, 3), (9, 9)])
    assert out.splitlines() == ["line 1", "line 4", "line 5", "line 6", "line 7", "line 8",
                                "line 10"]


@pytest.mark.parametrize("ranges", [[(0, 2)], [(5, 11)], [(3, 2)], [(1, 3), (3, 4)]])
def test_ablate_lines_refuses_a_range_that_does_not_fit(ranges):
    text = "a\n" * 10
    with pytest.raises(arms.ArmsError):
        arms.ablate_lines(text, ranges)


def test_hook_settings_shape():
    regs = [{"event": "Stop", "script": "gate.py"},
            {"event": "UserPromptSubmit", "script": "gate.py", "args": ["--prompt"]},
            {"event": "PreToolUse", "script": "w.py", "matcher": "Write|Bash", "timeout": 5}]
    s = arms.hook_settings(regs)
    assert s["Stop"][0]["hooks"][0]["command"] == \
        'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/gate.py"'
    assert s["UserPromptSubmit"][0]["hooks"][0]["command"].endswith("gate.py\" --prompt")
    assert s["PreToolUse"][0]["matcher"] == "Write|Bash"
    assert s["PreToolUse"][0]["hooks"][0]["timeout"] == 5


def test_classic_arms():
    a = arms.classic_arms([{"event": "Stop", "script": "g.py"}])
    assert a["A0"] == (False, []) and a["AP"] == (True, [])
    assert a["AD"][0] is False and a["AD"][1] and a["APD"][0] is True


def test_prepare_workdir_copies_registers_and_commits(tmp_path):
    tpl = tmp_path / "tpl"
    tpl.mkdir()
    (tpl / "app.py").write_text("print(1)\n")
    hook = tmp_path / "gate.py"
    hook.write_text("import sys\n")
    wd = tmp_path / "wd"
    base = arms.prepare_workdir(str(wd), str(tpl), "# rules\n",
                                [{"event": "Stop", "script": "gate.py"}],
                                {"gate.py": str(hook)})
    assert base
    assert (wd / "CLAUDE.md").read_text() == "# rules\n"
    assert (wd / ".claude" / "hooks" / "gate.py").exists()
    settings = json.loads((wd / ".claude" / "settings.json").read_text())
    assert settings["sandbox"]["enabled"] is True
    assert "Stop" in settings["hooks"]
    status = subprocess.run(["git", "-C", str(wd), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    assert status.strip() == ""


def test_prepare_workdir_without_door_has_no_hooks(tmp_path):
    wd = tmp_path / "wd"
    arms.prepare_workdir(str(wd), None, "x\n")
    settings = json.loads((wd / ".claude" / "settings.json").read_text())
    assert "hooks" not in settings
    assert not (wd / ".claude" / "hooks").exists()


def test_prepare_workdir_refuses_a_registration_with_no_file(tmp_path):
    with pytest.raises(arms.ArmsError):
        arms.prepare_workdir(str(tmp_path / "wd"), None, "x",
                             [{"event": "Stop", "script": "missing.py"}], {})


def test_clean_env_drops_inherited_claude_and_api_variables(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "y")
    env = arms.clean_env("/tmp/cfg", token="tok", extra={"TMPDIR": "/tmp/t"})
    assert "CLAUDE_CODE_SIMPLE" not in env
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["CLAUDE_CONFIG_DIR"] == "/tmp/cfg"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"
    assert env["TMPDIR"] == "/tmp/t"


def test_build_cmd_requires_a_model():
    with pytest.raises(arms.ArmsError):
        arms.build_cmd("hi", None, "sid")
    with pytest.raises(arms.ArmsError):
        arms.build_cmd("hi", "sonnet", "sid")
    with pytest.raises(ValueError):
        arms.build_cmd("hi", "opus", "sid")
    cmd = arms.build_cmd("hi", "claude-sonnet-5-5", "sid", max_turns=5)
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5-5"
    assert "--include-hook-events" in cmd and "--max-turns" in cmd


def ev(**kw):
    return json.dumps(kw)


def text_msg(t):
    return ev(type="assistant", message={"content": [{"type": "text", "text": t}]})


def tool_msg(i, name, inp):
    return ev(type="assistant", message={"content": [{"type": "tool_use", "id": i, "name": name,
                                                      "input": inp}]})


def tool_res(i, content, err=False):
    return ev(type="user", message={"content": [{"type": "tool_result", "tool_use_id": i,
                                                 "content": content, "is_error": err}]})


def stop_resp(output):
    return ev(type="system", subtype="hook_response", hook_event="Stop", hook_name="Stop",
              output=output, exit_code=0, outcome="success")


BLOCK = json.dumps({"decision": "block", "reason": "no"})
INNER_BLOCK = json.dumps({"hookSpecificOutput": {"hookEventName": "Stop", "decision": "block",
                                                 "reason": "dash"}})
DENY = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                          "permissionDecision": "deny",
                                          "permissionDecisionReason": "1 forbidden dash in it"}})


def test_parse_stream_stops_blocks_and_effect():
    lines = [ev(type="system", subtype="init", model="m-9", claude_code_version="9.9"),
             text_msg("bad ending?"), stop_resp(BLOCK), stop_resp(""),
             text_msg("good ending."), stop_resp(""),
             ev(type="result", result="good ending.", num_turns=3,
                usage={"output_tokens": 42})]
    info = arms.parse_stream(lines)
    assert info["model"] == "m-9" and info["cli_version"] == "9.9"
    assert len(info["stops"]) == 2
    assert info["closing_text"] == "good ending."
    assert arms.blocks(info) == 1
    assert arms.stop_blocks_took_effect(info, lambda t: t.endswith("?")) == [True]
    assert info["output_tokens"] == 42 and info["num_turns"] == 3


def test_a_block_declared_inside_hook_specific_output_counts_as_issued():
    lines = [text_msg("x " + DASH + " y"), stop_resp(INNER_BLOCK),
             ev(type="result", result="x " + DASH + " y")]
    info = arms.parse_stream(lines)
    assert arms.blocks(info) == 1
    # nothing followed the block: it did not take effect
    assert arms.stop_blocks_took_effect(info, lambda t: DASH in t) == [False]


def test_denial_is_tied_to_its_call_and_effect_is_read_from_the_next_write():
    lines = [tool_msg("a", "Write", {"file_path": "/r/f.md", "content": "x" + DASH}),
             ev(type="system", subtype="hook_response", hook_event="PreToolUse", output=DENY),
             tool_res("a", "1 forbidden dash in it", err=True),
             tool_msg("b", "Write", {"file_path": "/r/f.md", "content": "x, y"}),
             tool_res("b", "ok"), text_msg("done.")]
    info = arms.parse_stream(lines)
    assert arms.blocks(info) == 1
    assert info["tools"][0]["denied"] and not info["tools"][1]["denied"]
    assert arms.denials_took_effect(info, lambda p: DASH in p) == [True]


def test_denial_with_no_later_write_did_not_take_effect():
    lines = [tool_msg("a", "Write", {"file_path": "/r/f.md", "content": DASH}),
             ev(type="system", subtype="hook_response", hook_event="PreToolUse", output=DENY),
             tool_res("a", "1 forbidden dash in it", err=True), text_msg("gave up.")]
    info = arms.parse_stream(lines)
    assert arms.denials_took_effect(info, lambda p: DASH in p) == [False]


def test_prove():
    g = lambda t: "?" in t  # noqa: E731
    assert arms.prove(g, "why?", "fine.") == []
    assert len(arms.prove(lambda t: False, "why?", "fine.")) == 1
    assert arms.prove(lambda t: True, "why?", "fine.") == ["the grader flagged the clean sample"]


FAKE = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
assert "--model" in args
open("made.txt", "w").write("hello")
os.system("git add -A >/dev/null && git commit -qm 'the run' >/dev/null")
env_ok = "CLAUDE_CODE_SIMPLE" not in os.environ and "ANTHROPIC_API_KEY" not in os.environ
cfg = os.environ["CLAUDE_CONFIG_DIR"]
os.makedirs(os.path.join(cfg, "projects", "p"), exist_ok=True)
open(os.path.join(cfg, "projects", "p", "s.jsonl"), "w").write("{}\n")
print(json.dumps({"type": "system", "subtype": "init", "model": args[args.index("--model") + 1],
                  "claude_code_version": "0.0.1"}))
print(json.dumps({"type": "assistant", "message": {"content": [
    {"type": "text", "text": "env clean" if env_ok else "env dirty"}]}}))
print(json.dumps({"type": "result", "result": "env clean" if env_ok else "env dirty",
                  "num_turns": 1, "usage": {"output_tokens": 3},
                  "modelUsage": {args[args.index("--model") + 1]: {"outputTokens": 3}}}))
'''


def test_run_arm_end_to_end_with_a_fake_claude(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    tpl = tmp_path / "tpl"
    tpl.mkdir()
    (tpl / "a.txt").write_text("a")
    out = tmp_path / "out"
    info, state = arms.run_arm("do it", str(tpl), "rules\n", [], {}, "claude-haiku-4-5-20251001", str(out),
                               claude=str(fake), scratch_root=str(tmp_path), timeout_s=60)
    assert info["closing_text"] == "env clean"
    assert info["model"] == "claude-haiku-4-5-20251001"
    assert info["model_requested"] == "claude-haiku-4-5-20251001"
    assert info["model_usage"] == {"claude-haiku-4-5-20251001": {"outputTokens": 3}}
    assert state["files"]["made.txt"] == "hello"
    assert [c["message"].strip() for c in state["commits"]] == ["the run"]
    assert (out / "run.json").exists() and (out / "transcript-s.jsonl").exists()
    assert not [p for p in os.listdir(tmp_path) if p.startswith("arms-")]


def test_setup_script_can_leave_a_history_before_the_base_commit(tmp_path):
    tpl = tmp_path / "tpl"
    tpl.mkdir()
    (tpl / "a.txt").write_text("a\n")
    setup = tmp_path / "setup.sh"
    setup.write_text("git add a.txt && git commit -qm first\necho b > b.txt\n"
                     "git add b.txt && git commit -qm second\n")
    wd = tmp_path / "wd"
    base = arms.prepare_workdir(str(wd), str(tpl), "rules\n", setup=str(setup))
    log = subprocess.run(["git", "-C", str(wd), "log", "--format=%s"], capture_output=True,
                         text=True).stdout.split("\n")
    assert log[:3] == ["situation", "second", "first"]
    assert base and not (wd / "setup.sh").exists()


def test_a_failing_setup_is_refused(tmp_path):
    setup = tmp_path / "setup.sh"
    setup.write_text("exit 3\n")
    with pytest.raises(arms.ArmsError):
        arms.prepare_workdir(str(tmp_path / "wd"), None, "x", setup=str(setup))


def test_changed_files_are_what_the_run_wrote(tmp_path):
    tpl = tmp_path / "tpl"
    tpl.mkdir()
    (tpl / "same.txt").write_text("same\n")
    (tpl / "edit.txt").write_text("before\n")
    wd = tmp_path / "wd"
    base = arms.prepare_workdir(str(wd), str(tpl), "rules\n")
    (wd / "edit.txt").write_text("after\n")
    (wd / "new.txt").write_text("new\n")
    state = {"files": arms.tree_files(str(wd)), "base": arms.base_tree(str(wd), base)}
    assert sorted(arms.changed_files(state)) == ["edit.txt", "new.txt"]


def test_run_arm_refuses_an_alias_before_touching_anything(tmp_path):
    with pytest.raises(arms.ArmsError):
        arms.run_arm("do it", str(tmp_path), "rules\n", [], {}, "sonnet", str(tmp_path / "out"),
                     scratch_root=str(tmp_path))
    assert not (tmp_path / "out").exists()
