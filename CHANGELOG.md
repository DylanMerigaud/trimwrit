# Changelog

## 0.1.0, 2026-08-23

First release. The loop runs end to end on this repo's own corrections.

Added:

- `trimwrit log` / `show` / `pending`, an append-only JSONL ledger of corrections with the two
  promotion routes: counter at n=2, important at n=1 with a written consequence. Both refuse
  rather than warn.
- `trimwrit case`, which writes `evals/<id>-<slug>/prompt.md` plus `graders/*.md` in the format
  `claude plugin eval` reads natively.
- `trimwrit integrate`, which writes a rule into any target file with a marker carrying the case
  id, the date, and the incident. It refuses a rule with no case.
- `trimwrit run`, a local ablation runner: every case is run with the rule file present and with
  it absent, and the reported number is the delta.
- `trimwrit prune`, which lists orphan rules (the case is gone), inert rules (the case scores the
  same without them), and unused cases, and with `--apply` deletes them and records the removal
  in the same ledger as the promotion.
- Four skills: roast, case, integrate, prune.
- An optional `UserPromptSubmit` hook, shipped unwired.
- `tools/no-bad-dashes.py`, which enforces the headline rule on this repo itself.

Three bugs found by running the tool on itself, each one now covered by a test:

- **The baseline arm was contaminated.** The first ablation reported the em-dash and
  time-estimate cases as inert at 1.00 in both arms. Both rules were already in the operator's
  own `~/.claude/CLAUDE.md`, which Claude Code loads regardless of the working directory, so the
  `without` arm was never a baseline. The runner now passes `--setting-sources project,local`
  by default. With the fix, the em-dash case scores 1.00 with the rule and 0.00 without it.
- **A regex character class was parsed as a YAML list.** A bare `pattern: [...]` holding the
  two dash characters came back from the front matter as a one-element list, so the grader loaded, reported, and could never
  fire. Scalars that would not round-trip are now single-quoted, and single-quoted rather than
  double-quoted so the escape stays literal instead of resolving back into the character the
  tool refuses.
- **The dash scanner reported clean after reading zero files.** It trusted `git ls-files`, which
  succeeds with empty output in a repository with nothing staged yet. An empty list is now
  treated as no answer rather than as a pass.
