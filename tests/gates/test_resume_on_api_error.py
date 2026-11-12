"""resume-on-api-error: the detached command it would run, read through --dry-run."""
import json

import pytest

from gates._hook import run_sh

NAME, SCRIPT = "resume-on-api-error", "resume_on_api_error.sh"
NO_MUX = {"STY": "", "TMUX": "", "TMUX_PANE": ""}
MARK = ": resume-on-api-error sleeper; "


def run(tmp_path, error, env=None, config=None, payload=None, **extra):
    if config is not None:
        d = tmp_path / ".claude"
        d.mkdir(exist_ok=True)
        (d / "trimwrit-gates.json").write_text(json.dumps({NAME: config}))
    e = dict(NO_MUX, HOME=str(tmp_path))
    e.update(env or {})
    body = {"error": error, "session_id": "s1", "error_details": "boom",
            "transcript_path": str(tmp_path / "absent.jsonl")}
    body.update(payload or {})
    out = run_sh(NAME, SCRIPT, body, "--dry-run", env=e, **extra)
    log = tmp_path / ".claude" / "trimwrit-gates" / "api-failures.log"
    return out, (log.read_text() if log.exists() else "")


SCREEN = {"STY": "123.pop"}
TMUX = {"TMUX": "/tmp/x,1,0", "TMUX_PANE": "%3"}


def test_rate_limit_waits_120_and_stuffs_the_text_into_screen(tmp_path):
    out, log = run(tmp_path, "rate_limit", SCREEN)
    assert out.returncode == 0
    assert out.stdout.startswith(MARK + "sleep 120; ")
    assert "screen -S '123.pop' -X stuff 'continue: the previous turn ended on an API error" in out.stdout
    assert "(rate_limit)'" in out.stdout
    assert "stop-failure category=rate_limit session=s1 sty=123.pop" in log
    assert "\\033" not in out.stdout


def test_other_errors_wait_60(tmp_path):
    out, _ = run(tmp_path, "server_error", SCREEN)
    assert out.stdout.startswith(MARK + "sleep 60; ")
    assert "(server_error)" in out.stdout


@pytest.mark.parametrize("category", ["authentication_failed", "billing_error", "account_on_hold",
                                      "oauth_org_not_allowed", "cloud_credential_error"])
def test_credential_and_billing_errors_are_never_resumed(tmp_path, category):
    out, log = run(tmp_path, category, SCREEN)
    assert out.returncode == 0 and out.stdout == ""
    assert "not resumed: " + category in log


def test_vim_mode_adds_escape_and_i(tmp_path):
    out, _ = run(tmp_path, "server_error", SCREEN, config={"vim_mode": True})
    assert "stuff \"$(printf '\\033')\"; sleep 0.4; " in out.stdout
    assert "stuff 'icontinue: the previous" in out.stdout
    out, _ = run(tmp_path, "server_error", TMUX, config={"vim_mode": True})
    assert "send-keys -t '%3' Escape; sleep 0.4; " in out.stdout
    assert "-l -- 'icontinue: the previous" in out.stdout


def test_tmux_pane_gets_send_keys(tmp_path):
    out, log = run(tmp_path, "overloaded", TMUX)
    assert out.stdout.startswith(MARK + "sleep 120; ")
    assert "tmux send-keys -t %3 -l 'continue" not in out.stdout  # the pane is quoted
    assert "tmux send-keys -t '%3' -l -- 'continue: the previous turn" in out.stdout
    assert "tmux send-keys -t '%3' Enter" in out.stdout
    assert "screen" not in out.stdout.replace("resumed tmux", "")
    assert "tmux_pane=%3" in log


def test_screen_wins_when_both_are_set(tmp_path):
    out, _ = run(tmp_path, "server_error", dict(SCREEN, **TMUX))
    assert "screen -S '123.pop'" in out.stdout and "tmux send-keys" not in out.stdout


def test_no_multiplexer_logs_and_prints_nothing(tmp_path):
    out, log = run(tmp_path, "server_error")
    assert out.returncode == 0 and out.stdout == ""
    assert "stop-failure category=server_error" in log
    assert "no screen or tmux session" in log


