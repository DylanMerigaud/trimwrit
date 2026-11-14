"""The promise-gate plugin, run the way Claude Code runs it: a payload on stdin, --home on argv.
Ports growth-cockpit's test_stop_promise_gate.py and the source's self-test matrix."""
import importlib.util
import json
import os
import re
import sys

import pytest

from gates._hook import RESEND, ROOT, eval_cases, plugin, rows, run_py, write_config

NAME = "promise-gate"
SCRIPT = "promise_gate.py"


def load_gate():
    path = os.path.join(plugin(NAME), "scripts", SCRIPT)
    sys.path.insert(0, plugin(NAME))
    try:
        spec = importlib.util.spec_from_file_location("promise_gate_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(plugin(NAME))
    return mod


gate = load_gate()
CFG = {"target_patterns_extra": [], "promise_patterns_extra": [], "reason_suffix": ""}
PROMISE = "Trois morts de plus. Je continue sur le lot suivant."


def make_transcript(tmp_path, final_text, name="t.jsonl"):
    out = [{"type": "user", "message": {"content": "go"}},
           {"type": "assistant", "message": {"content": [{"type": "text", "text": final_text}]}}]
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in out), encoding="utf-8")
    return str(path)


def stop(path, session, active=False, event="Stop", message=None):
    p = {"hook_event_name": event, "stop_hook_active": active, "session_id": session,
         "transcript_path": path}
    if message is not None:
        p["last_assistant_message"] = message
    return p


def prompt(tmp_path, session, text):
    return run_py(NAME, SCRIPT, {"hook_event_name": "UserPromptSubmit", "session_id": session,
                                 "prompt": text}, tmp_path, "--prompt")


def blocked(r):
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout.strip())
    assert body["decision"] == "block"
    return body["reason"]


TARGETS = [
    "je vais dormir. ne t'arrete pas avant d'avoir identifié 5+ candidats fiables",
    "FAIT TOUT PUTIN TROUVEM OI DES BON CANDIDATS T'ARRETE PAS",
    "understoud? never stop untill results",
    "pourquoi tu fais pas tout le pool d'un coup?",
    "go autonome putin de merde",
    "Do not stop until all 24 candidates are scored.",
]
NOT_TARGETS = ["pk tu t'es arrete", "ok stop, on reprend demain", "daily", "recap",
               "c'est pas a toi de juger l'heure. go b.", "tu peux tuer la session"]
PROMISES = [
    PROMISE,
    "Running in background and will land results here and not stopping.",
    "Twenty dead, one survivor. I'll pick it up with the next batch.",
    # How the bare model ended the manuscript case on 2026-10-05 (Opus 5.5, no plugin), none of
    # which the first pattern set caught.
    "*End of Chapter One. Reply \"continue\" for Chapter Two: the first exposed hull.*",
    "**Status: Chapters 1 and 2 complete. Continuing with Chapter 3: the dry riverbed.**",
    "A one-line note is all I need. I'm continuing in sequence and holding the voice.",
    "*End of Chapter One. Chapter Two (The lock-keeper's house, her father's papers) follows.*",
    "*Chapter Two, \"The Lock-Keeper's House,\" continues in /manuscript/02.md.*",
    "Ch. 2 comes next, then onward in order.",
    "Chapter Two follows in the next message.",
    "Tell me which, and I\u2019ll draft the remaining tickets.",
]
FINISHED = [
    "24/24 traités et 2 gardés et pool épuisé.",
    "Rien en vol. tu peux tuer la session",
    "Le lot suivant est dans le pool, traite: 12 morts, 2 gardés, pool vide.",
    "All 12 chapters are drafted above. The manuscript follows the outline as written.",
    "The steps are as follows: open the lock, read the gauge, log the stage.",
    "Chapter Twelve closes the book; every chapter exists for your 8am edit.",
]


@pytest.mark.parametrize("text", TARGETS)
def test_a_target_arms(text):
    assert gate.target_in(text, CFG)


@pytest.mark.parametrize("text", NOT_TARGETS)
def test_talk_about_stopping_is_no_target(text):
    assert not gate.target_in(text, CFG)


@pytest.mark.parametrize("text", PROMISES)
def test_a_promise_is_found(text):
    assert gate.promise_in(text, CFG)


@pytest.mark.parametrize("text", FINISHED)
def test_a_finished_turn_is_no_promise(text):
    assert not gate.promise_in(text, CFG)


def test_only_the_tail_counts():
    assert not gate.promise_in("je continue " + "x" * 5000 + " pool épuisé, 24/24.", CFG)


def test_public_functions_load_their_own_settings_when_cfg_is_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    gate.trace.configure(["x", "--home", str(tmp_path)])
    try:
        assert gate.promise_in(PROMISE) == "Je continue"
        assert gate.target_in("never stop") is True
        assert gate.promise_in("Pool épuisé.") is None
    finally:
        gate.trace._HOME = None
        gate.trace._SETTINGS = None


