"""The no-em-dash plugin, run the way Claude Code runs it: a payload on stdin, --home on argv."""
import json
import os
import re
import sys

from gates._hook import ROOT, plugin, rows, run_py, write_config

EM, EN = chr(0x2014), chr(0x2013)
STOP, WRITE = "no_em_dash.py", "no_em_dash_write.py"
NAME = "no-em-dash"


def stop(text, session, active=False):
    return {"hook_event_name": "Stop", "session_id": session, "stop_hook_active": active,
            "last_assistant_message": text}


def tool(name, tool_input, session="w"):
    return {"hook_event_name": "PreToolUse", "session_id": session, "tool_name": name,
            "tool_input": tool_input}


def denied(r):
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    return out["permissionDecisionReason"]


def test_stop_blocks_an_em_dash_with_exit_2_a_reason_and_a_witness_row(tmp_path):
    r = run_py(NAME, STOP, stop("No news " + EM + " pausing here.", "s1"), tmp_path)
    assert r.returncode == 2
    assert "forbidden dash" in r.stderr and "em-dash" in r.stderr
    assert "hookSpecificOutput" not in r.stdout
    got = rows(os.path.join(str(tmp_path), "door-refusals.jsonl"))
    assert [(x["door"], x["event"], x["reason_class"]) for x in got] == \
        [("no-em-dash", "Stop", "forbidden_dash")]


def test_en_dash_is_caught_and_code_is_exempt(tmp_path):
    r = run_py(NAME, STOP, stop("pages 3" + EN + "5", "s2"), tmp_path)
    assert r.returncode == 2 and "en-dash" in r.stderr
    for text in ("the gate refuses `" + EM + "` in prose",
                 "see:\n```\na " + EM + " b\n```\ndone"):
        r = run_py(NAME, STOP, stop(text, "s2"), tmp_path)
        assert (r.returncode, r.stdout, r.stderr) == (0, "", ""), text


def test_clean_message_is_silent(tmp_path):
    r = run_py(NAME, STOP, stop("Done: 3 rows merged.", "s3"), tmp_path)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    assert rows(os.path.join(str(tmp_path), "hook-health.jsonl")) == []


def test_three_blocks_in_a_chain_then_the_cap_message(tmp_path):
    codes = []
    for i in range(4):
        r = run_py(NAME, STOP, stop("a " + EM + " b", "cap", active=i > 0), tmp_path)
        codes.append(r.returncode)
    assert codes == [2, 2, 2, 0]
    assert "did not clear it" in json.loads(r.stdout)["systemMessage"]
    caps = [x for x in rows(os.path.join(str(tmp_path), "hook-health.jsonl"))
            if x["outcome"] == "cap"]
    assert len(caps) == 1 and caps[0]["hook"] == "no-em-dash.py" and caps[0]["blocks"] == 3


def test_configured_codepoints_decide_what_is_refused(tmp_path):
    write_config(tmp_path, {NAME: {"codepoints": [8212]}})
    assert run_py(NAME, STOP, stop("pages 3" + EN + "5", "cfg"), tmp_path).returncode == 0
    assert run_py(NAME, STOP, stop("a " + EM + " b", "cfg"), tmp_path).returncode == 2
    r = run_py(NAME, WRITE, tool("Write", {"content": "3" + EN + "5"}), tmp_path)
    assert (r.returncode, r.stdout) == (0, "")
    assert "em-dash" in denied(run_py(NAME, WRITE, tool("Write", {"content": "a" + EM}), tmp_path))


def test_a_broken_config_is_a_loud_crash_not_a_block_or_a_silent_pass(tmp_path):
    write_config(tmp_path, {NAME: {"codepoint": [8212]}})
    r = run_py(NAME, STOP, stop("a " + EM + " b", "bad"), tmp_path)
    assert r.returncode == 0
    assert "UNGUARDED" in json.loads(r.stdout)["systemMessage"]


