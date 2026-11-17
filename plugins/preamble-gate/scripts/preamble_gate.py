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

THE SHELL DOOR (0.2.0). The checks above only see the Edit tool, and most memory lines never
went through it: on 2026-11-15 and again on 2026-11-17 growth-cockpit's memory index went over
its 4000 token budget through `cat >> $M/MEMORY.md <<'EOF'`, a write no hook measured. So:
  PreToolUse Bash   a command that writes an always-loaded file with a budget (a redirection
                    `>` `>>` `&>`, `tee`, an in-place `sed -i` or `perl -i`, the destination of
                    `cp` or `mv`) is refused and routed to the Edit tool, which measures. A path
                    the hook cannot resolve (a variable it cannot see) is matched by its name:
                    MEMORY.md, CLAUDE.md, a .md under .claude/rules. Plain `NAME=value`
                    assignments, $HOME and `cd` earlier in the same command are followed. Before an
                    allowed command it records the stat of every always-loaded file.
  PostToolUse Bash  every always-loaded file whose stat changed during the command (a program
                    that wrote it without naming it on the command line) and now stands over its
                    budget is reported to the model (decision block on PostToolUse: the command
                    already ran, the reason goes back to the model). No snapshot (the Pre hook did
                    not run): a file over budget modified in the last FRESH_S seconds is reported,
                    and the message says there was no snapshot.
What stays open: a program that writes the file without naming it is reported after the write,
never refused before it.

Every deny is counted, every error is loud: a crash or a timeout lets the edit through and is
reported. No environment variable and no configuration key turns this gate off.
"""
import glob
import hashlib
import json
import os
import re
import shlex
import sys
import time

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
FRESH_S = 120
SNAP_SUFFIX = ".preamble-snap"
SNAP_TTL_S = 86400

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


# --- the shell door ----------------------------------------------------------------------------

_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][\w]*)\1")
_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "|&", ";;"}
_REDIRECTS = {">", ">>", ">|", "&>", "&>>", ">&"}
_ASSIGN = re.compile(r"^([A-Za-z_]\w*)=(.*)$", re.S)
_WRAPPERS = {"sudo", "env", "command", "nohup", "time", "exec"}


def _strip_heredocs(command):
    """The command without its here-document bodies, each logical line ended by ' ; '."""
    out, waiting = [], []
    for line in command.split("\n"):
        if waiting:
            if line.strip() == waiting[0]:
                waiting.pop(0)
            continue
        waiting = [m.group(2) for m in _HEREDOC.finditer(line)]
        out.append(line[:-1] + " " if line.endswith("\\") else line + " ; ")
    return "".join(out)


def _tokens(command):
    text = _strip_heredocs(command)
    try:
        lex = shlex.shlex(text, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        return list(lex)
    except ValueError:
        return text.split()


def _segments(tokens):
    """[(words, [(redirect operator, target)])] per simple command."""
    segs, words, redirs, i = [], [], [], 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _SEPARATORS:
            segs.append((words, redirs))
            words, redirs = [], []
        elif tok in _REDIRECTS or tok in ("<", "<<", "<<<", "<>"):
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            if tok in _REDIRECTS and not (tok == ">&" and nxt.isdigit()) and nxt != "-":
                redirs.append((tok, nxt))
            i += 1
        else:
            words.append(tok)
        i += 1
    segs.append((words, redirs))
    return segs


def _short_flag(words, letter):
    return any(w.startswith("-") and not w.startswith("--") and letter in w[1:].split("=")[0]
               for w in words)


def write_targets(command, cwd):
    """[(how, raw target, cwd at that point, variables at that point)] of every file `command`
    writes by redirection, tee, an in-place edit, or as the destination of cp or mv."""
    found, env, here = [], {}, cwd
    for words, redirs in _segments(_tokens(command)):
        while words and _ASSIGN.match(words[0]):
            name, value = _ASSIGN.match(words[0]).groups()
            env[name] = _expand_vars(value, env)
            words = words[1:]
        while words and os.path.basename(words[0]) in _WRAPPERS:
            words = words[1:]
        for op, target in redirs:
            found.append(("redirection " + op, target, here, dict(env)))
        if not words:
            continue
        cmd, args = os.path.basename(words[0]), words[1:]
        plain = [a for a in args if not a.startswith("-")]
        if cmd == "cd":
            dest = _resolve(plain[0], here, env) if plain else os.path.expanduser("~")
            if dest:
                here = dest
        elif cmd == "tee":
            found += [("tee", a, here, dict(env)) for a in plain]
        elif cmd == "sed" and (_short_flag(args, "i") or "--in-place" in args):
            found += [("sed -i", a, here, dict(env)) for a in plain]
        elif cmd == "perl" and _short_flag(args, "i"):
            found += [("perl -i", a, here, dict(env)) for a in plain]
        elif cmd in ("cp", "mv", "install") and len(plain) >= 2:
            found.append((cmd, plain[-1], here, dict(env)))
    return found


def _expand_vars(text, env):
    def sub(m):
        name = m.group(1) or m.group(2)
        if name in env:
            return env[name]
        # A gate reads only its declared environment: $HOME is the one outside name it expands.
        return os.path.expanduser("~") if name == "HOME" else m.group(0)
    return re.sub(r"\$\{(\w+)\}|\$(\w+)", sub, text)


def _resolve(raw, cwd, env):
    """The absolute path `raw` names, or None when a variable or a substitution stays opaque."""
    if not raw:
        return None
    text = _expand_vars(raw, env)
    if text.startswith("~"):
        text = os.path.expanduser(text)
    if "$" in text or "`" in text:
        return None
    if not os.path.isabs(text):
        text = os.path.join(cwd or os.getcwd(), text)
    return os.path.realpath(text)


def _named_like(raw):
    """A target the hook cannot resolve, matched by its name."""
    base = os.path.basename(raw.rstrip("\"'"))
    return base in (MEMORY, PROJECT) or bool(re.search(r"\.claude/rules/[^/]*\.md$", raw))


def shell_refusal(payload, udir, budgets):
    """The deny reason for a Bash command that writes an always-loaded file, or None."""
    command = (payload.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return None
    cwd = payload.get("cwd") or os.getcwd()
    for how, raw, here, env in write_targets(command, cwd):
        path = _resolve(raw, here, env)
        if path is None:
            if not _named_like(raw):
                continue
            what = "{} (a path the hook cannot resolve, matched by its name)".format(raw)
        else:
            found = piece(path, udir)
            limit = found and budget(path, found[0], found[1], payload, udir, budgets)
            if not limit:
                continue
            current = _read(path) or ""
            before = measure(path, found[1], current, current, udir)[0]
            what = "{} ({} tokens now, budget {})".format(path, before, limit[1])
        return ("preamble-gate: this command writes {} through the shell ({}), and that file "
                "loads every turn. A shell write is measured by no one before it lands. Make "
                "the change with the Edit or Write tool, which measures it against the budget; "
                "a shrinking edit always passes. {}".format(what, how, ROUTE))
    return None


def candidates(payload, udir):
    """Every always-loaded file this session can reach: the user CLAUDE.md, the rules, each
    project's memory index, and every CLAUDE.md from the cwd and the project dir up."""
    out = {os.path.join(udir, PROJECT)}
    out.update(glob.glob(os.path.join(udir, "rules", "*.md")))
    out.update(glob.glob(os.path.join(udir, "projects", "*", "memory", MEMORY)))
    for start in (payload.get("cwd"), os.environ.get(ENV_PROJECT)):
        here = os.path.realpath(start) if start else None
        for _ in range(MAX_UP if here else 0):
            out.add(os.path.join(here, PROJECT))
            parent = os.path.dirname(here)
            if parent == here:
                break
            here = parent
    return sorted(os.path.realpath(p) for p in out)


