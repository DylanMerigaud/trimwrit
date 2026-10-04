"""worktree-kit: every script run against throwaway git repositories under a temporary HOME.

Repositories live only under tmp_path, with an isolated HOME and git config. The remote is always
a local bare repository. No `gh`, no real remote, nothing outside tmp_path is touched."""
import json
import os
import subprocess

import pytest

from gates._hook import clean_env, plugin, rows, run_sh, write_config

NAME = "worktree-kit"


class Env:
    """One sandbox: HOME, a hub repository with a local bare origin, and helpers."""

    def __init__(self, tmp_path):
        self.root = tmp_path.resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        (self.home / ".gitconfig").write_text(
            "[user]\n\tname = T\n\temail = t@example.com\n[init]\n\tdefaultBranch = main\n"
            "[protocol \"file\"]\n\tallow = always\n")
        self.data = self.root / "data"
        self.hub = self.root / "hub"
        self.origin = self.root / "origin.git"

    def env(self, **extra):
        e = {"HOME": str(self.home), "GIT_CONFIG_GLOBAL": str(self.home / ".gitconfig"),
             "GIT_CONFIG_NOSYSTEM": "1", "CLAUDE_PLUGIN_DATA": str(self.data)}
        e.update(extra)
        return e

    def git(self, cwd, *args):
        out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                             env=clean_env(self.env()), timeout=60)
        assert out.returncode == 0, (args, out.stderr)
        return out.stdout.strip()

    def make_hub(self, marker=False, gate=None, gitignore=""):
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)], check=True,
                       env=clean_env(self.env()))
        self.hub.mkdir()
        self.git(self.hub, "init", "-q")
        (self.hub / "README").write_text("hello\n")
        (self.hub / ".gitignore").write_text(gitignore)
        if marker:
            (self.hub / ".claude").mkdir()
            (self.hub / ".claude" / "auto-worktree").write_text("")
        if gate is not None:
            (self.hub / ".claude").mkdir(exist_ok=True)
            (self.hub / ".claude" / "automerge-gate.sh").write_text("exit {}\n".format(gate))
        self.git(self.hub, "add", "-A")
        self.git(self.hub, "commit", "-q", "-m", "init")
        self.git(self.hub, "remote", "add", "origin", str(self.origin))
        self.git(self.hub, "push", "-q", "-u", "origin", "main")
        return self.hub

    def worktree(self, name="w1"):
        wt = self.hub / ".claude" / "worktrees" / name
        self.git(self.hub, "worktree", "add", "-q", "-b", "wt-" + name, str(wt))
        return wt

    def commit(self, wt, name="f.txt"):
        (wt / name).write_text("x\n")
        self.git(wt, "add", name)
        self.git(wt, "commit", "-q", "-m", "add " + name)

    def run(self, script, payload, cwd=None, **extra):
        return run_sh(NAME, script, payload, env=self.env(**extra), cwd=str(cwd or self.root))

    def user_config(self, section):
        write_config(self.home / ".claude", {NAME: section})


@pytest.fixture
def sb(tmp_path):
    return Env(tmp_path)


def context(out):
    return json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]


# 1. autostart


def test_autostart_directs_the_main_checkout_of_a_marker_repo(sb):
    hub = sb.make_hub(marker=True)
    out = sb.run("autostart.sh", {"cwd": str(hub), "source": "startup"})
    assert out.returncode == 0
    ctx = context(out)
    assert "bash " + os.path.join(plugin(NAME), "scripts", "postenter.sh") in ctx
    assert "EnterWorktree" in ctx


def test_autostart_is_silent_in_a_linked_worktree(sb):
    hub = sb.make_hub(marker=True)
    wt = sb.worktree()
    out = sb.run("autostart.sh", {"cwd": str(wt), "source": "startup"})
    assert (out.returncode, out.stdout) == (0, "")
    assert hub.exists()


def test_autostart_is_silent_on_an_unmarked_repo_and_on_a_resume(sb):
    hub = sb.make_hub(marker=False)
    assert sb.run("autostart.sh", {"cwd": str(hub), "source": "startup"}).stdout == ""
    (hub / ".claude").mkdir()
    (hub / ".claude" / "auto-worktree").write_text("")
    assert sb.run("autostart.sh", {"cwd": str(hub), "source": "resume"}).stdout == ""


