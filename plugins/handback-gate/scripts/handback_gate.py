#!/usr/bin/env python3
"""Stop hook: a turn does not end by handing the user a gesture or an input the agent could do.

Why a hook and not a sentence in a CLAUDE.md: the rule "never ask the user for an input the
machine can produce" sat in one user's CLAUDE.md for six weeks while eleven of his corrections
said the same thing in other words ("fait tout toi meme, pour le numero met un faux", "tick les
toi meme", "mais tu peux faire tout les tests toi meme", "vraiment prend le controle. fait le toi
meme"). None of the failing messages asked a question. They handed a gesture back: "tu remplis les
4 champs toi-meme", "Dylan can tick the 23 boxes manually", "Colle le jeton", "Pour savoir si ca
marche, tape `curl ...`", "le geste physique reste a toi", "Ce qui sera a toi: creer le bot,
stocker le token". A gate that reads questions never sees them, and the sentence alone loses to
the agent's belief, in the moment, that the gesture is the user's.

What it checks: the last TAIL_CHARS of the final message (`last_assistant_message` from the
payload, the transcript only when it lacks one), paragraph by paragraph, for a hand-back (HANDBACK
below): the user named as the one who acts ("tu remplis", "you only need to paste", "<name> can
tick", "reste a toi", "il me faut de toi", "a toi de", "de ton cote", "toi-meme"), or a
second-person imperative at the head of a sentence or a bullet ("Colle le jeton", "Fais cmd+P",
"Pour savoir si ca marche, tape ..."). Code spans, fenced blocks, block quotes and quoted spans are
dropped first: a command shown, a draft for a third party or a verbatim is not a hand-back. A match
preceded by a negation ("rien a faire de ton cote", "none on your side", "you don't need to
paste") or describing what the user already did ("tu l'as toi-meme retire") is not one either.

What makes a hand-back legitimate: the SAME paragraph names why only the user can, with one of the
words of the asking contract (CLASS_RE: knowledge, irreversible or external, absent; the French
forms too), or names a check only a human may pass (HUMAN_ONLY_RE: a captcha, a second factor, a
biometric, a site whose terms forbid automation). A physical gesture is NOT such a reason: the
correction that taught it was "je vais pas faire l'ethernet c'est pas une option". Nothing else
lifts it: no configuration key adds an exemption.

Measured before it shipped, on 34,665 turn-final messages of the user's own transcripts (all
projects, 50 days): all 10 messages his corrections answered are refused, and 384 messages (1.1
percent) in all. In two hand-labelled random samples, about one in ten refusals was not a hand-back
at all (an explanation of a third party's process, a configurator's behaviour); the rest handed
something back, and the legitimate ones (a send only his go starts, a call in his voice) name the
class on the re-answer. "Do it yourself" was learned from the eval case, where the bare model
wrote it as an option: one more refusal on the same corpus, 385.

The bounds: the re-answer of a stop chain is checked like the first answer; the gate lets go after
CHAIN_CAP blocks in a row (a `cap` row in the ledger and a systemMessage). A crash or a timeout
lets the turn end and is reported loudly. No environment variable and no configuration key turns
the gate off. A SubagentStop is another event and is ignored. A final message that is a JSON
document (a headless worker's result) is not prose and is not read.

Configuration (section `handback-gate` of trimwrit-gates.json): `user_names` lists the names the
user goes by in the third person ("Dylan can tick ..."), `handback_patterns_extra` adds regex
alternatives (compiled case-insensitive and verbose, so a space inside an extra is written `\\s`),
`reason_suffix` is appended to the refusal.

Outputs:
    exit 0 with nothing on stdout                       the session ends normally
    exit 0 with {"decision": "block", "reason": ...}    the session continues with this message

Utilities:
    python3 handback_gate.py --print       what it would say on the last message, blocking nothing
"""
import json
import os
import re
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_ROOT)
from gatekit import config, trace, transcript  # noqa: E402

HOOK = "handback_gate.py"
TIMEOUT_S = 8
TAIL_CHARS = 1500

# The gestures, French infinitive (after "tu peux", "tu dois", "tu n'as qu'a"), French present
# second person ("tu remplis"), French imperative ("Colle", "Fais"), and English base form.
FR_INF = r"""(?:remplir|coller|copier|cocher|cliquer|imprimer|brancher|taper|cr[ée]er|ajouter
  |lancer|installer|activer|t[ée]l[ée]charger|d[ée]poser|scanner|signer|r[ée]essayer|tester
  |v[ée]rifier|saisir|renseigner|envoyer|stocker|enregistrer|(?:te\s+)?connecter
  |(?:te\s+)?(?:re)?logu?er|uploader|configurer|r[ée]gler|ex[ée]cuter|relancer
  |faire\s+(?:le|la|les|un|une|ce|cette|ces)\b)"""
