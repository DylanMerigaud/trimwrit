#!/bin/bash
# SessionEnd janitor. IT NEVER ERASES ANYTHING. It dates the worktrees, that is all.
#
# WHAT IT USED TO DO, AND WHAT THAT COST.
# An earlier version deleted every worktree under .claude/worktrees/ that was "clean and
# entirely pushed": `git status --porcelain` empty, and `git rev-list --count HEAD --not
# --remotes` at zero. The gate was carefully reasoned, it failed closed, it never used --force.
# It was wrong all the same, and here is the exact blind spot:
#
#   BOTH TESTS ASK GIT, AND GIT DOES NOT SEE WHAT IS GITIGNORED.
#   An ignored file shows up neither in `status --porcelain` nor in any commit. A worktree
#   stuffed with ignored data is therefore "clean" and "entirely pushed" in the sense of those
#   two commands, and got deleted without a line of warning.
#
# What disappeared that way: 1186 scraped reviews (4 to 6 hours of quota on a site behind
# anti-bot protection), 12545 forum messages, 44328 job postings that no re-run gives back (the
# job boards only serve their current window), and 100 cached pages. The reasoning "clean and
# pushed = disposable" had priced the cost of a mistake at "a few MB".
#
# We could have fixed the gate (add `status --porcelain --ignored`, refuse a worktree that
# carries ignored files). The decision was simpler: nothing is deleted any more. The cost of a
# forgotten worktree is a few MB of disk and a line in a list; the cost of a wrong deletion is
# unrecoverable work. The review is a gesture a human does with report.sh, looking.
#
# WHAT IT DOES NOW:
#   - `git worktree prune`, the only operation left automatic. It touches no file at all: it
#     cleans the registry entries whose directory has ALREADY disappeared.
#   - it records the first-seen date of each worktree in the ages file, so that "how old is
#     that one" has an answer. git does not carry it: the creation date of a directory is not
#     reliable and the first commit of a branch says nothing about the date of the checkout.
#
# The ages file is the configured `ages_file`, else
# ${CLAUDE_PLUGIN_DATA:-$HOME/.claude/trimwrit-gates}/worktree-ages.tsv.
# Compatible with /bin/bash 3.2.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Every setting in one python call, printed as shell assignments. A config error lands in
# CFG_ERROR and the defaults of defaults.json apply: the script never fails over a bad file.
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
    cfg = got
except Exception as e:
    err = "{}: {}".format(type(e).__name__, e).replace("\n", " ")[:300]
for name, value in (("CFG_ERROR", err), ("CFG_ROSTER", os.path.expanduser(cfg["roster"])),
                    ("CFG_AGES", os.path.expanduser(cfg["ages_file"])),
                    ("CFG_REMOTE", cfg["remote"])):
    print("{}={}".format(name, shlex.quote(value)))
PY
)"
  CFG_ERROR="python3 failed"; CFG_ROSTER=""; CFG_AGES=""; CFG_REMOTE="origin"
  case "$out" in
    CFG_ERROR=*) eval "$out" ;;
  esac
  if [ -n "$CFG_ERROR" ]; then
    echo "worktree-kit: config error, defaults applied: $CFG_ERROR" >&2
  fi
}

input="$(cat 2>/dev/null || true)"

json_field() {
  local field="$1"
  if [ -n "$input" ] && command -v jq >/dev/null 2>&1; then
    printf '%s' "$input" | jq -r --arg k "$field" '.[$k] // empty' 2>/dev/null && return 0
  fi
  printf '%s' "$input" | sed -n "s/.*\"$field\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n1
}

reason="$(json_field reason)"
[ "$reason" = "resume" ] && exit 0

cwd="$(json_field cwd)"
cd "${cwd:-${PWD:-.}}" 2>/dev/null || exit 0

command -v git >/dev/null 2>&1 || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

load_config "$PWD"
AGES="$CFG_AGES"
[ -n "$AGES" ] || AGES="${CLAUDE_PLUGIN_DATA:-$HOME/.claude/trimwrit-gates}/worktree-ages.tsv"

# Registry entries whose directory no longer exists. No file is touched.
git worktree prune 2>/dev/null || true

# First-seen date, one line per worktree, never rewritten afterwards: that is what makes it
# useful. A `touch` or a modification date would move forward on every pass and the age would
# always be zero.
today="$(date +%Y-%m-%d)"
mkdir -p "$(dirname "$AGES")" 2>/dev/null || true
[ -f "$AGES" ] || printf '# path\tfirst_seen\n' > "$AGES"

git worktree list --porcelain 2>/dev/null | awk '/^worktree /{print substr($0, 10)}' | while IFS= read -r wt; do
  [ -n "$wt" ] || continue
  case "$wt" in */.claude/worktrees/*) : ;; *) continue ;; esac
  [ -d "$wt" ] || continue
  grep -qF "$wt	" "$AGES" 2>/dev/null && continue
  printf '%s\t%s\n' "$wt" "$today" >> "$AGES"
done

exit 0
