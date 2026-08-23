---
name: integrate
description: Write a promoted rule into CLAUDE.md, a skill, or a hook, with its case and its incident attached. Use after a case exists and a tag has qualified, or when the user asks to add a rule, write this to CLAUDE.md, make this permanent, or remember this properly.
---

# integrate

Write the rule. The refusal built into this step is the product: any editor can add a line to a
CLAUDE.md, and what nobody has is a door that will not let an unjustified line through.

## Check the route first

```bash
trimwrit pending
```

Two routes, and the tool enforces both:

- **counter**, at two occurrences of the same tag. One is an observation, two is a pattern.
- **important**, at one occurrence, and only when a consequence is written on the ledger. Some
  corrections are expensive the first time and waiting for a second means paying twice on
  purpose. The consequence is mandatory on this route and it is stored with the entry.

A tag that appears in `pending` has earned a rule. A tag that does not has not, and the answer
to "can we just add it anyway" is no. That is the whole reason the counter exists: a rule you
can talk your way into is a rule that can be talked away again, and the corpus this design came
from lost a rule exactly that way, in an argument that sounded correct.

## Write it

```bash
trimwrit integrate <correction-id> \
  --target CLAUDE.md \
  --route counter \
  --incident "<one line: what went wrong, once, on a real day>" \
  --rule "<the rule as a model will read it>"
```

`--target` takes any file: `CLAUDE.md`, a `SKILL.md`, a hook script. Pick the narrowest one that
covers the behaviour. A rule about one workflow belongs in that workflow's skill, where it costs
nothing until the skill loads; the same rule in CLAUDE.md is paid for on every prompt of every
session forever.

## The incident line is not optional and it is not decoration

Every rule is written with a marker like this:

```
<!-- trimwrit: R0002 case 0002, 2026-08-23: a launch post went out with three em-dashes -->
```

That comment is the measured mitigation for the failure mode this whole tool exists against.
"Why Does CLAUDE.md Keep Growing? Catastrophic Remembering" (arXiv 2608.11095, 11 August 2026)
measured +226% growth across 1867 repositories and found the cause: adding an instruction is
cheap and deleting one is frightening, because the person deleting cannot tell what it was
protecting against. The same paper measured the fix. Instructions carrying the reason they
exist were removable, and 99.3% of the surplus was cut in a controlled test.

So write the incident as a fact with a date, not as a principle. `a launch post went out with
three em-dashes` can be checked and outgrown. `we value clean writing` cannot, and will still be
there in two years.

## Write the rule as an instruction, not as a value

Name the behaviour, name the boundary, name the exception if there is one. `Never use an
em-dash, anywhere. Use a comma, a colon, parentheses, or a new sentence. The ASCII hyphen is
fine.` A rule with no boundary gets applied where it does not belong, and a rule with no
exception gets argued away the first time the exception comes up.

## After writing

Run the case again with the rule in place. The rule is not integrated because you wrote it, it
is integrated when the delta says it changed something:

```bash
trimwrit run --case <case-dir> --target <the file you just wrote to>
```
