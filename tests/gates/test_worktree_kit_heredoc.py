"""worktree-kit heredoc_file.py: a quoted heredoc body moves into a file, nothing else changes.

The door exists because Claude Code's worktree shell guard refuses commands it cannot parse, and
a quoted heredoc is the commonest cause (see the module docstring). The proof here is that the
rewritten command PRINTS THE SAME THING under a real shell, and that anything the door is not
sure of comes back untouched."""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

from gates._hook import ROOT, clean_env, plugin, run_py

NAME = "worktree-kit"
SCRIPT = os.path.join(plugin(NAME), "scripts", "heredoc_file.py")


@pytest.fixture(scope="module")
def hf():
    sys.path.insert(0, plugin(NAME))
    spec = importlib.util.spec_from_file_location("heredoc_file_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def shell(command, cwd):
    out = subprocess.run(["bash", "-c", command], capture_output=True, text=True, cwd=str(cwd),
                         env=clean_env(), timeout=30)
    return out.returncode, out.stdout, out.stderr


SAME = {
    "plain python": "python3 - <<'EOF'\nprint('a')\nEOF\n",
    "double quoted delimiter": 'cat <<"END"\nhello $HOME `x` $(y)\nEND\n',
    "backslash delimiter": "cat <<\\EOF\nhello $HOME\nEOF\n",
    "dash strips tabs": "cat <<-'EOF'\n\tone\n\t\ttwo\n\tEOF\n",
    "trailing commands": "cat <<'EOF'\nx\nEOF\necho after; echo more\n",
    "redirect and pipe on the opener": "cat <<'EOF' 2>&1 | tr a-z A-Z\nabc\nEOF\n",
    "cd first": "cd /tmp && cat <<'EOF'\nwhere\nEOF\npwd\n",
    "two in a row": "cat <<'A'\n1\nA\ncat <<'B'\n2\nB\n",
    "two on one line": "cat <<'A' && cat <<'B'\n1\nA\n2\nB\n",
    "in a loop": "for i in 1 2; do cat <<'EOF'\nrow\nEOF\ndone\n",
    "in an if": "if true; then\ncat <<'EOF'\nyes\nEOF\nfi\n",
    "body shaped like code": "cat <<'EOF'\nx = {\"k\": len(\"T3\")}\n$(rm -rf nope)\n`nope`\nEOF\n",
    "terminator lookalikes": "cat <<'EOF'\n EOF\nEOF \nEOFX\nEOF\n",
    "empty body": "cat <<'EOF'\nEOF\necho done\n",
    "no newline after the terminator": "cat <<'EOF'\nlast\nEOF",
    "blank lines kept": "cat <<'EOF'\n\n\nx\n\nEOF\n",
    "heredoc word in a string": "echo '<<not a heredoc'\ncat <<'EOF'\nreal\nEOF\n",
    "comment with an operator": "# cat <<'NOPE'\ncat <<'EOF'\nreal\nEOF\n",
    "redirect to a file": "cat > out.txt <<'EOF'\nsaved\nEOF\ncat out.txt\n",
}


@pytest.mark.parametrize("label", sorted(SAME))
def test_rewritten_command_prints_the_same(hf, tmp_path, label):
    command = SAME[label]
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    new = hf.rewrite(command, str(tmp_path / "store"))
    assert new is not None and new != command
    assert "<<'" not in new.replace("echo '<<not a heredoc'", "").replace("# cat <<'NOPE'", ""), new
    assert shell(command, tmp_path / "a") == shell(new, tmp_path / "b")


def test_the_body_lands_in_a_file_named_by_its_hash(hf, tmp_path):
    store = str(tmp_path / "store")
    one = hf.rewrite("cat <<'EOF'\nsame\nEOF\n", store)
    two = hf.rewrite("python3 - <<'EOF'\nsame\nEOF\n", store)
    path = one.split("'")[1]
    assert two.split("'")[1] == path
    assert open(path, encoding="utf-8").read() == "same\n"
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert os.listdir(store) == [os.path.basename(path)]


LEFT_ALONE = {
    "unquoted delimiter": "cat <<EOF\n$HOME\nEOF\n",
    "unquoted with a dash": "cat <<-EOF\n\t$HOME\n\tEOF\n",
    "inside a command substitution": "x=$(cat <<'EOF'\nhi\nEOF\n)\necho $x\n",
    "inside a subshell": "(cat <<'EOF'\nhi\nEOF\n)\n",
    "inside backticks": "x=`cat <<'EOF'\nhi\nEOF\n`\n",
    "unterminated": "cat <<'EOF'\nhi\n",
    "operator on the last line": "cat <<'EOF'",
    "open single quote": "echo 'oops <<'EOF'\nx\nEOF\n",
    "ansi-c quoting": "echo $'it\\'s'; cat <<'EOF'\nx\nEOF\n",
    "unbalanced parenthesis": "case x in x) cat <<'EOF'\nx\nEOF\n;; esac\n",
    "delimiter with a space": "cat <<'END OF'\nx\nEND OF\n",
    "here-string only": "cat <<< 'text'\n",
    "a shift inside arithmetic is read as a nested operator": "echo $((1 << 2))\ncat <<'EOF'\nx\nEOF\n",
    "no heredoc": "echo hi\n",
}


@pytest.mark.parametrize("label", sorted(LEFT_ALONE))
def test_what_it_is_not_sure_of_comes_back_untouched(hf, tmp_path, label):
    command = LEFT_ALONE[label]
    try:
        got = hf.rewrite(command, str(tmp_path / "store"))
    except hf.Bail:
        got = None
    assert got is None
    assert not (tmp_path / "store").exists()


def test_a_mixed_command_with_one_unquoted_heredoc_is_not_half_rewritten(hf, tmp_path):
    command = "cat <<'A'\n1\nA\ncat <<B\n$HOME\nB\n"
    with pytest.raises(hf.Bail):
        hf.rewrite(command, str(tmp_path / "store"))
    assert not (tmp_path / "store").exists()


def worktree(tmp_path):
    """A repository with a linked worktree, as `git worktree add` makes one."""
    env = clean_env({"HOME": str(tmp_path), "GIT_CONFIG_GLOBAL": "/dev/null",
                     "GIT_CONFIG_NOSYSTEM": "1"})
    hub = tmp_path / "hub"
    hub.mkdir()
    for args in (["init", "-q", "-b", "main"],
                 ["-c", "user.name=T", "-c", "user.email=t@e.x", "commit", "-q",
                  "--allow-empty", "-m", "i"],
                 ["worktree", "add", "-q", "-b", "w", str(tmp_path / "wt")]):
        subprocess.run(["git", *args], cwd=str(hub), env=env, check=True, capture_output=True)
    return hub, tmp_path / "wt"


def hook(tmp_path, command, cwd, tool="Bash", **extra):
    payload = {"tool_name": tool, "cwd": str(cwd), "session_id": "s1",
               "tool_input": dict({"command": command}, **extra)}
    home = tmp_path / "home"
    out = run_py(NAME, "heredoc_file.py", payload, home, cwd=str(cwd))
    assert out.returncode == 0, out.stderr
    return out.stdout, home


def test_in_a_linked_worktree_the_hook_rewrites_and_keeps_the_other_fields(tmp_path):
    _hub, wt = worktree(tmp_path)
    stdout, home = hook(tmp_path, "python3 - <<'EOF'\nprint(1)\nEOF\n", wt,
                        description="d", timeout=5000)
    answer = json.loads(stdout)["hookSpecificOutput"]
    assert answer["hookEventName"] == "PreToolUse"
    updated = answer["updatedInput"]
    assert updated["description"] == "d" and updated["timeout"] == 5000
    assert "<<" not in updated["command"]
    assert updated["command"].startswith("python3 - < '" + str(home / "heredoc"))
    assert "permissionDecision" not in answer
    rows = [json.loads(line) for line in open(home / "hook-health.jsonl", encoding="ascii")]
    assert [r["outcome"] for r in rows if r["outcome"] != "ok"] == ["rewrote"]


def test_on_the_main_checkout_the_hook_says_nothing(tmp_path):
    hub, _wt = worktree(tmp_path)
    stdout, _home = hook(tmp_path, "cat <<'EOF'\nx\nEOF\n", hub)
    assert stdout == ""


def test_a_path_under_claude_worktrees_counts_as_a_worktree(tmp_path):
    here = tmp_path / "repo" / ".claude" / "worktrees" / "agent-1"
    here.mkdir(parents=True)
    stdout, _home = hook(tmp_path, "cat <<'EOF'\nx\nEOF\n", here)
    assert "updatedInput" in stdout


def test_other_tools_and_commands_without_a_heredoc_pass_untouched(tmp_path):
    _hub, wt = worktree(tmp_path)
    assert hook(tmp_path, "cat <<'EOF'\nx\nEOF\n", wt, tool="Write")[0] == ""
    assert hook(tmp_path, "echo hi", wt)[0] == ""


def test_a_command_it_cannot_read_passes_untouched_and_is_counted(tmp_path):
    _hub, wt = worktree(tmp_path)
    stdout, home = hook(tmp_path, "cat <<EOF\n$HOME\nEOF\n", wt)
    assert stdout == ""
    rows = [json.loads(line) for line in open(home / "hook-health.jsonl", encoding="ascii")]
    assert any(r["outcome"] == "left-alone" for r in rows)


def test_a_broken_payload_never_blocks_the_command(tmp_path):
    out = run_py(NAME, "heredoc_file.py", "not json", tmp_path / "home", cwd=str(tmp_path))
    assert out.returncode == 0
    assert "updatedInput" not in out.stdout
    assert "heredoc-file.py" in out.stdout


def test_the_hook_is_wired_for_bash_in_hooks_json():
    with open(os.path.join(plugin(NAME), "hooks", "hooks.json"), encoding="utf-8") as fh:
        hooks = json.load(fh)["hooks"]["PreToolUse"]
    wired = [h for entry in hooks if entry["matcher"] == "Bash" for h in entry["hooks"]]
    assert any("heredoc_file.py" in h["command"] for h in wired)
    assert os.path.isfile(SCRIPT) and ROOT
