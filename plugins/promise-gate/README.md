# promise-gate

Once the user sets a target ("never stop", "do not stop until...", "until results", "do the whole
pool"), refuses to end a turn on a promise to continue ("I will continue with the next batch",
"running in the background, will land", "Continuing with Chapter 3", "Chapter Two follows in
the next message", "Reply continue for the rest") instead of doing the next batch.

Two hooks. `UserPromptSubmit` (`promise_gate.py --prompt`) reads each prompt and, when it sets a
target, writes a per-session marker with an excerpt of it. `Stop` (`promise_gate.py`) does
nothing in an unarmed session. In an armed one it reads the last 700 characters of the final
message, English and French, and blocks the stop when it ends on a promise; the reason names the
promise and the target, and tells the model to do the next batch now, or to hand the rest to a
subagent if the context runs short. A re-answer in the same stop chain is checked like the first
one; after three blocks in a row the turn ends, with a visible message and a `cap` row in the
ledger. A `SubagentStop` is ignored. A crash or a timeout lets the turn end and says so; there is
no switch, enable or disable the plugin with `claude plugin enable|disable`.

An unarmed session never hears about a promise alone: a recap that says "next batch tomorrow"
after the user said stop is legitimate.

## A job still running (every session, since 0.2.0)

On 2026-10-15 a turn ended on "Les tests sont en cours. Le reste de la livraison (commit, push,
synchro du hub) et le rapport complet attendent leur résultat." The model had put its own test
run in the background and stopped to wait for the notification; the user, who had set no target,
read the stop as the end and asked where the result was ("tu m'as pas donné le resultq... ca a
compact mid response ou quoi") before the notification came, five minutes later. The gate let it
through two ways: the session was unarmed, and no pattern read French for "still running".

So the Stop hook also reads the transcript. A shell job of the session (a Bash call whose result
carries a `backgroundTaskId`, launched with `run_in_background` or backgrounded on the way) that
started since the last prompt of the user, with no task-notification and no TaskStop for it yet,
is work in flight. When one is pending and the final message ends on a promise or on a deferral
to it ("sont en cours", "attendent leur résultat", "dès que les tests passent", "tourne en fond",
"still running", "waiting on its result", "once the tests finish"), the stop is blocked, armed
or not, and told to wait for the job inside the turn, in foreground calls of a few minutes, then
give the result. The same chain cap applies. A background subagent or a Monitor is never such a
job: the harness delivers those only by ending the turn. The deferral words are read only while a
job runs, since "en cours" alone names someone else's work as often as a promise.

Since 0.3.0 the Stop payload's `background_tasks` array decides which jobs still run when Claude
Code sends it (the docs warn the transcript may lag at Stop time). Measured on 2.1.319: a
backgrounded `sleep 120` arrives as `{"id": "bxpghd8nv", "type": "shell", "status": "running",
...}`, the id being the transcript's `backgroundTaskId`, and the array is `[]` once it exits. An
entry counts when it is of type `shell`, not in a terminal status, and the transcript shows its
launch since the last prompt of the user (a dev server started turns ago is not this turn's work).
With no `background_tasks` key, the transcript scan alone decides, as before.

Measured before shipping on 639 past transcripts of the author's sessions with a background job
(2026-08-15 to 2026-10-16): 3371 stops, 891 with a job of the session still running, 249 of them
ending on a deferral to it. Of those 249 jobs, 119 ended within 10 minutes of the stop, 67 within
30, 41 ran longer and 22 never reported. The user wrote again after 57 of the 249 stops (the
others were followed by notifications only, or by the end of the session), and 11 of those 57
prompts asked for the state of the work ("on en est ou?", "t'en est ou", "where are we now", "le
sub agent a fini?").

## A stall the user noticed first (every session, since 0.4.0)

Eight corrections from 2026-09-20 to 2026-11-11 share one shape: work the model had set going (a
scheduled line, a cron, a pop, a plan steering a goal) stalled or went down, and the user saw it
first ("pk elle sont down?", "never fail ciletly", "why did we auto fix? with a worker
listening?", "okay mais ca avance pas notre stuff right?"). The model then answered well,
measured and right, and left nothing behind, so the next stall was again the user's to see.

So a prompt that notices a stall (not advancing, down, silent failure, a missing auto fix) arms a
per-session `.stall` marker and gets a context line saying what the turn owes. The Stop hook
refuses to end that turn until the transcript since the prompt shows a watcher gesture by the main
thread: a Monitor, CronCreate, ScheduleWakeup or RemoteTrigger call; a Bash command that installs
or reads a watcher (a systemd timer, a crontab, a health witness, an autofix entry, a scheduler
row, a pop); or a write to a file named for watching (`*health*`, `*watch*`, `*.timer`). A gesture
spends the marker; the next ordinary prompt clears it; the chain cap applies.

What is no stall (0.5.0): the prompt is read with fenced code, backticked spans, double-quoted
spans and blockquote lines removed, so a handoff that quotes "the user noticed a stall" or names
`scripts/autofix.py` arms nothing, and `autofix` written as one word (a script's name) is not the
user asking for an auto fix. A task notification, a system reminder or a local command is no
prompt the user typed: it neither arms nor clears a marker.

Replayed on the real transcript of correction 0633: the answer to "ca avance pas notre stuff" is
blocked, and the turn seven minutes later that ended on "le résultat arrive dans ce fil" while
its own plan run was in the background (a deferral no pattern read before 0.4.0) is blocked too.
Measured on 794 distinct past prompts: 10 arm it, all progress or outage questions.

## Install

```
claude plugin marketplace add DylanMerigaud/trimwrit
claude plugin install promise-gate@trimwrit
```

## Configuration

Section `promise-gate` of `~/.claude/trimwrit-gates.json` (user) and
`<project>/.claude/trimwrit-gates.json` (project, wins key by key). An unknown key, a value of
the wrong type or a regex that does not compile is a loud crash, not a silent pass.

| key | default | meaning |
|---|---|---|
| `target_patterns_extra` | `[]` | regex alternatives that also count as a target set by the user |
| `promise_patterns_extra` | `[]` | regex alternatives that also count as a promise to continue |
| `reason_suffix` | `""` | text appended to the block reason |

The patterns are compiled case-insensitive and in verbose mode, like the built-in ones, so a
space inside an extra must be written `\s` and a `#` starts a comment to the end of that extra.
Each extra is wrapped in `(?:...)` and added as one more alternative.

## Python API

`promise_gate.promise_in(text, cfg=None) -> str | None` returns the promise ending `text` (its
last 700 characters), `promise_gate.deferral_in(text, cfg=None)` the promise or the deferral to
running work ending it, `promise_gate.target_in(prompt, cfg=None) -> bool` says whether a prompt
sets a target. With no `cfg` they load the settings of the current directory.
`promise_gate.pending_jobs(transcript_path, live=None) -> dict` maps the task id of each
background shell job still running to its description (`live` is
`promise_gate.live_shell_tasks(payload)`, the payload's running shell tasks or None); `promise_gate.JobScan` does the same one line at a time.

Utilities for a hand run: `python3 scripts/promise_gate.py --arm "text"`, `--disarm` and
`--print` act on the session named by `CLAUDE_SESSION_ID`.

## Where the ledgers land

Crashes, timeouts and, for a Stop gate, chain caps are appended to `hook-health.jsonl`; every
refusal is counted in `door-refusals.jsonl` next to it. By default both sit in the plugin's data
directory (`$CLAUDE_PLUGIN_DATA`, which Claude Code keeps at `~/.claude/plugins/data/promise-gate-trimwrit/`),
or in `~/.claude/trimwrit-gates/` when that variable is not set. The `trace` section of
`trimwrit-gates.json` moves them: `ledger` is the path of the health file (the refusal file stays
beside it), and `witness_module` is a Python file exposing `log(door, event, reason_class,
session_id)` that counts refusals instead of `door-refusals.jsonl`.

## Proof

`tests/gates/test_promise_gate.py` replays both hooks through their stdin contract: what arms and
what does not, the block-three-times-then-cap chain, a finished turn, `SubagentStop`, the
configuration extras and a bad one. The job check replays the turn of 2026-10-15 verbatim, in an
unarmed session with a compaction and another job's notification before it, and its releases: the
job's notification, a TaskStop, a new prompt, a result given while it runs, a subagent or a
Monitor in its place. The eval case `evals/turn-ends-on-a-promise` measures the
Stop hook with `claude plugin eval`: its prompt opens with the user's order not to stop (it arms
the gate in the plugin arm through `UserPromptSubmit`), the plugin arm and the baseline arm are
graded by a regex on the final message, and a pytest pins the grader against the phrasings the
gate refuses and the ones it allows.
