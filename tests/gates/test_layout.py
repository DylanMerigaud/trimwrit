"""What every gate plugin must be, read from the tree itself."""
import ast
import json
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGINS = os.path.join(ROOT, "plugins")
NAMES = sorted(d for d in os.listdir(PLUGINS)
               if os.path.isdir(os.path.join(PLUGINS, d, ".claude-plugin"))) \
    if os.path.isdir(PLUGINS) else []
SWITCH_KEY = re.compile(r"(?:^|_)(?:enable|enabled|disable|disabled|bypass|skip|off|allow_all)"
                        r"(?:_|$)")
ALLOWED_ENV = {"CLAUDE_SESSION_ID", "CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_DATA"}
REMOVED_SWITCHES = ("AUTO_WORKTREE_ROSTER", "COCKPIT_NO_STOP_GATE", "no-stop-gate",
                    "CLAUDE_ALLOW_DASH")


def load(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return json.load(fh)


MARKET = load(ROOT, ".claude-plugin", "marketplace.json")
ENTRIES = {e["name"]: e for e in MARKET["plugins"]}


def test_vendored_copies_match_their_source():
    out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "vendor_gates.py"),
                          "--check"], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout


def test_root_versions_agree():
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as fh:
        py = re.search(r'^version = "([^"]+)"', fh.read(), re.M).group(1)
    with open(os.path.join(ROOT, "trimwrit", "__init__.py"), encoding="utf-8") as fh:
        init = re.search(r'__version__ = "([^"]+)"', fh.read()).group(1)
    plugin = load(ROOT, ".claude-plugin", "plugin.json")["version"]
    assert py == init == plugin == ENTRIES["trimwrit"]["version"]


def test_every_entry_has_a_directory():
    for name, entry in ENTRIES.items():
        if name == "trimwrit":
            assert entry["source"] == "./"
            continue
        assert entry["source"] == "./plugins/" + name
        assert name in NAMES


@pytest.mark.parametrize("name", NAMES)
def test_plugin_manifest(name):
    manifest = load(PLUGINS, name, ".claude-plugin", "plugin.json")
    assert manifest["name"] == name
    assert name in ENTRIES, "no marketplace entry for " + name
    assert manifest["version"] == ENTRIES[name]["version"]
    assert manifest["description"] == ENTRIES[name]["description"]
    assert manifest["license"] == "MIT"
    has_evals = os.path.isdir(os.path.join(PLUGINS, name, "evals"))
    assert (manifest.get("experimental", {}).get("evals") == "evals") == has_evals


@pytest.mark.parametrize("name", NAMES)
def test_hooks_json(name):
    hooks = load(PLUGINS, name, "hooks", "hooks.json")["hooks"]
    for event, groups in hooks.items():
        for group in groups:
            for h in group["hooks"]:
                assert h["type"] == "command"
                assert '"${CLAUDE_PLUGIN_ROOT}/scripts/' in h["command"], h["command"]
                assert isinstance(h.get("timeout"), int), (event, h)
                assert "--home" not in h["command"]
                script = re.search(r'scripts/([\w.-]+)"', h["command"]).group(1)
                path = os.path.join(PLUGINS, name, "scripts", script)
                assert os.path.isfile(path), path
                assert os.access(path, os.X_OK), path + " is not executable"


@pytest.mark.parametrize("name", NAMES)
def test_defaults_declare_no_switch(name):
    data = load(PLUGINS, name, "defaults.json")
    assert data["section"] == name
    for key in data["defaults"]:
        assert not SWITCH_KEY.search(key), "{}: {} reads like a switch".format(name, key)


def gate_sources():
    out = [os.path.join(ROOT, "gates", "gatekit", f)
           for f in os.listdir(os.path.join(ROOT, "gates", "gatekit")) if f.endswith(".py")]
    for name in NAMES:
        d = os.path.join(PLUGINS, name, "scripts")
        out += [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".py")]
    return out


def env_names(path):
    """Every environment variable a source reads, a module-level string constant resolved to
    its value (`os.environ.get(ENV_DATA)` counts as CLAUDE_PLUGIN_DATA), anything else
    reported as <dynamic>."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    consts[target.id] = node.value.value

    def value(arg):
        if isinstance(arg, ast.Constant):
            return arg.value
        if isinstance(arg, ast.Name) and arg.id in consts:
            return consts[arg.id]
        return "<dynamic>"

    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            f = node.func
            is_get = f.attr == "get" and isinstance(f.value, ast.Attribute) and f.value.attr == "environ"
            if (is_get or f.attr == "getenv") and node.args:
                names.add(value(node.args[0]))
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
                and node.value.attr == "environ":
            names.add(value(node.slice))
    return names


@pytest.mark.parametrize("path", gate_sources(), ids=lambda p: os.path.relpath(p, ROOT))
def test_gate_reads_only_declared_environment(path):
    assert env_names(path) <= ALLOWED_ENV, sorted(env_names(path) - ALLOWED_ENV)


def test_no_removed_switch_is_named_anywhere():
    for base, _dirs, files in os.walk(PLUGINS):
        for f in files:
            if f.endswith((".py", ".sh", ".json", ".md")):
                text = open(os.path.join(base, f), encoding="utf-8").read()
                for word in REMOVED_SWITCHES:
                    assert word not in text, os.path.join(base, f) + " names " + word
