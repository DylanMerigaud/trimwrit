# oneshot-gate

A `claude -p` session that disables every tool (`--tools ""`) is a one-shot answer: a judge, a
classifier, a grader. Started without `--bare` or `--safe-mode`, it still loads everything the
directory it runs from carries (CLAUDE.md files, the memory index, skills, the SessionStart
payload of every hook) and sends it with the question. This gate refuses such a session before its
first request, so the refusal itself costs nothing.

## Why it exists

On 2026-11-02 a library harness graded READMEs through a judge that spawned
`claude -p <prompt> --tools ""` from inside a repository. Each call carried about 55k tokens of
harness to answer PASS or FAIL. 2270 calls in five hours took 72 percent of the night's usage and
spent two accounts' weekly windows. The same call with `--safe-mode` carries 4.8k tokens.

## What it does

A `UserPromptSubmit` hook. Hooks only run in a session that loaded the harness (`--bare` and
`--safe-mode` skip them), and this one runs before the model is called: a refused prompt reports
0 tokens, 0 turns and 0 ms of API time. The hook reads the argv of the claude process that
started it (`/proc` on Linux, `ps` elsewhere) and refuses when it holds `-p` or `--print` and an
empty `--tools`. The refusal is one JSON line on stderr:

```json
{"refused": "oneshot-not-lean", "why": "...", "fix": "run it with --safe-mode ... or --bare ...", "flags": ["..."]}
```

Not refused: interactive sessions, print sessions that keep any tool (agents), and sessions whose
argv cannot be read (traced `unreadable`).

A caller sees a SUCCESS envelope with `num_turns: 0` and the hook's text as `result`; trimwrit's
runner treats that as a run that never reached the model, never as a verdict.

## Install

At user scope, and at project scope in any repository whose code spawns
`claude -p --setting-sources project,local` (user plugins do not load there):

```
claude plugin install oneshot-gate@trimwrit
```

No configuration, no switch.
