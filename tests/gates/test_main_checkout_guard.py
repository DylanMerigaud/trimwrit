"""The main-checkout-guard plugin: destructive git refused on the main checkout of a protected
repo, run the way Claude Code runs it (a PreToolUse payload on stdin, --home on argv).

Ports growth-cockpit's tests/test_git_hub_guard.py. Every case builds real throwaway repositories
under a temp dir with an isolated HOME and git config (nothing here touches a real repository or
pushes anywhere) and reads the exit code: 2 for a refusal with the reason on stderr, 0 for a pass.

FIXTURES: `hub` is a repo whose toplevel carries `.claude/auto-worktree` (and an
`.claude/auto-worktree-off`, which must NOT disarm the door), `wt` a linked worktree of it under
`.claude/worktrees/`, `plain` a repo with no marker.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOOK = os.path.join(ROOT, "plugins", "main-checkout-guard", "scripts", "git_hub_guard.py")
GIT_HOME = tempfile.mkdtemp(prefix="git-hub-guard-gitconfig-")

def clean_env(extra=None):
    """No inherited GIT_* (a test run from inside a git hook carries GIT_DIR and GIT_INDEX_FILE)
    and no CLAUDE_*, with an isolated HOME and git config so no real configuration is read."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "CLAUDE_"))}
    env.update({"HOME": GIT_HOME, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    env.update(extra or {})
    return env


def git(cwd, *args):
    env = clean_env()
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})
    subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
                   + list(args), cwd=cwd, env=env, check=True, capture_output=True, text=True)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class Fixture:
    """Built once per test class: a protected repo, its linked worktree, an unprotected repo."""

    def __init__(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="git-hub-guard-"))
        self.hub = os.path.join(self.root, "hubrepo")
        self.wt = os.path.join(self.hub, ".claude", "worktrees", "wt")
        self.plain = os.path.join(self.root, "plain")
        self.nowhere = os.path.join(self.root, "not-a-repo")
        os.makedirs(self.nowhere)

        os.makedirs(self.hub)
        git(self.hub, "init", "-q", "--template=", "-b", "main")
        write(os.path.join(self.hub, ".claude", "auto-worktree"), "opt-in marker\n")
        write(os.path.join(self.hub, ".gitignore"), ".claude/worktrees/\n.claude/auto-worktree-off\n")
        write(os.path.join(self.hub, "a.txt"), "one\n")
        git(self.hub, "add", "-A")
        git(self.hub, "commit", "-q", "-m", "first")
        write(os.path.join(self.hub, "a.txt"), "two\n")
        git(self.hub, "commit", "-q", "-am", "second")
        write(os.path.join(self.hub, ".claude", "auto-worktree-off"), "")
        git(self.hub, "worktree", "add", "-q", "-b", "wt-branch", self.wt)

        os.makedirs(self.plain)
        git(self.plain, "init", "-q", "--template=", "-b", "main")
        write(os.path.join(self.plain, "a.txt"), "one\n")
        git(self.plain, "add", "-A")
        git(self.plain, "commit", "-q", "-m", "first")

        self.home = tempfile.mkdtemp(prefix="git-hub-guard-home-")

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)


def run_hook(command, cwd, home, tool="Bash", raw=None):
    payload = raw if raw is not None else json.dumps({
        "hook_event_name": "PreToolUse", "tool_name": tool, "session_id": "git-hub-guard-test",
        "cwd": cwd, "tool_input": {"command": command, "description": "test"}})
    return subprocess.run([sys.executable, HOOK, "--home", home], input=payload,
                          capture_output=True, text=True, env=clean_env(), cwd=ROOT, timeout=60)