def test_arming_round_trip_on_a_fake_session(tmp_path):
    gate.trace.configure(["x", "--home", str(tmp_path)])
    try:
        gate.set_armed("fake", "ne t'arrete pas avant 5 candidats")
        assert gate.armed_excerpt("fake") == "ne t'arrete pas avant 5 candidats"
        gate.set_armed("fake", None)
        assert gate.armed_excerpt("fake") is None
    finally:
        gate.trace._HOME = None
        gate.trace._SETTINGS = None


def test_unarmed_session_is_never_held(tmp_path):
    path = make_transcript(tmp_path, PROMISE)
    r = run_py(NAME, SCRIPT, stop(path, "u"), tmp_path)
    assert (r.returncode, r.stdout.strip()) == (0, "")


def test_prompt_with_a_target_arms_and_a_promise_blocks_until_the_chain_cap(tmp_path):
    r = prompt(tmp_path, "s", "je vais dormir. ne t'arrete pas avant d'avoir identifié 5+ candidats")
    assert (r.returncode, r.stdout.strip()) == (0, "")
    path = make_transcript(tmp_path, "Vingt morts, un survivant. Je continue sur le lot suivant.")
    outs = [run_py(NAME, SCRIPT, stop(path, "s", active=i > 0), tmp_path).stdout.strip()
            for i in range(4)]
    for out in outs[:3]:
        body = json.loads(out)
        assert body["decision"] == "block"
        assert "Je continue" in body["reason"] and "ne t'arrete pas" in body["reason"]
        assert body["reason"].endswith("(promise-gate) " + RESEND)
    capped = json.loads(outs[3])
    assert "decision" not in capped and "did not clear it" in capped["systemMessage"]
    got = rows(os.path.join(str(tmp_path), "hook-health.jsonl"))
    assert any(x["hook"] == "stop_promise_gate.py" and x["outcome"] == "cap" for x in got)
    refusals = rows(os.path.join(str(tmp_path), "door-refusals.jsonl"))
    assert [(x["door"], x["event"], x["reason_class"]) for x in refusals] == \
        [("stop_promise_gate", "Stop", "promise_ending")] * 3


def test_armed_session_with_a_finished_turn_is_silent(tmp_path):
    prompt(tmp_path, "a", "never stop until results")
    path = make_transcript(tmp_path, "24/24 traités, 2 gardés, pool épuisé. Rien en vol.")
    r = run_py(NAME, SCRIPT, stop(path, "a"), tmp_path)
    assert (r.returncode, r.stdout.strip()) == (0, "")


def test_a_question_about_stopping_does_not_arm(tmp_path):
    prompt(tmp_path, "q", "pk tu t'es arrete")
    path = make_transcript(tmp_path, PROMISE)
    assert run_py(NAME, SCRIPT, stop(path, "q"), tmp_path).stdout.strip() == ""


def test_subagent_stop_is_ignored_and_a_re_answer_is_checked(tmp_path):
    prompt(tmp_path, "r", "fait tout")
    path = make_transcript(tmp_path, PROMISE)
    sub = run_py(NAME, SCRIPT, stop(path, "r", event="SubagentStop"), tmp_path)
    assert (sub.returncode, sub.stdout.strip()) == (0, "")
    blocked(run_py(NAME, SCRIPT, stop(path, "r", active=True), tmp_path))


def test_the_old_cockpit_escape_variable_changes_nothing(tmp_path):
    prompt(tmp_path, "e", "fait tout")
    path = make_transcript(tmp_path, PROMISE)
    r = run_py(NAME, SCRIPT, stop(path, "e"), tmp_path, env={"COCKPIT_NO_STOP_GATE": "1"})
    blocked(r)


def test_last_assistant_message_wins_over_a_lagging_transcript(tmp_path):
    prompt(tmp_path, "lag", "fait tout")
    path = make_transcript(tmp_path, "Pool épuisé.")
    blocked(run_py(NAME, SCRIPT, stop(path, "lag", message=PROMISE), tmp_path))


def test_arm_disarm_print_use_the_session_from_the_environment(tmp_path):
    env = {"CLAUDE_SESSION_ID": "hand"}
    assert "not armed" in run_py(NAME, SCRIPT, "", tmp_path, "--print", env=env).stdout
    run_py(NAME, SCRIPT, "", tmp_path, "--arm", "never stop", env=env)
    assert 'ARMED on "never stop"' in run_py(NAME, SCRIPT, "", tmp_path, "--print", env=env).stdout
    path = make_transcript(tmp_path, PROMISE)
    blocked(run_py(NAME, SCRIPT, stop(path, "hand"), tmp_path))
    run_py(NAME, SCRIPT, "", tmp_path, "--disarm", env=env)
    assert "not armed" in run_py(NAME, SCRIPT, "", tmp_path, "--print", env=env).stdout


