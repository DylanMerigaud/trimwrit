"""The no-third-party-pr plugin: no pull request on a GitHub repository you do not own, run the
way Claude Code runs it (a PreToolUse payload on stdin, --home on argv).

Ports growth-cockpit's tests/test_third_party_pr_gate.py. The repositories are throwaway
`git init` directories with local remotes added by hand (no network). The matrices run in this
process (the module loaded by path, configured with owners ["dylanmerigaud"]); the hook itself
runs as a subprocess with HOME pointing at a temp dir. Nothing here calls the real `gh` or opens
a pull request: the hook only judges commands, and the exemption command is a fixture script.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hook  # noqa: E402

NAME = "no-third-party-pr"
SCRIPT = "no_third_party_pr.py"
GATE = os.path.join(_hook.plugin(NAME), "scripts", SCRIPT)
DEFAULTS = json.load(open(os.path.join(_hook.plugin(NAME), "defaults.json")))["defaults"]
OWNED = dict(DEFAULTS, owners=["dylanmerigaud"])
DASHES = (chr(0x2014), chr(0x2013))


def _load():
    spec = importlib.util.spec_from_file_location("no_third_party_pr_under_test", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load()


@pytest.fixture(autouse=True)
def configured(tmp_path, monkeypatch):
    """Owners dylanmerigaud, no exemption command, and a HOME with no gh configuration."""
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    gate.configure_from(OWNED)
    yield
    gate.configure_from(OWNED)


def _git_repo(base, name, remotes):
    path = os.path.join(base, name)
    os.makedirs(path)
    subprocess.run(["git", "init", "-q", path], check=True)
    for rname, url in remotes:
        subprocess.run(["git", "-C", path, "remote", "add", rname, url], check=True)
    return path


@pytest.fixture(scope="module")
def repos():
    base = tempfile.mkdtemp(prefix="pr_gate_")
    out = {
        "own": _git_repo(base, "own", [("origin",
                                        "https://github.com/DylanMerigaud/growth-cockpit.git")]),
        "own_ssh": _git_repo(base, "own_ssh", [("origin",
                                                "git@github.com:DylanMerigaud/bankfile.git")]),
        "third": _git_repo(base, "third", [("origin", "https://github.com/psf/requests.git")]),
        "fork": _git_repo(base, "fork", [("origin", "git@github.com:DylanMerigaud/requests.git"),
                                         ("upstream", "https://github.com/psf/requests.git")]),
        "submodule": _git_repo(base, "captcha", [(
            "origin", "https://github.com/interfluve-wav/auto-captcha-solver.git")]),
        "bare": _git_repo(base, "bare", []),
        "gitlab": _git_repo(base, "gitlab", [("origin", "https://gitlab.com/DylanMerigaud/x.git")]),
        "someone": _git_repo(base, "someone", [("origin", "https://github.com/someone/x.git")]),
        "plain": os.path.join(base, "plain"),
    }
    os.makedirs(out["plain"])
    yield out
    shutil.rmtree(base, ignore_errors=True)


def refused(command, cwd):
    return gate.decide(command, cwd)


def fmt(command, repos):
    """Fill {own}, {third}, ... with the fixture paths; every other brace stays as written."""
    for key, path in repos.items():
        command = command.replace("{" + key + "}", path)
    return command


# -- the matrix -------------------------------------------------------------------------------

ALLOWED_IN_OWN = [
    "gh pr create --base main --head worktree-x --title t --body b",
    "gh pr create --fill",
    "gh pr create -R DylanMerigaud/growth-cockpit --fill",
    "gh pr create --repo dylanmerigaud/growth-cockpit --fill",
    "gh pr create --repo https://github.com/DylanMerigaud/growth-cockpit --fill",
    "git push -u origin HEAD && gh pr create --fill && gh pr merge --squash",
    "gh pr merge 12 --squash",
    "gh pr view 3 -R psf/requests",
    "gh pr list -R psf/requests --state all --limit 50",
    "gh pr checks 7 -R psf/requests",
    "gh pr create --dry-run -R psf/requests --fill",
    "gh api repos/psf/requests/pulls?state=all",
    "gh api repos/psf/requests/pulls --paginate -q '.[].number'",
    "gh api -X GET repos/psf/requests/pulls -f state=closed",
    "gh api -X POST repos/openjournals/joss-reviews/issues/9123/comments -f body=hello",
    "gh api repos/openjournals/joss-reviews/issues/9123/comments -f body=hello",
    "gh issue comment 9123 -R openjournals/joss-reviews --body-file review.md",
    "gh api -X POST repos/DylanMerigaud/growth-cockpit/pulls -f title=t -f head=x -f base=main",
    "gh api graphql -f query='query { viewer { login } }'",
    "curl -s https://api.github.com/repos/psf/requests/pulls",
    "curl -X POST https://api.github.com/repos/DylanMerigaud/growth-cockpit/pulls -d '{}'",
    "echo gh pr create -R psf/requests",
    "grep -rn 'gh pr create -R psf/requests' docs/",
    "python3 -c \"print('pulls are read only')\"",
    "git fetch origin && git log --oneline -3",
    "cd {third} && cd {own} && gh pr create --fill",
]

REFUSED_IN_OWN = [
    ("gh pr create -R psf/requests --fill", "psf/requests"),
    ("gh pr create --repo=psf/requests --fill", "psf/requests"),
    ("gh pr create -Rpsf/requests --fill", "psf/requests"),
    ("gh pr new -R psf/requests", "psf/requests"),
    ("gh pr -R psf/requests create --fill", "psf/requests"),
    ("gh pr create --repo https://github.com/psf/requests --fill", "psf/requests"),
    ("gh pr create --repo github.com/psf/requests --fill", "psf/requests"),
    ("command gh pr create -R psf/requests", "psf/requests"),
    ("/opt/homebrew/bin/gh pr create -R psf/requests", "psf/requests"),
    ("GH_REPO=psf/requests gh pr create --fill", "psf/requests"),
    ("env GH_REPO=psf/requests gh pr create --fill", "psf/requests"),
    ("export GH_REPO=psf/requests; gh pr create --fill", "psf/requests"),
    ("gh api -X POST repos/psf/requests/pulls -f title=t -f head=a:b -f base=main",
     "psf/requests"),
    ("gh api --method POST /repos/psf/requests/pulls -f title=t", "psf/requests"),
    ("gh api --method=post repos/psf/requests/pulls -f title=t", "psf/requests"),
    ("gh api -XPOST repos/psf/requests/pulls", "psf/requests"),
    ("gh api repos/psf/requests/pulls -f title=t -f head=a:b -f base=main", "psf/requests"),
    ("gh api repos/psf/requests/pulls --input body.json", "psf/requests"),
    ("gh api https://api.github.com/repos/psf/requests/pulls -X POST -f title=t",
     "psf/requests"),
    ("curl -X POST -H 'Authorization: token x' https://api.github.com/repos/psf/requests/pulls "
     "-d '{\"title\": \"t\"}'", "psf/requests"),
    ("curl https://api.github.com/repos/psf/requests/pulls -d '{}'", "psf/requests"),
    ("curl --request POST https://api.github.com/repos/psf/requests/pulls", "psf/requests"),
    ("python3 -c \"import subprocess; subprocess.run(['gh', 'pr', 'create', '-R', "
     "'psf/requests'])\"", "psf/requests"),
]

COMPOUND = [
    "git push -u origin HEAD && gh pr create -R psf/requests --fill",
    "true; gh pr create -R psf/requests",
    "false || gh pr create -R psf/requests",
    "bash -c 'gh pr create -R psf/requests --fill'",
    "sh -c \"cd {third} && gh pr create --fill\"",
    "zsh -c 'gh pr new -R psf/requests'",
    "echo $(gh pr create -R psf/requests --fill)",
    "x=`gh pr create -R psf/requests`",
    "if true; then gh pr create -R psf/requests; fi",
    "for i in 1; do gh pr create -R psf/requests; done",
    "eval 'gh pr create -R psf/requests'",
    "echo 'gh pr create -R psf/requests' | bash",
    "(cd {third} && gh pr create --fill)",
    "cd {third} && gh pr create --fill",
    "cd {fork} && gh pr create --fill",
    "nohup gh pr create -R psf/requests > /dev/null 2>&1 &",
    "bash -c \"bash -c 'gh pr create -R psf/requests'\"",
]

FAIL_CLOSED = [
    ("gh pr create -R \"$REPO\" --fill", "own"),
    ("cd \"$DIR\" && gh pr create --fill", "own"),
    ("gh pr create --fill", "plain"),
    ("gh pr create --fill", "bare"),
    ("gh pr create --fill", "gitlab"),
    ("gh api -X POST \"repos/$OWNER/requests/pulls\" -f title=t", "own"),
    ("gh api -X \"$M\" repos/psf/requests/pulls", "own"),
    ("gh api graphql -f query='mutation { createPullRequest(input: {repositoryId: \"R_1\", "
     "baseRefName: \"main\", headRefName: \"b\", title: \"t\"}) { pullRequest { url } } }'",
     "own"),
    ("gh pr create -R psf/requests 'unterminated", "own"),
    ("python3 -c \"import subprocess; subprocess.run(['gh', 'pr', 'create', '--fill'])\"",
     "own"),
]


@pytest.mark.parametrize("command", ALLOWED_IN_OWN)
def test_allowed_from_an_owned_checkout(command, repos):
    assert refused(fmt(command, repos), repos["own"]) == []


@pytest.mark.parametrize("command,target", REFUSED_IN_OWN)
def test_refused_on_a_third_party_target(command, target, repos):
    found = refused(command, repos["own"])
    assert found, command
    assert found[0]["class"] == "third_party_pr"
    assert found[0]["target"].endswith(target)


@pytest.mark.parametrize("command", COMPOUND)
def test_compound_commands_are_seen_through(command, repos):
    found = refused(fmt(command, repos), repos["own"])
    assert found, command
    assert found[0]["class"] in ("third_party_pr", "unresolved_target")


@pytest.mark.parametrize("command,where", FAIL_CLOSED)
def test_a_target_the_gate_cannot_resolve_is_refused(command, where, repos):
    found = refused(command, repos[where])
    assert found, command
    assert found[0]["class"] in ("unresolved_target", "unparsable_command", "third_party_pr")


def test_a_third_party_clone_refuses_a_bare_pr_create(repos):
    found = refused("gh pr create --fill", repos["third"])
    assert found and found[0]["target"] == "github.com/psf/requests"
    assert refused("gh pr create --fill", repos["submodule"])


def test_a_fork_with_a_third_party_upstream_refuses_the_default_target(repos):
    # gh picks `upstream` before `origin` when it resolves the base repository
    found = refused("gh pr create --fill", repos["fork"])
    assert found and found[0]["target"] == "github.com/psf/requests"
    assert refused("gh pr create -R DylanMerigaud/requests --fill", repos["fork"]) == []


def test_placeholders_resolve_from_the_checkout(repos):
    cmd = "gh api -X POST repos/{owner}/{repo}/pulls -f title=t"
    assert refused(cmd, repos["third"])
    assert refused(cmd, repos["own"]) == []
    assert refused(cmd.replace("{owner}/{repo}", ":owner/:repo"), repos["third"])


def test_an_owned_ssh_remote_keeps_working(repos):
    assert refused("gh pr create --fill", repos["own_ssh"]) == []
    assert refused("gh pr create --base main --head worktree-x --title t --body b",
                   repos["own"]) == []
    assert refused("gh pr merge --squash --delete-branch", repos["own"]) == []


def test_a_shell_script_file_is_read(repos, tmp_path):
    script = tmp_path / "open_pr.sh"
    script.write_text("#!/bin/bash\nset -e\ngh pr create -R psf/requests --fill\n")
    assert refused(f"bash {script}", repos["own"])
    assert refused(f"source {script}", repos["own"])
    safe = tmp_path / "safe.sh"
    safe.write_text("#!/bin/bash\ngh pr list -R psf/requests\n")
    assert refused(f"bash {safe}", repos["own"]) == []


def test_owners_compare_case_insensitively_and_hosts_are_configurable(repos):
    gate.configure_from(dict(OWNED, owners=["DylanMERIGAUD"]))
    assert refused("gh pr create -R dylanmerigaud/x --fill", repos["own"]) == []
    gate.configure_from(dict(OWNED, hosts=["git.example.com"]))
    assert refused("gh pr create -R DylanMerigaud/x --fill", repos["own"])
    assert refused("gh pr create -R git.example.com/DylanMerigaud/x --fill", repos["own"]) == []


def test_a_crashing_reader_fails_closed_on_a_pr_command(repos, monkeypatch):
    def boom(self, command):
        raise RuntimeError("parser broke")
    monkeypatch.setattr(gate.PrGuard, "check", boom)
    found = gate.decide("gh pr create -R psf/requests", repos["own"])
    assert found and found[0]["class"] == "door_crash"
    assert gate.decide("ls -la", repos["own"]) == []


def test_the_reason_names_the_rule_the_target_and_what_to_do(repos):
    found = refused("gh pr create -R psf/requests --fill", repos["own"])
    text = gate.reason_text(found)
    assert text.startswith("refused: third-party-pr")
    for needle in ("github.com/psf/requests", "not a repository of dylanmerigaud",
                   "never open a pull request on a repository you do not own",
                   "public trace under your name", "Do instead", "No bypass",
                   "(no-third-party-pr)"):
        assert needle in text, needle
    assert not any(d in text for d in DASHES)


def test_note_and_reason_extra_are_added_to_the_reason(repos):
    gate.configure_from(dict(OWNED, note="since the 2026-08-18 incident",
                             reason_extra="Ask the maintainer on the mailing list."))
    text = gate.reason_text(refused("gh pr create -R psf/requests --fill", repos["own"]))
    assert "public trace under your name. (since the 2026-08-18 incident)" in text
    assert "\n\nAsk the maintainer on the mailing list.\n\nDo instead" in text


# -- the owners: configured, else gh's own account, else nobody ------------------------------

def _hosts(tmp_path, monkeypatch, text):
    home = tmp_path / "ghhome"
    (home / ".config" / "gh").mkdir(parents=True)
    (home / ".config" / "gh" / "hosts.yml").write_text(text)
    monkeypatch.setenv("HOME", str(home))


HOSTS_YML = "github.com:\n    users:\n        someone:\n    user: someone\n    git_protocol: ssh\n"


def test_empty_owners_fall_back_to_the_gh_account(repos, tmp_path, monkeypatch):
    _hosts(tmp_path, monkeypatch, HOSTS_YML)
    gate.configure_from(dict(DEFAULTS))
    assert refused("gh pr create -R someone/x --fill", repos["own"]) == []
    assert refused("gh pr create --fill", repos["someone"]) == []
    assert refused("gh pr create -R psf/requests --fill", repos["own"])


def test_empty_owners_and_no_gh_account_refuse_every_pull_request(repos):
    gate.configure_from(dict(DEFAULTS))
    found = refused("gh pr create --fill", repos["own"])
    assert found and found[0]["class"] == "third_party_pr"
    text = gate.reason_text(found)
    assert "no owner is configured: set owners in trimwrit-gates.json" in text
    assert refused("gh pr create -R someone/x --fill", repos["own"])


def test_the_hook_reads_the_gh_account_from_home(repos, tmp_path):
    home, fake = tmp_path / "h", tmp_path / "ghhome"
    (fake / ".config" / "gh").mkdir(parents=True)
    (fake / ".config" / "gh" / "hosts.yml").write_text(HOSTS_YML)
    _hook.write_config(home, {})
    ok = _run(repos["own"], "gh pr create -R someone/x --fill", home, fake)
    assert ok.returncode == 0 and ok.stdout.strip() == "", ok.stderr
    bad = _run(repos["own"], "gh pr create -R psf/requests --fill", home, fake)
    assert json.loads(bad.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


# -- the hook as Claude Code runs it ----------------------------------------------------------

def _payload(cwd, command, tool="Bash"):
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": "t", "cwd": cwd,
            "tool_input": {"command": command}}


def _run(cwd, command, home, fake_home, tool="Bash"):
    return _hook.run_py(NAME, SCRIPT, _payload(cwd, command, tool), home,
                        env={"HOME": str(fake_home)}, cwd=cwd)


def _decision(p):
    return json.loads(p.stdout)["hookSpecificOutput"]


@pytest.fixture
def hook_home(tmp_path):
    home = tmp_path / "h"
    _hook.write_config(home, {NAME: {"owners": ["dylanmerigaud"]}})
    return home, tmp_path / "nohome"


def test_the_hook_denies_with_a_structured_reason(repos, hook_home):
    home, fake = hook_home
    p = _run(repos["own"], "gh pr create -R psf/requests --fill", home, fake)
    assert p.returncode == 0, p.stderr
    out = _decision(p)
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    assert out["permissionDecisionReason"].startswith("refused: third-party-pr")
    witnessed = _hook.rows(os.path.join(str(home), "door-refusals.jsonl"))
    assert [(r["door"], r["reason_class"]) for r in witnessed] == [("third-party-pr",
                                                                    "third_party_pr")]


def test_the_hook_is_silent_on_an_allowed_call(repos, hook_home):
    home, fake = hook_home
    for command in ("ls -la", "gh pr create --fill",
                    "gh api -X POST repos/openjournals/joss-reviews/issues/1/comments -f b=x"):
        p = _run(repos["own"], command, home, fake)
        assert p.returncode == 0 and p.stdout.strip() == "", (command, p.stdout, p.stderr)


def test_the_hook_ignores_other_tools(repos, hook_home):
    home, fake = hook_home
    p = _hook.run_py(NAME, SCRIPT, {"hook_event_name": "PreToolUse", "tool_name": "Write",
                                    "session_id": "t", "tool_input": {
                                        "file_path": "/tmp/x",
                                        "content": "gh pr create -R psf/requests"}},
                     home, env={"HOME": str(fake)})
    assert p.returncode == 0 and p.stdout.strip() == ""


def test_the_defaults_refuse_every_pull_request(repos, tmp_path):
    """No configuration file at all: owners is empty, so even an own-looking target is refused."""
    home = tmp_path / "h"
    p = _run(repos["own"], "gh pr create -R DylanMerigaud/x --fill", home, tmp_path / "nohome")
    out = _decision(p)
    assert out["permissionDecision"] == "deny"
    assert "no owner is configured: set owners in trimwrit-gates.json" in \
        out["permissionDecisionReason"]


# -- a broken configuration fails closed on a pull request -----------------------------------

BROKEN = [
    ("an unknown key", json.dumps({NAME: {"owner": ["x"]}})),
    ("a wrong type", json.dumps({NAME: {"owners": "dylanmerigaud"}})),
    ("invalid JSON", "{not json"),
]


@pytest.mark.parametrize("why,text", BROKEN, ids=[b[0] for b in BROKEN])
def test_a_broken_configuration_denies_a_pull_request(why, text, repos, tmp_path):
    home = tmp_path / "h"
    home.mkdir()
    (home / "trimwrit-gates.json").write_text(text)
    p = _run(repos["own"], "gh pr create --fill", home, tmp_path / "nohome")
    assert p.returncode == 0, p.stderr
    out = _decision(p)
    assert out["permissionDecision"] == "deny"
    reason = out["permissionDecisionReason"]
    assert "configuration is broken" in reason and "trimwrit-gates.json" in reason
    assert "Fix trimwrit-gates.json" in reason
    crash = [r for r in _hook.rows(os.path.join(str(home), "hook-health.jsonl"))
             if r["outcome"] == "crash"]
    assert crash and crash[0]["hook"] == "third_party_pr_gate.py", why


def test_a_broken_configuration_does_not_brick_a_command_without_a_pull_request(repos, tmp_path):
    home = tmp_path / "h"
    home.mkdir()
    (home / "trimwrit-gates.json").write_text("{not json")
    p = _run(repos["own"], "ls -la", home, tmp_path / "nohome")
    assert p.returncode == 0
    assert "permissionDecision" not in p.stdout
    assert "crashed" in json.loads(p.stdout)["systemMessage"]
    assert any(r["outcome"] == "crash" for r in
               _hook.rows(os.path.join(str(home), "hook-health.jsonl")))


# -- the one exemption: a command of the configuration ----------------------------------------

FIXTURE_COMMAND = """import json, sys
answers = json.load(open(sys.argv[1]))
ask = json.load(sys.stdin)
open(sys.argv[2], "a").write(json.dumps(ask) + "\\n")
mode = answers.get("mode")
if mode == "garbage":
    print("this is not json")