os.environ.update({"HOME": GIT_HOME, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
for _k in [k for k in os.environ if k.startswith("GIT_") and k not in
           ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")]:
    del os.environ[_k]

_MOD = []


def guard_module():
    """The hook script loaded once, in this process, under a unique module name, configured
    with the defaults (nothing protected but marker repos)."""
    if not _MOD:
        spec = importlib.util.spec_from_file_location("main_checkout_guard_under_test", HOOK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.configure_from({})
        _MOD.append(mod)
    return _MOD[0]


class Result:
    def __init__(self, returncode, stderr, stdout=""):
        self.returncode, self.stderr, self.stdout = returncode, stderr, stdout


def run_inproc(command, cwd, tool="Bash"):
    """body() of the hook without the process: same judge, same render, same exit code."""
    mod = guard_module()
    if tool != "Bash" or "git" not in command:
        return Result(0, "")
    findings = mod.Guard(os.path.normpath(cwd)).check(command)
    return Result(2, mod.render(findings)) if findings else Result(0, "")


class Base(unittest.TestCase):
    INPROC = True

    @classmethod
    def setUpClass(cls):
        cls.fx = Fixture()

    @classmethod
    def tearDownClass(cls):
        cls.fx.cleanup()

    def judge(self, command, cwd):
        if self.INPROC:
            return run_inproc(command, cwd)
        return run_hook(command, cwd, self.fx.home)

    def refused(self, command, cwd, *needles):
        r = self.judge(command, cwd)
        self.assertEqual(r.returncode, 2, "not refused: {!r} in {}\nstderr: {}".format(
            command, cwd, r.stderr))
        self.assertIn("git-hub-guard REFUSED", r.stderr)
        self.assertEqual(r.stdout, "", "a PreToolUse refusal is exit 2 plus stderr, nothing else")
        for needle in needles:
            self.assertIn(needle, r.stderr)
        return r

    def allowed(self, command, cwd):
        r = self.judge(command, cwd)
        self.assertEqual((r.returncode, r.stderr), (0, ""),
                         "refused or noisy: {!r} in {}".format(command, cwd))
        return r


class RefusedOnTheMainCheckout(Base):
    def test_checkout_dot_is_refused_with_the_why_and_the_safe_path(self):
        self.assertTrue(os.path.isfile(os.path.join(self.fx.hub, ".claude", "auto-worktree-off")),
                        "the fixture must carry the marker that disarms the Edit guard")
        self.refused("git checkout -- .", self.fx.hub, "Command: git checkout -- .",
                     "MAIN checkout", "EnterWorktree",
                     "git pull --ff-only", "no bypass",
                     "auto-worktree-off does not disarm")
        self.assertNotIn("This door exists since", self.refused(
            "git checkout -- .", self.fx.hub).stderr)

    def test_cd_to_the_hub_from_a_worktree_then_checkout(self):
        self.refused("cd {} && git checkout -- .".format(self.fx.hub), self.fx.wt)

    def test_cd_up_three_levels_from_a_worktree_lands_on_the_hub(self):
        self.refused("cd ../../.. && git checkout -- .", self.fx.wt)

    def test_a_read_only_command_first_does_not_hide_the_checkout(self):
        self.refused("git status && git checkout -- .", self.fx.hub)

    def test_the_incident_reset_after_a_fetch(self):
        self.refused("git fetch; git reset --hard origin/main", self.fx.hub,
                     "reset --hard")

    def test_stash_through_dash_C_from_a_worktree(self):
        self.refused("git -C {} stash".format(self.fx.hub), self.fx.wt)

    def test_restore_without_staged(self):
        self.refused("git restore a.txt", self.fx.hub)
        self.refused("git restore -SW a.txt", self.fx.hub)
        self.refused("git restore --staged --worktree a.txt", self.fx.hub)

    def test_clean_without_dry_run(self):
        self.refused("git clean -fd", self.fx.hub)
        self.refused("git clean -fdx -e keep", self.fx.hub)

    def test_commit_on_the_hub(self):
        self.refused("git add -A && git commit -m x", self.fx.hub, "hub's main")

    def test_every_stash_but_list_and_show(self):
        for cmd in ("git stash", "git stash push -m x", "git stash save x", "git stash pop",
                    "git stash apply", "git stash drop", "git stash clear", "git stash -u"):
            with self.subTest(cmd=cmd):
                self.refused(cmd, self.fx.hub)

    def test_branch_switches_and_path_checkouts(self):
        for cmd in ("git checkout wt-branch", "git checkout -b feature", "git checkout -f",
                    "git checkout .", "git checkout HEAD~1 -- a.txt", "git checkout main a.txt",
                    "git switch -c feature", "git switch wt-branch",
                    "git switch --discard-changes main"):
            with self.subTest(cmd=cmd):
                self.refused(cmd, self.fx.hub)

    def test_resets_that_move_head_or_drop_edits(self):
        for cmd in ("git reset HEAD~1", "git reset --soft HEAD~1", "git reset --merge",
                    "git reset --keep HEAD", "git reset --har", "git reset --hard HEAD"):
            with self.subTest(cmd=cmd):
                self.refused(cmd, self.fx.hub)

    def test_history_writers(self):
        for cmd in ("git merge wt-branch", "git merge --ff wt-branch",
                    "git merge --no-ff --ff-only wt-branch", "git pull", "git pull --rebase",
                    "git pull --ff-only --rebase", "git rebase wt-branch",
                    "git cherry-pick wt-branch", "git revert HEAD", "git am x.patch",
                    "git bisect start", "git commit --amend --no-edit"):
            with self.subTest(cmd=cmd):
                self.refused(cmd, self.fx.hub)

    def test_plumbing_equivalents(self):
        for cmd in ("git read-tree -u -m HEAD", "git checkout-index -f -a", "git rm -f a.txt",
                    "git symbolic-ref HEAD refs/heads/wt-branch", "git update-ref HEAD HEAD~1"):
            with self.subTest(cmd=cmd):
                self.refused(cmd, self.fx.hub)

    def test_a_subshell_cd_does_not_leak(self):
        self.refused("(cd {}); git checkout -- .".format(self.fx.wt), self.fx.hub)

    def test_nested_shells_eval_and_heredocs(self):
        hub = self.fx.hub
        self.refused("bash -c 'cd {} && git reset --hard'".format(hub), self.fx.plain)
        self.refused('sh -c "git -C {} checkout -- ."'.format(hub), self.fx.plain)
        self.refused("zsh -lc 'cd {}; git stash'".format(hub), self.fx.plain)
        self.refused("eval 'git checkout -- .'", hub)
        self.refused("bash <<'EOF'\ncd {}\ngit checkout -- .\nEOF".format(hub), self.fx.plain)
        self.refused("echo 'git reset --hard' | bash", hub)
        self.refused("echo $(git checkout -- .)", hub)
        self.refused("x=`git clean -fd`", hub)
        self.refused("git status\ngit checkout -- .", hub)

    def test_prefixes_and_indirections(self):
        hub = self.fx.hub
        for cmd in ("env FOO=1 git checkout -- .", "env -i PATH=$PATH git stash",
                    "command git reset --hard",
                    "nohup git clean -fd", "timeout 10 git stash",
                    "git ls-files -m | xargs git checkout --",
                    "find . -name a.txt -exec git checkout -- {} \\;",
                    "/usr/bin/git checkout -- .", "\\git checkout -- .",
                    "if true; then git checkout -- .; fi", "{ git stash; }",
                    "f() { git checkout -- .; }; f", "G=git; $G checkout -- ."):
            with self.subTest(cmd=cmd):
                self.refused(cmd, hub)

    def test_git_dir_and_work_tree_point_at_the_hub(self):
        hub = self.fx.hub
        self.refused("GIT_DIR={}/.git git stash".format(hub), self.fx.wt)
        self.refused("export GIT_DIR={}/.git; git checkout -- .".format(hub), self.fx.plain)
        self.refused("git --git-dir={0}/.git --work-tree={0} reset --hard".format(hub),
                     self.fx.nowhere)

    def test_cd_into_a_rev_parse_of_the_hub(self):
        self.refused('cd "$(git -C {} rev-parse --show-toplevel)" && git commit -m x'.format(
            self.fx.hub), self.fx.wt)

    def test_a_cd_that_may_fail_keeps_the_hub_in_play(self):
        missing = os.path.join(self.fx.root, "missing-dir")
        self.refused("cd {}; git checkout -- .".format(missing), self.fx.hub)
        self.refused("git status || cd {}; git reset --hard".format(self.fx.wt), self.fx.hub)


class RefusedAnywhereInTheRepo(Base):
    """The main ref is shared: a worktree can destroy it too."""

    def test_deleting_or_moving_main(self):
        for cmd in ("git branch -D main", "git branch -d main", "git branch --delete --force main",
                    "git branch -f main HEAD", "git branch -M wt-branch main",
                    "git branch -m main old", "git checkout -B main",
                    "git switch -C main", "git worktree add -B main ../x",
                    "git update-ref refs/heads/main HEAD", "git update-ref -d refs/heads/main",
                    "git update-ref --stdin"):
            with self.subTest(cmd=cmd):
                self.refused(cmd, self.fx.wt)

    def test_force_pushes_and_deletes_of_main(self):
        for cmd in ("git push origin HEAD:main --force-with-lease", "git push -f origin HEAD:main",
                    "git push --force origin main", "git push origin +main",
                    "git push origin +HEAD:refs/heads/main", "git push origin :main",
                    "git push --delete origin main", "git push --mirror origin",
                    "git push --all --force origin",
                    "git fetch origin && git reset --hard origin/main && "
                    "git commit --allow-empty -m x && "
                    "git push origin HEAD:main --force-with-lease"):
            with self.subTest(cmd=cmd):
                self.refused(cmd, self.fx.wt, "merged pull request")

    def test_a_bare_force_push_from_the_hub_is_a_force_push_of_main(self):
        self.refused("git push --force", self.fx.hub)
        self.refused("git push origin HEAD --force-with-lease", self.fx.hub)


class Allowed(Base):
    def test_read_only_git_on_the_hub(self):
        for cmd in ("git status", "git log --oneline -3", "git diff HEAD~1", "git show HEAD",
                    "git fetch origin", "git rev-parse --show-toplevel", "git ls-files",
                    "git blame a.txt", "git grep one", "git worktree list", "git branch -a",
                    "git branch --show-current", "git stash list", "git stash show -p",
                    "git remote -v", "git config --get user.name", "git checkout",
                    "git reset", "git reset HEAD -- a.txt", "git reset a.txt",
                    "git reset -- a.txt", "git restore --staged a.txt",
                    "git restore -S -s HEAD~1 a.txt", "git clean -n", "git clean -nd",
                    "git clean --dry-run -x", "git pull --ff-only", "git pull --ff-only origin main",
                    "git merge --ff-only wt-branch", "git checkout main", "git switch main",
                    "git bisect log", "git push --dry-run --force origin main",
                    "git log --grep 'reset --hard'", "git add a.txt"):
            with self.subTest(cmd=cmd):
                self.allowed(cmd, self.fx.hub)

    def test_the_sanctioned_deploy(self):
        self.allowed("bash scripts/sync.sh", self.fx.hub)
        self.allowed("bash scripts/sync.sh --dry-run", self.fx.wt)

    def test_strings_that_only_mention_destructive_git(self):
        hub = self.fx.hub
        self.allowed('echo "git checkout -- ."', hub)
        self.allowed("grep -rn 'git reset --hard' .", hub)
        self.allowed("cat <<'EOF'\ngit checkout -- .\ngit reset --hard\nEOF", hub)
        self.allowed("python3 - <<'EOF'\nprint('git stash drop')\nEOF", hub)
        self.allowed("# git checkout -- .\nls", hub)

    def test_everything_in_a_linked_worktree(self):
        wt = self.fx.wt
        for cmd in ("git checkout -- .", "git reset --hard origin/main", "git clean -fdx",
                    "git stash", "git stash pop", "git commit -m x", "git rebase origin/main",
                    "git switch -c other", "git restore a.txt", "git merge main",
                    "git push origin HEAD --force-with-lease", "git push -u origin wt-branch",
                    "git push --force origin wt-branch", "git push origin HEAD:main",
                    "git branch -D stale-branch", "git branch -f other main",
                    "git branch -c main copy-of-main"):
            with self.subTest(cmd=cmd):
                self.allowed(cmd, wt)

    def test_a_commit_message_built_by_a_heredoc_in_a_worktree(self):
        cmd = ('git add -A && git commit -m "$(cat <<\'EOF\'\nfix (thing): no more '
               '`git checkout -- .` on the hub\n\nBody with "quotes" and (parens).\nEOF\n)"')
        self.allowed(cmd, self.fx.wt)
        self.refused(cmd, self.fx.hub)

    def test_leaving_the_hub_for_a_worktree(self):
        wt = self.fx.wt
        self.allowed("cd {} && git reset --hard".format(wt), self.fx.hub)
        self.allowed("(cd {} && git checkout -- .)".format(wt), self.fx.hub)
        self.allowed("git -C {} checkout -- .".format(wt), self.fx.hub)
        self.allowed("cd {} && git commit -m x && git push origin HEAD --force-with-lease"
                     .format(wt), self.fx.hub)

    def test_a_repo_with_no_marker_allows_everything(self):
        plain = self.fx.plain
        for cmd in ("git checkout -- .", "git reset --hard HEAD~1", "git clean -fdx",
                    "git stash", "git commit -m x", "git branch -D main",
                    "git push --force origin main", "git update-ref -d refs/heads/main",
                    'cd "$SOMEWHERE" && git reset --hard'):
            with self.subTest(cmd=cmd):
                self.allowed(cmd, plain)

    def test_no_git_at_all_and_other_tools(self):
        self.allowed("ls -la && rm -rf build", self.fx.hub)
        r = run_inproc("git checkout -- .", self.fx.hub, tool="Write")
        self.assertEqual((r.returncode, r.stderr), (0, ""))


class FailsClosed(Base):
    INPROC = False

    def test_an_unresolved_directory_inside_a_protected_repo_is_refused(self):
        for cmd in ('cd "$SOMEWHERE" && git reset --hard', "cd - && git checkout -- .",
                    'git -C "$X" stash', "popd && git clean -fd",
                    'GIT_DIR="$D" git checkout -- .'):
            with self.subTest(cmd=cmd):
                self.refused(cmd, self.fx.wt, "Unresolved", "fails closed")

    def test_an_unresolved_directory_outside_a_protected_repo_passes(self):
        self.allowed('cd "$SOMEWHERE" && git reset --hard', self.fx.nowhere)
        self.allowed('cd "$SOMEWHERE" && git reset --hard', self.fx.plain)

    def test_a_read_only_command_in_an_unresolved_directory_passes(self):
        self.allowed('cd "$SOMEWHERE" && git status && git log -1', self.fx.wt)

    def test_an_unparsable_command_naming_destructive_git(self):
        cmd = "git reset --hard; echo )"
        self.refused(cmd, self.fx.wt, "could not be parsed")
        self.allowed(cmd, self.fx.nowhere)
        self.allowed("git status; echo )", self.fx.wt)


class ReviewRegressions(Base):
    """One method per finding of the adversarial review of 2026-09-30 (1,633 scripted probes and
    6,540 past Bash commands replayed): each was a real mismatch before its fix."""

    def test_a_worktree_the_same_command_creates_is_a_linked_worktree(self):
        wt, hub = self.fx.wt, self.fx.hub
        for cmd in ("git worktree add ../new -b x HEAD && cd ../new && git commit --allow-empty "
                    "-m x",
                    "git worktree add ../new2 -b y HEAD && git -C ../new2 reset --hard",
                    "git worktree add ../new3 -b z HEAD; (cd ../new3 && git stash)"):
            with self.subTest(cmd=cmd):
                self.allowed(cmd, wt)
        self.allowed("git worktree add .claude/worktrees/new4 -b o && cd .claude/worktrees/new4 "
                     "&& git checkout -- .", hub)
        self.refused("mkdir -p .claude/worktrees/plain-dir && cd .claude/worktrees/plain-dir && "
                     "git checkout -- .", hub)

    def test_xargs_and_find_placeholders(self):
        hub, wt = self.fx.hub, self.fx.wt
        self.refused("echo {} | xargs -I{{}} git -C {{}} reset --hard".format(hub), self.fx.plain)
        self.refused("echo {} | xargs -I% git -C % stash".format(hub), wt)
        self.refused("find {} -maxdepth 0 -exec git -C {{}} reset --hard \\;".format(hub),
                     self.fx.plain)
        self.refused("git ls-files | xargs -I{} sh -c 'cd {} && git reset --hard'", wt)
        self.allowed("echo {} | xargs -I{{}} git -C {{}} reset --hard".format(wt), hub)

    def test_text_fed_to_a_shell(self):
        hub = self.fx.hub
        for cmd in ("cat <<'EOF' | bash\ngit reset --hard\nEOF",
                    "cat <<'EOF' | sh -s\ngit clean -fd\nEOF",
                    "printf 'git reset --hard\\n' | bash",
                    "bash -c \"$(cat <<'EOF'\ngit stash\nEOF\n)\"",
                    'eval "$(echo git reset --hard)"'):
            with self.subTest(cmd=cmd):
                self.refused(cmd, hub)

    def test_a_double_dash_before_main(self):
        for cmd in ("git branch -D -- main", "git branch -m -- wt-branch main",
                    "git branch -f -- main HEAD~1"):
            with self.subTest(cmd=cmd):
                self.refused(cmd, self.fx.wt)

    def test_help_and_dry_run_forms_pass(self):
        for cmd in ("git commit --help", "git checkout --help", "git stash -h", "git pull -h",
                    "git commit --dry-run", "git rebase --show-current-patch"):
            with self.subTest(cmd=cmd):
                self.allowed(cmd, self.fx.hub)
        self.allowed("git filter-branch -h", self.fx.wt)

    def test_wrapper_options(self):
        for cmd in ("time -p git stash", "time -p git reset --hard",
                    "command -- git reset --hard"):
            with self.subTest(cmd=cmd):
                self.refused(cmd, self.fx.hub)

    def test_shell_forms_the_parser_used_to_reject(self):
        forms = ("if true; then (git commit -m x); fi",
                 "for ((i=0;i<2;i++)); do git stash; done",
                 "if (( 1 )); then git commit -m x; fi",
                 "[[ $b =~ ^wt-(a|b)$ ]] && git commit -am x",
                 'while read -r f; do git checkout HEAD -- "$f"; done < <(git diff --name-only)',
                 "exec > >(tee log) 2>&1; git commit -m x",
                 "time (git commit -m x)",
                 "! (git diff --quiet) && git commit -am x")
        for cmd in forms:
            with self.subTest(cmd=cmd):
                self.allowed(cmd, self.fx.wt)
                self.refused(cmd, self.fx.hub)

    def test_an_unexpandable_name_where_git_itself_protects_main(self):
        for cmd in ('git checkout -B "$B" origin/main', 'git switch -C "$B"',
                    'git worktree add -B "$B" ../x', "git $OPTS status",
                    'git --git-dir="$G" log -1'):
            with self.subTest(cmd=cmd):
                self.allowed(cmd, self.fx.wt)
        # A remote main is protected by nobody but this door: an unknown destination stays closed.
        self.refused('git push -f origin "$B"', self.fx.wt, "may be main")

    def test_a_long_chain_of_missing_directories_stays_fast(self):
        for chain in ("; ".join("cd n{}".format(k) for k in range(14)),
                      "; ".join("mkdir -p m{0} && cd m{0}".format(k) for k in range(14))):
            cmd = chain + "; git commit -m x"
            with self.subTest(cmd=cmd[:40]):
                started = time.time()
                r = run_hook(cmd, self.fx.wt, self.fx.home)
                self.assertLess(time.time() - started, 4.0)
                self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)

    def test_force_pushes_that_match_main_without_naming_it(self):
        for cmd in ("git push --force origin 'refs/heads/*:refs/heads/*'",
                    "git push -f origin '+refs/heads/*:refs/heads/*'",
                    "git push -f origin :"):
            with self.subTest(cmd=cmd):
                self.refused(cmd, self.fx.wt)

    def test_second_pass_findings(self):
        """The replay of the same review against the fixed door: what it still found."""
        hub = self.fx.hub
        self.refused("sh - <<'EOF'\ngit reset --hard\nEOF", hub)
        self.refused("bash -s -- x <<'EOF'\ngit reset --hard\nEOF", hub)
        self.refused("time -p -- git stash", hub)
        self.allowed("git pull --dry-run", hub)

    def test_nested_substitutions_and_many_unknown_verbs_stay_fast(self):
        nested = "x"
        for _ in range(40):
            nested = "echo $({})".format(nested)
        unknown = "; ".join("git verb{} a".format(k) for k in range(300))
        for cmd in (nested + "; git status", unknown):
            with self.subTest(cmd=cmd[:30]):
                started = time.time()
                r = run_hook(cmd, self.fx.hub, self.fx.home)
                self.assertLess(time.time() - started, 4.0)
                self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)

    def test_ff_only_with_autostash_or_rebase_false(self):
        self.allowed("git pull --ff-only --rebase=false", self.fx.hub)
        self.refused("git pull --ff-only --autostash", self.fx.hub)
        self.refused("git merge --ff-only --autostash wt-branch", self.fx.hub)


class Witness(Base):
    INPROC = False

    def test_one_refusal_writes_one_row_and_a_pass_writes_none(self):
        ledger = os.path.join(self.fx.home, "door-refusals.jsonl")
        if os.path.exists(ledger):
            os.remove(ledger)
        self.allowed("git status", self.fx.hub)
        self.assertFalse(os.path.exists(ledger))
        self.refused("git checkout -- . && git reset --hard", self.fx.hub)
        with open(ledger, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual([(r["door"], r["event"], r["reason_class"]) for r in rows],
                         [("git-hub-guard", "PreToolUse", "hub_worktree_destroy")])

    def test_a_broken_payload_is_a_loud_crash_not_a_silent_pass(self):
        r = run_hook(None, None, self.fx.home, raw="[1, 2]")
        self.assertEqual(r.returncode, 0, "a broken hook never bricks the session")
        self.assertIn("UNGUARDED", json.loads(r.stdout)["systemMessage"])
        with open(os.path.join(self.fx.home, "hook-health.jsonl"), encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual([(x["hook"], x["outcome"]) for x in rows][-1:],
                         [("git-hub-guard.py", "crash")])

    def test_inherited_git_dir_and_index_file_do_not_redirect_the_probe(self):
        elsewhere = os.path.join(self.fx.root, "elsewhere")
        os.makedirs(elsewhere)
        git(elsewhere, "init", "-q", "--template=", "-b", "main")
        env = clean_env({"GIT_DIR": os.path.join(elsewhere, ".git"),
                         "GIT_INDEX_FILE": os.path.join(elsewhere, ".git", "index"),
                         "GIT_WORK_TREE": elsewhere})
        payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                              "session_id": "git-hub-guard-test", "cwd": self.fx.hub,
                              "tool_input": {"command": "git checkout -- ."}})
        r = subprocess.run([sys.executable, HOOK, "--home", self.fx.home], input=payload,
                           capture_output=True, text=True, env=env, cwd=ROOT, timeout=60)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("git-hub-guard REFUSED", r.stderr)

    def test_a_mistyped_config_key_is_a_loud_crash(self):
        home = tempfile.mkdtemp(prefix="git-hub-guard-badcfg-")
        self.addCleanup(shutil.rmtree, home, True)
        write(os.path.join(home, "trimwrit-gates.json"),
              json.dumps({"main-checkout-guard": {"protected_root": []}}))
        r = run_hook("git checkout -- .", self.fx.hub, home)
        self.assertEqual(r.returncode, 0)
        self.assertIn("UNGUARDED", json.loads(r.stdout)["systemMessage"])


class ConfiguredRoot(unittest.TestCase):
    """A repo WITHOUT the marker, protected because its path is listed in `protected_roots`,
    and the configurable texts of a refusal."""

    @classmethod
    def setUpClass(cls):
        cls.root = os.path.realpath(tempfile.mkdtemp(prefix="git-hub-guard-cfg-"))
        cls.repo = os.path.join(cls.root, "listed")
        os.makedirs(cls.repo)
        git(cls.repo, "init", "-q", "--template=", "-b", "main")
        write(os.path.join(cls.repo, "a.txt"), "one\n")
        git(cls.repo, "add", "-A")
        git(cls.repo, "commit", "-q", "-m", "first")
        cls.trunk = os.path.join(cls.root, "trunkrepo")
        os.makedirs(cls.trunk)
        git(cls.trunk, "init", "-q", "--template=", "-b", "trunk")
        write(os.path.join(cls.trunk, ".claude", "auto-worktree"), "opt-in marker\n")
        write(os.path.join(cls.trunk, "a.txt"), "one\n")
        git(cls.trunk, "add", "-A")
        git(cls.trunk, "commit", "-q", "-m", "first")
        git(cls.trunk, "branch", "feature")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def home(self, cfg):
        home = tempfile.mkdtemp(prefix="git-hub-guard-cfghome-")
        self.addCleanup(shutil.rmtree, home, True)
        if cfg is not None:
            write(os.path.join(home, "trimwrit-gates.json"),
                  json.dumps({"main-checkout-guard": cfg}))
        return home

    def test_an_unlisted_unmarked_repo_passes(self):
        r = run_hook("git checkout -- .", self.repo, self.home(None))
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_a_listed_root_without_the_marker_is_refused(self):
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".claude", "auto-worktree")))
        r = run_hook("git checkout -- .", self.repo, self.home({"protected_roots": [self.repo]}))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("a configured protected root", r.stderr)

    def test_note_and_remedy_come_from_the_configuration(self):
        cfg = {"protected_roots": [self.repo], "note": "On 2026-01-01 a checkout wiped work.",
               "remedy_main_checkout": "run the sync script, nothing else"}
        r = run_hook("git checkout -- .", self.repo, self.home(cfg))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("On 2026-01-01 a checkout wiped work. This door exists since.", r.stderr)
        self.assertIn("run the sync script, nothing else", r.stderr)

    def test_with_nothing_configured_the_default_texts_apply(self):
        r = run_hook("git checkout -- .", self.trunk, self.home(None))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("EnterWorktree", r.stderr)
        self.assertIn("no bypass", r.stderr)
        self.assertNotIn("This door exists since", r.stderr)

    def test_default_branch_trunk_guards_trunk_and_frees_main(self):
        home = self.home({"default_branch": "trunk"})
        r = run_hook("git push --force origin trunk", self.trunk, home)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("main_ref_rewrite", open(os.path.join(home, "door-refusals.jsonl")).read())
        r = run_hook("git push --force origin main", self.trunk, home)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        r = run_hook("git branch -D trunk", self.trunk, home)
        self.assertEqual(r.returncode, 2, r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
