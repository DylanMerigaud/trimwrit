# trimwrit

A harness rule enters with a test, and leaves when the test passes without it.

You correct Claude, it apologises, and the correction evaporates. The usual fix is to write a
line into CLAUDE.md, which works until the file is 400 lines long and nobody can tell which
lines still do anything. trimwrit closes the other end of that loop: every rule it writes names
the eval case that justifies it, and every rule whose case passes **without** it gets listed for
deletion, with the two numbers that say so.

It is a Claude Code plugin and a Python CLI. No dependencies, no service, no account.

## Install

```bash
claude plugin marketplace add DylanMerigaud/trimwrit
claude plugin install trimwrit@trimwrit
```

Claude then reaches for the skills on its own when you push back on something it produced, and
the `trimwrit` command is on the PATH of its Bash tool. Nothing is pip-installed.

To use it as a plain CLI, clone it and run `bin/trimwrit`. Python 3.9 or newer, standard library
only.

## The loop, end to end

Say you have just told Claude, for the second time, to stop putting em-dashes in your writing.

**1. roast.** The `roast` skill fires on the correction and names the mechanism, not the
symptom: flowing prose with asides pulls em-dashes in, and nothing in the harness was checking
the output. Then it records what you actually said, verbatim:

```bash
trimwrit log "there are two em-dashes in the commit message you just wrote" \
  --tag em-dash --session s-2026-08-22
```

```
logged correction 0002  tag=em-dash
  tag em-dash now qualifies for a rule by the counter route (n=2).
  next: trimwrit case 0002
```

Two routes out of the ledger, both enforced. **counter** at two occurrences of a tag, because
one is an observation and two is a pattern. **important** at one occurrence, and only when a
consequence is written down, because some mistakes are expensive the first time and waiting for
a second means paying twice on purpose. `trimwrit promote` refuses below the threshold, and it
refuses the important route with no consequence on the record.

**2. case.** The correction becomes an eval case that replays the situation:

```bash
trimwrit case 0002 --title "no em dash anywhere" \
  --prompt "Write a two-paragraph launch announcement for a command line tool whose parser was rewritten and is now three times faster. Make it read well, with some rhythm to it." \
  --forbid "[$(printf '\u2014\u2013')]"
```

The prompt has to tempt a clean model into the same mistake. If you would need to already know
the rule to answer it correctly, the case measures recall of a sentence, and it will pass
forever while the behaviour rots.

What lands on disk is the **native `claude plugin eval` layout**, not a format of ours:

```
evals/0002-no-em-dash-anywhere/
  prompt.md
  graders/forbids-em-dash.md
```

```yaml
# graders/forbids-em-dash.md
---
type: regex
name: forbids-em-dash
pattern: '[\u2014\u2013]'
match: not_contains
flags: i
target: last_message
---

From correction 0002: there are two em-dashes in the commit message you just wrote
```

**3. integrate.** The rule goes into the target, and it cannot go in without a case:

```bash
trimwrit integrate 0002 --target CLAUDE.md --route counter \
  --incident "a launch post went out with three em-dashes and read as machine written" \
  --rule "Never use an em-dash or an en-dash, anywhere. Use a comma, a colon, parentheses, or start a new sentence. The ASCII hyphen is fine."
```

```
<!-- trimwrit: R0002 case 0002, 2026-08-23: a launch post went out with three em-dashes and read as machine written -->
Never use an em-dash or an en-dash, anywhere. Use a comma, a colon, parentheses, or start a new sentence. The ASCII hyphen is fine.
```

**4. run.** With the rule, and without it:

```bash
trimwrit run --target CLAUDE.md
```

```
case                                   with   without   delta  verdict
------------------------------------------------------------------------
0002-no-em-dash-anywhere               1.00      0.00   +1.00  earns its place
0005-decision-last-and-short           1.00      0.75   +0.25  earns its place
```

**5. prune.** The step nothing else ships:

```bash
trimwrit prune --target CLAUDE.md --results
```

```
kind         rule     why
------------------------------------------------------------------------
orphan       R0031    CLAUDE.md:88 names case 0031, which is not in evals/. Nothing can show this rule still earns its place.
inert        R0014    case 0014-json-only scores 1.00 with R0014 and 1.00 without it (delta +0.00). The model already behaves. Delete the rule and keep the case.
```

`--apply` deletes them and writes each removal into the same ledger as the promotion, with the
reason. The case stays: it is what will notice if the behaviour comes back after a model update.

## Why the deletion half is the point