elif mode == "list":
    print(json.dumps([1, 2]))
elif mode == "string":
    print(json.dumps("yes"))
elif mode == "sleep":
    import time; time.sleep(30)
else:
    print(json.dumps(answers.get(ask["repo"] + "|" + ask["title"],
                                 {"ok": False, "reason": "no ticket for this repo and title"})))
"""


@pytest.fixture
def exempt(tmp_path, repos):
    script = tmp_path / "exempt.py"
    script.write_text(FIXTURE_COMMAND)
    answers, log = tmp_path / "answers.json", tmp_path / "asked.jsonl"
    home = tmp_path / "h"
    cmd = [sys.executable, str(script), str(answers), str(log)]
    _hook.write_config(home, {NAME: {"owners": ["dylanmerigaud"], "exemption_command": cmd}})

    class Ctx:
        def answer(self, data):
            answers.write_text(json.dumps(data))

        def asked(self):
            return _hook.rows(str(log))

        def run(self, command):
            return _run(repos["own"], command, home, tmp_path / "nohome")

        cmd_ = cmd
    ctx = Ctx()
    ctx.answer({"psf/requests|Fix typo": {"ok": True, "reason": "aligned"}})
    return ctx


EXEMPT_OK = "gh pr create -R psf/requests --title 'Fix typo' --body 'Body text'"


def test_the_exemption_command_allows_exactly_the_ticketed_shape(exempt):
    p = exempt.run(EXEMPT_OK)
    assert p.returncode == 0 and p.stdout.strip() == "", (p.stdout, p.stderr)
    assert exempt.asked() == [{"repo": "psf/requests", "title": "Fix typo",
                               "body": "Body text", "command": EXEMPT_OK}]


def test_the_exemption_reads_a_body_file(exempt, repos):
    body = os.path.join(repos["own"], "body.md")
    with open(body, "w") as fh:
        fh.write("From a file\n")
    p = exempt.run("gh pr create -R psf/requests --title 'Fix typo' --body-file body.md")
    assert p.returncode == 0 and p.stdout.strip() == "", (p.stdout, p.stderr)
    assert exempt.asked()[0]["body"] == "From a file\n"


def test_another_title_is_refused_with_the_command_reason(exempt):
    p = exempt.run("gh pr create -R psf/requests --title 'Another' --body B")
    out = _decision(p)
    assert out["permissionDecision"] == "deny"
    assert "Ticket: no ticket for this repo and title" in out["permissionDecisionReason"]
    assert len(exempt.asked()) == 1


@pytest.mark.parametrize("command", [
    EXEMPT_OK + " --fill",
    EXEMPT_OK + " | cat",
    EXEMPT_OK + " && echo done",
    "gh pr create -R psf/requests --title 'Fix typo' --fill",
    "gh pr create -R psf/requests --title 'Fix typo' --body $BODY",
    "gh pr create -R psf/requests -R psf/other --title 'Fix typo' --body B",
])
def test_another_shape_is_refused_before_the_command_is_asked(command, exempt):
    p = exempt.run(command)
    assert _decision(p)["permissionDecision"] == "deny"
    assert exempt.asked() == [], command


def test_an_exemption_never_covers_an_unresolved_target(exempt):
    p = exempt.run('gh pr create -R "$REPO" --title \'Fix typo\' --body B')
    assert _decision(p)["permissionDecision"] == "deny"
    assert exempt.asked() == []


@pytest.mark.parametrize("mode,words", [
    ("garbage", "the exemption command failed"),
    ("list", "the exemption command failed: the answer is not a JSON object"),
    ("string", "the exemption command failed: the answer is not a JSON object"),
])
def test_an_answer_that_is_not_an_object_refuses(mode, words, exempt):
    exempt.answer({"mode": mode})
    out = _decision(exempt.run(EXEMPT_OK))
    assert out["permissionDecision"] == "deny"
    assert words in out["permissionDecisionReason"]


def test_a_missing_exemption_command_refuses(repos, tmp_path):
    home = tmp_path / "h"
    _hook.write_config(home, {NAME: {"owners": ["dylanmerigaud"],
                                     "exemption_command": ["/nonexistent/exemption"]}})
    out = _decision(_run(repos["own"], EXEMPT_OK, home, tmp_path / "nohome"))
    assert out["permissionDecision"] == "deny"
    assert "the exemption command failed" in out["permissionDecisionReason"]


def test_a_slow_exemption_command_times_out_into_a_refusal(exempt, monkeypatch):
    exempt.answer({"mode": "sleep"})
    monkeypatch.setattr(gate, "EXEMPTION_TIMEOUT_S", 1)
    ok, reason = gate.exemption(exempt.cmd_, "psf/requests", "t", "b", "c")
    assert not ok and reason.startswith("the exemption command failed")


def test_no_exemption_command_means_no_exemption(repos):
    found = gate.decide(EXEMPT_OK, repos["own"])
    assert found and found[0]["class"] == "third_party_pr" and "ticket" not in found[0]
    assert gate.exemption((), "a/b", "t", "b", "c") == (False, "no exemption command is configured")
