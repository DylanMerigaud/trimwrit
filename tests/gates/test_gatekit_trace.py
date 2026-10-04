import json
import os
import shutil
import subprocess
import sys
import time

import pytest

# relies on tests/gates being a regular package while the root gates/ has no __init__.py
from gates._hook import clean_env, rows, write_config

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(REPO, "gates", "gatekit")
sys.path.insert(0, os.path.join(REPO, "gates"))
from gatekit import trace  # noqa: E402

GATE = '''import json, os, sys
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_ROOT)
from gatekit import trace, transcript  # noqa: E402
HOOK = "fixture_gate.py"


def body():
    payload = trace.read_payload()
    trace.plugin_settings(payload, root=PLUGIN_ROOT)
    text = transcript.final_text(payload)
    if "CRASHME" in text:
        raise RuntimeError("boom")
    if "BLOCKME" not in text:
        trace.chain_clear(HOOK, payload)
        return 0
    if not trace.chain_should_block(HOOK, payload, "fixture"):
        print(trace.cap_message(HOOK, "fixture"))
        return 0
    trace.witness("fixture", "Stop", "fixture_block", payload)
    print(json.dumps({"decision": "block", "reason": "fixture"}))
    return 0


if __name__ == "__main__":
    trace.configure(sys.argv)
    sys.exit(trace.run(HOOK, "Stop", body, 8))
'''


