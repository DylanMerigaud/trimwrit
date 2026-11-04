#!/bin/bash
# resume_on_api_error.sh: StopFailure hook. A turn that ended on an API error resumes itself.
#
# An interactive Claude Code session that fails a turn on an API error waits at its prompt for a
# human to type "continue". No setting resumes it by itself; the StopFailure event runs INSTEAD
# of Stop when a turn ends on an API error, with the error category on stdin. So the resume is a
# keystroke: when the session lives inside a GNU screen window ($STY names it) or a tmux pane
# ($TMUX and $TMUX_PANE), a detached sleeper types the configured text into it after a delay. A
# network still down fails again, fires this hook again and gets the next keystroke.
#
# WHAT IS RESUMED is decided by scripts/resume_policy.py from the session's transcript (the
# payload's error_details comes in empty): never an authentication or billing failure, never a
# safeguards or usage-policy flag, never a request too large to send; an invalid_request at most
# once in a row; a transient failure without end but with a doubling delay past five in a row;
# any other category at most three times in a row. 2026-11-04: a safeguards flag reported as
# plain invalid_request got 991 "continue" keystrokes.
#
# ONE PENDING SLEEPER PER TARGET: a pidfile per screen window or tmux pane under the state
# directory, created atomically; while its sleeper is alive the next failure is logged and left,
# so sleepers never stack. With neither multiplexer there is nothing to type into: the failure
# is logged and the hook exits 0.
#
# Settings (section resume-on-api-error of trimwrit-gates.json): log, vim_mode, delay_s,
# rate_limit_delay_s, text. A config error is loud (stderr and the log) and the defaults apply.
#
# `--dry-run` prints the detached command instead of running it and creates no pidfile (tests;
# no hooks.json passes it). Compatible with /bin/bash 3.2.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

payload="$(cat 2>/dev/null)"
parsed="$(printf '%s' "${payload}" | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    d={}
if not isinstance(d,dict):
    d={}
def one(v,n):
    return str(v).strip()[:n].replace("\n"," ")
print(one(d.get("error",""),40))
print(one(d.get("session_id",""),36))
print(one(d.get("error_details",""),160))
print(one(d.get("cwd",""),1000))' 2>/dev/null)"
category="$(printf '%s\n' "${parsed}" | sed -n '1p')"
session="$(printf '%s\n' "${parsed}" | sed -n '2p')"
details="$(printf '%s\n' "${parsed}" | sed -n '3p')"
pcwd="$(printf '%s\n' "${parsed}" | sed -n '4p')"

# Every setting in one python call, printed as shell assignments. A config error is reported on
# CFG_ERROR and the defaults of defaults.json apply: the hook never exits 2 over a bad file.
settings="$(python3 - "$ROOT" "${pcwd:-$PWD}" <<'PY' 2>/dev/null
import json, os, shlex, sys
root, cwd = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
with open(os.path.join(root, "defaults.json"), encoding="utf-8") as fh:
    cfg = json.load(fh)["defaults"]
err = ""
try:
    from gatekit import config
    got = config.load_plugin({"cwd": cwd}, None, root)
    for key in ("delay_s", "rate_limit_delay_s"):
        if isinstance(got[key], bool) or not isinstance(got[key], int) or got[key] < 0:
            raise config.ConfigError("{} must be a non-negative integer".format(key))
    if not isinstance(got["vim_mode"], bool):
        raise config.ConfigError("vim_mode must be true or false")
    for key in ("log", "text"):
        if not isinstance(got[key], str):
            raise config.ConfigError("{} must be a string".format(key))
    cfg = got
except Exception as e:
    err = "{}: {}".format(type(e).__name__, e).replace("\n", " ")[:300]
log = os.path.expanduser(cfg["log"]) if cfg["log"] else ""
for name, value in (("CFG_ERROR", err), ("CFG_LOG", log), ("CFG_VIM", "1" if cfg["vim_mode"] else "0"),
                    ("CFG_DELAY", str(cfg["delay_s"])), ("CFG_RATE_DELAY", str(cfg["rate_limit_delay_s"])),
                    ("CFG_TEXT", cfg["text"])):
    print("{}={}".format(name, shlex.quote(value)))
PY
)"
CFG_ERROR="python3 failed"; CFG_LOG=""; CFG_VIM=0; CFG_DELAY=60; CFG_RATE_DELAY=120
CFG_TEXT="continue: the previous turn ended on an API error; resume exactly where you were and retry what failed"
case "${settings}" in
  CFG_ERROR=*) eval "${settings}" ;;
esac

LOG="${CFG_LOG:-${CLAUDE_PLUGIN_DATA:-$HOME/.claude/trimwrit-gates}/api-failures.log}"
mkdir -p "$(dirname "${LOG}")" 2>/dev/null
now() { date '+%F %T'; }
if [ -n "${CFG_ERROR}" ]; then
  echo "resume-on-api-error: config error, defaults applied: ${CFG_ERROR}" >&2
  echo "$(now)   config error, defaults applied: ${CFG_ERROR}" >> "${LOG}"
fi

