#!/bin/bash
# PreToolUse(Edit|Write|NotebookEdit) guard: on an OPTED-IN repository, refuse writes to the HUB.
#
# Why this exists: autostart.sh only INJECTS a directive into context, and a directive is a
# suggestion. A session that never reads it, or reads it and decides the task is "just a quick
# doc edit", writes on the hub anyway. This hook is the enforcement half: the directive says
# where to go, this says you cannot work until you go.
#
# It is deliberately narrow. It denies ONLY when all of these hold:
#   1. the cwd is inside a git repo,
#   2. that repo's toplevel has the opt-in marker .claude/auto-worktree,
#   3. the session is NOT already inside a linked worktree,
#   4. the target file is not itself under .claude/ (see escape hatches).
# Anything else, any parse failure, any unexpected state -> silent exit 0, which is "no
# opinion", NOT "allow": the normal permission rules still run.
#
# ESCAPE HATCHES, in order of how they should be used:
#   - Files under <toplevel>/.claude/ are always writable. That is what makes the guard
#     self-disableable from inside a blocked session (delete the marker, touch the bypass)
#     and keeps settings and hooks reachable when everything else is refused.
#   - touch <toplevel>/.claude/auto-worktree-off  -> guard off for this repo, marker kept.
#   - rm <toplevel>/.claude/auto-worktree         -> repo opts out entirely.
# Compatible with /bin/bash 3.2.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

input="$(cat 2>/dev/null || true)"
[ -n "$input" ] || exit 0

if command -v jq >/dev/null 2>&1; then
  cwd="$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null || true)"
  target="$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' 2>/dev/null || true)"
else
  cwd="$(printf '%s' "$input" | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
  target="$(printf '%s' "$input" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
fi

cd "${cwd:-${PWD:-.}}" 2>/dev/null || exit 0

command -v git >/dev/null 2>&1 || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

# Already isolated in a linked worktree (and not a submodule)? Nothing to enforce.
gd="$(git rev-parse --git-dir 2>/dev/null || true)"
gc="$(git rev-parse --git-common-dir 2>/dev/null || true)"
GIT_DIR="$( { cd "$gd" 2>/dev/null && pwd -P; } || printf '%s' "$gd" )"
COMMON="$( { cd "$gc" 2>/dev/null && pwd -P; } || printf '%s' "$gc" )"
superproject="$(git rev-parse --show-superproject-working-tree 2>/dev/null || true)"
if [ -n "$GIT_DIR" ] && [ -n "$COMMON" ] && [ "$GIT_DIR" != "$COMMON" ] && [ -z "$superproject" ]; then
  exit 0
fi

toplevel="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$toplevel" ] || exit 0
[ -f "$toplevel/.claude/auto-worktree" ] || exit 0
[ -f "$toplevel/.claude/auto-worktree-off" ] && exit 0

# The .claude/ carve-out. Resolve the target to an absolute path first: a relative file_path
# would otherwise never match and the hatch would be unreachable.
case "$target" in
  /*) abs="$target" ;;
  "") abs="" ;;
  *)  abs="$toplevel/$target" ;;
esac
case "$abs" in
  "$toplevel"/.claude/*) exit 0 ;;
esac

# Target OUTSIDE the repo: no opinion.
# The guard exists to keep the HUB's tree clean. A file that is not in the repo cannot dirty
# it, and refusing it goes beyond the mandate. Measured: a subagent had its write to a scratch
# directory refused and worked around it with a heredoc, which is exactly the "do not silently
# work around this" the message below forbids. A guard that refuses outside its mandate
# teaches others to work around it.
case "$abs" in
  "$toplevel"/*) : ;;                 # inside the repo: the guard carries on
  "") : ;;                            # unreadable target: let the rest decide
  *) exit 0 ;;                        # outside the repo: nothing to protect here
esac

read -r -d '' reason <<'REASON' || true
This repo is opted into per-session worktree isolation (.claude/auto-worktree exists) and you are writing on the MAIN checkout, which must stay clean.

Isolate first, then retry this edit:
1. Call EnterWorktree with no name argument.
2. Run: bash @POSTENTER@
3. Redo this edit from inside the worktree.

If the user explicitly asked to work on the hub, do not argue with them and do not silently work around this: run `touch .claude/auto-worktree-off` to disable the guard for this repo, and tell them you did.
REASON
reason="${reason//@POSTENTER@/$ROOT/scripts/postenter.sh}"

# Witness: one row in the refusal ledger. Never alters the decision below.
sid="$(printf '%s' "$input" | jq -r '.session_id // empty' 2>/dev/null || true)"
python3 "$ROOT/gatekit/trace.py" witness worktree-guard PreToolUse hub_edit_needs_worktree ${sid:+--session "$sid"} >/dev/null 2>&1 || true

jq -n --arg r "$reason" \
  '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $r}}' \
  2>/dev/null || printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Repo opted into worktree isolation. Call EnterWorktree, run bash %s/scripts/postenter.sh, then redo this edit. To work on the hub: touch .claude/auto-worktree-off"}}\n' "$ROOT"
exit 0
