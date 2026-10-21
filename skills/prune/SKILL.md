---
name: prune
description: Find and remove harness rules that no longer earn their place. Use when CLAUDE.md is getting long, when the user asks to clean up or trim the rules, audit the harness, or asks whether a rule is still needed, and as the last step after running the eval cases.
---

# prune

Taking a rule back out. This is the step no comparable tool ships, and it is the reason the
rest of the loop is safe to run: a system that only ever adds instructions is a system whose
harness gets worse every week while every individual decision looks correct.

## The findings, and what each one rests on

```bash
trimwrit prune --target CLAUDE.md              # orphans only, free, no model call
trimwrit prune --target CLAUDE.md --results    # adds the measured findings, needs a run first
```

**orphan**: the rule names a case, and the case is not there. Nothing can ever show this rule is
still needed, because the thing that would show it does not exist. Detected by reading files,
costs nothing.

**inert**: the case exists, it runs, the two arms pass at the same rate, and the case passes
without the rule. The rule is not wrong. It is unnecessary, because the model already behaves.
This is the finding a growing CLAUDE.md never gets told, and it is the only honest reason to
delete an instruction everybody still agrees with.

**inert-fail**: the two arms are equivalent, but the case is not reliably passed without the
rule. The numbers cannot say the rule is unneeded. Rewrite the rule or its case and run it
again before deleting anything. `--apply` never deletes one.

**harmful**: the case passes less often with the rule than without it, decided. The rule makes
its case worse: rewrite it. `--apply` never deletes one, because the behaviour it was written
for still needs a rule that works.

**unused-case**: a case no rule points at. Usually not a deletion order, usually an integrate
step that never ran. Write the rule, or delete the case.

## Get a verdict before you delete anything

```bash
trimwrit run --decide --ablate rule --target CLAUDE.md
trimwrit prune --target CLAUDE.md --results
```

`--decide` runs each case 3 times per arm at a time until the pass counts decide (`keep`,
`harmful`, `inert`, `inert-fail`) or it reaches 30 runs per arm (`undecided`). The counts add
up across invocations until the case, the rule file or the model changes. `--ablate rule` takes
out only the rule blocks that cite the case, so the verdict is about that rule and not about the
whole file. Add `--budget N` to cap the sessions one invocation starts; the next one picks up
where it stopped.

A finding is a measurement and it carries its counts. Never report one without them: `delete
this, it does nothing` is an opinion, and `case 0002: 18/18 with, 18/18 without, P(helps)
0.499` is a finding. A results file from a plain `trimwrit run` carries means and no verdict;
prune still reads it and says it was measured with a fixed run count, which is a signal to run
`--decide`, not a reason to delete.

## Deleting

```bash
trimwrit prune --target CLAUDE.md --results --apply
```

`--apply` deletes orphans and rules whose case is decided `inert`, nothing else. It refuses to
delete a rule whose file changed since it was measured ("the rule file changed since it was
measured; run `trimwrit run --decide` again"), and it keeps a rule that cites another case not
decided `inert`.

Every removal is written into the same ledger as the promotion that put the rule there, with
the reason. One file answers "is this rule live", which is the point: a second file for
removals would give two answers and the tool would have reproduced the drift it exists to stop.

**Keep the case.** Deleting an inert rule keeps its case, and that is deliberate. The case is
the thing that will notice if the behaviour comes back, and it is the evidence that the rule was
tried. A model update six months from now can make an inert rule necessary again, and the case
is what will say so.

## What to tell the user

The findings with their counts, then the count of rules removed. Do not soften an inert finding
into "you might consider", and do not turn an `undecided` case into a finding: the numbers
either decided or they did not.
