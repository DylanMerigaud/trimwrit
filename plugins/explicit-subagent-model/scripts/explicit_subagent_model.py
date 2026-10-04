#!/usr/bin/env python3
"""PreToolUse on Agent|Task: a subagent names its model, it never inherits one.

An Agent call with no `model` inherits the parent session's model, which is whatever /model
last set, possibly a cheap one nobody meant for that work. This gate refuses such a call with
exit 2 and the reason on stderr; the model reads it and calls again with a model. A fork is
exempt: the harness ignores `model` on a fork, it always runs the parent's. That exemption is
hard coded, never a configuration key.

A crash or a timeout lets the call through and is reported loudly; no environment variable and
no configuration key turns the gate off. The optional `guidance` key of the
`explicit-subagent-model` section of trimwrit-gates.json is appended to the refusal reason
(for instance which model fits which kind of work).
"""
import os
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_ROOT)
from gatekit import trace  # noqa: E402

HOOK = "agent-model-explicit.py"
TIMEOUT_S = 8
EXEMPT_TYPES = ("fork",)

REASON = ("This Agent call names no model, so the subagent would inherit the session's /model. "
          "Pass model explicitly.")


def verdict(payload, guidance=""):
    """None when the call may run, else the refusal text."""
    if payload.get("tool_name") not in ("Agent", "Task"):
        return None
    ti = payload.get("tool_input") or {}
    if str(ti.get("subagent_type") or "") in EXEMPT_TYPES:
        return None
    if str(ti.get("model") or "").strip():
        return None
    return REASON + (" " + guidance if guidance else "")


def body():
    payload = trace.read_payload()
    guidance = trace.plugin_settings(payload, root=PLUGIN_ROOT)["guidance"]
    reason = verdict(payload, guidance)
    if reason:
        sys.stderr.write(reason + "\n")
        return 2
    return 0


def main():
    trace.configure(sys.argv)
    return trace.run(HOOK, "PreToolUse", body, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
