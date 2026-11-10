"""The preamble-gate plugin, run the way Claude Code runs it: a payload on stdin, --home on argv.

--home stands for ~/.claude: the user CLAUDE.md, the rules directory and trimwrit-gates.json all
live under it in these tests."""
import importlib.util
import json
import os
import sys

from gates._hook import ROOT, plugin, rows, run_py, write_config

NAME = "preamble-gate"
SCRIPT = "preamble_gate.py"


def tool(name, tool_input, cwd):
    return {"hook_event_name": "PreToolUse", "session_id": "p", "tool_name": name,
            "tool_input": tool_input, "cwd": str(cwd)}


def repo(tmp_path, budgets=None, claude=None):
    root = tmp_path / "repo"
    root.mkdir()
    if budgets is not None:
        (root / ".trimwrit").mkdir()
        (root / ".trimwrit" / "budget.json").write_text(json.dumps(budgets), encoding="utf-8")
    if claude is not None:
        (root / "CLAUDE.md").write_text(claude, encoding="utf-8")
    return root


def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir(exist_ok=True)
    return h


def run(tmp_path, payload):
    return run_py(NAME, SCRIPT, payload, home(tmp_path), cwd=str(tmp_path))


def denied(r):
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    return out["permissionDecisionReason"]


def allowed(r):
    assert (r.returncode, r.stdout) == (0, ""), (r.stdout, r.stderr)


def test_a_write_over_budget_is_denied_and_counted(tmp_path):
    root = repo(tmp_path, {"CLAUDE.md": 100})
    path = root / "CLAUDE.md"
    reason = denied(run(tmp_path, tool("Write", {"file_path": str(path), "content": "x" * 470},
                                       root)))
    assert str(path) in reason and "from 0 to 200 tokens, budget 100" in reason
    assert "SKILL.md" in reason and "history doc" in reason and "trimwrit relocate" in reason
    got = rows(os.path.join(str(home(tmp_path)), "door-refusals.jsonl"))
    assert [(x["door"], x["event"], x["reason_class"]) for x in got] == \
        [("preamble-gate", "PreToolUse", "over_budget")]
    allowed(run(tmp_path, tool("Write", {"file_path": str(path), "content": "x" * 200}, root)))


def test_a_shrinking_edit_passes_while_still_over(tmp_path):
    root = repo(tmp_path, {"CLAUDE.md": 10}, claude="a" * 470 + "\nold line\n")
    path = str(root / "CLAUDE.md")
    allowed(run(tmp_path, tool("Edit", {"file_path": path, "old_string": "old line",
                                        "new_string": "new"}, root)))
    denied(run(tmp_path, tool("Edit", {"file_path": path, "old_string": "old line",
                                       "new_string": "a much longer line than before"}, root)))


def test_edit_honors_replace_all_and_a_missing_old_string_passes(tmp_path):
    root = repo(tmp_path, {"CLAUDE.md": 100}, claude="ab " * 70)
    path = str(root / "CLAUDE.md")
    # one replacement: 211 bytes (90 tokens); every one: 350 bytes (149 tokens)
    allowed(run(tmp_path, tool("Edit", {"file_path": path, "old_string": "ab",
                                        "new_string": "abc"}, root)))
    reason = denied(run(tmp_path, tool("Edit", {"file_path": path, "old_string": "ab",
                                                "new_string": "abcd", "replace_all": True},
                                       root)))
    assert "to 149 tokens" in reason
    allowed(run(tmp_path, tool("Edit", {"file_path": path, "old_string": "zz",
                                        "new_string": "z" * 900}, root)))


def test_multiedit_applies_each_edit_in_order(tmp_path):
    root = repo(tmp_path, {"CLAUDE.md": 50}, claude="one\n")
    path = str(root / "CLAUDE.md")
    edits = [{"old_string": "one", "new_string": "two"},
             {"old_string": "two", "new_string": "three " * 40}]
    reason = denied(run(tmp_path, tool("MultiEdit", {"file_path": path, "edits": edits}, root)))
    assert "to {} tokens".format(int(round(241 / 2.35))) in reason
    edits[1]["old_string"] = "one"   # gone after the first edit: the tool will fail, let it
    allowed(run(tmp_path, tool("MultiEdit", {"file_path": path, "edits": edits}, root)))