def test_promise_patterns_extra_adds_an_alternative(tmp_path):
    prompt(tmp_path, "x", "never stop")
    path = make_transcript(tmp_path, "Nine done, to be resumed after the break.")
    assert run_py(NAME, SCRIPT, stop(path, "x"), tmp_path).stdout.strip() == ""
    write_config(tmp_path, {NAME: {"promise_patterns_extra": ["\\bto\\s+be\\s+resumed\\b"]}})
    reason = blocked(run_py(NAME, SCRIPT, stop(path, "x"), tmp_path))
    assert "to be resumed" in reason


def test_target_patterns_extra_arms(tmp_path):
    write_config(tmp_path, {NAME: {"target_patterns_extra": ["\\bkeep\\s+at\\s+it\\b"]}})
    prompt(tmp_path, "y", "please keep at it")
    path = make_transcript(tmp_path, PROMISE)
    blocked(run_py(NAME, SCRIPT, stop(path, "y"), tmp_path))


def test_an_extra_with_a_comment_cannot_swallow_the_base_alternatives():
    cfg = dict(CFG, promise_patterns_extra=["x # a comment"])
    assert gate.promise_in("Je continue.", cfg) == "Je continue"
    assert gate.promise_in("zzz x", cfg) == "x"


@pytest.mark.parametrize("section", [
    {"promise_patterns_extra": ["("]},
    {"target_patterns_extra": ["("]},
    {"promise_patterns_extra": "not a list"},
    {"target_patterns_extra": [1]},
    {"reason_suffixes": "x"},
])
def test_a_bad_extra_or_key_is_a_crash_row_and_exit_0(tmp_path, section):
    write_config(tmp_path, {NAME: section})
    path = make_transcript(tmp_path, "Pool épuisé.")
    r = run_py(NAME, SCRIPT, stop(path, "bad"), tmp_path)
    assert r.returncode == 0
    assert "UNGUARDED" in json.loads(r.stdout)["systemMessage"]
    got = rows(os.path.join(str(tmp_path), "hook-health.jsonl"))
    assert [(x["hook"], x["event"], x["outcome"]) for x in got] == \
        [("stop_promise_gate.py", "Stop", "crash")]


def test_a_bad_extra_on_the_prompt_entry_is_a_crash_too(tmp_path):
    write_config(tmp_path, {NAME: {"target_patterns_extra": ["("]}})
    r = prompt(tmp_path, "bp", "never stop")
    assert r.returncode == 0 and "UNGUARDED" in json.loads(r.stdout)["systemMessage"]


def test_reason_suffix_ends_the_block_reason(tmp_path):
    write_config(tmp_path, {NAME: {"reason_suffix": "See docs/batches.md."}})
    prompt(tmp_path, "sfx", "never stop")
    path = make_transcript(tmp_path, PROMISE)
    reason = blocked(run_py(NAME, SCRIPT, stop(path, "sfx"), tmp_path))
    assert reason.endswith("(promise-gate) See docs/batches.md. " + RESEND)


# ---- the eval case: the grader must separate the plugin arm from the baseline arm -------------
def graders():
    sys.path.insert(0, ROOT)
    found = eval_cases(NAME)
    assert [os.path.basename(c.path) for c in found] == ["turn-ends-on-a-promise"]
    return {g["name"]: g for g in found[0].graders}, found[0]


def test_eval_graders_are_proven():
    from trimwrit import check
    _specs, case = graders()
    assert [p for p in check.check_case(case)] == []


def test_eval_prompt_opens_with_a_target_that_arms_the_gate():
    _specs, case = graders()
    first = case.prompt.strip().splitlines()[0]
    assert gate.target_in(first, CFG), first


def test_forbid_grader_carries_every_alternative_of_the_gate():
    # The grader is the gate's PROMISE_SRC written for a non-verbose regex: comments dropped,
    # spaces removed, an apostrophe as \x27. A new alternative in the gate that the grader lacks
    # would score a refused promise as a pass.
    pattern = graders()[0]["forbids-turn-ends-on-a-promise"]["pattern"]
    for line in gate.PROMISE_SRC.strip().split("\n"):
        alt = re.split(r"\s{2,}#", line.strip().lstrip("|").strip())[0]
        alt = alt.replace(" ", "").replace("'", "\\x27")
        assert alt in pattern, alt


def forbid():
    return re.compile(graders()[0]["forbids-turn-ends-on-a-promise"]["pattern"], re.I)


@pytest.mark.parametrize("text", PROMISES + [
    "I will keep going with 13 to 24.",
    "Scored 9 to 16.\nI will\n continue.",
    "Scored 9 to 16.  Je   continue  sur le lot suivant.",
    "Next\nbatch is running in  the  background.",
    "x " * 300 + "I'll report back later.",
    "Scored 9 to 16. " + "Details follow. " * 20 + "To be continued.",
])
def test_forbid_grader_hits_what_the_gate_refuses(text):
    assert gate.promise_in(text, CFG), text
    assert forbid().search(text), text


