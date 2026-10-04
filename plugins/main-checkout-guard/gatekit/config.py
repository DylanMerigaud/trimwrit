#!/usr/bin/env python3
"""gatekit.config: the one configuration file of the trimwrit gates.

Two layers, merged per section, project over user, key by key:
  user     ~/.claude/trimwrit-gates.json      (or DIR/trimwrit-gates.json under --home DIR)
  project  <project>/.claude/trimwrit-gates.json, found through CLAUDE_PROJECT_DIR, else by
           walking up from the payload cwd

A plugin declares its keys in its own defaults.json ({"section": ..., "defaults": {...}}). A key
the plugin does not declare is a ConfigError: a typo must be loud, never a setting silently
ignored. No key turns a gate off; a gate is on when its plugin is enabled, and enabling or
disabling the plugin is the only switch.

CLI, for the bash plugins (the plugin root is the parent of this file's directory):
  python3 gatekit/config.py get KEY   [--cwd DIR] [--home DIR]   one JSON value
  python3 gatekit/config.py lines KEY [--cwd DIR] [--home DIR]   one list item per line
  python3 gatekit/config.py run KEY   [--cwd DIR] [--home DIR]   run each argv of a list of
                                                                  argv lists, stdin forwarded,
                                                                  always exit 0
"""
import json
import os
import shlex
import subprocess
import sys

FILE = "trimwrit-gates.json"
MAX_UP = 12
RUN_TIMEOUT_S = 200


class ConfigError(ValueError):
    """The configuration is unreadable, or names a key the plugin does not declare."""


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise ConfigError("{}: {}".format(path, e))
    if not isinstance(data, dict):
        raise ConfigError("{}: the top level is not a JSON object".format(path))
    return data


def user_path(home=None):
    if home:
        return os.path.join(home, FILE)
    return os.path.join(os.path.expanduser("~"), ".claude", FILE)


def project_path(payload=None, home=None):
    """The project layer, or None. CLAUDE_PROJECT_DIR first (the project whose settings enabled
    the plugin), then the nearest .claude/trimwrit-gates.json above the payload cwd. The user
    layer itself is never taken for a project layer: neither the --home file nor the real
    ~/.claude one, so a walk up from the home directory cannot count it twice."""
    user = os.path.abspath(user_path(home))
    real_user = os.path.abspath(os.path.join(os.path.expanduser("~"), ".claude", FILE))
    root = os.environ.get("CLAUDE_PROJECT_DIR")
    if root:
        cand = os.path.join(root, ".claude", FILE)
        if os.path.isfile(cand) and os.path.abspath(cand) not in (user, real_user):
            return cand
    here = os.path.abspath((payload or {}).get("cwd") or os.getcwd())
    for _ in range(MAX_UP):
        cand = os.path.join(here, ".claude", FILE)
        if os.path.isfile(cand) and os.path.abspath(cand) not in (user, real_user):
            return cand
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return None


def load(section, defaults, payload=None, home=None):
    """DEFAULTS, then the user layer, then the project layer, for one section."""
    out = dict(defaults)
    layers = [user_path(home)]
    proj = project_path(payload, home)
    if proj:
        layers.append(proj)
    for path in layers:
        sec = _read(path).get(section)
        if sec is None:
            continue
        if not isinstance(sec, dict):
            raise ConfigError("{}: section {!r} is not an object".format(path, section))
        unknown = sorted(set(sec) - set(defaults))
        if unknown:
            raise ConfigError("{}: section {!r} declares no key {}".format(
                path, section, ", ".join(unknown)))
        for key, value in sec.items():
            want = type(defaults[key])
            if defaults[key] is not None and not isinstance(value, want):
                raise ConfigError("{}: {}.{} must be a {}, got {}".format(
                    path, section, key, want.__name__, type(value).__name__))
            out[key] = value
    return out


def plugin_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def plugin_defaults(root=None):
    path = os.path.join(root or plugin_root(), "defaults.json")
    data = _read(path)
    if not data:
        raise ConfigError("{}: missing or empty".format(path))
    section, defaults = data.get("section"), data.get("defaults")
    if not isinstance(section, str) or not isinstance(defaults, dict):
        raise ConfigError("{}: needs a string section and an object defaults".format(path))
    return section, defaults


def load_plugin(payload=None, home=None, root=None):
    section, defaults = plugin_defaults(root)
    return load(section, defaults, payload, home)


def expand(word):
    return os.path.expanduser(word) if isinstance(word, str) and word.startswith("~") else word


def _opt(argv, name):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            value = argv[i + 1]
            del argv[i:i + 2]
            return value
        raise SystemExit("{} needs a value".format(name))
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    home = _opt(argv, "--home")
    cwd = _opt(argv, "--cwd")
    if len(argv) != 2 or argv[0] not in ("get", "lines", "run"):
        sys.stderr.write(__doc__.split("CLI,")[1] if "CLI," in __doc__ else "usage\n")
        return 64
    verb, key = argv
    cfg = load_plugin({"cwd": cwd} if cwd else None, home)
    if key not in cfg:
        raise SystemExit("no key {!r} in this plugin's defaults.json".format(key))
    value = cfg[key]
    if verb == "get":
        print(json.dumps(value))
        return 0
    if not isinstance(value, list):
        raise SystemExit("{} is not a list".format(key))
    if verb == "lines":
        for item in value:
            print(shlex.join([expand(w) for w in item]) if isinstance(item, list) else expand(item))
        return 0
    stdin = sys.stdin.buffer.read() if not sys.stdin.isatty() else b""
    for item in value:
        if not isinstance(item, list) or not item:
            sys.stderr.write("config run: {!r} is not an argv list, skipped\n".format(item))
            continue
        args = [expand(w) for w in item]
        try:
            rc = subprocess.run(args, input=stdin, cwd=cwd or os.getcwd(),
                                timeout=RUN_TIMEOUT_S).returncode
        except (OSError, subprocess.TimeoutExpired) as e:
            rc = "error: {}".format(e)
        sys.stderr.write("config run: {} exit {}\n".format(shlex.join(args), rc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
