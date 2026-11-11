#!/usr/bin/env python3
"""Stop hook: a turn does not end on a promise to continue once the user set a target, nor, in
any session, on a deferral to its own background job still running.

Why a hook and not a sentence in a CLAUDE.md: six turns in a row of one night ended on "je
continue sur le lot suivant" and stopped, and the user had to write "ne t'arrete pas" three
times. The rule ("a turn never ends on a sentence naming work still to do") scored the same with
and without it on its eval case, because a one-prompt replay cannot reproduce a turn that ENDS:
the failure only exists at the boundary of a turn, and the only thing that runs there is a Stop
hook.

What arms it: only a message from the user that sets a target ("ne t'arrete pas", "never stop",
"until results", "jusqu'a N candidats", "fait tout", "tout le pool d'un coup", "do not stop").
The UserPromptSubmit entry (`--prompt`) reads the prompt and writes a per-session marker carrying
an excerpt. A recap that says "next batch tomorrow" after the user said stop is legitimate, and a
guard that fires there gets uninstalled, so an unarmed session hears from it only in the case
below.

What it checks: the last TAIL_CHARS of the final message (`last_assistant_message` from the
payload, the transcript only when it lacks one) against the promise patterns ("je continue", "je
passe au lot suivant", "next batch", "I'll pick it up", "running in the background, will land").
A match while armed blocks the stop, with a reason naming the target.

A job still running, in EVERY session, armed or not (2026-10-16, after the relapse of 2026-10-15:
the turn ended on "Les tests sont en cours. Le reste de la livraison (commit, push, synchro du
hub) et le rapport complet attendent leur résultat.", and the user: "tu m'as pas donné le
resultq... ca a compact mid response ou quoi"). That turn got past this gate two ways: the
session was unarmed (the user had asked for four fixes and set no target), and no pattern read
French for "still running". The model had put its own test run in the background and ended the
turn to wait for the notification, which came five minutes later, after the user had already
asked where the result was. So the gate also
reads the transcript: a shell job of this session (a Bash call whose result carries a
backgroundTaskId, explicit `run_in_background` or backgrounded on the way) launched since the last
prompt of the user, with no task-notification and no TaskStop for it yet, is work in flight whose
result the turn could still give. When one is pending and the final message ends on a promise or
on a deferral to it (PENDING_SRC: "sont en cours", "attendent leur résultat", "dès que les tests
passent", "still running", "once the tests finish"), the stop is blocked and told to wait for the
job inside the turn. A background subagent or a Monitor is not such a job: the harness can only
deliver those by ending the turn, and a turn that ends on them is the harness's own way to wait.

Which jobs are still running (2026-11-08, harness item promise-gate-read-background-tasks):
the Stop payload's `background_tasks` array decides when it is present. Claude Code builds it from
its task registry for exactly this check, and the docs warn the transcript may lag at Stop time.
Measured on 2.1.319 the same day: a `sleep 120` put in the background gave a Stop payload with
`{"id": "bxpghd8nv", "type": "shell", "status": "running", "command": ..., "description": ...}`,
its id the very `backgroundTaskId` of the transcript, and `[]` once it had exited. So a job is
pending when its entry is of type `shell` and not in a terminal status; a subagent, a Monitor or a
workflow is the harness's own wait, as above. The array carries no launch time, so the transcript
still scopes it: an entry counts only when the transcript shows its launch since the last prompt
of the user (a dev server started three turns ago, "the server is still running", is no deferral
of this turn). With no `background_tasks` key (an older Claude Code, the registry unreachable),
the transcript alone decides, as before (JobScan).

A stall the user noticed first, in EVERY session (0.4.0, lesson s:dispatched-work-left-unwatched,
eight corrections from 2026-09-20 to 2026-11-11, the newest "okay mais ca avance pas notre
stuff right?": a plan steered toward a goal for a day, no gear had dropped in 48 hours, and the
user saw it before the model did). The prompt of a user who notices work that stalled, went down
or failed silently (STALL_SRC: "ca avance pas", "verifier que tout avance", "pk elle sont down",
"never fail silently", "with a worker listening?", "auto fix") arms a per-session `.stall`
marker, and the prompt hook tells the model what the turn owes. The Stop hook then refuses to end
the turn until the transcript, since that prompt, shows a WATCHER gesture: a Monitor, a
CronCreate, a ScheduleWakeup or a RemoteTrigger call, a Bash command that installs or reads a
watcher (WATCH_CMD_SRC: a systemd timer, a crontab, a health witness, an autofix entry, a
scheduler row, a pop), or a write to a file whose name says it watches (WATCH_FILE_SRC). An
answer alone, however well measured, leaves the next stall to the user again. The next prompt
that is no stall clears the marker.

The bounds: the re-answer of a stop chain is checked like the first answer; the gate lets go after
CHAIN_CAP blocks in a row (a `cap` row in the ledger and a systemMessage). A crash or a timeout
lets the turn end and is reported loudly. No environment variable and no configuration key
turns the gate off. A SubagentStop is another event and is ignored.

Configuration (section `promise-gate` of trimwrit-gates.json): `target_patterns_extra` and
`promise_patterns_extra` add regex alternatives (compiled case-insensitive and verbose, so a
space inside an extra is written `\\s`), `reason_suffix` is appended to the refusal.

Outputs:
    exit 0 with nothing on stdout                       the session ends normally
    exit 0 with {"decision": "block", "reason": ...}    the session continues with this message

Utilities:
    python3 promise_gate.py --print        what it would say now, blocking nothing
    python3 promise_gate.py --arm "text"   arm this session with a target excerpt
    python3 promise_gate.py --disarm       disarm this session
    python3 promise_gate.py --prompt       the UserPromptSubmit hook, JSON on stdin
"""
import json
import os
import re
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_ROOT)
from gatekit import config, trace, transcript  # noqa: E402

