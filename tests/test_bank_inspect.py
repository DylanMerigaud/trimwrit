"""The optional Inspect AI engine (trimwrit/bank_inspect.py) behind bank.main's engine seam.

Inspect AI is never a dependency of trimwrit: this module is skipped where inspect_ai does not
import (the default install). Run it from a separate venv that has inspect-ai and pytest:
`v/bin/python -m pytest -q tests/test_bank_inspect.py`. Inspect is handed `mockllm/model`,
which calls nothing: no test calls a real model or the network.

Besides the parity tests, the ten hole tests and the clean-bank test of test_bank_standard.py
run again here with `--engine inspect` appended to every bank.main argv (test_on_inspect_<name>);
a test there that never calls bank.main is engine independent and runs unchanged."""
import glob
import json

import pytest

pytest.importorskip("inspect_ai")

import test_bank_standard as standard  # noqa: E402  (after the skip, on pytest's rootdir path)
from test_bank_standard import model_bank, release, standard_bank  # noqa: E402

from trimwrit import bank, bank_inspect  # noqa: E402

ENGINE_ONLY = ("at", "run", "wall_s", "engine", "engine_log")
RAISE_ON_C03 = ('if case["id"] == "c03":\n    raise ValueError("no answer for c03")\n'
                'return "yes" if case["input"]["x"] % 2 else "no"')


def run_both(tmp_path, build, *argv):
    """Build the same bank twice and run it once per engine: {engine: (code, bank dir)}."""
    out = {}
    for engine in ("trimwrit", "inspect"):
        sub = tmp_path / engine
        sub.mkdir()
        d = build(sub)
        code, _ = standard.run_main(d, *(list(argv) + ["--engine", engine]))
        out[engine] = (code, d)
    return out


def rows_of(d):
    paths = [p for p in glob.glob(str(d / "RUNS" / "*.jsonl"))
             if p.count(".") == 1 and not p.endswith("cache.jsonl")]
    assert len(paths) == 1, paths
    with open(paths[0], encoding="utf-8") as fh:
        return {r["id"]: r for r in (json.loads(line) for line in fh if line.strip())}


def test_a_deterministic_bank_releases_the_same_on_both_engines(tmp_path):
    runs = run_both(tmp_path, standard_bank)
    assert runs["trimwrit"][0] == 0 and runs["inspect"][0] == 0
    local, insp = release(runs["trimwrit"][1]), release(runs["inspect"][1])
    assert insp["engine"] == "inspect" and local["engine"] == "trimwrit"
    assert "engine_log" not in local
    assert insp["engine_log"].startswith(str(runs["inspect"][1] / "RUNS" / "inspect"))
    assert glob.glob(insp["engine_log"])
    assert set(local) | {"engine_log"} == set(insp)
    for key in ("passed", "n", "accuracy", "right", "splits", "repeats"):
        assert insp[key] == local[key], key
    strip = {k: v for k, v in local.items() if k not in ENGINE_ONLY}
    assert {k: v for k, v in insp.items() if k not in ENGINE_ONLY} == strip
    assert bank.envelope_problems(insp) == []
    assert bank.released(str(runs["inspect"][1])) is True


def test_a_model_bank_records_the_same_usage_on_both_engines(tmp_path):
    runs = run_both(tmp_path, model_bank, "--no-probe")
    assert runs["trimwrit"][0] == 0 and runs["inspect"][0] == 0
    local, insp = release(runs["trimwrit"][1]), release(runs["inspect"][1])
    assert insp["usage"]["calls"] == local["usage"]["calls"] == 40 + 8
    assert insp["usage"] == local["usage"]
    by_local, by_insp = rows_of(runs["trimwrit"][1]), rows_of(runs["inspect"][1])
    assert {i: r["usage"] for i, r in by_insp.items()} == \
        {i: r["usage"] for i, r in by_local.items()}


def test_an_answer_that_raises_gives_the_same_error_row_on_both_engines(tmp_path):
    runs = run_both(tmp_path, lambda p: standard_bank(p, run_body=RAISE_ON_C03))
    assert runs["trimwrit"][0] == runs["inspect"][0] == 0
    local, insp = rows_of(runs["trimwrit"][1])["c03"], rows_of(runs["inspect"][1])["c03"]
    assert local["got"] is None and local["right"] is False
    assert "no answer for c03" in local["error"]
    assert insp == local


def test_the_engine_keeps_the_order_and_names_a_missing_sample(tmp_path):
    def call(case):
        if case["id"] == "b":
            raise KeyError("b")
        return [case["id"]]

    cases = [{"id": "a"}, {"id": "b"}, {"id": 3}]
    out = bank_inspect.run(cases, call, 2, log_dir=str(tmp_path / "logs"))
    assert [c for c, _, _ in out] == cases
    assert out[0][1:] == (["a"], None) and out[2][1:] == ([3], None)
    assert out[1][1] is None and out[1][2].startswith("KeyError")
    assert bank_inspect.LAST_LOG.startswith(str(tmp_path / "logs"))
    assert bank_inspect.run([], call, 2) == []


# ------------------------------------------------- the hole tests, again, on engine inspect

ON_INSPECT = (
    "test_bar_moved_after_a_run_is_refused_until_a_dated_codebook_line",
    "test_a_grader_that_cannot_fail_is_refused",
    "test_labels_written_by_the_judged_model_are_refused",
    "test_a_bank_with_no_holdout_is_refused",
    "test_a_tiny_corpus_is_refused_unless_a_dated_waiver_holds",
    "test_a_bespoke_runner_is_refused",
    "test_a_corpus_edited_after_its_release_voids_the_release",
    "test_a_model_that_is_not_pinned_is_refused",
    "test_a_run_with_no_cost_record_writes_no_release",
    "test_one_run_cannot_decide_alone",
    "test_a_standard_bank_passes_clean",
)


def _on_inspect(name):
    def test(tmp_path, monkeypatch):
        plain = standard.run_main

        def on_inspect(d, *argv):
            return plain(d, *(list(argv) + ["--engine", "inspect"]))

        monkeypatch.setattr(standard, "run_main", on_inspect)
        getattr(standard, name)(tmp_path)
    test.__name__ = "test_on_inspect_" + name[len("test_"):]
    return test


for _name in ON_INSPECT:
    globals()["test_on_inspect_" + _name[len("test_"):]] = _on_inspect(_name)
