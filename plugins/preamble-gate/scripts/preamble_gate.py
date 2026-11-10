#!/usr/bin/env python3
"""PreToolUse hook: refuse a hand edit that grows an always-loaded file past its budget.

`trimwrit integrate` refuses a rule that would push a CLAUDE.md past its budget, but most lines
reach those files through Write and Edit, not through the CLI. One user's global CLAUDE.md grew
to 14.7k tokens, every token reread on every turn of every session, and no write was ever
refused. This gate holds the hand edit to the same budget the CLI enforces.

WHAT IS ALWAYS LOADED
  any file named CLAUDE.md (the project one, a nested one, the user one)
  ~/.claude/rules/*.md        one piece: every file of the directory counts together
  MEMORY.md inside a `memory` directory (Claude Code's per-project memory index)

THE TEXT AFTER THE EDIT
  Write      tool_input.content
  Edit       old_string to new_string on the file as it is now, every occurrence when
             replace_all is set
  MultiEdit  each edit in order, on the result of the previous one
  An old_string the file does not hold lets the call through: the tool itself will fail.

THE BUDGET
  Project layer: the nearest `.trimwrit/budget.json` above the edited file (for a MEMORY.md and
  a user file, above CLAUDE_PROJECT_DIR or the payload cwd), keyed as trimwrit's preamble module
  keys it: the piece name (`CLAUDE.md`, `~/.claude/CLAUDE.md`, `~/.claude/rules`, `MEMORY.md`) or
  the path relative to that root (`outreach/CLAUDE.md`).
  User layer: the `preamble_budgets` key of the `preamble-gate` section of trimwrit-gates.json.
  There `CLAUDE.md` also covers every project's CLAUDE.md. The project layer wins on a key.
  No budget found: the edit passes.

A SHRINKING EDIT ALWAYS PASSES, even on a file still over: refusing it would block the very edit
that brings the file back down. Refused: after > budget AND after > before.

Tokens are trimwrit's estimate (trimwrit.preamble.tokens): HTML comments stripped, UTF-8 bytes
divided by BYTES_PER_TOKEN, rounded. A plugin ships alone and cannot import trimwrit, so the
function is duplicated here and a test holds the two to the same numbers.

Every deny is counted, every error is loud: a crash or a timeout lets the edit through and is
reported. No environment variable and no configuration key turns this gate off.
"""
import glob
import json
import os
import re
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_ROOT)
from gatekit import config, trace  # noqa: E402

HOOK = "preamble-gate.py"
DOOR = "preamble-gate"
TIMEOUT_S = 8
ENV_PROJECT = "CLAUDE_PROJECT_DIR"
BYTES_PER_TOKEN = 2.35
BUDGET_FILE = os.path.join(".trimwrit", "budget.json")
MAX_UP = 40

PROJECT = "CLAUDE.md"
USER = "~/.claude/CLAUDE.md"
RULES = "~/.claude/rules"
MEMORY = "MEMORY.md"
USER_PREFIX = "~/.claude"

ROUTE = ("Put a situational rule in the SKILL.md of its situation, its story in the "
         "repository's history doc, keep one line naming the door; `trimwrit relocate <rule> "
         "--to <file>` moves a rule with its marker.")

_COMMENT = re.compile(r"<!--.*?-->", re.S)


def tokens(text):
    """Estimated tokens of what the model sees of `text` (trimwrit.preamble.tokens)."""
    return int(round(len(_COMMENT.sub("", text).encode("utf-8")) / BYTES_PER_TOKEN))


def user_dir():
    """The user layer: --home in a test, ~/.claude otherwise."""
    return os.path.realpath(trace.home() or os.path.join(os.path.expanduser("~"), ".claude"))


def piece(path, udir):
    """(piece name, kind) of an always-loaded file, or None. kind is project, user, rules or
    memory."""
    base = os.path.basename(path)
    if base == PROJECT:
        return (USER, "user") if path == os.path.join(udir, PROJECT) else (PROJECT, "project")
    if os.path.dirname(path) == os.path.join(udir, "rules") and base.endswith(".md"):
        return RULES, "rules"
    if base == MEMORY and os.path.basename(os.path.dirname(path)) == "memory":
        return MEMORY, "memory"
    return None


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def after_text(tool, tool_input, current):
    """The file's text once this call ran, or None when it cannot be known (let it through)."""
    if tool == "Write":
        content = tool_input.get("content")
        return content if isinstance(content, str) else None
    if tool == "Edit":
        edits = [tool_input]
    elif tool == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            return None
    else:
        return None
    text = current or ""
    for edit in edits:
        if not isinstance(edit, dict):
            return None
        old, new = edit.get("old_string"), edit.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str) or old not in text:
            return None
        text = text.replace(old, new) if edit.get("replace_all") else text.replace(old, new, 1)
    return text


