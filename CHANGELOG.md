# Changelog

## 0.1.2, 2026-08-23

Both changes come from the first real use of the tool, on a global `~/.claude/CLAUDE.md`, within
the hour.

- **A rule can cite several cases**, joined with `+`: `case 0001+0002`. One ban on one character
  needs a case that replays it in prose and another that replays it in a commit message. With a
  single id, the second case showed up forever as `unused-case` and the only ways out were to
  delete a good case or to ignore the tool. A rule is an orphan only when EVERY case it names is
  missing, so a rule held up by one surviving test is never proposed for deletion.
- **Re-integrating an already promoted rule is no longer reported as a failure.** Rewriting the
  wording, or adding a second case to the marker, is a normal thing to do; it was exiting 1 with
  "NOT promoted in the ledger" and made a correct command look broken.

## 0.1.1, 2026-08-23

The hook was shipping live. Claude Code auto-discovers `hooks/hooks.json` at a plugin root
whether or not `plugin.json` mentions it, so "the manifest does not reference it" was never an
off switch: `claude plugin details trimwrit` on the installed copy reported
`Hooks (1) UserPromptSubmit`. The config now ships as `hooks/hooks.disabled.json`, which
auto-discovery ignores. Rename it to turn the hook on.

A version bump rather than a silent re-push, because a pinned version is what `claude plugin
update` compares and 0.1.0 would have left every installed copy running the hook.

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
- An optional `UserPromptSubmit` hook, shipped off as `hooks/hooks.disabled.json`.
- `tools/no-bad-dashes.py`, which enforces the headline rule on this repo itself.

Four bugs found by running the tool on itself, three of them now covered by a test:

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
- **The hook shipped live while the README said it was off.** Claude Code auto-discovers
  `hooks/hooks.json` at a plugin root with no manifest entry, so "plugin.json does not reference
  it" was not the off switch it was claimed to be. `claude plugin details trimwrit` reported
  `Hooks (1) UserPromptSubmit` on the installed copy. The config now ships as
  `hooks/hooks.disabled.json`, which auto-discovery ignores.
