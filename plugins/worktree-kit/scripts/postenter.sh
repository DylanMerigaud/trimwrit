#!/bin/bash
# Runs INSIDE the freshly entered worktree, invoked by Claude per the SessionStart directive.
# Five jobs:
#   (0) fast-forward this fresh branch onto the remote, because EnterWorktree branches from the
#       LOCAL head
#   (a) make the hub ignore .claude/worktrees/ (local only, via .git/info/exclude)
#   (b) symlink the hub's gitignored config files into this worktree (absolute targets, any depth)
#   (b2) run the configured postenter_after commands
#   (c) install dependencies for the detected package manager (skip if no manifest)
#
# This script NEVER creates a worktree (EnterWorktree owns creation) and NEVER writes hub source
# files. The only hub mutation is an idempotent append to .git/info/exclude.
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

command -v git >/dev/null 2>&1 || { echo "postenter: git not found; skipping." >&2; exit 0; }

# Worktree root = current toplevel.
WT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$WT" ] || { echo "postenter: not in a git repo; skipping." >&2; exit 0; }
cd "$WT" || exit 0

load_config "$WT"
REMOTE="$CFG_REMOTE"
LINK_EXTRA=()
while IFS= read -r g; do
  if [ -n "$g" ]; then LINK_EXTRA+=("$g"); fi
done <<LIST
$CFG_LINK_EXTRA
LIST

# A file also links when its path or its basename matches a configured link_extra glob.
extra_match() {
  local rel="$1" g
  [ "${#LINK_EXTRA[@]}" -gt 0 ] || return 1
  for g in "${LINK_EXTRA[@]}"; do
    [ -n "$g" ] || continue
    # shellcheck disable=SC2053
    [[ "$rel" == $g || "${rel##*/}" == $g ]] && return 0
  done
  return 1
}

# Hub = the first entry of `git worktree list` (always the main working tree).
#
# awk must NOT `exit` here, and this is not style. `exit` closes the pipe while git is still
# writing, git takes SIGPIPE, the pipeline returns 141, and `set -euo pipefail` kills this whole
# script on that line. Everything below, symlinks included, never runs.
#
# THE BUG ARMS ITSELF AS WORKTREES ACCUMULATE, which is why it hid for weeks. With a handful of
# worktrees git finishes writing before awk exits and nothing happens. Measured on a session
# that fanned out about 20 agents: `git worktree list --porcelain` reached 418 lines and the
# script began dying every time. Early agents got their symlinks, late agents did not, from the
# same command, on the same machine, an hour apart.
#
# The failure is SILENT where it matters: no .env, no database, no OAuth tokens, and the agent
# discovers it much later as "script died on arrival" or "no token". Reading the whole stream
# costs nothing, so the flag-and-keep-reading form below is strictly better than swallowing the
# 141 with `|| true`, which would leave HUB empty and skip the symlinks anyway, more quietly.
HUB="$(git worktree list --porcelain 2>/dev/null | awk '/^worktree /{if (!seen) {print substr($0, 10); seen=1}}')"
if [ -z "$HUB" ] || [ ! -d "$HUB" ]; then
  echo "postenter: could not resolve hub working tree; skipping symlinks." >&2
  HUB=""
fi

# The branch to catch up on: the hub's own branch, else the remote's default branch, else main.
base=""
if [ -n "$HUB" ]; then
  base="$(git -C "$HUB" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
fi
if [ -z "$base" ]; then
  base="$(git symbolic-ref --quiet --short "refs/remotes/$REMOTE/HEAD" 2>/dev/null || true)"
  base="${base#"$REMOTE"/}"
fi
[ -n "$base" ] || base=main

# --- (0) catch up with the remote BEFORE everything else ---
# EnterWorktree branches from the LOCAL HEAD, and nothing in this chain did a fetch. The hub
# drifts by construction: every session pushes its worktree branch, the merges into the remote
# default branch happen elsewhere, and nobody brings the local one up to date. It can only get
# older, and every worktree was born with that lag without the session being able to see it.
# Measured: a session worked on a checkout about 200 commits behind. It declared that a
# 223-line file did not exist and rewrote a poor version of it, and it declared a video angle
# impossible to reproduce for want of an mp3 that was present on the remote. Both conclusions
# were served with confidence and were false.
# HERE it is riskless, and it is the only place that is: the branch has just been created and
# carries no commit, so moving it forward is a pure fast-forward. We NEVER touch the hub's own
# branch, whose tree can be dirty or carry unpushed commits.
# Offline or with no remote: we warn and carry on, we never block a startup.
if [ -n "$HUB" ] && git remote get-url "$REMOTE" >/dev/null 2>&1; then
  if git fetch --quiet "$REMOTE" "$base" 2>/dev/null; then
    behind="$(git rev-list --count HEAD..FETCH_HEAD 2>/dev/null || echo 0)"
    if [ "${behind:-0}" -gt 0 ] 2>/dev/null; then
      if git merge --ff-only FETCH_HEAD >/dev/null 2>&1; then
        echo "postenter: caught up $behind commit(s) from $REMOTE/$base"
      else
        echo "postenter: WARNING, this worktree is $behind commit(s) behind $REMOTE/$base and the fast-forward FAILED. Before concluding that a file or an asset is missing, read 'git log HEAD..FETCH_HEAD --name-only'." >&2
      fi
    fi
  else
    echo "postenter: cannot fetch $REMOTE/$base (offline?). This worktree may be stale; do not conclude that a file is missing without fetching again." >&2
  fi
