"""`trimwrit report`: the whole life of one harness from its own ledgers, counts and dates only."""
import json

import pytest

from trimwrit import report
from trimwrit.cli import main

SENTINEL = "SENTINEL-PRIVATE"


def _write(path, rows, raw=None):
    lines = [json.dumps(r) for r in rows]
    if raw:
        lines.extend(raw)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _corr(cid, at="2026-08-24T10:00:00Z", **kw):
    row = {"kind": "correction", "id": cid, "at": at, "text": "t", "tag": None,
           "consequence": None, "session": None, "source": "manual", "case": None,
           "rule": None, "promoted": None}
    row.update(kw)
    return row


def _amend(cid, field, value, at="2026-08-25T10:00:00Z"):
    return {"kind": "amend", "id": cid, "at": at, "field": field, "value": value}


def test_counts_fold_amends(tmp_path):
    p = _write(tmp_path / "l.jsonl", [
        _corr("0001"), _corr("0002", tag="a"), _corr("0003", tag="a"),
        _amend("0001", "tag", "b"), _amend("0001", "case", "0001"),
        _amend("0002", "promoted", "rule-1"),
    ])
    c = report.build(p)["corrections"]
    assert c["count"] == 3
    assert c["tagged"] == 3
    assert c["distinct_tags"] == 2
    assert c["cased"] == 1
    assert c["promoted"] == 1
    assert c["malformed"] == 0


def test_first_last_in_utc(tmp_path):
    p = _write(tmp_path / "l.jsonl", [
        _corr("0001", at="2026-08-24T17:50:46-0500"),
        _corr("0002", at="2026-08-24T23:00:00+0200"),
        _corr("0003", at="2026-08-24T22:00:00.753Z"),
    ])
    c = report.build(p)["corrections"]
    assert c["first_at"] == "2026-08-24T21:00:00Z"
    assert c["last_at"] == "2026-08-24T22:50:46Z"


def test_never_emits_free_text(tmp_path):
    led = _write(tmp_path / "l.jsonl", [
        _corr("0001", text=SENTINEL, consequence=SENTINEL, session=SENTINEL, tag="t"),
        _amend("0001", "consequence", SENTINEL),
    ])
    ref = _write(tmp_path / "r.jsonl", [
        {"at": "2026-09-30T14:59:45Z", "door": "presence_gate", "event": "Stop",
         "reason": SENTINEL, "reason_class": "x", "session_id": SENTINEL,
         "subject_hash": SENTINEL, "took_effect": True},
    ])
    his = _write(tmp_path / "h.jsonl", [
        {"ts": "2026-09-02T01:25:55Z", "case": "0099-" + SENTINEL + "-words", "with": 1.0, "without": 0.0,
         "delta": 1.0, "runs": 3, "unmeasured": {"with": 0, "without": 0},
         "target": [SENTINEL], "claude": SENTINEL},
    ])
    doc = report.build(led, ref, his)
    assert SENTINEL not in report.render(doc)
    assert SENTINEL not in json.dumps(doc)
    assert doc["refusals"]["count"] == 1
    assert [e["case"] for e in doc["cases"]["latest"]] == ["0099"]
    assert "0099" in report.render(doc)


def test_case_shown_by_number_only(tmp_path):
    his = _write(tmp_path / "h.jsonl", [
        {"ts": "2026-09-01T00:00:00Z", "case": "0064-some-words-of-the-correction", "with": 1.0,
         "without": 0.0, "delta": 1.0, "runs": 3},
        {"ts": "2026-09-01T00:00:00Z", "case": "no-number-here", "with": 1.0,
         "without": 0.5, "delta": 0.5, "runs": 3},
    ])
    doc = report.build(_write(tmp_path / "l.jsonl", []), history_path=his)
    assert [e["case"] for e in doc["cases"]["latest"]] == ["0064", "(unnamed)"]
    assert "some-words" not in report.render(doc) + json.dumps(doc)


def test_malformed_lines_counted(tmp_path):
    led = _write(tmp_path / "l.jsonl", [_corr("0001")], raw=["", "{not json", "[1, 2]"])
    ref = _write(tmp_path / "r.jsonl", [{"at": "2026-09-30T14:59:45Z", "door": "d"}],
                 raw=["", "garbage"])
    his = _write(tmp_path / "h.jsonl", [], raw=["", "nope"])
    for path in (led, ref, his):
        with open(path, "ab") as fh:
            fh.write(b"\xff\xfe\n")
    doc = report.build(led, ref, his)
    assert doc["corrections"]["count"] == 1
    assert doc["corrections"]["malformed"] == 4
    assert doc["refusals"]["count"] == 1
    assert doc["refusals"]["malformed"] == 3
    assert doc["cases"]["malformed"] == 3
    assert report.render(doc)


