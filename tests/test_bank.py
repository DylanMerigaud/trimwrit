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
       "--producer", "router.py:route"]


def _py(args, cwd):
    env = dict(os.environ, PYTHONPATH=REPO)
    return subprocess.run([sys.executable] + args, cwd=cwd, capture_output=True, text=True,
                          timeout=120, env=env)


def _fill(b):
    cases = [{"id": "t1", "input": "refund please", "expected": "billing", "why": "row 12"},
             {"id": "t2", "input": "app crashes", "expected": "bugs", "why": "row 40"}]
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
    assert res.returncode != 0 and "CORPUS.jsonl is empty" in res.stdout
    _fill(b)
    res = _py(["-m", "pytest", "-q", "-p", "no:cacheprovider", str(b)], tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr


def test_the_run_writes_its_run_file_and_an_enveloped_release(tmp_path):
    assert main(["--root", str(tmp_path)] + NEW) == 0
    b = tmp_path / "banks" / "ticket-route"
    _fill(b)
    off = _py([str(b / "run.py"), "--offline"], tmp_path)
    assert off.returncode == 0 and json.loads(off.stdout)["passed"] is True
    assert not (b / "RELEASE.json").exists()
    run_py = (b / "run.py").read_text().replace(
        '    raise NotImplementedError("call the producer on case[\'input\']")',
        '    return {"refund please": "billing"}.get(case["input"], "general")')
    (b / "run.py").write_text(run_py)
    res = _py([str(b / "run.py")], tmp_path)
    assert res.returncode == 1                    # 1 of 2 right is under the 0.9 bar
    rel = bank.read_release(str(b))
    assert bank.envelope_problems(rel) == []
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
    assert "def answer(case, model=MODEL):" in src and "smaller model" in src
    assert "--no-probe" in src and "--probe-sample" in src and '"downshift"' in src


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
        '    raise NotImplementedError("call the producer on case[\'input\']")',
        '    if model != MODEL and case["id"] == "t2":\n'
        '        raise RuntimeError("small model down")\n'
        '    return {"refund please": "billing", "app crashes": "bugs"}[case["input"]]')
    (b / "run.py").write_text(run_py)
    return b


def test_an_opus_bank_probes_sonnet_and_the_probe_never_changes_passed(tmp_path):
    b = _opus_bank(tmp_path)
    res = _py([str(b / "run.py")], tmp_path)
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
    assert off.returncode == 0 and "probe" not in off.stderr