HOOK = "stop_promise_gate.py"
TIMEOUT_S = 8
TAIL_CHARS = 700
ARM_SUFFIX = ".promise"
EXCERPT_CHARS = 160

# A target set by the user: the message that makes stopping wrong.
TARGET_SRC = r"""
    (?:ne\s+)?t'?\s?arr[eê]te[sz]?\s+pas          # "ne t'arrete pas", "t'arrete pas", "T'ARRETE PAS"
  | never\s+stop
  | (?:don'?t|do\s+not)\s+stop
  | untill?\s+results
  | jusqu'?[aà]\s+(?:ce\s+que|avoir|\d)           # "jusqu'a avoir 5", "jusqu'a ce que"
  | fai[st]\s+tout\b
  | tout\s+le\s+pool
  | d'un\s+coup
  | go\s+autonome
"""

# The promise, kept in sync by hand with the forbid grader of the eval case.
PROMISE_SRC = r"""
    je\s+continue
  | je\s+passe\s+au\s+(?:lot|batch|prochain)
  | prochain\s+lot
  | next\s+batch
  | I(?:['\u2019]ll|\s+will)\s+(?:continue|keep\s+going|pick\s+(?:it\s+)?up)
  | to\s+be\s+continued
  | je\s+reprends
  | running\s+in\s+(?:the\s+)?background
  | will\s+land
  | not\s+stopping\s+here
  | I(?:['\u2019]ll|\s+will)\s+(?:report|follow\s+up)\s+(?:back|later)
  | I(?:['\u2019]ll|\s+will)\s+(?:draft|write|do|finish|score|process|handle|cover)\s+(?:the\s+)?(?:remaining|rest)\b
  | I(?:['\u2019]m|\s+am)\s+continuing\b                     # "I'm continuing in sequence"
  | (?:^|(?<=[\n.!?*_]))\s*continuing\s+(?:with|in|on|to)\b   # "Continuing with Chapter 3."
  | \b(?:follows|comes)\s+next\b                    # "Ch. 2 comes next"
  | \bup\s+next\b
  | \bfollows\s*[.)\]*]                             # "Chapter Two (the papers) follows."
  | \bcontinues\s+in\s+\S                            # "continues in /manuscript/02.md"
  | in\s+(?:the|my)\s+next\s+(?:message|reply|turn|response)
  | (?:reply|say|type|answer)\s+\W{0,2}(?:continue|go|next|more)\b   # "Reply "continue" for Chapter Two"
  | dans\s+(?:le|mon)\s+prochain\s+message
  | la\s+suite\s+(?:arrive|suit|dans)
"""