def test_unmeasured_history_rows_apart(tmp_path):
    his = _write(tmp_path / "h.jsonl", [
        {"ts": "2026-09-01T00:00:00Z", "case": "0001", "with": 1.0, "without": 0.0, "delta": 1.0,
         "runs": 3, "unmeasured": {"with": 0, "without": 0}},
        {"ts": "2026-09-02T00:00:00Z", "case": "0002", "with": None, "without": 0.0, "delta": None,
         "runs": 3, "unmeasured": {"with": 3, "without": 0}},
        {"ts": "2026-09-03T00:00:00Z", "case": "0003", "with": 1.0, "without": 0.0, "delta": 1.0,
         "runs": 3, "unmeasured": True},
        {"ts": "2026-09-04T00:00:00Z", "case": "0004", "with": 0.5, "without": 0.0, "delta": 0.5,
         "runs": 3},
        {"ts": "2026-09-05T00:00:00Z", "case": "0005", "with": 1.0, "without": 0.0, "delta": 1.0,
         "runs": 3, "unmeasured": "yes"},
    ])
    led = _write(tmp_path / "l.jsonl", [])
    cases = report.build(led, history_path=his)["cases"]
    assert cases["measured"] == 2
    assert cases["unmeasured"] == 3
    assert [r["case"] for r in cases["latest"]] == ["0001", "0004"]
    assert [r["delta"] for r in cases["latest"]] == [1.0, 0.5]


def test_latest_keeps_last_row_per_case_by_parsed_ts(tmp_path):
    his = _write(tmp_path / "h.jsonl", [
        {"ts": "2026-09-02T01:00:00+0200", "case": "a", "with": 1.0, "without": 0.0,
         "delta": 1.0, "runs": 3},
        {"ts": "2026-09-01T23:30:00Z", "case": "a", "with": 0.0, "without": 0.0,
         "delta": 0.0, "runs": 3},
    ])
    led = _write(tmp_path / "l.jsonl", [])
    latest = report.build(led, history_path=his)["cases"]["latest"]
    assert len(latest) == 1
    assert latest[0]["delta"] == 0.0
    assert latest[0]["ts"] == "2026-09-01T23:30:00Z"


def test_refusals_by_door_and_took_effect(tmp_path):
    led = _write(tmp_path / "l.jsonl", [])
    ref = _write(tmp_path / "r.jsonl", [
        {"at": "2026-09-30T15:00:00Z", "door": "b", "took_effect": True},
        {"at": "2026-09-30T15:01:00Z", "door": "a", "took_effect": None},
        {"at": "2026-09-30T15:02:00Z", "door": "b", "took_effect": False},
        {"at": "2026-09-30T15:03:00Z", "door": "c", "took_effect": True},
    ])
    r = report.build(led, ref)["refusals"]
    assert r["count"] == 4
    assert r["took_effect"] == 2
    assert r["by_door"] == [["b", 2], ["a", 1], ["c", 1]]


def test_missing_explicit_path_exit_2(tmp_path, capsys):
    led = _write(tmp_path / "l.jsonl", [_corr("0001")])
    missing = str(tmp_path / "nope.jsonl")
    for flag in ("--refusals", "--history", "--ledger"):
        for bad in (missing, ""):
            args = ["report", "--ledger", led]
            if flag == "--ledger":
                args = ["report", "--ledger", bad]
            else:
                args += [flag, bad]
            assert main(args) == 2
            err = capsys.readouterr().err
            assert "refused: cannot read" in err and bad in err


