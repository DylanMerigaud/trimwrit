# preamble-gate

Refuses a `Write`, `Edit` or `MultiEdit` that grows an always-loaded file past its token budget.
A shrinking edit always passes, even on a file still over: refusing it would block the very edit
that brings the file back down.

`trimwrit integrate` already holds a rule to the budget in `.trimwrit/budget.json`, but most lines
reach a CLAUDE.md by hand. One user's global CLAUDE.md grew to 14.7k tokens, reread on every turn
of every session, and no write was ever refused. This gate holds the hand edit to the same
budget.

`preamble_gate.py` runs on `PreToolUse` (`Write|Edit|MultiEdit|Bash`), `PostToolUse` (`Bash`) and `SessionStart`. It acts on:

- any file named `CLAUDE.md` (the project one, a nested one, `~/.claude/CLAUDE.md`);
- `~/.claude/rules/*.md`, one piece: every file of the directory counts together;
- a `MEMORY.md` inside a `memory` directory (Claude Code's per-project memory index).

It computes the file's text after the call (`Write`: the content; `Edit`: `old_string` to
`new_string` on the current file, every occurrence with `replace_all`; `MultiEdit`: each edit in
order), measures it with trimwrit's estimate (HTML comments stripped, UTF-8 bytes / 2.35), and
denies when the after size is over the budget AND larger than the before size. An `old_string`
the file does not hold lets the call through: the tool itself will fail. The refusal names the
file, both sizes, the budget and its source, and the route: a situational rule goes to the
SKILL.md of its situation, its story to the repository's history doc, one line naming the door
stays; `trimwrit relocate <rule> --to <file>` moves a rule with its marker.

## The shell door (0.2.0)

Most memory lines never went through the Edit tool: a `cat >> $M/MEMORY.md <<'EOF'` took one
user's memory index over its 4000 token budget twice in one week, and no hook measured it.

- **PreToolUse Bash**: a command that writes an always-loaded file with a budget is refused and
  routed to the Edit tool, which measures. Writes recognised: a redirection (`>`, `>>`, `&>`,
  `>|`), `tee`, an in-place `sed -i` or `perl -i`, the destination of `cp`, `mv` or `install`.
  Here-document bodies are skipped, `NAME=value` assignments and `cd` earlier in the same
  command are followed, and a path behind a variable the hook cannot see is matched by name
  (`MEMORY.md`, `CLAUDE.md`, a `.md` under `.claude/rules/`). Reads always pass.
- **PostToolUse Bash**: the Pre hook records the stat of every always-loaded file (the user
  CLAUDE.md, the rules, every project's memory index, each CLAUDE.md from the cwd up); after
  the command, a file whose stat changed and now stands over its budget is reported to the model
  (`decision: block`, the command already ran). With no snapshot, a file over budget modified in
  the last 120 s is reported, and the message says so.

Left open: a program that writes the file without naming it on the command line is reported
after the write, never refused before it. Refusals count as `shell_write`, reports as
`over_after_shell`.

## The session start (0.3.0)

The gate refuses growth only, and a shrinking edit passes even when it stops over the budget. A
memory index that went over before the shell door existed was trimmed by 28 tokens and stayed
over, and the only session told was an unrelated one that merely waited during the write.

- **SessionStart** (`startup`, `clear`, `compact`): every always-loaded file of this session that
  stands over its budget is given to the session as context, with the instruction to bring it
  back under in this session. The files: the user CLAUDE.md, the rules, every CLAUDE.md from the
  cwd up, and the session's OWN memory index (the key of its cwd, of the git top level above it,
  and of the main checkout when that top level is a linked worktree). Counted as `over_at_start`.
- The PostToolUse report covers the same files only. Another project's memory index is told at
  the start of that project's next session, where it loads and where its context is.

A crash or a timeout lets the edit through and says so in a systemMessage. There is no switch:
enable or disable the plugin with `claude plugin enable|disable`.

## Install

```
claude plugin marketplace add DylanMerigaud/trimwrit
claude plugin install preamble-gate@trimwrit
```

## Budgets

Two layers, the project winning on the same key. No budget found: the edit passes.

- **Project**: the nearest `.trimwrit/budget.json` above the edited file (above
  `CLAUDE_PROJECT_DIR` or the session's cwd for a `MEMORY.md` or a user file), keyed exactly as
  `trimwrit preamble` keys it: `CLAUDE.md`, `~/.claude/CLAUDE.md`, `~/.claude/rules`,
  `MEMORY.md`, or a path relative to that root (`outreach/CLAUDE.md`).
- **User**: the `preamble_budgets` key of section `preamble-gate` of
  `~/.claude/trimwrit-gates.json`. There `CLAUDE.md` covers every project's CLAUDE.md that the
  project layer does not budget.

```json
{"preamble-gate": {"preamble_budgets": {
  "~/.claude/CLAUDE.md": 5000, "~/.claude/rules": 1700, "MEMORY.md": 4000, "CLAUDE.md": 15000}}}
```

| key | default | meaning |
|---|---|---|
| `preamble_budgets` | `{}` | token budget per piece name or path, the user layer of the budgets |

An unknown key, or a budget that is not a whole number, is a loud crash, not a silent pass.

## Where the ledgers land

Crashes and timeouts are appended to `hook-health.jsonl`; every refusal is counted in
`door-refusals.jsonl` next to it (door `preamble-gate`, reason class `over_budget`). By default
both sit in the plugin's data directory (`$CLAUDE_PLUGIN_DATA`), or in `~/.claude/trimwrit-gates/`
when that variable is not set. The `trace` section of `trimwrit-gates.json` moves them.

## Proof

`tests/gates/test_preamble_gate.py` replays the hook through its stdin contract: a Write over
budget denied, a shrinking edit allowed while over, Edit and MultiEdit computed, a file that is
not always loaded ignored, the rules directory summed, the two layers' precedence, no budget
allowing, and the token estimate held equal to `trimwrit.preamble.tokens`.