fi

# --- (a) ensure the hub ignores .claude/worktrees/ (no commit, no tracked-file change) ---
if [ -n "$HUB" ]; then
  if ! git -C "$HUB" check-ignore -q .claude/worktrees 2>/dev/null; then
    exclude_file="$(git -C "$HUB" rev-parse --git-common-dir 2>/dev/null)/info/exclude"
    case "$exclude_file" in
      /*) : ;;
      *) exclude_file="$HUB/$exclude_file" ;;
    esac
    mkdir -p "$(dirname "$exclude_file")" 2>/dev/null || true
    if ! grep -qxF '.claude/worktrees/' "$exclude_file" 2>/dev/null; then
      printf '%s\n' '.claude/worktrees/' >> "$exclude_file" \
        && echo "postenter: added .claude/worktrees/ to hub .git/info/exclude"
    fi
  fi
fi

# --- (b) symlink gitignored config and credentials from the hub (absolute targets) ---
if [ -n "$HUB" ]; then
  linked=0
  # `ls-files --others --ignored --exclude-standard` = gitignored files that actually exist in the hub.
  while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    # Match on the BASENAME, not the whole path. These patterns used to be anchored at the repo
    # root (`.env`, `.env.*`), so a monorepo's `app/.env` and `app/.neon_urls.json` were
    # silently skipped and every command that read them died on arrival inside a worktree.
    # The class is "gitignored config or credential file", not "gitignored file at the top level".
    case "$rel" in
      .vercel/project.json|*/.vercel/project.json) : ;;  # so the vercel CLI works, at any depth
      .*/*|*/.*/*) continue ;;   # inside a dot-DIRECTORY: .next, .turbo, .venv, .cache = build junk
      # Dependency and build trees are NOT dot-directories, so the line above misses them, and
      # the credential-shape rule below happily matches a vendored `token.json`. Caught by a
      # match table, not in production: `node_modules/pkg/token.json` was scoring LINK.
      node_modules/*|*/node_modules/*|dist/*|*/dist/*|build/*|*/build/*|target/*|*/target/*|vendor/*|*/vendor/*) continue ;;
      *)
        case "${rel##*/}" in
          .env.example|.DS_Store) continue ;;  # tracked per-branch / OS litter; never link
          # OAuth state. NOT dotfiles, so the dot rule below misses them, and missing them
          # breaks every send path that needs a token in EVERY worktree. LINKED, never copied:
          # the token refreshes itself in place, so a copy forks the OAuth state and the stale
          # half starts failing on its own schedule.
          # Matched by SHAPE rather than by exact names, so the next credential file is covered
          # without another incident. A blanket *.json would be wrong: it would sweep up build
          # manifests and lockfiles, which is how an allowlist stops being one.
          *token*.json|*credential*.json|*secret*.json) : ;;
          # A gitignored dot-FILE in a source directory is config or credentials, never a build
          # artifact (artifacts live in dot-directories, excluded above). Covers .env, .env.local,
          # .env.production and per-project secrets like .neon_urls.json.
          .*) : ;;
          # Anything else links only when the configuration asks for it (link_extra).
          *) extra_match "$rel" || continue ;;
        esac ;;
    esac
    # Don't clobber a real per-worktree override (real file OR an existing symlink, even if broken).
    if [ -e "$WT/$rel" ] || [ -L "$WT/$rel" ]; then
      continue
    fi
    mkdir -p "$WT/$(dirname "$rel")" 2>/dev/null || continue
    if ln -s "$HUB/$rel" "$WT/$rel" 2>/dev/null; then
      linked=$((linked + 1))
    fi
  done < <(git -C "$HUB" ls-files --others --ignored --exclude-standard 2>/dev/null || true)
  if [ "$linked" -gt 0 ]; then echo "postenter: symlinked $linked gitignored file(s) from hub"; fi
fi

# --- (b2) the configured postenter_after commands, run in the worktree ---
python3 "$ROOT/gatekit/config.py" run postenter_after --cwd "$WT" </dev/null \
  || echo "postenter: postenter_after not run (see above)" >&2

# --- (c) install deps for the detected package manager (skip if no manifest) ---
if   [ -f pnpm-lock.yaml ];    then echo "worktree-postenter: pnpm install"; pnpm install
elif [ -f bun.lockb ];         then echo "worktree-postenter: bun install";  bun install
elif [ -f bun.lock ];          then echo "worktree-postenter: bun install";  bun install
elif [ -f yarn.lock ];         then echo "worktree-postenter: yarn install"; yarn install
elif [ -f package-lock.json ]; then echo "worktree-postenter: npm ci"; npm ci || npm install
elif [ -f package.json ];      then echo "worktree-postenter: package.json but no known lockfile; skipping install." >&2
else echo "worktree-postenter: no JS manifest; skipping dependency install."
fi

# Unmerged branches are NOT reported here: that is report.sh, run when someone reviews the
# worktrees. Printing orphans at every worktree entry buried the one line a session needed.

echo "worktree ready: $WT"
