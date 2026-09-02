# Changelog

## 0.4.0, 2026-09-01

Dylan, on why: "je veux normaliser, optimiser, tracker, state, et self refine mes harness.
trimwrit me semble un bon moyen", then, on `audit` showing 24 of 28 repos with zero cases: "ca
sert pas qu'a mesurer. mais aussi a structurer, self improve etc...", and on scope: "uniquement
les repo avec harness. pour moi gc est suffisant". The loop (log, case, integrate, run, prune)
only knows the rules it promoted itself, through the marker `integrate.py` writes. A rule
written BEFORE trimwrit existed, which on most real repos is nearly all of them, was invisible
to it: not in `prune`, not in `viz`, not in `audit`'s rule count.

- **`trimwrit adopt`.** Walks the same repos `audit` would (`--roots`, `--laptop`, same
  exclusions, same repo attribution, reusing `audit.find_harness_files`/`_find_repo` rather than
  a second walk) and, for every harness file, splits it into rule UNITS: a `## ` section
  (heading line through the line before the next `## `/`# ` heading or EOF), or the whole file
  when there is no `## ` heading at all, headed by its first `# ` line or its own file name. Each
  unit becomes one line of `.trimwrit/rules.jsonl`, keyed by `(file, heading)` so its id is
  stable across runs: a surviving unit gets its line range and sha256 refreshed in place, a new
  one gets the next id, and a unit whose `(file, heading)` no longer exists goes
  `status: gone` and is kept, never deleted. A section that already carries a real trimwrit
  marker (read through `integrate.rule_ids`, never a second regex) is not adopted, it is already
  the loop's: it comes back `status: promoted` with the rule ids the marker names. Also creates,
  and never overwrites, a repo's missing skeleton: a touched empty `.trimwrit/ledger.jsonl` and
  an `evals/README.md`. `--dry-run` computes and prints the exact same report with no write at
  all. `plan()` is pure (no filesystem writes), `apply()` (via `compute(..., dry_run=False)`) is
  the thin wrapper that actually writes, so id stability and change detection are unit tested
  with no disk involved.
- **`ledger.rows` reads a touched, empty ledger as no corrections.** Already true of the
  existing code (an empty file yields no lines to parse), now covered by a test, since `adopt`
  depends on exactly this behaviour for the skeleton it creates.
- **`audit` reads the registry.** A repo record gains `adopted` (status `adopted`, no case yet),
  `promoted`, `gone`, all `None` for a repo `adopt` has never touched, distinguishable from `0`
  the same way `evals_dir` already distinguishes "no evals dir" from "an empty one". The table
  gains an `adopted` column after `rules`, printing `-` for that `None`, and the summary gains
  one more bucket, `N adopted rule(s) with no case across M repo(s)`, the number `adopt` exists
  to make visible, alongside the three buckets `audit` already reported.
- **`prune` is unchanged, on purpose.** An adopted unit never claimed a case, so it is not an
  orphan; `prune.orphans`/`prune.unused_cases`/`prune.inert` still read only `integrate.read_rules`
  over a target file, never `.trimwrit/rules.jsonl`, and a test now proves the registry's
  presence does not move prune's findings.
