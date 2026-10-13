#!/usr/bin/env python3
"""heredoc_file.py: PreToolUse(Bash) door that moves a quoted heredoc body into a file.

WHY. A session or subagent isolated in a git worktree has a shell guard inside Claude Code that
refuses any command it cannot prove stays inside the worktree: "this command is too complex to
verify that it stays inside the worktree". Measured on Dylan's transcripts (2026-09-02 to
2026-10-13, 2631 refusals in 1229 sessions), 61 percent of the refusals were a command carrying a
quoted heredoc, nearly always a `python3 - <<'EOF'` script. The guard's parser trips on the body
(`x = {"k": len("T3")}` is enough) although a quoted heredoc body is inert text that no shell
expands. Each refusal costs the model a turn and a rewrite.

WHAT. In a linked worktree, for a Bash command whose heredocs are all quoted (`<<'EOF'`,
`<<"EOF"`, `<<\\EOF`, with or without `-`), the body is written to a file and the operator becomes
a plain redirect from that file:

    python3 - <<'EOF'            python3 - < /private/tmp/claude-501/trimwrit-heredoc/ab12cd34.txt
    print(1)               ->
    EOF

A quoted heredoc is stdin read from a file the shell never expands, so the rewrite does not
change what runs. The guard accepts a file redirect.

WHAT IT NEVER DOES. It never touches an unquoted heredoc (its body is expanded, a file would not
be), a heredoc nested in a command substitution, a subshell or backticks, a command it cannot
scan to the end (an open quote, an unterminated body), or anything outside a linked worktree.
Whatever it is not sure of, it leaves alone: the guard then decides as before. The guard is not
bypassed: a command that names git keeps being judged, only the inert text is moved out of it.

NO BYPASS. No environment variable and no marker file turns it off (disable the plugin). A crash
is loud through gatekit's trace and lets the command through unchanged. `--home DIR` is the
tests' fixture knob, an argument no hooks.json passes.
"""
import hashlib
import json
import os
import re
import sys
import time

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_ROOT)
from gatekit import trace  # noqa: E402

HOOK = "heredoc-file.py"
TIMEOUT_S = 5
KEEP_DAYS = 2
DELIM = re.compile(r"[A-Za-z0-9_.-]+")


class Bail(Exception):
    """The command is not one this door is sure of: leave it as it is."""


def _skip_single(text, i):
    j = text.find("'", i + 1)
    if j < 0:
        raise Bail("open single quote")
    return j + 1


def _skip_double(text, i):
    n = len(text)
    j = i + 1
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
        elif c == '"':
            return j + 1
        elif c == "`" or (c == "$" and text.startswith("$(", j)):
            raise Bail("substitution inside double quotes")
        else:
            j += 1
    raise Bail("open double quote")


def _operator(text, i):
    """Parse `<<`, `<<-` and the delimiter word at i. Returns (end, delimiter, strip_tabs)."""
    j = i + 2
    strip = text.startswith("-", j)
    if strip:
        j += 1
    while j < len(text) and text[j] in " \t":
        j += 1
    if j >= len(text):
        raise Bail("heredoc operator with no delimiter")
    c = text[j]
    if c in "'\"":
        m = DELIM.match(text, j + 1)
        if not m or text[m.end():m.end() + 1] != c:
            raise Bail("quoted delimiter this door does not read")
        return m.end() + 1, m.group(0), strip
    if c == "\\":
        m = DELIM.match(text, j + 1)
        if not m:
            raise Bail("escaped delimiter this door does not read")
        return m.end(), m.group(0), strip
    raise Bail("unquoted heredoc: its body is expanded")


def _bodies(text, pos, pending):
    """Read the bodies of the heredocs opened on the line that ends at pos (the newline).
    Returns (position after the last terminator line, [body, ...])."""
    bodies = []
    i = pos + 1
    for _idx, delim, strip in pending:
        lines = []
        while True:
            if i > len(text):
                raise Bail("unterminated heredoc")
            j = text.find("\n", i)
            end = len(text) if j < 0 else j
            line = text[i:end]
            if (line.lstrip("\t") if strip else line) == delim:
                i = end + 1 if j >= 0 else len(text) + 1
                break
            lines.append(line.lstrip("\t") if strip else line)
            if j < 0:
                raise Bail("unterminated heredoc")
            i = j + 1
        bodies.append("".join(line + "\n" for line in lines))
    return min(i, len(text)), bodies