def test_omitted_inputs_are_null(tmp_path, monkeypatch, capsys):
    led = _write(tmp_path / "l.jsonl", [_corr("0001")])
    monkeypatch.chdir(tmp_path)
    doc = report.build(led)
    assert doc["refusals"] is None
    assert doc["cases"] is None
    assert main(["report", "--ledger", led, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["refusals"] is None and out["cases"] is None
    assert main(["report", "--ledger", led]) == 0
    assert "CORRECTIONS" in capsys.readouterr().out


def test_history_defaults_to_evals_results_when_present(tmp_path, monkeypatch, capsys):
    led = _write(tmp_path / "l.jsonl", [])
    (tmp_path / "evals" / "results").mkdir(parents=True)
    _write(tmp_path / "evals" / "results" / "history.jsonl", [
        {"ts": "2026-09-01T00:00:00Z", "case": "a", "with": 1.0, "without": 0.0, "delta": 1.0,
         "runs": 3}])
    monkeypatch.chdir(tmp_path)
    assert main(["report", "--ledger", led, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["cases"]["measured"] == 1


def test_empty_ledger_has_null_dates(tmp_path):
    p = _write(tmp_path / "l.jsonl", [])
    c = report.build(p)["corrections"]
    assert c["count"] == 0 and c["first_at"] is None and c["last_at"] is None


def test_unreadable_default_ledger_is_missing_ledger(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["report"]) == 2
    assert "refused: cannot read" in capsys.readouterr().err


def test_parse_ts_shapes():
    f = report.parse_ts
    assert f("2026-08-24T17:50:46-0500").isoformat() == "2026-08-24T22:50:46+00:00"
    assert f("2026-08-24T23:00:00+02:00").isoformat() == "2026-08-24T21:00:00+00:00"
    assert f("2026-08-24T22:00:00.753Z").isoformat() == "2026-08-24T22:00:00.753000+00:00"
    assert f("2026-08-24T22:00:00Z").isoformat() == "2026-08-24T22:00:00+00:00"
    assert f("2026-08-24T22:00:00").isoformat() == "2026-08-24T22:00:00+00:00"
    assert f("2026-08-24T22:00:00+9999") is None
    assert f("2026-08-24T22:00:00-2400") is None
    assert f("junk") is None and f(None) is None and f(5) is None


def test_verdicts_counted_from_the_latest_decided_row_per_case_only(tmp_path):
    his = _write(tmp_path / "h.jsonl", [
        {"ts": "2026-10-01T00:00:00Z", "case": "0001-a", "with": 1.0, "without": 0.0,
         "delta": 1.0, "runs": 3, "verdict": "undecided"},
        {"ts": "2026-10-02T00:00:00Z", "case": "0001-a", "with": 1.0, "without": 0.0,
         "delta": 1.0, "runs": 3, "verdict": "keep"},
        {"ts": "2026-10-01T00:00:00Z", "case": "0002-b", "with": 1.0, "without": 1.0,
         "delta": 0.0, "runs": 3, "verdict": "inert"},
        # A later plain run carries no verdict: the case's latest verdict is still the one above.
        {"ts": "2026-10-03T00:00:00Z", "case": "0002-b", "with": 1.0, "without": 1.0,
         "delta": 0.0, "runs": 3},
        # A batch with no measured run still carries the cumulative verdict.
        {"ts": "2026-10-01T00:00:00Z", "case": "0003-c", "with": None, "without": None,
         "delta": None, "runs": 3, "verdict": "undecided"},
        {"ts": "2026-10-01T00:00:00Z", "case": "0004-d", "with": 1.0, "without": 0.0,
         "delta": 1.0, "runs": 3},
        {"ts": "2026-10-01T00:00:00Z", "case": "0005-e", "with": 1.0, "without": 0.0,
         "delta": 1.0, "runs": 3, "verdict": SENTINEL},
    ])
    doc = report.build(_write(tmp_path / "l.jsonl", []), history_path=his)
    assert doc["cases"]["verdicts"] == {"keep": 1, "inert": 1, "undecided": 1}
    text = report.render(doc)
    assert "verdicts (latest per case): keep 1, inert 1, undecided 1" in text
    assert SENTINEL not in text + json.dumps(doc)


def test_verdicts_line_with_no_decided_row(tmp_path):
    his = _write(tmp_path / "h.jsonl", [
        {"ts": "2026-10-01T00:00:00Z", "case": "0004-d", "with": 1.0, "without": 0.0,
         "delta": 1.0, "runs": 3}])
    doc = report.build(_write(tmp_path / "l.jsonl", []), history_path=his)
    assert doc["cases"]["verdicts"] == {}
    assert "verdicts (latest per case): none yet, `trimwrit run --decide` writes them" in \
        report.render(doc)


def test_a_snapshot_with_verdicts_passes_the_privacy_scanner(tmp_path):
    """`docs/production/` holds `report --json` snapshots; the verdict counts must not give the
    scanner anything to refuse (no free-text key, no case slug)."""
    import importlib.util
    import os
    import subprocess

    his = _write(tmp_path / "h.jsonl", [
        {"ts": "2026-10-01T00:00:00Z", "case": "0001-some-private-words", "with": 1.0,
         "without": 1.0, "delta": 0.0, "runs": 3, "verdict": "inert"}])
    doc = report.build(_write(tmp_path / "l.jsonl", []), history_path=his)
    repo = tmp_path / "repo"
    (repo / "docs" / "production").mkdir(parents=True)
    (repo / "docs" / "production" / "snap.json").write_text(json.dumps(doc, indent=2),
                                                            encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "no_private_data", os.path.join(root, "tools", "no-private-data.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert doc["cases"]["verdicts"] == {"inert": 1}
    assert mod.scan(str(repo)) == []


def _keyed(ts, key, passes, measured, attempted=None, **kw):
    attempted = attempted or measured
    row = {"ts": ts, "case": "0007-some-case", "key": key,
           "with": passes[0] / float(measured[0]) if measured[0] else None,
           "without": passes[1] / float(measured[1]) if measured[1] else None,
           "runs": attempted[0],
           "passes": {"with": passes[0], "without": passes[1]},
           "measured": {"with": measured[0], "without": measured[1]},
           "attempted": {"with": attempted[0], "without": attempted[1]},
           "unmeasured": {"with": attempted[0] - measured[0],
                          "without": attempted[1] - measured[1]}}
    row["delta"] = (row["with"] - row["without"]
                    if row["with"] is not None and row["without"] is not None else None)
    row.update(kw)
    return row


def test_a_case_s_figures_are_the_sum_of_its_current_key(tmp_path):
    # Three rounds of `run --decide` under one key, after an older row under another key (the
    # rule before an edit). The figures are the sum of the three, the verdict the one decided on
    # that sum, never the last round alone.
    his = _write(tmp_path / "h.jsonl", [
        _keyed("2026-10-01T00:00:00Z", "old", (0, 3), (3, 3)),
        _keyed("2026-10-14T00:00:00Z", "new", (3, 0), (3, 3), verdict="undecided"),
        _keyed("2026-10-14T00:01:00Z", "new", (3, 0), (3, 3), verdict="keep"),
        _keyed("2026-10-21T00:00:00Z", "new", (2, 1), (3, 3), verdict="keep"),
    ])
    doc = report.build(_write(tmp_path / "l.jsonl", []), history_path=his)
    entry, = doc["cases"]["latest"]
    assert entry["case"] == "0007"
    assert entry["passes"] == {"with": 8, "without": 1}
    assert entry["measured"] == {"with": 9, "without": 9}
    assert entry["attempted"] == {"with": 9, "without": 9}
    assert entry["runs"] == 9
    assert entry["with"] == pytest.approx(8 / 9.0) and entry["without"] == pytest.approx(1 / 9.0)
    assert entry["delta"] == pytest.approx(7 / 9.0)
    assert entry["verdict"] == "keep"
    assert entry["ts"] == "2026-10-21T00:00:00Z"
    assert doc["cases"]["verdicts"] == {"keep": 1}
    text = report.render(doc)
    assert "0007" in text and "0.89" in text and "0.11" in text
    assert "verdicts (latest per case): keep 1" in text


def test_the_verdict_is_decided_on_the_sum_not_copied_from_the_last_row(tmp_path):
    # The last row says keep, but the sum (3/3 with, 3/3 without) is not a keep.
    his = _write(tmp_path / "h.jsonl", [
        _keyed("2026-10-14T00:00:00Z", "k", (2, 3), (2, 3), verdict="undecided"),
        _keyed("2026-10-21T00:00:00Z", "k", (1, 0), (1, 0), verdict="keep"),
    ])
    doc = report.build(_write(tmp_path / "l.jsonl", []), history_path=his)
    assert doc["cases"]["verdicts"] == {"undecided": 1}
    assert doc["cases"]["latest"][0]["verdict"] == "undecided"


def test_a_key_with_an_arm_never_measured_has_no_figures_but_keeps_its_verdict(tmp_path):
    his = _write(tmp_path / "h.jsonl", [
        _keyed("2026-10-21T00:00:00Z", "k", (0, 0), (0, 0), attempted=(3, 3),
               verdict="undecided", stopped="unmeasured")])
    doc = report.build(_write(tmp_path / "l.jsonl", []), history_path=his)
    assert doc["cases"]["latest"] == []
    assert doc["cases"]["verdicts"] == {"undecided": 1}
    assert report.render(doc)
