---
name: prune
description: Find and remove harness rules that no longer earn their place. Use when CLAUDE.md is getting long, when the user asks to clean up or trim the rules, audit the harness, or asks whether a rule is still needed, and as the last step after running the eval cases.
---

# prune

Taking a rule back out. This is the step no comparable tool ships, and it is the reason the
rest of the loop is safe to run: a system that only ever adds instructions is a system whose
harness gets worse every week while every individual decision looks correct.

## Two findings, two kinds of evidence

```bash
trimwrit prune --target CLAUDE.md              # orphans only, free, no model call
trimwrit prune --target CLAUDE.md --results    # adds the inert finding, needs a run first
```

**orphan**: the rule names a case, and the case is not there. Nothing can ever show this rule is
still needed, because the thing that would show it does not exist. Detected by reading files,
costs nothing.

**inert**: the case exists, it runs, and it scores the same with the rule and without it. The
rule is not wrong. It is unnecessary, because the model already behaves. This is the finding a
growing CLAUDE.md never gets told, and it is the only honest reason to delete an instruction
everybody still agrees with.

**unused-case**: a case no rule points at. Usually not a deletion order, usually an integrate
step that never ran. Write the rule, or delete the case.

## Get the numbers before you delete anything

```bash
trimwrit run --target CLAUDE.md
trimwrit prune --target CLAUDE.md --results
```

An inert verdict is a measurement and it carries its two scores. Never report one without them:
`delete this, it does nothing` is an opinion, and `case 0002 scores 1.00 with R0002 and 1.00
without it` is a finding.

## Deleting

```bash
trimwrit prune --target CLAUDE.md --results --apply
```

Every removal is written into the same ledger as the promotion that put the rule there, with
the reason. One file answers "is this rule live", which is the point: a second file for
removals would give two answers and the tool would have reproduced the drift it exists to stop.

**Keep the case.** Deleting an inert rule keeps its case, and that is deliberate. The case is
the thing that will notice if the behaviour comes back, and it is the evidence that the rule was
tried. A model update six months from now can make an inert rule necessary again, and the case
is what will say so.

## What to tell the user

The findings with their numbers, then the count. Do not soften an inert finding into "you might
consider". The measurement either moved or it did not.
