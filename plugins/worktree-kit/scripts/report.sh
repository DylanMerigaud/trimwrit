#!/bin/bash
# report.sh: the state of every worktree, for a periodic review.
#
# WHY IT EXISTS. An old end-of-session janitor automatically deleted the worktrees that were
# "clean and pushed", and that gate erased corpora worth hours of harvesting: it asked git, and
# git does not see what is gitignored (see cleanup.sh). The automatic deletion is gone. This
# report replaces it with a gesture where a human looks before erasing.
#
# WHAT IT SHOWS, and the last columns are the ones the old gate was missing:
#   AGE        from the ages file, written by cleanup.sh on first sight.
#   UNPUSHED   commits present here and on NO remote.
#   UNMERGED   commits with no equivalent on the remote default branch. PUSHED but never
#              merged, so `git log <remote>/main` never shows them and nobody reads them. A
#              worktree sat on 8 commits of harvest that were all pushed, so UNPUSHED scored 0
#              and its line looked settled. Counted with `git cherry`, so a commit that reached
#              the default branch by cherry-pick or rebase does not come back here forever
#              under a different sha. Also the trailing section for BRANCHES WITHOUT A WORKTREE,
#              which the per-worktree table cannot see.
#   DIRTY      tracked files modified, or untracked ones.
#   IGNORED    weight of the gitignored files. That is the blind spot that cost 1186 reviews,
#              12545 messages and 44328 job postings: invisible to `status` as to `rev-list`,
#              so a worktree carrying only those passed for empty.
#
# UNMERGED reads the remote default branch AS OF THE LAST FETCH, and fetches nothing itself:
# this report walks every worktree of every listed repository, and waiting on that many
# networks would turn a glance into a coffee break. postenter.sh fetches at every session
# start, so the ref is fresh on any repository actually being worked in. A repository with no
# remote default branch scores "?", never 0: doubt keeps the worktree.
#
# It erases NOTHING and does not even offer it as a ready-made command: the deletion is typed
# by hand, worktree by worktree, after reading its line.
#
# Repositories: the lines of the configured `roster` file; with no roster, the repository of the
# current directory only. The ages file is the configured `ages_file`, else
# ${CLAUDE_PLUGIN_DATA:-$HOME/.claude/trimwrit-gates}/worktree-ages.tsv.
#
# Usage: bash <plugin root>/scripts/report.sh
# Compatible with /bin/bash 3.2.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Settings: load_config, validated in one place.
. "$ROOT/scripts/_config.sh"

load_config "$PWD"
REMOTE="$cfg_remote"
AGES="$cfg_ages"
[ -n "$AGES" ] || AGES="${CLAUDE_PLUGIN_DATA:-$HOME/.claude/trimwrit-gates}/worktree-ages.tsv"
mkdir -p "$(dirname "$AGES")" 2>/dev/null || true
[ -f "$AGES" ] || printf '# path\tfirst_seen\n' > "$AGES" 2>/dev/null || true

# The repositories to walk, one per line.
REPOS=""
if [ -n "$cfg_roster" ] && [ -f "$cfg_roster" ]; then
  REPOS="$(grep -v -e '^[[:space:]]*$' -e '^#' "$cfg_roster" 2>/dev/null || true)"
else
  REPOS="$(git worktree list --porcelain 2>/dev/null | awk '/^worktree /{if (!seen) {print substr($0, 10); seen=1}}')"
fi

today=$(date +%Y-%m-%d)

# Days between two ISO dates, "?" when either is unreadable. Python, so it runs on Linux and macOS.
days_between() {
  python3 -c 'import datetime, sys
try:
    print((datetime.date.fromisoformat(sys.argv[1]) - datetime.date.fromisoformat(sys.argv[2])).days)
except Exception:
    print("?")' "$1" "$2" 2>/dev/null || echo "?"
}

# The remote default branch ref of a repository, as of the last fetch.
default_ref() {
  local ref
  ref=$(git -C "$1" symbolic-ref --quiet --short "refs/remotes/$REMOTE/HEAD" 2>/dev/null)
  [ -n "$ref" ] || ref="$REMOTE/main"
  printf '%s' "$ref"
}

printf '%-20s %-34s %6s %9s %9s %6s %9s\n' \
  "REPO" "WORKTREE" "AGE" "UNPUSHED" "UNMERGED" "DIRTY" "IGNORED"
printf '%s\n' "----------------------------------------------------------------------------------------------"

