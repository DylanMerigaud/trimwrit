# rule-gate

Refuses to end a turn that announces a new behavioural rule ("from now on", "never again", "every
deploy must", "rule:") without naming the code that enforces it. A rule that is only a sentence
survives until the moment it costs something, because the reader who is supposed to enforce it is
also the one deciding whether to bother. The rule holds when it ships as a hook, a script or a
test, and the sentence names that door.

One `Stop` hook (`rule_gate.py`). It reads the last 1600 characters of the final message,
English and French, for a rule marker: "from now on", "desormais", "never again", "plus jamais",
"every X must", "chaque X doit", a `rule:` label, a heading starting with "Rule", or "always"
applied to a process of ours ("I will always run the gate"). When it finds one and the WHOLE
message names no enforcing path, it blocks the stop and asks for the file that refuses when the
rule is broken. A path counts when it sits under one of the configured prefixes (`scripts/x.py`),
when it is the bare filename of a hook command in `<project>/.claude/settings.json`, or when it is
the name of a plugin enabled in that file or in `~/.claude/settings.json` ("the claim-gate plugin
enforces it").

Not counted as an announcement: a quoted verbatim (a line starting with `> `, a span wrapped in
`*"..."*`), and a sentence whose subject is a third party's own rule ("LinkedIn caps invitations
at 100 a day"). A re-answer in the same stop chain is checked like the first one; after three
blocks in a row the turn ends, with a visible message and a `cap` row in the ledger. A
`SubagentStop` is ignored. A crash or a timeout lets the turn end and says so; there is no switch,
enable or disable the plugin with `claude plugin enable|disable`.

## Install

```
claude plugin marketplace add DylanMerigaud/trimwrit
claude plugin install rule-gate@trimwrit
```

## Configuration

Section `rule-gate` of `~/.claude/trimwrit-gates.json` (user) and
`<project>/.claude/trimwrit-gates.json` (project, wins key by key). An unknown key, a value of
the wrong type or a regex that does not compile is a loud crash, not a silent pass.

| key | default | meaning |
|---|---|---|
| `phrase_patterns_extra` | `[]` | regex alternatives that also count as a rule marker |
| `vendor_names` | LinkedIn, X, Twitter, Google, GitHub, Vercel, Stripe, Anthropic, OpenAI, Meta, TikTok, Instagram, YouTube, Cloudflare, Apple, Microsoft, Slack, AWS | third parties whose own rules are not ours |
| `vendor_names_extra` | `[]` | more third parties, added to the list above |
| `enforcer_path_prefixes` | `.claude`, `.github`, `hooks`, `plugins`, `scripts`, `tests` | directories an enforcing path lives under |
| `reason_suffix` | `""` | text appended to the block reason |

`phrase_patterns_extra` is compiled case-insensitive and in verbose mode, like the built-in
markers, so a space inside an extra must be written `\s` and a `#` starts a comment to the end
of that extra. Each extra is wrapped in `(?:...)` and added as one more alternative.

## Python API

`rule_gate.rule_without_door(text, root, cfg=None) -> str | None` returns the matched marker when
`text` announces a rule with no door, else None. `root` is the project root whose
`.claude/settings.json` names hooks and plugins (or None). With no `cfg` it loads the settings of
the current directory.

## Where the ledgers land

Crashes, timeouts and, for a Stop gate, chain caps are appended to `hook-health.jsonl`; every
refusal is counted in `door-refusals.jsonl` next to it. By default both sit in the plugin's data
directory (`$CLAUDE_PLUGIN_DATA`, which Claude Code keeps at `~/.claude/plugins/data/rule-gate-trimwrit/`),
or in `~/.claude/trimwrit-gates/` when that variable is not set. The `trace` section of
`trimwrit-gates.json` moves them: `ledger` is the path of the health file (the refusal file stays
beside it), and `witness_module` is a Python file exposing `log(door, event, reason_class,
session_id)` that counts refusals instead of `door-refusals.jsonl`.

## Proof

`tests/gates/test_rule_gate.py` replays the hook through its stdin contract: a rule with no door
blocks, the same message naming a path, a hook file or an enabled plugin passes, a quote and a
vendor's rule pass, the chain cap, `SubagentStop`, and the configuration keys. The eval case
`evals/rule-announced-without-its-door` measures the Stop hook with `claude plugin eval`: a
session that wrote a standing order into a notes file and a CLAUDE.md and has no code for it. The
plugin arm and the baseline arm are graded by a regex on the final message (a marker with no path
named, and the opposite), and a pytest pins the grader against the messages the gate refuses and
the ones it allows.
