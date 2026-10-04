#!/usr/bin/env python3
"""gatekit.trace: the chain cap, the crash report and the refusal witness every gate shares.

Ported from growth-cockpit's hook_trace.py (2026-09-30). Why each part exists:

THE CHAIN CAP. A Stop gate is a regex. A false positive the model cannot write around would
block every answer of the turn, and Claude Code's own override (8 consecutive continuations
shared by every Stop hook) ends such a loop without telling the hook. Each Stop gate counts ITS
OWN consecutive blocks inside one stop chain (`stop_hook_active` true means the chain goes on)
and at CHAIN_CAP lets the turn end, writes a `cap` row and says so in a systemMessage. A
PreToolUse deny has no such loop, so no PreToolUse gate carries a cap.

A CRASH IS LOUD, AND THE SESSION STAYS USABLE. A gate that raises, or has not decided after its
alarm, lets the action through (a broken hook must never brick a session), writes a `crash` or
`timeout` row, runs the configured crash command, and prints a systemMessage the user sees. The
next clean run records the recovery.

NO BYPASS. No environment variable and no marker file turns a gate off. The only knob is
`--home DIR`, an argument no hooks.json passes: tests point every file at a fixture with it.

WHERE THINGS GO is configuration, section "trace" of trimwrit-gates.json:
  ledger            the health JSONL (default: the plugin's data directory)
  witness_module    a Python file exposing log(door, event, reason_class, session_id) that
                    counts refusals (default: door-refusals.jsonl next to the health ledger)
  on_crash_command  an argv list, run detached as `<argv> hook:<name> --ok` or
                    `<argv> hook:<name> --detail D --reproduce R` (default: none)
"""
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime

if __package__:
    from . import config
else:  # run as a script: python3 gatekit/trace.py witness ...
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from gatekit import config  # noqa: E402

LEDGER_NAME = "hook-health.jsonl"
REFUSALS_NAME = "door-refusals.jsonl"
STATE_NAME = "trimwrit-gates"
CHAIN_CAP = 3
DETAIL_MAX = 600
ENV_SESSION = "CLAUDE_SESSION_ID"
ENV_DATA = "CLAUDE_PLUGIN_DATA"
TRACE_DEFAULTS = {"ledger": "", "witness_module": "", "on_crash_command": []}

_HOME = None
_PAYLOAD = {}
_SETTINGS = None


class HookTimeout(Exception):
    """Raised by the alarm when a hook has not decided in time."""


def configure(argv):
    """Take `--home DIR` out of argv (in place) and remember it. Returns argv."""
    global _HOME, _SETTINGS
    if "--home" in argv:
        i = argv.index("--home")
        if i + 1 >= len(argv):
            raise SystemExit("--home needs a directory")
        _HOME = argv[i + 1]
        del argv[i:i + 2]
        _SETTINGS = None
    return argv


def home():
    return _HOME


def trace_settings():
    """The "trace" section. A broken file must not break the crash path that reports it, so an
    error here falls back to the defaults (the gate's own plugin_settings call raises it)."""
    global _SETTINGS
    if _SETTINGS is None:
        try:
            _SETTINGS = config.load("trace", TRACE_DEFAULTS, _PAYLOAD, _HOME)
        except config.ConfigError:
            _SETTINGS = dict(TRACE_DEFAULTS)
    return _SETTINGS


def plugin_settings(payload=None, root=None):
    """This plugin's own section. Raises config.ConfigError, which run() reports as a crash.
    `root` names the plugin directory whose defaults.json applies; a plugin script passes its
    PLUGIN_ROOT, because one Python process holds a single gatekit module and a gate imported
    next to another would otherwise read the wrong defaults."""
    return config.load_plugin(payload if payload is not None else _PAYLOAD, _HOME, root)


def data_dir():
    if _HOME:
        return _HOME
    return os.environ.get(ENV_DATA) or os.path.join(os.path.expanduser("~"), ".claude",
                                                    STATE_NAME)