@pytest.mark.parametrize("text", FINISHED + [
    "I will not continue past the pool: it is empty. 24/24 scored.",
    'Earlier I wrote "next batch" and then did it. ' + "x " * 400 + "24/24 scored, pool exhausted.",
])
def test_forbid_grader_spares_what_the_gate_allows(text):
    assert not gate.promise_in(text, CFG), text
    assert not forbid().search(text), text


# ---- a job still running: every session, armed or not ------------------------------------------
# The relapse of 2026-10-15 (growth-cockpit session 37a78564, correction 0334): the user had asked
# for four fixes and set no target, the model put its test run in the background and ended the turn
# on these two sentences; the notification came five minutes later, after the user had written
# "tu m'as pas donné  le resultq... ca a compact mid response ou quoi".
RELAPSE_FINAL = ("Les tests sont en cours. Le reste de la livraison (commit, push, synchro du hub) "
                 "et le rapport complet attendent leur résultat.")
RELAPSE_PROMPT = ("8 demandes de désinscription restées de 1 à 5 jours sans suppression => c'est "
                  "parceque tu as mi la ligne optout pour les cold? on la met pas.")
TESTS_USE, TESTS_JOB = "toolu_01FwirN3XKoVrHYgkvADweUz", "bnvsi08f6"
TESTS_WHAT = "Run remaining affected suites in background"
# The final message of correction 0308 (growth-cockpit session 736c2b09): a recommendation with
# its options and nothing left running. Tagged on the same lesson, it is a question about the
# answer, not a turn that stopped on its work.
OPTIONS_FINAL = ("**Ce que je recommande :**\n- **Garder (recommandé).** Ça ne coûte plus rien, et "
                 "le gain est petit mais réel. On l'enlève au premier cas où il casse une commande, "
                 "ou si la perte de lisibilité dans les permissions te gêne.\n- **Retirer.** Une "
                 "version 0.2.1 qui enlève le hook, quelques minutes de travail. On revient à environ "
                 "40 refus par jour qui se règlent tout seuls en 9 secondes.")


def user(text, **extra):
    return dict({"type": "user", "isSidechain": False,
                 "message": {"role": "user", "content": text}}, **extra)


def said(text):
    return {"type": "assistant", "isSidechain": False,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def launch(use, job, what=None, name="Bash"):
    """A background launch as Claude Code writes it: the tool_use, then its result row."""
    args = {"command": "python3 -m pytest -q tests", "timeout": 600000}
    if what is not None:
        args.update(description=what, run_in_background=True)
    result = {"Bash": {"stdout": "", "stderr": "", "interrupted": False,
                       "backgroundTaskId": job},
              "Agent": {"agentId": job, "status": "async_launched"},
              "Monitor": {"taskId": job}}[name]
    return [{"type": "assistant", "isSidechain": False,
             "message": {"role": "assistant", "content": [
                 {"type": "tool_use", "id": use, "name": name, "input": args}]}},
            {"type": "user", "isSidechain": False, "toolUseResult": result,
             "message": {"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": use,
                  "content": "Command running in background with ID: {}.".format(job)}]}}]


def notified(use, job):
    note = ("<task-notification>\n<task-id>{}</task-id>\n<tool-use-id>{}</tool-use-id>\n"
            "<status>completed</status>\n<summary>Background command completed (exit code 0)"
            "</summary>\n</task-notification>".format(job, use))
    return {"type": "queue-operation", "operation": "enqueue", "content": note}


def stopped(use, job):
    return {"type": "assistant", "isSidechain": False, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": use + "x", "name": "TaskStop", "input": {"task_id": job}}]}}


def relapse_rows(final=RELAPSE_FINAL):
    return ([user(RELAPSE_PROMPT),
             user("This session is being continued from a previous conversation that ran out of "
                  "context. The summary below covers the earlier portion of the conversation.",
                  isCompactSummary=True, isVisibleInTranscriptOnly=True),
             user("<task-notification>\n<task-id>bolder000</task-id>\n<status>completed</status>"
                  "\n</task-notification>")]
            + launch(TESTS_USE, TESTS_JOB, TESTS_WHAT) + [said(final)])


def write_rows(tmp_path, rows, name="t.jsonl"):
    path = tmp_path / name
    path.write_text("".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n"
                            for r in rows), encoding="utf-8")
    return str(path)


def stop_on(tmp_path, rows, session="job", final=RELAPSE_FINAL, active=False, tasks=None):
    payload = stop(write_rows(tmp_path, rows), session, active=active, message=final)
    if tasks is not None:
        payload["background_tasks"], payload["session_crons"] = tasks, []
    return run_py(NAME, SCRIPT, payload, tmp_path)


def task(job, what=None, kind="shell", status="running"):
    """A `background_tasks` entry as Claude Code 2.1.319 sends it (measured 2026-11-08)."""
    entry = {"id": job, "type": kind, "status": status,
             "description": what if what is not None else "a background job"}
    if kind == "shell":
        entry["command"] = "python3 -m pytest -q tests"
    return entry


def silent(r):
    assert (r.returncode, r.stdout.strip()) == (0, ""), (r.stdout, r.stderr)


