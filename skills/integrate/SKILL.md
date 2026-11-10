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
  --door <the file or plugin that enforces it> \
  --rule "<the rule as a model will read it>"
```

`--target` takes any file: `CLAUDE.md`, a `SKILL.md`, a hook script. Pick the narrowest one that
covers the behaviour. A rule about one workflow belongs in that workflow's skill, where it costs
nothing until the skill loads; the same rule in CLAUDE.md is paid for on every prompt of every
session forever.

## An always-loaded target takes a door and three lines

Any file named CLAUDE.md, `~/.claude/rules/*.md`, the memory index, or a key of
`.trimwrit/budget.json` loads every turn. Into one of those, add `--door <path or plugin>`: the
hook, script or plugin that refuses the behaviour (`--door hooks/send_gate.py`, `--door
no-em-dash`). It must exist; when the rule does not already name it, ` Door: <door>.` is appended.
Each refusal below exits 3 and writes nothing:

- `no-door:` no `--door`, or one that names nothing that exists. A rule with no door is a wish:
  build or name the gate first.
- `rule-too-long:` more than three lines once the door is named (a line counts once per 100
  characters). Keep the imperative there; move the story to the repository's history doc, and a
  rule that only matters in one situation to that situation's skill (`trimwrit relocate <rule>
  --to <file>` moves an existing one with its marker).
- `over-budget:` the file would end over its budget in `.trimwrit/budget.json`.
- `no-description:` the target is a `SKILL.md` with no frontmatter `description`. A skill is
  picked by its description alone; write one that names the situation. A new SKILL.md can take
  its frontmatter in `--rule`, it is written above the marker. A skill needs no door and has no
  line cap.

After moving rules into skills, `trimwrit skills --check --refusals <door-refusals.jsonl>` names
a skill that never fired while its rule's door kept refusing: rewrite that description.

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