def extract(command):
    """Return (pieces, bodies): `pieces` is the command split around each heredoc operator (the
    operator slots hold None), `bodies` the text for each slot in order. Raises Bail."""
    n = len(command)
    out = []
    pending = []
    bodies = []
    depth = 0
    start = 0   # start of the plain text not yet appended to `out`
    i = 0
    while i < n:
        c = command[i]
        if c == "\\":
            i += 2
        elif c == "'":
            i = _skip_single(command, i)
        elif c == '"':
            i = _skip_double(command, i)
        elif c == "`":
            raise Bail("backticks")
        elif c == "$" and command.startswith(("$'", '$"'), i):
            raise Bail("ANSI-C or locale quoting")
        elif c == "(":
            depth += 1
            i += 1
        elif c == ")":
            depth -= 1
            if depth < 0:
                raise Bail("unbalanced parenthesis")
            i += 1
        elif c == "#" and (i == 0 or command[i - 1] in " \t\n;&|("):
            j = command.find("\n", i)
            i = n if j < 0 else j
        elif command.startswith("<<<", i):
            i += 3
        elif command.startswith("<<", i):
            if depth:
                raise Bail("heredoc inside a substitution or subshell")
            end, delim, strip = _operator(command, i)
            out.append(command[start:i])
            out.append(None)
            pending.append((len(out) - 1, delim, strip))
            start = i = end
        elif c == "\n" and pending:
            if depth:
                raise Bail("heredoc inside a substitution or subshell")
            out.append(command[start:i + 1])
            i, got = _bodies(command, i, pending)
            bodies.extend(got)
            pending = []
            start = i
        else:
            i += 1
    if pending:
        raise Bail("heredoc operator on the last line")
    if depth:
        raise Bail("unbalanced parenthesis")
    out.append(command[start:])
    return out, bodies


def store_dir():
    if trace.home():
        return os.path.join(trace.home(), "heredoc")
    uid = os.getuid()
    root = "/private/tmp/claude-{}".format(uid)
    if not os.path.isdir(root):
        import tempfile
        root = tempfile.gettempdir()
    return os.path.join(root, "trimwrit-heredoc")


def sweep(directory):
    cutoff = time.time() - KEEP_DAYS * 86400
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        path = os.path.join(directory, name)
        try:
            if name.endswith(".txt") and os.path.getmtime(path) < cutoff:
                os.unlink(path)
        except OSError:
            pass


def write_body(directory, body):
    """The body in a file named by its hash (same text, same file), written atomically."""
    os.makedirs(directory, mode=0o700, exist_ok=True)
    name = hashlib.sha256(body.encode("utf-8")).hexdigest()[:20] + ".txt"
    path = os.path.join(directory, name)
    if not os.path.isfile(path):
        tmp = "{}.{}.tmp".format(path, os.getpid())
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(body)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    return path


def _quote(path):
    return "'" + path.replace("'", "'\\''") + "'"


def in_linked_worktree(cwd):
    """True when cwd sits in a linked worktree: a `.git` FILE on the way up, or a path under
    `.claude/worktrees/`. The guard this door works around only exists there."""
    if not cwd:
        return False
    real = os.path.realpath(cwd)
    if os.sep + os.path.join(".claude", "worktrees") + os.sep in real + os.sep:
        return True
    cur = real
    while True:
        git = os.path.join(cur, ".git")
        if os.path.isfile(git):
            return True
        if os.path.exists(git):
            return False
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def rewrite(command, directory):
    """The command with every heredoc moved to a file, or None when nothing should change."""
    if "<<" not in command:
        return None
    pieces, bodies = extract(command)
    if not bodies:
        return None
    paths = [write_body(directory, b) for b in bodies]
    it = iter(paths)
    return "".join("< " + _quote(next(it)) if p is None else p for p in pieces)


def body():
    payload = trace.read_payload()
    if payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command")
    if not isinstance(command, str) or "<<" not in command:
        return 0
    if not in_linked_worktree(payload.get("cwd")):
        return 0
    directory = store_dir()
    try:
        new = rewrite(command, directory)
    except Bail as why:
        trace.trace(HOOK, "PreToolUse", "left-alone", payload, why=str(why))
        return 0
    if new is None or new == command:
        return 0
    sweep(directory)
    trace.trace(HOOK, "PreToolUse", "rewrote", payload, heredocs=command.count("<<"))
    updated = dict(tool_input)
    updated["command"] = new
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                        "updatedInput": updated}}))
    return 0


def main():
    trace.configure(sys.argv)
    return trace.run(HOOK, "PreToolUse", body, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