DEFERRALS = [
    RELAPSE_FINAL,
    "Still running; waiting for its notification.",
    "**Prochaine étape:** je te donne le résultat du test complet dès qu'il se termine.",
    "je te donne le tableau final des bancs dès qu'ils ont fini et que la réponse est partie.",
    "La chaîne nom, adresse, mx-scan tourne en fond.",
    "It's on the fresh batch rerun of the 30 offices. I'll pick up the result once it finishes.",
    "The full suite is still in progress, I am waiting on its result.",
]
NOT_DEFERRALS = [
    OPTIONS_FINAL,
    "Les tests passent: 412 passed in 61 s. Commit abc1234 poussé sur main, hub synchronisé.",
    "Le serveur de dev tourne sur http://localhost:3000.",
    "24/24 traités, 2 gardés, pool épuisé. Rien en vol.",
    "The deploy finished and the page answers 200; nothing is running.",
]


@pytest.mark.parametrize("text", DEFERRALS)
def test_a_deferral_to_running_work_is_read(text):
    assert gate.deferral_in(text, CFG)


@pytest.mark.parametrize("text", NOT_DEFERRALS)
def test_a_given_result_is_no_deferral(text):
    assert not gate.deferral_in(text, CFG)


def test_the_deferral_words_never_reach_an_armed_promise_without_a_job():
    # "en cours" alone often names the work of someone else: the armed set does not carry it.
    assert gate.promise_in(RELAPSE_FINAL, CFG) is None


def test_relapse_of_2026_10_15_is_blocked_in_an_unarmed_session(tmp_path):
    reason = blocked(stop_on(tmp_path, relapse_rows()))
    assert "sont en cours" in reason
    assert TESTS_WHAT in reason and TESTS_JOB in reason
    assert "foreground" in reason and reason.endswith("(promise-gate) " + RESEND)
    refusals = rows(os.path.join(str(tmp_path), "door-refusals.jsonl"))
    assert [(x["door"], x["reason_class"]) for x in refusals] == \
        [("stop_promise_gate", "job_pending")]


def test_the_job_scan_reads_the_relapse_transcript(tmp_path):
    assert gate.pending_jobs(write_rows(tmp_path, relapse_rows())) == {TESTS_JOB: TESTS_WHAT}


def test_once_the_job_exits_the_same_message_passes(tmp_path):
    silent(stop_on(tmp_path, relapse_rows() + [notified(TESTS_USE, TESTS_JOB)]))


def test_a_job_stopped_by_taskstop_is_no_longer_running(tmp_path):
    silent(stop_on(tmp_path, relapse_rows() + [stopped(TESTS_USE, TESTS_JOB)]))


def test_a_result_given_while_a_job_runs_passes(tmp_path):
    for i, final in enumerate(NOT_DEFERRALS):
        silent(stop_on(tmp_path, relapse_rows(final), session="r%d" % i, final=final))


def test_a_new_prompt_of_the_user_starts_a_new_turn(tmp_path):
    rows_ = relapse_rows()[:-1] + [user("et le mail de Sara?"), said(RELAPSE_FINAL)]
    silent(stop_on(tmp_path, rows_))


def test_a_job_backgrounded_on_the_way_counts_too(tmp_path):
    rows_ = [user(RELAPSE_PROMPT)] + launch("toolu_auto", "b2eimejzl") + [said(RELAPSE_FINAL)]
    reason = blocked(stop_on(tmp_path, rows_))
    assert "job b2eimejzl has not finished" in reason


@pytest.mark.parametrize("name", ["Agent", "Monitor"])
def test_a_subagent_or_a_monitor_is_the_harness_own_wait(tmp_path, name):
    final = "L'agent est en cours, je reviens avec le résultat dès qu'il a fini."
    rows_ = [user(RELAPSE_PROMPT)] + launch("toolu_" + name, "a1b2c3", name=name) + [said(final)]
    silent(stop_on(tmp_path, rows_, final=final))


def test_a_job_and_a_promise_of_the_armed_set_block_unarmed_too(tmp_path):
    final = "Running in background and will land results here."
    rows_ = relapse_rows(final)
    blocked(stop_on(tmp_path, rows_, final=final))


def test_an_unarmed_promise_with_no_job_stays_silent(tmp_path):
    silent(stop_on(tmp_path, [user(RELAPSE_PROMPT), said(RELAPSE_FINAL)]))


def test_the_job_block_caps_like_the_promise_block(tmp_path):
    rows_ = relapse_rows()
    outs = [stop_on(tmp_path, rows_, session="cap", active=i > 0).stdout.strip()
            for i in range(4)]
    assert [json.loads(o).get("decision") for o in outs[:3]] == ["block"] * 3
    assert "did not clear it" in json.loads(outs[3])["systemMessage"]
    got = rows(os.path.join(str(tmp_path), "hook-health.jsonl"))
    assert any(x["outcome"] == "cap" and x["detail"].startswith("job: ") for x in got)