def test_autostart_roster_flags_a_listed_repo_missing_its_marker(sb):
    hub = sb.make_hub(marker=False)
    roster = sb.root / "roster.txt"
    roster.write_text(str(hub) + "\n")
    sb.user_config({"roster": str(roster)})
    out = sb.run("autostart.sh", {"cwd": str(hub), "source": "startup"})
    ctx = context(out)
    assert ctx.startswith("AUTO-WORKTREE ANOMALY: this repository is listed in the roster")
    assert "{}/.claude/auto-worktree is MISSING".format(hub) in ctx
    assert "Do not choose alone." in ctx


def test_autostart_roster_not_listing_the_repo_is_silent(sb):
    hub = sb.make_hub(marker=False)
    roster = sb.root / "roster.txt"
    roster.write_text("/somewhere/else\n")
    sb.user_config({"roster": str(roster)})
    assert sb.run("autostart.sh", {"cwd": str(hub), "source": "startup"}).stdout == ""


def test_autostart_no_roster_configured_prints_nothing_for_an_unmarked_repo(sb):
    hub = sb.make_hub(marker=False)
    out = sb.run("autostart.sh", {"cwd": str(hub), "source": "startup"})
    assert (out.returncode, out.stdout) == (0, "")


def test_autostart_a_broken_config_is_loud_and_the_directive_still_comes(sb):
    hub = sb.make_hub(marker=True)
    (sb.home / ".claude").mkdir()
    (sb.home / ".claude" / "trimwrit-gates.json").write_text("{not json")
    out = sb.run("autostart.sh", {"cwd": str(hub), "source": "startup"})
    assert out.returncode == 0
    assert "AUTO-WORKTREE:" in context(out)


def test_autostart_a_broken_config_is_reported_when_the_roster_is_read(sb):
    hub = sb.make_hub(marker=False)
    (sb.home / ".claude").mkdir()
    (sb.home / ".claude" / "trimwrit-gates.json").write_text("{not json")
    out = sb.run("autostart.sh", {"cwd": str(hub), "source": "startup"})
    assert out.returncode == 0 and out.stdout == ""
    assert "worktree-kit: config error, defaults applied" in out.stderr


# 2. guard


def guard(sb, cwd, target, **extra):
    payload = {"cwd": str(cwd), "session_id": "s1", "tool_name": "Write",
               "tool_input": {"file_path": str(target)}}
    return sb.run("guard.sh", payload, **extra)


def decision(out):
    if not out.stdout.strip():
        return None
    return json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"]