def test_configured_text_delays_and_log(tmp_path):
    cfg = {"text": "go on", "delay_s": 5, "rate_limit_delay_s": 7, "log": str(tmp_path / "my.log")}
    out, _ = run(tmp_path, "rate_limit", SCREEN, config=cfg)
    assert out.stdout.startswith(MARK + "sleep 7; ") and "stuff 'go on (rate_limit)'" in out.stdout
    assert "stop-failure" in (tmp_path / "my.log").read_text()
    out, _ = run(tmp_path, "server_error", SCREEN, config=cfg)
    assert out.stdout.startswith(MARK + "sleep 5; ")


def test_a_single_quote_in_the_text_survives_the_detached_command(tmp_path):
    out, _ = run(tmp_path, "server_error", dict(SCREEN, STY="12'3.pop"),
                 config={"text": "it's fine; echo PWNED"})
    assert "'it'\\''s fine; echo PWNED (server_error)'" in out.stdout
    assert "'12'\\''3.pop'" in out.stdout
    # the printed command parses back to the exact text under a real shell
    import subprocess
    probe = out.stdout.replace(MARK + "sleep 60; ", "").split(" -X stuff ", 1)[1].split('"$(printf', 1)[0]
    got = subprocess.run(["bash", "-c", "printf %s " + probe], capture_output=True, text=True)
    assert got.stdout == "it's fine; echo PWNED (server_error)"


@pytest.mark.parametrize("config", [{"nope": 1}, {"delay_s": "soon"}])
def test_a_bad_config_is_loud_and_the_defaults_apply(tmp_path, config):
    out, log = run(tmp_path, "server_error", SCREEN, config=config)
    assert out.returncode == 0
    assert "config error, defaults applied" in out.stderr
    assert "config error, defaults applied" in log
    assert out.stdout.startswith(MARK + "sleep 60; ") and "continue: the previous turn" in out.stdout


def test_a_garbage_payload_does_not_crash(tmp_path):
    out = run_sh(NAME, SCRIPT, "not json", "--dry-run", env=dict(NO_MUX, HOME=str(tmp_path), **SCREEN))
    assert out.returncode == 0 and "(unknown)" in out.stdout


def test_tmux_text_starting_with_a_dash_is_not_read_as_a_flag(tmp_path):
    out, _ = run(tmp_path, "overloaded", TMUX, config={"text": "-keep going"})
    assert "tmux send-keys -t '%3' -l -- '-keep going (overloaded)'" in out.stdout


# ---- 0.2.0: what is resumed is read from the transcript, and sleepers never stack ----------

SAFEGUARDS_TEXT = ("API Error: Opus 5.5's safeguards flagged this session "
                   "(https://www.anthropic.com/legal/aup). You may be seeing this for the first "
                   "time. Details: `[cyber]`")


def row(kind, text="", error=None):
    r = {"type": kind, "isSidechain": False,
         "message": {"role": kind, "content": [{"type": "text", "text": text}]}}
    if error:
        r.update({"isApiErrorMessage": True, "error": error})
    return json.dumps(r)


def transcript(tmp_path, *rows, name="t.jsonl"):
    path = tmp_path / name
    path.write_text("\n".join(rows) + "\n")
    return str(path)


def ok():
    return row("assistant", "done, the tests pass")


def fail(text="API Error: 500 Internal server error", error="server_error"):
    return row("assistant", text, error)


def test_a_safeguards_flag_read_from_the_transcript_is_never_resumed(tmp_path):
    t = transcript(tmp_path, row("user", "go"), ok(), row("user", "next"),
                   fail(SAFEGUARDS_TEXT, "invalid_request"))
    out, log = run(tmp_path, "invalid_request", TMUX,
                   payload={"error_details": "", "transcript_path": t})
    assert out.returncode == 0 and out.stdout == ""
    assert "not resumed: safeguards flag" in log


@pytest.mark.parametrize("text", ["blocked under Anthropic's Usage Policy",
                                  "see https://www.anthropic.com/legal/aup"])
