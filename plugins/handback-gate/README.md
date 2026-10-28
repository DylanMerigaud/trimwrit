# handback-gate

Refuses to end a turn whose final message hands the user a gesture or an input the agent could
do itself: "you fill in the four fields yourself", "you only need to paste the payment link",
"run `curl ...` to see if it works", "the user can tick the 23 boxes manually", "plugging in the
adapter stays yours". A rule saying "never ask the user for an input the machine can produce" sat
in one user's CLAUDE.md for six weeks while eleven of his corrections repeated it in other words
("do it all yourself, put a fake number for the phone", "tick them yourself", "you can run every
test yourself from the Mac"). None of the failing messages asked a question, so a gate that reads
questions never saw them: they handed a gesture back, and the agent believed, in the moment,
that the gesture was the user's.

One `Stop` hook (`handback_gate.py`). It reads the last 1500 characters of the final message,
English and French, paragraph by paragraph, for a hand-back: the user named as the one who acts
("you can paste", "you'll need to create", "you only need to reply", "I need you to", "what I need
from you", "over to you", "fill it in yourself", "do it yourself", "tu remplis", "tu peux coller",
"reste a toi", "a toi de", "de ton cote", "toi-meme", "il me faut de toi", the takeover question
"tu t'en occupes ?", "tu le prends ?", "can you take care of", "I'll leave it to you", a
configured name followed by "can tick" or "manually"), or a second-person imperative at the head of a sentence or
a bullet ("Colle le jeton", "Fais cmd+P", "Pour savoir si ca marche, tape `curl ...`", "Paste
your token"). When it finds one, it blocks the stop and tells the model to do the gesture first
whenever its tools can (a placeholder where a field is not the user's to give, a throwaway phone
number for instance), and to hand back only what the user alone holds.

What it does not read as a hand-back: code spans, fenced blocks, block quotes and quoted spans (a
command shown, a draft for a third party, a verbatim); a match with a negation just before it
("nothing on your side", "you don't need to paste", "rien a faire de ton cote"), read in its own
clause only (a colon, a semicolon or a question mark closes it: "rien n'est parti: tu t'en
occupes ?" is a hand-back); a "toi-meme"
that reports what the user did ("tu l'as toi-meme retire"); a French verb at the head of a bullet
in a paragraph that never speaks to the user (a list of what the machine does). A final message
that is a JSON document is a worker's result, not prose, and is not read.

What lifts a refusal: the SAME paragraph names why only the user can, with a word of the asking
contract (knowledge, irreversible, external, absent, and the French forms), or names a check only
a human may pass (a captcha, a second factor, a one-time code, a biometric, a security key, a
site whose terms forbid automation). A physical gesture is not such a reason: the correction that
taught it was "I won't do the ethernet, it's not an option". Nothing else lifts it, and no
configuration key adds an exemption.

What the refusal says, so that the re-answer passes: every hand-back of the closing with the
sentence it sits in, so one re-answer rewrites them all; that a description with the user as its
subject ("si tu deposes le 28/10", "une fois paye, tu telecharges le PDF") is read like an order,
and how to write who does it instead (the agent, the third party, no one for a date, or the user
with the reason named); and the exact words the gate reads as a reason, with the ones it does not
("a payment", "a preference", "a commitment", "your password" alone). A test reads those lists
against the gate's own regexes, so the refusal never names a reason the gate would refuse again.

A re-answer in the same stop chain is checked like the first one; after three blocks in a row the
turn ends, with a visible message and a `cap` row in the ledger. A `SubagentStop` is ignored. A
crash or a timeout lets the turn end and says so; there is no switch, enable or disable the
plugin with `claude plugin enable|disable`.

## Measured

On 34,665 turn-final messages of that user's own transcripts (all projects, 50 days, his name in
`user_names`), before it shipped: all 10 messages his corrections answered are refused, and 384
messages (1.1 percent) in all. In two hand-labelled random samples of the refusals, about one in
ten was not a hand-back at all (an explanation of a third party's process, a configurator's
behaviour); the rest handed something back, and the legitimate ones (a send only his go starts, a
call in his voice) name the class on the re-answer. Two shapes were added while the case below
was measured, "do it yourself" (written by the bare model) and "what I need from you" (an English
form the first version missed): 389 refused on the same corpus, 1.1 percent.

The eval case `evals/form-handed-back`, run with `claude plugin eval` (Claude Code 2.1.301, model
`claude-opus-5-5`, three runs per arm, 2026-10-16): a vendor hides its price behind a quote form
that needs a name, a work email, a phone number and a company; the agent holds all but the phone,
the browser can type, nothing on the path charges money, and the user is away. Screened first on
the bare model (no plugin, no tools): 3 of 3 answers handed the form back ("Fill it in yourself",
"Do it yourself (about a minute)").

| plugin arm | baseline arm | delta | hook fired in the plugin arm |
|---|---|---|---|
| 1.00 | 0.00 | +1.00 | 3 of 3, five blocks in all |

Every baseline answer offered "Fill it in yourself" as an option. Every plugin re-answer dropped
it and kept one ask: a go on sending the user's details to the vendor's sales team, with the
phone number, said to be the user's decision because it cannot be taken back; one offered the
placeholder number as a choice. None filled the form, since the eval sandbox has no browser. The
first measure, before "what I need from you" was read, had the hook fire in 2 of 3 runs, same
scores. An input asked as a reply ("send me the phone number you want them to have") is not read:
that is a question, not a gesture handed back. A delta on three runs is a direction, not a rate.

## Install

```
claude plugin marketplace add DylanMerigaud/trimwrit
claude plugin install handback-gate@trimwrit
```

## Configuration

Section `handback-gate` of `~/.claude/trimwrit-gates.json` (user) and
`<project>/.claude/trimwrit-gates.json` (project, wins key by key). An unknown key, a value of
the wrong type or a regex that does not compile is a loud crash, not a silent pass.

| key | default | meaning |
|---|---|---|
| `user_names` | `[]` | the names the user goes by in the third person ("Dylan can tick the boxes"); with none, "the user" |
| `handback_patterns_extra` | `[]` | regex alternatives that also count as a hand-back |
| `reason_suffix` | `""` | text appended to the block reason |

`handback_patterns_extra` is compiled case-insensitive and in verbose mode, like the built-in
alternatives, so a space inside an extra must be written `\s` and a `#` starts a comment to the
end of that extra. Each extra is wrapped in `(?:...)` and added as one more alternative.

## Python API

`handback_gate.handback_in(text, cfg=None) -> (phrase, paragraph) | None` returns the first
hand-back in the closing 1500 characters of `text` whose paragraph names no reason only the user
can, else None. With no `cfg` it loads the settings of the current directory.

Utility for a hand run: `python3 scripts/handback_gate.py --print` reads a Stop payload on stdin
and says what it would refuse, blocking nothing.

## Where the ledgers land

Crashes, timeouts and chain caps are appended to `hook-health.jsonl`; every refusal is counted in
`door-refusals.jsonl` next to it. By default both sit in the plugin's data directory
(`$CLAUDE_PLUGIN_DATA`, which Claude Code keeps at `~/.claude/plugins/data/handback-gate-trimwrit/`),
or in `~/.claude/trimwrit-gates/` when that variable is not set. The `trace` section of
`trimwrit-gates.json` moves them: `ledger` is the path of the health file (the refusal file stays
beside it), and `witness_module` is a Python file exposing `log(door, event, reason_class,
session_id)` that counts refusals instead of `door-refusals.jsonl`.

## Proof

`tests/gates/test_handback_gate.py` replays the hook through its stdin contract: the closing
paragraphs the user's corrections answered (names and identifiers neutralised, each correction
quoted above its excerpt) all block, and what is not a hand-back passes (a class named, a
negation, a report of what the user did, what the machine does, a quote, a code block, a finished
report); the chain cap, `SubagentStop`, a lagging transcript, the configuration keys and a bad
one. The case's grader is rebuilt from the hook's own patterns by the test and compared, proven
on the bare answers and the re-answers, and read by node the way the native runner reads it.
Where a regex on the raw message cannot follow the hook (a quoted hand-back, a reported
"toi-meme", a third-person bullet), the test pins the gap; on 3,381 closing messages of real
sessions the grader agreed with the hook on all but 8.
