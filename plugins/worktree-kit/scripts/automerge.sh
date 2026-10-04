#!/bin/bash
# automerge.sh: land the session's worktree branch on the default branch, at session end.
#
# WHY IT EXISTS. Measured cost of not having it: a session delivered a schema column, a weekly
# harvest and its tests, all committed and pushed on its branch, and NONE of that was visible
# from the default branch. A later session reading that branch re-derives work that already
# exists, or worse, redoes it differently.
#
# THE TENSION, AND IT IS OWNED. cleanup.sh has a doctrine written after a deletion incident: no
# destructive automatic action, a report a human reads. This file adds an automatic action. The
# difference that makes it acceptable is REVERSIBILITY: the old janitor DELETED, and an erased
# harvest does not come back. Here we ADD a commit on the default branch, which a revert undoes
# and which destroys nothing.
#
# THE JANITOR'S BLIND SPOT DOES NOT BITE HERE. `git status --porcelain` does not see gitignored
# files. For the old janitor that was fatal: it concluded "empty, therefore deletable". Here we
# only conclude "nothing to commit, therefore mergeable", and an ignored file has by definition
# nothing to do in a commit.
#
# THE FIVE CONDITIONS, all required, failure closes at each step:
#   1. we really are in a worktree under .claude/worktrees/;
#   2. the tree is clean (nothing tracked pending): we never merge half-committed work;
#   3. there is at least one commit that the remote default branch does not have;
#   4. the repo carries a GATE, .claude/automerge-gate.sh, and it exits 0. No file = no merge.
#      That is the heart of the safety: the mechanism is generic, but ONLY THE REPO knows what
#      "safe to merge" means at home, and it has to declare it;
#   5. the rebase onto the remote default branch goes through WITHOUT conflict. A conflict
#      means two sessions touched the same thing, and that is precisely the case where a human
#      must read. We abort the rebase and leave the branch exactly as it was.
#
# It NEVER forces anything. If the push is refused (another session pushed in the meantime), it
# says so and stops: the loser of the race keeps its branch intact.
#
# The remote is the configured `remote`; the default branch is the remote's HEAD branch, else
# main. It is never the hub's checked-out branch (postenter.sh catches up on that one, which
# only reads).
#
# Usage: called by the SessionEnd hook through session_end.sh, which feeds the payload on stdin.
# Compatible with /bin/bash 3.2.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Settings: load_config, validated in one place.
. "$ROOT/scripts/_config.sh"

input="$(cat 2>/dev/null || true)"

json_field() {
  local field="$1"
  if [ -n "$input" ] && command -v jq >/dev/null 2>&1; then
    printf '%s' "$input" | jq -r --arg k "$field" '.[$k] // empty' 2>/dev/null && return 0
  fi
  printf '%s' "$input" | sed -n "s/.*\"$field\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n1
}

say() { printf 'automerge: %s\n' "$1" >&2; }

# `resume` is not a session end: the session starts again, the work continues.
[ "$(json_field reason)" = "resume" ] && exit 0

cwd="$(json_field cwd)"
cd "${cwd:-${PWD:-.}}" 2>/dev/null || exit 0

command -v git >/dev/null 2>&1 || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

# 1. only from an auto worktree, never from the main repo.
case "$PWD" in */.claude/worktrees/*) : ;; *) exit 0 ;; esac

top="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
branch="$(git -C "$top" rev-parse --abbrev-ref HEAD 2>/dev/null)" || exit 0

load_config "$top"
REMOTE="$cfg_remote"

# The default branch: the remote's HEAD branch, else main. NEVER the hub's checked-out
# branch: a hub sitting on a feature branch would otherwise receive the push of every
# session that ends.
default="$(git -C "$top" symbolic-ref --quiet --short "refs/remotes/$REMOTE/HEAD" 2>/dev/null || true)"
default="${default#"$REMOTE"/}"
[ -n "$default" ] || default=main

[ "$branch" = "HEAD" ] && { say "detached HEAD, nothing to merge"; exit 0; }
[ "$branch" = "$default" ] && exit 0

# 2. clean tree. Half-committed work is not work to merge.
if [ -n "$(git -C "$top" status --porcelain 2>/dev/null)" ]; then
  say "dirty tree on $branch: nothing merged (commit first)"
  exit 0
fi

git -C "$top" fetch "$REMOTE" "$default" --quiet 2>/dev/null || { say "cannot fetch $REMOTE/$default, stopping"; exit 0; }

# 3. is there anything to merge.
ahead="$(git -C "$top" rev-list --count "$REMOTE/$default"..HEAD 2>/dev/null || echo 0)"
[ "${ahead:-0}" -gt 0 ] 2>/dev/null || exit 0

# 4. THE REPO'S GATE. Absent = no merge, and that is the intended default: a repo must
#    explicitly declare that it accepts this mechanism, and say for itself what it checks.
gate="$top/.claude/automerge-gate.sh"
if [ ! -f "$gate" ]; then
  say "no .claude/automerge-gate.sh in this repository: $ahead commit(s) stay on $branch"
  exit 0
fi
if ! (cd "$top" && bash "$gate" >/dev/null 2>&1); then
  say "the repository's gate REFUSES the merge. Detail:"
  (cd "$top" && bash "$gate" 2>&1 | tail -15 >&2)
  say "$ahead commit(s) stay on $branch"
  exit 0
fi

# 5. rebase without conflict, otherwise we put everything back as it was.
before="$(git -C "$top" rev-parse HEAD)"
if ! git -C "$top" rebase "$REMOTE/$default" --quiet >/dev/null 2>&1; then
  git -C "$top" rebase --abort >/dev/null 2>&1 || true
  say "CONFLICT with $REMOTE/$default: two sessions touched the same thing, a human must read."
  say "branch left untouched at $before"
  exit 0
fi

# Never --force. A refusal means another session pushed between our rebase and our push, which
# is FREQUENT when several sessions end together. We retry ONCE, re-rebasing onto the brand-new
# default branch: without that, the loser of each race leaves its work on its branch and it
# takes a human to notice. Two attempts, not a loop: if the branch moves twice within a few
# seconds, something else is going on and a human must read.
for attempt in 1 2; do
  if git -C "$top" push "$REMOTE" "HEAD:$default" --quiet 2>/dev/null; then
    say "$ahead commit(s) of $branch merged into $default (attempt $attempt)."
    exit 0
  fi
  [ "$attempt" = 2 ] && break
  say "push refused ($default moved). Rebasing again and retrying once."
  git -C "$top" fetch "$REMOTE" "$default" --quiet 2>/dev/null || break
  if ! git -C "$top" rebase "$REMOTE/$default" --quiet >/dev/null 2>&1; then
    git -C "$top" rebase --abort >/dev/null 2>&1 || true
    say "CONFLICT on the second rebase: a human must read. Branch untouched."
    exit 0
  fi
done
say "push still refused. Branch rebased, nothing lost:"
say "  git -C $top push $REMOTE HEAD:$default"
exit 0