FR_PRES = r"""(?:remplis|colles|copies|coches|cliques|imprimes|branches|tapes|cr[ée]es|ajoutes
  |lances|installes|actives|t[ée]l[ée]charges|d[ée]poses|scannes|signes|r[ée]essaies|r[ée]essayes
  |testes|v[ée]rifies|saisis|renseignes|envoies|stockes|enregistres|te\s+connectes
  |te\s+reconnectes|configures|r[ée]gles|ex[ée]cutes|relances
  |fais\s+(?:le|la|les|un|une|ce|cette|ces|cmd|ctrl)\b)"""
FR_IMP = r"""(?:remplis|colle|copie|coche|clique|imprime|branche|tape|cr[ée]e|ajoute|lance
  |installe|active|t[ée]l[ée]charge|d[ée]pose|scanne|signe|r[ée]essaie|r[ée]essaye|teste
  |v[ée]rifie|saisis|renseigne|envoie|stocke|enregistre|connecte[\s-]toi|reconnecte[\s-]toi
  |configure|r[ée]gle|ex[ée]cute|relance|fais)"""
# What follows an imperative: an object or a complement, so a heading word ("Branche: main") is
# not read as an order. A code span counts only after a subordinate clause ("Pour savoir si ca
# marche, tape `curl ...`"): at the head of a line, "Branche `x` poussee" is a report.
FR_OBJ = r"""(?:le\b|la\b|les\b|l'|ton\b|ta\b|tes\b|un\b|une\b|ce\b|cette\b|ces\b|[çc]a\b|cmd\b
  |ctrl\b|maintenant\b|depuis\b|sur\b|dans\b|-le\b|-la\b|-les\b|-moi\b|-lui\b|\d)"""
FR_OBJ_CODE = r"""(?:CODE\b|""" + FR_OBJ + r""")"""
# A first-group imperative is spelled like the third person present ("Coche les cases" is an
# order to the user, "- Coche les cases" under "Machine:" describes what the machine does). At
# the head of a bullet it counts only when the paragraph speaks to the user (SECOND_PERSON); at
# the head of a line, a sentence or a bold heading it is an order.
FR_AMBIGUOUS = re.compile(
    r"^(?:colle|copie|coche|clique|imprime|branche|tape|cr[ée]e|ajoute|lance|installe|active"
    r"|t[ée]l[ée]charge|d[ée]pose|scanne|signe|r[ée]essaye|teste|v[ée]rifie|renseigne|envoie"
    r"|stocke|enregistre|configure|r[ée]gle|ex[ée]cute|relance)$", re.I)
SECOND_PERSON = re.compile(r"\b(?:tu|ton|ta|tes|toi|te|t'|you|your)\b", re.I)

EN_VERB = r"""(?:fill(?:\s+in|\s+out)?|paste|copy|tick|check\s+(?:the\s+)?box(?:es)?|click|print
  |create|plug(?:\s+in)?|run|type|add|store|save|send|log\s+in|sign\s+(?:in|up)|upload|download
  |install|enable|test|retry|try\s+(?:it\s+)?again|enter|set\s+up|configure|connect|scan|submit
  |register|top\s+up|solve|merge)"""
# After the subject: a contraction ("you'll need to") or a spaced modal ("you only need to").
EN_MODAL = r"""(?:['’]ll\s+(?:need|have)\s+to|\s+(?:can|could|need\s+to|needs\s+to|have\s+to
  |has\s+to|must|should|will\s+(?:need|have)\s+to|only\s+need\s+to|just\s+need\s+to
  |may\s+want\s+to|might\s+want\s+to|then))"""

