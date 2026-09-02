"""`trimwrit adopt`: bring the rules written before trimwrit existed into its registry, with no
case, so `audit` (and later `case` / `integrate`, once a correction touches one) can see them
instead of counting them as nothing.

No model call, no network. Fixtures reuse the shapes of `tests/test_audit.py`: a `.git`
directory makes a fake tree its own repo, `_write` creates a file and its parent directories.
"""
import json
import os

from trimwrit import adopt, audit, cases, integrate, ledger, prune
from trimwrit.cli import main


def _run(tmp_path, *argv):
    return main(["--root", str(tmp_path)] + list(argv))


def _stdout(tmp_path, *argv):
    """Run the CLI and return what it printed to stdout. Copied from test_audit.py's helper of
    the same name, on purpose: two readers of the same CLI wiring drifting apart in how they are
    exercised is exactly the kind of thing this suite is supposed to catch, not cause."""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = _run(tmp_path, *argv)
    assert code == 0
    return buf.getvalue()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _build_repo_a(base):
    """A real repo (`.git` directory) with a `CLAUDE.md` of three `## ` sections and a
    `.claude/rules/r.md` with no `## ` heading at all.

    The integration marker on the third section is produced through `integrate.write_rule`, the
    tool's own writer, never a hand copied string: a test that guesses the marker format proves
    only that adopt agrees with its own guess. `write_rule` appends the marker with no heading of
    its own in front of it, so it lands as trailing content of the LAST section already in the
    file, "## three", which is exactly the section this fixture expects back as `promoted`.
    """
    repo = os.path.join(base, "Code", "repoA")
    os.makedirs(os.path.join(repo, ".git"))
    _write(os.path.join(repo, "CLAUDE.md"),
           "# repoA\n\n## one\n\nfirst body\n\n## two\n\nsecond body\n\n## three\n\n"
           "third body\n")
    integrate.write_rule("CLAUDE.md", "R0001", "0001", "2026-08-23",
                         "an incident, once, on a real day", "the rule text", root=repo)
    _write(os.path.join(repo, ".claude", "rules", "r.md"), "some rule text with no heading\n")
    return repo


def _build_repo_b(base):
    """A second repo with nothing adopted yet: used to prove `audit` prints `-`, not `0`, for a
    repo that has never run `adopt`."""
    repo = os.path.join(base, "Code", "repoB")
    os.makedirs(os.path.join(repo, ".git"))
    _write(os.path.join(repo, "CLAUDE.md"), "# repoB\n\n## only\n\nbody\n")
    return repo


def _rules_path(repo):
    return os.path.join(repo, ".trimwrit", "rules.jsonl")


def _registry(repo):
    return adopt.read_registry(_rules_path(repo))


def _by_key(repo):
    return {(e["file"], e["heading"]): e for e in _registry(repo)}


# ------------------------------------------------------------------ the skeleton


def test_ledger_skeleton_is_touched_empty_and_read_as_no_corrections(tmp_path):
    repo = _build_repo_b(str(tmp_path))
    assert not os.path.exists(os.path.join(repo, ".trimwrit"))

    assert _run(tmp_path, "adopt") == 0

    ledger_path = os.path.join(repo, ".trimwrit", "ledger.jsonl")
    assert os.path.exists(ledger_path)
    assert os.path.getsize(ledger_path) == 0
    assert ledger.corrections(root=repo) == []
    assert ledger.pending(root=repo) == []


def test_evals_readme_created_with_the_three_things_it_needs_to_say(tmp_path):
    repo = _build_repo_a(str(tmp_path))
    assert not os.path.isdir(os.path.join(repo, "evals"))

    assert _run(tmp_path, "adopt") == 0

    readme = os.path.join(repo, "evals", "README.md")
    assert os.path.isfile(readme)
    text = open(readme, encoding="utf-8").read()
    assert "trimwrit case" in text
    assert "trimwrit run" in text


def test_skeleton_never_overwrites_an_existing_ledger_line(tmp_path):
    repo = _build_repo_a(str(tmp_path))
    # A ledger that already has a real correction on it: adopt must never touch it, only create
    # one where none exists at all.
    ledger.log("a correction that predates adopt ever running here", tag="t", root=repo)

    assert _run(tmp_path, "adopt") == 0

    rows = ledger.corrections(root=repo)
    assert len(rows) == 1
    assert rows[0]["text"] == "a correction that predates adopt ever running here"


