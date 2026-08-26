import json

from trimwrit import stats
from trimwrit.cli import main

EM = chr(0x2014)


def _write(path, lines):
    path.write_text("\n".join(json.dumps(row) for row in lines) + "\n", encoding="utf-8")


def _flat(rule, status="PASS", margin=10, surface=None):
    row = {"rule": rule, "status": status, "margin": margin}
    if surface is not None:
        row["surface"] = surface
    return row


def test_a_rule_that_fires_15x_clean_with_wide_margins_is_dead_weight(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write(ledger, [_flat("no-time-estimate", margin=10) for _ in range(15)])

    result = stats.compute(str(ledger))
    assert [r["rule"] for r in result["dead_weight"]] == ["no-time-estimate"]
    row = result["rules"][0]
    assert row["fires"] == 15 and row["fail"] == 0 and row["close_calls"] == 0


def test_one_close_call_clears_the_dead_weight_flag(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    lines = [_flat("no-time-estimate", margin=10) for _ in range(14)]
    lines.append(_flat("no-time-estimate", margin=2))  # <= the default close margin of 2
    _write(ledger, lines)

    result = stats.compute(str(ledger))
    row = result["rules"][0]
    assert row["fires"] == 15
    assert row["close_calls"] == 1
    assert row["dead_weight"] is False
    assert result["dead_weight"] == []


def test_fourteen_fires_is_under_the_dead_weight_floor(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write(ledger, [_flat("no-time-estimate", margin=10) for _ in range(14)])

    result = stats.compute(str(ledger))
    row = result["rules"][0]
    assert row["fires"] == 14
    assert row["dead_weight"] is False
    assert result["dead_weight"] == []


def test_three_of_nine_decided_is_friction_with_fail_rate_0_333(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    lines = [_flat("no-em-dash", status="FAIL", margin=-1) for _ in range(3)]
    lines += [_flat("no-em-dash", status="PASS", margin=5) for _ in range(6)]
    _write(ledger, lines)

    result = stats.compute(str(ledger))
    row = result["rules"][0]
    assert row["decided"] == 9
    assert row["fail_rate"] == 0.333
    assert row["friction"] is True
    assert [r["rule"] for r in result["friction"]] == ["no-em-dash"]


def test_skips_are_excluded_from_decided_and_fail_rate(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    lines = [_flat("no-em-dash", status="FAIL", margin=-1) for _ in range(3)]
    lines += [_flat("no-em-dash", status="PASS", margin=5) for _ in range(6)]
    lines += [_flat("no-em-dash", status="SKIP", margin=None) for _ in range(5)]
    _write(ledger, lines)

    result = stats.compute(str(ledger))
    row = result["rules"][0]
    assert row["fires"] == 14
    assert row["skip"] == 5
    assert row["decided"] == 9
    assert row["fail_rate"] == 0.333


def test_nested_verdict_rows_with_surfaces_aggregate_by_surface_and_gate(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    nested = {
        "surface": "linkedin",
        "gates": [
            {"gate": "no-em-dash", "status": "PASS", "margin": 5},
            {"gate": "no-time-estimate", "status": "FAIL", "margin": -1},
        ],
    }
    flat = _flat("no-em-dash", status="PASS", margin=5)  # no surface: a different key
    _write(ledger, [nested, flat])

    result = stats.compute(str(ledger))
    assert result["total_rows"] == 2
    assert result["malformed"] == 0
    keys = {(r["surface"], r["rule"]) for r in result["rules"]}
    assert keys == {
        ("linkedin", "no-em-dash"),
        ("linkedin", "no-time-estimate"),
        ("", "no-em-dash"),
    }
    assert result["surfaces"] == {"linkedin": 2}


def test_malformed_lines_are_skipped_with_a_count_not_a_crash(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        "\n".join([
            "",  # blank
            "{not json",  # not JSON
            json.dumps({"nothing": "recognisable"}),  # valid JSON, neither known shape
            json.dumps(_flat("no-em-dash", status="PASS", margin=5)),
        ]) + "\n",
        encoding="utf-8",
    )

    result = stats.compute(str(ledger))
    assert result["total_rows"] == 1
    assert result["malformed"] == 3
    assert len(result["rules"]) == 1


def _run(*argv):
    return main(list(argv))


def test_cli_stats_runs_on_a_tmp_ledger_and_json_round_trips(tmp_path, capsys):
    ledger = tmp_path / "ledger.jsonl"
    lines = [_flat("no-time-estimate", margin=10) for _ in range(15)]
    lines += [_flat("no-em-dash", status="FAIL", margin=-1) for _ in range(3)]
    lines += [_flat("no-em-dash", status="PASS", margin=5) for _ in range(6)]
    _write(ledger, lines)

    assert _run("stats", str(ledger)) == 0
    out = capsys.readouterr().out
    assert "DEAD WEIGHT" in out and "no-time-estimate" in out
    assert "FRICTION" in out and "no-em-dash" in out
    assert EM not in out

    assert _run("stats", str(ledger), "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_rows"] == 24
    assert {r["rule"] for r in payload["dead_weight"]} == {"no-time-estimate"}
    assert {r["rule"] for r in payload["friction"]} == {"no-em-dash"}


def test_cli_stats_thresholds_are_overridable(tmp_path, capsys):
    ledger = tmp_path / "ledger.jsonl"
    _write(ledger, [_flat("no-time-estimate", margin=10) for _ in range(5)])

    assert _run("stats", str(ledger), "--min-fires", "5", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["thresholds"]["min_fires"] == 5
    assert {r["rule"] for r in payload["dead_weight"]} == {"no-time-estimate"}