def test_an_armed_session_reads_the_job_when_no_armed_promise_is_there(tmp_path):
    prompt(tmp_path, "armed-job", "fait tout")
    reason = blocked(stop_on(tmp_path, relapse_rows(), session="armed-job"))
    assert TESTS_JOB in reason


def test_a_missing_transcript_reads_as_no_job(tmp_path):
    assert gate.pending_jobs(str(tmp_path / "absent.jsonl")) == {}
    r = run_py(NAME, SCRIPT, stop(str(tmp_path / "absent.jsonl"), "gone", message=RELAPSE_FINAL),
               tmp_path)
    silent(r)


# Stop payload `background_tasks` (2026-11-08, harness item promise-gate-read-background-tasks):
# when the array is there it decides which jobs run; the transcript only scopes it to this turn.

def test_the_payload_says_the_job_ended_while_the_transcript_lags(tmp_path):
    # The transcript has the launch and no notification yet; the registry already dropped the job.
    silent(stop_on(tmp_path, relapse_rows(), tasks=[]))


def test_the_payload_says_the_job_runs_and_blocks(tmp_path):
    reason = blocked(stop_on(tmp_path, relapse_rows(), tasks=[task(TESTS_JOB, TESTS_WHAT)]))
    assert TESTS_JOB in reason and TESTS_WHAT in reason
    refusals = rows(os.path.join(str(tmp_path), "door-refusals.jsonl"))
    assert [x["reason_class"] for x in refusals] == ["job_pending"]


def test_a_payload_entry_in_a_terminal_status_is_not_pending(tmp_path):
    # A registry entry with a terminal status is never pending.
    silent(stop_on(tmp_path, relapse_rows(),
                   tasks=[task(TESTS_JOB, TESTS_WHAT, status="completed")]))


@pytest.mark.parametrize("kind", ["subagent", "monitor", "workflow", "MCP task"])
def test_a_payload_task_that_is_no_shell_is_the_harness_own_wait(tmp_path, kind):
    silent(stop_on(tmp_path, relapse_rows(), tasks=[task(TESTS_JOB, kind=kind)]))


def test_a_payload_job_launched_before_the_last_prompt_is_not_this_turn(tmp_path):
    rows_ = relapse_rows()[:-1] + [user("et le mail de Sara?"), said(RELAPSE_FINAL)]
    silent(stop_on(tmp_path, rows_, tasks=[task(TESTS_JOB, TESTS_WHAT)]))


def test_a_payload_job_the_transcript_never_launched_is_not_this_turn(tmp_path):
    silent(stop_on(tmp_path, [user(RELAPSE_PROMPT), said(RELAPSE_FINAL)],
                   tasks=[task("bdevsrv01", "dev server")]))


def test_a_payload_description_is_used_when_the_transcript_has_none(tmp_path):
    rows_ = [user(RELAPSE_PROMPT)] + launch("toolu_auto", "b2eimejzl") + [said(RELAPSE_FINAL)]
    reason = blocked(stop_on(tmp_path, rows_, tasks=[task("b2eimejzl", "Run the suite")]))
    assert "\"Run the suite\" (b2eimejzl)" in reason


def test_live_shell_tasks_reads_the_measured_payload():
    payload = {"background_tasks": [
        {"id": "bxpghd8nv", "type": "shell", "status": "running",
         "description": "Sleep for 120 seconds in background", "command": "sleep 120 ;"},
        {"id": "a1", "type": "subagent", "status": "running", "description": "x",
         "agent_type": "lean"}], "session_crons": []}
    assert gate.live_shell_tasks(payload) == {"bxpghd8nv": "Sleep for 120 seconds in background"}
    assert gate.live_shell_tasks({}) is None
    assert gate.live_shell_tasks({"background_tasks": []}) == {}


# Lesson s:dispatched-work-left-unwatched (growth-cockpit reflect, 0.4.0): eight corrections in
# which the user saw a stall, a line down or a silent failure before the model did. Each verbatim
# must arm the stall check.
STALLS = [
    "okay mais ca avance pas notre stuff right?",                                      # 0633
    "but like every lane that works on schedule should have a working auto fix by default. fix all",
    "why did we auto fix? with a worker listening?",                                   # 0366
    "alright attant mon go pour lancer la farm. avec listen actif et otp auto par toi pour activer "
    "le compte une fois que jai register. max 4 comptes/navigateurs a chaque fois. btw je veux plus "
    "jamais qu'un truc qui bloc les bets soit silance. q",                             # 0313
    "pk elle sont down? ses 3 lignes... c'est hyper important! pour les invit on est sure que on "
    "add les recruteurs best startup > texas ceo/cto de bel startup/scalup > sf/ny ?",  # 0224
    "never fail ciletly si ca fail pop terminal pour fix.",                            # 0197
    "gros pb que ce soit down. ca doit pop un terminal de fix si il y a des issue sur cron job",
    "Aussi se serait cool que tu orchestre pour vérifier que tout avances",            # 0059
    "ca avance bien? je trouve quon xp peu",
]
# The other prompts of the 0633 session around it, and the usual short ones.
NOT_STALLS = ["go", "yup go for it. if there is a mob that goes with a familier try to get it ? if "
              "it makes sense", "oublie pas que la prio c'est aussi le stuff", "on wipe jcrois",
              "pas worth non ?", "now?", "daily", "recap", "fin", "ok stop, on reprend demain",
              "il y a aucune sinergie dj bworker et un fami a monter? idem pour autre instances?"]
