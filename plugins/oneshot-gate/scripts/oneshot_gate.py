#!/usr/bin/env python3
"""oneshot-gate: a `claude -p` that can use no tool never runs with the whole harness loaded.

THE CASE, 2026-11-02. A library harness graded 415 READMEs against three rules, twice, through
a judge that spawned `claude -p <prompt> --tools ""` from inside a repository. Every call was a
full session: the repository's CLAUDE.md, the user's, the memory index, the skills and the
SessionStart payload, about 55k tokens written to the cache to answer PASS or FAIL. 2270 calls
in five hours were 72 percent of the night's usage and spent two accounts' weekly windows. The
judge was fixed (trimwrit's make_judge now passes --safe-mode); this door holds the class, for
any library, script or language that spawns the CLI.

THE DOOR. A UserPromptSubmit hook. Hooks run only in a session that loaded the harness (`--bare`
and `--safe-mode` skip every hook), and this one runs before the first request, so refusing
here costs nothing: measured 2026-11-03, a refused `claude -p` reports 0 tokens, 0 turns and
0 ms of API time. It reads the argv of the claude process that started it (the nearest
ancestor named claude, through /proc where there is one, `ps` elsewhere) and refuses when that
argv is a print-mode session (`-p`, `--print`) with every built-in tool disabled (`--tools ""`):
a session that cannot act is a one-shot answer, and a one-shot answer needs no CLAUDE.md,
memory, skill or hook. The fix it names: `--safe-mode` (the OAuth login still works) or
`--bare` (needs ANTHROPIC_API_KEY or an apiKeyHelper).

NOT REFUSED: an interactive session, a print session that keeps any tool (an agent: a fixer, a
worker, a replay that declares tools), a session whose argv cannot be read (allowed, traced
`unreadable`, never a guess). A replay that measures a CLAUDE.md with no tool runs in a scratch
directory under `--setting-sources project,local`, where no user plugin loads.
"""
import json
import os
import subprocess
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_ROOT)

from gatekit import trace  # noqa: E402

HOOK = "oneshot-gate"
EVENT = "UserPromptSubmit"
TIMEOUT_S = 8
PRINT_FLAGS = ("-p", "--print")
LEAN_FLAGS = ("--bare", "--safe-mode")
# hook -> sh -> claude is two steps; a deeper walk would reach an unrelated outer session
MAX_DEPTH = 4
FIX = ("run it with --safe-mode (keeps the OAuth login) or --bare (needs ANTHROPIC_API_KEY or an "
       "apiKeyHelper): a call that can use no tool needs no CLAUDE.md, memory, skill or hook")


def is_claude(argv):
    """Is this argv the Claude Code CLI? `claude` by name or path, or node running its cli.js."""
    for a in argv[:2]:
        base = os.path.basename(a)
        if base == "claude" or a.endswith(("/claude-code/cli.js", "/claude-code/cli.mjs")):
            return True
    return False


def tools_disabled(argv):
    """`--tools ""` (or `--tools=`): every built-in tool off. An argument read back from `ps`
    loses the empty string, so a `--tools` followed by another flag or by nothing counts too."""
    for i, a in enumerate(argv):
        if a.startswith("--tools="):
            return a.split("=", 1)[1].strip(" \"'") == ""
        if a == "--tools":
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            return nxt is None or nxt.strip(" \"'") == "" or nxt.startswith("-")
    return False


def refusal(argv):
    """The reason to refuse this claude argv, or None."""
    if not argv or not is_claude(argv):
        return None
    flags = argv[1:]
    if any(f in LEAN_FLAGS for f in flags):
        return None
    if not any(f in PRINT_FLAGS for f in flags):
        return None
    if not tools_disabled(flags):
        return None
    return "a print-mode claude with every tool disabled loaded the whole harness"


def _proc(pid):
    """(argv, ppid) from /proc, or None where there is no /proc."""
    try:
        with open("/proc/{}/cmdline".format(pid), "rb") as fh:
            raw = fh.read()
        with open("/proc/{}/stat".format(pid), encoding="utf-8", errors="replace") as fh:
            ppid = int(fh.read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return [a.decode("utf-8", "replace") for a in raw.split(b"\0")[:-1]], ppid


def _ps(pid):
    """(argv, ppid) from ps: the argv is split on blanks, so an empty argument is lost."""
    try:
        out = subprocess.run(["ps", "-ww", "-o", "ppid=", "-o", "args=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        ppid, _, args = out.partition(" ")
        return args.split(), int(ppid)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def claude_argv(start=None):
    """The argv of the nearest claude ancestor of this process, or None."""
    pid = start or os.getppid()
    read = _proc if os.path.isdir("/proc/{}".format(pid)) else _ps
    for _ in range(MAX_DEPTH):
        got = read(pid)
        if got is None:
            return None
        argv, ppid = got
        if is_claude(argv):
            return argv
        if ppid <= 1:
            return None
        pid = ppid
    return None


def shown(argv):
    """The flags of an argv without the prompt text it may carry, for the ledger."""
    out, skip = [], False
    for a in argv[1:]:
        if skip:
            skip = False
            if not a.startswith("-"):
                continue
        if a in PRINT_FLAGS:
            out.append(a)
            skip = True
            continue
        out.append(a if a.startswith("-") or len(a) < 80 else a[:40] + "...")
    return out[:30]


def on_prompt(payload):
    argv = claude_argv()
    if argv is None:
        trace.trace(HOOK, EVENT, "unreadable", payload)
        return 0
    why = refusal(argv)
    if not why:
        return 0
    flags = shown(argv)
    trace.trace(HOOK, EVENT, "refused", payload, argv=" ".join(flags)[:400])
    trace.witness("oneshot_gate", EVENT, "oneshot_not_lean", payload)
    sys.stderr.write(json.dumps({"refused": "oneshot-not-lean", "why": why, "fix": FIX,
                                 "flags": flags}, ensure_ascii=False) + "\n")
    return 2


def main():
    trace.configure(sys.argv)

    def body():
        payload = trace.read_payload() if not sys.stdin.isatty() else {}
        return on_prompt(payload)

    return trace.run(HOOK, EVENT, body, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
