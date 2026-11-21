"""The choice-gate plugin, run the way Claude Code runs it: a payload on stdin, --home on argv.

The replay of lesson s:session-feedback-dropped-not-applied: every one of its eleven corrections
(verbatim below) is recorded in the session ledger and handed back after a compaction, and the
answer that dropped the choice again is refused at Stop."""
import json
import re

import pytest

from gates._hook import RESEND, eval_cases, run_py

NAME = "choice-gate"
SCRIPT = "choice_gate.py"

# The lesson's events, verbatim (growth-cockpit .trimwrit/ledger.jsonl, id: text).
EVENTS = {
    "0745": "on prefera aldi pour les bouteilles que carrefour.  riselsing + pino noir i said. "
            "peut etre 1 blanc alsace et 1 rouge. je prendrais plus de vin d'aldi , idealment "
            "tout le vin aldi ou lidl",
    "0704": "t'as update ? on a encore bcp de noix de cagout alors quej ai dit un melange de "
            "tout?\ngrimbergent tjr en unite vs grande boutaille de ambree? \nfoie gras c pour le "
            "vin d'honneur ou le repas?",
    "0698": "combien ca me fait perdre de faire le max max de courses pour ce samedi 21 ? et "
            "eviter le max de courses le vendedi prochain... du moins les aldi, lidl etc...? "
            "Remove champomy, hedgehog Bloc de foie gras de canard Mets des Rois => pas autre "
            "que boite? frais? panetonne jai dit cosco Coca-Cola, pack de 8 L en bouteilles => "
            "pas compris c 4 bouteille s de 2l? Thé glacé pêche River => iced "
            "tea. de maniere generale flag les trucs que ta prix moins bon, sous marque. "
            "Tire-bouchon limonadier => on en as donc non. jus clementine => non jus d'orange.",
    "0632": "non ca me vas pas gingy. celui que jai envoyé c pas bon?",
    "0517": "samedi 28 on avait aussi dit eglise a cote de st eugene. de austerliz on peut aller "
            "a oberkampft poser les sacs et on les reprends le soir?",
    "0343": "orchestrateur du papier => ON AVAIT PAS DIT DE LE LIMITé? kill le ptin. Ferme "
            "Apollo=> hiberne ou fou une ligne weekly qui check si new domain mais je vais pas "
            "en buy, de toute facon 0 interet.",
    "0168": "ok et quels piste d'amelioration on a ? stats des bets. stats de cold. j'ai en tete "
            "de send bientot 1 mail a chaque boite yc/sf hyper scalup,moins de 20 employés",
    "0122": "les tts doivent rester coherant en fonction de l'ennemeie, la meme voix pour le "
            "meme enemie. pour le reste des feature tu pense finir quand?",
    "0116": "il y a tjr les glitch noir, les load prennent trop de temps... tu pense que tout "
            "polish ca prend cb de temps?",
    "0097": "it's really buggy the arrows make sure to fix them like i told you before the v1 "
            "arrows erre great.",
    "0699": "Noix de cajou Alesto, 200 => ok mais il faut differentes graines (noix, noisette, "
            "noix du bresil...) ajoute lefe blanche pour papa",
}


def prompt(home, session, text):
    return run_py(NAME, SCRIPT, {"hook_event_name": "UserPromptSubmit", "session_id": session,
                                 "prompt": text}, home, "--prompt")


def session_start(home, session, source):
    return run_py(NAME, SCRIPT, {"hook_event_name": "SessionStart", "session_id": session,
                                 "source": source}, home, "--session")


def stop(home, session, message, active=False, event="Stop"):
    return run_py(NAME, SCRIPT, {"hook_event_name": event, "session_id": session,
                                 "stop_hook_active": active, "last_assistant_message": message,
                                 "transcript_path": ""}, home)


def blocked(r):
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout.strip())
    assert body["decision"] == "block"
    assert body["reason"].endswith(RESEND)
    return body["reason"]


def passed(r):
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", r.stdout


@pytest.mark.parametrize("cid", sorted(EVENTS))
def test_every_event_is_recorded_and_survives_a_compaction(tmp_path, cid):
    """Changed by the door: the correction is in the ledger and comes back after a compaction,
    verbatim, whatever the summary kept."""
    r = prompt(tmp_path, cid, EVENTS[cid])
    assert r.returncode == 0, r.stderr
    r = session_start(tmp_path, cid, "compact")
    assert r.returncode == 0, r.stderr
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "choice-gate" in ctx
    parts = [" ".join(p.split()) for p in re.split(r"(?<=[.!?])\s+|\n+", EVENTS[cid])]
    assert any(p and p in ctx for p in parts), ctx


def test_startup_hands_nothing_back(tmp_path):
    prompt(tmp_path, "s", EVENTS["0745"])
    passed(session_start(tmp_path, "s", "startup"))


def test_0698_champomy_kept_after_remove_is_refused(tmp_path):
    prompt(tmp_path, "s", EVENTS["0698"])
    reason = blocked(stop(tmp_path, "s", "Liste :\n- Champomy, 6 bouteilles, 11,94 EUR\n"
                                         "- Coca-Cola 2 L x4"))
    assert "Remove champomy" in reason


