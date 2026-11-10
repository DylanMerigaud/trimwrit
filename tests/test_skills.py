import json
import os
import time
from datetime import datetime, timedelta, timezone

from trimwrit import skills as S
from trimwrit.cli import main

NOW = datetime.now(timezone.utc)


def iso(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def skill_call(name, days_ago, session="s1"):
    return {"type": "assistant", "timestamp": iso(days_ago), "sessionId": session,
            "message": {"role": "assistant", "content": [
                {"type": "text", "text": "loading"},
                {"type": "tool_use", "id": "t", "name": "Skill", "input": {"skill": name}}]}}


def slash(name, days_ago, session="s1"):
    return {"type": "user", "timestamp": iso(days_ago), "sessionId": session,
            "message": {"role": "user", "content": "<command-name>/{}</command-name>\n"
                                                    "<command-args></command-args>".format(name)}}


def write_jsonl(path, rows, mtime_days_ago=0):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    t = time.time() - mtime_days_ago * 86400
    os.utime(str(path), (t, t))


def skill(base, name, body="", description="Use when it fits.", age_days=20):
    d = base / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: {}\ndescription: {}\n---\n\n{}".format(
        name, description, body), encoding="utf-8")
    old = time.time() - age_days * 86400
    os.utime(d / "SKILL.md", (old, old))


def layout(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "repo"
    root.mkdir()
    skill(home / "skills", "deploy")
    skill(home / "skills", "outreach",
          "<!-- trimwrit: R7 case 0007 -->\nNever send before the gate. Door: send_gate.py.\n")
    skill(root / ".claude" / "skills", "review")
    projects = home / "projects" / "-repo"
    write_jsonl(projects / "a.jsonl", [
        skill_call("deploy", 1, "s1"), skill_call("deploy", 2, "s1"),
        skill_call("myplugin:review", 3, "s2"), slash("deploy", 1, "s3"),
        {"type": "assistant", "timestamp": iso(40), "sessionId": "s0",
         "message": {"content": [{"type": "tool_use", "name": "Skill",
                                  "input": {"skill": "outreach"}}]}},
        {"type": "user", "timestamp": iso(1), "message": {"content": "plain text"}}])
    # An old file is never opened, even if its lines would count.
    write_jsonl(projects / "old.jsonl", [skill_call("outreach", 1)], mtime_days_ago=90)
    return home, root


def test_invocations_and_sessions_per_skill(tmp_path):
    home, root = layout(tmp_path)
    found = S.list_skills(str(root), str(home))
    assert sorted(s["name"] for s in found) == ["deploy", "outreach", "review"]
    since = S.window(30, NOW)
    calls = S.invocations(S.transcript_files([str(home / "projects")], since), since)
    rows = {r["name"]: r for r in S.usage(found, calls, since)}
    assert (rows["deploy"]["invocations"], rows["deploy"]["sessions"]) == (3, 2)
    assert (rows["review"]["invocations"], rows["review"]["sessions"]) == (1, 1)
    assert rows["outreach"]["invocations"] == 0
    assert [r["name"] for r in S.usage(found, calls, since)][0] == "outreach"


def test_lines_without_a_marker_are_never_parsed(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("not json at all\n" + json.dumps(skill_call("x", 0)) + "\n{broken \"Skill\"\n",
                    encoding="utf-8")
    calls = S.invocations([str(path)], S.window(1, NOW))
    assert [c[0] for c in calls] == ["x"]


def test_door_keys_ignore_suffix_and_separator():
    assert S.door_key("send_gate.py") == S.door_key("send-gate") == "send-gate"
    assert S.door_key("hooks/no_em_dash.sh") == "no-em-dash"


def test_cli_table_sorted_ascending(tmp_path, capsys):
    home, root = layout(tmp_path)
    assert main(["skills", "--root", str(root), "--user-dir", str(home)]) == 0
    lines = capsys.readouterr().out.splitlines()
    names = [line.split()[0] for line in lines[1:4]]
    assert names == ["outreach", "review", "deploy"]


def test_check_without_a_ledger_only_reports(tmp_path, capsys):
    home, root = layout(tmp_path)
    assert main(["skills", "--check", "--root", str(root), "--user-dir", str(home)]) == 0
    assert "no refusals ledger was given" in capsys.readouterr().err


def test_check_flags_a_silent_skill_whose_door_refused(tmp_path, capsys):
    home, root = layout(tmp_path)
    ledger = tmp_path / "door-refusals.jsonl"
    write_jsonl(ledger, [{"door": "send-gate", "at": iso(2)}, {"hook": "send_gate.py",
                                                               "ts": iso(5)},
                         {"door": "send-gate", "at": iso(20)}, {"door": "other", "at": iso(1)}])
    argv = ["skills", "--check", "--root", str(root), "--user-dir", str(home),
            "--refusals", str(ledger)]
    assert main(argv) == 1
    out = capsys.readouterr().out
    assert ("skill outreach: rule R7 door send-gate refused 2 times in 14 days, the skill "
            "never fired: rewrite its description so it triggers") in out
    # The skill fires once: nothing to say.
    write_jsonl(home / "projects" / "-repo" / "b.jsonl", [skill_call("outreach", 3)])
    assert main(argv) == 0


def test_check_waits_until_the_skill_is_as_old_as_the_window(tmp_path, capsys):
    """A skill written (or its description rewritten) inside the window has not had its 14 days
    yet: the rules moved into it on its first day would all read as never fired."""
    home, root = layout(tmp_path)
    young = home / "skills" / "outreach" / "SKILL.md"
    recent = time.time() - 3 * 86400
    os.utime(young, (recent, recent))
    ledger = tmp_path / "door-refusals.jsonl"
    write_jsonl(ledger, [{"door": "send-gate", "at": iso(1)}])
    assert main(["skills", "--check", "--root", str(root), "--user-dir", str(home),
                 "--refusals", str(ledger)]) == 0
    assert "never fired" not in capsys.readouterr().out


def test_check_ignores_a_door_that_did_not_refuse_in_the_window(tmp_path, capsys):
    home, root = layout(tmp_path)
    ledger = tmp_path / "door-refusals.jsonl"
    write_jsonl(ledger, [{"door": "send-gate", "at": iso(20)}])
    assert main(["skills", "--check", "--json", "--root", str(root), "--user-dir", str(home),
                 "--refusals", str(ledger)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["findings"] == [] and doc["days"] == 30
