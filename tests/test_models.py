"""Every `claude -p` trimwrit makes names a full model id, and every result records who answered.

No model call: the fake binaries below print the stream a real `claude -p` would, and record the
argv they were given so the test can read which model was asked for.
"""
import json
import os

import pytest

from trimwrit import models, runner
from trimwrit.cases import Case
from trimwrit.cli import main


def test_require_full_id_accepts_every_known_id_and_refuses_an_alias():
    for mid in models.KNOWN_MODELS:
        assert models.require_full_id(mid) == mid
    for bad in ("sonnet", "opus", "haiku", "fable", "", None, "claude-sonnet"):
        with pytest.raises(ValueError) as exc:
            models.require_full_id(bad)
        assert "claude-opus-5-5" in str(exc.value)


def test_defaults_are_full_ids():
    assert models.DEFAULT_CASE_MODEL == "claude-opus-5-5"
    assert models.DEFAULT_JUDGE_MODEL == "claude-sonnet-5-5"


def test_model_usage_of_is_empty_when_absent_or_malformed():
    assert models.model_usage_of({}) == {}
    assert models.model_usage_of(None) == {}
    assert models.model_usage_of({"modelUsage": "nope"}) == {}
    assert models.model_usage_of({"modelUsage": {"a": {"x": 1}}}) == {"a": {"x": 1}}


FAKE = '''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
open(LOG, "a").write(json.dumps(args) + "\\n")
m = args[args.index("--model") + 1]
if MODE == "stream":
    print(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "hello"}]}}))
    print(json.dumps({"type": "result", "result": "a clean answer",
                      "modelUsage": {m: {"outputTokens": 7}}}))
else:
    print(json.dumps({"result": "PASS", "modelUsage": {m: {"outputTokens": 1}}}))
'''


def _fake(tmp_path, mode="stream"):
    """A fake claude that logs its argv to argv.jsonl and reports the asked model in modelUsage."""
    log = tmp_path / "argv.jsonl"
    path = tmp_path / "claude"
    body = FAKE.replace("LOG", repr(str(log))).replace("MODE", repr(mode))
    path.write_text(body, encoding="utf-8")
    os.chmod(path, 0o755)
    return str(path), log


def _argv(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_run_once_names_the_default_full_id_and_records_model_usage(tmp_path):
    claude, log = _fake(tmp_path)
    case = Case(str(tmp_path / "0001-x"), {"name": "0001-x"}, "say hi", [])
    res = runner.run_once(case, runner.ARM_WITHOUT, 0, {}, claude=claude)
    args = _argv(log)[0]
    assert args[args.index("--model") + 1] == "claude-opus-5-5"
    assert res.model == "claude-opus-5-5"
    assert res.model_usage == {"claude-opus-5-5": {"outputTokens": 7}}


def test_run_once_refuses_an_alias_before_any_subprocess(tmp_path):
    claude, log = _fake(tmp_path)
    case = Case(str(tmp_path / "0001-x"), {"name": "0001-x"}, "say hi", [])
    with pytest.raises(ValueError):
        runner.run_once(case, runner.ARM_WITHOUT, 0, {}, claude=claude, model="sonnet")
    with pytest.raises(ValueError):
        runner.run_many([case], {}, claude=claude, model="opus")
    assert not log.exists()


def test_result_without_model_usage_stays_an_empty_dict():
    res = runner.Result(None, runner.ARM_WITH, 0, "x", [], {})
    assert res.model is None and res.model_usage == {} and res.judge_model_usage == {}


def test_judge_names_its_full_id_and_reports_its_usage(tmp_path):
    claude, log = _fake(tmp_path, mode="json")
    with pytest.raises(ValueError):
        runner.make_judge(claude, "sonnet")
    with pytest.raises(ValueError):
        runner.make_judge("no-such-binary-anywhere", "opus")
    judge = runner.make_judge(claude)
    assert judge.model == "claude-sonnet-5-5"
    case = Case("/tmp/x", {"name": "x"}, "p", [{"type": "llm", "name": "j", "criteria": "c"}])
    res = runner.apply_graders(case, runner.Result(case, runner.ARM_WITH, 0, "t", [], {}), judge)
    assert res.passed
    args = _argv(log)[0]
    assert args[args.index("--model") + 1] == "claude-sonnet-5-5"
    assert res.judge_model == "claude-sonnet-5-5"
    assert res.judge_model_usage == {"claude-sonnet-5-5": [{"outputTokens": 1}]}


def _case_and_rule(tmp_path):
    from trimwrit import cases
    cases.write_case("0001", "bad word case", "say something",
                     [cases.forbid_grader("bad word", name="g")],
                     evals_dir=str(tmp_path / "evals"))
    (tmp_path / "CLAUDE.md").write_text("the rule\n", encoding="utf-8")


def test_cli_run_refuses_an_alias_with_exit_2(tmp_path, capsys):
    _case_and_rule(tmp_path)
    claude, log = _fake(tmp_path)
    base = ["--root", str(tmp_path), "run", "--target", "CLAUDE.md", "--runs", "1",
            "--claude", claude]
    assert main(base + ["--model", "opus"]) == 2
    assert main(base + ["--judge-model", "sonnet"]) == 2
    assert "claude-opus-5-5" in capsys.readouterr().err
    assert not log.exists()


def test_cli_run_records_model_and_usage_in_json_evidence_and_history(tmp_path, capsys):
    _case_and_rule(tmp_path)
    claude, log = _fake(tmp_path)
    rc = main(["--root", str(tmp_path), "run", "--target", "CLAUDE.md", "--runs", "1",
               "--claude", claude, "--model", "claude-haiku-4-5-20251001", "--json"])
    assert rc == 0
    for args in _argv(log):
        assert args[args.index("--model") + 1] == "claude-haiku-4-5-20251001"
    payload = json.loads(capsys.readouterr().out)
    run = payload["cases"][0]["arms"]["with"][0]
    assert run["model"] == "claude-haiku-4-5-20251001"
    assert run["model_usage"] == {"claude-haiku-4-5-20251001": {"outputTokens": 7}}
    hist = (tmp_path / "evals" / "results" / "history.jsonl").read_text().splitlines()
    row = json.loads(hist[-1])
    assert row["model"] == "claude-haiku-4-5-20251001"
    assert row["judge_model"] == "claude-sonnet-5-5"
    assert row["answered_by"] == {"claude-haiku-4-5-20251001": 2}
    evidence = next((tmp_path / "evals" / "results" / "runs").rglob("with-0.md"))
    assert "model: claude-haiku-4-5-20251001" in evidence.read_text()