echo "$(now) stop-failure category=${category:-unknown} session=${session:-?} sty=${STY:-none} tmux_pane=${TMUX_PANE:-none} cwd=${PWD} details=${details}" >> "${LOG}"

STATE="${CLAUDE_PLUGIN_DATA:-$HOME/.claude/trimwrit-gates}/resume-on-api-error"
mkdir -p "${STATE}" 2>/dev/null
VERDICT="resume"; REASON="policy unavailable"; COUNT=1; FACTOR=1
policy="$(printf '%s' "${payload}" | python3 "${ROOT}/scripts/resume_policy.py" "${STATE}" 2>/dev/null)"
case "${policy}" in
  VERDICT=*) eval "${policy}" ;;
esac
if [ "${VERDICT}" != "resume" ]; then
  echo "$(now)   not resumed: ${REASON}" >> "${LOG}"
  exit 0
fi

if [ -n "${STY:-}" ]; then
  target="screen"
elif [ -n "${TMUX:-}" ] && [ -n "${TMUX_PANE:-}" ]; then
  target="tmux"
else
  echo "$(now)   not resumed: no screen or tmux session to type into" >> "${LOG}"
  exit 0
fi

case "${category}" in
  rate_limit|overloaded) delay="${CFG_RATE_DELAY}" ;;
  *) delay="${CFG_DELAY}" ;;
esac
# The policy's back-off: past five transient failures in a row the delay doubles, capped at one
# hour (never below the configured delay).
if [ "${FACTOR}" -gt 1 ] 2>/dev/null; then
  delay=$((delay * FACTOR))
  [ "${delay}" -gt 3600 ] && delay=3600
  [ "${delay}" -lt "${CFG_DELAY}" ] && delay="${CFG_DELAY}"
fi

# sq: single-quote a value for the detached bash -c string, so a quote inside the configured
# text (or a session name) cannot break the command.
sq() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

text="${CFG_TEXT} (${category:-unknown})"
# VIM MODE: with the TUI in vim editorMode a bare stuff lands as NORMAL-mode commands. Escape,
# a short pause, then `i` (prefixed to the text) enters INSERT before the text and return.
[ "${CFG_VIM}" = "1" ] && text="i${text}"

if [ "${target}" = "screen" ]; then
  where="${STY}"; key="screen-${STY}"
else
  where="${TMUX_PANE}"; key="tmux-${TMUX%%,*}-${TMUX_PANE}"
fi
PIDF="${STATE}/pending-$(printf '%s' "${key}" | tr -c 'A-Za-z0-9_.-' '_').pid"

# pending: true while the pidfile names a live process of ours, a sleeper or a hook that just
# claimed the file (the marker is in its argv, so a recycled pid of another program does not
# count).
pending() {
  local pid
  pid="$(cat "${PIDF}" 2>/dev/null)"
  [ -n "${pid}" ] || return 1
  kill -0 "${pid}" 2>/dev/null || return 1
  ps -p "${pid}" -o args= 2>/dev/null | grep -q -e 'resume-on-api-error sleeper' -e 'resume_on_api_error\.sh'
}
claim() { ( set -C; printf '%s\n' "$$" > "${PIDF}" ) 2>/dev/null; }

cmd=": resume-on-api-error sleeper; sleep ${delay}; "
if [ "${target}" = "screen" ]; then
  [ "${CFG_VIM}" = "1" ] && cmd="${cmd}screen -S $(sq "${STY}") -X stuff \"\$(printf '\\033')\"; sleep 0.4; "
  cmd="${cmd}screen -S $(sq "${STY}") -X stuff $(sq "${text}")\"\$(printf '\\r')\""
else
  [ "${CFG_VIM}" = "1" ] && cmd="${cmd}tmux send-keys -t $(sq "${TMUX_PANE}") Escape; sleep 0.4; "
  cmd="${cmd}tmux send-keys -t $(sq "${TMUX_PANE}") -l -- $(sq "${text}") && tmux send-keys -t $(sq "${TMUX_PANE}") Enter"
fi
cmd="${cmd} && echo \"\$(date '+%F %T')   resumed ${target} ${where} after ${delay}s (${REASON})\" >> $(sq "${LOG}"); rm -f $(sq "${PIDF}")"

if pending; then
  echo "$(now)   not resumed: a sleeper is already pending for ${target} ${where} (pid $(cat "${PIDF}" 2>/dev/null))" >> "${LOG}"
  exit 0
fi

if [ "${DRY}" = "1" ]; then
  printf '%s\n' "${cmd}"
  exit 0
fi
# Atomic claim (noclobber is O_EXCL): of two hooks racing, one creates the pidfile and the other
# is refused. A stale file (its sleeper dead) is removed and claimed once more.
if ! claim; then
  if pending; then
    echo "$(now)   not resumed: another hook claimed ${target} ${where} first" >> "${LOG}"
    exit 0
  fi
  rm -f "${PIDF}"
  if ! claim; then
    echo "$(now)   not resumed: another hook claimed ${target} ${where} first" >> "${LOG}"
    exit 0
  fi
fi
nohup bash -c "${cmd}" >/dev/null 2>&1 &
printf '%s\n' "$!" > "${PIDF}"
disown 2>/dev/null || true
exit 0
