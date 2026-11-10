"""Which skills fire, read from the transcripts, and which never do while their rule's door refuses.

A rule moved out of CLAUDE.md into a SKILL.md costs nothing until the skill loads, and is read
by nobody if the skill never loads. A skill is picked by its description alone, so a description
that does not name the situation leaves its rules dead while the behaviour they describe keeps
happening. The door is the witness: when the gate a rule names refused the behaviour in the last
CHECK_DAYS days and the skill holding that rule did not fire once in the same days, the
description is what failed, and `trimwrit skills --check` says so.

Claude Code writes one JSONL transcript per session under `~/.claude/projects/<slug>/`. A skill
invocation is an assistant `tool_use` named `Skill` whose `input.skill` is `name` or
`plugin:name`, or a user message that opens with `<command-name>/name</command-name>` (the user
typed the slash command). The directory runs to tens of thousands of files and several GB, so a
file whose mtime is before the window is never opened, and a line that holds neither marker is
skipped before any JSON is parsed.
"""
import glob
import json
import os
import re
from datetime import datetime, timedelta, timezone

from . import integrate
from . import preamble

WINDOW_DAYS = 30
CHECK_DAYS = 14
SKILL_FILE = "SKILL.md"
SKILL_TOOL = "Skill"
COMMAND = re.compile(r"^\s*<command-name>/?([^<\s]+)</command-name>")
DOOR_SUFFIXES = (".py", ".sh")


def _udir(user_dir):
    return preamble._user_dir(user_dir)


def _skill_dirs(base, source):
    out = []
    for path in sorted(glob.glob(os.path.join(base, "*", SKILL_FILE))):
        out.append({"name": os.path.basename(os.path.dirname(path)), "path": path,
                    "source": source})
    return out


def _plugin_skills(udir):
    """The skills of the installed plugins, read from Claude Code's registry; [] on any doubt."""
    try:
        with open(os.path.join(udir, integrate.PLUGIN_REGISTRY), encoding="utf-8") as fh:
            plugins = json.load(fh).get("plugins") or {}
    except (OSError, ValueError, AttributeError):
        return []
    out = []
    for key, entries in sorted(plugins.items()):
        paths = [e.get("installPath") for e in entries if isinstance(e, dict)] \
            if isinstance(entries, list) else []
        for path in paths[-1:]:
            if isinstance(path, str):
                out += _skill_dirs(os.path.join(path, "skills"), "plugin " + key)
    return out


def list_skills(root, user_dir=None, plugins=True):
    """[{name, path, source}] for the user's skills, the project's, and the installed plugins'."""
    udir = _udir(user_dir)
    out = _skill_dirs(os.path.join(udir, "skills"), "user")
    out += _skill_dirs(os.path.join(os.path.realpath(root), ".claude", "skills"), "project")
    if plugins:
        out += _plugin_skills(udir)
    return out


def parse_time(value):
    """An aware datetime from an ISO string (a naive one is UTC), or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def transcript_files(dirs, since):
    """Every .jsonl under `dirs` modified at or after `since` (an older file cannot hold a newer
    line)."""
    cutoff = since.timestamp()
    out = []
    for d in dirs:
        for base, _sub, files in os.walk(d):
            for f in files:
                if not f.endswith(".jsonl"):
                    continue
                path = os.path.join(base, f)
                try:
                    if os.path.getmtime(path) >= cutoff:
                        out.append(path)
                except OSError:
                    continue
    return sorted(out)


def _bare(name):
    return name.rsplit(":", 1)[-1]


def _from_line(row):
    """The skill names one transcript row invokes."""
    message = row.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if row.get("type") == "assistant" and isinstance(content, list):
        return [_bare(c["input"]["skill"]) for c in content
                if isinstance(c, dict) and c.get("type") == "tool_use"
                and c.get("name") == SKILL_TOOL and isinstance(c.get("input"), dict)
                and isinstance(c["input"].get("skill"), str)]
    if row.get("type") == "user":
        texts = [content] if isinstance(content, str) else [
            c.get("text") for c in content or [] if isinstance(c, dict) and c.get("type") == "text"]
        for text in texts:
            m = COMMAND.match(text or "")
            if m:
                return [_bare(m.group(1))]
    return []


def invocations(files, since):
    """[(skill name, session id, time)] for every invocation at or after `since`."""
    out = []
    for path in files:
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"Skill"' not in line and "<command-name>" not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                at = parse_time(row.get("timestamp"))
                if at is None or at < since:
                    continue
                for name in _from_line(row):
                    out.append((name, row.get("sessionId") or path, at))
    return out


def usage(skills, calls, since):
    """[{name, path, source, invocations, sessions}] over the calls at or after `since`, sorted
    by invocations ascending: the skills that never fire come first."""
    rows = []
    for skill in skills:
        hits = [c for c in calls if c[0] == skill["name"] and c[2] >= since]
        rows.append(dict(skill, invocations=len(hits), sessions=len({c[1] for c in hits})))
    return sorted(rows, key=lambda r: (r["invocations"], r["name"], r["source"]))


def door_key(name):
    """A door name as a block names it: no .py or .sh suffix, `_` and `-` alike, lowercase."""
    name = os.path.basename(str(name).strip())
    for suffix in DOOR_SUFFIXES:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return name.replace("_", "-").lower()


def refusals(paths, since):
    """{door key: (door as written, count)} of the refusals at or after `since` in the JSONL
    ledgers (a row names its door in `door` or `hook`, its time in `at` or `ts`)."""
    out = {}
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                door = row.get("door") or row.get("hook")
                at = parse_time(row.get("at") or row.get("ts"))
                if not isinstance(door, str) or not door or at is None or at < since:
                    continue
                key = door_key(door)
                if key:
                    name, n = out.get(key, (door, 0))
                    out[key] = (name, n + 1)
    return out


def check(skills, calls, refused, since):
    """[{skill, rule, door, refused}] for each rule of a skill that did not fire since `since`
    while a door its block names refused at least once in the same window.

    A skill whose SKILL.md changed after `since` is not judged yet: on the day rules move into a
    new skill, every one of them reads as never fired over the window before it existed, and a
    rewritten description deserves its own full window before it is called dead again.
    """
    findings = []
    fired = {c[0] for c in calls if c[2] >= since}
    for skill in skills:
        if skill["name"] in fired:
            continue
        try:
            changed = datetime.fromtimestamp(os.path.getmtime(skill["path"]), timezone.utc)
        except OSError:
            continue
        if changed > since:
            continue
        try:
            with open(skill["path"], encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        for rule_id, _case in integrate.rule_ids(text):
            block = (integrate.rule_block(text, rule_id) or "").replace("_", "-").lower()
            for key, (door, n) in sorted(refused.items()):
                if key in block:
                    findings.append({"skill": skill["name"], "rule": rule_id, "door": door,
                                     "refused": n})
    return findings


def finding_line(f, days=CHECK_DAYS):
    return ("skill {}: rule {} door {} refused {} times in {} days, the skill never fired: "
            "rewrite its description so it triggers".format(
                f["skill"], f["rule"], f["door"], f["refused"], days))


def default_transcripts(user_dir=None):
    return os.path.join(_udir(user_dir), "projects")


def now():
    return datetime.now(timezone.utc)


def window(days, at=None):
    return (at or now()) - timedelta(days=days)
