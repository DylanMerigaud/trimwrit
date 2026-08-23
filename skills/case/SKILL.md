---
name: case
description: Turn a logged correction into an eval case that replays the situation. Use after roast has recorded a correction and a tag has qualified for a rule, or when the user asks to write an eval, add a regression test for a behaviour, or turn a correction into a test.
---

# case

A correction becomes a rule only through a case. This step writes the case.

## The one thing that makes a case worth having

**The prompt must REPLAY the situation that produced the bad output.** Not describe it, not ask
about it. If you would have to already know the rule to answer the prompt correctly, the case
measures recall of a sentence and it will pass forever while the behaviour rots.

Test it by asking: with no rule loaded at all, would this prompt tempt the model into the same
mistake? If the answer is no, the prompt is wrong. Rewrite it until a clean model fails it.

Good: `Write a two-paragraph launch announcement, make it read well, with some rhythm to it.`
That pulls em-dashes out of a model that has not been told.

Bad: `Write a paragraph without using em-dashes.` Everything passes that, including a model
that would have used four of them a second earlier.

## Write it

```bash
trimwrit case <correction-id> \
  --prompt "<the prompt that replays the situation>" \
  --forbid "<regex the output must not contain>" \
  --require "<regex the output must contain>" \
  --tool "<a tool the run must call>" \
  --judge "<rubric, last resort>"
```

This writes `evals/<id>-<slug>/prompt.md` and `evals/<id>-<slug>/graders/*.md` in the format
`claude plugin eval` reads natively, so the same files run under `trimwrit run` today and under
the native runner when it opens up.

## Mechanical graders first, always

In order of preference: `--forbid` and `--require` (regex), `--tool` (a tool was or was not
called), file checks, and only then `--judge`.

A judge costs money on every run, disagrees with itself between runs, and rates a vague rubric
generously. A regex on a forbidden string is boring and it is right every time. Reach for the
judge only for the part of the correction no string can express, and when you do, keep the
mechanical graders alongside it rather than replacing them.

Write the rubric as a pass condition and a fail condition, both concrete. `PASS if the final
block is short and carries an explicit pick. FAIL if the pick is buried earlier.` A rubric that
only says what good looks like will pass almost anything.

## Then measure it before you trust it

```bash
trimwrit run --case <case-dir> --target <the file that will carry the rule>
```

Read the delta, not the score. A case that scores the same with the rule and without it is an
instrument that never moves. Break it and rewrite it before anyone relies on it.
