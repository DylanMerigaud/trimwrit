# resume-on-api-error

When a turn ends on an API error, types a continue message into the GNU screen or tmux pane
hosting the session, after a delay; never on an authentication, billing or safeguards error.

- **StopFailure hook** (`resume_on_api_error.sh`, async): logs the failure, then a detached
  sleeper types the configured `text` plus ` (<category>)` into the screen window (`$STY`) or the
  tmux pane (`$TMUX` and `$TMUX_PANE`) hosting the session. A network still down fails again,
  fires the hook again and gets the next keystroke. `rate_limit` and `overloaded` wait
  `rate_limit_delay_s`, everything else `delay_s`.
- **What is resumed** is decided by `scripts/resume_policy.py`, which reads the error text from
  the session's transcript (the payload's `transcript_path`, else
  `~/.claude/projects/<cwd slug>/<session>.jsonl`): the payload's `error_details` comes in empty.
  - **Never**: `authentication_failed`, `billing*`, `insufficient*`, `invalid_api_key*`; a
    safeguards or usage-policy flag (`safeguards flagged`, `anthropic.com/legal/aup`, `Usage
    Policy`, reported as plain `invalid_request`); a request too large to send (`Prompt is too
    long`).
  - **Any other `invalid_request`**: once in a row per session.
  - **Transient** (`rate_limit`, `overloaded`, `server_error`, `unknown`, network): without end,
    the delay doubling per failure past five in a row, capped at one hour.
  - **Any other category**: loop breaker, at most three in a row. "In a row" means failures with
    no successful assistant row between them; without a readable transcript a per-session counter
    stands in.
  Every refusal is logged as `not resumed: <reason>`.
- **One pending sleeper per target**: a pidfile per screen window or tmux pane under
  `$CLAUDE_PLUGIN_DATA/resume-on-api-error/` (else `~/.claude/trimwrit-gates/resume-on-api-error/`),
  claimed atomically. While its sleeper lives, the next failure is logged as `not resumed: a
  sleeper is already pending` and left: keystrokes never stack. With neither screen nor tmux the failure is logged and the
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
detached command instead of running it, plus one real run with a fake `tmux` on `PATH` for the
pidfile. 2026-11-04: a safeguards flag got 991 resumes in one session and four sleepers stacked on
one pane; both are replayed there. No model-run eval: the hook is deterministic.
