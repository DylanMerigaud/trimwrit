"""The bare-id-gate plugin, run the way Claude Code runs it: a payload on stdin, --home on argv.

SHOWN_BARE replays closing blocks his corrections answered, names of third parties neutralised;
the correction each one drew is quoted above it, verbatim. CFG carries the families his own
configuration adds (`c` codes of his review queue, `T` tasks of a plan, `R` rules of trimwrit)."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

from gates._hook import ROOT, eval_cases, plugin, rows, run_py, write_config

NAME = "bare-id-gate"
SCRIPT = "bare_id_gate.py"


def load_gate():
    path = os.path.join(plugin(NAME), "scripts", SCRIPT)
    sys.path.insert(0, plugin(NAME))
    try:
        spec = importlib.util.spec_from_file_location("bare_id_gate_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(plugin(NAME))
    return mod


gate = load_gate()
EXTRA = ["c\\d{1,3}", "T\\d{1,2}", "R\\d{4}"]
CFG = {"id_patterns_extra": EXTRA, "reason_suffix": ""}
BARE = {"id_patterns_extra": [], "reason_suffix": ""}
MANY = "Cinq tests (intake-004, intake-195, intake-069) restent bloqués sous leur lot."
QUEUE = ("Ok, on s'arrête là.\n\n"
         "- **Ferme de comptes :** 40 comptes actifs, rien à faire tant que la demande reste "
         "basse.\n"
         "- **Disque :** 95 Go libres, lu toutes les 30 min.\n"
         "- **Reste à toi :**\n"
         "  - **c37 :** 5 lignes dormantes.\n"
         "  - **c43 :** le créneau d'appel de Sam, chez Acme (Product Engineer).\n\n"
         "**Conclusion :** tout est réglé, rien ne tourne pour toi.\n"
         "**Prochaine étape (recommandé) :** c37 et c43 quand tu veux.")

SHOWN_BARE = [
    # "c37 et c43 c quoi"
    (QUEUE, CFG, ["c37", "c43"]),
    # "a- intake-068 jamais afficher les trucs comme ca pour moi ej comprend rien. ajouter dans
    # les rules." (the block as the bare model rewrites it when the case replays it)
    ("**BLOQUE**\n\n| Item | Cause | Unblocks |\n|---|---|---|\n"
     "| intake-068: lab field test (bet labwatch), 38/60 sent | Mailbox cap limits sending to 8 "
     "a day | 09-04 |\n"
     "| dig-0003: dig on cockpit rows that never log | Waiting for your verdict | No date |",
     BARE, ["intake-068", "dig-0003"]),
    # "intake 004 c'est quel bet?" (a list of ids in one parenthesis names only its first)
    (MANY, BARE, ["intake-195", "intake-069"]),
    # Shapes measured on his other messages.
    ("- **Fix:** commit `b6bef2fd` (merged to main, hub caught up) makes the render count it.",
     BARE, ["b6bef2fd"]),
    ("Deployed: main `0f64a4bd`, hub synced, 137 tests green.", BARE, ["0f64a4bd"]),
    ("Commits sur main : `6741101b..788f6dfc`. Pas d'envoi de mail.", BARE,
     ["6741101b", "788f6dfc"]),
    ("**Conclusion:** reoon-45 is live and rostered.", BARE, ["reoon-45"]),
    ("- **c8, le papier :** l'agent qui le bascule vers un papier tourne.", CFG, ["c8"]),
    ("J'ai sorti c43 de ta liste (`sched answer c43 --skip`).", CFG, ["c43"]),
    ("T11 est livré sur main (`65bea2e1`, journal `dd6e14c4`).", CFG, ["T11"]),
    ("The rule: R0042 in the global CLAUDE.md is rewritten through trimwrit.", CFG, ["R0042"]),
    ("| Session | Statut |\n|---|---|\n| 5e4807fd-f93b-458c-b36e-4c956bfed603 | close |", BARE,
     ["5e4807fd-f93b-458c-b36e-4c956bfed603"]),
    ("Tests intake-910 et intake-354 (SBIR et préférences) tournent seuls.", BARE,
     ["intake-910", "intake-354"]),
    # A parenthesis with no name before it is not the name it follows.
    ("- (c37) les cinq lignes dormantes.", CFG, ["c37"]),
    ("Le plan (T11, T12 et T13) est livré.", CFG, ["T12", "T13"]),
    # An id may sit in a parenthesis on one line and stand bare on the next.
    ("Les cinq lignes dormantes (c37) attendent.\nc37 se règle d'un mot.", CFG, ["c37"]),
]

NOT_SHOWN_BARE = [
    # The thing named, the id after it in parentheses.
    "**Reste à toi :** réveiller ou retirer les 5 lignes dormantes (c37), et dire oui ou non au "
    "créneau d'appel proposé par Sam, chez Acme (c43).",
    "Merged on main (b6bef2fd), hub caught up.",
    "Le test terrain du bet labwatch (intake-068) est bloqué par le plafond de la boîte.",
    "Poussé sur main (`87b31564..aae26054`), hub synchronisé.",
    "La porte de relance (commit b4ee70e4, avec ses tests) est en place.",
    "Le plan (T11) est livré, la revue (T12) aussi.",
    # A command needs the id: it stays inside its code span.
    "Pour réveiller les lignes dormantes, `python3 -m sched answer c37 --go`.",
    "```\ngit show b6bef2fd\nintake-068 | 38/60\n```\nThe fix is on main.",
    # A label the message numbers itself, from 1.
    "**DONE**\n- a1. Fixed the import timeout.\n- a2. Synced the hub.\n\nRecommendation: a1.",
    "**IL FAUT TOI**\n- **c1** Rien.\n- **c2** Rien non plus.",
    "1. **T1:** harvest the roster.\n2. **T2:** score it.\n\nT2 runs after T1.",
    # Not an id: a standard, a version, a date, a word with digits, a path, a link, an address.
    "Hashes are SHA-256 and dates ISO-8601; the COVID-19 dataset is public.",
    "The pre-2025 cuts and the FY-2026 budget are in the filing.",
    "See [DV-2026 instructions](https://travel.state.gov/content/dv-2026.html).",
    "Read `career/FIELD-TESTS.jsonl` and `scripts/test_kill_bet.py` for the rest.",
    "The page https://github.com/acme/repo/commit/b6bef2fd shows the diff.",
    "The handle @a1b2c3d4e5 and the 2026-10-21 run are not ids.",
    "The colour #3bb2702c and the number 20261021 are not ids.",
    "A c37 code needs the config, as does T11: with no family added they pass.",
    # Quoted, block quoted, a draft for a third party.
    "Tu m'as écrit « c37 et c43 c quoi », alors je les nomme.",
    "> Hi Sam, the build at b6bef2fd is live, intake-068 is closed.\n\nDraft ready.",
    # A finished report with no id.
    "Fait : 4 champs remplis, récapitulatif lu, rien en vol.",
]


@pytest.mark.parametrize("text,cfg,tokens", SHOWN_BARE)
def test_a_bare_id_is_found(text, cfg, tokens):
    assert [tok for _, tok in gate.bare_ids(text, cfg)] == tokens, text


@pytest.mark.parametrize("text", NOT_SHOWN_BARE)
def test_what_is_not_a_bare_id_passes(text):
    cfg = BARE if "needs the config" in text else CFG
    assert gate.bare_ids(text, cfg) == [], (text, gate.bare_ids(text, cfg))


def test_the_queue_codes_need_their_family():
    assert gate.bare_ids(QUEUE, BARE) == []
    assert [t for _, t in gate.bare_ids(QUEUE, CFG)] == ["c37", "c43"]


def test_a_json_result_is_not_prose():
    assert gate.bare_ids('{"pour_toi": "c37", "commit": "b6bef2fd"}', CFG) == []


def test_offsets_survive_the_blanking():
    text = "`a b` (`x y`) then `cmd intake-068` and c37\n> quoted b6bef2fd\nok"
    out = gate.prose(text)
    assert len(out) == len(text) and out.count("\n") == text.count("\n")
    assert [t for _, t in gate.bare_ids(text, CFG)] == ["c37"]


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


def blocked(r):
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout.strip())
    assert body["decision"] == "block"
    return body["reason"]


TICKET = SHOWN_BARE[1][0]


def test_a_bare_key_blocks_until_the_chain_cap(tmp_path):
    path = make_transcript(tmp_path, TICKET)
    outs = [run_py(NAME, SCRIPT, stop(path, "s", active=i > 0), tmp_path).stdout.strip()
            for i in range(4)]
    for out in outs[:3]:
        body = json.loads(out)
        assert body["decision"] == "block"
        assert "\"intake-068\", \"dig-0003\"" in body["reason"]
        assert "in parentheses" in body["reason"]
        assert body["reason"].endswith("(bare-id-gate)")
    capped = json.loads(outs[3])
    assert "decision" not in capped and "did not clear it" in capped["systemMessage"]
    got = rows(os.path.join(str(tmp_path), "hook-health.jsonl"))
    assert any(x["hook"] == SCRIPT and x["outcome"] == "cap" for x in got)
    refusals = rows(os.path.join(str(tmp_path), "door-refusals.jsonl"))
    assert [(x["door"], x["event"], x["reason_class"]) for x in refusals] == \
        [("bare_id_gate", "Stop", "internal_id_bare")] * 3


def test_a_clean_turn_is_silent(tmp_path):
    path = make_transcript(tmp_path, "Merged on main (b6bef2fd). Nothing waits on you.")
    r = run_py(NAME, SCRIPT, stop(path, "c"), tmp_path)
    assert (r.returncode, r.stdout.strip()) == (0, "")


def test_subagent_stop_is_ignored_and_a_re_answer_is_checked(tmp_path):
    path = make_transcript(tmp_path, TICKET)
    sub = run_py(NAME, SCRIPT, stop(path, "r", event="SubagentStop"), tmp_path)
    assert (sub.returncode, sub.stdout.strip()) == (0, "")
    blocked(run_py(NAME, SCRIPT, stop(path, "r", active=True), tmp_path))


def test_last_assistant_message_wins_over_a_lagging_transcript(tmp_path):
    path = make_transcript(tmp_path, "Fait, rien en vol.")
    blocked(run_py(NAME, SCRIPT, stop(path, "lag", message=TICKET), tmp_path))


def test_the_queue_codes_come_from_the_config(tmp_path):
    path = make_transcript(tmp_path, QUEUE)
    assert run_py(NAME, SCRIPT, stop(path, "q"), tmp_path).stdout.strip() == ""
    write_config(tmp_path, {NAME: {"id_patterns_extra": EXTRA}})
    assert "\"c37\", \"c43\"" in blocked(run_py(NAME, SCRIPT, stop(path, "q2"), tmp_path))


@pytest.mark.parametrize("section", [
    {"id_patterns_extra": ["("]},
    {"id_patterns_extra": "not a list"},
    {"id_patterns_extra": [7]},
    {"ids_allowed": ["c37"]},
])
def test_a_bad_extra_or_key_is_a_crash_row_and_exit_0(tmp_path, section):
    write_config(tmp_path, {NAME: section})
    path = make_transcript(tmp_path, "Fait.")
    r = run_py(NAME, SCRIPT, stop(path, "bad"), tmp_path)
    assert r.returncode == 0
    assert "UNGUARDED" in json.loads(r.stdout)["systemMessage"]
    got = rows(os.path.join(str(tmp_path), "hook-health.jsonl"))
    assert [(x["hook"], x["event"], x["outcome"]) for x in got] == [(SCRIPT, "Stop", "crash")]


def test_reason_suffix_ends_the_block_reason(tmp_path):
    write_config(tmp_path, {NAME: {"reason_suffix": "See docs/naming.md."}})
    path = make_transcript(tmp_path, TICKET)
    reason = blocked(run_py(NAME, SCRIPT, stop(path, "sfx"), tmp_path))
    assert reason.endswith("(bare-id-gate) See docs/naming.md.")


def test_no_environment_variable_lifts_it(tmp_path):
    path = make_transcript(tmp_path, TICKET)
    for var in ("COCKPIT_NO_STOP_GATE", "BARE_ID_GATE_OFF", "CLAUDE_ALLOW_BARE_ID"):
        blocked(run_py(NAME, SCRIPT, stop(path, "env-" + var), tmp_path, env={var: "1"}))


# ---- the eval case: the grader must separate the plugin arm from the baseline arm -------------
# Excerpts of the three answers the baseline arm gave the case's prompt (Opus 5.5, no plugin),
# and of what the plugin arm ended on once the hook had refused, 2026-10-21.
BARE_ANSWERS = [
    "The roster import timeout is fixed. The fix is commit 4f2a9c1e, merged on main, and all 81 "
    "tests pass.\n\nTwo items in your queue are waiting on you:\n\n**INT-0438: call with Noah "
    "Brandt (head of engineering, Eve)**\nHe proposed Thursday at 15:00 Paris time, which is "
    "tomorrow (Oct 22).\n- `answer INT-0438 --go` accepts the call.\n- `answer INT-0438 --skip` "
    "declines it.",
    "The roster import timeout is fixed. The fix is commit `4f2a9c1e`, merged on main, and all "
    "81 tests pass.\n\n**INT-0412: dental-lab field test**\n38 of 60 first mails went out, then "
    "sending stopped at the mailbox's daily cap of 38. 22 mails remain.",
    "**INT-0412: dental-lab field test**\n38 of the 60 first mails went out. Sending stopped "
    "because the mailbox hit its daily cap of 38, and 22 mails are still waiting.",
]
RE_ANSWERS = [
    "**Call with Noah Brandt, head of engineering at Eve (INT-0438)**\nHe proposes Thursday at "
    "15:00 Paris time, which is tomorrow, Oct 22.\n- Yes: `answer INT-0438 --go`\n- No: "
    "`answer INT-0438 --skip`\n\n**Dental-lab field test (INT-0412)**\n38 of the 60 first mails "
    "went out before the sending mailbox hit its daily cap of 38.",
    "**Call with Noah Brandt, head of engineering at Eve**\nHe proposed Thursday at 15:00 Paris "
    "time. That's tomorrow, 22 Oct. This item is in your queue (INT-0438).\n- Yes: "
    "`answer INT-0438 --go`",
    "Please answer the dental-lab test (INT-0412) in a short text message, for example "
    "\"dental-lab: raise limit\" or \"dental-lab: second mailbox\". Until you do, the 22 mails "
    "stay unsent.",
    "**The roster import script no longer times out.** The fix is merged on main and all 81 "
    "tests pass. You don't need to do anything for it.",
]
# Where a regex on the raw message cannot follow the hook, which first drops fences, quoted spans
# and block quotes, reads a JSON result as no prose, spares a label numbered from 1, and wants a
# word of prose before the parenthesis.
GAP_REFUSED_UNSEEN = [
    "- (INT-0412) the dental-lab test.",
    MANY,
]
GAP_ALLOWED_SEEN = [
    "```\nINT-0412 | 38/60\n```\nThe dental-lab test is stuck.",
    "```\ngit show b6bef2fd\nintake-068 | 38/60\n```\nThe fix is on main.",
    "> Hi Sam, the build at b6bef2fd is live, intake-068 is closed.\n\nDraft ready.",
    "Tu m'as écrit \"INT-0412 c quoi\", alors je le nomme.",
    "> the build at 4f2a9c1e is live\n\nDraft ready.",
    '{"item": "INT-0412"}',
]


def case():
    sys.path.insert(0, ROOT)
    found = eval_cases(NAME)
    assert [os.path.basename(c.path) for c in found] == ["id-shown-bare"]
    return found[0]


def test_eval_graders_are_proven():
    from trimwrit import check
    assert [p for p in check.check_case(case())] == []


def test_eval_prompt_carries_no_personal_data():
    text = case().prompt
    assert "@" not in text
    assert not re.search(r"\d[\d .-]{6,}\d", text)


def grader_pattern():
    """The forbid grader, rebuilt from the gate's default families: on one line, a run of
    characters that are no parenthesis and no backtick, closed parenthesis groups or whole code
    spans (a command span is skipped whole, the ids inside it unread), then an id, bare or in a
    one-token code span (a commit range `a..b` included)."""
    core = "(?:" + "|".join(src for _, src in gate.ID_FAMILIES) + ")"
    ident = "(?:`" + core + r"(?:\.\.\.?" + core + ")?`|" + gate.LEFT + core + ")" + gate.RIGHT
    return r"(?:^|\n)(?:[^(\n`]|\([^()\n]*\)|`[^`\n]*`)*?" + ident


def spec(name):
    return {g["name"]: g for g in case().graders}[name]


def forbid():
    return re.compile(spec("forbids-id-shown-bare")["pattern"])


def test_forbid_grader_is_built_from_the_gate():
    # A family, a boundary or an exclusion in the gate that the grader lacks would score a
    # refused id as a pass.
    assert spec("forbids-id-shown-bare")["pattern"] == grader_pattern()


GRADER_HITS = [t for t, cfg, _ in SHOWN_BARE if cfg is BARE and t not in GAP_REFUSED_UNSEEN] \
    + BARE_ANSWERS
GRADER_MISSES = [t for t in NOT_SHOWN_BARE if t not in GAP_ALLOWED_SEEN] + RE_ANSWERS


@pytest.mark.parametrize("text", GRADER_HITS)
def test_forbid_grader_hits_what_the_gate_refuses(text):
    assert gate.bare_ids(text, BARE), text
    assert forbid().search(text), text


@pytest.mark.parametrize("text", GRADER_MISSES)
def test_forbid_grader_spares_what_the_gate_allows(text):
    assert not gate.bare_ids(text, BARE), (text, gate.bare_ids(text, BARE))
    assert not forbid().search(text), text


def test_the_grader_gaps_are_the_known_ones():
    for text in GAP_REFUSED_UNSEEN:
        assert gate.bare_ids(text, BARE) and not forbid().search(text), text
    for text in GAP_ALLOWED_SEEN:
        assert not gate.bare_ids(text, BARE) and forbid().search(text), text


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_javascript_reads_the_grader_like_python():
    # `claude plugin eval` compiles the pattern as a JavaScript RegExp, whose \w and \b are
    # ASCII; the proof above runs Python's re.
    texts = GRADER_HITS + GRADER_MISSES + GAP_REFUSED_UNSEEN + GAP_ALLOWED_SEEN
    script = ("const d = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
              "const r = new RegExp(d.p);"
              "process.stdout.write(JSON.stringify(d.t.map((t) => r.test(t))));")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60,
                         input=json.dumps({"p": spec("forbids-id-shown-bare")["pattern"],
                                           "t": texts}))
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [bool(forbid().search(t)) for t in texts]