def test_write_with_a_dash_in_content_is_denied(tmp_path):
    reason = denied(run_py(NAME, WRITE, tool("Write", {"content": "x " + EM + " y"}), tmp_path))
    assert "forbidden dash" in reason
    got = rows(os.path.join(str(tmp_path), "door-refusals.jsonl"))
    assert [(x["door"], x["reason_class"]) for x in got] == \
        [("no-em-dash-write", "forbidden_dash_write")]
    ok = run_py(NAME, WRITE, tool("Write", {"content": "clean"}), tmp_path)
    assert (ok.returncode, ok.stdout) == (0, "")


def test_bash_heredoc_redirect_and_read_cases(tmp_path):
    heredoc = "cat > f.md <<'EOF'\nhello " + EM + " there\nEOF"
    assert "em-dash" in denied(run_py(NAME, WRITE, tool("Bash", {"command": heredoc}), tmp_path))
    redirect = 'echo "x ' + EM + ' y" > f.md'
    assert "em-dash" in denied(run_py(NAME, WRITE, tool("Bash", {"command": redirect}), tmp_path))
    read = "grep -c $'\\u2014' file"
    r = run_py(NAME, WRITE, tool("Bash", {"command": read}), tmp_path)
    assert (r.returncode, r.stdout) == (0, "")
    split = "echo clean > a.txt ; grep -c " + EM + " b.txt"
    r = run_py(NAME, WRITE, tool("Bash", {"command": split}), tmp_path)
    assert (r.returncode, r.stdout) == (0, "")


def test_multiedit_checks_every_edit(tmp_path):
    edits = [{"old_string": "a", "new_string": "clean"},
             {"old_string": "b", "new_string": "dirty " + EM}]
    assert "em-dash" in denied(run_py(NAME, WRITE, tool(
        "MultiEdit", {"file_path": "f", "edits": edits}), tmp_path))
    r = run_py(NAME, WRITE, tool("MultiEdit", {"file_path": "f", "edits": edits[:1]}), tmp_path)
    assert (r.returncode, r.stdout) == (0, "")


def test_eval_grader_is_proven():
    """The tracked grader holds the escape, never the character, so its proof here is a
    must_not_match sample in the file and the must_match sample built at run time."""
    sys.path.insert(0, ROOT)
    from trimwrit import cases, check
    found = cases.discover(os.path.join(plugin(NAME), "evals"))
    assert [os.path.basename(c.path) for c in found] == ["no-em-dash-in-prose"]
    problems = [p for c in found for p in check.check_case(c)]
    assert problems == []
    spec = found[0].graders[0]
    assert "\\u2014" in spec["pattern"] and EM not in spec["pattern"]
    pat = re.compile(spec["pattern"], re.I)
    assert pat.search("a " + EM + " b") and pat.search("3" + EN + "5")
    assert not pat.search("a - b, c")


def test_codepoints_that_would_refuse_nothing_are_a_loud_crash(tmp_path):
    for i, bad in enumerate([[EM], [], [True], [-1], [0x110000], "x", [8212.0]]):
        home = tmp_path / str(i)
        write_config(home, {NAME: {"codepoints": bad}})
        for script, payload in ((STOP, stop("a " + EM + " b", "cp" + str(i))),
                                (WRITE, tool("Write", {"content": "a" + EM}))):
            r = run_py(NAME, script, payload, home)
            assert r.returncode == 0, (bad, r.stderr)
            assert "UNGUARDED" in json.loads(r.stdout)["systemMessage"], (bad, script)


def test_stop_reads_the_transcript_when_the_payload_has_no_final_message(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "late " + EM + " news"}]}}) + "\n", encoding="utf-8")
    payload = {"hook_event_name": "Stop", "session_id": "tr", "stop_hook_active": False,
               "transcript_path": str(path)}
    r = run_py(NAME, STOP, payload, tmp_path)
    assert r.returncode == 2 and "em-dash" in r.stderr
