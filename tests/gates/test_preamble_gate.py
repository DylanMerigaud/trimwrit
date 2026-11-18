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


# --- Bash: the shell door (0.2.0) ---------------------------------------------------------------
# A memory line appended by `cat >> $M/MEMORY.md` never reached the Edit check above: the index
# of growth-cockpit went over its 4000 token budget twice in a week that way (2026-11-15, 11-17).


def bash(command, cwd, event="PreToolUse", tool_use_id="t1"):
    return {"hook_event_name": event, "session_id": "p", "tool_name": "Bash",
            "tool_use_id": tool_use_id, "tool_input": {"command": command}, "cwd": str(cwd)}


def key(path):
    """Claude Code's project directory name for a session started at `path`."""
    return "".join(c if c.isalnum() and c.isascii() else "-" for c in os.path.realpath(str(path)))


def memory_index(tmp_path, text="- a\n", project=None):
    project = project or (tmp_path / "repo")
    mem = home(tmp_path) / "projects" / key(project) / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "MEMORY.md").write_text(text, encoding="utf-8")
    return mem / "MEMORY.md"


def test_a_shell_write_to_an_always_loaded_file_is_refused(tmp_path):
    write_config(home(tmp_path), {NAME: {"preamble_budgets": {"MEMORY.md": 4000,
                                                              "~/.claude/rules": 1700}}})
    root = repo(tmp_path, {"CLAUDE.md": 9000}, claude="x\n")
    idx = memory_index(tmp_path)
    mem = idx.parent
    rules = home(tmp_path) / "rules"
    rules.mkdir()
    for cmd in ("echo '- b' >> {}".format(idx),
                "M={}\ncat >> $M/MEMORY.md <<'EOF'\n- it's a line\nEOF".format(mem),
                "printf x > \"{}\"".format(idx),
                "echo x | tee -a {} >/dev/null".format(idx),
                "sed -i 's/a/b/' {}".format(idx),
                "perl -pi -e 's/a/b/' {}".format(idx),
                "cd {} && echo hi >> CLAUDE.md".format(root),
                "echo r >> {}".format(rules / "new.md"),
                "cp /tmp/x {}".format(idx),
                "echo x >> \"$SOMEWHERE/MEMORY.md\""):
        reason = denied(run(tmp_path, bash(cmd, root)))
        assert "Edit" in reason and "shell" in reason, (cmd, reason)
    got = rows(os.path.join(str(home(tmp_path)), "door-refusals.jsonl"))
    assert {x["reason_class"] for x in got} == {"shell_write"}


def test_a_shell_read_or_an_unrelated_write_passes(tmp_path):
    write_config(home(tmp_path), {NAME: {"preamble_budgets": {"MEMORY.md": 4000}}})
    root = repo(tmp_path)
    idx = memory_index(tmp_path)
    for cmd in ("cat {}".format(idx), "grep -c x {} > /tmp/out".format(idx),
                "sed -n 1,5p {}".format(idx), "cp {} /tmp/backup".format(idx),
                "echo x 2>&1 >> {}/notes.md".format(idx.parent),
                "python3 -c 'print(1)' >/dev/null 2>&1", "echo \"it's\" | tee /tmp/a"):
        allowed(run(tmp_path, bash(cmd, root)))


def post(tmp_path, cmd, root, tool_use_id="t1"):
    return run(tmp_path, bash(cmd, root, event="PostToolUse", tool_use_id=tool_use_id))


def test_a_script_that_leaves_a_file_over_budget_is_reported_after(tmp_path):
    write_config(home(tmp_path), {NAME: {"preamble_budgets": {"MEMORY.md": 10}}})
    root = repo(tmp_path)
    idx = memory_index(tmp_path, "- a\n")
    cmd = "python3 grow.py"
    allowed(run(tmp_path, bash(cmd, root)))
    idx.write_text("m" * 100, encoding="utf-8")
    r = post(tmp_path, cmd, root)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["decision"] == "block"
    assert str(idx) in out["reason"] and "43 tokens" in out["reason"] and "budget 10" in out["reason"]
    got = rows(os.path.join(str(home(tmp_path)), "door-refusals.jsonl"))
    assert [(x["event"], x["reason_class"]) for x in got] == [("PostToolUse", "over_after_shell")]


