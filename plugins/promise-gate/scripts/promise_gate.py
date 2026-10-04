#!/usr/bin/env python3
"""Stop hook: a turn does not end on a promise to continue once the user set a target.

Why a hook and not a sentence in a CLAUDE.md: six turns in a row of one night ended on "je
continue sur le lot suivant" and stopped, and the user had to write "ne t'arrete pas" three
times. The rule ("a turn never ends on a sentence naming work still to do") scored the same with
and without it on its eval case, because a one-prompt replay cannot reproduce a turn that ENDS:
the failure only exists at the boundary of a turn, and the only thing that runs there is a Stop
hook.

What arms it: only a message from the user that sets a target ("ne t'arrete pas", "never stop",
"until results", "jusqu'a N candidats", "fait tout", "tout le pool d'un coup", "do not stop").
The UserPromptSubmit entry (`--prompt`) reads the prompt and writes a per-session marker carrying
an excerpt. An unarmed session never hears from this hook: a recap that says "next batch
tomorrow" after the user said stop is legitimate, and a guard that fires there gets uninstalled.

What it checks: the last TAIL_CHARS of the final message (`last_assistant_message` from the
payload, the transcript only when it lacks one) against the promise patterns ("je continue", "je
passe au lot suivant", "next batch", "I'll pick it up", "running in the background, will land").
A match while armed blocks the stop, with a reason naming the target.

The bounds: the re-answer of a stop chain is checked like the first answer; the gate lets go after
CHAIN_CAP blocks in a row (a `cap` row in the ledger and a systemMessage). A crash or a timeout
lets the turn end and is reported loudly. No environment variable and no configuration key
turns the gate off. A SubagentStop is another event and is ignored.

Configuration (section `promise-gate` of trimwrit-gates.json): `target_patterns_extra` and
`promise_patterns_extra` add regex alternatives (compiled case-insensitive and verbose, so a
space inside an extra is written `\\s`), `reason_suffix` is appended to the refusal.

Outputs:
    exit 0 with nothing on stdout                       the session ends normally
    exit 0 with {"decision": "block", "reason": ...}    the session continues with this message

Utilities:
    python3 promise_gate.py --print        what it would say now, blocking nothing
    python3 promise_gate.py --arm "text"   arm this session with a target excerpt
    python3 promise_gate.py --disarm       disarm this session
    python3 promise_gate.py --prompt       the UserPromptSubmit hook, JSON on stdin
"""
import json
import os
import re
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_ROOT)
from gatekit import config, trace, transcript  # noqa: E402

HOOK = "stop_promise_gate.py"
TIMEOUT_S = 8
TAIL_CHARS = 700
ARM_SUFFIX = ".promise"
EXCERPT_CHARS = 160

# A target set by the user: the message that makes stopping wrong.
TARGET_SRC = r"""
    (?:ne\s+)?t'?\s?arr[eê]te[sz]?\s+pas          # "ne t'arrete pas", "t'arrete pas", "T'ARRETE PAS"
  | never\s+stop
  | (?:don'?t|do\s+not)\s+stop
  | untill?\s+results
  | jusqu'?[aà]\s+(?:ce\s+que|avoir|\d)           # "jusqu'a avoir 5", "jusqu'a ce que"
  | fai[st]\s+tout\b
  | tout\s+le\s+pool
  | d'un\s+coup
  | go\s+autonome
"""

# The promise, kept in sync by hand with the forbid grader of the eval case.
PROMISE_SRC = r"""
    je\s+continue
  | je\s+passe\s+au\s+(?:lot|batch|prochain)
  | prochain\s+lot
  | next\s+batch
  | I(?:'ll|\s+will)\s+(?:continue|keep\s+going|pick\s+(?:it\s+)?up)
  | to\s+be\s+continued
  | je\s+reprends
  | running\s+in\s+(?:the\s+)?background
  | will\s+land
  | not\s+stopping\s+here
  | I(?:'ll|\s+will)\s+(?:report|follow\s+up)\s+(?:back|later)
"""

FLAGS = re.I | re.X
_COMPILED = {}


def _alternatives(where, value):
    if not isinstance(value, list) or not all(isinstance(a, str) for a in value):
        raise config.ConfigError("promise-gate: {} must be a list of regex strings".format(where))
    return value