# Each alternative is one way of handing the user the gesture. {names} is the `user_names`
# config, the third person the agent uses for the user. The eval case's grader is built from this
# source and compiled as a JavaScript RegExp, whose \w is ASCII: a word ending on an accented
# letter is closed with (?!\w), which Python reads exactly like \b there.
HANDBACK_SRC = (r"""
    \btoi[\s-]m[eê]me\b
  | \b(?:reste(?:nt)?|sera(?:it|ient)?|seront)\s+[àa]\s+toi\b
  | (?<!\w)[àa]\s+toi\s+de\s+\w
  | \bde\s+ton\s+c[oô]t[ée](?!\w)
  | \bil\s+me\s+faut\s+de\s+toi\b
  | \bj'ai\s+besoin\s+(?:que\s+tu|de\s+toi)\b
  | \bce\s+qu'il\s+(?:me\s+)?faut\s+de\s+toi\b
  | \btu\s+(?:dois|devras|devrais|peux|pourras|pourrais|n'as\s+qu'[àa]|as\s+juste\s+[àa]
           |auras\s+[àa]|vas\s+devoir|dois\s+juste|as\s+[àa])\s+(?:\w+\s+){0,2}?""" + FR_INF + r"""\b
  | \btu\s+""" + FR_PRES + r"""\b
  | (?:^[ \t]*|[.:;!?]\s+|\*\*\s*)""" + FR_IMP + r"""\s*""" + FR_OBJ + r"""
  | (?:^[ \t]*(?:[-*]|\d[.)])\s+)(?P<bullet>""" + FR_IMP + r""")\s*""" + FR_OBJ + r"""
  | \b(?:pour|si|quand|une\s+fois|d[èe]s\s+que|apr[èe]s|ensuite|sinon|alors)\b[^,.\n]{0,80},\s*"""
    + FR_IMP + r"""\s*""" + FR_OBJ_CODE + r"""
  | \b""" + EN_VERB + r"""\b[^.\n]{0,60}(?<!for\s)\byourself\b
  | \bdo\s+(?:it|this|that|them|these|those)\s+yourself\b
  | \b(?:you|{names})""" + EN_MODAL + r"""\s+(?:\w+\s+){0,3}?""" + EN_VERB + r"""\b
  | \b(?:you|{names})\s+(?:\w+\s+){0,2}?manually\b
  | \b(?:I\s+need\s+you\s+to|need(?:ed)?\s+from\s+you|over\s+to\s+you|your\s+turn|left\s+for\s+you
       |(?:is|are)\s+(?:on|up\s+to)\s+you|(?:is|are)\s+your\s+call)\b
  | (?:^|\n|[.:;!?]\s+|[-*]\s+|\d[.)]\s+|\*\*\s*)(?:then\s+|and\s+)?(?:paste|fill\s+(?:in|out)
       |tick|click|print|plug\s+in|type|log\s+in|sign\s+in|upload|retry|try\s+again|run)\b
       [^.\n]{0,60}\byour\b
""")

# Why only the user can, named in the same paragraph. The words of the asking contract (his own
# knowledge, a go on an irreversible or external gesture, a fact absent from every store), in
# English and French.
CLASS_RE = re.compile(r"\b(?:knowledge|connaissance|irr[ée]versible|external|externe"
                      r"|absente?s?)\b", re.I)
# A check only a human may pass.
HUMAN_ONLY_RE = re.compile(
    r"\b(?:h?captcha|recaptcha|2fa|two[\s-]factor|otp|one[\s-]time\s+(?:code|password)"
    r"|verification\s+code|code\s+de\s+v[ée]rification|code\s+(?:par\s+)?sms|sms\s+code"
    r"|face\s*id|touch\s*id|passkey|biometric|biom[ée]tri\w*|security\s+key"
    r"|cl[ée]\s+de\s+s[ée]curit[ée]|yubikey)(?!\w)"
    r"|\b(?:forbid\w*|prohibit\w*|interdi\w+|breaks?|viol\w+)\b[^.\n]{0,60}"
    r"\b(?:terms|conditions|cgu|tos|automat\w*|bots?)\b", re.I)
# A negation just before the match: "rien a faire de ton cote", "none on your side".
NEGATED = re.compile(r"(?:\b(?:pas|plus|rien|jamais|aucun|aucune|sans|ni|no|not|never|nothing"
                     r"|none|without|nobody|personne)\b|n'|n\u2019|\b(?:don|doesn|won|didn|isn"
                     r"|aren)'?t\b)[^.\n]{0,25}$", re.I)
# "toi-meme" after a past tense, a relative clause or a preposition reports or describes:
# "tu l'as toi-meme retire", "un texte que tu as choisi toi-meme", "un message vers toi-meme".
REPORTED = re.compile(r"(?:\btu\s+(?:l'|les\s+|la\s+|y\s+)?(?:as|avais|avait|es|[ée]tais)"
                      r"(?:\s+\w+)?|\bl'as-tu(?:\s+\w+)?|\bas-tu(?:\s+\w+)?|\bque\s+tu\s+\w+"
                      r"|\bviens\s+d'\w+|\bvers|\b[àa]|\bpour|\bde)\s*$", re.I)

CODE = re.compile(r"```.*?```|`[^`\n]{0,300}`", re.S)
BLOCKQUOTE = re.compile(r"^\s*>.*$", re.M)
QUOTED = re.compile("\"[^\"\\n]{0,400}\"|\u201c[^\u201d\\n]{0,400}\u201d"
                    "|\u00ab[^\u00bb\\n]{0,400}\u00bb")

FLAGS = re.I | re.X | re.M
_COMPILED = {}


def _strings(where, value):
    if not isinstance(value, list) or not all(isinstance(a, str) for a in value):
        raise config.ConfigError("handback-gate: {} must be a list of strings".format(where))
    return value


