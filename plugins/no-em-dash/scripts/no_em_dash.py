#!/usr/bin/env python3
"""Stop hook: refuse to end the turn when the final message carries a forbidden dash.

Why a Stop hook and not a line in a CLAUDE.md: the rule lived in prose and was lost to a
reading, never to disagreement. Prose can be scoped, qualified and argued with; a string search
cannot. Why `decision` is not in `hookSpecificOutput`: for Stop, Claude Code reads the block at
the top level or from exit code 2, and an unrecognized key is dropped without an error, so a
block written there announced itself and held nothing. This hook exits 2 with the reason on
stderr, and checks the re-answers of a stop chain like the first one, up to the chain cap.

It checks the final assistant message the Stop payload hands over, not the transcript, which
lags the current turn. Anything inside a fenced block or inline backticks is exempt: quoting a
diff or a tool output is not the failure this gate polices, and to talk about the character you
put it in backticks. That is the rule's own exemption, not a bypass.

A crash or a timeout lets the turn end and is reported loudly; no environment variable and no
configuration key turns the gate off. The refused characters are the `codepoints` key of the
`no-em-dash` section of trimwrit-gates.json.
"""
import os
import re
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_ROOT)
from gatekit import trace  # noqa: E402

HOOK = "no-em-dash.py"
TIMEOUT_S = 8

# Names for the refusal text, as codepoints so this file stays clean under a grep for the
# characters it refuses. A configured codepoint with no name here is reported as U+XXXX.
BAD = {0x2014: "em-dash", 0x2013: "en-dash", 0x2012: "figure dash", 0x2015: "horizontal bar"}

FENCED = re.compile(r"^[ \t]*```.*?^[ \t]*```", re.MULTILINE | re.DOTALL)
INLINE = re.compile(r"`[^`\n]*`")


def offenders(text, bad):
    """[(name, context)] for each forbidden dash in prose. Context so it can be found fast."""
    prose = INLINE.sub("", FENCED.sub("", text))
    found = []
    for i, ch in enumerate(prose):
        if ord(ch) not in bad:
            continue
        name = BAD.get(ord(ch), "U+{:04X}".format(ord(ch)))
        snippet = prose[max(0, i - 40):i + 40].replace("\n", " ")
        found.append((name, snippet.strip()))
    return found


def reason_text(found):
    lines = ["{} occurrence(s) of a forbidden dash in the message you were about to send. "
             "Rewrite those sentences and answer again.".format(len(found))]
    for name, snippet in found[:6]:
        lines.append("  {}: ...{}...".format(name, snippet))
    if len(found) > 6:
        lines.append("  and {} more.".format(len(found) - 6))
    lines.append("")
    lines.append("Use a comma, a colon, parentheses, or a new sentence. The ASCII hyphen is "
                 "fine. To talk about the character itself, put it in backticks: code spans "
                 "and fenced blocks are not checked.")
    return "\n".join(lines)


def body():
    payload = trace.read_payload()
    bad = set(trace.plugin_settings(payload, root=PLUGIN_ROOT)["codepoints"])
    message = payload.get("last_assistant_message")
    found = offenders(message, bad) if isinstance(message, str) and message else []
    if not found:
        trace.chain_clear(HOOK, payload)
        return 0
    detail = "{} dash(es): {}".format(len(found), found[0][1])
    if not trace.chain_should_block(HOOK, payload, detail):
        print(trace.cap_message(HOOK, detail))
        return 0
    trace.witness("no-em-dash", "Stop", "forbidden_dash", payload)
    sys.stderr.write(reason_text(found) + "\n")
    return 2


def main():
    trace.configure(sys.argv)
    return trace.run(HOOK, "Stop", body, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
