# no-em-dash

Refuses a final message, a file write or a shell write that carries an em dash, an en dash, a
figure dash or a horizontal bar. The characters are configurable.

- **Stop hook** (`no_em_dash.py`): the final message is refused with exit 2 and a reason on
  stderr. Text inside a fenced block or backticks is exempt, so you can talk about the character.
  Re-answers in a stop chain are checked too, up to three blocks in a row, then the turn ends
  with a visible message and a `cap` row.
- **PreToolUse hook** (`no_em_dash_write.py`): denies `Write` (`content`), `Edit` (`new_string`),
  `MultiEdit` (every `edits[].new_string`), `NotebookEdit` (`new_source`) and `Bash` (heredoc
  bodies, and any command segment that redirects, tees or edits in place; a plain `grep` for the
  character is allowed). No code-fence exemption: write the codepoint instead (a JSON or YAML
  escape, `chr(0x2014)` in Python, `&mdash;` in HTML).

A crash or a timeout lets the action through and says so in a systemMessage. There is no switch:
enable or disable the plugin with `claude plugin enable|disable`.

## Install

```
claude plugin marketplace add DylanMerigaud/trimwrit
claude plugin install no-em-dash@trimwrit
```

## Configuration

Section `no-em-dash` of `~/.claude/trimwrit-gates.json` (user) and
`<project>/.claude/trimwrit-gates.json` (project, wins key by key). An unknown key is a loud
crash, not a silent pass.

| key | default | meaning |
|---|---|---|
| `codepoints` | `[8212, 8211, 8210, 8213]` | decimal codepoints refused (em, en, figure dash, horizontal bar) |

## Where the ledgers land

Crashes, timeouts and, for a Stop gate, chain caps are appended to `hook-health.jsonl`; every
refusal is counted in `door-refusals.jsonl` next to it. By default both sit in the plugin's data
directory (`$CLAUDE_PLUGIN_DATA`, which Claude Code keeps at `~/.claude/plugins/data/no-em-dash-trimwrit/`),
or in `~/.claude/trimwrit-gates/` when that variable is not set. The `trace` section of
`trimwrit-gates.json` moves them: `ledger` is the path of the health file (the refusal file stays
beside it), and `witness_module` is a Python file exposing `log(door, event, reason_class,
session_id)` that counts refusals instead of `door-refusals.jsonl`.

## Proof

`tests/gates/test_no_em_dash.py` replays both hooks through their stdin contract. The eval case
`evals/no-em-dash-in-prose` measures the Stop hook with `claude plugin eval`: a plugin arm and a
baseline arm on a prompt that pulls dashes in (a warm, funny tribute: the bare model wrote one
in 3 runs of 3), graded by a regex on the final message.
