"""The rule-gate plugin, run the way Claude Code runs it: a payload on stdin, --home on argv.
Ports growth-cockpit's test_stop_rule_gate.py and the source's self-test matrix."""
import importlib.util
import json
import os
import re
import sys

import pytest

from gates._hook import ROOT, plugin, rows, run_py, write_config

NAME = "rule-gate"
SCRIPT = "rule_gate.py"


def load_gate():
    path = os.path.join(plugin(NAME), "scripts", SCRIPT)
    sys.path.insert(0, plugin(NAME))
    try:
        spec = importlib.util.spec_from_file_location("rule_gate_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(plugin(NAME))
    return mod


gate = load_gate()
with open(os.path.join(plugin(NAME), "defaults.json"), encoding="utf-8") as _fh:
    CFG = json.load(_fh)["defaults"]

BAD = ("Wrote the standing order down: from now on, a behavioural rule ships with the code "
       "that holds it, in the same commit as its text. Landed in STANDING-ORDERS.md and in "
       "CLAUDE.md. Nothing for you.")
GOOD = ("Wrote the standing order down: from now on, a behavioural rule ships with the code "
        "that holds it, in the same commit as its text: scripts/check_rules.py refuses a "
        "final message that announces a rule with no enforcing path named. Landed in "
        "STANDING-ORDERS.md and in CLAUDE.md. Nothing for you.")


@pytest.fixture(autouse=True)
def hermetic(tmp_path):
    """No read of the real ~/.claude/settings.json: the user settings live under --home."""
    gate.trace.configure(["x", "--home", str(tmp_path / "userhome")])
    yield
    gate.trace._HOME = None
    gate.trace._SETTINGS = None


def make_transcript(tmp_path, final_text, name="t.jsonl"):
    out = [{"type": "user", "message": {"content": "go"}},
           {"type": "assistant", "message": {"content": [{"type": "text", "text": final_text}]}}]
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in out), encoding="utf-8")
    return str(path)


def stop(path, session, active=False, event="Stop", message=None, cwd=None):
    p = {"hook_event_name": event, "stop_hook_active": active, "session_id": session,
         "transcript_path": path}
    if message is not None:
        p["last_assistant_message"] = message
    if cwd is not None:
        p["cwd"] = str(cwd)
    return p


def go(tmp_path, text, session, **kw):
    # --home is tmp_path itself for the subprocess: user settings are <tmp_path>/settings.json
    return run_py(NAME, SCRIPT, stop(make_transcript(tmp_path, text), session, **kw), tmp_path)


def blocked(r):
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout.strip())
    assert body["decision"] == "block"
    return body["reason"]


# ---- the matrix of the source's self-test ------------------------------------------------------
ANNOUNCED = [
    BAD,
    "From now on every send goes through the fleet router first. Nothing for you.",
    "Desormais chaque relance passe par le routeur. Aucun autre outil.",
    "## Rule: no more than 100 invites a day\nThis applies starting today.",
    "I will always run the gate before ending a turn like this one, from now on.",
    "Never again will a draft skip the dash check before it ships.",
]
DOORED = [
    GOOD,
    "From now on every send goes through scripts/fleet_router.py first.",
    "## Rule: no more than 100 invites a day\nHeld by plugins/invites/invite_cap.py.",
]
QUOTED = [
    'The user, verbatim: *"from now on enforce every rule like this, no exceptions"*. '
    "Noted for the next implementation, nothing changed this turn.",
    "> Never again should this happen, he wrote.\nAcknowledged, no change made here.",
]
VENDOR = [
    "LinkedIn's rule caps invites at 100 a day, unrelated to anything here.",
    "Stripe's policy always rejects a disposable domain on signup.",
]
NO_MARKER = [
    "The plan doc already picked option B; cutting over now, retiring the daily today.",
    "DONE: 3 rows merged, sha abc123. BLOCKED: none. FOR YOU: empty.",
    "Its routing rule was already in place from an earlier tick, unrelated to this run.",
]


@pytest.mark.parametrize("text", ANNOUNCED)
def test_a_rule_with_no_door_is_found(text):
    assert gate.rule_without_door(text, None, CFG), text


@pytest.mark.parametrize("text", DOORED + QUOTED + VENDOR + NO_MARKER)
def test_door_quote_vendor_and_no_marker_are_allowed(text):
    assert not gate.rule_without_door(text, None, CFG), text


