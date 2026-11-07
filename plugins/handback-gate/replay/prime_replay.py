#!/usr/bin/env python3
"""Replay the situations a primed session was refused in, and count the first drafts the gate
would refuse, arm by arm.

Why: the prime is the producer-side half of the gate (what a refused session is told before its
next closing), and the only way to know a prime helps is to let the model draft a closing under
it and read that closing with the gate. 0.5.0 measured its prime this way by hand; 0.6.0 measures
the check the same way, and this file keeps the way so the next prime is measured, not believed.

Each situation (situations.json) is a cut, neutralised moment of a real session: the context a
compaction summary would hold, the user's message, and the session's refusal state at that
moment. Each arm is a handback_gate.py that renders that state the way its --prime would (or
`none`: no prime): a state whose `since` is past the arm's window gets the short prime, or nothing
in an arm older than 0.8.0. A situation never sees its own lesson: the shapes, rewrites and unread reasons it taught
(`holdout`) are removed from every arm's prime before it is rendered.

Each run is one `claude -p --safe-mode` call (no CLAUDE.md, plugin, hook or MCP of the machine;
the OAuth login kept), the user's global CLAUDE.md appended with --claude-md, Bash allowed for
python3 only so a prime that names the check can be followed. The final text is read by the gate
under test (--score, this plugin's script by default) with its default settings and the user's
name. A run is `refused` when the gate would block its final message, `checked` when it ran the
gate's --check.

    python3 prime_replay.py --arm old=/path/old/handback_gate.py --arm new=../scripts/handback_gate.py \
        --claude-md ~/.claude/CLAUDE.md --runs 3 --out /tmp/replay

A handful of runs per arm is a direction, not a rate.
"""
import argparse
import concurrent.futures
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "scripts", "handback_gate.py")
HELD = ("PRIME_SHAPES", "PRIME_REWRITES", "PRIME_NOT_READ")


def load_gate(path, name):
    """A handback_gate.py as a module, with its own plugin root first on sys.path."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(path)))
    sys.path.insert(0, root)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(root)
        for key in [k for k in sys.modules if k == "gatekit" or k.startswith("gatekit.")]:
            del sys.modules[key]
    return mod


def prime_for(gate, state, holdout):
    """The arm's prime for this state, the situation's own lesson removed. Pure on `gate`."""
    if gate is None:
        return ""
    kept = {k: getattr(gate, k) for k in HELD if hasattr(gate, k)}
    try:
        for key, value in kept.items():
            setattr(gate, key, tuple(v for v in value if v not in holdout))
        if int(state.get("since") or 0) <= getattr(gate, "PRIME_PROMPTS", 12):
            return gate.prime_text(state)
        # past the window: 0.8.0 keeps one line, an older arm fell silent
        return gate.prime_short(state) if hasattr(gate, "prime_short") else ""
    finally:
        for key, value in kept.items():
            setattr(gate, key, value)


def prompt_for(situation, prime):
    parts = ["<context>\n" + situation["context"] + "\n</context>"]
    if prime:
        parts.append("<system-reminder>\nUserPromptSubmit hook additional context: " + prime
                     + "\n</system-reminder>")
    parts.append("Dylan: " + situation["message"])
    parts.append("(Everything the context says is done is done. Write your final message to "
                 "Dylan for this turn, in his language.)")
    return "\n\n".join(parts)


def run_one(job, args, claude_md):
    cmd = ["claude", "-p", "--safe-mode", "--model", args.model, "--effort", args.effort,
           "--tools", "Bash", "--allowedTools", "Bash(python3:*)",
           "--output-format", "stream-json", "--verbose"]
    if claude_md:
        cmd += ["--append-system-prompt", claude_md]
    cmd.append(job["prompt"])
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with tempfile.TemporaryDirectory(prefix="prime-replay-") as cwd:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd,
                                 timeout=args.timeout)
            lines = out.stdout.splitlines()
        except subprocess.TimeoutExpired:
            lines = []
    result, checked, error = None, False, None
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use" and "--check" in json.dumps(block.get("input")):
                    checked = True
        if event.get("type") == "result":
            result = event.get("result")
            error = event.get("is_error")
    return dict(job, result=result, checked=checked, error=error or result is None)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arm", action="append", required=True,
                    help="NAME=PATH of a handback_gate.py, or NAME=none")
    ap.add_argument("--situations", default=os.path.join(HERE, "situations.json"))
    ap.add_argument("--only", action="append", help="a situation id (repeatable)")
    ap.add_argument("--score", default=SCRIPT)
    ap.add_argument("--claude-md", default=None)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    with open(args.situations, encoding="utf-8") as fh:
        situations = [s for s in json.load(fh) if not args.only or s["id"] in args.only]
    claude_md = None
    if args.claude_md:
        with open(os.path.expanduser(args.claude_md), encoding="utf-8") as fh:
            claude_md = fh.read()
    arms = {}
    for spec in args.arm:
        name, _, path = spec.partition("=")
        arms[name] = None if path == "none" else load_gate(path, "arm_" + name)
    scorer = load_gate(args.score, "scorer")
    cfg = {"user_names": ["Dylan"], "handback_patterns_extra": [], "reason_suffix": ""}

    jobs = []
    for s in situations:
        for name, gate in arms.items():
            prime = prime_for(gate, s["state"], set(s.get("holdout") or ()))
            for i in range(args.runs):
                jobs.append({"situation": s["id"], "arm": name, "run": i,
                             "prompt": prompt_for(s, prime)})
    os.makedirs(args.out, exist_ok=True)
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        for row in pool.map(lambda j: run_one(j, args, claude_md), jobs):
            found = [] if row["error"] else scorer.handbacks_in(row["result"], cfg)
            row["refused"] = bool(found)
            row["phrases"] = [[p, s] for p, _, s in found]
            rows.append(row)
            print(json.dumps({k: row[k] for k in ("situation", "arm", "run", "refused",
                                                  "checked", "error")}), flush=True)
    with open(os.path.join(args.out, "runs.jsonl"), "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("\nsituation / arm: refused of measured, checked")
    for s in situations:
        for name in arms:
            mine = [r for r in rows if r["situation"] == s["id"] and r["arm"] == name]
            done = [r for r in mine if not r["error"]]
            print("{} / {}: {} of {}, checked {} ({} errors)".format(
                s["id"], name, sum(r["refused"] for r in done), len(done),
                sum(r["checked"] for r in done), len(mine) - len(done)))
    for name in arms:
        done = [r for r in rows if r["arm"] == name and not r["error"]]
        print("ALL / {}: {} of {} refused, {} checked".format(
            name, sum(r["refused"] for r in done), len(done), sum(r["checked"] for r in done)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