def test_guard_denies_a_write_on_the_main_checkout_and_witnesses_it(sb):
    hub = sb.make_hub(marker=True)
    out = guard(sb, hub, hub / "a.txt")
    assert out.returncode == 0
    assert decision(out) == "deny"
    reason = json.loads(out.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "bash " + os.path.join(plugin(NAME), "scripts", "postenter.sh") in reason
    assert ".claude/auto-worktree-off" in reason
    log = rows(str(sb.data / "door-refusals.jsonl"))
    assert len(log) == 1
    assert log[0]["door"] == "worktree-guard"
    assert log[0]["reason_class"] == "hub_edit_needs_worktree"


def test_guard_denies_a_relative_path_too(sb):
    hub = sb.make_hub(marker=True)
    out = guard(sb, hub, "src/a.txt")
    assert decision(out) == "deny"


def test_guard_allows_dot_claude_outside_paths_worktrees_and_the_off_file(sb):
    hub = sb.make_hub(marker=True)
    assert decision(guard(sb, hub, hub / ".claude" / "x")) is None
    assert decision(guard(sb, hub, sb.root / "elsewhere.txt")) is None
    wt = sb.worktree()
    assert decision(guard(sb, wt, wt / "a.txt")) is None
    assert decision(guard(sb, hub, hub / "a.txt")) == "deny"
    (hub / ".claude" / "auto-worktree-off").write_text("")
    assert decision(guard(sb, hub, hub / "a.txt")) is None
    assert len(rows(str(sb.data / "door-refusals.jsonl"))) == 1


def test_guard_has_no_opinion_on_an_unmarked_repo(sb):
    hub = sb.make_hub(marker=False)
    assert decision(guard(sb, hub, hub / "a.txt")) is None


# 3. postenter


def test_postenter_links_config_files_and_skips_build_junk(sb):
    sb.make_hub(gitignore=".env.local\n.vercel/\nnode_modules/\nbuild.db\nbuild/\n.env.example\n"
                          "app/token.json\n")
    hub = sb.hub
    (hub / ".env.local").write_text("A=1\n")
    (hub / ".vercel").mkdir()
    (hub / ".vercel" / "project.json").write_text("{}")
    (hub / "node_modules").mkdir()
    (hub / "node_modules" / "x").write_text("x")
    (hub / "build.db").write_text("db")
    (hub / ".env.example").write_text("EX")
    (hub / "app").mkdir()
    (hub / "app" / "token.json").write_text("{}")
    wt = sb.worktree()
    out = sb.run("postenter.sh", None, cwd=wt)
    assert out.returncode == 0, out.stderr
    assert (wt / ".env.local").is_symlink()
    assert os.readlink(str(wt / ".env.local")) == str(hub / ".env.local")
    assert (wt / ".vercel" / "project.json").is_symlink()
    assert (wt / "app" / "token.json").is_symlink()
    assert not (wt / "node_modules" / "x").exists()
    assert not (wt / "build.db").exists()
    assert not (wt / ".env.example").exists()
    assert "worktree ready: " + str(wt) in out.stdout
    assert ".claude/worktrees/" in (hub / ".git" / "info" / "exclude").read_text()


def test_postenter_link_extra_from_the_project_layer(sb):
    sb.make_hub(gitignore="build.db\ndata/\n")
    (sb.hub / "build.db").write_text("db")
    (sb.hub / "data").mkdir()
    (sb.hub / "data" / "big.db").write_text("db")
    wt = sb.worktree()
    (wt / ".claude").mkdir()
    (wt / ".claude" / "trimwrit-gates.json").write_text(
        json.dumps({NAME: {"link_extra": ["*.db"]}}))
    out = sb.run("postenter.sh", None, cwd=wt)
    assert out.returncode == 0, out.stderr
    assert (wt / "build.db").is_symlink()
    assert (wt / "data" / "big.db").is_symlink()


def test_postenter_after_runs_in_the_worktree(sb):
    sb.make_hub()
    wt = sb.worktree()
    (wt / ".claude").mkdir()
    (wt / ".claude" / "trimwrit-gates.json").write_text(
        json.dumps({NAME: {"postenter_after": [["sh", "-c", "touch ran"]]}}))
    out = sb.run("postenter.sh", None, cwd=wt)
    assert out.returncode == 0, out.stderr
    assert (wt / "ran").exists()


def test_postenter_catches_the_branch_up_with_the_remote(sb):
    sb.make_hub()
    other = sb.root / "other"
    sb.git(sb.root, "clone", "-q", str(sb.origin), str(other))
    (other / "new.txt").write_text("n\n")
    sb.git(other, "add", "new.txt")
    sb.git(other, "commit", "-q", "-m", "ahead")
    sb.git(other, "push", "-q", "origin", "main")
    wt = sb.worktree()
    assert not (wt / "new.txt").exists()
    out = sb.run("postenter.sh", None, cwd=wt)
    assert "caught up 1 commit(s) from origin/main" in out.stdout
    assert (wt / "new.txt").exists()


def test_postenter_a_broken_config_is_loud_and_the_links_still_happen(sb):
    sb.make_hub(gitignore=".env.local\n")
    (sb.hub / ".env.local").write_text("A=1\n")
    wt = sb.worktree()
    (sb.home / ".claude").mkdir()
    (sb.home / ".claude" / "trimwrit-gates.json").write_text(json.dumps({NAME: {"bogus": 1}}))
    out = sb.run("postenter.sh", None, cwd=wt)
    assert out.returncode == 0
    assert "worktree-kit: config error, defaults applied" in out.stderr
    assert (wt / ".env.local").is_symlink()


# 4. automerge


def automerge(sb, wt):
    return sb.run("automerge.sh", {"cwd": str(wt), "reason": "other"}, cwd=wt)


def origin_main(sb):
    return sb.git(sb.origin, "rev-parse", "main")


@pytest.mark.parametrize("gate,merged", [(0, True), (1, False), (None, False)])
def test_automerge_lands_a_clean_branch_only_through_the_repos_gate(sb, gate, merged):
    sb.make_hub(gate=gate)
    before = origin_main(sb)
    wt = sb.worktree()
    sb.commit(wt)
    head = sb.git(wt, "rev-parse", "HEAD")
    out = automerge(sb, wt)
    assert out.returncode == 0
    if merged:
        assert origin_main(sb) == head
        assert "merged into main" in out.stderr
    else:
        assert origin_main(sb) == before
        assert sb.git(wt, "rev-parse", "HEAD") == head


def test_automerge_refuses_a_dirty_tree_and_the_hub_itself(sb):
    sb.make_hub(gate=0)
    before = origin_main(sb)
    wt = sb.worktree()
    sb.commit(wt)
    (wt / "README").write_text("changed\n")
    out = automerge(sb, wt)
    assert "dirty tree" in out.stderr and origin_main(sb) == before
    hub_out = sb.run("automerge.sh", {"cwd": str(sb.hub)}, cwd=sb.hub)
    assert hub_out.stderr == "" and origin_main(sb) == before


def test_automerge_a_conflict_leaves_the_branch_untouched(sb):
    sb.make_hub(gate=0)
    wt = sb.worktree()
    (wt / "README").write_text("from the worktree\n")
    sb.git(wt, "commit", "-q", "-am", "wt change")
    head = sb.git(wt, "rev-parse", "HEAD")
    other = sb.root / "other"
    sb.git(sb.root, "clone", "-q", str(sb.origin), str(other))
    (other / "README").write_text("from elsewhere\n")
    sb.git(other, "commit", "-q", "-am", "other change")
    sb.git(other, "push", "-q", "origin", "main")
    pushed = origin_main(sb)
    out = automerge(sb, wt)
    assert "CONFLICT" in out.stderr
    assert origin_main(sb) == pushed
    assert sb.git(wt, "rev-parse", "HEAD") == head
    assert sb.git(wt, "status", "--porcelain") == ""


def test_automerge_resume_is_not_a_session_end(sb):
    sb.make_hub(gate=0)
    before = origin_main(sb)
    wt = sb.worktree()
    sb.commit(wt)
    out = sb.run("automerge.sh", {"cwd": str(wt), "reason": "resume"}, cwd=wt)
    assert out.stderr == "" and origin_main(sb) == before


def test_automerge_uses_the_configured_remote(sb):
    sb.make_hub(gate=0)
    sb.git(sb.hub, "remote", "rename", "origin", "upstream")
    sb.user_config({"remote": "upstream"})
    wt = sb.worktree()
    sb.commit(wt)
    head = sb.git(wt, "rev-parse", "HEAD")
    automerge(sb, wt)
    assert origin_main(sb) == head


# 5. cleanup


def ages_path(sb):
    return sb.data / "worktree-ages.tsv"


def test_cleanup_prunes_the_registry_dates_worktrees_and_removes_none(sb):
    sb.make_hub()
    live, gone = sb.worktree("live"), sb.worktree("gone")
    (live / "ignored-junk").write_text("keep me")
    subprocess.run(["rm", "-rf", str(gone)], check=True)
    out = sb.run("cleanup.sh", {"cwd": str(live), "reason": "other"}, cwd=live)
    assert out.returncode == 0, out.stderr
    assert live.is_dir() and (live / "ignored-junk").read_text() == "keep me"
    listing = sb.git(sb.hub, "worktree", "list", "--porcelain")
    assert str(live) in listing and str(gone) not in listing
    lines = ages_path(sb).read_text().splitlines()
    assert lines[0] == "# path\tfirst_seen"
    assert [l.split("\t")[0] for l in lines[1:]] == [str(live)]
    # a second run adds no row
    sb.run("cleanup.sh", {"cwd": str(live), "reason": "other"}, cwd=live)
    assert len(ages_path(sb).read_text().splitlines()) == 2


def test_cleanup_one_row_per_worktree_and_the_configured_ages_file(sb):
    sb.make_hub()
    a, b = sb.worktree("a"), sb.worktree("b")
    custom = sb.root / "elsewhere" / "ages.tsv"
    sb.user_config({"ages_file": str(custom)})
    sb.run("cleanup.sh", {"cwd": str(a)}, cwd=a)
    got = [l.split("\t")[0] for l in custom.read_text().splitlines()[1:]]
    assert sorted(got) == sorted([str(a), str(b)])
    assert not ages_path(sb).exists()


# 6. report


def table(out):
    return [l.split() for l in out.stdout.splitlines()]


def test_report_shows_an_unmerged_worktree(sb):
    sb.make_hub()
    sb.worktree("clean")
    wt = sb.worktree("work")
    sb.commit(wt)
    (wt / "scratch.tmp").write_text("x" * 10)
    (sb.hub / ".git" / "info" / "exclude").write_text("*.tmp\n")
    out = sb.run("report.sh", None, cwd=sb.hub)
    assert out.returncode == 0, out.stderr
    rows_ = table(out)
    assert rows_[0] == ["REPO", "WORKTREE", "AGE", "UNPUSHED", "UNMERGED", "DIRTY", "IGNORED"]
    work = [r for r in rows_ if r[:2] == ["hub", "work"]][0]
    clean = [r for r in rows_ if r[:2] == ["hub", "clean"]][0]
    assert work[2] == "0d"
    assert work[3] == "1" and work[4] == "1" and work[5] == "0"
    assert work[6].endswith("KB")
    assert clean[4] == "0" and clean[6] == "0"
    assert "Nothing was deleted." in out.stdout
    assert ages_path(sb).exists()


def test_report_lists_branches_whose_worktree_is_gone(sb):
    sb.make_hub()
    sb.git(sb.hub, "checkout", "-q", "-b", "orphan")
    (sb.hub / "o.txt").write_text("o\n")
    sb.git(sb.hub, "add", "o.txt")
    sb.git(sb.hub, "commit", "-q", "-m", "orphan work")
    sb.git(sb.hub, "checkout", "-q", "main")
    out = sb.run("report.sh", None, cwd=sb.hub)
    assert "BRANCHES WITHOUT A WORKTREE" in out.stdout
    assert "orphan (+1)" in out.stdout


def test_report_walks_the_roster_and_the_age_math_is_python(sb):
    sb.make_hub()
    wt = sb.worktree("old")
    roster = sb.root / "roster.txt"
    roster.write_text("# comment\n\n{}\n/not/a/repo\n".format(sb.hub))
    sb.user_config({"roster": str(roster)})
    ages_path(sb).parent.mkdir(parents=True, exist_ok=True)
    ages_path(sb).write_text("# path\tfirst_seen\n{}\t2020-01-01\n".format(wt))
    out = sb.run("report.sh", None, cwd=sb.root)
    row = [r for r in table(out) if r[:2] == ["hub", "old"]][0]
    assert row[2].endswith("d") and int(row[2][:-1]) > 2000


# 7. session end


def test_session_end_runs_automerge_then_cleanup_then_session_end_after(sb):
    sb.make_hub(gate=0)
    wt = sb.worktree()
    sb.commit(wt)
    head = sb.git(wt, "rev-parse", "HEAD")
    snap = sb.root / "snap.txt"
    probe = ("test -f {ages} && echo ages-written >> {snap}; "
             "git --git-dir {origin} rev-parse main >> {snap}; cat > {root}/payload.json"
             ).format(ages=ages_path(sb), snap=snap, origin=sb.origin, root=sb.root)
    sb.user_config({"session_end_after": [["sh", "-c", probe]]})
    payload = {"cwd": str(wt), "reason": "other", "session_id": "s1"}
    out = sb.run("session_end.sh", payload, cwd=wt)
    assert out.returncode == 0, out.stderr
    assert snap.read_text().split() == ["ages-written", head]
    assert json.loads((sb.root / "payload.json").read_text())["session_id"] == "s1"
    assert out.stderr.index("automerge took") < out.stderr.index("cleanup took") \
        < out.stderr.index("session_end_after took") < out.stderr.index("total")


def test_session_end_a_failing_after_command_blocks_nothing(sb):
    sb.make_hub()
    wt = sb.worktree()
    sb.user_config({"session_end_after": [["sh", "-c", "exit 3"], ["sh", "-c", "touch last"]]})
    out = sb.run("session_end.sh", {"cwd": str(wt)}, cwd=wt)
    assert out.returncode == 0
    assert (wt / "last").exists()
    assert "exit 3" in out.stderr


def test_session_end_a_broken_config_is_loud_and_exits_zero(sb):
    sb.make_hub()
    wt = sb.worktree()
    (sb.home / ".claude").mkdir()
    (sb.home / ".claude" / "trimwrit-gates.json").write_text(json.dumps({NAME: {"bogus": 1}}))
    out = sb.run("session_end.sh", {"cwd": str(wt)}, cwd=wt)
    assert out.returncode == 0
    assert "worktree-kit:" in out.stderr