def ledger_path():
    if _HOME:
        return os.path.join(_HOME, LEDGER_NAME)
    configured = trace_settings().get("ledger")
    return config.expand(configured) if configured else os.path.join(data_dir(), LEDGER_NAME)


def refusals_path():
    return os.path.join(os.path.dirname(ledger_path()), REFUSALS_NAME)


def state_dir():
    d = os.path.join(_HOME, "state") if _HOME else os.path.join(tempfile.gettempdir(), STATE_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def remember(payload):
    """Keep the payload so a crash row can name the session and the cwd."""
    global _PAYLOAD, _SETTINGS
    _PAYLOAD = payload if isinstance(payload, dict) else {}
    _SETTINGS = None
    return _PAYLOAD


def session_key(payload=None):
    p = payload if isinstance(payload, dict) else _PAYLOAD
    return (p or {}).get("session_id") or os.environ.get(ENV_SESSION, "")


def session_file(session_id, suffix):
    key = hashlib.sha256((session_id or "no-id").encode("utf-8")).hexdigest()[:16]
    return os.path.join(state_dir(), key + suffix)


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _append(path, row):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="ascii") as fh:
        fh.write(json.dumps(row, ensure_ascii=True) + "\n")


def trace(hook, event, outcome, payload=None, **fields):
    """Append one health row. True when written; an unwritable ledger says so on stderr."""
    p = payload if isinstance(payload, dict) else _PAYLOAD
    row = {"at": now_iso(), "hook": hook, "event": event, "outcome": outcome,
           "session": session_key(p), "cwd": (p or {}).get("cwd") or os.getcwd()}
    for k, v in fields.items():
        if isinstance(v, str) and len(v) > DETAIL_MAX:
            v = v[:DETAIL_MAX]
        row[k] = v
    try:
        _append(ledger_path(), row)
        return True
    except OSError as e:
        sys.stderr.write("gatekit: health ledger unwritable ({}): {}\n".format(e, row))
        return False


def witness_script():
    if _HOME:
        fixture = os.path.join(_HOME, "door_ledger.py")
        return fixture if os.path.isfile(fixture) else None
    configured = trace_settings().get("witness_module")
    return config.expand(configured) if configured else None


def witness(door, event, reason_class, payload=None, script=None):
    """Count one refusal. The door already decided: a witness that cannot be reached changes
    nothing here. `script` lets a caller name a module explicitly."""
    sid = session_key(payload) or None
    script = script or witness_script()
    if script:
        if not os.path.isfile(script):
            sys.stderr.write("gatekit: witness module missing: {}\n".format(script))
            return False
        try:
            spec = importlib.util.spec_from_file_location("gatekit_witness", script)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return bool(mod.log(door, event, reason_class, sid))
        except Exception as e:  # noqa: BLE001  said on stderr, never a change of decision
            sys.stderr.write("gatekit: witness unreachable: {}\n".format(e))
            return False
    try:
        _append(refusals_path(), {"at": now_iso(), "door": door, "event": event,
                                  "reason_class": reason_class, "session": sid or ""})
        return True
    except OSError as e:
        sys.stderr.write("gatekit: refusal ledger unwritable: {}\n".format(e))
        return False


def _read_int(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def _write_int(path, n):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(n))


def chain_path(hook, payload):
    return session_file(session_key(payload), "." + hook + ".chain")


def chain_should_block(hook, payload, detail):
    """For a Stop gate that found a violation. True: block now. False: this gate already
    blocked CHAIN_CAP times in a row in this stop chain, so the turn ends, a `cap` row is
    written and the caller prints cap_message()."""
    path = chain_path(hook, payload)
    used = _read_int(path) if (payload or {}).get("stop_hook_active") else 0
    if used >= CHAIN_CAP:
        _write_int(path, 0)
        trace(hook, "Stop", "cap", payload, blocks=used, detail=detail)
        return False
    _write_int(path, used + 1)
    return True


def chain_clear(hook, payload):
    try:
        os.remove(chain_path(hook, payload))
    except OSError:
        pass


