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
        {"ts": "2026-09-02T01:25:55Z", "case": "0020-x", "with": 1.0, "without": 0.0,
         "delta": 1.0, "runs": 3, "unmeasured": {"with": 0, "without": 0},
         "target": [SENTINEL], "claude": SENTINEL},
    ])
    doc = report.build(led, ref, his)
    assert SENTINEL not in report.render(doc)
    assert SENTINEL not in json.dumps(doc)
    assert doc["refusals"]["count"] == 1


def test_malformed_lines_counted(tmp_path):
    led = _write(tmp_path / "l.jsonl", [_corr("0001")], raw=["", "{not json", "[1, 2]"])
    ref = _write(tmp_path / "r.jsonl", [{"at": "2026-09-30T14:59:45Z", "door": "d"}],
                 raw=["", "garbage"])
    his = _write(tmp_path / "h.jsonl", [], raw=["", "nope"])
    doc = report.build(led, ref, his)
    assert doc["corrections"]["count"] == 1
    assert doc["corrections"]["malformed"] == 3
    assert doc["refusals"]["count"] == 1
    assert doc["refusals"]["malformed"] == 2
    assert doc["cases"]["malformed"] == 2
    assert report.render(doc)


def test_unmeasured_history_rows_apart(tmp_path):
    his = _write(tmp_path / "h.jsonl", [
        {"ts": "2026-09-01T00:00:00Z", "case": "a", "with": 1.0, "without": 0.0, "delta": 1.0,
         "runs": 3, "unmeasured": {"with": 0, "without": 0}},
        {"ts": "2026-09-02T00:00:00Z", "case": "b", "with": None, "without": 0.0, "delta": None,
         "runs": 3, "unmeasured": {"with": 3, "without": 0}},
        {"ts": "2026-09-03T00:00:00Z", "case": "c", "with": 1.0, "without": 0.0, "delta": 1.0,
         "runs": 3, "unmeasured": True},
        {"ts": "2026-09-04T00:00:00Z", "case": "d", "with": 0.5, "without": 0.0, "delta": 0.5,
         "runs": 3},
    ])
    led = _write(tmp_path / "l.jsonl", [])
    cases = report.build(led, history_path=his)["cases"]
    assert cases["measured"] == 2
    assert cases["unmeasured"] == 2
    assert [r["case"] for r in cases["latest"]] == ["a", "d"]
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
        args = ["report", "--ledger", led]
        if flag == "--ledger":
            args = ["report", "--ledger", missing]
        else:
            args += [flag, missing]
        assert main(args) == 2
        err = capsys.readouterr().err
        assert "refused: cannot read" in err and missing in err


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
    assert f("junk") is None and f(None) is None and f(5) is None
