"""resume-on-api-error: the detached command it would run, read through --dry-run."""
import json

import pytest

from gates._hook import run_sh

NAME, SCRIPT = "resume-on-api-error", "resume_on_api_error.sh"
NO_MUX = {"STY": "", "TMUX": "", "TMUX_PANE": ""}


def run(tmp_path, error, env=None, config=None, **extra):
    if config is not None:
        d = tmp_path / ".claude"
        d.mkdir(exist_ok=True)
        (d / "trimwrit-gates.json").write_text(json.dumps({NAME: config}))
    e = dict(NO_MUX, HOME=str(tmp_path))
    e.update(env or {})
    payload = {"error": error, "session_id": "s1", "error_details": "boom"}
    out = run_sh(NAME, SCRIPT, payload, "--dry-run", env=e, **extra)
    log = tmp_path / ".claude" / "trimwrit-gates" / "api-failures.log"
    return out, (log.read_text() if log.exists() else "")


SCREEN = {"STY": "123.pop"}
TMUX = {"TMUX": "/tmp/x,1,0", "TMUX_PANE": "%3"}


def test_rate_limit_waits_120_and_stuffs_the_text_into_screen(tmp_path):
    out, log = run(tmp_path, "rate_limit", SCREEN)
    assert out.returncode == 0
    assert out.stdout.startswith("sleep 120; ")
    assert "screen -S '123.pop' -X stuff 'continue: the previous turn ended on an API error" in out.stdout
    assert "(rate_limit)'" in out.stdout
    assert "stop-failure category=rate_limit session=s1 sty=123.pop" in log
    assert "\\033" not in out.stdout


def test_other_errors_wait_60(tmp_path):
    out, _ = run(tmp_path, "server_error", SCREEN)
    assert out.stdout.startswith("sleep 60; ")
    assert "(server_error)" in out.stdout


@pytest.mark.parametrize("category", ["authentication_failed", "billing_error"])
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
    assert "-l 'icontinue: the previous" in out.stdout


def test_tmux_pane_gets_send_keys(tmp_path):
    out, log = run(tmp_path, "overloaded", TMUX)
    assert out.stdout.startswith("sleep 120; ")
    assert "tmux send-keys -t %3 -l 'continue" not in out.stdout  # the pane is quoted
    assert "tmux send-keys -t '%3' -l 'continue: the previous turn" in out.stdout
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
    assert out.stdout.startswith("sleep 7; ") and "stuff 'go on (rate_limit)'" in out.stdout
    assert "stop-failure" in (tmp_path / "my.log").read_text()
    out, _ = run(tmp_path, "server_error", SCREEN, config=cfg)
    assert out.stdout.startswith("sleep 5; ")


def test_a_single_quote_in_the_text_survives_the_detached_command(tmp_path):
    out, _ = run(tmp_path, "server_error", dict(SCREEN, STY="12'3.pop"),
                 config={"text": "it's fine; echo PWNED"})
    assert "'it'\\''s fine; echo PWNED (server_error)'" in out.stdout
    assert "'12'\\''3.pop'" in out.stdout
    # the printed command parses back to the exact text under a real shell
    import subprocess
    probe = out.stdout.replace("sleep 60; ", "").split(" -X stuff ", 1)[1].split('"$(printf', 1)[0]
    got = subprocess.run(["bash", "-c", "printf %s " + probe], capture_output=True, text=True)
    assert got.stdout == "it's fine; echo PWNED (server_error)"


@pytest.mark.parametrize("config", [{"nope": 1}, {"delay_s": "soon"}])
def test_a_bad_config_is_loud_and_the_defaults_apply(tmp_path, config):
    out, log = run(tmp_path, "server_error", SCREEN, config=config)
    assert out.returncode == 0
    assert "config error, defaults applied" in out.stderr
    assert "config error, defaults applied" in log
    assert out.stdout.startswith("sleep 60; ") and "continue: the previous turn" in out.stdout


def test_a_garbage_payload_does_not_crash(tmp_path):
    out = run_sh(NAME, SCRIPT, "not json", "--dry-run", env=dict(NO_MUX, HOME=str(tmp_path), **SCREEN))
    assert out.returncode == 0 and "(unknown)" in out.stdout