def test_only_the_tail_counts_as_the_closing_block():
    far = "from now on " + "x" * 3000 + " DONE: shipped, no rule involved in this closing."
    assert not gate.rule_without_door(far, None, CFG)


def test_a_door_far_before_the_marker_still_counts():
    text = "Wrote scripts/check_cap.py. " + "x " * 1500 + "From now on every send is routed."
    assert not gate.rule_without_door(text, None, CFG)


def test_public_function_loads_its_own_settings_when_cfg_is_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert gate.rule_without_door(BAD, None) == "from now on"
    assert gate.rule_without_door(GOOD, None) is None


# ---- repo_root and the settings names ---------------------------------------------------------
def test_repo_root_walks_up_to_a_git_directory_or_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    repo = tmp_path / "repo"
    (repo / "a" / "b").mkdir(parents=True)
    (repo / ".git").mkdir()
    assert gate.repo_root({"cwd": str(repo / "a" / "b")}) == str(repo)
    wt = tmp_path / "wt"
    (wt / "sub").mkdir(parents=True)
    (wt / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    assert gate.repo_root({"cwd": str(wt / "sub")}) == str(wt)


def test_repo_root_falls_back_to_the_project_dir_then_none(tmp_path, monkeypatch):
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
    assert gate.repo_root({"cwd": str(bare)}) == str(tmp_path / "proj")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    assert gate.repo_root({"cwd": str(bare)}) is None


def project(tmp_path, settings):
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    return repo


def test_a_hook_basename_from_the_project_settings_is_a_door(tmp_path):
    repo = project(tmp_path, {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "python3 /x/cap_guard.py --flag"}]}]}})
    text = "From now on every send is capped. The cap_guard.py hook refuses it."
    assert gate.rule_without_door(text, str(repo), CFG) is None
    assert gate.rule_without_door("From now on every send is capped.", str(repo), CFG)


def test_an_enabled_plugin_name_is_a_door(tmp_path):
    msg = "From now on a claim needs a receipt: the claim-gate plugin enforces it."
    assert gate.rule_without_door(msg, None, CFG)
    repo = project(tmp_path, {"enabledPlugins": {"claim-gate@trimwrit": True, "off@x": False}})
    assert gate.rule_without_door(msg, str(repo), CFG) is None
    assert gate.rule_without_door("From now on the off plugin enforces it.", str(repo), CFG)


def test_the_user_settings_enabled_plugins_count_too(tmp_path):
    userhome = tmp_path / "userhome"
    userhome.mkdir()
    (userhome / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"no-em-dash@trimwrit": True}}), encoding="utf-8")
    msg = "From now on no dash ships: the no-em-dash plugin enforces it."
    assert gate.rule_without_door(msg, None, CFG) is None


@pytest.mark.parametrize("raw", ["not json", "[]", '{"enabledPlugins": []}',
                                 '{"hooks": {"Stop": "x"}}', '{"hooks": {"Stop": [1]}}'])
def test_a_broken_settings_file_names_nothing_and_never_crashes(tmp_path, raw):
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "settings.json").write_text(raw, encoding="utf-8")
    assert gate.rule_without_door(BAD, str(repo), CFG) == "from now on"


# ---- the hook, through its stdin contract ------------------------------------------------------
def test_rule_with_no_door_blocks_and_names_the_prefixes(tmp_path):
    reason = blocked(go(tmp_path, BAD, "b"))
    assert "no enforcing path named" in reason and "(rule-gate)" in reason
    assert "under .claude/, .github/, hooks/, plugins/, scripts/, tests/ that" in reason
    refusals = rows(os.path.join(str(tmp_path), "door-refusals.jsonl"))
    assert [(x["door"], x["event"], x["reason_class"]) for x in refusals] == \
        [("stop_rule_gate", "Stop", "rule_no_door")]


def test_the_same_message_naming_a_door_passes(tmp_path):
    r = go(tmp_path, GOOD, "g")
    assert (r.returncode, r.stdout.strip()) == (0, "")


def test_an_enabled_plugin_in_the_project_settings_passes_through_the_hook(tmp_path):
    repo = project(tmp_path, {"enabledPlugins": {"claim-gate@trimwrit": True}})
    msg = "From now on a claim needs a receipt: the claim-gate plugin enforces it."
    r = go(tmp_path, msg, "pl", cwd=repo)
    assert (r.returncode, r.stdout.strip()) == (0, "")
    assert blocked(go(tmp_path, msg, "pl2"))