def snapshot(paths):
    snap = {}
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        snap[p] = [st.st_mtime_ns, st.st_size]
    return snap


def snap_path(payload):
    key = payload.get("tool_use_id") or hashlib.sha256(
        json.dumps(payload.get("tool_input"), sort_keys=True).encode("utf-8")).hexdigest()
    sid = trace.session_key(payload) + ":" + str(key)
    return trace.session_file(sid, SNAP_SUFFIX)


def _prune_snapshots():
    cutoff = time.time() - SNAP_TTL_S
    for old in glob.glob(os.path.join(trace.state_dir(), "*" + SNAP_SUFFIX)):
        try:
            if os.path.getmtime(old) < cutoff:
                os.remove(old)
        except OSError:
            continue    # another hook removed it first: nothing left to prune


def shell_pre(payload, udir, budgets):
    reason = shell_refusal(payload, udir, budgets)
    if reason:
        trace.witness(DOOR, "PreToolUse", "shell_write", payload)
        json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                          "permissionDecision": "deny",
                                          "permissionDecisionReason": reason}}, sys.stdout)
        return 0
    _prune_snapshots()
    with open(snap_path(payload), "w", encoding="utf-8") as fh:
        json.dump(snapshot(candidates(payload, udir)), fh)
    return 0


def shell_post(payload, udir, budgets):
    path = snap_path(payload)
    try:
        with open(path, encoding="utf-8") as fh:
            before = json.load(fh)
        os.remove(path)
    except FileNotFoundError:
        before = None
    now = snapshot(candidates(payload, udir))
    if before is None:
        fresh = time.time() - FRESH_S
        changed = [p for p, (mtime_ns, _) in now.items() if mtime_ns / 1e9 >= fresh]
    else:
        changed = [p for p, st in now.items() if before.get(p) != st]
    over = []
    for p in changed:
        found = piece(p, udir)
        limit = found and budget(p, found[0], found[1], payload, udir, budgets)
        if not limit:
            continue
        text = _read(p) or ""
        size = measure(p, found[1], text, text, udir)[1]
        if size > limit[1]:
            over.append("{} is {} tokens, budget {} (key {} in {})".format(
                p, size, limit[1], limit[0], limit[2]))
    if not over:
        return 0
    how = ("this command changed" if before is not None else
           "no snapshot was taken before this command, so these were modified in the last {} s "
           "and".format(FRESH_S))
    reason = ("preamble-gate: {} an always-loaded file that now stands over its budget: {}. "
              "It loads every turn. Bring it back under with the Edit tool (a shrinking edit "
              "always passes). {}".format(how, "; ".join(over), ROUTE))
    trace.witness(DOOR, "PostToolUse", "over_after_shell", payload)
    json.dump({"decision": "block", "reason": reason,
               "hookSpecificOutput": {"hookEventName": "PostToolUse",
                                      "additionalContext": reason}}, sys.stdout)
    return 0


def body():
    payload = trace.read_payload()
    budgets = user_budgets(trace.plugin_settings(payload, root=PLUGIN_ROOT))
    if payload.get("tool_name") == "Bash":
        udir = user_dir()
        if payload.get("hook_event_name") == "PostToolUse":
            return shell_post(payload, udir, budgets)
        return shell_pre(payload, udir, budgets)
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
    return trace.run(HOOK, "PreToolUse|PostToolUse", body, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