def test_0698_champomy_said_removed_passes(tmp_path):
    prompt(tmp_path, "s", EVENTS["0698"])
    passed(stop(tmp_path, "s", "Fait. Champomy retire, 0 EUR.\n- Panettone : Costco\n"
                               "- Coca-Cola 2 L x4"))


def test_0698_arrow_no_excludes_the_item(tmp_path):
    prompt(tmp_path, "s", EVENTS["0698"])
    blocked(stop(tmp_path, "s", "- Jus clementine Carrefour, 4 L, 7,96 EUR"))


def test_0745_wine_at_carrefour_after_preferring_aldi_is_refused(tmp_path):
    prompt(tmp_path, "s", EVENTS["0745"])
    reason = blocked(stop(tmp_path, "s", "Vin :\n- 10 bouteilles de Riesling, drive Carrefour, "
                                         "59,90 EUR\n- 6 Pinot Noir Aldi"))
    assert "carrefour" in reason


def test_0745_carrefour_for_something_else_passes(tmp_path):
    prompt(tmp_path, "s", EVENTS["0745"])
    passed(stop(tmp_path, "s", "Vin : 6 Riesling et 6 Pinot Noir, tout chez Aldi.\n"
                               "- Baguettes tradition, Carrefour, 12 x 1,10 EUR"))


def test_0745_repeat_unapplied_is_refused(tmp_path):
    prompt(tmp_path, "s", EVENTS["0745"])
    reason = blocked(stop(tmp_path, "s", "Page a jour : 23 champagnes, 6 Macon chez Aldi."))
    assert "i said" in reason


def test_0704_mix_of_nuts_repeat_unapplied_is_refused(tmp_path):
    prompt(tmp_path, "s", EVENTS["0704"])
    blocked(stop(tmp_path, "s", "1. Noix de cajou : 4 sachets Alesto, 8,76 EUR"))
    passed(stop(tmp_path, "s", "1. Noix : le mélange est fait, cajou, noisettes, amandes."))


def test_0517_church_repeat_unapplied_is_refused(tmp_path):
    prompt(tmp_path, "s", EVENTS["0517"])
    blocked(stop(tmp_path, "s", "Samedi 28 : Austerlitz puis Oberkampf pour les sacs."))
    passed(stop(tmp_path, "s", "Samedi 28 : l'église à côté de "
                               "Saint-Eugène, puis Oberkampf."))


def test_0343_limit_repeat_unapplied_is_refused(tmp_path):
    prompt(tmp_path, "s", EVENTS["0343"])
    blocked(stop(tmp_path, "s", "Apollo est ferme."))
    passed(stop(tmp_path, "s", "L'orchestrateur est tue ; il depassait sa limite."))


def test_0097_one_typo_word_does_not_block_a_good_answer(tmp_path):
    prompt(tmp_path, "s", EVENTS["0097"])
    passed(stop(tmp_path, "s", "Fixed: the arrows are back to the v1 behaviour."))


def test_a_repeat_arms_only_its_own_turn(tmp_path):
    prompt(tmp_path, "s", EVENTS["0517"])
    prompt(tmp_path, "s", "ok merci")
    passed(stop(tmp_path, "s", "Avec plaisir."))


def test_a_later_mention_takes_an_exclusion_back(tmp_path):
    prompt(tmp_path, "s", "non pas de bassine ni bouteilles.")
    prompt(tmp_path, "s", "9 bouteilles de vin? on peut pas checker avec aldi?")
    passed(stop(tmp_path, "s", "- 9 bouteilles de vin Aldi, 47,91 EUR"))


def test_a_question_rules_nothing(tmp_path):
    prompt(tmp_path, "s", "il y a pas de blinis?")
    passed(stop(tmp_path, "s", "- Blinis : 11 sachets"))


def test_repeat_prompt_hands_the_ledger_back(tmp_path):
    prompt(tmp_path, "s", "Remove champomy.")
    r = prompt(tmp_path, "s", EVENTS["0745"])
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Remove champomy." in ctx and "riselsing + pino noir i said" in ctx


def test_harness_text_is_never_a_choice(tmp_path):
    prompt(tmp_path, "s", "Stop hook feedback: Remove champomy")
    passed(session_start(tmp_path, "s", "compact"))
    passed(stop(tmp_path, "s", "- Champomy x6, 11,94 EUR"))


def test_subagent_stop_is_ignored(tmp_path):
    prompt(tmp_path, "s", "Remove champomy.")
    passed(stop(tmp_path, "s", "- Champomy x6, 11,94 EUR", event="SubagentStop"))


def test_chain_cap_lets_the_turn_end(tmp_path):
    prompt(tmp_path, "s", "Remove champomy.")
    msg = "- Champomy x6, 11,94 EUR"
    blocked(stop(tmp_path, "s", msg))
    blocked(stop(tmp_path, "s", msg, active=True))
    blocked(stop(tmp_path, "s", msg, active=True))
    r = stop(tmp_path, "s", msg, active=True)
    assert r.returncode == 0 and "decision" not in r.stdout


def test_eval_case_graders_bite():
    from trimwrit.check import check_case
    for case in eval_cases(NAME):
        assert check_case(case) == [], case.path
