"""The run of a standard bank (trimwrit/bank_run.py, bank.main) on what test_bank_standard.py does
not pin: the cache, partial runs, stamp(), the engine seam, the probes, a raising answer, the
schema 2 release and its reading, the usage sink. No test calls a real model or the network."""
import contextlib
import io
import json
import os
import textwrap
import threading

import pytest

from trimwrit import bank

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTE = ('bank.note_usage({"model": model, "input_tokens": 10, "output_tokens": 5, '
        '"cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.001})')
PARITY = 'return "yes" if case["input"]["x"] % 2 else "no"'


def make_bank(tmp_path, *, model=None, effort=None, body=PARITY, extra="", n=40,
              name="run-bank"):
    """A standard bank of `n` parity cases; `body` is answer()'s body, `extra` top level code."""
    d = tmp_path / name
    d.mkdir()
    rows = [{"id": "c%02d" % i, "input": {"x": i}, "expected": "yes" if i % 2 else "no",
             "why": "parity of x"} for i in range(n)]
    (d / "CORPUS.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (d / "CODEBOOK.md").write_text("# run\n\n## 2. The bar\naccuracy >= 0.9\n\n## 3. Changes\n"
                                   "- 2026-11-01: laid out.\n")
    (d / "run.py").write_text(
        "import os, sys\nsys.path.insert(0, %r)\nfrom trimwrit import bank\n"
        "MODEL = %r\nEFFORT = %r\nBAR = {'accuracy': 0.9}\n"
        "LABEL_SOURCE = \"the door's own record\"\n"
        "def answer(case, model=MODEL, effort=EFFORT):\n%s\n"
        "def grade(case, got):\n    return got == case[\"expected\"]\n%s\n"
        "if __name__ == \"__main__\":\n    sys.exit(bank.main(__file__))\n"
        % (ROOT, model, effort, textwrap.indent(textwrap.dedent(body), "    "),
           textwrap.dedent(extra)))
    return d


def counting(log):
    """An answer body appending one line per call to `log` (outside the bank), then parity."""
    return "with open(%r, 'a') as fh:\n    fh.write(case['id'] + '\\n')\n%s" % (str(log), PARITY)


def calls(log):
    return len(log.read_text().splitlines()) if log.exists() else 0