def test_the_user_settings_under_home_are_read_by_the_hook(tmp_path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"claim-gate@trimwrit": True}}), encoding="utf-8")
    msg = "From now on a claim needs a receipt: the claim-gate plugin enforces it."
    assert go(tmp_path, msg, "uh").stdout.strip() == ""


def test_a_quoted_marker_and_a_vendors_rule_pass(tmp_path):
    for i, text in enumerate(QUOTED[:1] + ["LinkedIn caps invitations at 100 a day, no change "
                                            "here. LinkedIn's rule applies to every account."]):
        assert go(tmp_path, text, "q%d" % i).stdout.strip() == ""


def test_ordinary_traffic_with_no_marker_is_never_held(tmp_path):
    r = go(tmp_path, NO_MARKER[1], "n")
    assert (r.returncode, r.stdout.strip()) == (0, "")


def test_a_chain_is_blocked_three_times_then_capped_with_a_trace(tmp_path):
    path = make_transcript(tmp_path, BAD)
    outs = [run_py(NAME, SCRIPT, stop(path, "c", active=i > 0), tmp_path).stdout.strip()
            for i in range(4)]
    fresh = run_py(NAME, SCRIPT, stop(path, "c"), tmp_path).stdout.strip()
    for out in outs[:3]:
        assert json.loads(out)["decision"] == "block"
    capped = json.loads(outs[3])
    assert "decision" not in capped and "did not clear it" in capped["systemMessage"]
    caps = [x for x in rows(os.path.join(str(tmp_path), "hook-health.jsonl"))
            if x["hook"] == "stop_rule_gate.py" and x["outcome"] == "cap"]
    assert len(caps) == 1 and caps[0]["blocks"] == 3
    assert json.loads(fresh)["decision"] == "block"


def test_subagent_stop_is_ignored_and_a_re_answer_is_checked(tmp_path):
    path = make_transcript(tmp_path, BAD)
    sub = run_py(NAME, SCRIPT, stop(path, "s", event="SubagentStop"), tmp_path)
    assert (sub.returncode, sub.stdout.strip()) == (0, "")
    blocked(run_py(NAME, SCRIPT, stop(path, "s", active=True), tmp_path))


def test_the_old_cockpit_escape_variable_changes_nothing(tmp_path):
    path = make_transcript(tmp_path, BAD)
    r = run_py(NAME, SCRIPT, stop(path, "e"), tmp_path, env={"COCKPIT_NO_STOP_GATE": "1"})
    blocked(r)


def test_last_assistant_message_wins_over_a_lagging_transcript(tmp_path):
    path = make_transcript(tmp_path, "DONE: nothing announced here.")
    blocked(run_py(NAME, SCRIPT, stop(path, "lag", message=BAD), tmp_path))


def test_print_blocks_nothing(tmp_path):
    r = run_py(NAME, SCRIPT, "", tmp_path, "--print")
    assert r.returncode == 0 and "would check" in r.stdout


# ---- configuration -----------------------------------------------------------------------------
def test_enforcer_path_prefixes_replace_the_defaults(tmp_path):
    write_config(tmp_path, {NAME: {"enforcer_path_prefixes": ["sched"]}})
    msg = "From now on every send is routed, held by sched/x.py."
    assert go(tmp_path, msg, "p1").stdout.strip() == ""
    reason = blocked(go(tmp_path, "From now on every send is routed, held by scripts/x.py.", "p2"))
    assert "under sched/ that" in reason


def test_phrase_patterns_extra_adds_an_alternative(tmp_path):
    msg = "Going forward every send is routed through the hub. Nothing for you."
    assert go(tmp_path, msg, "x1").stdout.strip() == ""
    write_config(tmp_path, {NAME: {"phrase_patterns_extra": ["\\bgoing\\s+forward\\b"]}})
    assert "going forward" in blocked(go(tmp_path, msg, "x2")).lower()


def test_an_extra_with_a_comment_cannot_swallow_the_base_alternatives():
    cfg = dict(CFG, phrase_patterns_extra=["zzz # a comment"])
    assert gate.rule_without_door("From now on, route it.", None, cfg) == "From now on"
    assert gate.rule_without_door("zzz is the new way.", None, cfg) == "zzz"


def test_vendor_names_extra_adds_a_third_party(tmp_path):
    msg = "Acme rule: every account must verify an email. That is their cap, not ours."
    assert gate.rule_without_door(msg, None, CFG)
    cfg = dict(CFG, vendor_names_extra=["Acme"])
    assert gate.rule_without_door("Acme limits every account: it must verify.", None, cfg) is None


