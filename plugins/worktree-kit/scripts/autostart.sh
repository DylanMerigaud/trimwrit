#!/bin/bash
# SessionStart hook: detect "on the main checkout (the hub), not isolated, opted in" and tell
# Claude to relocate into a fresh worktree with the native EnterWorktree tool.
#
# The hook CANNOT move the session (no hook can set the cwd). It only DETECTS and INJECTS a
# directive; EnterWorktree does the actual relocation.
#
# OPT-IN: active only when <repo-toplevel>/.claude/auto-worktree exists. Without it this is a
# silent no-op on every repository.
#
# Robustness: never crash a session start. Any unexpected condition exits 0 silently. A bad
# configuration file is reported on stderr and the defaults apply.
# Compatible with /bin/bash 3.2.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Every setting in one python call, printed as shell assignments. A config error lands in
# CFG_ERROR and the defaults of defaults.json apply: the hook never fails over a bad file.
load_config() {
  local out
  out="$(python3 - "$ROOT" "$1" <<'PY' 2>/dev/null
import json, os, shlex, sys
root, cwd = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
with open(os.path.join(root, "defaults.json"), encoding="utf-8") as fh:
    cfg = json.load(fh)["defaults"]
err = ""
try:
    from gatekit import config
    got = config.load_plugin({"cwd": cwd}, None, root)
    for key in ("roster", "ages_file", "remote"):
        if not isinstance(got[key], str):
            raise config.ConfigError("{} must be a string".format(key))
    if not got["remote"].strip():
        raise config.ConfigError("remote must not be empty")
    if not all(isinstance(g, str) for g in got["link_extra"]):
        raise config.ConfigError("link_extra must be a list of strings")
    cfg = got
except Exception as e:
    err = "{}: {}".format(type(e).__name__, e).replace("\n", " ")[:300]
for name, value in (("CFG_ERROR", err), ("CFG_ROSTER", os.path.expanduser(cfg["roster"])),
                    ("CFG_AGES", os.path.expanduser(cfg["ages_file"])),
                    ("CFG_REMOTE", cfg["remote"]), ("CFG_LINK_EXTRA", "\n".join(cfg["link_extra"]))):
    print("{}={}".format(name, shlex.quote(value)))
PY
)"
  CFG_ERROR="python3 failed"; CFG_ROSTER=""; CFG_AGES=""; CFG_REMOTE="origin"; CFG_LINK_EXTRA=""
  case "$out" in
    CFG_ERROR=*) eval "$out" ;;
  esac
  if [ -n "$CFG_ERROR" ]; then
    echo "worktree-kit: config error, defaults applied: $CFG_ERROR" >&2
  fi
}

# --- read hook input from stdin (cwd + source). jq if present, sed fallback otherwise. ---
input="$(cat 2>/dev/null || true)"

json_field() {
  # $1 = field name. Prints the value or nothing.
  local field="$1"
  if [ -n "$input" ] && command -v jq >/dev/null 2>&1; then
    printf '%s' "$input" | jq -r --arg k "$field" '.[$k] // empty' 2>/dev/null && return 0
  fi
  printf '%s' "$input" | sed -n "s/.*\"$field\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n1
}

cwd="$(json_field cwd)"
source_field="$(json_field source)"

# Double safety with the hooks.json matcher: only act on brand-new sessions.
if [ -n "$source_field" ] && [ "$source_field" != "startup" ]; then
  exit 0
fi

[ -n "$cwd" ] || cwd="${PWD:-.}"
cd "$cwd" 2>/dev/null || exit 0

# --- 1. git repo? else silent no-op. ---
command -v git >/dev/null 2>&1 || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

# --- 2. already isolated (linked worktree, not a submodule)? -> no-op. ---
gd="$(git rev-parse --git-dir 2>/dev/null || true)"
gc="$(git rev-parse --git-common-dir 2>/dev/null || true)"
GIT_DIR="$( { cd "$gd" 2>/dev/null && pwd -P; } || printf '%s' "$gd" )"
COMMON="$( { cd "$gc" 2>/dev/null && pwd -P; } || printf '%s' "$gc" )"
superproject="$(git rev-parse --show-superproject-working-tree 2>/dev/null || true)"
if [ -n "$GIT_DIR" ] && [ -n "$COMMON" ] && [ "$GIT_DIR" != "$COMMON" ] && [ -z "$superproject" ]; then
  exit 0   # already in a linked worktree (and not a submodule): nothing to do
fi

# --- 3. OPT-IN gate: only activate where the marker exists. ---
toplevel="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$toplevel" ] || exit 0

emit() {
  # $1 = additionalContext text, as one SessionStart JSON object.
  if command -v jq >/dev/null 2>&1; then
    jq -n --arg ctx "$1" '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}'
  else
    local esc
    esc="$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | awk 'BEGIN{ORS=""} {print (NR==1?"":"\\n") $0}')"
    printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"%s"}}\n' "$esc"
  fi
}

# 3bis. THE MARKER IS MISSING: silence, or alert?
# Exiting silently is right on a repository that never opted in, and it is a trap on one that
# opted in then lost its marker. Measured: a blocked session took the escape hatch
# `rm .claude/auto-worktree` instead of `touch .claude/auto-worktree-off`, nothing said so, and
# the next day two sessions shared one tree and the commit of one erased the uncommitted work
# of the other. The roster tells the two cases apart: a repository LISTED and with no marker is
# an anomaly, not a choice. No roster configured means no anomaly check.
if [ ! -f "$toplevel/.claude/auto-worktree" ]; then
  load_config "$toplevel"
  ROSTER="$CFG_ROSTER"
  if [ -n "$ROSTER" ] && [ -f "$ROSTER" ] && grep -qxF "$toplevel" "$ROSTER" 2>/dev/null; then
    warn="AUTO-WORKTREE ANOMALY: this repository is listed in the roster ($ROSTER) as isolating every session, but its marker $toplevel/.claude/auto-worktree is MISSING, so isolation is off and two sessions can overwrite each other in the same tree. Tell the user first, before anything else, and offer to re-arm it (touch $toplevel/.claude/auto-worktree) or to remove the roster line. Do not choose alone."
    emit "$warn"
  fi
  exit 0
fi

# --- 4. On the hub, opted in, not isolated -> instruct Claude to relocate and set up. ---
read -r -d '' directive <<'DIRECTIVE' || true
AUTO-WORKTREE: You launched on this repo's MAIN checkout (the hub), which must stay clean. No work happens here. BEFORE anything else, isolate this session:
1. Call the EnterWorktree tool with NO name argument (let it auto-generate a random name). It creates a worktree under .claude/worktrees/ and switches your working directory into it.
2. Then run this exact command: bash @POSTENTER@
   It symlinks the repo's gitignored .env* and .vercel/project.json into the worktree and installs dependencies for the detected package manager. If the install step is slow, run it in the background.
3. Only after that, proceed with the user's task, working entirely inside the worktree. Never `git checkout` a branch on the hub, and never commit on the hub.
If the user explicitly asks to stay on the hub, skip these steps.
DIRECTIVE
directive="${directive//@POSTENTER@/$ROOT/scripts/postenter.sh}"

emit "$directive"
exit 0