def cap_message(hook, detail):
    return json.dumps({"systemMessage": (
        "{}: {} blocks in a row did not clear it, the turn ends anyway and the miss is recorded "
        "({}, outcome cap): {}".format(hook, CHAIN_CAP, ledger_path(), detail[:200]))},
        ensure_ascii=False)


def _crash_marker(hook):
    return os.path.join(state_dir(), hook + ".crashed")


def crash_command():
    if _HOME:
        fixture = os.path.join(_HOME, "autofix.py")
        return [sys.executable, fixture, "record"] if os.path.isfile(fixture) else None
    cmd = trace_settings().get("on_crash_command")
    return [config.expand(w) for w in cmd] if cmd else None


def autofix(hook, ok, detail=""):
    """Run the configured crash command, detached: the session never waits on it. Returns a
    short status for the ledger row."""
    cmd = crash_command()
    if not cmd:
        return "no crash command configured"
    args = cmd + ["hook:" + hook]
    if ok:
        args.append("--ok")
    else:
        args += ["--detail", detail[-400:], "--reproduce",
                 "replay the last {} row of {} into the hook on stdin".format(hook, ledger_path())]
    try:
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        return "spawned"
    except OSError as e:
        return "spawn failed: {}".format(e)


def crashed(hook, event, kind, detail):
    """The one exit of a broken gate: ledger row, crash command, a visible systemMessage, 0."""
    status = autofix(hook, ok=False, detail="{} {}: {}".format(hook, kind, detail))
    trace(hook, event, kind, None, detail=detail, autofix=status)
    try:
        with open(_crash_marker(hook), "w", encoding="utf-8") as fh:
            fh.write(now_iso())
    except OSError:
        pass
    print(json.dumps({"systemMessage": (
        "{} {} ({}): this {} goes on UNGUARDED. Recorded in {} (crash command: {}).".format(
            hook, "crashed" if kind == "crash" else "timed out", detail[-200:],
            "turn" if event == "Stop" else "call", ledger_path(), status))},
        ensure_ascii=False))
    sys.stdout.flush()
    return 0


def recovered(hook):
    """After a clean run: if the last run crashed, record the recovery."""
    marker = _crash_marker(hook)
    if not os.path.exists(marker):
        return
    try:
        os.remove(marker)
    except OSError:
        return
    trace(hook, "", "recovered", None, autofix=autofix(hook, ok=True))


def run(hook, event, body, timeout_s):
    """Run `body()` (returns an exit code) under an alarm; a crash or a timeout goes through
    crashed(), a clean run through recovered(). Returns the exit code for sys.exit."""
    def on_alarm(*_):
        raise HookTimeout()

    signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(timeout_s)
    try:
        try:
            code = body()
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 0
        signal.alarm(0)
    except HookTimeout:
        return crashed(hook, event, "timeout", "no decision after {}s".format(timeout_s))
    except Exception as e:  # noqa: BLE001  every error is reported, none is swallowed
        signal.alarm(0)
        tail = "".join(traceback.format_exception(type(e), e, e.__traceback__))[-DETAIL_MAX:]
        return crashed(hook, event, "crash", tail)
    recovered(hook)
    return code or 0


def read_payload():
    """stdin as a JSON object. Anything else raises, and run() reports it as a crash."""
    raw = sys.stdin.read()
    payload = json.loads(raw or "{}")
    if not isinstance(payload, dict):
        raise ValueError("hook payload is not a JSON object: {!r}".format(raw[:120]))
    return remember(payload)


def main(argv=None):
    argv = configure(list(sys.argv[1:] if argv is None else argv))
    if len(argv) >= 4 and argv[0] == "witness":
        session = ""
        if "--session" in argv:
            i = argv.index("--session")
            session = argv[i + 1] if i + 1 < len(argv) else ""
        witness(argv[1], argv[2], argv[3], {"session_id": session})
        return 0
    sys.stderr.write("usage: trace.py witness DOOR EVENT REASON_CLASS [--session ID] "
                     "[--home DIR]\n")
    return 64


if __name__ == "__main__":
    sys.exit(main())