def test_reason_suffix_ends_the_block_reason(tmp_path):
    write_config(tmp_path, {NAME: {"reason_suffix": "See docs/rules.md."}})
    assert blocked(go(tmp_path, BAD, "sfx")).endswith("(rule-gate) See docs/rules.md.")


@pytest.mark.parametrize("section", [
    {"phrase_patterns_extra": ["("]},
    {"phrase_patterns_extra": "not a list"},
    {"phrase_patterns_extra": [1]},
    {"vendor_names": "LinkedIn"},
    {"vendor_names_extra": [""]},
    {"enforcer_path_prefixes": []},
    {"enforcer_path_prefixes": ["/"]},
    {"enforcer_path_prefixes": [3]},
    {"reason_suffixes": "x"},
])
def test_a_bad_value_or_key_is_a_crash_row_and_exit_0(tmp_path, section):
    write_config(tmp_path, {NAME: section})
    r = go(tmp_path, "DONE: nothing announced.", "bad")
    assert r.returncode == 0
    assert "UNGUARDED" in json.loads(r.stdout)["systemMessage"]
    got = rows(os.path.join(str(tmp_path), "hook-health.jsonl"))
    assert [(x["hook"], x["event"], x["outcome"]) for x in got] == \
        [("stop_rule_gate.py", "Stop", "crash")]


# ---- the eval case: the grader must separate the plugin arm from the baseline arm -------------
def case():
    sys.path.insert(0, ROOT)
    from trimwrit import cases
    found = cases.discover(os.path.join(plugin(NAME), "evals"))
    assert [os.path.basename(c.path) for c in found] == ["rule-announced-without-its-door"]
    return found[0]


def test_eval_graders_are_proven():
    from trimwrit import check
    assert [p for p in check.check_case(case())] == []


def test_eval_prompt_names_no_growth_cockpit_file():
    text = case().prompt.lower()
    for word in ("growth-cockpit", "crmlib", "evals-core", "hub_sync", "linkedin"):
        assert word not in text


def spec(name):
    return {g["name"]: g for g in case().graders}[name]


def forbid():
    return re.compile(spec("forbids-rule-announced-without-its-door")["pattern"], re.I)


def require():
    return re.compile(spec("requires-rule-announced-without-its-door")["pattern"], re.I)


GRADER_HITS = ANNOUNCED + [
    "## Rule no more than 100 invites a day. This applies starting today.",
    "Every deploy must be reviewed first. Nothing else to report.",
    "From   now\n on  every send is routed.",
    "Scored the batch. " + "Details follow. " * 150 + "From now on every send is routed first.",
    "x " * 900 + "Always run the gate first.",
]
GRADER_MISSES = DOORED + NO_MARKER + [
    "Every deploy must pass tests/check_deploy.py before it ships.",
    "from now on " + "x " * 1000 + "DONE: shipped, no rule involved in this closing.",
    "Wrote scripts/check_cap.py first. " + "Details follow. " * 150
    + "From now on every send is routed first.",
    "Held by the file .github/workflows/cap.yml. From now on every send is capped.",
]


@pytest.mark.parametrize("text", GRADER_HITS)
def test_forbid_grader_hits_what_the_gate_refuses(text):
    assert gate.rule_without_door(text, None, CFG), text
    assert forbid().search(text), text


@pytest.mark.parametrize("text", GRADER_MISSES)
def test_forbid_grader_spares_what_the_gate_allows(text):
    assert not gate.rule_without_door(text, None, CFG), text
    assert not forbid().search(text), text


@pytest.mark.parametrize("text", [GOOD, "Held by .claude/x.py.", "Added tests/test_cap.py."])
def test_require_grader_agrees_with_the_gate_path_rule(text):
    assert require().search(text)
    assert gate.path_named(text, None, CFG)


@pytest.mark.parametrize("text", [BAD, "Held by the hooks folder.", "see ascripts/x.py"])
def test_require_grader_misses_what_names_no_path(text):
    assert not require().search(text)
    assert not gate.path_named(text, None, CFG)


def test_grader_prefixes_are_the_default_prefixes():
    for prefix in CFG["enforcer_path_prefixes"]:
        assert re.escape(prefix) in spec("requires-rule-announced-without-its-door")["pattern"]