def run(d, *argv, engine_impls=None):
    """(code, stdout, stderr, refusals) of bank.main on `d`."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = bank.main(str(d / "run.py"), argv=list(argv), engine_impls=engine_impls)
    refusals = []
    for line in err.getvalue().splitlines():
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict) and "refused" in doc:
            refusals.append(doc)
    return code, out.getvalue(), err.getvalue(), refusals


def release(d):
    return json.loads((d / "RELEASE.json").read_text())


def run_rows(d, run_name, ext=".jsonl"):
    with open(str(d / "RUNS" / (run_name + ext))) as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ------------------------------------------------------------------ the cache

def test_the_cache_answers_the_main_phase_and_never_the_repeats(tmp_path):
    log = tmp_path / "calls.log"
    d = make_bank(tmp_path, body=counting(log),
                  extra="def cache_key(case):\n    return [case['id'], case['input']]\n")
    code, _, _, refusals = run(d)
    assert code == 0 and refusals == []
    assert calls(log) == 40 + 8
    cache = d / "RUNS" / bank.CACHE_FILE
    assert len(cache.read_text().splitlines()) == 40
    with open(str(cache), "a") as fh:
        fh.write('{"key": ["torn"\n')
    log.unlink()
    code, _, err, _ = run(d)
    assert code == 0
    assert calls(log) == 8, "the main phase reads the cache, the repeats call afresh"
    assert "1 torn lines skipped" in err
    doc = release(d)
    assert doc["usage"]["cached"] == 40 and doc["usage"]["phases"]["main"]["cached"] == 40
    rows = run_rows(d, doc["run"])
    assert all(r["cached"] is True for r in rows)
    assert bank.latest_run(str(d)).endswith(doc["run"] + ".jsonl")


def test_no_cache_key_means_no_cache(tmp_path):
    log = tmp_path / "calls.log"
    d = make_bank(tmp_path, body=counting(log))
    run(d)
    run(d)
    assert calls(log) == 2 * (40 + 8)
    assert not (d / "RUNS" / bank.CACHE_FILE).exists()


# ------------------------------------------------------------------ partial runs, stamp

@pytest.mark.parametrize("argv", [("--ids", "c01,c02"), ("--split", "dev")])
def test_a_partial_run_grades_but_writes_no_release(tmp_path, argv):
    d = make_bank(tmp_path)
    code, out, _, refusals = run(d, *argv)
    assert code == 0 and refusals == []
    summary = json.loads(out.strip().splitlines()[-1])
    assert summary["partial"] is True and summary["passed"] is True
    assert summary["n"] == (2 if argv[0] == "--ids" else 30)
    assert not (d / "RELEASE.json").exists()


def test_a_stamp_key_reaches_the_release_and_a_colliding_one_raises(tmp_path):
    d = make_bank(tmp_path, extra="def stamp():\n    return {'prompt_sha': 'abc123'}\n")
    assert run(d)[0] == 0
    assert release(d)["prompt_sha"] == "abc123"
    two = tmp_path / "two"
    two.mkdir()
    d2 = make_bank(two, extra="def stamp():\n    return {'n': 3}\n")
    with pytest.raises(bank.BankError, match="stamp"):
        run(d2)
    assert not (d2 / "RELEASE.json").exists()


# ------------------------------------------------------------------ the engine seam

def test_an_engine_given_in_engine_impls_runs_the_cases_and_names_the_release(tmp_path):
    seen = []

    def fake(cases, call, jobs):
        # every call on its own thread: the attribution of usage must still hold per case
        out = {}

        def one(case):
            try:
                out[case["id"]] = (case, call(case), None)
            except Exception as exc:  # noqa: BLE001
                out[case["id"]] = (case, None, str(exc))
        threads = [threading.Thread(target=one, args=(c,)) for c in cases]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        seen.append(len(cases))
        return [out[c["id"]] for c in cases]

    d = make_bank(tmp_path, model="claude-sonnet-5-5", effort="medium",
                  body=NOTE + "\n" + PARITY)
    code, _, _, refusals = run(d, "--engine", "fake", engine_impls={"fake": fake})
    assert code == 0 and refusals == []
    assert seen == [40, 8], "the main phase and the repeat both went through the engine"
    doc = release(d)
    assert doc["engine"] == "fake"
    assert all(r["usage"]["calls"] == 1 for r in run_rows(d, doc["run"]))
    assert doc["usage"]["phases"]["main"]["calls"] == 40


def test_an_unknown_engine_exits_2(tmp_path):
    d = make_bank(tmp_path)
    code, _, err, _ = run(d, "--engine", "nope")
    assert code == 2 and "nope" in err
    assert not (d / "RELEASE.json").exists()


def test_the_inspect_engine_without_inspect_ai_names_the_install(tmp_path):
    try:
        import inspect_ai  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("inspect-ai is installed here")
    d = make_bank(tmp_path)
    code, _, err, _ = run(d, "--engine", "inspect")
    assert code == 2 and "pip install inspect-ai in a separate venv" in err


def test_run_local_keeps_the_order_and_catches_an_error():
    def call(case):
        if case["id"] == "b":
            raise RuntimeError("boom")
        return case["id"].upper()
    out = bank.run_local([{"id": "a"}, {"id": "b"}, {"id": "c"}], call, 3)
    assert [(c["id"], got, err) for c, got, err in out] == [
        ("a", "A", None), ("b", None, "RuntimeError: boom"), ("c", "C", None)]


# ------------------------------------------------------------------ the probes

def test_an_opus_bank_probes_sonnet_and_a_cheaper_effort_counted_in_the_probe_phase(tmp_path):
    d = make_bank(tmp_path, model="claude-opus-5-5", effort="high", body=NOTE + "\n" + PARITY)
    code, _, err, refusals = run(d)
    assert code == 0 and refusals == []
    doc = release(d)
    assert doc["downshift"]["model"] == "claude-sonnet-5-5" and doc["downshift"]["n"] == 20
    assert doc["downshift"]["effort"]["effort"] == "medium"
    phases = doc["usage"]["phases"]
    assert phases["main"]["calls"] == 40 and phases["repeats"]["calls"] == 8
    assert phases["probe"]["calls"] == 20 + 20
    assert doc["usage"]["calls"] == 40 + 8 + 40
    assert doc["usage"]["models"] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert len(run_rows(d, doc["run"], bank.PROBE_EXT)) == 20
    assert len(run_rows(d, doc["run"], bank.PROBE_EFFORT_EXT)) == 20
    assert "probe claude-sonnet-5-5 on 20 cases" in err
    code, _, _, _ = run(d, "--no-probe")
    assert code == 0 and "downshift" not in release(d)
    assert release(d)["usage"]["phases"]["probe"]["calls"] == 0


def test_a_model_bank_whose_answer_calls_another_model_is_refused(tmp_path):
    d = make_bank(tmp_path, model="claude-sonnet-5-5", effort="medium",
                  body=NOTE.replace('"model": model', '"model": "claude-haiku-4-5"') + "\n"
                  + PARITY)
    code, _, _, refusals = run(d)
    assert code == 65
    assert [r["rule"] for r in refusals] == ["model-not-pinned"]
    assert not (d / "RELEASE.json").exists()


# ------------------------------------------------------------------ rows, release, reading

def test_an_answer_that_raises_is_a_wrong_row_not_an_abort(tmp_path):
    d = make_bank(tmp_path, body='if case["id"] == "c03":\n    raise ValueError("no answer")\n'
                  + PARITY)
    code, _, _, refusals = run(d)
    assert code == 0 and refusals == []
    doc = release(d)
    assert doc["n"] == 40 and doc["right"] == 39 and doc["accuracy"] == 0.975
    bad = [r for r in run_rows(d, doc["run"]) if r["id"] == "c03"][0]
    assert bad["right"] is False and bad["got"] is None
    assert "ValueError: no answer" in bad["error"]


def test_a_schema_2_release_is_voided_by_changed_inputs_and_restored_by_a_rerun(tmp_path):
    d = make_bank(tmp_path)
    assert run(d)[0] == 0
    assert bank.released(str(d)) is True
    edited = (d / "CORPUS.jsonl").read_text().replace('"why": "parity of x"}',
                                                     '"why": "x is odd or even"}', 1)
    (d / "CORPUS.jsonl").write_text(edited)
    assert bank.released(str(d)) is False
    code, _, _, refusals = run(d)
    assert code == 0 and refusals == []
    assert bank.released(str(d)) is True
    assert release(d)["inputs_sha"] == bank.inputs_sha(str(d))


def test_a_schema_1_release_is_read_as_before(tmp_path):
    d = tmp_path / "old-bank"
    d.mkdir()
    (d / "CORPUS.jsonl").write_text('{"id": "c1", "expected": "PASS"}\n')
    doc = bank.write_release(str(d), passed=True, n=1, bar={"accuracy": 0.9}, model="opus")
    assert doc["schema"] == 1 and bank.SCHEMA == 1
    assert bank.envelope_problems(doc) == []
    assert bank.released(str(d)) is True
    with open(str(d / "CORPUS.jsonl"), "a") as fh:
        fh.write('{"id": "c2", "expected": "FAIL"}\n')
    assert bank.released(str(d)) is True, "a schema 1 release carries no inputs_sha to recheck"


def test_a_schema_1_release_with_the_same_bar_dates_the_bar_from_it(tmp_path):
    d = make_bank(tmp_path)
    bank.write_release(str(d), passed=True, n=40, bar={"accuracy": 0.9}, model=None,
                       at="2026-10-02T08:00:00+00:00")
    assert run(d)[0] == 0
    assert release(d)["bar_since"] == "2026-10-02"
    two = tmp_path / "two"
    two.mkdir()
    d2 = make_bank(two)
    bank.write_release(str(d2), passed=True, n=40, bar={"accuracy": 0.8}, model=None,
                       at="2026-10-02T08:00:00+00:00")
    code, _, _, refusals = run(d2)
    assert code == 65 and [r["rule"] for r in refusals] == ["bar-moved"]
    three = tmp_path / "three"
    three.mkdir()
    d3 = make_bank(three)
    bank.write_release(str(d3), passed=True, n=40, bar="most cases right", model=None,
                       at="2026-10-02T08:00:00+00:00")
    assert run(d3)[0] == 0, "a schema 1 bar written as a sentence locks nothing"


@pytest.mark.parametrize("key", ["usage", "repeats", "inputs_sha", "effort"])
def test_a_schema_2_release_missing_a_key_breaks_the_envelope(tmp_path, key):
    d = make_bank(tmp_path)
    assert run(d)[0] == 0
    doc = release(d)
    assert bank.envelope_problems(doc) == []
    del doc[key]
    assert "no `{}`".format(key) in bank.envelope_problems(doc)


def test_a_schema_2_release_is_typed(tmp_path):
    d = make_bank(tmp_path)
    assert run(d)[0] == 0
    doc = release(d)
    for key, value, word in (("model", "opus", "full model id"),
                             ("effort", "high", "effort"),
                             ("inputs_sha", "xyz", "inputs_sha"),
                             ("repeats", {"k": 0, "sample": 8, "flips": 0, "ids": [],
                                          "rates": []}, "repeats.k")):
        bad = dict(doc, **{key: value})
        assert any(word in p for p in bank.envelope_problems(bad)), key
    bad = dict(doc, usage=dict(doc["usage"], calls="many"))
    assert any("usage.calls" in p for p in bank.envelope_problems(bad))


def test_a_gate_false_fails_the_release_and_is_written(tmp_path):
    d = make_bank(tmp_path, extra=(
        "def score(rows):\n"
        "    right = sum(1 for r in rows if r['right'])\n"
        "    return {'accuracy': round(right / len(rows), 4), 'right': right,\n"
        "            'gates': {'x': False}}\n"))
    code, out, _, refusals = run(d)
    assert code == 1 and refusals == []
    doc = release(d)
    assert doc["passed"] is False and doc["gates"] == {"x": False}
    assert doc["accuracy"] == 1.0
    assert json.loads(out.strip().splitlines()[-1]) == {"passed": False, "n": 40,
                                                        "accuracy": 1.0}
    assert bank.envelope_problems(doc) == []


def test_two_metrics_without_score_are_refused_before_any_call(tmp_path):
    log = tmp_path / "calls.log"
    d = make_bank(tmp_path, body=counting(log))
    text = (d / "run.py").read_text().replace("BAR = {'accuracy': 0.9}",
                                             "BAR = {'accuracy': 0.9, 'recall': 0.8}")
    (d / "run.py").write_text(text)
    (d / "CODEBOOK.md").write_text((d / "CODEBOOK.md").read_text()
                                   .replace("accuracy >= 0.9\n",
                                            "accuracy >= 0.9\nrecall >= 0.8\n"))
    code, _, _, refusals = run(d)
    assert code == 65 and [r["rule"] for r in refusals] == ["bar-moved"]
    assert "define score(rows)" in refusals[0]["what"]
    assert calls(log) == 0


def test_the_json_flag_prints_the_release_and_the_splits_are_scored(tmp_path):
    d = make_bank(tmp_path)
    code, out, _, _ = run(d, "--json")
    assert code == 0
    printed = json.loads(out.strip().splitlines()[-1])
    assert printed == release(d)
    assert printed["splits"]["holdout"]["n"] == 10 and printed["splits"]["dev"]["n"] == 30
    assert printed["schema"] == bank.SCHEMA_STANDARD and printed["standard"] == bank.STANDARD
    assert isinstance(printed["wall_s"], float)


def test_self_check_prints_ok_and_calls_nothing(tmp_path):
    log = tmp_path / "calls.log"
    d = make_bank(tmp_path, body=counting(log))
    for flag in ("--self-check", "--offline"):
        code, out, _, _ = run(d, flag)
        assert code == 0 and json.loads(out) == {"self_check": "ok"}
    assert calls(log) == 0
    assert not (d / "RELEASE.json").exists() and not (d / "RUNS").exists()


# ------------------------------------------------------------------ the usage sink

def test_note_usage_reads_the_claude_envelope_and_never_raises():
    bank.note_usage({"model": "x"})  # outside any scope: a no-op
    with bank.usage_scope() as acc:
        bank.note_usage({"total_cost_usd": 0.02, "modelUsage": {
            "claude-sonnet-5-5": {"inputTokens": 100, "outputTokens": 20,
                                  "cacheReadInputTokens": 7, "cacheCreationInputTokens": 3,
                                  "costUSD": 0.02}}})
        bank.note_usage("not an envelope")
        bank.note_usage({"model": "claude-sonnet-5-5", "input_tokens": "lots"})
    assert acc["calls"] == 3
    assert (acc["input_tokens"], acc["output_tokens"]) == (100, 20)
    assert (acc["cache_read_tokens"], acc["cache_write_tokens"]) == (7, 3)
    assert acc["cost_usd"] == pytest.approx(0.02)
    assert acc["models"] == {"claude-sonnet-5-5"}