# A deferral of the turn's result to work still running. Judged only while a shell job of the
# session is pending (see pending_jobs): alone, "en cours" or "still running" describes the
# work of someone else as often as a promise.
PENDING_SRC = r"""
    \b(?:sont|est|reste(?:nt)?)\s+(?:encore\s+|toujours\s+)?en\s+cours\b      # "Les tests sont en cours"
  | \btourne(?:nt)?\s+(?:encore\s+|toujours\s+)?en\s+(?:fond|arri[eè]re[\s-]plan)\b
  | \b(?:attend(?:ent)?|en\s+attendant)\s+(?:leur|son|sa|le|la|les)\s+(?:r[ée]sultats?|fin|retour|verdict)\b
  | \bd[eè]s\s+qu(?:e\s|')[^.\n]{0,60}?\b(?:fini[st]?|finissent|termin\w*|passe(?:nt)?\b|revien\w*)
  | \bje\s+reviens\s+(?:vers\s+toi\s+)?(?:avec|quand|d[eè]s)\b
  | \bstill\s+(?:running|in\s+progress)\b
  | \b(?:waiting|wait)\s+(?:on|for)\s+(?:the|their|its)\s+(?:results?|outputs?|tests?|run|build|verdict)\b
  | \bonce\s+(?:it|they|the\s+(?:tests?|suites?|build|run|checks?|job))\s+(?:finish(?:es)?|completes?|pass(?:es)?|lands?|comes?\s+back|exits?)\b
  | \br[ée]sultat\s+arrive\b                                    # "le resultat arrive dans ce fil"
  | \b[àa]\s+(?:la\s+fin\s+du|l'arriv[ée]e\s+du)\s+(?:calcul|run|test|job|scan|plan|lot)\b
"""

# The user noticed a stall before the model did: work that does not advance, went down, failed
# silently, or lacks its fixer. Each alternative replays a verbatim of the lesson
# s:dispatched-work-left-unwatched (tests/gates/test_promise_gate.py STALLS).
STALL_SRC = r"""
    \b(?:[cç]a|rien|tout|stuff|on|it)\s+(?:n['’]?\s*)?avances?\b   # "ca avance pas", "tout avances"
  | \b(?:sont|est|soit|is|are|was|were|went|goes|it['’]?s|c['’]?est)\s+(?:encore\s+|still\s+)?down\b
  | \b(?:silent(?:ly)?|cilet(?:ly)?|silencieu\w*)\b              # "never fail ciletly"
  | \bsoit\s+sil[ae]nce\b                                        # "soit silance"
  | \bauto\s*-?\s*fix                                             # "a working auto fix by default"
  | \bworker\s+listening\b|\blisten\s+actif\b
  | \b(?:not|isn['’]?t|aren['’]?t|nothing(?:'s|\s+is)?)\s+(?:moving|progressing|advancing|happening)\b
  | \bstall(?:ed|s|ing)?\b
"""

# A Bash command that installs a watcher or reads the one already there.
WATCH_CMD_SRC = r"""
    systemctl\s+--user\s+(?:enable|start|restart|status|list-timers)
  | \.timer\b | \bcrontab\b | \bwitness | _health\.py | _watch\.py | \bautofix
  | cockpit\.py\s+channel | -m\s+sched\b | pop_claude\.sh | \bheartbeat
"""
WATCH_FILE_SRC = r"(?:health|watch|witness|monitor|alert|autofix|sentinel|heartbeat)[^/]*$|\.(?:timer|service)$"
WATCH_TOOLS = ("Monitor", "CronCreate", "ScheduleWakeup", "RemoteTrigger")
STALL_SUFFIX = ".stall"

FLAGS = re.I | re.X
_COMPILED = {}
JOB_SHOWN = 3
TASK_ID = re.compile(r"<task-id>([^<\s]+)</task-id>")
NOT_A_PROMPT = ("<task-notification>", "<local-command", "<system-reminder>",
                "This session is being continued", "[Request interrupted")


def _alternatives(where, value):
    if not isinstance(value, list) or not all(isinstance(a, str) for a in value):
        raise config.ConfigError("promise-gate: {} must be a list of regex strings".format(where))
    return value


def patterns(cfg):
    """(target, promise, pending) compiled with the configured extras appended as alternatives;
    `pending` is the promise set plus PENDING_SRC. An extra that does not compile, or is not a
    list of strings, is a config.ConfigError."""
    key = json.dumps([cfg.get("target_patterns_extra"), cfg.get("promise_patterns_extra")])
    if key not in _COMPILED:
        out = []
        for where, base in (("target_patterns_extra", TARGET_SRC),
                            ("promise_patterns_extra", PROMISE_SRC),
                            ("promise_patterns_extra", PROMISE_SRC.rstrip() + "\n  |"
                             + PENDING_SRC.lstrip("\n"))):
            extras = _alternatives(where, cfg.get(where) or [])
            src = base.rstrip() + "".join("\n  | (?:{}\n)".format(a) for a in extras)
            try:
                out.append(re.compile(src, FLAGS))
            except re.error as e:
                raise config.ConfigError("promise-gate {}: {}".format(where, e))
        _COMPILED[key] = tuple(out)
    return _COMPILED[key]


