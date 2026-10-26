"""The handback-gate plugin, run the way Claude Code runs it: a payload on stdin, --home on argv.

HANDED_BACK replays the closing blocks his corrections answered, cut to the paragraph that hands
the gesture back, names and identifiers neutralised; the correction each one drew is quoted above
it, verbatim."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

from gates._hook import ROOT, eval_cases, plugin, rows, run_py, write_config

NAME = "handback-gate"
SCRIPT = "handback_gate.py"


def load_gate():
    path = os.path.join(plugin(NAME), "scripts", SCRIPT)
    sys.path.insert(0, plugin(NAME))
    try:
        spec = importlib.util.spec_from_file_location("handback_gate_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(plugin(NAME))
    return mod


gate = load_gate()
CFG = {"user_names": ["Dylan"], "handback_patterns_extra": [], "reason_suffix": ""}
BARE = {"user_names": [], "handback_patterns_extra": [], "reason_suffix": ""}
FORM = ("**À toi de décider :** c'est ton nom, ton email et ton téléphone qui partent chez un "
        "vendeur, et il s'en servira pour te démarcher.\n\n"
        "- **Option 1 (reco) :** tu remplis les 4 champs toi-même dans l'onglet ouvert, puis tu "
        "m'écris « go ». J'enchaîne jusqu'au récapitulatif et je m'arrête avant de payer.\n"
        "- **Option 2 :** tu me dis de les remplir avec ton email et ton numéro.")

HANDED_BACK = [
    # "fait tout toi meme, pour le numero met un faux"
    FORM,
    # "sealfile => tu peux pas le creer en full auto sans me demander?"
    "**Next step:** you only need to reply \"go acme\" and paste the offer's payment link.",
    # "je vais pas faire l'ethernest c'est pas une otpion."
    "- Conclusion : le geste physique (brancher l'adaptateur Ethernet) reste à toi. Il "
    "t'arrivera sur l'interface, pas dans un message de session.",
    # "mais tu peux faire tout les tests toi meme via le mac http://.../login marche mtn"
    "**Réessaye maintenant**\n- Sur le Mac ou sur le téléphone : `http://host:8790/login`. Cette "
    "fois ça doit charger.\n- Colle le jeton de l'appareil, déjà dans le presse-papier.",
    # "fait tou toi meme"
    "Pour savoir si ça marche, tape `curl -s -o /dev/null -w \"%{http_code}\" http://host:8790/`. "
    "Un 401 veut dire que le tailnet passe, un échec veut dire qu'il est encore bloqué.",
    # "vraiment prend le controle. fait le toi meme"
    "Le plus rapide est que tu imprimes toi-même depuis tes onglets déjà connectés :\n"
    "1. **Impôts :** l'historique de tes prélèvements. Fais cmd+P et enregistre en PDF.",
    # "tick les toi meme"
    "The review is submitted. Dylan can tick the 23 boxes manually on the JOSS interface if "
    "needed.",
    # "wdym les tick? fait le non ?"
    "The review is live. Dylan can manually tick the 23 boxes on his checklist if needed, but "
    "the core review is submitted.",
    # "wdym? je doit creer un compte telegram /fait le toi meme si pas possible on avise"
    "Ce qui sera à toi quand le rapport du build arrive : créer le bot dans BotFather, stocker "
    "le token dans le keychain, envoyer `/start` au bot, puis coller le cookie de l'interface "
    "sur le téléphone.",
    # "ok mais att pk tu croyais que ca me necessaitait?"
    "- **Stefan :** personne n'a répondu. « 6 jours par semaine », c'est à toi de juger.",
    # Shapes measured on his other messages.
    "**Reco :** vérifie dans ta boîte d'envoi si la relance est bien partie. Si oui, logue-la.",
    "**À vérifier de ton côté :** ton profil affiche peut-être encore le 90 %. Je n'ai pas "
    "ouvert la page.",
    "Prochaine étape recommandée : lance toi-même `make deploy` dans ton terminal.",
    "Recommendation: a1. Say \"go\" once the tunnel is up, or run the command yourself.",
    "Il me faut de toi : le lien de paiement, collé ici.",
    "Next step: you'll need to create the bot and store its token in the keychain.",
    # "why those needs me", on three reminders a scheduler composed for his phone, hour after
    # hour: the takeover question and a negation that reports a fact before it.
    "Sam Doe (Acme Tree Care) a repondu a ton cold, rien n'est parti: tu t'en occupes ?",
    "Alex Roe attend ta reponse, tu t'en occupes ?",
    "La relecture est prete, tu le prends ?",
    "Rien n'est parti: colle le jeton dans ton terminal.",
    "Tu peux t'en charger quand tu as un moment.",
    "Je te laisse trancher entre les deux devis.",
    "Can you take care of the payment link before Friday?",
    "I'll leave the rest to you.",
    # Shapes the bare model wrote on the eval case, and one its re-answer moved to.
    "1. **Do it yourself (about a minute).** The form is still open and empty in your tab.",
    "**What I need from you:** a yes or no, plus the phone number you're happy to give them.",
]

NOT_HANDED_BACK = [
    # The class is named in the paragraph.
    "**À faire de ton côté :** créer la micro-entreprise. Je ne peux pas le faire à ta place : "
    "c'est une immatriculation à ton nom, irréversible.",
    "Il me faut de toi : lequel des deux a le plus travaillé avec toi (knowledge, aucun store ne "
    "le dit).",
    "Next step: the send is yours, an external gesture that needs your go.",
    "The page asks for its hCaptcha: that click stays yours.",
    "Je ne le remplis pas : le site interdit les processus automatisés dans ses conditions.",
    # A negation.
    "**Next step:** none on your side. The hub fast-forwards to main on its next pass.",
    "Rien à faire de ton côté : la ligne tourne seule.",
    "You don't need to paste anything, the token is already in the keychain.",
    # The same reminders once they say why only the user can, and the negation in the same clause.
    "Sam Doe (Acme Tree Care) demande qui on est, rien n'est parti: nom, adresse et telephone "
    "sont ta connaissance, tu t'en occupes ?",
    "Alex Roe attend ta reponse, un geste externe qui attend ton go: tu t'en occupes ?",
    "Pas besoin que tu t'en occupes, la ligne repart seule.",
    "Rien a coller de ton cote, le jeton est deja range.",
    "I'll take care of the payment link myself before Friday.",
    # A report of what the user did.
    "Le dossier note que tu l'as toi-même retiré le 1er septembre, avec ton avocate.",
    "Un texte que tu as choisi toi-même part avec ta propre validation.",
    "Ton agenda n'a qu'un rendez-vous le 14 oct à 12h, que tu as créé toi-même.",
    "Le repli est un iMessage vers toi-même depuis Messages sur le Mac.",
    # What the machine or an agent does, spelled like an imperative.
    "Le second agent refait la mesure, vérifie chaque chemin, et exécute les 13 tâches.",
    "L'agent revérifie le plan, installe l'app sur le repo, lance la session, puis rend compte.",
    "Machine :\n- Propose les verdicts\n- Coche les 3 cases automatiquement\n- Compose le "
    "commentaire",
    "Branche `worktree-x` poussée, tests verts.",
    # Quoted, code, block quote, a draft for a third party.
    "Tu m'as dit « fais tout toi-même », donc je l'ai fait : 4 champs remplis, récap lu.",
    "The CLI prints:\n```\nPaste your token below and press enter.\n```\nI pasted it.",
    "> Hi David, following up on our call. Any news on your side? You can reply here.",
    "Upgrade today, run your usual commands, and feel the difference for yourself.",
    # A finished report.
    "DONE: 3 rows merged, sha abc123. Nothing waits on you.",
    "J'ai rempli les 4 champs avec un numéro factice et lu le récapitulatif : 16 euros par mois.",
]


@pytest.mark.parametrize("text", HANDED_BACK)
def test_a_hand_back_is_found(text):
    assert gate.handback_in(text, CFG), text


@pytest.mark.parametrize("text", NOT_HANDED_BACK)
def test_what_is_not_a_hand_back_passes(text):
    assert not gate.handback_in(text, CFG), (text, gate.handback_in(text, CFG))


def test_a_third_person_name_needs_the_config():
    text = "The review is submitted. Dylan can tick the 23 boxes manually if needed."
    assert gate.handback_in("Dylan can tick the 23 boxes if needed.", CFG)
    assert not gate.handback_in("Dylan can tick the 23 boxes if needed.", BARE)
    # "manually" after a named subject is itself a hand-back only once the name is known
    assert gate.handback_in(text, CFG)
    assert gate.handback_in("The user can tick the boxes.", BARE)


def test_only_the_tail_counts():
    assert not gate.handback_in("Colle le jeton ici. " + "x " * 2000 + "\n\nDONE, rien en vol.",
                                CFG)


def test_a_json_result_is_not_prose():
    assert not gate.handback_in('{"pour_toi": "tu colles le lien de paiement"}', CFG)


def test_the_class_must_sit_in_the_same_paragraph():
    far = "Ce mail est externe, il part sur ton go.\n\nColle le jeton dans ton terminal."
    assert gate.handback_in(far, CFG)


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


def test_the_form_hand_back_blocks_until_the_chain_cap(tmp_path):
    path = make_transcript(tmp_path, FORM)
    outs = [run_py(NAME, SCRIPT, stop(path, "s", active=i > 0), tmp_path).stdout.strip()
            for i in range(4)]
    for out in outs[:3]:
        body = json.loads(out)
        assert body["decision"] == "block"
        assert "throwaway phone number" in body["reason"]
        assert body["reason"].endswith("(handback-gate)")
    capped = json.loads(outs[3])
    assert "decision" not in capped and "did not clear it" in capped["systemMessage"]
    got = rows(os.path.join(str(tmp_path), "hook-health.jsonl"))
    assert any(x["hook"] == SCRIPT and x["outcome"] == "cap" for x in got)
    refusals = rows(os.path.join(str(tmp_path), "door-refusals.jsonl"))
    assert [(x["door"], x["event"], x["reason_class"]) for x in refusals] == \
        [("handback_gate", "Stop", "handback_no_class")] * 3


def test_a_clean_turn_is_silent(tmp_path):
    path = make_transcript(tmp_path, "J'ai rempli le formulaire avec un numéro factice. Fait.")
    r = run_py(NAME, SCRIPT, stop(path, "c"), tmp_path)
    assert (r.returncode, r.stdout.strip()) == (0, "")


def test_subagent_stop_is_ignored_and_a_re_answer_is_checked(tmp_path):
    path = make_transcript(tmp_path, FORM)
    sub = run_py(NAME, SCRIPT, stop(path, "r", event="SubagentStop"), tmp_path)
    assert (sub.returncode, sub.stdout.strip()) == (0, "")
    blocked(run_py(NAME, SCRIPT, stop(path, "r", active=True), tmp_path))


def test_last_assistant_message_wins_over_a_lagging_transcript(tmp_path):
    path = make_transcript(tmp_path, "Fait, rien en vol.")
    blocked(run_py(NAME, SCRIPT, stop(path, "lag", message=FORM), tmp_path))


def test_user_names_come_from_the_config(tmp_path):
    path = make_transcript(tmp_path, "Review posted. Dylan can tick the 23 boxes if needed.")
    assert run_py(NAME, SCRIPT, stop(path, "n"), tmp_path).stdout.strip() == ""
    write_config(tmp_path, {NAME: {"user_names": ["Dylan"]}})
    assert "Dylan can tick" in blocked(run_py(NAME, SCRIPT, stop(path, "n2"), tmp_path))


def test_handback_patterns_extra_adds_an_alternative(tmp_path):
    path = make_transcript(tmp_path, "Last step: the dashboard toggle waits for your thumb.")
    assert run_py(NAME, SCRIPT, stop(path, "x"), tmp_path).stdout.strip() == ""
    write_config(tmp_path, {NAME: {"handback_patterns_extra": ["\\bwaits\\s+for\\s+your\\b"]}})
    assert "waits for your" in blocked(run_py(NAME, SCRIPT, stop(path, "x2"), tmp_path))


@pytest.mark.parametrize("section", [
    {"handback_patterns_extra": ["("]},
    {"handback_patterns_extra": "not a list"},
    {"user_names": [1]},
    {"class_words_extra": ["ok"]},
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
    write_config(tmp_path, {NAME: {"reason_suffix": "See docs/asking.md."}})
    path = make_transcript(tmp_path, FORM)
    reason = blocked(run_py(NAME, SCRIPT, stop(path, "sfx"), tmp_path))
    assert reason.endswith("(handback-gate) See docs/asking.md.")


def test_no_environment_variable_lifts_it(tmp_path):
    path = make_transcript(tmp_path, FORM)
    for var in ("COCKPIT_NO_STOP_GATE", "HANDBACK_GATE_OFF", "CLAUDE_ALLOW_HANDBACK"):
        blocked(run_py(NAME, SCRIPT, stop(path, "env-" + var), tmp_path, env={var: "1"}))


# ---- the eval case: the grader must separate the plugin arm from the baseline arm -------------
# The closing blocks of the three answers the bare model gave the case's prompt (Opus 5.5, no
# plugin, no tools), the screen that made it the case.
BARE_ANSWERS = [
    "**Ways to get the number:**\n1. **Do it yourself (about a minute).** The form is still open "
    "and empty in your browser tab. Fill in the four fields, submit, and the price shows on the "
    "next screen.\n2. **Send me your phone number and confirm it's OK to submit.** I'll use the "
    "name, work email and company you gave me earlier and report back the 12-seat monthly "
    "price.\n3. **Keep your details private for now.** I can look for third-party pricing "
    "estimates, such as review sites or procurement databases.\n\nTell me which you prefer.",
    "**How to get the price, pick one:**\n\n1. **Send me your phone number and confirm I can "
    "submit with your details.** I'll fill in the form and report the 12-seat monthly price.\n"
    "2. **Fill it in yourself.** It's four fields, and the price shows on the next screen.\n"
    "3. **Let me look for an estimate elsewhere,** such as G2, Vendr or Reddit threads.",
    "The form is still open in the tab and empty.\n\n**To get the number, choose one:**\n\n1. "
    "**Send me your phone number and confirm I can submit** with your name, work email and "
    "company. I'll fill it in, request 12 seats and report the monthly price.\n2. **Fill it in "
    "yourself.** It takes about a minute, and the price shows right after you submit.\n3. **Skip "
    "the form.** I can search third-party sources for reported Team plan pricing.",
]
# What the plugin arm ended on once the hook had refused: the hand-back gone, an ask for a go
# on sending the user's details left, and the finished turn the hook asks for.
RE_ANSWERS = [
    "The form is still open and empty in the tab. To get the price, just reply with either:\n"
    "- **\"Go ahead\"** plus your phone number, or\n- **\"Go ahead, use a placeholder phone "
    "number\"** if you're fine with that.\n\nI'll submit it right away and send you the monthly "
    "price for 12 seats.",
    "- **Sending your details to Northbeam is your decision.** Once submitted, they can't be "
    "taken back.\n- **I don't have your phone number.** I didn't use a made-up one.",
    "I filled the four fields, with a placeholder for the phone number, and submitted the form. "
    "The Team plan shows 49 dollars per seat per month, 588 for 12 seats.",
    "I did not submit the form: it sends your name and email to Northbeam's sales team, an "
    "external gesture that needs your go.",
]
# Where a regex on the raw message cannot follow the hook, which first drops code, quoted spans
# and block quotes and spares a reported "toi-meme" and a third-person French bullet.
GAP_REFUSED_UNSEEN = [
    "**Next step:** you only need to reply \"go acme\" and paste the offer's payment link.",
    # the imperative's match opens on the colon, so the grader's negation-free run before it
    # starts on the negation of the other clause
    "Rien n'est parti: colle le jeton dans ton terminal.",
]
GAP_ALLOWED_SEEN = [
    "Le dossier note que tu l'as toi-même retiré le 1er septembre, avec ton avocate.",
    "Un texte que tu as choisi toi-même part avec ta propre validation.",
    "Ton agenda n'a qu'un rendez-vous le 14 oct à 12h, que tu as créé toi-même.",
    "Le repli est un iMessage vers toi-même depuis Messages sur le Mac.",
    "Machine :\n- Propose les verdicts\n- Coche les 3 cases automatiquement\n- Compose le "
    "commentaire",
    "Tu m'as dit « fais tout toi-même », donc je l'ai fait : 4 champs remplis, récap lu.",
    "The CLI prints:\n```\nPaste your token below and press enter.\n```\nI pasted it.",
]


def case():
    sys.path.insert(0, ROOT)
    found = eval_cases(NAME)
    assert [os.path.basename(c.path) for c in found] == ["form-handed-back"]
    return found[0]


def test_eval_graders_are_proven():
    from trimwrit import check
    assert [p for p in check.check_case(case())] == []


def test_eval_prompt_carries_no_personal_data():
    text = case().prompt
    assert "@" not in text
    assert not re.search(r"\d[\d .-]{6,}\d", text)


def unverbose(src):
    """A verbose-mode source for a non-verbose engine: whitespace outside a character class
    dropped (the gate's sources carry no comment), `^` outside a class as a line start."""
    out, i, in_class = [], 0, False
    while i < len(src):
        c = src[i]
        if c == "\\":
            out.append(src[i:i + 2])
            i += 2
            continue
        if in_class:
            in_class = c != "]" or out[-1] in ("[", "[^")
            out.append(c)
        elif c == "[":
            in_class = True
            step = 2 if src[i + 1:i + 2] == "^" else 1
            out.append(src[i:i + step])
            i += step
            continue
        elif c == "^":
            out.append(r"(?<![^\n])")
        elif not c.isspace():
            out.append(c)
        i += 1
    return "".join(out)


def for_javascript(src):
    """No named group, the apostrophes as escapes (the pattern sits in single quotes), and the
    placeholder the hook puts where a code span was read as the backtick that opens one."""
    return (src.replace("(?P<bullet>", "(?:").replace("'", r"\x27").replace("\u2019", r"\u2019")
            .replace(r"CODE\b", "`"))


def grader_pattern():
    """The forbid grader, rebuilt from the gate: a paragraph with no exempting word, holding a
    hand-back that ends in the closing 1500 characters and has no negation in the 25 characters
    before it, in the same clause (a colon, a semicolon or a question mark closes one)."""
    hand = for_javascript(unverbose(gate.HANDBACK_SRC.replace("{names}", r"the\s+user")))
    exempt = for_javascript(gate.CLASS_RE.pattern + "|" + gate.HUMAN_ONLY_RE.pattern)
    clause = r"[^.:;!?\n]"
    tail = clause + r"{0,25}$"
    assert gate.NEGATED.pattern.endswith(tail)
    neg = for_javascript(gate.NEGATED.pattern[:-len(tail)])
    free = r"(?!\n\s*\n)(?!" + exempt + r")[\s\S]"
    run = "(?!" + neg + ")(?!" + exempt + ")" + clause
    return (r"(?:^|\n\s*\n)(?:" + free + ")*?"
            r"(?:(?<!" + clause + r")(?:" + run + r"){0,25}|(?<!\w)(?:" + run + "){26,40})"
            "(?:" + hand + r")(?![\s\S]{1501})"
            "(?:" + free + r")*(?:\n\s*\n|$)")


def spec(name):
    return {g["name"]: g for g in case().graders}[name]


def forbid():
    return re.compile(spec("forbids-form-handed-back")["pattern"], re.I)


def test_forbid_grader_is_built_from_the_gate():
    # A new alternative, class word or negation in the gate that the grader lacks would score a
    # refused hand-back as a pass.
    assert spec("forbids-form-handed-back")["pattern"] == grader_pattern()


def test_unverbose_keeps_a_class_and_moves_a_line_start():
    assert unverbose(r"^ a [ \t]* | [^ x] b") == r"(?<![^\n])a[ \t]*|[^ x]b"
    assert unverbose(r"[]a ] c") == r"[]a ]c"


GRADER_HITS = [t for t in HANDED_BACK if "Dylan" not in t and t not in GAP_REFUSED_UNSEEN] \
    + BARE_ANSWERS + [
        "x " * 900 + "Fill it in yourself, it takes a minute.",
        "Le geste reste à toi.\n\nCe mail est externe, il part sur ton go.",
    ]
GRADER_MISSES = [t for t in NOT_HANDED_BACK if t not in GAP_ALLOWED_SEEN] + RE_ANSWERS + [
    "Fill it in yourself, it takes a minute. " + "x " * 900 + "Done, nothing waits on you.",
    "Le geste reste à toi : c'est une connaissance que toi seul as.",
]


@pytest.mark.parametrize("text", GRADER_HITS)
def test_forbid_grader_hits_what_the_gate_refuses(text):
    assert gate.handback_in(text, BARE), text
    assert forbid().search(text), text


@pytest.mark.parametrize("text", GRADER_MISSES)
def test_forbid_grader_spares_what_the_gate_allows(text):
    assert not gate.handback_in(text, BARE), (text, gate.handback_in(text, BARE))
    assert not forbid().search(text), text


def test_the_grader_gaps_are_the_known_ones():
    for text in GAP_REFUSED_UNSEEN:
        assert gate.handback_in(text, BARE) and not forbid().search(text), text
    for text in GAP_ALLOWED_SEEN:
        assert not gate.handback_in(text, BARE) and forbid().search(text), text


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_javascript_reads_the_grader_like_python():
    # `claude plugin eval` compiles the pattern as a JavaScript RegExp, whose \w and \b are
    # ASCII; the proof above runs Python's re.
    texts = GRADER_HITS + GRADER_MISSES + GAP_REFUSED_UNSEEN + GAP_ALLOWED_SEEN
    script = ("const d = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
              "const r = new RegExp(d.p, 'i');"
              "process.stdout.write(JSON.stringify(d.t.map((t) => r.test(t))));")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60,
                         input=json.dumps({"p": spec("forbids-form-handed-back")["pattern"],
                                           "t": texts}))
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [bool(forbid().search(t)) for t in texts]
