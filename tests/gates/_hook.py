"""Helpers to run a plugin hook the way Claude Code does: a payload on stdin, --home on argv,
no inherited CLAUDE_* or ANTHROPIC_* variable."""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def plugin(name):
    return os.path.join(ROOT, "plugins", name)


_SCRATCH = []


def scratch_cwd():
    """A directory outside any project, so a hook run from it never finds a live config."""
    if not _SCRATCH:
        _SCRATCH.append(tempfile.mkdtemp(prefix="gates-cwd-"))
    return _SCRATCH[0]


def clean_env(extra=None):
    """The parent env minus every CLAUDE_* and ANTHROPIC_* variable (CLAUDE_PROJECT_DIR,
    CLAUDE_SESSION_ID and CLAUDE_PLUGIN_DATA among them), unless a test passes them in extra."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "ANTHROPIC_"))}
    env.update(extra or {})
    return env


def run_py(name, script, payload, home, *args, env=None, cwd=None):
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, os.path.join(plugin(name), "scripts", script),
                           "--home", str(home), *args], input=stdin, capture_output=True,
                          text=True, env=clean_env(env), cwd=cwd or str(home), timeout=60)


def run_sh(name, script, payload, *args, env=None, cwd=None):
    stdin = "" if payload is None else (payload if isinstance(payload, str) else json.dumps(payload))
    return subprocess.run(["bash", os.path.join(plugin(name), "scripts", script), *args],
                          input=stdin, capture_output=True, text=True, env=clean_env(env),
                          cwd=cwd or scratch_cwd(), timeout=120)


def write_config(home, data):
    os.makedirs(str(home), exist_ok=True)
    with open(os.path.join(str(home), "trimwrit-gates.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def rows(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except FileNotFoundError:
        return []