def _settings(cfg):
    """The configuration of a caller outside a hook: the project layer of its cwd and the user
    layer, read from this plugin's own defaults."""
    if cfg is not None:
        return cfg
    return trace.plugin_settings({"cwd": os.getcwd()}, root=PLUGIN_ROOT)


def stall_in(prompt):
    m = re.search(STALL_SRC, prompt or "", FLAGS)
    return m.group(0) if m else None


def stall_path(session_id):
    return trace.session_file(session_id, "") + STALL_SUFFIX


def stall_excerpt(session_id):
    try:
        with open(stall_path(session_id), encoding="utf-8") as fh:
            return fh.read().strip() or "(stall noticed)"
    except OSError:
        return None


def set_stall(session_id, excerpt):
    path = stall_path(session_id)
    if excerpt is None:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(excerpt[:EXCERPT_CHARS])


def watcher_gestures(transcript_path):
    """The watcher gestures of the main thread since the last prompt of the user: tool names, a
    Bash command matching WATCH_CMD_SRC, a Write/Edit of a file matching WATCH_FILE_SRC."""
    found = []
    try:
        fh = open(transcript_path or "", encoding="utf-8", errors="replace")
    except OSError:
        return found
    with fh:
        for line in fh:
            if '"user"' in line and '"tool_result"' not in line:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and _prompt_text(row) is not None:
                    found = []
                continue
            if '"tool_use"' not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("isSidechain") or row.get("type") != "assistant":
                continue
            content = (row.get("message") or {}).get("content")
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                name, args = b.get("name"), b.get("input") if isinstance(b.get("input"), dict) else {}
                if name in WATCH_TOOLS:
                    found.append(name)
                elif name == "Bash" and re.search(WATCH_CMD_SRC, str(args.get("command") or ""), FLAGS):
                    found.append("Bash: " + str(args.get("command"))[:80])
                elif name in ("Write", "Edit", "MultiEdit") and re.search(
                        WATCH_FILE_SRC, str(args.get("file_path") or ""), re.I):
                    found.append(name + ": " + str(args.get("file_path")))
    return found


def stall_reason_text(excerpt, cfg):
    text = ("The user noticed a stall before you did: \"{}\". An answer, even measured, leaves the "
            "next one to the user again. In this turn: measure it from the store or the log, fix "
            "its cause, and leave a WATCHER that catches the next stall without the user (a health "
            "witness or an autofix entry, a scheduled check: a timer, a cron, CronCreate, a "
            "scheduler row; a Monitor on the running work), or read the watcher already there and "
            "say why it did not fire. The transcript since that prompt shows no such gesture. "
            "(promise-gate)".format(excerpt))
    suffix = cfg.get("reason_suffix")
    return text + " " + suffix if suffix else text


def arm_path(session_id):
    return trace.session_file(session_id, "") + ARM_SUFFIX


def armed_excerpt(session_id):
    try:
        with open(arm_path(session_id), encoding="utf-8") as fh:
            return fh.read().strip() or "(target set)"
    except OSError:
        return None


def set_armed(session_id, excerpt):
    path = arm_path(session_id)
    if excerpt is None:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(excerpt[:EXCERPT_CHARS])


def target_in(prompt, cfg=None):
    return bool(patterns(_settings(cfg))[0].search(prompt or ""))


def promise_in(text, cfg=None):
    """The promise to continue ending `text` (its last TAIL_CHARS), or None."""
    m = patterns(_settings(cfg))[1].search((text or "")[-TAIL_CHARS:])
    return m.group(0) if m else None


def deferral_in(text, cfg=None):
    """A promise, or a deferral of the result to work still running (PENDING_SRC), ending `text`
    (its last TAIL_CHARS), or None. Read only while a shell job of the session is pending."""
    m = patterns(_settings(cfg))[2].search((text or "")[-TAIL_CHARS:])
    return m.group(0) if m else None


