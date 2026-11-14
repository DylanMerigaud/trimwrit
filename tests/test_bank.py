"""trimwrit/bank.py and `trimwrit bank`: the kit of an eval bank (cases, runs, the RELEASE
envelope) and the scaffold that lays one out, end to end."""
import datetime
import json
import os
import subprocess
import sys

import pytest

from trimwrit import bank
from trimwrit.cli import main

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHES = (chr(0x2014), chr(0x2013))


def _bank(tmp_path, cases=({"id": "c1", "expected": "PASS"},)):
    d = tmp_path / "x-bank"
    d.mkdir()
    (d / "CORPUS.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cases) + "\n")
    (d / "CODEBOOK.md").write_text("# x\n## 2. The bar\naccuracy >= 0.9\n")
    return str(d)


# ------------------------------------------------------------------ the kit

def test_load_reads_objects_and_names_a_bad_line(tmp_path):
    b = _bank(tmp_path, [{"id": "a"}, {"id": "b"}])
    assert [r["id"] for r in bank.corpus(b)] == ["a", "b"]
    p = tmp_path / "bad.jsonl"
    p.write_text('{"id": "a"}\n\n[1, 2]\n')
    with pytest.raises(bank.BankError, match=r"bad.jsonl:3: a list"):
        bank.load(str(p))
    p.write_text('{"id": "a"}\n{oops\n')
    with pytest.raises(bank.BankError, match=r"bad.jsonl:2: not JSON"):
        bank.load(str(p))


def test_sha_reads_a_file_or_a_text(tmp_path):
    p = tmp_path / "prompt.txt"
    p.write_text("grade this")
    assert bank.sha(str(p)) == bank.sha("grade this")
    assert len(bank.sha("a", "b")) == 12 and bank.sha("a", "b") != bank.sha("ab")


def test_bank_sha_follows_the_inputs_and_ignores_the_outputs(tmp_path):
    b = _bank(tmp_path)
    before = bank.bank_sha(b)
    open(bank.run_path(b, "20261103T000000"), "w").close()
    with open(os.path.join(b, "RELEASE.json"), "w") as fh:
        fh.write("{}\n")
    assert bank.bank_sha(b) == before
    with open(os.path.join(b, "CORPUS.jsonl"), "a") as fh:
        fh.write('{"id": "c2"}\n')
    assert bank.bank_sha(b) != before


def test_runs_are_named_by_stamp_and_the_newest_is_found(tmp_path):
    b = _bank(tmp_path)
    assert bank.latest_run(b) is None
    t = datetime.datetime(2026, 11, 3, 9, 15, tzinfo=datetime.timezone.utc)
    old = bank.run_path(b, bank.run_stamp(t))
    new = bank.run_path(b, bank.run_stamp(t + datetime.timedelta(hours=1)), ".json")
    for p in (old, new):
        open(p, "w").close()
    assert os.path.basename(old) == "20261103T091500.jsonl"
    assert bank.latest_run(b) == new and bank.latest_run(b, "*.jsonl") == old


def test_write_release_carries_the_envelope_then_the_bank_keys(tmp_path):
    b = _bank(tmp_path)
    doc = bank.write_release(b, passed=True, n=1, bar={"accuracy": 0.9}, model="m-1",
                             run="20261103T091500",
                             extra={"accuracy": 1.0, "verbatim": "a" + DASHES[0] + "b"})
    assert bank.read_release(b) == doc and bank.envelope_problems(doc) == []
    assert doc["schema"] == 1 and doc["bank_sha"] == bank.bank_sha(b)
    assert bank.released(b) is True
    raw = open(os.path.join(b, "RELEASE.json"), encoding="utf-8").read()
    assert not any(ch in raw for ch in DASHES) and "\\u2014" in raw
    assert not [f for f in os.listdir(b) if f.startswith(".RELEASE.")]


def test_a_failed_bar_is_not_released(tmp_path):
    b = _bank(tmp_path)
    bank.write_release(b, passed=False, n=1, bar="accuracy >= 0.9", model=None)
    assert bank.released(b) is False


@pytest.mark.parametrize("kw,match", [
    ({"extra": {"passed": True}}, "overwrite the envelope"),
    ({"passed": "yes"}, "passed"),
    ({"n": -1}, "count"),
    ({"bar": ""}, "bar"),
    ({"model": 3}, "model"),
    ({"at": "yesterday"}, "ISO"),
])
def test_a_broken_release_is_refused_and_nothing_is_written(tmp_path, kw, match):
    b = _bank(tmp_path)
    args = dict(passed=True, n=1, bar="accuracy >= 0.9", model=None)
    args.update(kw)
    with pytest.raises(bank.BankError, match=match):
        bank.write_release(b, **args)
    assert bank.read_release(b) is None


def test_a_release_without_the_envelope_is_never_released(tmp_path):
    b = _bank(tmp_path)
    with open(os.path.join(b, "RELEASE.json"), "w") as fh:
        json.dump({"passed": True, "at": "2026-11-02T00:00:00+00:00"}, fh)
    assert "no `schema`" in bank.envelope_problems(bank.read_release(b))
    assert bank.released(b) is False


# ------------------------------------------------------------------ the scaffold

NEW = ["bank", "new", "ticket-route", "--judges", "Does the router send a ticket to the right "
       "queue", "--bar", "accuracy >= 0.9", "--model", "claude-sonnet-5-5",
       "--producer", "router.py:route", "--label-source", "the support lead, by hand"]
NOTE = ('    bank.note_usage({"model": model, "input_tokens": 1, "output_tokens": 1, '
        '"cost_usd": 0.0})\n')
RAISE = '    raise NotImplementedError("call the producer on case[\'input\']")'
WAIVE = ('\nWAIVE = {{"corpus-too-small": {{"since": "{0}", "why": "two cases"}}, '
         '"no-holdout": {{"since": "{0}", "why": "two cases"}}}}\n')


def _py(args, cwd):
    env = dict(os.environ, PYTHONPATH=REPO)
    return subprocess.run([sys.executable] + args, cwd=cwd, capture_output=True, text=True,
                          timeout=120, env=env)


def _fill(b):
    """Two cases, and a dated WAIVE of the two size rules so the run of a 2 case bank holds."""
    cases = [{"id": "t1", "input": "refund please", "expected": "billing", "why": "row 12"},
             {"id": "t2", "input": "app crashes", "expected": "bugs", "why": "row 40"}]
    (b / "CORPUS.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cases))
    run_py = (b / "run.py").read_text()
    head, tail = run_py.split("\n\ndef answer", 1)
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    (b / "run.py").write_text(head + WAIVE.format(today) + "\n\ndef answer" + tail)


def _fill_40(b):
    cases = [{"id": "t%02d" % i, "input": "ticket %d" % i,
              "expected": "billing" if i % 3 else "bugs", "why": "row %d" % i}
             for i in range(40)]
    (b / "CORPUS.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cases))


def test_new_lays_out_the_four_files(tmp_path, capsys):
    assert main(["--root", str(tmp_path)] + NEW) == 0
    b = tmp_path / "banks" / "ticket-route"
    assert sorted(os.listdir(b)) == ["CODEBOOK.md", "CORPUS.jsonl", "run.py",
                                     "test_ticket_route_bank.py"]
    book = (b / "CODEBOOK.md").read_text()
    assert "## 2. The bar, declared" in book and "accuracy >= 0.9" in book
    assert "router.py:route" in book and "next: fill CODEBOOK.md" in capsys.readouterr().out
    assert os.access(b / "run.py", os.X_OK)


def test_an_empty_bank_fails_its_own_test_until_cases_are_written(tmp_path):
    assert main(["--root", str(tmp_path)] + NEW) == 0
    b = tmp_path / "banks" / "ticket-route"
    res = _py(["-m", "pytest", "-q", "-p", "no:cacheprovider", str(b)], tmp_path)
    assert res.returncode != 0 and "corpus-too-small" in res.stdout
    assert "0 cases, the standard needs 30" in res.stdout and "fix:" in res.stdout
    _fill_40(b)
    res = _py(["-m", "pytest", "-q", "-p", "no:cacheprovider", str(b)], tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr


def test_the_run_writes_its_run_file_and_an_enveloped_release(tmp_path):
    assert main(["--root", str(tmp_path)] + NEW) == 0
    b = tmp_path / "banks" / "ticket-route"
    _fill(b)
    off = _py([str(b / "run.py"), "--offline"], tmp_path)
    assert off.returncode == 0 and json.loads(off.stdout) == {"self_check": "ok"}, off.stderr
    assert not (b / "RELEASE.json").exists()
    run_py = (b / "run.py").read_text().replace(
        RAISE, NOTE + '    return {"refund please": "billing"}.get(case["input"], "general")')
    (b / "run.py").write_text(run_py)
    res = _py([str(b / "run.py")], tmp_path)
    assert res.returncode == 1, res.stderr          # 1 of 2 right is under the 0.9 bar
    rel = bank.read_release(str(b))
    assert bank.envelope_problems(rel) == [] and rel["schema"] == 2
    assert rel["passed"] is False and rel["n"] == 2 and rel["accuracy"] == 0.5
    assert rel["model"] == "claude-sonnet-5-5" and rel["bar"] == {"accuracy": 0.9}
    assert os.path.isfile(b / "RUNS" / (rel["run"] + ".jsonl"))
    assert main(["--root", str(tmp_path), "bank", "check"]) == 0


def test_a_repository_can_bring_its_own_import_of_the_kit(tmp_path):
    files = bank.plan("x", "Judge x", "accuracy >= 0.5", "evals/x-bank",
                      import_block="import bank  # noqa: E402",
                      path_block="sys.path.insert(0, '/kit')")
    run_py = files["evals/x-bank/run.py"]
    assert "import bank  # noqa: E402" in run_py and "sys.path.insert(0, '/kit')" in run_py
    assert "from trimwrit import bank" not in run_py
    with pytest.raises(bank.BankError, match="bind the name"):
        bank.plan("x", "Judge x", "accuracy >= 0.5", "evals/x-bank", import_block="import kit")


def test_new_never_overwrites_and_refuses_a_bad_slug_bar_or_model(tmp_path, capsys):
    assert main(["--root", str(tmp_path)] + NEW) == 0
    assert main(["--root", str(tmp_path)] + NEW) == 2
    assert "exists: nothing written" in capsys.readouterr().err
    for i, bad in ((2, "Bad_Slug"), (6, "accuracy > 0.9"), (8, "bad model id")):
        argv = list(NEW)
        argv[i] = bad
        assert main(["--root", str(tmp_path / "other")] + argv) == 2, bad
    assert not (tmp_path / "other").exists()


def test_check_reads_every_release_and_fails_on_a_shape_of_its_own(tmp_path, capsys):
    good = tmp_path / "a"
    bad = tmp_path / "deep" / "b"
    for d in (good, bad):
        d.mkdir(parents=True)
        (d / "CORPUS.jsonl").write_text('{"id": "x"}\n')
    bank.write_release(str(good), passed=True, n=1, bar="accuracy >= 0.9", model=None)
    (bad / "RELEASE.json").write_text(json.dumps({"released": False}))
    assert main(["--root", str(tmp_path), "bank", "check"]) == 1
    out = capsys.readouterr().out
    assert "ok    a" in out and "FAIL  deep/b" in out and "1 without the envelope" in out


def test_nothing_the_scaffold_writes_carries_a_long_dash(tmp_path):
    files = bank.plan("x", "Judge x", "accuracy >= 0.5", "banks/x", model="m")
    for rel, text in files.items():
        assert not any(ch in text for ch in DASHES), rel


# ------------------------------------------------------------------ the downshift probe

def test_the_ladder_probes_only_top_tier_models():
    assert bank.smaller("claude-opus-5-5") == "claude-sonnet-5-5"
    assert bank.smaller("claude-fable-5-1") == "claude-opus-5-5"
    for m in ("claude-sonnet-5-5", "claude-haiku-4-5", None, "gpt-x", 3):
        assert bank.smaller(m) is None, m


def _cases(n):
    return [{"id": "c{:02d}".format(i), "input": i, "expected": i % 2} for i in range(n)]


def test_the_sample_is_deterministic_and_capped():
    cases = _cases(50)
    a = bank.probe_sample(cases, 20)
    assert len(a) == 20 and a == bank.probe_sample(list(reversed(cases)), 20)
    assert [c["id"] for c in a] != [c["id"] for c in cases[:20]]   # not the head of the file
    assert len(bank.probe_sample(cases[:5], 20)) == 5 and bank.probe_sample(cases, 0) == []


def test_probe_reports_agreement_and_diffs_on_the_same_cases():
    cases = _cases(30)
    # the big model is wrong on c00 only; the small one answers 0 on every case
    rows = [{"id": c["id"], "right": c["id"] != "c00"} for c in cases]
    calls = []

    def answer(case, model="claude-opus-5-5"):
        calls.append(model)
        return 0

    out = []
    d = bank.probe(cases, rows, answer, lambda c, got: got == c["expected"], "claude-opus-5-5",
                   sample=10, out=out)
    picked = bank.probe_sample(cases, 10)
    assert set(calls) == {"claude-sonnet-5-5"} and len(calls) == 10 == d["n"] == len(out)
    small_right = {c["id"] for c in picked if c["expected"] == 0}
    big_right = {c["id"] for c in picked if c["id"] != "c00"}
    assert d["model"] == "claude-sonnet-5-5" and d["big_model"] == "claude-opus-5-5"
    assert d["right"] == len(small_right) and d["rate"] == round(len(small_right) / 10, 4)
    assert d["big_rate"] == round(len(big_right) / 10, 4)
    diff_ids = {x["id"] for x in d["diffs"]}
    assert diff_ids == small_right ^ big_right and d["agree"] == 10 - len(diff_ids)
    for x in d["diffs"]:
        assert x["small_right"] == (x["id"] in small_right) and x["small_got"] == 0
    line = bank.probe_line(d)
    assert line.startswith("probe claude-sonnet-5-5 on 10 cases: {}/10 vs claude-opus-5-5"
                           .format(d["right"])) and "diffs (ids " in line


def test_probe_samples_only_cases_the_main_run_graded():
    cases = _cases(10)
    rows = [{"id": "c03", "right": True}]
    d = bank.probe(cases, rows, lambda c, model=None: 1, lambda c, g: True, "claude-opus-5-5")
    assert d["n"] == 1 and d["big_rate"] == 1.0 and d["agree"] == 1 and d["diffs"] == []
    assert bank.probe_line(d).endswith(", 0 diffs")


def test_a_raising_small_model_counts_wrong_and_never_aborts():
    cases = _cases(4)
    rows = [{"id": c["id"], "right": True} for c in cases]

    def answer(case, model=None):
        if case["id"] == "c01":
            raise RuntimeError("quota hit")
        return case["expected"]

    d = bank.probe(cases, rows, answer, lambda c, g: g == c["expected"], "claude-opus-5")
    assert d["n"] == 4 and d["right"] == 3 and d["agree"] == 3
    assert d["diffs"] == [{"id": "c01", "big_right": True, "small_right": False,
                           "small_got": "error: RuntimeError: quota hit"}]


def test_no_probe_for_a_sonnet_or_a_deterministic_bank():
    rows = [{"id": "c00", "right": True}]
    for m in ("claude-sonnet-5-5", None):
        assert bank.probe(_cases(1), rows, lambda c, model=None: 0, lambda c, g: True, m) is None


def test_the_run_template_carries_the_probe():
    files = bank.plan("x", "Judge x", "accuracy >= 0.5", "banks/x", model="claude-opus-5-5")
    src = files["banks/x/run.py"]
    compile(src, "run.py", "exec")
    assert "def answer(case, model=MODEL, effort=EFFORT):" in src and "smaller model" in src
    assert "--no-probe" in src and "--probe-sample" in src and "downshift" in src


def test_latest_run_is_never_the_probe_file(tmp_path):
    b = _bank(tmp_path)
    run = bank.run_path(b, "20261109T000000")
    probe = bank.run_path(b, "20261109T000000", bank.PROBE_EXT)
    for p in (run, probe):
        open(p, "w").close()
    assert bank.latest_run(b) == run == bank.latest_run(b, "*.jsonl")
    assert bank.latest_run(b, "*.probe.jsonl") == probe


def _opus_bank(tmp_path):
    argv = list(NEW)
    argv[8] = "claude-opus-5-5"
    assert main(["--root", str(tmp_path)] + argv) == 0
    b = tmp_path / "banks" / "ticket-route"
    _fill(b)
    run_py = (b / "run.py").read_text().replace(
        RAISE, NOTE +
        '    if model != MODEL and case["id"] == "t2":\n'
        '        raise RuntimeError("small model down")\n'
        '    return {"refund please": "billing", "app crashes": "bugs"}[case["input"]]')
    (b / "run.py").write_text(run_py)
    return b


def test_an_opus_bank_probes_sonnet_and_the_probe_never_changes_passed(tmp_path):
    b = _opus_bank(tmp_path)
    res = _py([str(b / "run.py"), "--json"], tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    assert ("probe claude-sonnet-5-5 on 2 cases: 1/2 vs claude-opus-5-5 2/2, 1 diffs (ids t2)"
            in res.stderr)
    rel = bank.read_release(str(b))
    assert bank.envelope_problems(rel) == [] and rel["passed"] is True
    ds = rel["downshift"]
    assert ds["model"] == "claude-sonnet-5-5" and ds["rate"] == 0.5 and ds["big_rate"] == 1.0
    assert ds["diffs"][0]["small_got"] == "error: RuntimeError: small model down"
    probe_rows = bank.load(str(b / "RUNS" / (rel["run"] + ".probe.jsonl")))
    assert sorted(r["id"] for r in probe_rows) == ["t1", "t2"]
    assert json.loads(res.stdout)["downshift"] == ds


def test_no_probe_and_offline_ask_no_smaller_model(tmp_path):
    b = _opus_bank(tmp_path)
    res = _py([str(b / "run.py"), "--no-probe"], tmp_path)
    assert res.returncode == 0 and "probe" not in res.stderr
    assert "downshift" not in bank.read_release(str(b))
    assert not list((b / "RUNS").glob("*.probe.jsonl"))
    off = _py([str(b / "run.py"), "--offline"], tmp_path)
    assert off.returncode == 0 and "probe" not in off.stderr, off.stdout + off.stderr


# ------------------------------------------------------------------ the effort probe

def test_the_effort_ladder_steps_down_to_medium_and_stops():
    assert bank.EFFORT_LADDER == {"xhigh": "high", "high": "medium"}
    assert bank.cheaper_effort("xhigh") == "high" and bank.cheaper_effort("high") == "medium"
    for e in ("medium", "low", "max", None, 3, "HIGH"):
        assert bank.cheaper_effort(e) is None, e


def test_no_effort_probe_at_medium_or_below_or_without_a_model():
    rows = [{"id": "c00", "right": True}]

    def answer(case, model=None, effort=None):
        raise AssertionError("never asked")

    for m, e in (("claude-opus-5-5", "medium"), ("claude-opus-5-5", "low"), (None, "xhigh")):
        assert bank.probe_effort(_cases(1), rows, answer, lambda c, g: True, m, e) is None
    # nothing sampled: no dict either
    assert bank.probe_effort(_cases(3), [], answer, lambda c, g: True, "m", "high") is None


def test_the_effort_probe_asks_the_same_model_on_the_model_probes_sample():
    cases = _cases(40)
    rows = [{"id": c["id"], "right": c["id"] != "c00"} for c in cases]
    calls, model_out, effort_out = [], [], []

    def answer(case, model="claude-opus-5-5", effort="xhigh"):
        calls.append((model, effort))
        return 0

    grade = lambda c, got: got == c["expected"]  # noqa: E731
    m = bank.probe(cases, rows, answer, grade, "claude-opus-5-5", sample=12, out=model_out)
    calls.clear()
    d = bank.probe_effort(cases, rows, answer, grade, "claude-opus-5-5", "xhigh", sample=12,
                          out=effort_out)
    assert set(calls) == {("claude-opus-5-5", "high")} and len(calls) == 12 == d["n"]
    assert [r["id"] for r in effort_out] == [r["id"] for r in model_out]   # the same cases
    assert all(r["effort"] == "high" and r["model"] == "claude-opus-5-5" for r in effort_out)
    assert d["effort"] == "high" and d["big_effort"] == "xhigh" and d["model"] == "claude-opus-5-5"
    assert d["big_rate"] == m["big_rate"] and d["right"] == m["right"]
    assert {x["id"] for x in d["diffs"]} == {x["id"] for x in m["diffs"]}
    assert d["agree"] == 12 - len(d["diffs"])
    line = bank.probe_effort_line(d)
    assert line.startswith("probe effort high on 12 cases: {}/12 vs xhigh".format(d["right"]))
    assert "(claude-opus-5-5)" in line


def test_a_raising_cheaper_effort_counts_wrong_and_never_aborts():
    cases = _cases(3)
    rows = [{"id": c["id"], "right": True} for c in cases]

    def answer(case, model=None, effort=None):
        if case["id"] == "c02":
            raise RuntimeError("timeout")
        return case["expected"]

    d = bank.probe_effort(cases, rows, answer, lambda c, g: g == c["expected"], "m", "high")
    assert d["effort"] == "medium" and d["n"] == 3 and d["right"] == 2
    assert d["diffs"] == [{"id": "c02", "big_right": True, "small_right": False,
                           "small_got": "error: RuntimeError: timeout"}]


def test_the_scaffold_carries_an_effort_and_refuses_an_unknown_one():
    src = bank.plan("x", "Judge x", "accuracy >= 0.5", "banks/x", model="m")["banks/x/run.py"]
    assert "EFFORT = 'medium'" in src and "def answer(case, model=MODEL, effort=EFFORT):" in src
    assert "effort probe" in src and "downshift.effort" in src
    src = bank.plan("x", "Judge x", "accuracy >= 0.5", "banks/x", model="m",
                    effort="xhigh")["banks/x/run.py"]
    compile(src, "run.py", "exec")
    assert "EFFORT = 'xhigh'" in src
    with pytest.raises(bank.BankError, match="effort"):
        bank.plan("x", "Judge x", "accuracy >= 0.5", "banks/x", effort="huge")


def test_latest_run_is_never_the_effort_probe_file(tmp_path):
    b = _bank(tmp_path)
    run = bank.run_path(b, "20261109T000000")
    eff = bank.run_path(b, "20261109T000001", bank.PROBE_EFFORT_EXT)
    for p in (run, eff):
        open(p, "w").close()
    assert bank.latest_run(b) == run
    assert bank.latest_run(b, "*.effort-probe.jsonl") == eff


def _effort_bank(tmp_path, model, effort):
    argv = list(NEW)
    argv[8] = model
    assert main(["--root", str(tmp_path)] + argv + ["--effort", effort]) == 0
    b = tmp_path / "banks" / "ticket-route"
    _fill(b)
    run_py = (b / "run.py").read_text().replace(
        RAISE, NOTE +
        '    if effort != EFFORT and case["id"] == "t1":\n'
        '        return "general"\n'
        '    if model != MODEL and case["id"] == "t2":\n'
        '        raise RuntimeError("small model down")\n'
        '    return {"refund please": "billing", "app crashes": "bugs"}[case["input"]]')
    (b / "run.py").write_text(run_py)
    return b


def test_an_opus_xhigh_bank_writes_both_probes_under_downshift(tmp_path):
    b = _effort_bank(tmp_path, "claude-opus-5-5", "xhigh")
    res = _py([str(b / "run.py")], tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    assert ("probe effort high on 2 cases: 1/2 vs xhigh 2/2 (claude-opus-5-5), 1 diffs (ids t1)"
            in res.stderr)
    rel = bank.read_release(str(b))
    assert bank.envelope_problems(rel) == [] and rel["passed"] is True
    ds = rel["downshift"]
    assert ds["model"] == "claude-sonnet-5-5" and ds["rate"] == 0.5    # the model keys kept
    eff = ds["effort"]
    assert eff["effort"] == "high" and eff["big_effort"] == "xhigh"
    assert eff["model"] == "claude-opus-5-5" and eff["n"] == 2 and eff["right"] == 1
    assert eff["big_rate"] == 1.0 and eff["agree"] == 1
    assert eff["diffs"] == [{"id": "t1", "big_right": True, "small_right": False,
                             "small_got": "general"}]
    rows = bank.load(str(b / "RUNS" / (rel["run"] + bank.PROBE_EFFORT_EXT)))
    assert sorted(r["id"] for r in rows) == ["t1", "t2"]
    assert {r["effort"] for r in rows} == {"high"}


def test_a_sonnet_high_bank_writes_the_effort_probe_alone(tmp_path):
    b = _effort_bank(tmp_path, "claude-sonnet-5-5", "high")
    res = _py([str(b / "run.py")], tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
    ds = bank.read_release(str(b))["downshift"]
    assert list(ds) == ["effort"] and ds["effort"]["effort"] == "medium"
    assert not list((b / "RUNS").glob("*[0-9].probe.jsonl"))


def test_a_medium_bank_probes_no_effort(tmp_path):
    b = _effort_bank(tmp_path, "claude-opus-5-5", "medium")
    res = _py([str(b / "run.py")], tmp_path)
    assert res.returncode == 0 and "probe effort" not in res.stderr
    rel = bank.read_release(str(b))
    assert "effort" not in rel["downshift"]
    assert not list((b / "RUNS").glob("*.effort-probe.jsonl"))


def test_no_probe_skips_both_probes(tmp_path):
    b = _effort_bank(tmp_path, "claude-opus-5-5", "xhigh")
    res = _py([str(b / "run.py"), "--no-probe"], tmp_path)
    assert res.returncode == 0 and "probe" not in res.stderr
    assert "downshift" not in bank.read_release(str(b))
    assert not list((b / "RUNS").glob("*probe.jsonl"))


# ------------------------------------------------------------------ the standard shape

def _lay(tmp_path, **kw):
    files = bank.plan("x", "Judge x", "accuracy >= 0.9", "banks/x", **kw)
    bank.lay_out(str(tmp_path), files)
    b = tmp_path / "banks" / "x"
    _fill_40(b)
    return files, b


def test_the_scaffold_lays_out_the_standard_shape_only(tmp_path):
    files, b = _lay(tmp_path, model="claude-sonnet-5-5", label_source="the support lead")
    src = files["banks/x/run.py"]
    compile(src, "run.py", "exec")
    for gone in ("def main", "argparse", "def score", "write_release", "run_path"):
        assert gone not in src, gone
    assert src.rstrip().endswith('if __name__ == "__main__":\n    sys.exit(bank.main(__file__))')
    assert "MODEL = 'claude-sonnet-5-5'" in src and "EFFORT = 'medium'" in src
    assert "LABEL_SOURCE = 'the support lead'" in src and "BAR = {\"accuracy\": 0.9}" in src
    assert "def answer(case, model=MODEL, effort=EFFORT):" in src and "note_usage" in src
    assert "def grade(case, got):" in src and "--self-check" in src and "--repeats" in src
    assert bank.lint(str(b)) == []
    assert bank.self_check(str(b)) == []


def test_a_deterministic_scaffold_declares_no_model_and_no_effort(tmp_path):
    files, b = _lay(tmp_path, label_source="the ledger")
    src = files["banks/x/run.py"]
    assert "MODEL = None" in src and "EFFORT = None" in src and "def answer(case):" in src
    assert bank.self_check(str(b)) == []


def test_a_scaffold_with_no_label_source_is_refused_until_one_is_named(tmp_path):
    files, b = _lay(tmp_path)
    assert "LABEL_SOURCE = None" in files["banks/x/run.py"]
    assert [r.rule for r in bank.lint(str(b))] == ["label-by-judged-model"]


def test_the_codebook_names_the_row_keys_the_bar_and_the_dated_changes(tmp_path):
    files = bank.plan("x", "Judge x", "accuracy >= 0.9", "banks/x")
    book = files["banks/x/CODEBOOK.md"]
    for key in ("`id`", "`input`", "`expected`", "`why`", "`split`", "`label_source`"):
        assert key in book, key
    assert "never written by the model the bank judges" in book
    assert "accuracy >= 0.9" in book.split("## 2.")[1].split("## 3.")[0]
    assert "a bar change needs a dated line here" in book.split("## 3.")[1]


def test_the_bank_test_is_the_self_check_and_names_each_fix(tmp_path):
    files, b = _lay(tmp_path)
    test = files["banks/x/test_x_bank.py"]
    assert "def test_the_bank_is_on_the_standard" in test and "bank.self_check(HERE)" in test
    res = _py(["-m", "pytest", "-q", "-p", "no:cacheprovider", str(b)], tmp_path)
    assert res.returncode != 0
    assert "label-by-judged-model" in res.stdout and "add 'LABEL_SOURCE" in res.stdout


def test_new_takes_a_label_source(tmp_path):
    assert main(["--root", str(tmp_path)] + NEW) == 0
    src = (tmp_path / "banks" / "ticket-route" / "run.py").read_text()
    assert "LABEL_SOURCE = 'the support lead, by hand'" in src
