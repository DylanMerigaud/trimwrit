# preamble-gate

Refuses a `Write`, `Edit` or `MultiEdit` that grows an always-loaded file past its token budget.
A shrinking edit always passes, even on a file still over: refusing it would block the very edit
that brings the file back down.

`trimwrit integrate` already holds a rule to the budget in `.trimwrit/budget.json`, but most lines
reach a CLAUDE.md by hand. One user's global CLAUDE.md grew to 14.7k tokens, reread on every turn
of every session, and no write was ever refused. This gate holds the hand edit to the same
budget.

One `PreToolUse` hook (`preamble_gate.py`, matcher `Write|Edit|MultiEdit`). It acts on:

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