def _prompt_text(row):
    """The text of a prompt the user typed, or None for any other row (a tool result, a
    task-notification, a compaction summary, a meta or sidechain row)."""
    if row.get("type") != "user" or row.get("isSidechain") or row.get("isMeta") \
            or row.get("isCompactSummary"):
        return None
    content = (row.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        text = "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    elif isinstance(content, str):
        text = content
    else:
        return None
    text = text.strip()
    return text if text and not text.startswith(NOT_A_PROMPT) else None


SHELL_TYPES = ("shell", "local_bash")
DONE_STATUSES = ("completed", "failed", "killed", "stopped", "cancelled", "canceled", "error")


def live_shell_tasks(payload):
    """{task id: description} of the payload's `background_tasks` entries that are shell jobs
    still in flight, or None when the payload carries no such array (the transcript decides)."""
    tasks = payload.get("background_tasks")
    if not isinstance(tasks, list):
        return None
    out = {}
    for t in tasks:
        if not isinstance(t, dict) or t.get("type") not in SHELL_TYPES or not t.get("id"):
            continue
        if str(t.get("status") or "").lower() in DONE_STATUSES:
            continue
        out[str(t["id"])] = str(t.get("description") or "").strip()
    return out


class JobScan:
    """The shell jobs this session launched in the background since the last prompt of the user
    and that are still running, fed one transcript line at a time: a tool result carrying a
    backgroundTaskId, with no <task-id> of a task-notification (Claude Code writes its queue row
    the moment the job exits, delivered or not) and no TaskStop naming it since. A subagent
    (agentId) or a Monitor (taskId) is never one. Only the lines that can matter are parsed, so a
    transcript of tens of megabytes reads in a fraction of a second."""

    def __init__(self):
        # `launched`: every background shell job launched since the last prompt, finished or not.
        self.jobs, self.launched, self._described = {}, {}, {}

    def feed(self, line):
        if "<task-id>" in line:
            for tid in TASK_ID.findall(line):
                self.jobs.pop(tid, None)
        if not ("backgroundTaskId" in line or "run_in_background" in line
                or "TaskStop" in line or "KillShell" in line
                or (('"type":"user"' in line or '"type": "user"' in line)
                    and '"tool_result"' not in line)):
            return
        try:
            row = json.loads(line)
        except ValueError:
            return
        if not isinstance(row, dict) or row.get("isSidechain"):
            return
        if _prompt_text(row) is not None:
            self.jobs.clear()
            self.launched.clear()
            return
        content = (row.get("message") or {}).get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        for b in blocks:
            if b.get("type") != "tool_use":
                continue
            args = b.get("input") if isinstance(b.get("input"), dict) else {}
            if b.get("name") == "Bash" and args.get("run_in_background"):
                self._described[b.get("id")] = str(args.get("description") or "").strip()
            elif b.get("name") in ("TaskStop", "KillShell"):
                for k in ("task_id", "shell_id", "id"):
                    self.jobs.pop(str(args.get(k)), None)
        result = row.get("toolUseResult")
        if isinstance(result, dict) and result.get("backgroundTaskId"):
            tid = str(result["backgroundTaskId"])
            use = next((b.get("tool_use_id") for b in blocks if b.get("type") == "tool_result"),
                       None)
            self.jobs[tid] = self.launched[tid] = self._described.get(use, "")


def pending_jobs(transcript_path, live=None):
    """{task id: description} of the session's background shell jobs still running. `live` is
    live_shell_tasks(payload): when it is a dict, it decides which jobs run, and the transcript
    only keeps those launched since the last prompt of the user; when None, the transcript alone
    decides (JobScan)."""
    if live is not None and not live:
        return {}
    scan = JobScan()
    try:
        fh = open(transcript_path or "", encoding="utf-8", errors="replace")
    except OSError:
        return {}
    with fh:
        for line in fh:
            scan.feed(line)
    if live is None:
        return scan.jobs
    return {t: d or scan.launched[t] for t, d in live.items() if t in scan.launched}


def reason_text(promise, excerpt, cfg):
    text = ("The turn ends on a promise to continue (\"{}\") while the user set a target: \"{}\". "
            "Do the next batch now, in this turn, until the target is reached or the pool is "
            "exhausted; a batch report is a line inside the running turn, never a stop. If the "
            "context runs short, hand the remaining work to a subagent before ending. "
            "(promise-gate)".format(promise, excerpt))
    suffix = cfg.get("reason_suffix")
    return text + " " + suffix if suffix else text


def job_reason_text(promise, jobs, cfg):
    names = ", ".join("\"{}\" ({})".format(d, t) if d else t
                      for t, d in list(jobs.items())[:JOB_SHOWN])
    more = len(jobs) - JOB_SHOWN
    text = ("The turn ends on work still running (\"{}\"): this session's background job {}{} "
            "has not finished, so the final message carries no result, and the user reads it as "
            "the end of the work. Wait for it inside this turn, with foreground Bash calls of a "
            "few minutes each that return when it exits (its process gone, or its output file "
            "showing its end), so a message from the user is read between them; never start a "
            "job with effects a second time. Then finish what waited on it and give the result. "
            "(promise-gate)".format(promise, names,
                                    " and {} more".format(more) if more > 0 else ""))
    suffix = cfg.get("reason_suffix")
    return text + " " + suffix if suffix else text


def on_prompt():
    payload = trace.read_payload()
    cfg = trace.plugin_settings(payload, root=PLUGIN_ROOT)
    prompt = payload.get("prompt") or ""
    sid = trace.session_key(payload)
    if target_in(prompt, cfg):
        set_armed(sid, " ".join(prompt.split()))
    if stall_in(prompt):
        set_stall(sid, " ".join(prompt.split()))
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                "promise-gate: the user noticed a stall before you did. This turn ends only "
                "after it measured the stall, fixed its cause, and left a watcher that catches "
                "the next one without the user (a witness, an autofix entry, a timer or cron, a "
                "scheduler row, a Monitor), or read the watcher already there.")}},
            ensure_ascii=False))
    else:
        set_stall(sid, None)
    return 0


