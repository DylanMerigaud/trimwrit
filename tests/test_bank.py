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
