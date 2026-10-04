import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "gates"))
from gatekit import config  # noqa: E402

DEFAULTS = {"owners": [], "note": "", "cap": 3}


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(data if isinstance(data, str) else json.dumps(data))


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert config.load("s", DEFAULTS, {"cwd": str(tmp_path)}, home=str(tmp_path / "h")) == DEFAULTS


def test_project_over_user_key_by_key(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    home = tmp_path / "h"
    write(str(home / "trimwrit-gates.json"), {"s": {"owners": ["a"], "note": "user"}})
    proj = tmp_path / "repo"
    write(str(proj / ".claude" / "trimwrit-gates.json"), {"s": {"note": "project"}})
    got = config.load("s", DEFAULTS, {"cwd": str(proj / "sub" / "dir")}, home=str(home))
    assert got == {"owners": ["a"], "note": "project", "cap": 3}


def test_claude_project_dir_wins_over_cwd(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    write(str(a / ".claude" / "trimwrit-gates.json"), {"s": {"note": "a"}})
    write(str(b / ".claude" / "trimwrit-gates.json"), {"s": {"note": "b"}})
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
    assert config.load("s", DEFAULTS, {"cwd": str(b)}, home=str(tmp_path / "h"))["note"] == "a"


@pytest.mark.parametrize("bad", [
    "{not json",
    '["a list"]',
    json.dumps({"s": {"owner": ["typo"]}}),
    json.dumps({"s": {"cap": "three"}}),
    json.dumps({"s": "not an object"}),
])
def test_bad_config_raises(tmp_path, monkeypatch, bad):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    write(str(tmp_path / "h" / "trimwrit-gates.json"), bad)
    with pytest.raises(config.ConfigError):
        config.load("s", DEFAULTS, {"cwd": str(tmp_path)}, home=str(tmp_path / "h"))


def test_cli_get_lines_run(tmp_path):
    root = tmp_path / "plugin"
    write(str(root / "defaults.json"), {"section": "p", "defaults": {
        "globs": [], "after": [], "name": ""}})
    write(str(tmp_path / "h" / "trimwrit-gates.json"), {"p": {
        "globs": ["*.db", "a b.json"],
        "after": [["sh", "-c", "cat > out.txt"]], "name": "x"}})
    os.makedirs(str(root / "gatekit"))
    src = os.path.dirname(config.__file__)
    for f in ("__init__.py", "config.py"):
        with open(os.path.join(src, f), encoding="utf-8") as i, \
                open(str(root / "gatekit" / f), "w", encoding="utf-8") as o:
            o.write(i.read())
    cli = [sys.executable, str(root / "gatekit" / "config.py")]
    home = ["--home", str(tmp_path / "h"), "--cwd", str(tmp_path)]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    out = subprocess.run(cli + ["get", "name"] + home, capture_output=True, text=True, env=env)
    assert out.stdout.strip() == '"x"'
    out = subprocess.run(cli + ["lines", "globs"] + home, capture_output=True, text=True, env=env)
    assert out.stdout.splitlines() == ["*.db", "a b.json"]
    out = subprocess.run(cli + ["run", "after"] + home, input="payload", capture_output=True,
                         text=True, env=env)
    assert out.returncode == 0
    assert (tmp_path / "out.txt").read_text() == "payload"


def test_project_path_never_returns_the_user_layer(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    home = tmp_path / "h"
    write(str(home / "trimwrit-gates.json"), {"s": {"note": "user"}})
    # a walk up from a directory holding the --home file as <dir>/.claude/ finds the user layer
    fake = tmp_path / "fakehome"
    write(str(fake / ".claude" / "trimwrit-gates.json"), {"s": {"note": "real user"}})
    monkeypatch.setenv("HOME", str(fake))
    assert config.project_path({"cwd": str(fake / "work")}, home=str(tmp_path / "h")) is None
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(fake))
    assert config.project_path({"cwd": str(tmp_path)}, home=str(tmp_path / "h")) is None
    # and with no --home, the real user file is the user layer, never counted a second time
    assert config.project_path({"cwd": str(fake)}) is None
    assert config.load("s", DEFAULTS, {"cwd": str(fake)})["note"] == "real user"


def test_load_plugin_takes_an_explicit_root(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    root = tmp_path / "other"
    write(str(root / "defaults.json"), {"section": "o", "defaults": {"k": 7}})
    assert config.load_plugin({"cwd": str(tmp_path)}, str(tmp_path / "h"), str(root)) == {"k": 7}
