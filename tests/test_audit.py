"""`trimwrit audit`: one table for every harness on the machine, and the three counts that make
it a report worth reading (zero cases, stale, integrated with nothing to measure them).

No model call, no network. Everything here is a fake tree under `tmp_path`, built by hand or
through the CLI doors the rest of the suite already trusts (`trimwrit log`, `ledger.log`), never
by hand-writing a ledger line, because a hand-written ledger line does not prove the reader
matches the writer.
"""
import json
import os
import time

from trimwrit import audit, ledger
from trimwrit.cli import main


def _run(tmp_path, *argv):
    return main(["--root", str(tmp_path)] + list(argv))


MARKER_TMPL = "<!-- trimwrit: R{rid} case {cid}, 2026-08-23: an incident, once, on a real day -->\nthe rule text\n"

CASE_PROMPT = (
    "---\nname: 0001-x\nruns: 1\n---\n\nreplay the situation\n"
)
CASE_GRADER = (
    "---\ntype: regex\nname: forbids-x\npattern: 'x'\nmatch: not_contains\ntarget: last_message\n---\n"
)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _build_repo_a(base):
    """A real repo: a `.git` DIRECTORY, three sections, one integration marker, one case that
    `cases.discover` can actually load, a two line history, and a correction with a consequence
    (so `ledger.pending` has something to report, not just `ledger.corrections`)."""
    repo = os.path.join(base, "Code", "repoA")
    os.makedirs(os.path.join(repo, ".git"))
    _write(os.path.join(repo, "CLAUDE.md"),
           "# repoA\n\n## one\n\nbody\n\n## two\n\nbody\n\n## three\n\nbody\n\n"
           + MARKER_TMPL.format(rid="0001", cid="0001"))
    _write(os.path.join(repo, "evals", "0001-x", "prompt.md"), CASE_PROMPT)
    _write(os.path.join(repo, "evals", "0001-x", "graders", "g.md"), CASE_GRADER)
    now = time.time()
    history = "\n".join([
        json.dumps({"ts": now - 3600, "with": 0.5}),
        json.dumps({"ts": now, "with": 1.0}),
    ]) + "\n"
    _write(os.path.join(repo, "evals", "results", "history.jsonl"), history)
    ledger.log("the correction that justified R0001", tag="t1",
               consequence="a run went out wrong once", root=repo)
    return repo


def _build_repo_b(base):
    """A worktree-shaped repo: `.git` is a FILE, not a directory. No evals anywhere, so it must
    land in the zero cases bucket, and its own integration marker (with nothing to measure it)
    must land in the unmeasured rules bucket."""
    repo = os.path.join(base, "Code", "repoB")
    os.makedirs(repo)
    with open(os.path.join(repo, ".git"), "w", encoding="utf-8") as fh:
        fh.write("gitdir: ../somewhere/.git\n")
    _write(os.path.join(repo, "CLAUDE.md"), "# repoB\n\n" + MARKER_TMPL.format(rid="0099", cid="0099"))
    return repo


def _build_exclusions(base):
    _write(os.path.join(base, "Code", "node_modules", "junk", "CLAUDE.md"), "# junk\n")
    _write(os.path.join(base, "Code", "something.worktrees", "x", "CLAUDE.md"), "# stale worktree copy\n")


def test_finds_harnesses_and_excludes_junk_and_worktrees(tmp_path):
    repo_a = _build_repo_a(str(tmp_path))
    repo_b = _build_repo_b(str(tmp_path))
    _build_exclusions(str(tmp_path))

    result = audit.compute(root=str(tmp_path))
    paths = [h["path"] for h in result["harnesses"]]

    assert not any("node_modules" in p or "junk" in p for p in paths)
    assert not any("worktrees" in p for p in paths)
    assert len(result["harnesses"]) == 2

    a_harness = next(h for h in result["harnesses"] if h["path"].endswith("repoA/CLAUDE.md")
                     or h["path"].endswith("repoA" + os.sep + "CLAUDE.md"))
    assert a_harness["sections"] == 3
    assert a_harness["rules"] == 1
    assert a_harness["lines"] > 0
    assert a_harness["size_kb"] > 0

    a_repo = next(r for r in result["repos"] if r["repo"] == a_harness["repo"])
    assert a_repo["cases"] == 1
    assert a_repo["corrections"] == 1
    assert a_repo["pending_promotions"] == 1
    assert a_repo["last_run"] is not None
    assert a_repo["last_with"] == 1.0

    b_harness = next(h for h in result["harnesses"] if h["path"].endswith("repoB/CLAUDE.md")
                     or h["path"].endswith("repoB" + os.sep + "CLAUDE.md"))
    b_repo = next(r for r in result["repos"] if r["repo"] == b_harness["repo"])
    assert b_repo["cases"] == 0
    assert b_repo["evals_dir"] is None

    summary = result["summary"]
    assert summary["harness_count"] == 2
    assert summary["repo_count"] == 2
    assert b_repo["repo"] in summary["zero_cases"]
    # repo B has a rule marker and no evals dir anywhere: exactly the "promoted and nothing can
    # measure it" case the U bucket exists for.
    assert b_harness["path"] in summary["unmeasured_rules"]