def test_after_a_command_an_untouched_or_in_budget_file_says_nothing(tmp_path):
    write_config(home(tmp_path), {NAME: {"preamble_budgets": {"MEMORY.md": 10}}})
    root = repo(tmp_path)
    idx = memory_index(tmp_path, "m" * 100)       # already over, not touched by this command
    allowed(run(tmp_path, bash("ls", root, tool_use_id="t2")))
    allowed(post(tmp_path, "ls", root, tool_use_id="t2"))
    allowed(run(tmp_path, bash("python3 trim.py", root, tool_use_id="t3")))
    idx.write_text("short", encoding="utf-8")       # touched, under budget
    allowed(post(tmp_path, "python3 trim.py", root, tool_use_id="t3"))


def test_after_a_command_with_no_snapshot_a_fresh_overrun_is_still_reported(tmp_path):
    write_config(home(tmp_path), {NAME: {"preamble_budgets": {"MEMORY.md": 10}}})
    root = repo(tmp_path)
    memory_index(tmp_path, "m" * 100)
    r = post(tmp_path, "python3 grow.py", root, tool_use_id="never-seen")
    out = json.loads(r.stdout)
    assert out["decision"] == "block" and "no snapshot" in out["reason"]


# 0.3.0. The gate refuses growth only: one project's memory index went over through an `echo >>`
# (before the shell door), a shrinking edit left it at 4051 of 4000 on 2026-11-18, and the only
# one told was a growth-cockpit session that merely waited. A file over budget is now told at the
# start of the session that loads it, and only that session's files are reported after a command.


def start(cwd, source="startup"):
    return {"hook_event_name": "SessionStart", "session_id": "p", "source": source,
            "cwd": str(cwd)}


def test_a_session_starts_told_of_its_own_memory_index_over_budget(tmp_path):
    write_config(home(tmp_path), {NAME: {"preamble_budgets": {"MEMORY.md": 10}}})
    root = repo(tmp_path)
    idx = memory_index(tmp_path, "m" * 100)
    r = run(tmp_path, start(root))
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    ctx = out["additionalContext"]
    assert str(idx) in ctx and "43 tokens, budget 10" in ctx
    assert "never drops a memory file" in ctx and "in this session" in ctx
    got = rows(os.path.join(str(home(tmp_path)), "door-refusals.jsonl"))
    assert [(x["event"], x["reason_class"]) for x in got] == [("SessionStart", "over_at_start")]
    idx.write_text("short", encoding="utf-8")
    allowed(run(tmp_path, start(root, "compact")))


def test_another_projects_memory_index_is_told_to_neither_start_nor_command(tmp_path):
    write_config(home(tmp_path), {NAME: {"preamble_budgets": {"MEMORY.md": 10}}})
    root = repo(tmp_path)
    memory_index(tmp_path, "- a\n")
    other = memory_index(tmp_path, "- b\n", project=tmp_path / "elsewhere")
    allowed(run(tmp_path, start(root)))
    allowed(run(tmp_path, bash("sleep 1", root, tool_use_id="w")))
    other.write_text("m" * 100, encoding="utf-8")       # another session grew it meanwhile
    allowed(post(tmp_path, "sleep 1", root, tool_use_id="w"))
    r = run(tmp_path, start(tmp_path / "elsewhere"))
    assert str(other) in json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


def test_a_linked_worktree_answers_for_its_main_checkouts_memory_index(tmp_path):
    write_config(home(tmp_path), {NAME: {"preamble_budgets": {"MEMORY.md": 10}}})
    main = repo(tmp_path)
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    wt = main / ".claude" / "worktrees" / "wt"
    (wt / "sub").mkdir(parents=True)
    (wt / ".git").write_text("gitdir: {}\n".format(main / ".git" / "worktrees" / "wt"),
                             encoding="utf-8")
    idx = memory_index(tmp_path, "m" * 100, project=main)
    r = run(tmp_path, start(wt / "sub"))
    assert str(idx) in json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


def test_a_user_file_over_budget_is_told_at_start_and_the_rules_once(tmp_path):
    h = home(tmp_path)
    write_config(h, {NAME: {"preamble_budgets": {"~/.claude/CLAUDE.md": 10,
                                                 "~/.claude/rules": 10}}})
    (h / "CLAUDE.md").write_text("u" * 100, encoding="utf-8")
    (h / "rules").mkdir()
    for name in ("a.md", "b.md"):
        (h / "rules" / name).write_text("r" * 20, encoding="utf-8")
    ctx = json.loads(run(tmp_path, start(repo(tmp_path))).stdout)["hookSpecificOutput"][
        "additionalContext"]
    assert "43 tokens, budget 10" in ctx and ctx.count("rules is 18 tokens, budget 10") == 1
