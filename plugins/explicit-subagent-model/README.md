# explicit-subagent-model

Refuses an Agent or Task call that names no model, so a subagent never silently inherits
whatever /model last set.

- **PreToolUse hook** (`explicit_subagent_model.py`, matcher `Agent|Task`): a call whose `model`
  is missing or blank is refused with exit 2 and a reason on stderr; the model reads it and calls
  again with a model. A `fork` is exempt, because the harness ignores `model` on a fork. That is
  the one exemption and it is not configurable.

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
| `guidance` | `""` | text appended to the refusal reason, e.g. which model fits which work |

## Proof

`tests/gates/test_explicit_subagent_model.py` replays the hook through its stdin contract. A
PreToolUse gate is deterministic, so it ships no model-run eval.
