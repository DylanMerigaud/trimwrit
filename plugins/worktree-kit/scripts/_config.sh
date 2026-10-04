#!/bin/bash
# _config.sh: sourced by every worktree-kit script, never run as a hook.
#
# load_config DIR reads the plugin's settings (user layer, then the project layer found from DIR)
# in ONE python3 call that prints shell-quoted assignments, and validates every key the plugin
# declares in this one place. It sets cfg_error, cfg_roster, cfg_ages, cfg_remote and
# cfg_link_extra (one glob per line).
#
# A config error never stops a script: cfg_error carries it, one loud line goes to stderr and the
# defaults apply. That includes python3 itself failing or defaults.json being unreadable, so the
# call is guarded against `set -e` and the fallback below always runs.
# A `remote` that is empty or starts with `-` is refused: it would reach `git fetch` or
# `git push` as an option (`--upload-pack=...` runs a command).
# Compatible with /bin/bash 3.2.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

load_config() {
  local out
  out="$(python3 - "$ROOT" "$1" <<'PY' 2>/dev/null
import json, os, shlex, sys
root, cwd = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
cfg = {"roster": "", "ages_file": "", "remote": "origin", "link_extra": []}
err = ""
try:
    from gatekit import config
    with open(os.path.join(root, "defaults.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)["defaults"]
    got = config.load_plugin({"cwd": cwd}, None, root)
    for key in ("roster", "ages_file", "remote"):
        if not isinstance(got[key], str):
            raise config.ConfigError("{} must be a string".format(key))
    if not got["remote"].strip() or got["remote"].startswith("-"):
        raise config.ConfigError("remote must be a name, not empty and not starting with '-'")
    if not all(isinstance(g, str) for g in got["link_extra"]):
        raise config.ConfigError("link_extra must be a list of strings")
    for key in ("postenter_after", "session_end_after"):
        if not all(isinstance(a, list) and all(isinstance(w, str) for w in a) for a in got[key]):
            raise config.ConfigError("{} must be a list of argv lists of strings".format(key))
    cfg = got
except BaseException as e:
    err = "{}: {}".format(type(e).__name__, e).replace("\n", " ")[:300]
for name, value in (("cfg_error", err), ("cfg_roster", os.path.expanduser(cfg["roster"])),
                    ("cfg_ages", os.path.expanduser(cfg["ages_file"])),
                    ("cfg_remote", cfg["remote"]), ("cfg_link_extra", "\n".join(cfg["link_extra"]))):
    print("{}={}".format(name, shlex.quote(value)))
PY
)" || true
  cfg_error="python3 failed"; cfg_roster=""; cfg_ages=""; cfg_remote="origin"; cfg_link_extra=""
  case "$out" in
    cfg_error=*) eval "$out" ;;
  esac
  if [ -n "$cfg_error" ]; then
    echo "worktree-kit: config error, defaults applied: $cfg_error" >&2
  fi
}