def test_every_safeguards_wording_is_caught(tmp_path, text):
    t = transcript(tmp_path, fail("API Error: " + text, "invalid_request"))
    out, log = run(tmp_path, "invalid_request", SCREEN, payload={"transcript_path": t})
    assert out.stdout == "" and "not resumed: safeguards flag" in log


def test_a_safeguards_flag_in_error_details_is_never_resumed(tmp_path):
    out, log = run(tmp_path, "invalid_request", SCREEN,
                   payload={"error_details": SAFEGUARDS_TEXT})
    assert out.stdout == "" and "not resumed: safeguards flag" in log


def test_the_transcript_is_found_from_cwd_and_session_when_the_path_is_absent(tmp_path):
    cwd = "/work/some.repo"
    d = tmp_path / ".claude" / "projects" / "-work-some-repo"
    d.mkdir(parents=True)
    (d / "s9.jsonl").write_text(fail(SAFEGUARDS_TEXT, "invalid_request") + "\n")
    out, log = run(tmp_path, "invalid_request", SCREEN,
                   payload={"transcript_path": None, "session_id": "s9", "cwd": cwd,
                            "error_details": ""})
    assert out.stdout == "" and "not resumed: safeguards flag" in log


def test_a_prompt_too_long_is_never_resumed(tmp_path):
    t = transcript(tmp_path, fail("API Error: Prompt is too long", "invalid_request"))
    out, log = run(tmp_path, "invalid_request", SCREEN, payload={"transcript_path": t})
    assert out.stdout == "" and "not resumed: request too large" in log


def test_another_invalid_request_is_resumed_once_in_a_row_only(tmp_path):
    bad = fail("API Error: 400 something odd", "invalid_request")
    t = transcript(tmp_path, ok(), bad)
    out, _ = run(tmp_path, "invalid_request", SCREEN, payload={"transcript_path": t})
    assert out.stdout.startswith(MARK + "sleep 60; ")
    t = transcript(tmp_path, ok(), bad, row("user", "continue"), bad, name="t2.jsonl")
    out, log = run(tmp_path, "invalid_request", SCREEN, payload={"transcript_path": t})
    assert out.stdout == "" and "not resumed: invalid_request 2 times in a row" in log
    # a successful turn in between resets the count
    t = transcript(tmp_path, bad, ok(), bad, name="t3.jsonl")
    out, _ = run(tmp_path, "invalid_request", SCREEN, payload={"transcript_path": t})
    assert "continue: the previous turn" in out.stdout


@pytest.mark.parametrize("category", ["server_error", "overloaded", "rate_limit", "unknown"])
def test_a_plain_transient_failure_still_resumes(tmp_path, category):
    t = transcript(tmp_path, ok(), fail("API Error: 529 overloaded", category))
    out, log = run(tmp_path, category, TMUX, payload={"transcript_path": t, "error_details": ""})
    assert "tmux send-keys -t '%3' -l -- 'continue: the previous turn" in out.stdout
    assert "not resumed" not in log


def test_a_transient_loop_backs_off_but_never_stops(tmp_path):
    # Dylan's standing order (retry-forever-everywhere): a network cut is retried without end.
    def nth(n):
        t = transcript(tmp_path, ok(), *[fail()] * n, name="n{}.jsonl".format(n))
        out, _ = run(tmp_path, "server_error", SCREEN, payload={"transcript_path": t})
        return out.stdout
    assert nth(5).startswith(MARK + "sleep 60; ")
    assert nth(6).startswith(MARK + "sleep 120; ")
    assert nth(8).startswith(MARK + "sleep 480; ")
    assert nth(40).startswith(MARK + "sleep 3600; ")


def test_the_loop_breaker_stops_another_category_after_three_in_a_row(tmp_path):
    def nth(n):
        t = transcript(tmp_path, ok(), *[fail("API Error: cut", "max_output_tokens")] * n,
                       name="m{}.jsonl".format(n))
        return run(tmp_path, "max_output_tokens", SCREEN, payload={"transcript_path": t})
    for n in (1, 2, 3):
        assert "continue: the previous turn" in nth(n)[0].stdout
    out, log = nth(4)
    assert out.stdout == ""
    assert "not resumed: loop breaker: 4 max_output_tokens failures in a row" in log


