#!/usr/bin/env python3
"""SessionStart: print the configured routing card as additional context for the session.

The `route_card` key of the `explicit-subagent-model` section of trimwrit-gates.json holds a
short text (which model and effort fit which kind of work). When it is non-empty it goes out as
one SessionStart additionalContext object; when it is empty nothing is printed. A crash or a
timeout is reported loudly and the session starts anyway, the same contract as the gate.
"""
import json
import os
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_ROOT)
from gatekit import trace  # noqa: E402

HOOK = "route-card.py"
TIMEOUT_S = 8


def card_output(card):
    """The JSON line for a card, or None when there is nothing to say."""
    if not isinstance(card, str) or not card.strip():
        return None
    return json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                              "additionalContext": card}}, ensure_ascii=False)


def body():
    payload = trace.read_payload()
    out = card_output(trace.plugin_settings(payload, root=PLUGIN_ROOT)["route_card"])
    if out:
        print(out)
        sys.stdout.flush()
    return 0


def main():
    trace.configure(sys.argv)
    return trace.run(HOOK, "SessionStart", body, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