"Why Does CLAUDE.md Keep Growing? Catastrophic Remembering"
([arXiv 2608.11095](https://arxiv.org/abs/2608.11095), 11 August 2026) measured **+226% growth**
across 1867 repositories and named the cause: adding an instruction is cheap, deleting one is
frightening, because the person deleting cannot tell what it was protecting against. The same
paper measured the fix. Instructions carrying the reason they exist were removable, and **99.3%**
of the surplus was cut in a controlled test.

That is why every rule marker carries the case id, the date, and the incident in one line. It is
not documentation, it is the thing that makes the rule deletable by someone who was not there.

And it is why `prune` reports numbers rather than an opinion. An **inert** rule is not wrong. It
is unnecessary, because the model already behaves, and that is the only honest reason to delete
an instruction everybody still agrees with.

## The baseline has to be clean, and by default it is

The runner passes `--setting-sources project,local`, which drops your own
`~/.claude/CLAUDE.md` from both arms.

This is not a precaution, it is a bug that was found by running the suite. The first ablation of
this repo's own dogfood reported the em-dash case and the time-estimate case as inert at exactly
1.00 in both arms. Both rules were already in the user memory of the machine running it, so the
`without` arm was never a baseline. Measured after the fix: with a local CLAUDE.md present the
model reports the rule, with the file absent it reports no such rule.

If you want the contaminated behaviour, `--no-isolation` is there. It is off by default because
a delta measured against your own harness is not a delta.

## Two runners, one format

`claude plugin eval` is in early access. On the machine this was built on, Claude Code 2.1.241,
`claude plugin eval .` answers *"`plugin eval` is currently in early access"* and runs nothing.
A tool whose promise is "the rule is backed by a test" cannot ship with its test runner behind
someone else's flag, so `trimwrit run` reads the same files and drives `claude -p` directly.

There is a second reason, and it outlasts the first. The native runner takes a plugin or a
skills directory, and its baseline arm is *the plugin is not loaded*. The most common place a
harness rule actually lives is a CLAUDE.md, and no plugin flag will ever remove one. `trimwrit
run` ablates a **file**, so it works on CLAUDE.md, on a SKILL.md, on a hook, on anything the
model reads.

Because the case format is native, the day `plugin eval` opens up, every case ever written by
this tool runs under it with no migration.

| grader | `trimwrit run` | native |
|---|---|---|
| `regex` | yes | yes |
| `tool_used` | yes | yes |
| `tool_order` | yes | yes |
| `file_exists` | yes | yes |
| `llm` | yes, via `claude -p` | yes |
| `baseline` | no, reported as a failure | yes |

A grader this runner cannot score does **not** silently pass. It fails the case, because a hole
in the measurement has to look like a hole.

## What this is not

**Not a memory.** Claude Code has had automatic `feedback` memories since v2.1.59. This tool
does not store what you like; it writes rules and it deletes them, and the deleting is the part
you cannot get anywhere else.

**Not an eval runner.** The format belongs to `claude plugin eval`. `trimwrit run` exists
because that one is gated today and cannot ablate a CLAUDE.md ever.

**Not self-improving, and it does not learn.** Nothing here happens without you: you tag the
correction, you write the rule, you approve the deletion. What it removes is the ability to add
a rule without evidence, or to keep one after the evidence is gone.

## Dogfood

`evals/` holds this repo's own cases, generated by the tool from real corrections, and
`examples/CLAUDE.md` holds the rules they justify. `trimwrit run --target examples/CLAUDE.md`
reproduces the table above.

`tools/no-bad-dashes.py` enforces the repo's headline rule on the repo itself, and it exists
because the obvious shell check does not work: a `grep -rn` written over the two characters
reported a clean tree on macOS with zsh while three em-dashes sat in a test file inside it.
The first version of the scanner then did the same thing to itself, trusting `git ls-files`,
which succeeds with empty output before anything is staged. Both misses are in the tests.

## Commands

| | |
|---|---|
| `trimwrit log` | record a correction, verbatim |
| `trimwrit show` | the ledger, folded |
| `trimwrit pending` | tags that have earned a rule, and by which route |
| `trimwrit case` | a correction becomes a native eval case |
| `trimwrit integrate` | write the rule, with its case and its incident |
| `trimwrit run` | run the cases with the rule and without it |
| `trimwrit prune` | rules that no longer earn their place |

## State

Claude Code only. The case format is Anthropic's, the ledger and the runner are not, and nothing
here has been ported to Codex or Gemini yet.

The optional hook in `hooks/` is **not wired**, and it is off by filename rather than by
omission: Claude Code auto-discovers `hooks/hooks.json` at a plugin root whether or not the
manifest mentions it, so the config ships as `hooks/hooks.disabled.json`. Rename it to turn it
on, and read `hooks/notice-correction.sh` first, because it runs on every prompt you type.

MIT.