def test_a_file_that_is_not_always_loaded_is_ignored(tmp_path):
    root = repo(tmp_path, {"CLAUDE.md": 1, "docs/notes.md": 1})
    for rel in ("docs/notes.md", "README.md", "memory/notes.md"):
        allowed(run(tmp_path, tool("Write", {"file_path": str(root / rel), "content": "x" * 900},
                                   root)))


def test_the_rules_directory_is_one_piece(tmp_path):
    h = home(tmp_path)
    (h / "rules").mkdir()
    (h / "rules" / "a.md").write_text("a" * 235, encoding="utf-8")
    write_config(h, {NAME: {"preamble_budgets": {"~/.claude/rules": 150}}})
    root = repo(tmp_path)
    target = str(h / "rules" / "b.md")
    allowed(run(tmp_path, tool("Write", {"file_path": target, "content": "b" * 100}, root)))
    reason = denied(run(tmp_path, tool("Write", {"file_path": target, "content": "b" * 200},
                                       root)))
    assert "from 100 to 185 tokens, budget 150" in reason


def test_the_project_layer_wins_on_the_same_key(tmp_path):
    h = home(tmp_path)
    write_config(h, {NAME: {"preamble_budgets": {"CLAUDE.md": 1000, "MEMORY.md": 10}}})
    root = repo(tmp_path, {"CLAUDE.md": 50})
    path = str(root / "CLAUDE.md")
    assert "budget 50" in denied(run(tmp_path, tool("Write", {"file_path": path,
                                                               "content": "x" * 470}, root)))
    # The user layer's CLAUDE.md covers a project with no budget of its own.
    other = tmp_path / "other"
    other.mkdir()
    reason = denied(run(tmp_path, tool("Write", {"file_path": str(other / "CLAUDE.md"),
                                                 "content": "x" * 2400}, other)))
    assert "budget 1000" in reason and "preamble_budgets" in reason
    mem = h / "projects" / "-repo" / "memory"
    mem.mkdir(parents=True)
    assert "budget 10" in denied(run(tmp_path, tool(
        "Write", {"file_path": str(mem / "MEMORY.md"), "content": "m" * 100}, root)))


def test_the_user_claude_md_and_a_nested_key(tmp_path):
    h = home(tmp_path)
    root = repo(tmp_path, {"~/.claude/CLAUDE.md": 20, "sub/CLAUDE.md": 30, "CLAUDE.md": 9999})
    (root / "sub").mkdir()
    assert "budget 20" in denied(run(tmp_path, tool(
        "Write", {"file_path": str(h / "CLAUDE.md"), "content": "u" * 100}, root)))
    assert "budget 30" in denied(run(tmp_path, tool(
        "Write", {"file_path": str(root / "sub" / "CLAUDE.md"), "content": "s" * 100}, root)))


def test_no_budget_allows(tmp_path):
    root = repo(tmp_path)
    allowed(run(tmp_path, tool("Write", {"file_path": str(root / "CLAUDE.md"),
                                         "content": "x" * 90000}, root)))


def test_a_broken_budget_is_a_loud_crash_not_a_block(tmp_path):
    write_config(home(tmp_path), {NAME: {"preamble_budgets": {"CLAUDE.md": "big"}}})
    root = repo(tmp_path)
    r = run(tmp_path, tool("Write", {"file_path": str(root / "CLAUDE.md"), "content": "x"}, root))
    assert r.returncode == 0 and "permissionDecision" not in r.stdout
    assert "systemMessage" in r.stdout
    crash = rows(os.path.join(str(home(tmp_path)), "hook-health.jsonl"))
    assert crash and crash[0]["outcome"] == "crash"


def test_the_token_estimate_agrees_with_trimwrit():
    sys.path.insert(0, ROOT)
    from trimwrit import preamble
    spec = importlib.util.spec_from_file_location(
        "preamble_gate_under_test", os.path.join(plugin(NAME), "scripts", SCRIPT))
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    samples = ["", "x" * 235, "<!-- trimwrit: R1 case 0001 -->\nrule", "café " * 99,
               "a<!-- multi\nline -->b" * 7, "中文" * 50, "x\n" * 1000]
    for text in samples:
        assert gate.tokens(text) == preamble.tokens(text), text[:20]
