# promise-gate

Once the user sets a target ("never stop", "do not stop until...", "until results", "do the whole
pool"), refuses to end a turn on a promise to continue ("I will continue with the next batch",
"running in the background, will land") instead of doing the next batch.

Two hooks. `UserPromptSubmit` (`promise_gate.py --prompt`) reads each prompt and, when it sets a
target, writes a per-session marker with an excerpt of it. `Stop` (`promise_gate.py`) does
nothing in an unarmed session. In an armed one it reads the last 700 characters of the final
message, English and French, and blocks the stop when it ends on a promise; the reason names the
promise and the target, and tells the model to do the next batch now, or to hand the rest to a
subagent if the context runs short. A re-answer in the same stop chain is checked like the first
one; after three blocks in a row the turn ends, with a visible message and a `cap` row in the
ledger. A `SubagentStop` is ignored. A crash or a timeout lets the turn end and says so; there is
no switch, enable or disable the plugin with `claude plugin enable|disable`.

An unarmed session never hears from it: a recap that says "next batch tomorrow" after the user
said stop is legitimate.

## Install

```
claude plugin marketplace add DylanMerigaud/trimwrit
claude plugin install promise-gate@trimwrit
```

## Configuration

Section `promise-gate` of `~/.claude/trimwrit-gates.json` (user) and
`<project>/.claude/trimwrit-gates.json` (project, wins key by key). An unknown key, a value of
the wrong type or a regex that does not compile is a loud crash, not a silent pass.

| key | default | meaning |
|---|---|---|
| `target_patterns_extra` | `[]` | regex alternatives that also count as a target set by the user |
| `promise_patterns_extra` | `[]` | regex alternatives that also count as a promise to continue |
| `reason_suffix` | `""` | text appended to the block reason |

The patterns are compiled case-insensitive and in verbose mode, like the built-in ones, so a
space inside an extra must be written `\s` and a `#` starts a comment to the end of that extra.
Each extra is wrapped in `(?:...)` and added as one more alternative.

## Python API

`promise_gate.promise_in(text, cfg=None) -> str | None` returns the promise ending `text` (its
last 700 characters), `promise_gate.target_in(prompt, cfg=None) -> bool` says whether a prompt
sets a target. With no `cfg` they load the settings of the current directory.

Utilities for a hand run: `python3 scripts/promise_gate.py --arm "text"`, `--disarm` and
`--print` act on the session named by `CLAUDE_SESSION_ID`.

## Proof

`tests/gates/test_promise_gate.py` replays both hooks through their stdin contract: what arms and
what does not, the block-three-times-then-cap chain, a finished turn, `SubagentStop`, the
configuration extras and a bad one. The eval case `evals/turn-ends-on-a-promise` measures the
Stop hook with `claude plugin eval`: its prompt opens with the user's order not to stop (it arms
the gate in the plugin arm through `UserPromptSubmit`), the plugin arm and the baseline arm are
graded by a regex on the final message, and a pytest pins the grader against the phrasings the
gate refuses and the ones it allows.