def test_case_count_falls_back_to_a_plain_walk_on_a_malformed_case(tmp_path):
    repo = os.path.join(str(tmp_path), "repoC")
    os.makedirs(os.path.join(repo, ".git"))
    _write(os.path.join(repo, "CLAUDE.md"), "# repoC\n")
    # A case directory with a prompt.md and no graders/ at all: cases.discover raises CaseError
    # on it (a caseless case can never fail), and the audit must not crash, only fall back to
    # counting the directory as a case by presence alone.
    _write(os.path.join(repo, "evals", "0001-y", "prompt.md"), "---\nname: y\n---\n\nreplay\n")

    result = audit.compute(root=str(tmp_path))
    repo_rec = next(r for r in result["repos"] if r["repo"].endswith("repoC"))
    assert repo_rec["cases"] == 1


def test_stale_depends_on_the_threshold(tmp_path):
    _build_repo_a(str(tmp_path))

    out_soon = json.loads(_stdout(tmp_path, "audit", "--stale", "999999", "--json"))
    out_now = json.loads(_stdout(tmp_path, "audit", "--stale", "0", "--json"))

    a_label = next(r["repo"] for r in out_now["repos"] if r["repo"].endswith("repoA"))
    assert a_label not in out_soon["summary"]["stale"]
    assert a_label in out_now["summary"]["stale"]


def test_zero_case_repos_are_never_counted_stale(tmp_path):
    _build_repo_b(str(tmp_path))
    out = json.loads(_stdout(tmp_path, "audit", "--stale", "0", "--json"))
    b_label = next(r["repo"] for r in out["repos"] if r["repo"].endswith("repoB"))
    assert b_label not in out["summary"]["stale"]
    assert b_label in out["summary"]["zero_cases"]


def test_json_shape(tmp_path):
    _build_repo_a(str(tmp_path))
    payload = json.loads(_stdout(tmp_path, "audit", "--json"))
    assert set(payload.keys()) == {"harnesses", "repos", "summary"}
    assert isinstance(payload["harnesses"], list)
    assert isinstance(payload["repos"], list)
    for key in ("harness_count", "repo_count", "zero_cases", "stale", "unmeasured_rules",
                "stale_days"):
        assert key in payload["summary"]
    for field in ("repo", "path", "lines", "sections", "rules", "size_kb"):
        assert field in payload["harnesses"][0]
    for field in ("repo", "evals_dir", "cases", "corrections", "pending_promotions",
                  "last_run", "last_with"):
        assert field in payload["repos"][0]


def test_default_table_output_exits_zero_and_names_the_zero_case_bucket(tmp_path, capsys):
    _build_repo_a(str(tmp_path))
    _build_repo_b(str(tmp_path))
    assert _run(tmp_path, "audit") == 0
    out = capsys.readouterr().out
    assert "harness file" in out
    assert "repo" in out and "cases" in out
    assert "with zero cases" in out


def test_laptop_resolves_home_claude_and_skips_a_missing_code(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _write(os.path.join(str(tmp_path), ".claude", "CLAUDE.md"), "# global\n\n## a\n\nbody\n")
    _write(os.path.join(str(tmp_path), ".claude", "rules", "r.md"), "some rule text\n")
    # deliberately no ~/Code: --laptop must skip it rather than fail.

    payload = json.loads(_stdout(tmp_path, "audit", "--laptop", "--json"))
    paths = sorted(h["path"] for h in payload["harnesses"])
    assert paths == sorted(["~/.claude/CLAUDE.md", "~/.claude/rules/r.md"])
    assert len(payload["repos"]) == 1
    assert payload["repos"][0]["repo"] == "~/.claude"
    assert not any("Code" in h["path"] for h in payload["harnesses"])


def _stdout(tmp_path, *argv):
    """Run the CLI and return what it printed to stdout, without needing a `capsys` fixture in
    every caller: most of these tests want the JSON text, not pytest's capture object."""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = _run(tmp_path, *argv)
    assert code == 0
    return buf.getvalue()


def test_an_umbrella_directory_never_borrows_a_nested_repos_cases(tmp_path):
    """Found on the first laptop run, 2026-09-01: `~/Code/newsnakeproject` has no `.git`, so it
    fell back to `~/Code` as its repo, and the evals walk under `~/Code` then found
    `~/Code/growth-cockpit/evals` and reported the umbrella as holding 38 cases that belong to a
    repo of its own. An `evals/` inside a directory carrying its own `.git` is that repo's row,
    never the umbrella's.
    """
    base = tmp_path
    umbrella = os.path.join(base, "Code")
    # The non-repo project that makes the umbrella a pseudo-repo.
    _write(os.path.join(umbrella, "loose-project", "CLAUDE.md"), "# loose\n\n## one\n")
    # The real repo next to it, with cases of its own.
    nested = os.path.join(umbrella, "nested")
    os.makedirs(os.path.join(nested, ".git"))
    _write(os.path.join(nested, "CLAUDE.md"), "# nested\n\n## one\n")
    _write(os.path.join(nested, "evals", "0001-x", "prompt.md"), CASE_PROMPT)
    _write(os.path.join(nested, "evals", "0001-x", "graders", "g.md"), CASE_GRADER)

    doc = audit.compute(root=umbrella)
    by_repo = {r["repo"]: r for r in doc["repos"]}
    assert by_repo[audit._display_path(nested)]["cases"] == 1
    assert by_repo[audit._display_path(umbrella)]["cases"] == 0, (
        "the umbrella borrowed the nested repo's evals dir")
    assert audit._display_path(umbrella) in doc["summary"]["zero_cases"]
