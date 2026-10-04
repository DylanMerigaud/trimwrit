# claim-gate

Refuses to end a turn whose final message claims a gesture done (pushed, merged, sent, deployed,
tests pass, verified) with no tool result in the same turn that proves it.

The Stop hook (`claim_gate.py`) reads the last 1500 characters of the final message, English and
French, for a first person or status claim ("I pushed", "merged to main", "j'ai envoye", "tests
pass", "I verified"). It then looks at the tool calls after the last typed user message: a claim
needs a call of its kind whose result is not an error (push: `git push` or `gh pr merge`; sent: a
send command or tool; deployed: vercel, a deploy command, launchctl; tests: pytest, vitest, go
test and friends; verified: any Bash, Read, Grep, Glob or MCP read). A subagent or a workflow that
ran in the turn counts for every kind. No receipt: the stop is blocked with a reason that quotes
the claim, and the model either runs the command now or rewords the claim to what it knows.

Not a claim: a negation, a future, a question, a condition, a third party's act, a quoted line, a
code span, or an item of a list whose introducing line ends on a negation governing its colon:
the negation is the last word before ":", or is followed only by "yet"/"encore" or one
auxiliary or participle ("So far I have not:", "I haven't yet:", "Nothing was:", "Je n'ai pas
:"), emphasis markup ignored. A negation earlier in the line ("I did not wait:", "Nothing
blocked me:") or another void word ("Done, since you asked before:") does not void the list. A block is checked in a chain up to three times, then the turn ends with a visible
message. A crash or a timeout lets the turn end and says so; there is no switch, enable or
disable the plugin with `claude plugin enable|disable`.

## Install

```
claude plugin marketplace add DylanMerigaud/trimwrit
claude plugin install claim-gate@trimwrit
```

## Configuration

Section `claim-gate` of `~/.claude/trimwrit-gates.json` (user) and
`<project>/.claude/trimwrit-gates.json` (project, wins key by key). An unknown key, an unknown
kind or a regex that does not compile is a loud crash, not a silent pass. Kinds are `pushed`,
`merged`, `sent`, `deployed`, `tests` (receipts) and `pushed`, `sent`, `deployed`, `tests`,
`verified` (claims).

| key | default | meaning |
|---|---|---|
| `claims_extra` | `{}` | kind to a list of regex alternatives that also count as that claim |
| `receipts_extra` | `{}` | kind to a list of regex alternatives matched against `ToolName command` that also count as that receipt (for example your own `./scripts/ship.sh` for `pushed`) |
| `void_extra` | `[]` | regex alternatives that void a claim sentence, like "not" or "once" |
| `reason_suffix` | `""` | text appended to the block reason |

## Python API

`claim_gate.claims_in(text, cfg=None) -> dict` and `claim_gate.claims_without_receipt(text,
cfg=None) -> bool` replay the claim detector outside a hook (a ledger of past messages, for
instance). With no `cfg` they load the settings of the current directory.

`python3 scripts/claim_gate.py --check TRANSCRIPT.jsonl` prints what the gate would say about a
transcript, blocking nothing.

## Where the ledgers land

Crashes, timeouts and, for a Stop gate, chain caps are appended to `hook-health.jsonl`; every
refusal is counted in `door-refusals.jsonl` next to it. By default both sit in the plugin's data
directory (`$CLAUDE_PLUGIN_DATA`, which Claude Code keeps at `~/.claude/plugins/data/claim-gate-trimwrit/`),
or in `~/.claude/trimwrit-gates/` when that variable is not set. The `trace` section of
`trimwrit-gates.json` moves them: `ledger` is the path of the health file (the refusal file stays
beside it), and `witness_module` is a Python file exposing `log(door, event, reason_class,
session_id)` that counts refusals instead of `door-refusals.jsonl`.

## Proof

`tests/gates/test_claim_gate.py` replays the hook through its stdin contract with transcripts
built as JSONL files: the claim and clean matrices, the receipt matrix, an errored push and a
previous turn's push as non-receipts, the chain cap, and the configuration keys. The eval case
`evals/claim-without-receipt` measures the Stop hook with `claude plugin eval`: a plugin arm and
a baseline arm on a turn where nothing was run, graded by a regex on the final message.
