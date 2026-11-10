import json
import types

from trimwrit import preamble as P
from trimwrit.cli import main


def _layout(tmp_path, project="p" * 235, user="u" * 470, rules=("r" * 47,), memory=None):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "CLAUDE.md").write_text(project, encoding="utf-8")
    udir = tmp_path / "home" / ".claude"
    (udir / "rules").mkdir(parents=True)
    (udir / "CLAUDE.md").write_text(user, encoding="utf-8")
    for i, text in enumerate(rules):
        (udir / "rules" / "r{}.md".format(i)).write_text(text, encoding="utf-8")
    if memory is not None:
        mem = udir / "projects" / P.project_slug(str(root)) / "memory"
        mem.mkdir(parents=True)
        (mem / "MEMORY.md").write_text(memory, encoding="utf-8")
    return root, udir


def _budget(root, data):
    (root / ".trimwrit").mkdir(exist_ok=True)
    (root / ".trimwrit" / "budget.json").write_text(json.dumps(data), encoding="utf-8")


def test_comments_are_not_counted():
    assert P.visible("a<!-- trimwrit: R1 case 0001,\n2026: x -->b") == "ab"
    assert P.tokens("x" * 235) == 100
    assert P.tokens("<!-- " + "y" * 900 + " -->" + "x" * 235) == 100


def test_pieces_measure_project_user_rules_and_memory(tmp_path):
    root, udir = _layout(tmp_path, memory="m" * 470)
    (root / "sub").mkdir()
    (root / "sub" / "CLAUDE.md").write_text("n" * 2000, encoding="utf-8")
    rows = {r["name"]: r for r in P.pieces(str(root), user_dir=str(udir))}
    assert set(rows) == {"CLAUDE.md", "~/.claude/CLAUDE.md", "~/.claude/rules", "MEMORY.md"}
    assert rows["CLAUDE.md"]["tokens"] == 100
    assert rows["~/.claude/CLAUDE.md"]["tokens"] == 200
    assert rows["~/.claude/rules"]["tokens"] == 20
    assert rows["MEMORY.md"]["tokens"] == 200
    assert all(r["budget"] is None and not r["over"] for r in rows.values())


def test_missing_memory_and_dot_claude_are_skipped_and_dot_claude_counts_when_present(tmp_path):
    root, udir = _layout(tmp_path)
    names = [r["name"] for r in P.pieces(str(root), user_dir=str(udir))]
    assert "MEMORY.md" not in names and ".claude/CLAUDE.md" not in names
    (root / ".claude").mkdir()
    (root / ".claude" / "CLAUDE.md").write_text("d" * 235, encoding="utf-8")
    names = [r["name"] for r in P.pieces(str(root), user_dir=str(udir))]
    assert ".claude/CLAUDE.md" in names


def test_the_budget_file_sets_budgets_by_name_and_relative_path(tmp_path):
    root, udir = _layout(tmp_path)
    (root / "outreach").mkdir()
    (root / "outreach" / "CLAUDE.md").write_text("o" * 470, encoding="utf-8")
    _budget(root, {"CLAUDE.md": 50, "~/.claude/CLAUDE.md": 500, "outreach/CLAUDE.md": 100})
    rows = {r["name"]: r for r in P.pieces(str(root), user_dir=str(udir))}
    assert rows["CLAUDE.md"]["over"] and rows["CLAUDE.md"]["budget"] == 50
    assert not rows["~/.claude/CLAUDE.md"]["over"]
    assert rows["outreach/CLAUDE.md"]["over"] and rows["outreach/CLAUDE.md"]["tokens"] == 200
    assert P.budget_for(str(root), str(udir / "CLAUDE.md"), user_dir=str(udir)) == (
        "~/.claude/CLAUDE.md", 500)
    assert P.budget_for(str(root), "outreach/CLAUDE.md", user_dir=str(udir)) == (
        "outreach/CLAUDE.md", 100)
    assert P.budget_for(str(root), "docs/other.md", user_dir=str(udir)) is None


def test_cli_exits_3_when_a_piece_is_over_and_0_otherwise(tmp_path, capsys):
    root, udir = _layout(tmp_path)
    assert main(["preamble", "--root", str(root), "--user-dir", str(udir)]) == 0
    out = capsys.readouterr().out
    assert "total" in out and "OVER" not in out
    _budget(root, {"CLAUDE.md": 50})
    assert main(["preamble", "--root", str(root), "--user-dir", str(udir)]) == 3
    out = capsys.readouterr().out
    assert "100 / 50  OVER" in out
    assert main(["preamble", "--root", str(root), "--user-dir", str(udir), "--json"]) == 3
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["name"] == "CLAUDE.md" and rows[0]["over"] is True


def _fake(stdout="", returncode=0, stderr="", raise_=None):
    calls = []

    def run(cmd, cwd):
        calls.append((cmd, cwd))
        if raise_:
            raise raise_
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    run.calls = calls
    return run


def test_the_probe_sums_the_usage_of_one_real_call(tmp_path):
    usage = {"usage": {"input_tokens": 4, "cache_creation_input_tokens": 1000,
                       "cache_read_input_tokens": 20000}}
    run = _fake(stdout=json.dumps(usage))
    assert P.probe(str(tmp_path), runner=run) == (21004, None)
    cmd, cwd = run.calls[0]
    assert cmd[:2] == ["claude", "-p"] and "json" in cmd


def test_an_unavailable_probe_says_why_and_keeps_the_exit_code(tmp_path, capsys):
    root, udir = _layout(tmp_path)
    assert P.probe(str(root), runner=_fake(raise_=FileNotFoundError()))[0] is None
    assert P.probe(str(root), runner=_fake(returncode=1, stderr="boom"))[1] == "exit 1: boom"
    assert P.probe(str(root), runner=_fake(stdout="not json"))[1] == "no usage in the output"
    from trimwrit import cli
    args = ["preamble", "--root", str(root), "--user-dir", str(udir), "--probe"]
    orig = P._default_runner
    P._default_runner = _fake(raise_=FileNotFoundError())
    try:
        assert cli.main(args) == 0
    finally:
        P._default_runner = orig
    assert "probe: unavailable (claude not found)" in capsys.readouterr().out
