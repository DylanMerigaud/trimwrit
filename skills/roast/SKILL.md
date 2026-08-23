---
name: roast
description: Diagnose why the last output was wrong and record it as a correction. Use the moment the user pushes back on something you produced, in any wording: "I don't like this", "j'aime pas", "non pas ca", "that's not what I asked", "wrong", "you did it again", "no, redo it", "stop doing X". Also use when the user asks to roast an answer, or to log a correction.
---

# roast

The user just told you something you produced was wrong. This is the highest value signal you
will get all session and it evaporates the moment the conversation moves on. Capture it now.

## What a roast is

One line that names the MECHANISM, sourced. Not a score, not an apology, not a list of things
you could have done better.

A diagnosis answers exactly one of these, and says which:

- **A rule existed and was violated.** Name it and quote it. `CLAUDE.md:14 says never estimate
  in hours, and the third paragraph said "about two days".`
- **No rule existed.** Say so plainly. That is the interesting case, because it is the one that
  earns a new rule. `Nothing in the harness says where the recommendation goes, so it went in
  the middle.`
- **A rule existed and was ambiguous or was argued away.** The worst case, and the most common.
  `The rule says "be concise", which lost to "the user asked for the reasoning".`

Then name the mechanism in the same breath: WHY did the wrong output look right at the time.
"I used an em-dash" is an observation. "Flowing prose with asides pulls em-dashes in, and
nothing in the harness was checking the output for them" is a mechanism, and only a mechanism
tells you what kind of rule would have stopped it.

## Then write it down, through the CLI

```bash
trimwrit log "<what the user actually said, verbatim>" --tag <handle> --session <session id>
```

Verbatim. Not your summary of it. The summary is the diagnosis and it belongs in the case; if
the two share a field the ledger stops being evidence.

The `--tag` is the claim that this incident and some earlier incident are the same incident. It
is what makes a counter count, so pick an existing tag when one fits (`trimwrit show` lists
them) and coin a new one only when nothing does.

Add `--consequence "<what it cost, once, in the real world>"` when the correction is expensive
the first time: a message that went out, a wrong number a third party saw, work thrown away.
That unlocks a rule at one occurrence instead of two. Irritation is not a consequence. If the
only cost is that the user had to ask twice, leave it off and let the counter do its job.

Never write to the ledger with Write or Edit. The CLI is the only door, and it refuses
duplicates that would fabricate a second occurrence out of one retry.

## What to say back

The diagnosis, in one or two lines. Then what you logged and what it unlocked. `trimwrit log`
tells you when a tag has just qualified for a rule; pass that on and offer the next step. Do
not apologise at length, do not restate the correction back, do not promise to remember. A
promise to remember is exactly the thing this tool exists to replace with a test.

## Do not

Do not invent a consequence to reach the important route faster. Do not tag two different
mistakes the same way to reach n=2. Both fabricate evidence, and a rule written on fabricated
evidence is worse than no rule: it cannot be pruned, because its case never described anything
real.