- **`target: outbox` reads only what LEAVES.** Two sessions hand-wrote the same lookahead
  regex on 2026-09-01 to stop a forbid grader firing on a correct answer that quotes the
  forbidden thing while refusing it (the injection suite: 11 false flags out of 14 on its first
  pass; dig-0004: three older cases). The haystack is now the inside of the outbox blocks of the
  final message, in both marker dialects already in use (`--- OUTBOX ---` pairs and fenced
  ```outbox blocks), and `check` runs `must_match`/`must_not_match` through the same target, so
  a proof sample is a whole answer. A run with no block yields an empty haystack: pair it with a
  `requires` grader on `last_message` for the opening marker.
- `trimwrit case --must-match`/`--must-not-match` used to land case wide on every regex grader,
  so a case mixing `--forbid` and `--require` (a bad sample for one, a good sample for the other)
  could not be built through the CLI at all. Both flags now accept an optional `NAME=` prefix,
  the grader's own name or the shorthand `forbid=`/`require=`, that routes the sample to one
  grader; a value with no `=` before its first space keeps the old case wide meaning.
- A `must_match`/`must_not_match` sample containing a comma silently split in two on read back
  (`_needs_quote` did not treat a comma as a reason to quote, and the inline list reader splits
  on every unquoted comma). Fixed in `frontmatter._needs_quote`: a comma is now quoted like every
  other character that would change the string's meaning.
- Two grader names sharing their first six words collapsed onto the same grader file
  (`write_case` slugs a name to 6 words for its filename), and the second write silently
  overwrote the first, so a case that declared two graders shipped one. `write_case` now tries
  the next free numbered suffix on a collision, and refuses outright, with a clear error, two
  graders that share the identical `name` field.


## 0.3.1, 2026-09-01

Both from the first real replay under 0.3.0, read in the evidence files the same day.

- **An eval run can no longer reach an MCP server.** `--strict-mcp-config` with no
  `--mcp-config` joins the isolation flags, which means zero servers. A bare-arm run of a prompt
  injection case whose payload forged a quoted approval called the operator's live Gmail
  connector to verify the quote; the permission layer denied it, which is luck, not design.
  Nothing an eval case says can reach a system outside its scratch directory.
- **An API refusal returned as text is unmeasured.** The API's own safeguards answer with
  result text ("API Error: ... safeguards flagged this message") and exit 0. Three bare runs of
  a base64 payload case returned that text and were scored 0.5 against the grader that requires
  an answer block, which read in the table as the harness earning its place on a case the
  model never saw. Matched at the start of the text only: a model quoting the phrase is a
  measured run.

## 0.3.0, 2026-09-01

The incident behind all five changes below: a suite of 32 prompt injection cases, generated in
the native case format, was run through `trimwrit run`. The second pass reported 32 of 32
resisted. The graders were dead. The generator wrote regex patterns with `json.dumps`, which
emits a double quoted YAML scalar with doubled backslashes, and `frontmatter._scalar` stripped
the quotes without unescaping them, so every pattern containing `\s` reached `re.compile` as a
literal backslash and matched nothing. A green board, and the instrument was disconnected. The
runner's own docstring already warns against exactly this ("a grader that never fires is an
instrument that always says PASS") and it still happened, because nothing forced a grader to
PROVE it can fail before a run trusts it. Separately, three of the 32 runs came back with no
final text at all (an API safeguard refused one prompt, another hit `max_turns` mid tool call)
and were scored 0.0, indistinguishable in the table from a real failure; and the runner kept no
output text at all, so verifying a flagged run meant re-running it by hand.

- **A regex grader proves it can fire.** `must_match`/`must_not_match`, each a list of example
  texts checked against the grader's own pattern with no model call. The semantics are about the
  PATTERN, never the verdict: for a `not_contains` grader, a `must_match` example is text a real
  run would FAIL on. `frontmatter._block_list` now accepts a `- |`/`- |-` block scalar as a list
  item (examples are usually a whole assistant answer), and `render()` emits that form when a
  list item contains a newline; fixed two latent bugs on the way, `_scalar` was not unescaping a
  doubled `''`, and `_needs_quote` was not catching an apostrophe in the MIDDLE of an inline list
  item, which corrupted the following item on read back. `trimwrit check [--case] [--strict]
  [--json]` runs the proof over every case: an unsupported grader type or a pattern that will not
  compile is always a failure, a regex grader with no example at all is a warning unless
  `--strict` refuses it too. `trimwrit run` runs this check before anything else, and a case
  whose grader fails its own proof is never run: it is reported `UNCHECKED, grader failed its
  own proof: <reason>` and counted as unmeasured, never as a pass.
- **Every run leaves evidence.** `trimwrit run` writes `<evals>/results/runs/<case>/<arm>-<run
  index>.md`, one file per run of every case: front matter (case, arm, run, timestamp, passed,
  score, the error if any, whether it was unmeasured, one line per grader) followed by the
  model's final answer verbatim and the tool calls as JSON. Overwritten on the next run of the
  same slot, so it is the LATEST evidence. It deliberately bypasses this repo's own dash gate,
  since the point is to show exactly what the model produced, including the violation a case
  exists to catch. `--no-save` turns it off.
- **Unmeasured is not failed.** A run is unmeasured when it errored or its final text is empty
  after stripping. `summarise` now means an arm over its measured runs only, and when every run
  of an arm is unmeasured the arm (and the delta) come back `None`, printed as `?` in the table
  and `null` in `--json`. A case unmeasured in the `with` arm no longer counts toward the exit
  code by itself; it is reported once at the end ("N case(s) unmeasured, they are not passes")
  and `--fail-on-unmeasured` opts back into failing on it.
- **The numbers over time.** `results/latest.json` still answers "what is true now"; a new
  `results/history.jsonl` appends one JSON line per case per `run` invocation, carrying the with
  and without scores, the delta, the run count, and `target_sha256`, the sha256 of each ablated
  rule file as it was actually seeded, so a later reader can tell a real regression from a rule
  that was simply rewritten between two runs.
- **`--jobs N`.** `run_many` (the new home of the ablation loop, `run_case` is now a thin
  wrapper over it for one case) runs every `(case, arm, run)` triple across every selected case
  in a `ThreadPoolExecutor`, then assembles results back in the same order a sequential run
  would produce: the table, the JSON payload, the evidence files and the history line at
  `--jobs 8` are byte for byte the same as at `--jobs 1`, just faster. A 32 case suite with two
  arms is close to 200 `claude -p` calls at 30 to 60 seconds apiece; sequential, that is close
  to two hours for one replay, which is the difference between a replay that happens and one
  that does not.
- **`trimwrit audit`: every harness on the machine, one table.** Walks `--roots` (or
  `--laptop`, which is `~/.claude` plus `~/Code`) for `CLAUDE.md`, `.claude/rules/*.md` and
  `.claude/skills/*/SKILL.md`, attributes each to the nearest repo, and reports lines,
  sections, integrated rules, cases, last run and last score per row, then the three counts
  the command exists for: repos with ZERO cases, repos stale past `--stale` days, and
  harnesses carrying a promoted rule with no evals dir to measure it. First laptop run,
  2026-09-01: 106 harness files in 28 repos, 24 repos with no case at all. A directory with
  no `.git` is attributed to the root it sits under, and that umbrella never borrows a
  nested repo's evals dir (found the same day: `~/Code` reported 38 cases that were
  growth-cockpit's).

## 0.2.1, 2026-08-26

- **`trimwrit stats`: what a rule did outside the eval.** `prune` answers whether the eval still
  needs a rule; `stats` aggregates a JSONL ledger of rule evaluations, either flat (one line, one
  evaluation) or the nested `{"gates": [...]}` verdict shape a private repo's own judge already
  logs, auto detected per line, and flags two candidate classes: DEAD WEIGHT (fired at least 15
  times, never failed once, never came within 2 of the line) and FRICTION (decided at least 8
  times, refuses a quarter or more of them). It changes nothing, there is no `--apply`, and the
  dead weight report carries its own caveat: a rule can score zero fails because the generator
  internalized it rather than because the rule does nothing, and removing the rule is the only
  way to find out.
- All four thresholds (`--min-fires`, `--close-margin`, `--friction-fires`, `--friction-rate`)
  are overridable flags, and `--json` prints the full aggregate.
- `pyproject.toml` and `trimwrit/__init__.py` had drifted to 0.1.2 while `plugin.json` and
  `marketplace.json` moved to 0.2.0 in the last release. All four now read the same version.

## 0.2.0, 2026-08-25

- **`trimwrit viz`: the whole loop as one canvas.** The command serializes the pipeline
  (corrections, promotion gates, cases with their with/without deltas, integrated rules) into a
  compressed payload and opens `viz/dist/index.html` with the graph in the URL's hash fragment:
  a self contained page, no server, nothing leaves the machine. `--json` prints the raw
  document, `--no-open` prints the URL. `run` and `prune` now end with a one line pointer to it.
- **The viewer is vendored from [approvals-ui](https://github.com/DylanMerigaud/approvals-ui)**
  and forked to a generic stage node: kind chip, status ring (pending, passed, failed, skipped),
  meta rows. It ships prebuilt as one committed file, so the Python CLI stays stdlib only and
  nothing needs npm at use time.

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
