"""oneshot-gate: a print-mode claude with every tool disabled is refused before its first request.

The decision is read twice: on argv lists (refusal), and end to end, with a stand-in `claude`
binary that starts the real hook the way Claude Code does (through /bin/sh, payload on stdin),
so the ancestor walk that finds the session's argv is the one under test."""
import json
import os
import stat
import subprocess
import sys

import pytest

from gates import _hook

PLUGIN = "oneshot-gate"
SCRIPT = os.path.join(_hook.plugin(PLUGIN), "scripts", "oneshot_gate.py")
sys.path.insert(0, _hook.plugin(PLUGIN))
sys.path.insert(0, os.path.dirname(SCRIPT))
import oneshot_gate as gate  # noqa: E402

LEDGER = "hook-health.jsonl"
# trimwrit's judge before 0.11.0, as it ran 2270 times on 2026-11-02
JUDGE_2026_11_02 = ["claude", "-p", "You are grading one output against one rule.", "--model",
                    "claude-opus-5-5", "--output-format", "json", "--setting-sources",
                    "project,local", "--strict-mcp-config", "--tools", ""]


@pytest.mark.parametrize("argv", [
    JUDGE_2026_11_02,
    ["/usr/bin/claude", "--print", "classify", "--tools", ""],
    ["claude", "-p", "x", "--tools="],
    # read back from ps: the empty argument is gone
    ["claude", "-p", "grade", "this", "--tools", "--model", "m"],
    ["claude", "-p", "grade", "--tools"],
    ["node", "/opt/lib/node_modules/@anthropic-ai/claude-code/cli.js", "-p", "x", "--tools", ""],
])
def test_a_one_shot_carrying_the_harness_is_refused(argv):
    assert gate.refusal(argv)


@pytest.mark.parametrize("argv", [
    JUDGE_2026_11_02 + ["--safe-mode"],
    ["claude", "--bare", "-p", "x", "--tools", ""],
    ["claude", "--dangerously-skip-permissions"],
    ["claude", "--tools", "", "--model", "m"],
    ["claude", "-p", "fix the failing test", "--dangerously-skip-permissions"],
    ["claude", "-p", "x", "--tools", "Bash,Read"],
    ["claude", "-p", "x", "--tools", "default"],
    ["python3", "-p", "--tools", ""],
    [],
])
def test_an_agent_an_interactive_or_a_lean_session_passes(argv):
    assert gate.refusal(argv) is None


def test_the_prompt_is_kept_out_of_the_ledger():
    flags = gate.shown(JUDGE_2026_11_02)
    assert "You are grading one output against one rule." not in flags
    assert flags[:1] == ["-p"] and "--tools" in flags
    assert gate.shown(["claude", "-p", "--output-format", "json"]) == ["-p", "--output-format",
                                                                        "json"]


def _fake_claude(tmp_path, home):
    """A `claude` that starts the hook as its own grandchild (claude -> sh -> python3), hands it
    a UserPromptSubmit payload and exits with the hook's code, printing what it said."""
    path = tmp_path / "bin" / "claude"
    path.parent.mkdir()
    hook = "{} {} --home {}".format(sys.executable, SCRIPT, home)
    path.write_text(
        "#!{}\nimport json, subprocess, sys\n"
        "p = subprocess.run(['/bin/sh', '-c', {!r}], input=json.dumps({{"
        "'hook_event_name': 'UserPromptSubmit', 'session_id': 's1', 'cwd': '/tmp', "
        "'prompt': 'grade'}}), capture_output=True, text=True)\n"
        "sys.stderr.write(p.stderr)\nsys.stdout.write(p.stdout)\nsys.exit(p.returncode)\n"
        .format(sys.executable, hook), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _run(claude, *args):
    return subprocess.run([claude, *args], capture_output=True, text=True,
                          env=_hook.clean_env(), timeout=60)


def test_the_hook_finds_its_session_and_refuses_it(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    claude = _fake_claude(tmp_path, home)
    out = _run(claude, *JUDGE_2026_11_02[1:])
    assert out.returncode == 2, (out.stdout, out.stderr)
    said = json.loads(out.stderr.strip().splitlines()[-1])
    assert said["refused"] == "oneshot-not-lean"
    assert "--safe-mode" in said["fix"] and "--bare" in said["fix"]
    rows = _hook.rows(os.path.join(str(home), LEDGER))
    assert [r["outcome"] for r in rows if r["hook"] == "oneshot-gate"] == ["refused"]


@pytest.mark.parametrize("args", [
    ("-p", "fix it", "--tools", "Bash,Read"),
    ("-p", "grade", "--tools", "", "--safe-mode"),
    ("--dangerously-skip-permissions",),
])
def test_the_hook_lets_an_agent_or_a_lean_call_through(tmp_path, args):
    home = tmp_path / "home"
    home.mkdir()
    out = _run(_fake_claude(tmp_path, home), *args)
    assert out.returncode == 0, (out.stdout, out.stderr)
    assert out.stderr == ""


def test_no_claude_ancestor_reads_as_none():
    # pid 1 has no parent to walk to (this suite may itself run under a claude session, so a
    # hook started from here would find that one)
    assert gate.claude_argv(start=1) is None


def test_a_session_whose_argv_cannot_be_read_is_let_through_and_traced(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(gate.trace, "_HOME", str(home))
    monkeypatch.setattr(gate, "claude_argv", lambda: None)
    assert gate.on_prompt({"hook_event_name": "UserPromptSubmit"}) == 0
    rows = _hook.rows(os.path.join(str(home), LEDGER))
    assert [r["outcome"] for r in rows] == ["unreadable"]
