# resume-on-api-error

When a turn ends on an API error, types a continue message into the GNU screen or tmux pane
hosting the session, after a delay; never on an authentication or billing error.

- **StopFailure hook** (`resume_on_api_error.sh`, async): logs the failure, then a detached
  sleeper types the configured `text` plus ` (<category>)` into the screen window (`$STY`) or the
  tmux pane (`$TMUX` and `$TMUX_PANE`) hosting the session. A network still down fails again,
  fires the hook again and gets the next keystroke. `rate_limit` and `overloaded` wait
  `rate_limit_delay_s`, everything else `delay_s`.
- **Never resumed**: `authentication_failed`, `billing*`, `insufficient*`, `invalid_api_key*`.
  Those are logged as `not resumed`. With neither screen nor tmux the failure is logged and the
  hook exits 0.
- A bad config file is reported on stderr and in the log, and the defaults apply. The hook never
  exits 2.

There is no switch: enable or disable the plugin with `claude plugin enable|disable`.

## Install

```
claude plugin marketplace add DylanMerigaud/trimwrit
claude plugin install resume-on-api-error@trimwrit
```

## Configuration

Section `resume-on-api-error` of `~/.claude/trimwrit-gates.json` (user) and
`<project>/.claude/trimwrit-gates.json` (project, wins key by key).

| key | default | meaning |
|---|---|---|
| `log` | `""` | log file, `~` expanded; empty means `$CLAUDE_PLUGIN_DATA/api-failures.log`, else `~/.claude/trimwrit-gates/api-failures.log` |
| `vim_mode` | `false` | when the prompt is a vim editor: send Escape, then `i` before the text |
| `delay_s` | `60` | seconds to wait before typing |
| `rate_limit_delay_s` | `120` | seconds to wait on `rate_limit` and `overloaded` |
| `text` | `continue: the previous turn ended on an API error; resume exactly where you were and retry what failed` | the message typed, followed by ` (<category>)` |

## Proof

`tests/gates/test_resume_on_api_error.py` runs the script with `--dry-run`, which prints the
detached command instead of running it. No model-run eval: the hook is deterministic.
