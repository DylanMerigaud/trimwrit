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
(for instance which model fits which kind of work). The opt-in `require_effort` key makes a
missing or blank `effort` a refusal too (a fork stays exempt: the harness ignores effort on one). The opt-in
`require_subagent_type` key does the same for a missing or blank `subagent_type` (a fork names its
type by definition, so it never trips it).
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
EFFORT_REASON = ("This Agent call names no effort, so the subagent would run at whatever effort "
                 "its model defaults to. Pass effort explicitly (low, medium, high, xhigh, max).")


TYPE_REASON = ("This Agent call names no subagent_type, so it would run as general-purpose with "
               "every CLAUDE.md loaded (about 50 to 60k tokens before its first word). Pass "
               "subagent_type explicitly: lean when the brief carries what the agent needs, "
               "general-purpose only when it must apply a repository's whole doctrine.")


def verdict(payload, guidance="", require_effort=False, require_subagent_type=False):
    """None when the call may run, else the refusal text. A missing model gives REASON, a
    missing effort (only when `require_effort`) gives EFFORT_REASON, several give several, model,
    effort, subagent_type in that order; `guidance` closes the text once."""
    if payload.get("tool_name") not in ("Agent", "Task"):
        return None
    ti = payload.get("tool_input") or {}
    if str(ti.get("subagent_type") or "") in EXEMPT_TYPES:
        return None
    reasons = []
    if not str(ti.get("model") or "").strip():
        reasons.append(REASON)
    if require_effort and not str(ti.get("effort") or "").strip():
        reasons.append(EFFORT_REASON)
    if require_subagent_type and not str(ti.get("subagent_type") or "").strip():
        reasons.append(TYPE_REASON)
    if not reasons:
        return None
    if guidance:
        reasons.append(guidance)
    return " ".join(reasons)


def body():
    payload = trace.read_payload()
    settings = trace.plugin_settings(payload, root=PLUGIN_ROOT)
    reason = verdict(payload, settings["guidance"], settings["require_effort"],
                     settings["require_subagent_type"])
    if reason:
        sys.stderr.write(reason + "\n")
        return 2
    return 0


def main():
    trace.configure(sys.argv)
    return trace.run(HOOK, "PreToolUse", body, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