def find_root(start):
    """The nearest directory at or above `start` holding .trimwrit/budget.json, or None."""
    here = os.path.realpath(start)
    for _ in range(MAX_UP):
        if os.path.isfile(os.path.join(here, BUDGET_FILE)):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent
    return None


def project_budgets(root):
    """{key: tokens} of a budget.json. A broken one raises: run() reports it as a crash."""
    with open(os.path.join(root, BUDGET_FILE), encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("{} is not a JSON object".format(os.path.join(root, BUDGET_FILE)))
    return {str(k): int(v) for k, v in data.items()}


def _expand(key, root, udir):
    """The absolute path a budget key names (~/.claude is the user layer)."""
    if key == USER_PREFIX or key.startswith(USER_PREFIX + "/"):
        return os.path.realpath(udir + key[len(USER_PREFIX):])
    key = os.path.expanduser(key)
    if not os.path.isabs(key):
        if root is None:
            return None
        key = os.path.join(root, key)
    return os.path.realpath(key)


def _lookup(budgets, keys, path, root, udir):
    for key in keys:
        if key in budgets:
            return key, budgets[key]
    for key, value in budgets.items():
        if _expand(key, root, udir) == path:
            return key, value
    return None


def budget(path, name, kind, payload, udir, user_budgets):
    """(key, budget, where) for the file at `path`, or None. The project layer wins a key."""
    # A CLAUDE.md answers to the repository it sits in, never to the one the session runs from.
    starts = [os.path.dirname(path)] if kind == "project" else [
        p for p in (os.environ.get(ENV_PROJECT), payload.get("cwd")) if p]
    root = next((r for r in (find_root(s) for s in starts) if r), None)
    keys = [name]
    if kind == "project" and root and (path + os.sep).startswith(root + os.sep):
        rel = os.path.relpath(path, root)
        keys = [rel] if rel != name else keys
    if root:
        found = _lookup(project_budgets(root), keys, path, root, udir)
        if found:
            return found + (os.path.join(root, BUDGET_FILE),)
    generic = keys + ([PROJECT] if kind == "project" else [])
    found = _lookup(user_budgets, generic, path, None, udir)
    return found + ("trimwrit-gates.json preamble-gate.preamble_budgets",) if found else None


def measure(path, kind, current, after, udir):
    """(before, after) in tokens; the rules directory counts all its files together."""
    others = 0
    if kind == "rules":
        for other in glob.glob(os.path.join(udir, "rules", "*.md")):
            if os.path.realpath(other) != path:
                others += tokens(_read(other) or "")
    return others + tokens(current or ""), others + tokens(after)


def user_budgets(settings):
    """The user layer's budgets. A value that is not a whole number is a loud ConfigError."""
    out = settings["preamble_budgets"]
    for key, value in out.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise config.ConfigError(
                "preamble-gate.preamble_budgets.{} must be a token count, got {!r}".format(
                    key, value))
    return out


def body():
    payload = trace.read_payload()
    budgets = user_budgets(trace.plugin_settings(payload, root=PLUGIN_ROOT))
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("file_path"), str):
        return 0
    path = tool_input["file_path"]
    if not os.path.isabs(path):
        path = os.path.join(payload.get("cwd") or os.getcwd(), path)
    path = os.path.realpath(path)
    udir = user_dir()
    found = piece(path, udir)
    if not found:
        return 0
    name, kind = found
    current = _read(path)
    after = after_text(payload.get("tool_name"), tool_input, current)
    if after is None:
        return 0
    limit = budget(path, name, kind, payload, udir, budgets)
    if not limit:
        return 0
    key, cap, where = limit
    before, after_n = measure(path, kind, current, after, udir)
    if after_n <= cap or after_n <= before:
        return 0
    reason = ("preamble-gate: {} loads every turn; this {} takes it from {} to {} tokens, budget "
              "{} (key {} in {}). {} A shrinking edit always passes.".format(
                  path, payload.get("tool_name"), before, after_n, cap, key, where, ROUTE))
    trace.witness(DOOR, "PreToolUse", "over_budget", payload)
    json.dump({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }, sys.stdout)
    return 0


def main():
    trace.configure(sys.argv)
    return trace.run(HOOK, "PreToolUse", body, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
