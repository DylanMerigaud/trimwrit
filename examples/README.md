# examples

`CLAUDE.md` here is the demonstration target for this repo's own dogfood suite. Every block in it
was written by `trimwrit integrate`, from a real correction on this machine, and every block
names the case that keeps it alive.

Reproduce the measurement:

```bash
trimwrit run --target examples/CLAUDE.md
trimwrit prune --target examples/CLAUDE.md --results
```

Delete a rule from `CLAUDE.md` by hand and run it again. The delta for that rule's case is the
whole answer to "did that rule do anything".
