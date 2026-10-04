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
STOP_GATES = {"no-em-dash", "claim-gate", "promise-gate", "rule-gate"}
BASH_ENV = {"HOME", "PWD", "STY", "TMUX", "TMUX_PANE", "SECONDS", "BASH_SOURCE",
            "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "CLAUDE_PROJECT_DIR"}
SCHEMA = "https://json.schemastore.org/claude-code-plugin-manifest.json"
REPO = "https://github.com/DylanMerigaud/trimwrit"
COMMAND = re.compile(r'^(python3|bash) "\$\{CLAUDE_PLUGIN_ROOT\}/scripts/[\w.-]+\.(py|sh)"(?: --(?!home\b)[\w-]+)?$')
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


def test_no_invisible_plugin():
    dirs = [d for d in os.listdir(PLUGINS) if os.path.isdir(os.path.join(PLUGINS, d))] \
        if os.path.isdir(PLUGINS) else []
    assert sorted(dirs) == NAMES
    assert set(NAMES) <= set(ENTRIES), sorted(set(NAMES) - set(ENTRIES))


@pytest.mark.parametrize("name", NAMES)
def test_plugin_manifest(name):
    m = load(PLUGINS, name, ".claude-plugin", "plugin.json")
    assert m["$schema"] == SCHEMA
    assert m["name"] == name
    assert m["displayName"] == name
    assert m["version"] == "0.1.0"
    assert m["author"] == {"name": "Dylan Merigaud", "url": "https://github.com/DylanMerigaud"}
    assert m["homepage"] == REPO + "/tree/main/plugins/" + name
    assert m["repository"] == REPO
    assert m["license"] == "MIT"
    assert isinstance(m["keywords"], list) and m["keywords"]
    assert m["description"].strip()
    has_evals = os.path.isdir(os.path.join(PLUGINS, name, "evals"))
    assert has_evals == (name in STOP_GATES)
    assert m.get("experimental") == ({"evals": "evals"} if name in STOP_GATES else None)
    e = ENTRIES[name]
    assert e["source"] == "./plugins/" + name
    assert e["version"] == m["version"]
    assert e["description"] == m["description"]
    assert e["author"] == {"name": "Dylan Merigaud"}
    assert e["license"] == "MIT"
    assert e["category"] == "productivity"
    assert e["tags"] == m["keywords"]
    assert os.path.isfile(os.path.join(PLUGINS, name, "README.md"))
    scripts = os.path.join(PLUGINS, name, "scripts")
    for f in os.listdir(scripts):
        assert os.access(os.path.join(scripts, f), os.X_OK), f + " is not executable"


@pytest.mark.parametrize("name", NAMES)
def test_hooks_json(name):
    hooks = load(PLUGINS, name, "hooks", "hooks.json")["hooks"]
    assert hooks
    for event, groups in hooks.items():
        assert groups
        for group in groups:
            assert group["hooks"]
            for h in group["hooks"]:
                assert h["type"] == "command"
                m = COMMAND.match(h["command"])
                assert m, h["command"]
                script = re.search(r'scripts/([\w.-]+)"', h["command"]).group(1)
                assert (m.group(1), m.group(2)) in (("python3", "py"), ("bash", "sh")), h
                assert h["timeout"] == (240 if event == "SessionEnd" else 10), (event, h)
                path = os.path.join(PLUGINS, name, "scripts", script)
                assert os.path.isfile(path), path
                assert os.access(path, os.X_OK), path + " is not executable"


def is_switch(key):
    """camelCase and hyphenated keys are normalized to snake_case before matching."""
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).replace("-", "_").lower()
    return bool(SWITCH_KEY.search(snake))


def all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            for sub in all_keys(v):
                yield sub
    elif isinstance(obj, list):
        for v in obj:
            for sub in all_keys(v):
                yield sub


@pytest.mark.parametrize("name", NAMES)
def test_defaults_declare_no_switch(name):
    data = load(PLUGINS, name, "defaults.json")
    assert data["section"] == name
    assert isinstance(data["defaults"], dict)
    for key in all_keys(data["defaults"]):
        assert not is_switch(key), "{}: {} reads like a switch".format(name, key)


def test_command_regex_bites():
    base = 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/g.py"'
    assert COMMAND.match(base)
    assert COMMAND.match(base + " --prompt")
    for bad in (" --home", " --home /x", " --prompt --x", " --a --b"):
        assert not COMMAND.match(base + bad), bad


def test_switch_detector_bites():
    for bad in ("enabled", "skipChecks", "no-bypass", "allowAll", "turn_off", "Disable"):
        assert is_switch(bad), bad
    assert any(is_switch(k) for k in all_keys({"a": {"b": [{"bypassGate": 1}]}}))
    for ok in ("patterns", "offset_lines", "allowlist", "maxChain"):
        assert not is_switch(ok), ok


