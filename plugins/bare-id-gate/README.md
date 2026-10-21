# bare-id-gate

Refuses to end a turn whose final message shows an internal id standing alone: "**c37 :** 5
dormant rows", "c37 and c43 whenever you like", "commit `b6bef2fd` is on main", "| intake-068 |
38/60 sent |". The codes a machine gives its queue items, commits, plan tasks and leads are clear
to the agent that has just read them in its tools; to the user reading the message on a phone
they are noise. One user wrote the rule into his CLAUDE.md after "intake-068 never show me things
like that, I understand nothing": name the thing in my words, the id may follow in parentheses
only when I need it to run a command, never alone. Six weeks later a closing block still ended on
"Next step: c37 and c43 whenever you like", and his next message was "what are c37 and c43".

One `Stop` hook (`bare_id_gate.py`). It reads the whole final message, prose only, for a token of
an id family: a git sha (7 to 40 lowercase hex characters holding a digit and a letter), a UUID,
a ticket-like key (`intake-068`, `PROJ-123`), plus the families the configuration adds. An id is
NAMED when it sits inside a parenthesis and a word of prose stands between it and the previous
id of its line: "the five dormant rows (c37)", "merged on main (b6bef2fd)", "(commit b4ee70e4,
log dd6e14c4)". Anything else is bare: an id heading a line, a bullet, a label or a table cell,
an id in running text, the second id of a list in one parenthesis ("five tests (intake-004,
intake-195)" names neither test). When it finds one, it blocks the stop and tells the model to
name each thing in the user's words first and put the id after it, or drop it.

What it does not read: fenced blocks, code spans holding a space (a command needs its id:
`` `answer c37 --go` ``), URLs, file paths, block quotes and quoted spans holding a space (a
verbatim, a draft for a third party). A one-token code span is read: `` `b6bef2fd` `` is an id
shown, backticks or not. A final message that is a JSON document is a worker's result, not prose,
and is not read.

What is not an id: a label the message numbers itself, from 1, at the head of its list items
("a1.", "**c1**", "- T1:"), the lettered list a user answers with a code; a key whose prefix
names a standard or a version (SHA-256, ISO-8601, COVID-19); a year after a word of time
(pre-2025, FY-2026); a commit range `a..b` takes the name of its first end. Nothing else lifts
it, and no configuration key adds an exemption.

A re-answer in the same stop chain is checked like the first one; after three blocks in a row the
turn ends, with a visible message and a `cap` row in the ledger. A `SubagentStop` is ignored. A
crash or a timeout lets the turn end and says so; there is no switch, enable or disable the
plugin with `claude plugin enable|disable`.

## Measured

On 38,071 turn-final messages of that user's own transcripts (all projects, 50 days), before it
shipped: the closing block his "what are c37 and c43" answered is refused, and so are 788
messages (2.1 percent) with the default families, 1,187 (3.1 percent) with his three added (the
codes of his review queue, a plan's task ids, his rule ids). In a hand-labelled random sample of
40 of those refusals, every one showed an id the user would have to look up: a sha after
"commit", an intake key heading a row, a queue code heading a bullet, "T7 and T9 are running",
an alias as the subject of a sentence. The one borderline was an exit code written `exit-75`.

The eval case `evals/id-shown-bare`, run with `claude plugin eval` (Claude Code 2.1.306, model
`claude-opus-5-5`, three runs per arm, twice, 2026-10-21): a session closes for a user away on a
phone, two review-queue items keyed `INT-0412` and `INT-0438` wait on him, and the fix of the
session is commit `4f2a9c1e`.

| plugin arm | baseline arm | delta | hook fired in the plugin arm |
|---|---|---|---|
| 1.00 | 0.00 | +1.00 | 3 of 3, both measures |

Every baseline answer headed each item with its key ("**INT-0438: call with Noah Brandt**") and
wrote "the fix is commit 4f2a9c1e". Every re-answer headed the item with its name ("**Call with
Noah Brandt, head of engineering at Eve (INT-0438)**"), kept the key inside the answer command,
and dropped the sha, which the user needs for nothing. A delta on three runs is a direction, not a
rate.

## Install

```
claude plugin marketplace add DylanMerigaud/trimwrit
claude plugin install bare-id-gate@trimwrit
```

## Configuration

Section `bare-id-gate` of `~/.claude/trimwrit-gates.json` (user) and
`<project>/.claude/trimwrit-gates.json` (project, wins key by key). An unknown key, a value of
the wrong type or a regex that does not compile is a loud crash, not a silent pass.

| key | default | meaning |
|---|---|---|
| `id_patterns_extra` | `[]` | more id families, one regex each, read as a whole token |
| `reason_suffix` | `""` | text appended to the block reason |

Each extra is wrapped in the same token bounds as the built-in families, so `c\d{1,3}` matches
`c37` and not `ec37` or `c37x`. It is compiled case-sensitive: `T\d{1,2}` is a plan's task, `t5`
is not.

## Python API

`bare_id_gate.bare_ids(text, cfg=None) -> [(family, token)]` returns every id of `text` that no
name precedes, in reading order, each token once. With no `cfg` it loads the settings of the
current directory.

Utility for a hand run: `python3 scripts/bare_id_gate.py --print` reads a Stop payload on stdin
and says what it would refuse, blocking nothing.

## Where the ledgers land

Crashes, timeouts and chain caps are appended to `hook-health.jsonl`; every refusal is counted in
`door-refusals.jsonl` next to it. By default both sit in the plugin's data directory
(`$CLAUDE_PLUGIN_DATA`, which Claude Code keeps at `~/.claude/plugins/data/bare-id-gate-trimwrit/`),
or in `~/.claude/trimwrit-gates/` when that variable is not set. The `trace` section of
`trimwrit-gates.json` moves them: `ledger` is the path of the health file (the refusal file stays
beside it), and `witness_module` is a Python file exposing `log(door, event, reason_class,
session_id)` that counts refusals instead of `door-refusals.jsonl`.

## Proof

`tests/gates/test_bare_id_gate.py` replays the hook through its stdin contract: the closing
blocks the user's corrections answered (names of third parties neutralised, each correction
quoted above its excerpt) all block, and what is not a bare id passes (a name then the id in
parentheses, a command, a fence, a label numbered from 1, a standard, a date, a path, a link, an
address, a quote, a block quote); the chain cap, `SubagentStop`, a lagging transcript, the
configuration keys and a bad one. The case's grader is rebuilt from the hook's own families by
the test and compared, proven on the baseline answers and the re-answers, and read by node the
way the native runner reads it. Where a regex on the raw message cannot follow the hook (a fence,
a quote, a block quote, a JSON result, a parenthesis with no word before it, a list of ids in one
parenthesis), the test pins the gap.