def patterns(cfg):
    """(target, promise) compiled with the configured extras appended as alternatives. An extra
    that does not compile, or is not a list of strings, is a config.ConfigError."""
    key = json.dumps([cfg.get("target_patterns_extra"), cfg.get("promise_patterns_extra")])
    if key not in _COMPILED:
        out = []
        for where, base in (("target_patterns_extra", TARGET_SRC),
                            ("promise_patterns_extra", PROMISE_SRC)):
            extras = _alternatives(where, cfg.get(where) or [])
            src = base.rstrip() + "".join("\n  | (?:{}\n)".format(a) for a in extras)
            try:
                out.append(re.compile(src, FLAGS))
            except re.error as e:
                raise config.ConfigError("promise-gate {}: {}".format(where, e))
        _COMPILED[key] = tuple(out)
    return _COMPILED[key]


def _settings(cfg):
    """The configuration of a caller outside a hook: the project layer of its cwd and the user
    layer, read from this plugin's own defaults."""
    if cfg is not None:
        return cfg
    return trace.plugin_settings({"cwd": os.getcwd()}, root=PLUGIN_ROOT)


def arm_path(session_id):
    return trace.session_file(session_id, "") + ARM_SUFFIX


def armed_excerpt(session_id):
    try:
        with open(arm_path(session_id), encoding="utf-8") as fh:
            return fh.read().strip() or "(target set)"
    except OSError:
        return None


def set_armed(session_id, excerpt):
    path = arm_path(session_id)
    if excerpt is None:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(excerpt[:EXCERPT_CHARS])


def target_in(prompt, cfg=None):
    return bool(patterns(_settings(cfg))[0].search(prompt or ""))


def promise_in(text, cfg=None):
    """The promise to continue ending `text` (its last TAIL_CHARS), or None."""
    m = patterns(_settings(cfg))[1].search((text or "")[-TAIL_CHARS:])
    return m.group(0) if m else None


def reason_text(promise, excerpt, cfg):
    text = ("The turn ends on a promise to continue (\"{}\") while the user set a target: \"{}\". "
            "Do the next batch now, in this turn, until the target is reached or the pool is "
            "exhausted; a batch report is a line inside the running turn, never a stop. If the "
            "context runs short, hand the remaining work to a subagent before ending. "
            "(promise-gate)".format(promise, excerpt))
    suffix = cfg.get("reason_suffix")
    return text + " " + suffix if suffix else text


def on_prompt():
    payload = trace.read_payload()
    cfg = trace.plugin_settings(payload, root=PLUGIN_ROOT)
    prompt = payload.get("prompt") or ""
    if target_in(prompt, cfg):
        set_armed(trace.session_key(payload), " ".join(prompt.split()))
    return 0


def on_stop():
    payload = trace.read_payload()
    if payload.get("hook_event_name") != "Stop":
        return 0
    cfg = trace.plugin_settings(payload, root=PLUGIN_ROOT)
    patterns(cfg)  # a bad extra is a loud crash even on an unarmed session
    excerpt = armed_excerpt(trace.session_key(payload))
    if not excerpt:
        return 0
    promise = promise_in(transcript.final_text(payload), cfg)
    if not promise:
        trace.chain_clear(HOOK, payload)
        return 0
    if not trace.chain_should_block(HOOK, payload, "promise: " + promise):
        print(trace.cap_message(HOOK, "promise: " + promise))
        return 0
    trace.witness("stop_promise_gate", "Stop", "promise_ending", payload)
    print(json.dumps({"decision": "block", "reason": reason_text(promise, excerpt, cfg)},
                     ensure_ascii=False))
    return 0


def main():
    trace.configure(sys.argv)
    if "--prompt" in sys.argv:
        return trace.run(HOOK, "UserPromptSubmit", on_prompt, TIMEOUT_S)
    if "--arm" in sys.argv:
        i = sys.argv.index("--arm")
        excerpt = sys.argv[i + 1] if i + 1 < len(sys.argv) else "(target set by hand)"
        set_armed(trace.session_key({}), excerpt)
        print("promise gate ARMED for this session: it will refuse a turn ending on a promise.")
        return 0
    if "--disarm" in sys.argv:
        set_armed(trace.session_key({}), None)
        print("promise gate DISARMED for this session.")
        return 0
    if "--print" in sys.argv:
        sid = trace.session_key({})
        excerpt = armed_excerpt(sid)
        print("session {}: {}".format(sid or "(unknown)",
                                      "ARMED on \"{}\"".format(excerpt) if excerpt
                                      else "not armed, silent"))
        return 0
    return trace.run(HOOK, "Stop", on_stop, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