def on_stop():
    payload = trace.read_payload()
    if payload.get("hook_event_name") != "Stop":
        return 0
    cfg = trace.plugin_settings(payload, root=PLUGIN_ROOT)
    patterns(cfg)  # a bad extra is a loud crash even on an unarmed session
    excerpt = armed_excerpt(trace.session_key(payload))
    text = transcript.final_text(payload)
    promise = promise_in(text, cfg) if excerpt else None
    jobs = {}
    if not promise:
        deferral = deferral_in(text, cfg)
        jobs = pending_jobs(payload.get("transcript_path"),
                            live_shell_tasks(payload)) if deferral else {}
        promise = deferral if jobs else None
    stall = None
    if not promise:
        stall = stall_excerpt(trace.session_key(payload))
        if stall and watcher_gestures(payload.get("transcript_path")):
            set_stall(trace.session_key(payload), None)
            stall = None
    if not promise and not stall:
        trace.chain_clear(HOOK, payload)
        return 0
    if stall:
        if not trace.chain_should_block(HOOK, payload, "stall: " + stall):
            print(trace.cap_message(HOOK, "stall: " + stall))
            return 0
        trace.witness("stop_promise_gate", "Stop", "stall_unwatched", payload)
        print(json.dumps({"decision": "block", "reason": stall_reason_text(stall, cfg)},
                         ensure_ascii=False))
        return 0
    detail = ("job: " if jobs else "promise: ") + promise
    if not trace.chain_should_block(HOOK, payload, detail):
        print(trace.cap_message(HOOK, detail))
        return 0
    trace.witness("stop_promise_gate", "Stop", "job_pending" if jobs else "promise_ending",
                  payload)
    reason = job_reason_text(promise, jobs, cfg) if jobs else reason_text(promise, excerpt, cfg)
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


def main():
    trace.configure(sys.argv)
    if "--prompt" in sys.argv:
        return trace.run(HOOK, "UserPromptSubmit", on_prompt, TIMEOUT_S)
    if "--arm" in sys.argv:
        i = sys.argv.index("--arm")
        excerpt = sys.argv[i + 1] if i + 1 < len(sys.argv) else "(target set by hand)"
        set_armed(trace.session_key({}), excerpt)
        print("promise gate ARMED for this session: it will refuse a turn ending on a promise.")
        return 0
    if "--disarm" in sys.argv:
        set_armed(trace.session_key({}), None)
        print("promise gate DISARMED for this session.")
        return 0
    if "--print" in sys.argv:
        sid = trace.session_key({})
        excerpt = armed_excerpt(sid)
        print("session {}: {}".format(sid or "(unknown)",
                                      "ARMED on \"{}\"".format(excerpt) if excerpt
                                      else "not armed, silent"))
        return 0
    return trace.run(HOOK, "Stop", on_stop, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