@pytest.fixture
def fx(tmp_path):
    plugin = tmp_path / "plugin"
    (plugin / "scripts").mkdir(parents=True)
    shutil.copytree(SRC, str(plugin / "gatekit"), ignore=shutil.ignore_patterns("__pycache__"))
    (plugin / "defaults.json").write_text(json.dumps(
        {"section": "fixture", "defaults": {"cap": 3}}), encoding="utf-8")
    (plugin / "scripts" / "fixture_gate.py").write_text(GATE, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    return plugin, home


def call(fx, message, session="s1", active=False, raw=None):
    plugin, home = fx
    payload = raw if raw is not None else json.dumps({
        "session_id": session, "stop_hook_active": active, "last_assistant_message": message,
        "cwd": str(home)})
    return subprocess.run(
        [sys.executable, str(plugin / "scripts" / "fixture_gate.py"), "--home", str(home)],
        input=payload, capture_output=True, text=True, env=clean_env(), cwd=str(home),
        timeout=60)


def health(fx):
    return rows(os.path.join(str(fx[1]), "hook-health.jsonl"))


def refusals(fx):
    return rows(os.path.join(str(fx[1]), "door-refusals.jsonl"))


def wait_for(path, needle, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            if needle in text:
                return text
        except FileNotFoundError:
            pass
        time.sleep(0.05)
    return ""


def test_three_blocks_in_a_row_then_the_cap(fx):
    for i in range(3):
        out = call(fx, "BLOCKME", active=i > 0)
        assert json.loads(out.stdout)["decision"] == "block"
    out = call(fx, "BLOCKME", active=True)
    assert out.returncode == 0
    assert "3 blocks in a row" in json.loads(out.stdout)["systemMessage"]
    caps = [r for r in health(fx) if r["outcome"] == "cap"]
    assert len(caps) == 1 and caps[0]["blocks"] == 3


def test_a_new_chain_after_a_cap_blocks_again(fx):
    for i in range(3):
        call(fx, "BLOCKME", active=i > 0)
    call(fx, "BLOCKME", active=True)
    out = call(fx, "BLOCKME", active=False)
    assert json.loads(out.stdout)["decision"] == "block"


def test_a_clean_answer_resets_the_count(fx):
    call(fx, "BLOCKME")
    call(fx, "BLOCKME", active=True)
    assert call(fx, "all good", active=True).stdout == ""
    # chain_clear removed the count: three more blocks inside the same chain do not cap
    # (without chain_clear the third would hit the cap)
    for _ in range(3):
        out = call(fx, "BLOCKME", active=True)
        assert json.loads(out.stdout)["decision"] == "block"
    assert not [r for r in health(fx) if r["outcome"] == "cap"]


def test_a_crash_is_loud_and_the_next_clean_run_recovers(fx):
    out = call(fx, "CRASHME")
    assert out.returncode == 0
    assert "UNGUARDED" in json.loads(out.stdout)["systemMessage"]
    assert [r["outcome"] for r in health(fx)] == ["crash"]
    out = call(fx, "fine")
    assert (out.returncode, out.stdout) == (0, "")
    assert [r["outcome"] for r in health(fx)] == ["crash", "recovered"]


def test_the_crash_command_hears_the_crash_and_the_recovery(fx):
    _, home = fx
    (home / "autofix.py").write_text(
        "import sys\nopen(sys.argv[0].replace('autofix.py', 'autofix.log'), 'a').write("
        "' '.join(sys.argv[1:]) + '\\n')\n", encoding="utf-8")
    call(fx, "CRASHME")
    log = wait_for(str(home / "autofix.log"), "--detail")  # the child is detached: poll
    assert "record hook:fixture_gate.py --detail" in log
    call(fx, "fine")
    log = wait_for(str(home / "autofix.log"), "--ok")
    assert "record hook:fixture_gate.py --ok" in log


def test_each_block_is_witnessed_and_a_door_ledger_takes_over(fx):
    _, home = fx
    call(fx, "BLOCKME")
    got = refusals(fx)
    assert len(got) == 1 and got[0]["door"] == "fixture"
    (home / "door_ledger.py").write_text(
        "import os\n\ndef log(door, event, reason_class, session_id):\n"
        "    with open(os.path.join(os.path.dirname(__file__), 'dl.log'), 'a') as fh:\n"
        "        fh.write('%s %s %s %s\\n' % (door, event, reason_class, session_id))\n"
        "    return True\n", encoding="utf-8")
    os.remove(str(home / "door-refusals.jsonl"))
    call(fx, "BLOCKME", session="s2")
    assert (home / "dl.log").read_text().strip() == "fixture Stop fixture_block s2"
    assert not (home / "door-refusals.jsonl").exists()


@pytest.mark.parametrize("bad", [json.dumps({"fixture": {"nokey": 1}}),
                                 json.dumps({"trace": {"nokey": 1}}), "{not json"])
def test_a_broken_config_is_a_loud_crash_never_a_silent_allow(fx, bad):
    _, home = fx
    (home / "trimwrit-gates.json").write_text(bad, encoding="utf-8")
    out = call(fx, "BLOCKME")
    assert out.returncode == 0
    assert "UNGUARDED" in json.loads(out.stdout)["systemMessage"]
    crash = [r for r in health(fx) if r["outcome"] == "crash"]
    assert len(crash) == 1
    if "nokey" in bad:
        assert "nokey" in crash[0]["detail"]


def test_a_typed_config_file_is_read(fx):
    _, home = fx
    write_config(str(home), {"fixture": {"cap": 5}})
    assert json.loads(call(fx, "BLOCKME").stdout)["decision"] == "block"


def test_a_non_object_payload_is_a_crash(fx):
    out = call(fx, None, raw="[1, 2]")
    assert out.returncode == 0
    assert [r["outcome"] for r in health(fx)] == ["crash"]


def test_the_witness_cli_does_not_care_where_the_options_sit(tmp_path):
    out = subprocess.run(
        [sys.executable, os.path.join(SRC, "trace.py"), "--home", str(tmp_path), "--session", "s9",
         "witness", "d", "Stop", "c"], capture_output=True, text=True, env=clean_env(),
        cwd=str(tmp_path), timeout=60)
    assert out.returncode == 0
    got = rows(os.path.join(str(tmp_path), "door-refusals.jsonl"))
    assert [(r["door"], r["session"]) for r in got] == [("d", "s9")]


def test_the_witness_cli_writes_one_row(tmp_path):
    out = subprocess.run(
        [sys.executable, os.path.join(SRC, "trace.py"), "witness", "d", "PreToolUse", "c",
         "--session", "s1", "--home", str(tmp_path)], capture_output=True, text=True,
        env=clean_env(), cwd=str(tmp_path), timeout=60)
    assert out.returncode == 0
    got = rows(os.path.join(str(tmp_path), "door-refusals.jsonl"))
    assert len(got) == 1
    assert {k: got[0][k] for k in ("door", "event", "reason_class", "session")} == {
        "door": "d", "event": "PreToolUse", "reason_class": "c", "session": "s1"}
    assert "at" in got[0]


def test_an_explicit_root_wins_over_gatekits_own_location(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "defaults.json").write_text(json.dumps(
        {"section": "elsewhere", "defaults": {"k": 9}}), encoding="utf-8")
    trace.configure(["x", "--home", str(tmp_path / "h")])
    try:
        assert trace.plugin_settings({"cwd": str(tmp_path)}, root=str(other)) == {"k": 9}
    finally:
        trace._HOME = None
        trace._SETTINGS = None


def test_transcript_prefers_the_payload_then_the_last_main_thread_row(tmp_path):
    from gatekit import transcript
    path = tmp_path / "t.jsonl"
    rows_ = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}},
        {"type": "assistant", "isSidechain": True,
         "message": {"content": [{"type": "text", "text": "side"}]}},
        {"type": "assistant", "message": {"content": "plain string"}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use"}]}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows_) + "\nnot json\n", encoding="utf-8")
    assert transcript.last_assistant_text(str(path)) == "plain string"
    assert transcript.last_assistant_text(str(tmp_path / "missing")) == ""
    assert transcript.final_text({"last_assistant_message": "live",
                                  "transcript_path": str(path)}) == "live"
    assert transcript.final_text({"transcript_path": str(path)}) == "plain string"