def test_skeleton_never_overwrites_an_existing_evals_dir(tmp_path):
    repo = _build_repo_a(str(tmp_path))
    evals = os.path.join(repo, "evals")
    cases.write_case("0001", "kept case", "p", [cases.forbid_grader("x", name="g")],
                     evals_dir=evals)
    # No README.md written by hand here on purpose: an evals/ dir that predates adopt must be
    # left alone even when it never got a README of its own.
    assert not os.path.exists(os.path.join(evals, "README.md"))

    assert _run(tmp_path, "adopt") == 0

    assert not os.path.exists(os.path.join(evals, "README.md"))
    assert os.path.isdir(os.path.join(evals, "0001-kept-case"))


def test_dry_run_writes_nothing_at_all(tmp_path):
    repo = _build_repo_a(str(tmp_path))
    assert _run(tmp_path, "adopt", "--dry-run") == 0
    assert not os.path.exists(os.path.join(repo, ".trimwrit"))
    assert not os.path.exists(os.path.join(repo, "evals"))


# ------------------------------------------------------------------ units, ids, boundaries


def test_unit_ids_boundaries_and_the_marked_section_is_promoted_not_adopted(tmp_path):
    repo = _build_repo_a(str(tmp_path))
    assert _run(tmp_path, "adopt") == 0

    by_key = _by_key(repo)
    one = by_key[("CLAUDE.md", "one")]
    two = by_key[("CLAUDE.md", "two")]
    three = by_key[("CLAUDE.md", "three")]
    r_unit = by_key[(os.path.join(".claude", "rules", "r.md"), "r.md")]

    # "## one" runs from its heading line through the blank line right before "## two": lines
    # 3 to 6 of the fixture text (1-based, counting "# repoA" as line 1).
    assert (one["start"], one["end"], one["lines"]) == (3, 6, 4)
    assert (two["start"], two["end"], two["lines"]) == (7, 10, 4)
    # "## three" runs to EOF, which now includes the appended marker and rule text: exactly the
    # reason this is the section that comes back promoted.
    assert three["start"] == 11
    assert three["status"] == "promoted"
    assert three["rules"] == ["R0001"]
    assert "rules" not in one
    assert "rules" not in two

    assert one["status"] == "adopted"
    assert two["status"] == "adopted"
    assert one["case"] is None and three["case"] is None

    # A file with no `## ` heading at all is one unit, headed by its own file name since it has
    # no `# ` line either.
    assert r_unit["status"] == "adopted"
    assert r_unit["start"] == 1
    assert r_unit["heading"] == "r.md"

    ids = [e["id"] for e in _registry(repo)]
    assert len(ids) == len(set(ids)) == 4
    assert sorted(ids) == ids, "the file is written sorted by id"


def test_json_shape(tmp_path):
    _build_repo_a(str(tmp_path))
    payload = json.loads(_stdout(tmp_path, "adopt", "--json"))
    assert set(payload.keys()) == {"repos", "summary"}
    for field in ("repo", "harness_files", "units", "adopted", "promoted", "gone", "changed",
                  "registry", "skeleton"):
        assert field in payload["repos"][0]
    for field in ("repo_count", "harness_files", "units", "adopted", "promoted", "gone",
                  "changed"):
        assert field in payload["summary"]
    assert payload["summary"]["promoted"] == 1
    assert payload["summary"]["adopted"] == 3


def test_report_line_names_the_repo_and_the_four_counts(tmp_path):
    _build_repo_a(str(tmp_path))
    out = _stdout(tmp_path, "adopt")
    assert "adopted" in out and "promoted" in out and "gone" in out and "changed" in out


# ------------------------------------------------------------------ idempotence and drift


def test_a_second_run_changes_nothing(tmp_path):
    repo = _build_repo_a(str(tmp_path))
    assert _run(tmp_path, "adopt") == 0
    first = _registry(repo)

    payload = json.loads(_stdout(tmp_path, "adopt", "--dry-run", "--json"))
    assert payload["summary"]["changed"] == 0

    second = _registry(repo)
    assert first == second, "a dry run after a real run must not have written anything"


def test_editing_a_section_body_changes_its_sha_and_reports_one_changed_with_the_same_id(
        tmp_path):
    repo = _build_repo_a(str(tmp_path))
    assert _run(tmp_path, "adopt") == 0
    before = _by_key(repo)[("CLAUDE.md", "one")]

    claude_md = os.path.join(repo, "CLAUDE.md")
    text = open(claude_md, encoding="utf-8").read()
    _write(claude_md, text.replace("first body", "first body, edited after adoption"))

    payload = json.loads(_stdout(tmp_path, "adopt", "--json"))
    assert payload["summary"]["changed"] == 1

    after = _by_key(repo)[("CLAUDE.md", "one")]
    assert after["id"] == before["id"]
    assert after["sha256"] != before["sha256"]
    assert after["adopted"] == before["adopted"], "first-seen date must not move on an edit"