def test_without_a_transcript_a_per_session_counter_stands_in(tmp_path):
    for _ in range(3):
        out, _ = run(tmp_path, "max_output_tokens", SCREEN)
        assert "continue" in out.stdout
    out, log = run(tmp_path, "max_output_tokens", SCREEN)
    assert out.stdout == "" and "loop breaker" in log


def test_credentials_stay_never_resumed_even_on_a_first_failure(tmp_path):
    t = transcript(tmp_path, ok(), fail("API Error: 401", "authentication_failed"))
    out, log = run(tmp_path, "authentication_failed", SCREEN, payload={"transcript_path": t})
    assert out.stdout == "" and "credential or billing" in log


def _state(tmp_path):
    return tmp_path / ".claude" / "trimwrit-gates" / "resume-on-api-error"


def test_a_pending_sleeper_on_the_same_pane_refuses_a_second_one(tmp_path):
    import subprocess
    sleeper = subprocess.Popen(["bash", "-c", ": resume-on-api-error sleeper; sleep 30; true"])
    try:
        st = _state(tmp_path)
        st.mkdir(parents=True)
        (st / "pending-tmux-_tmp_x-_3.pid").write_text("{}\n".format(sleeper.pid))
        out, log = run(tmp_path, "server_error", TMUX)
        assert out.stdout == ""
        assert "not resumed: a sleeper is already pending for tmux %3 (pid {})".format(sleeper.pid) in log
        # another pane is not blocked
        out, _ = run(tmp_path, "server_error", dict(TMUX, TMUX_PANE="%4"))
        assert "send-keys -t '%4'" in out.stdout
    finally:
        sleeper.kill()
        sleeper.wait()
    # its sleeper gone, the stale pidfile no longer blocks
    out, _ = run(tmp_path, "server_error", TMUX)
    assert "send-keys -t '%3'" in out.stdout


def test_a_pidfile_naming_a_foreign_live_process_does_not_block(tmp_path):
    import os
    st = _state(tmp_path)
    st.mkdir(parents=True)
    (st / "pending-tmux-_tmp_x-_3.pid").write_text("{}\n".format(os.getpid()))
    out, _ = run(tmp_path, "server_error", TMUX)
    assert "send-keys -t '%3'" in out.stdout


def test_real_runs_stack_at_most_one_sleeper_and_it_cleans_up(tmp_path):
    """No --dry-run: a fake tmux on PATH records the keystrokes."""
    import os
    import subprocess
    import time
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    keys = tmp_path / "keys.txt"
    (bin_dir / "tmux").write_text("#!/bin/bash\necho \"$*\" >> {}\n".format(keys))
    (bin_dir / "tmux").chmod(0o755)
    env = dict(NO_MUX, HOME=str(tmp_path), PATH=str(bin_dir) + os.pathsep + os.environ["PATH"], **TMUX)
    d = tmp_path / ".claude"
    d.mkdir()
    (d / "trimwrit-gates.json").write_text(json.dumps({NAME: {"delay_s": 2}}))
    payload = {"error": "server_error", "session_id": "s1",
               "transcript_path": str(tmp_path / "absent.jsonl")}
    for _ in range(4):
        out = run_sh(NAME, SCRIPT, payload, env=env)
        assert out.returncode == 0
    pidf = _state(tmp_path) / "pending-tmux-_tmp_x-_3.pid"
    assert pidf.exists()
    log = (d / "trimwrit-gates" / "api-failures.log").read_text()
    assert log.count("not resumed: a sleeper is already pending for tmux %3") == 3
    for _ in range(100):
        if not pidf.exists():
            break
        time.sleep(0.1)
    assert not pidf.exists()
    sent = keys.read_text()
    assert sent.count("Enter") == 1 and sent.count("continue: the previous turn") == 1
    assert "resumed tmux %3 after 2s" in (d / "trimwrit-gates" / "api-failures.log").read_text()