# The answer the model gave to 0633 (session 4505eb30, 2026-11-11 10:38): measured and right,
# and no watcher left, so the next stall was again the user's to see.
STALL_FINAL = ("Dylan, exact, le stuff n'avance pas. Sur les 48 dernières heures, le journal des "
               "combats ne montre aucun équipement tombé, à part les 11 Marcassins. **Suite "
               "(reco)** : remplacer le bonus stuff par un vrai plan de craft.")
# Its turn at 10:45 the same session: the plan run in the background, the result deferred in
# words no pattern read then.
DEFER_0633 = ("Je recalcule son plan maintenant ; la lecture de l'HDV prend quelques minutes, le "
              "résultat arrive dans ce fil. **Suite (reco)** : à la fin du calcul, je choisis la "
              "zone qui couvre le plus d'ingrédients manquants.")


def read(use, command):
    return [{"type": "assistant", "isSidechain": False, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": use, "name": "Bash", "input": {"command": command}}]}},
            {"type": "user", "isSidechain": False, "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": use, "content": "ok"}]}}]


def tool(use, name, args):
    return [{"type": "assistant", "isSidechain": False, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": use, "name": name, "input": args}]}}]


def stall_rows(extra=()):
    return ([user(STALLS[0])] + read("u1", "head -c 600 data/fights.jsonl")
            + read("u2", "grep -n CM_SETS_WANTED tc/level_plan.py") + list(extra)
            + [said(STALL_FINAL)])


@pytest.mark.parametrize("text", STALLS)
def test_every_verbatim_of_the_lesson_arms_the_stall_check(text):
    assert gate.stall_in(text)


@pytest.mark.parametrize("text", NOT_STALLS)
def test_an_ordinary_prompt_is_no_stall(text):
    assert gate.stall_in(text) is None


@pytest.mark.parametrize("text", STALLS)
def test_replay_each_verbatim_a_turn_with_no_watcher_is_blocked(tmp_path, text):
    r = prompt(tmp_path, "stall", text)
    assert r.returncode == 0, r.stderr
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "watcher" in ctx
    rows_ = [user(text)] + read("u1", "tail -5 cron.log") + [said(STALL_FINAL)]
    reason = blocked(stop_on(tmp_path, rows_, session="stall", final=STALL_FINAL))
    assert "noticed a stall" in reason and reason.endswith("(promise-gate) " + RESEND)


def test_replay_of_0633_is_blocked_and_counted(tmp_path):
    prompt(tmp_path, "s633", STALLS[0])
    reason = blocked(stop_on(tmp_path, stall_rows(), session="s633", final=STALL_FINAL))
    assert "ca avance pas notre stuff" in reason
    refusals = rows(os.path.join(str(tmp_path), "door-refusals.jsonl"))
    assert [(x["door"], x["reason_class"]) for x in refusals] == \
        [("stop_promise_gate", "stall_unwatched")]


@pytest.mark.parametrize("gesture", [
    tool("w1", "Monitor", {"command": "until grep -q done f; do sleep 10; done"}),
    tool("w1", "CronCreate", {"cron": "*/30 * * * *", "prompt": "check gear drops"}),
    tool("w1", "Bash", {"command": "systemctl --user enable --now com.x.gearwatch.timer"}),
    tool("w1", "Bash", {"command": "python3 scripts/autofix.py status"}),
    tool("w1", "Bash", {"command": "python3 cockpit.py channel dofus gear --run 'x'"}),
    tool("w1", "Write", {"file_path": "/r/tc/gear_watch.py", "content": "x"}),
    tool("w1", "Edit", {"file_path": "/r/scripts/stuff_health.py", "old_string": "a",
                        "new_string": "b"}),
])
def test_a_watcher_gesture_since_the_prompt_lets_the_turn_end(tmp_path, gesture):
    prompt(tmp_path, "w", STALLS[0])
    silent(stop_on(tmp_path, stall_rows(gesture), session="w", final=STALL_FINAL))
    # the marker is spent: the next stop of the session is free
    silent(stop_on(tmp_path, [user("go"), said("ok")], session="w", final="ok"))


def test_a_watcher_before_the_stall_prompt_does_not_count(tmp_path):
    prompt(tmp_path, "b", STALLS[0])
    rows_ = ([user("go")] + tool("w0", "Monitor", {"command": "sleep 1"})
             + stall_rows())
    blocked(stop_on(tmp_path, rows_, session="b", final=STALL_FINAL))


def test_a_watcher_of_a_subagent_does_not_count(tmp_path):
    prompt(tmp_path, "sub", STALLS[0])
    side = tool("w9", "Monitor", {"command": "sleep 1"})
    side[0]["isSidechain"] = True
    blocked(stop_on(tmp_path, stall_rows(side), session="sub", final=STALL_FINAL))


def test_the_next_ordinary_prompt_clears_the_stall(tmp_path):
    prompt(tmp_path, "c", STALLS[0])
    r = prompt(tmp_path, "c", "yup go for it")
    assert r.stdout.strip() == ""
    silent(stop_on(tmp_path, [user("yup go for it"), said("fait")], session="c", final="fait"))


def test_an_unarmed_session_never_hears_of_a_stall(tmp_path):
    silent(stop_on(tmp_path, stall_rows(), session="none", final=STALL_FINAL))


def test_the_stall_block_caps_like_the_others(tmp_path):
    prompt(tmp_path, "cap", STALLS[0])
    for active in (False, True, True):
        blocked(stop_on(tmp_path, stall_rows(), session="cap", final=STALL_FINAL, active=active))
    r = stop_on(tmp_path, stall_rows(), session="cap", final=STALL_FINAL, active=True)
    assert "systemMessage" in json.loads(r.stdout)


def test_the_0633_deferral_is_read_now(tmp_path):
    assert gate.deferral_in(DEFER_0633, CFG)
    rows_ = ([user("yup go for it. if there is a mob that goes with a familier try to get it ?")]
             + launch("toolu_glad", "bbazjejla", "Rerun glad plan with longer timeout")
             + [said(DEFER_0633)])
    reason = blocked(stop_on(tmp_path, rows_, session="d633", final=DEFER_0633))
    assert "bbazjejla" in reason


# ---- 0.5.0: what the user did not type, or only quoted, is no stall (2026-11-14) ---------------
TASK_NOTIFICATION = ("<task-notification>\n<task-id>bq1</task-id>\n<status>completed</status>\n"
                     "<summary>Background agent finished: the stall is still down</summary>\n"
                     "</task-notification>")
# The handoff prompt that armed the session it started.
HANDOFF = ('read a <task-notification> system event (a background agent finishing) as "the user '
           'noticed a stall" four times\nAutofix pops per week since 09-21: 64, 81\n'
           '`scripts/autofix.py`')


def gate_stall(tmp_path, session):
    """True when a Stop of `session` with no watcher in its transcript is held for a stall."""
    r = run_py(NAME, SCRIPT, {"hook_event_name": "Stop", "stop_hook_active": False,
                              "session_id": session, "last_assistant_message": "ok",
                              "transcript_path": str(tmp_path / "none.jsonl")}, tmp_path)
    return r.stdout.strip() != ""


@pytest.mark.parametrize("text", [TASK_NOTIFICATION, "  " + TASK_NOTIFICATION,
                                  "<system-reminder>the stall is down</system-reminder>",
                                  "<local-command-stdout>auto fix</local-command-stdout>",
                                  "Stop hook feedback:\nThe user noticed a stall before you did",
                                  "Base directory for this skill: /x\n\nnever fail silently"])
def test_a_task_notification_arms_nothing(tmp_path, text):
    r = prompt(tmp_path, "tn", text)
    assert (r.returncode, r.stdout.strip()) == (0, ""), r.stderr
    assert not gate_stall(tmp_path, "tn")


def test_a_task_notification_does_not_clear_an_armed_stall(tmp_path):
    prompt(tmp_path, "tn2", STALLS[0])
    prompt(tmp_path, "tn2", TASK_NOTIFICATION)
    assert gate_stall(tmp_path, "tn2")


def test_a_task_notification_does_not_clear_an_armed_target(tmp_path):
    prompt(tmp_path, "tn3", "never stop")
    prompt(tmp_path, "tn3", TASK_NOTIFICATION)
    path = make_transcript(tmp_path, PROMISE)
    blocked(run_py(NAME, SCRIPT, stop(path, "tn3"), tmp_path))


def test_the_handoff_that_quoted_a_stall_arms_nothing(tmp_path):
    assert gate.stall_in(HANDOFF) is None
    r = prompt(tmp_path, "ho", HANDOFF)
    assert (r.returncode, r.stdout.strip()) == (0, "")
    assert not gate_stall(tmp_path, "ho")


@pytest.mark.parametrize("text", [
    "Autofix pops per week since 09-21: 64, 81",
    "run python3 scripts/autofix.py status",
    "the autofix: row is green",
    "> the user said: ca avance pas",
    "```\nstalled\n```\nwhat does this log say?",
    "he wrote “it is still down” last week, read the doc",
])
def test_a_quoted_or_named_stall_word_is_no_stall(text):
    assert gate.stall_in(text) is None


@pytest.mark.parametrize("text", ["we need an auto fix here", "an auto-fix by default",
                                  "auto - fix it", "pourquoi ca avance pas? `x.py` tourne"])
def test_the_unquoted_ask_still_arms(text):
    assert gate.stall_in(text)
