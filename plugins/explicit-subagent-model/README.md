# explicit-subagent-model

Refuses an Agent or Task call that names no model (and, opt-in, no effort), so a subagent never
silently inherits the session's tier. Optionally gives every session a short routing card at its
start.

- **PreToolUse hook** (`explicit_subagent_model.py`, matcher `Agent|Task`): a call whose `model`
  is missing or blank is refused with exit 2 and a reason on stderr; the model reads it and calls
  again with a model. With `require_effort` on, a missing or blank `effort` is refused the same
  way, and a call missing both gets both sentences, model first. A `fork` is exempt, because the
  harness ignores `model` and `effort` on a fork. That is the one exemption and it is not
  configurable.
- **SessionStart hook** (`route_card.py`, matcher `startup|clear|compact`): when `route_card` is
  non-empty, prints it as one `additionalContext` object so the main session starts knowing which
  model and effort fit which work. Empty, it prints nothing.

A crash or a timeout lets the call through and says so in a systemMessage. There is no switch:
enable or disable the plugin with `claude plugin enable|disable`.

## Install

```
claude plugin marketplace add DylanMerigaud/trimwrit
claude plugin install explicit-subagent-model@trimwrit
```

## Configuration

Section `explicit-subagent-model` of `~/.claude/trimwrit-gates.json` (user) and
`<project>/.claude/trimwrit-gates.json` (project, wins key by key). An unknown key is a loud
crash, not a silent pass.

| key | default | meaning |
|---|---|---|
| `guidance` | `""` | text appended once at the end of the refusal reason, e.g. which model fits which work |
| `require_effort` | `false` | when `true`, an Agent or Task call (a `fork` excepted) with a missing or blank `effort` is refused too |
| `route_card` | `""` | text printed as SessionStart `additionalContext` on `startup`, `clear` and `compact`; empty prints nothing |

## Where the ledgers land

Crashes, timeouts and, for a Stop gate, chain caps are appended to `hook-health.jsonl`; every
refusal is counted in `door-refusals.jsonl` next to it. By default both sit in the plugin's data
directory (`$CLAUDE_PLUGIN_DATA`, which Claude Code keeps at `~/.claude/plugins/data/explicit-subagent-model-trimwrit/`),
or in `~/.claude/trimwrit-gates/` when that variable is not set. The `trace` section of
`trimwrit-gates.json` moves them: `ledger` is the path of the health file (the refusal file stays
beside it), and `witness_module` is a Python file exposing `log(door, event, reason_class,
session_id)` that counts refusals instead of `door-refusals.jsonl`.

## Proof

`tests/gates/test_explicit_subagent_model.py` replays both hooks through their stdin contract:
the model refusals and the fork exemption, `require_effort` (off by default; missing and blank
effort refused, both present passes, a fork passes, both missing gives both sentences in order
with the guidance once at the end), and the route card (valid JSON with the configured text,
nothing printed when the key is empty or absent, the SessionStart wiring, a crash passing
loudly). A hook of this kind is deterministic, so it ships no model-run eval.