def test_deleting_a_section_marks_it_gone_and_keeps_it(tmp_path):
    repo = _build_repo_a(str(tmp_path))
    assert _run(tmp_path, "adopt") == 0
    before_ids = {k: v["id"] for k, v in _by_key(repo).items()}

    claude_md = os.path.join(repo, "CLAUDE.md")
    _write(claude_md, "# repoA\n\n## one\n\nfirst body\n\n## three\n\nthird body\n")
    # Re-integrate on the smaller file so "three" keeps carrying its marker: this test is about
    # "two" disappearing, not about losing the promoted section along the way.
    integrate.write_rule("CLAUDE.md", "R0001", "0001", "2026-08-23",
                         "an incident, once, on a real day", "the rule text", root=repo)

    assert _run(tmp_path, "adopt") == 0
    by_key = _by_key(repo)
    two = by_key[("CLAUDE.md", "two")]
    assert two["status"] == "gone"
    assert two["id"] == before_ids[("CLAUDE.md", "two")]
    # Gone units are kept, never deleted.
    assert ("CLAUDE.md", "two") in by_key


def test_adding_a_section_gets_the_next_id(tmp_path):
    repo = _build_repo_a(str(tmp_path))
    assert _run(tmp_path, "adopt") == 0
    used = {e["id"] for e in _registry(repo)}

    claude_md = os.path.join(repo, "CLAUDE.md")
    text = open(claude_md, encoding="utf-8").read()
    _write(claude_md, text + "\n## four\n\nfourth body\n")

    assert _run(tmp_path, "adopt") == 0
    by_key = _by_key(repo)
    four = by_key[("CLAUDE.md", "four")]
    assert four["id"] not in used
    assert four["status"] == "adopted"


# ------------------------------------------------------------------ audit reads the registry


def test_audit_shows_the_adopted_count_and_a_dash_for_a_repo_without_a_registry(tmp_path):
    repo_a = _build_repo_a(str(tmp_path))
    _build_repo_b(str(tmp_path))
    assert _run(tmp_path, "adopt", "--roots", os.path.join(str(tmp_path), "Code", "repoA")) == 0

    payload = json.loads(_stdout(tmp_path, "audit", "--json"))
    by_repo = {r["repo"]: r for r in payload["repos"]}
    a_repo = by_repo[audit._display_path(repo_a)]
    assert a_repo["adopted"] == 3
    assert a_repo["promoted"] == 1

    table = _stdout(tmp_path, "audit")
    assert "adopted" in table.splitlines()[0]
    # repoB never had `adopt` run on it: its row must show `-`, distinguishable from an
    # adopted count of zero.
    b_lines = [ln for ln in table.splitlines() if "repoB" in ln]
    assert b_lines
    assert "-" in b_lines[0].split()


def test_audit_summary_gets_the_adopted_no_case_bucket(tmp_path):
    _build_repo_a(str(tmp_path))
    assert _run(tmp_path, "adopt") == 0
    payload = json.loads(_stdout(tmp_path, "audit", "--json"))
    s = payload["summary"]
    assert s["adopted_no_case_total"] == 3
    assert len(s["adopted_no_case_repos"]) == 1

    table = _stdout(tmp_path, "audit")
    assert "adopted rule(s) with no case" in table


# ------------------------------------------------------------------ prune is unaffected


def test_prune_orphans_are_unchanged_by_the_presence_of_a_registry(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, ".git"))
    evals = os.path.join(root, "evals")
    cases.write_case("0001", "kept case", "p", [cases.forbid_grader("x", name="g")],
                     evals_dir=evals)
    integrate.write_rule("CLAUDE.md", "R0001", "0001", "2026-08-23", "i", "rule one", root=root)
    integrate.write_rule("CLAUDE.md", "R0009", "0009", "2026-08-23", "i", "orphan rule",
                         root=root)
    before = [(f.rule, f.case) for f in prune.orphans(["CLAUDE.md"], evals, root)]

    assert _run(tmp_path, "adopt") == 0
    assert os.path.exists(_rules_path(root))

    after = [(f.rule, f.case) for f in prune.orphans(["CLAUDE.md"], evals, root)]
    assert after == before
    assert [rule for rule, _case in after] == ["R0009"]
