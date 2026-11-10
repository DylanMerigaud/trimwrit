"""What a repository and the user layer load into every session, in tokens the model sees.

`trimwrit integrate` writes rules into instruction files, and nothing stopped an always-loaded
file from growing: one user's global CLAUDE.md reached 14.7k tokens, reread on every turn of
every session. This module measures the pieces that load every time and holds them to a budget
the repository writes in `.trimwrit/budget.json`:

    {"CLAUDE.md": 4000, "~/.claude/CLAUDE.md": 6000, "~/.claude/rules": 2000,
     "MEMORY.md": 1500, "outreach/CLAUDE.md": 1200}

A nested CLAUDE.md loads only when the session reads a file under it, so it is not a piece of
its own; a path relative to the root still works as a budget key, and `integrate` holds it to
that budget.

The estimate is deliberately plain: HTML comments are stripped (the model never sees a rule
marker as instruction text, and the harness drops them), then the UTF-8 byte count divided by
`BYTES_PER_TOKEN`. `--probe` asks the real CLI once when a measured number is wanted.
"""
import glob
import json
import os
import re
import subprocess

BYTES_PER_TOKEN = 2.35
BUDGET_FILE = os.path.join(".trimwrit", "budget.json")
DEFAULT_USER_DIR = os.path.join("~", ".claude")

PROJECT = "CLAUDE.md"
USER = "~/.claude/CLAUDE.md"
RULES = "~/.claude/rules"
MEMORY = "MEMORY.md"
PROBE_PROMPT = "Reply with the single word ok."

_COMMENT = re.compile(r"<!--.*?-->", re.S)


def visible(text):
    """`text` without its HTML comments, which is what reaches the model as instructions."""
    return _COMMENT.sub("", text)


def tokens(text):
    """Estimated tokens of what the model sees of `text`."""
    return int(round(len(visible(text).encode("utf-8")) / BYTES_PER_TOKEN))


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _user_dir(user_dir):
    return os.path.realpath(os.path.expanduser(user_dir or DEFAULT_USER_DIR))


def project_slug(root):
    """The directory name Claude Code keeps a project's memory under.

    The root's resolved absolute path with every character that is not a letter or a digit
    turned into `-` (so `/home/u/repo` is `-home-u-repo`, and `.claude` becomes `-claude`).
    """
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(root))


def memory_path(root, user_dir=None):
    return os.path.join(_user_dir(user_dir), "projects", project_slug(root), "memory", MEMORY)


def load_budgets(root):
    """{key: tokens} from `<root>/.trimwrit/budget.json`, {} when there is none."""
    text = _read(os.path.join(root, BUDGET_FILE))
    if text is None:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("{} must be a JSON object mapping a piece to a token budget"
                         .format(BUDGET_FILE))
    return {str(k): int(v) for k, v in data.items()}


def _sources(root, user_dir):
    """[(name, [paths])] of the always-loaded pieces, in load order, absent files left in."""
    root = os.path.realpath(root)
    udir = _user_dir(user_dir)
    out = [(PROJECT, [os.path.join(root, "CLAUDE.md")])]
    dot = os.path.join(root, ".claude", "CLAUDE.md")
    if os.path.exists(dot):
        out.append((".claude/CLAUDE.md", [dot]))
    out.append((USER, [os.path.join(udir, "CLAUDE.md")]))
    out.append((RULES, sorted(glob.glob(os.path.join(udir, "rules", "*.md")))))
    out.append((MEMORY, [memory_path(root, user_dir)]))
    return out


def piece_name(root, target, user_dir=None):
    """The piece name `target` belongs to (`CLAUDE.md`, `~/.claude/CLAUDE.md`, ...), or None."""
    path = _abs(root, target)
    for name, paths in _sources(root, user_dir):
        if name == RULES:
            rules_dir = os.path.join(_user_dir(user_dir), "rules")
            if os.path.dirname(path) == rules_dir and path.endswith(".md"):
                return name
        elif path in paths:
            return name
    return None


def _abs(root, target):
    target = os.path.expanduser(target)
    if not os.path.isabs(target):
        target = os.path.join(os.path.realpath(root), target)
    return os.path.realpath(target)


def budget_for(root, target, user_dir=None, budgets=None):
    """(key, budget) for the piece `target` writes into, or None when it has no budget.

    A target matches by its piece name, by its path relative to the root, or by a key that
    names the same file once `~` is expanded, so `~/.claude/CLAUDE.md` and its absolute path
    both match.
    """
    budgets = load_budgets(root) if budgets is None else budgets
    if not budgets:
        return None
    path = _abs(root, target)
    name = piece_name(root, target, user_dir)
    if name and name in budgets:
        return name, budgets[name]
    rel = os.path.relpath(path, os.path.realpath(root))
    for key in (rel, target):
        if key in budgets:
            return key, budgets[key]
    for key, value in budgets.items():
        if _abs(root, key) == path:
            return key, value
    return None


def piece_files(root, target, user_dir=None):
    """Every file that counts toward the piece `target` belongs to (the rules are one piece)."""
    if piece_name(root, target, user_dir) == RULES:
        return sorted(glob.glob(os.path.join(_user_dir(user_dir), "rules", "*.md")))
    return [_abs(root, target)]


def _row(name, paths, budgets):
    texts = [t for t in (_read(p) for p in paths) if t is not None]
    if not texts:
        return None
    n = sum(tokens(t) for t in texts)
    budget = budgets.get(name)
    path = paths[0] if len(paths) == 1 else os.path.dirname(paths[0])
    return {"name": name, "path": path, "tokens": n, "budget": budget,
            "over": budget is not None and n > budget}


def pieces(root, user_dir=None, budgets=None):
    """[{name, path, tokens, budget, over}] for every always-loaded piece that exists.

    A budget key that is a relative path to a file of the root and not already a piece (a
    nested `outreach/CLAUDE.md`) is listed too, so the file `integrate` holds to it shows here.
    """
    budgets = load_budgets(root) if budgets is None else budgets
    out = []
    seen = set()
    for name, paths in _sources(root, user_dir):
        row = _row(name, paths, budgets)
        seen.update(paths)
        if row:
            out.append(row)
    names = {name for name, _ in _sources(root, user_dir)}
    for key in sorted(budgets):
        if key in names or key.startswith("~") or os.path.isabs(key):
            continue
        path = _abs(root, key)
        if path in seen:
            continue
        row = _row(key, [path], budgets)
        if row:
            out.append(row)
    return out


def _default_runner(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=300)


def probe(root, runner=None):
    """(total input tokens, None) from one real `claude -p` call, or (None, reason).

    The total is cache creation plus cache read plus fresh input: everything the session sent
    the model before the one word answer. `runner(cmd, cwd)` returns an object with
    `returncode`, `stdout`, `stderr`, like `subprocess.run`; tests pass a fake one.
    """
    runner = runner or _default_runner
    cmd = ["claude", "-p", PROBE_PROMPT, "--output-format", "json"]
    try:
        res = runner(cmd, os.path.realpath(root))
    except FileNotFoundError:
        return None, "claude not found"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc) or exc.__class__.__name__
    if res.returncode != 0:
        why = (res.stderr or res.stdout or "").strip().splitlines()
        return None, "exit {}{}".format(res.returncode, ": " + why[-1] if why else "")
    try:
        usage = json.loads(res.stdout)["usage"]
        total = sum(int(usage.get(k) or 0) for k in (
            "cache_creation_input_tokens", "cache_read_input_tokens", "input_tokens"))
    except (ValueError, KeyError, TypeError, AttributeError):
        return None, "no usage in the output"
    return total, None
