#!/bin/bash
# session_end.sh: the ONE SessionEnd hook entry. It runs, in this order, from the SAME stdin
# payload: automerge.sh (lands the worktree's branch on the default branch), cleanup.sh (dates
# the worktree and prunes registry entries that already vanished; it deletes nothing, see its
# own header), then the configured session_end_after commands. A plain session used to pay
# separate hook entries with no single number to read; this prints one measured line per step
# plus the chain's own total, so the cost of closing a session is one number, not a guess.
#
# stdin is READ ONCE here and RE-FED to each step: a pipe is consumed on first read, so without
# this the later steps would see an empty stdin and lose `cwd` and `reason`.
#
# NEVER blocks one step on another's failure: every step runs regardless of any one's exit
# code, which is only ever logged, never propagated.
# Compatible with /bin/bash 3.2.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
input="$(cat 2>/dev/null || true)"

run_step() {
  local name="$1" script="$2"
  local t0=$SECONDS
  printf '%s' "$input" | bash "$script"
  local rc=$?
  printf 'session-end: %s took %ss (exit %s)\n' "$name" "$((SECONDS - t0))" "$rc" >&2
}

chain_start=$SECONDS
run_step automerge "$ROOT/scripts/automerge.sh"
run_step cleanup "$ROOT/scripts/cleanup.sh"

# The after-commands run where the session ran: the payload's cwd, else PWD.
cwd="$(printf '%s' "$input" | { jq -r '.cwd // empty' 2>/dev/null || true; })"
if [ -z "$cwd" ]; then
  cwd="$(printf '%s' "$input" | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
fi
[ -d "$cwd" ] || cwd="${PWD}"

t0=$SECONDS
printf '%s' "$input" | python3 "$ROOT/gatekit/config.py" run session_end_after --cwd "$cwd"
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "worktree-kit: session_end_after not run (config error above)" >&2
fi
printf 'session-end: session_end_after took %ss (exit %s)\n' "$((SECONDS - t0))" "$rc" >&2
printf 'session-end: total %ss\n' "$((SECONDS - chain_start))" >&2
exit 0