while IFS= read -r repo; do
  [ -n "$repo" ] || continue
  [ -d "$repo/.git" ] || [ -f "$repo/.git" ] || continue
  git -C "$repo" worktree list --porcelain 2>/dev/null | awk '/^worktree /{print substr($0, 10)}' | \
  while IFS= read -r wt; do
    case "$wt" in */.claude/worktrees/*) : ;; *) continue ;; esac
    [ -d "$wt" ] || continue

    # cleanup.sh only dates the repo of the current session. This report walks through all of
    # them: it records what it discovers, otherwise the worktrees of the other repos would show
    # "?" forever. So the date is "first seen", not "creation", and that is what the column says.
    seen=$(grep -F "$wt	" "$AGES" 2>/dev/null | head -1 | cut -f2)
    if [ -z "$seen" ]; then
      printf '%s\t%s\n' "$wt" "$today" >> "$AGES"
      seen="$today"
    fi
    n=$(days_between "$today" "$seen")
    if [ "$n" = "?" ]; then age="?"; else age="${n}d"; fi

    # Any error counts as "there is something here": doubt keeps the worktree, as in the old
    # gate. What changes is that none of these columns triggers anything on its own any more.
    unpushed=$(git -C "$wt" rev-list --count HEAD --not --remotes 2>/dev/null || echo "?")

    head_ref=$(default_ref "$wt")
    if git -C "$wt" rev-parse --verify --quiet "$head_ref" >/dev/null 2>&1; then
      unmerged=$(git -C "$wt" cherry "$head_ref" HEAD 2>/dev/null | awk '$1 == "+"' | wc -l | tr -d ' ')
    else
      unmerged="?"
    fi

    dirty=$(git -C "$wt" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
    ignored=$(git -C "$wt" status --porcelain --ignored 2>/dev/null | grep -c '^!!' | tr -d ' ')
    weight=""
    if [ "${ignored:-0}" -gt 0 ] 2>/dev/null; then
      weight=$(git -C "$wt" status --porcelain --ignored 2>/dev/null | sed -n 's/^!! //p' | \
               (cd "$wt" && xargs -I{} du -sk "{}" 2>/dev/null) | \
               awk '{s+=$1} END {if (s>1024) printf "%dMB", s/1024; else printf "%dKB", s}')
    fi

    printf '%-20s %-34s %6s %9s %9s %6s %9s\n' \
      "$(basename "$repo")" "$(basename "$wt")" "$age" "$unpushed" "$unmerged" "$dirty" "${weight:-0}"
  done
done <<LIST
$REPOS
LIST

# Branches with unmerged work whose WORKTREE is gone: the per-worktree table above cannot see
# them. Same counting rules as UNMERGED (git cherry against the remote default branch, as of
# the last fetch). A branch held by ANY worktree is skipped: the table already carries it.
first=1
while IFS= read -r repo; do
  [ -n "$repo" ] || continue
  [ -d "$repo/.git" ] || [ -f "$repo/.git" ] || continue
  head_ref=$(default_ref "$repo")
  git -C "$repo" rev-parse --verify --quiet "$head_ref" >/dev/null 2>&1 || continue
  held=$(git -C "$repo" worktree list --porcelain 2>/dev/null | \
         awk '/^branch /{sub("refs/heads/", "", $2); print $2}')
  while IFS= read -r b; do
    [ -z "$b" ] && continue
    printf '%s\n' "$held" | grep -qxF "$b" && continue
    new=$(git -C "$repo" cherry "$head_ref" "$b" 2>/dev/null | awk '$1 == "+"' | wc -l | tr -d ' ')
    if [ "${new:-0}" -gt 0 ] 2>/dev/null; then
      if [ "$first" -eq 1 ]; then
        printf '\nBRANCHES WITHOUT A WORKTREE, work absent from the remote default branch\n'
        printf '(merge it or abandon it explicitly, nothing is deleted here):\n'
        first=0
      fi
      printf '  %-24s %s (+%s)\n' "$(basename "$repo")" "$b" "$new"
    fi
  done < <(git -C "$repo" branch --no-merged "$head_ref" --format='%(refname:short)' 2>/dev/null)
done <<LIST
$REPOS
LIST

printf '\n'
printf 'Nothing was deleted. To remove one, after reading its line:\n'
printf '    git -C <repo> worktree remove <path>\n'
printf 'The IGNORED column is the one that cost hours of harvesting: it counts in NEITHER\n'
printf '"unpushed" NOR "dirty", and the old automatic deletion missed it.\n'
printf 'The UNMERGED column is PUSHED work nobody reads: it is neither on the default branch nor\n'
printf 'in any log one consults. A non-zero figure is merged or abandoned explicitly, it is not\n'
printf 'left to sleep.\n'
exit 0
