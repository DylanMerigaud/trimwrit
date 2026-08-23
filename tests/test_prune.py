import os

from trimwrit import cases, integrate, prune


def _setup(tmp_path):
    root = str(tmp_path)
    evals = os.path.join(root, "evals")
    cases.write_case("0001", "kept case", "p", [cases.forbid_grader("x", name="g")],
                     evals_dir=evals)
    integrate.write_rule("CLAUDE.md", "R0001", "0001", "2026-08-23", "i", "rule one", root=root)
    integrate.write_rule("CLAUDE.md", "R0009", "0009", "2026-08-23", "i", "orphan rule",
                         root=root)
    return root, evals


def test_orphan_is_found_and_names_the_missing_case(tmp_path):
    root, evals = _setup(tmp_path)
    found = prune.orphans(["CLAUDE.md"], evals, root)
    assert [f.rule for f in found] == ["R0009"]
    assert "case 0009" in found[0].detail


def test_a_rule_whose_case_exists_is_not_an_orphan(tmp_path):
    root, evals = _setup(tmp_path)
    assert "R0001" not in [f.rule for f in prune.orphans(["CLAUDE.md"], evals, root)]


def test_unused_case_is_reported_when_no_rule_points_at_it(tmp_path):
    root, evals = _setup(tmp_path)
    cases.write_case("0002", "lonely", "p", [cases.forbid_grader("y", name="g")],
                     evals_dir=evals)
    found = prune.unused_cases(["CLAUDE.md"], evals, root)
    assert [f.case for f in found] == ["0002-lonely"]


def test_inert_needs_a_zero_or_negative_delta(tmp_path):
    root, evals = _setup(tmp_path)
    summaries = {"0001-kept-case": {"with": 1.0, "without": 1.0, "delta": 0.0}}
    found = prune.inert(summaries, ["CLAUDE.md"], evals, root)
    assert [f.rule for f in found] == ["R0001"]
    assert found[0].numbers == {"with": 1.0, "without": 1.0, "delta": 0.0}


def test_a_positive_delta_is_never_inert(tmp_path):
    root, evals = _setup(tmp_path)
    summaries = {"0001-kept-case": {"with": 1.0, "without": 0.5, "delta": 0.5}}
    assert prune.inert(summaries, ["CLAUDE.md"], evals, root) == []


def test_an_inert_finding_carries_its_two_scores(tmp_path):
    root, evals = _setup(tmp_path)
    summaries = {"0001-kept-case": {"with": 0.8, "without": 0.8, "delta": 0.0}}
    detail = prune.inert(summaries, ["CLAUDE.md"], evals, root)[0].detail
    assert "0.80 with R0001" in detail and "0.80 without" in detail


def test_report_without_results_reports_only_the_free_findings(tmp_path):
    root, evals = _setup(tmp_path)
    kinds = {f.kind for f in prune.report(["CLAUDE.md"], evals, root)}
    assert prune.INERT not in kinds
    assert prune.ORPHAN in kinds