def gate_sources():
    out = [os.path.join(ROOT, "gates", "gatekit", f)
           for f in os.listdir(os.path.join(ROOT, "gates", "gatekit")) if f.endswith(".py")]
    for name in NAMES:
        d = os.path.join(PLUGINS, name, "scripts")
        if os.path.isdir(d):
            out += [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".py")]
    return out


def env_names(path):
    with open(path, encoding="utf-8") as fh:
        return env_names_of(fh.read())


def env_names_of(source):
    """Every environment variable a source reads, a module-level string constant resolved to
    its value (`os.environ.get(ENV_DATA)` counts as CLAUDE_PLUGIN_DATA). Any read whose name
    cannot be resolved, any use of the whole mapping (`dict(os.environ)`, `.items()`,
    `.copy()`) and any `from os import environ|getenv` is reported as <dynamic>."""
    tree = ast.parse(source)
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    consts[target.id] = node.value.value

    def value(arg):
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        if isinstance(arg, ast.Name) and arg.id in consts:
            return consts[arg.id]
        return "<dynamic>"

    def is_environ(n):
        return (isinstance(n, ast.Attribute) and n.attr == "environ") \
            or (isinstance(n, ast.Name) and n.id == "environ")

    def is_getenv(n):
        return (isinstance(n, ast.Attribute) and n.attr == "getenv") \
            or (isinstance(n, ast.Name) and n.id == "getenv")

    names, handled = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "os" \
                and any(a.name in ("environ", "getenv") for a in node.names):
            names.add("<dynamic>")
        elif isinstance(node, ast.Call):
            f = node.func
            if is_getenv(f):
                handled.add(id(f))
                names.add(value(node.args[0]) if node.args else "<dynamic>")
            elif isinstance(f, ast.Attribute) and is_environ(f.value) \
                    and f.attr in ("get", "setdefault", "pop"):
                handled.add(id(f.value))
                names.add(value(node.args[0]) if node.args else "<dynamic>")
        elif isinstance(node, ast.Subscript) and is_environ(node.value):
            handled.add(id(node.value))
            names.add(value(node.slice))
        elif isinstance(node, ast.Compare) and len(node.ops) == 1 \
                and isinstance(node.ops[0], (ast.In, ast.NotIn)) \
                and is_environ(node.comparators[0]):
            handled.add(id(node.comparators[0]))
            names.add(value(node.left))
    for node in ast.walk(tree):
        if (is_environ(node) or is_getenv(node)) and id(node) not in handled:
            if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)):
                names.add("<dynamic>")
    return names


def test_env_reader_bites():
    dyn = {"<dynamic>"}
    cases = {
        "import os\nos.environ.get('A')": {"A"},
        "import os\nK='B'\nos.getenv(K)": {"B"},
        "import os\nos.environ['C']": {"C"},
        "import os\n'D' in os.environ": {"D"},
        "import os\nos.environ.setdefault('E', 'x')": {"E"},
        "from os import getenv\ngetenv('F')": {"F", "<dynamic>"},
        "from os import environ\nenviron.get('G')": {"G", "<dynamic>"},
        "import os\ngetenv('H')": {"H"},
        "import os\nx = dict(os.environ)": dyn,
        "import os\nos.environ.items()": dyn,
        "import os\nos.environ.copy()": dyn,
        "import os\nos.environ.get(name)": dyn,
        "import os\nx = os.environ": dyn,
    }
    for src, want in cases.items():
        assert env_names_of(src) == want, src
    assert not env_names_of("import os\nos.path.join('a')")


def bash_env(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    used = set(re.findall(r"\$\{?[#!]?([A-Z_][A-Z0-9_]*)", text))
    own = set(re.findall(r"\b([A-Z_][A-Z0-9_]*)\+?=", text))
    own |= set(re.findall(r"\bfor\s+([A-Z_][A-Z0-9_]*)\s+in\b", text))
    for decl in re.findall(r"\b(?:local|declare|readonly|read)\b([^\n;|&]*)", text):
        own |= set(re.findall(r"(?<![\w-])([A-Z_][A-Z0-9_]*)\b", decl))
    return used - own


def bash_sources():
    out = []
    for name in NAMES:
        d = os.path.join(PLUGINS, name, "scripts")
        if os.path.isdir(d):
            out += [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".sh")]
    return out


@pytest.mark.parametrize("path", bash_sources(), ids=lambda p: os.path.relpath(p, ROOT))
def test_bash_reads_only_declared_environment(path):
    assert bash_env(path) <= BASH_ENV, sorted(bash_env(path) - BASH_ENV)


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