def pattern(cfg):
    """HANDBACK_SRC with the configured user names and extras. A name list or an extra that is
    not a list of strings, or an extra that does not compile, is a config.ConfigError."""
    key = json.dumps([cfg.get("user_names"), cfg.get("handback_patterns_extra")])
    if key not in _COMPILED:
        names = [n.strip() for n in _strings("user_names", cfg.get("user_names") or [])
                 if n.strip()]
        alt = "|".join(r"\s+".join(re.escape(w) for w in n.split()) for n in names)
        src = HANDBACK_SRC.replace("{names}", alt or "the\\s+user")
        extras = _strings("handback_patterns_extra", cfg.get("handback_patterns_extra") or [])
        src = src.rstrip() + "".join("\n  | (?:{}\n)".format(a) for a in extras)
        try:
            _COMPILED[key] = re.compile(src, FLAGS)
        except re.error as e:
            raise config.ConfigError("handback-gate handback_patterns_extra: {}".format(e))
    return _COMPILED[key]


def _settings(cfg):
    if cfg is not None:
        return cfg
    return trace.plugin_settings({"cwd": os.getcwd()}, root=PLUGIN_ROOT)


def prose(text):
    """The text the gate reads: code, block quotes and quoted spans replaced by a placeholder."""
    t = CODE.sub(" CODE ", text or "")
    t = BLOCKQUOTE.sub(" ", t)
    return QUOTED.sub(" QUOTE ", t)


def handback_in(text, cfg=None):
    """(the hand-back phrase, its paragraph) for the first paragraph of the tail of `text` that
    hands the user a gesture without naming why only the user can, else None. Pure."""
    tail = (text or "")[-TAIL_CHARS:]
    if tail.lstrip().startswith(("{", "[")):
        return None
    rx = pattern(_settings(cfg))
    for para in re.split(r"\n\s*\n", prose(tail)):
        if CLASS_RE.search(para) or HUMAN_ONLY_RE.search(para):
            continue
        for m in rx.finditer(para):
            if NEGATED.search(para[max(0, m.start() - 40):m.start()]):
                continue
            bullet = m.groupdict().get("bullet")
            if bullet and FR_AMBIGUOUS.match(bullet.strip()) and not SECOND_PERSON.search(para):
                continue
            if re.match(r"toi[\s-]m", m.group(0), re.I) \
                    and REPORTED.search(para[max(0, m.start() - 30):m.start()]):
                continue
            return " ".join(m.group(0).split()), para.strip()
    return None


def reason_text(phrase, cfg):
    text = ("The final message hands the user a gesture or an input (\"{}\"). Do it yourself "
            "first whenever your tools can: fill the form (a placeholder value where a field is "
            "not the user's to give, a throwaway phone number for instance), run the test or the "
            "command, tick the boxes, read the page or the mailbox, create the account, paste the "
            "link. Hand back only what the user alone holds, and say why in the same paragraph: "
            "KNOWLEDGE (a fact or a preference only the user has), an IRREVERSIBLE or EXTERNAL "
            "gesture that needs the user's go (a send, a payment, a public post, a commitment), "
            "a fact ABSENT from every store, file and page you can read, or a check only a human "
            "may pass (a captcha, a second factor, a biometric, a site whose terms forbid "
            "automation). A physical gesture is not a reason: find the path that does not need "
            "it. (handback-gate)".format(phrase[:160]))
    suffix = cfg.get("reason_suffix")
    return text + " " + suffix if suffix else text


def on_stop():
    payload = trace.read_payload()
    if payload.get("hook_event_name") != "Stop":
        return 0
    cfg = trace.plugin_settings(payload, root=PLUGIN_ROOT)
    pattern(cfg)  # a bad name list or extra is a loud crash even on a clean message
    found = handback_in(transcript.final_text(payload), cfg)
    if not found:
        trace.chain_clear(HOOK, payload)
        return 0
    phrase = found[0]
    if not trace.chain_should_block(HOOK, payload, "handback: " + phrase):
        print(trace.cap_message(HOOK, "handback: " + phrase))
        return 0
    trace.witness("handback_gate", "Stop", "handback_no_class", payload)
    print(json.dumps({"decision": "block", "reason": reason_text(phrase, cfg)},
                     ensure_ascii=False))
    return 0


def main():
    trace.configure(sys.argv)
    if "--print" in sys.argv:
        payload = trace.read_payload() if not sys.stdin.isatty() else {}
        found = handback_in(transcript.final_text(payload), None)
        print("handback-gate: " + ("would refuse on \"{}\"".format(found[0]) if found
                                   else "nothing handed back, silent"))
        return 0
    return trace.run(HOOK, "Stop", on_stop, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
