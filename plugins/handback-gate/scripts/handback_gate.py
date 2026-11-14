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

What the refusal says, so that the re-answer passes: every hand-back of the closing with the
sentence it sits in (up to LISTED_MAX), the first phrase as ("...") first; that a description
with the user as its subject is read like an order, and how to write who does it; and the exact
words the gate reads as a reason (CLASS_WORDS, HUMAN_ONLY_WORDS), with the ones it does not
(NOT_A_CLASS). A test reads those lists against CLASS_RE and HUMAN_ONLY_RE. Taught 2026-10-28 by
two sessions refused five times or more in a week: the re-answers reworded only the one phrase
quoted, and the reasons they gave were the refusal's own examples, which the gate did not read.

The hand-back that names no verb: "C'est a toi, parce qu'accepter des conditions engage le compte
a ton nom", "**A toi seul :** l'achat des 20, parce que c'est ton argent", "payer est ton geste",
"Dis-moi quand c'est paye", "That part is yours". The gate read a gesture with the user as its
subject, so a closing that stated whose the gesture was, and gave its own reason, passed: 2
refusals on 897 closings of the user's sessions over 9 days, and 81 more in those shapes, two
of them answered by "tu peux le faire toi meme via token ?" and "sur l'acceptation des conditions
du revendeur => fait le toi meme". Those shapes are read since 2026-10-30, and the refusal says
that an irreversible gesture the agent's tools can perform is asked as a go, never handed over.

Measured before it shipped, on 34,665 turn-final messages of the user's own transcripts (all
projects, 50 days): all 10 messages his corrections answered are refused, and 384 messages (1.1
percent) in all. In two hand-labelled random samples, about one in ten refusals was not a hand-back
at all (an explanation of a third party's process, a configurator's behaviour); the rest handed
something back, and the legitimate ones (a send only his go starts, a call in his voice) name the
class on the re-answer. "Do it yourself" was learned from the eval case, where the bare model
wrote it as an option: one more refusal on the same corpus, 385.

THE PRIME. A refusal teaches the re-answer, and the re-answer passed; but the lesson lived only
in the context of the turn, and the next closing of the same session was drafted without it. The
session that taught it (2026-10-30, a company dissolution: signatures, payments, a bank closed
by the user's own passkey) was refused 7 times in 7 different turns, every re-answer passing at
its first try, while 6 compactions erased each refusal from the context: an option ("No: you call
Delaware yourself"), a process ("tu signes le consentement et le certificat"), a mechanism
("quand tu cliques sur Continue Filing"), a reco pointing at a gesture classified higher up ("tu
fais le geste 1"), a reason written in the NEXT paragraph. So a refusal also writes the session's
state, and the same script, run as a UserPromptSubmit hook and as a SessionStart hook after a
compaction or a resume (`--prime`), hands the producer the rule BEFORE it drafts: the count, the
last sentences refused, the shapes, the words the gate reads. It runs in full for PRIME_PROMPTS
prompts after the last refusal (that session's longest gap between two refusals was 6 prompts),
then as one line holding the command until the session ends (2026-11-07, see THE PATHS AROUND THE
CHECK), says nothing in a session the gate never refused, and lifts nothing: the Stop gate reads
every closing exactly as before.

THE INPUT ASKED AS A QUESTION (2026-11-04). A closing (an application form, 2,696 characters)
asked the user, under a header naming the user's knowledge and "absent de tous mes fichiers",
for the name, date and format of a Paris event, and for "une phrase de toi pour dire si c'est
bien comme ca que tu le vois" on a reading the agent had already written. Answer: "1. cherche
toi meme pour l'event paris qui etait sur luma 2. la partie guardrail .... vasi putin brode
tout". Three paths
went around the gate, each closed here:
  1. The window. The asks sat 1,572 characters from the end ("tu reponds" in "Le relecteur pose
     ensuite ses questions, tu reponds, je resserre" included), and the closing only pointed at them
     ("tes reponses aux 4 questions"). A closing that points at asks higher up (POINTER_RE) is
     read whole.
  2. The class word on a fact a page holds. An event's name, date or link is on its page, its
     platform, its confirmation mail: neither the user's knowledge nor absent until a search came
     back empty. A question asking the user for one (LOOKUP_A, LOOKUP_B) is read in the whole
     message and lifted only by the search said in its paragraph (SEARCHED_RE) or a check only a
     human may pass; "knowledge" and "absent" do not lift it. An absence claimed over the agent's
     own files (NARROW_ABSENT: "absent de mes fichiers") is not the class absent anywhere: the
     contract's absent is absent from every store AND page.
  3. The confirmation of a reading the agent wrote (CONFIRM_RE: "dis-moi si c'est bien ca",
     "is that how you see it"): the reading is in hand, so it is used as the agent's and the user
     corrects it. Lifted by a go on an irreversible or external gesture or a human-only check,
     never by knowledge.
Measured on gc-hub, 9,023 turn-final messages of every project over 30 days (pops and headless
runs included): 16 closings point at asks higher up and the whole read refuses 1 more, the
incident; the lookup read fires on 1, the incident; the confirmation read on 1, the incident;
the narrow absence sits in 4 paragraphs and changes no outcome (each names another class).
"j'attends de toi" and "expect from you" are read with the gesture shapes, the French form of
"what I need from you".

THE CHECK. The prime described the gate and asked the producer to read its own closing like it;
the producer could not. On 2026-11-03 a session (a job application: forms, references, a submit
click) was primed before every closing and still refused 4 more times: a reason written in other
words ("None of my files names any", "I don't have that information"), an argument with the user
as its subject ("alors que tu coches Yes sur leur politique IA"), a way to look at a page ("Pour
les voir, ouvre l'écran du serveur"), and a reco repeating a gesture classified in a table above
it, drafted as the re-answer to ANOTHER Stop hook's refusal, which no prompt hook reaches. So the
gate is handed over as a command: `--check` reads a draft on stdin (or a file) through the same
handbacks_in, with the same configuration, and prints each sentence the Stop hook would refuse
(exit 1) or that it passes (exit 0). The prime and the refusal name the exact command, this
script's own path, so a closing and a re-answer are checked before they are shown. It lifts
nothing either: the Stop gate still reads every closing.

THE PATHS AROUND THE CHECK (2026-11-07). Reflect counted three sessions stuck after 0.7.0, refused
5 to 8 times each. Reading their transcripts: of their 20 refusals, 3 ran a version with the
check, and each session that got it was not refused again once its re-answer had passed. Under
the check, 24 refusals over 18 sessions, none of them stuck. What went around it:
  1. The version. Claude Code fixes the plugin root when a session starts, so a session ran 0.5.0
     for three days while 0.7.1 was installed, 8 refusals and not one check. gatekit's
     follow_install now hands a stale copy over to the installed one, the first thing a gate does.
  2. The window. 12 prompts came from one session's longest gap, 6. 8 of the 20 refusals came 16
     to 86 prompts after the last one, past the prime. It shortens, it never stops.
  3. Agreement. The refusal says "irréversible" and "externe"; the model wrote them in agreement
     with a plural noun, as French requires, and the gate refused them. 2 of the 3 re-answers
     refused again in those sessions were that, and handback_retract_gate reads this matcher, so
     it refused the line it asks for and then denied the --check call. A plural is the class
     word spelled right: on 3,436 closings of 14 days it flips 7, each one naming its class,
     against 17 more refused by the wordings of point 5.
  4. The reason in other words, again: the user's own rule ("ta règle", "tu m'as demandé"), the
     reason of 6 of the 20, where a go on the agent's own gesture was the honest sentence. Still
     refused, now named, with the rewrite that passes.
  5. The synonym. The check tells a producer exactly what the gate reads, so a loop on it ends on
     a wording the gate does not read: the first replay of this fix passed 12 of 12 with "reste
     le tien", "qui t'appartient" and "réservée à toi", the user's rule still the only reason.
     Those wordings are read now, and the refusal says a synonym lifts nothing.

THE TEXT SENT IS THE TEXT CHECKED (2026-11-10). Reflect counted one session refused 12 times
over 8 days (a game bot, every turn a report and a choice). 10 of its refusals ran 0.7.1 or
older: the session started before 0.8.0 and follow_install ships inside 0.8.0, so it reached the
session only at a resume. The 2 refusals under 0.8.0, back to back in one turn:
  1. A misread. "Il calcule ce qui manque pour les 24 Cras, achète les ressources à l'HDV et
     fait crafter les pièces" tells what a script does; "pour ..., achète les" read as an order.
     A first-group verb after a subordinate is no order when its sentence opens on a subject
     pronoun with no colon or semicolon between (SUBJECT_HEAD); "Pour finir, achète les ..." and
     "Il reste une étape : pour finir, achète les ..." stay refused.
  2. The rewording. The re-answer to a question was checked ("Réponse possible : oui, ..."),
     passed, then sent reworded ("Tu peux répondre oui, ..."), and refused. A pass now says the
     text it read is the one to send; the prime and the refusal say a rewording is a new draft;
     and a refusal of a closing that differs from the turn's last checked text (read from the
     transcript's --check heredoc, last_checked) opens on the sentences the check never saw.

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

    exit 0 with {"hookSpecificOutput": {..., "additionalContext": ...}}   (--prime) the rule,
                                                        before a closing of a refused session

    exit 0 / 1, a report on stdout                    (--check) the draft passes / would be
                                                        refused, each sentence listed
    exit 2, the error on stderr                        (--check) a bad configuration or file

Utilities:
    python3 handback_gate.py --print       what it would say on the last message, blocking nothing
    python3 handback_gate.py --check [FILE] what it would refuse in a draft (stdin when no FILE)
"""
import json
import os
import re
import shlex
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
  |payer|acheter|commander|accepter|confirmer|valider|r[ée]pondre
  |t['’]en\s+(?:occuper|charger)|t['’]y\s+coller|te\s+charger
  |faire\s+(?:le|la|les|un|une|ce|cette|ces)\b)"""
FR_PRES = r"""(?:remplis|colles|copies|coches|cliques|imprimes|branches|tapes|cr[ée]es|ajoutes
  |lances|installes|actives|t[ée]l[ée]charges|d[ée]poses|scannes|signes|r[ée]essaies|r[ée]essayes
  |testes|v[ée]rifies|saisis|renseignes|envoies|stockes|enregistres|te\s+connectes
  |te\s+reconnectes|configures|r[ée]gles|ex[ée]cutes|relances
  |ach[eè]tes|commandes|acceptes|r[ée]ponds(?!\s*\?)
  |fais\s+(?:le|la|les|un|une|ce|cette|ces|cmd|ctrl)\b)"""
FR_IMP = r"""(?:remplis|colle|copie|coche|clique|imprime|branche|tape|cr[ée]e|ajoute|lance
  |installe|active|t[ée]l[ée]charge|d[ée]pose|scanne|signe|r[ée]essaie|r[ée]essaye|teste
  |v[ée]rifie|saisis|renseigne|envoie|stocke|enregistre|connecte[\s-]toi|reconnecte[\s-]toi
  |configure|r[ée]gle|ex[ée]cute|relance|fais
  |accepte|paie|ach[eè]te|commande|valide|confirme|ouvre|r[ée]ponds)"""
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
    r"|stocke|enregistre|configure|r[ée]gle|ex[ée]cute|relance"
    r"|accepte|paie|ach[eè]te|commande|valide|confirme|ouvre)$", re.I)
SECOND_PERSON = re.compile(r"\b(?:tu|ton|ta|tes|toi|te|t'|you|your)\b", re.I)
# A first-group verb after "pour ..., " is the third person of a list when its sentence opens on
# a subject pronoun, no colon or semicolon between them: "Il calcule ce qui manque pour les 24
# Cras, achète les ressources à l'HDV et fait crafter les pièces" tells what a script does
# (2026-11-10, refused, and the re-answer that followed handed a gesture back for real).
# "Pour savoir si ça marche, tape ..." opens on its subordinate and stays an order.
SUBJECT_HEAD = re.compile(r"^[\s\-*•\d.)]*(?:il|elle|on|ils|elles|je|nous)\s+[^:;]*$|^[\s\-*•\d.)]*j['’][^:;]*$",
                          re.I)

EN_VERB = r"""(?:fill(?:\s+in|\s+out)?|paste|copy|tick|check\s+(?:the\s+)?box(?:es)?|click|print
  |create|plug(?:\s+in)?|run|type|add|store|save|send|log\s+in|sign\s+(?:in|up)|upload|download
  |install|enable|test|retry|try\s+(?:it\s+)?again|enter|set\s+up|configure|connect|scan|submit
  |register|top\s+up|solve|merge|pay|buy|accept|confirm|approve|reply|bid)"""
# After the subject: a contraction ("you'll need to") or a spaced modal ("you only need to").
EN_MODAL = r"""(?:['’]ll\s+(?:need|have)\s+to|\s+(?:can|could|need\s+to|needs\s+to|have\s+to
  |has\s+to|must|should|will\s+(?:need|have)\s+to|only\s+need\s+to|just\s+need\s+to
  |may\s+want\s+to|might\s+want\s+to|then))"""

# Each alternative is one way of handing the user the gesture. {names} is the `user_names`
# config, the third person the agent uses for the user. The eval case's grader is built from this
# source and compiled as a JavaScript RegExp, whose \w is ASCII: a word ending on an accented
# letter is closed with (?!\w), which Python reads exactly like \b there.
# Whose a gesture is, in the wordings a --check loop finds when the shapes it hits are the only
# ones it hears about (2026-11-07, 12 replays and 17 closings of 14 days): "reste le tien",
# "t'appartient", "ne dépend que de toi", "réservée à toi". The nouns keep "les verdicts sont les
# tiens", which tells who wrote what, and the imperfect keeps "ce qui ne pouvait venir que de toi
# est fait".
# Three subjects that are not the user, read as one in primed sessions after 2026-11-01 and so
# refused where no prime could help: a bold code span ("**`tests/x.py`** verifie dans le code"),
# a watcher that will tell the agent ("A watch will tell me as soon as OVH delivers it"), and, in
# REPORTED below, a wish the user had ("alors que tu voulais payer toi-meme"). "Tell me when",
# "you can tell me when" and "**Recommande :** envoie" stay refused.
HANDBACK_SRC = (r"""
    \btoi[\s-]m[eê]me\b
  | \b(?:reste(?:nt)?|sera(?:it|ient)?|seront)\s+[àa]\s+toi\b
  | (?<!\w)[àa]\s+toi\s+de\s+\w
  | \bde\s+ton\s+c[oô]t[ée](?!\w)
  | \bc['’]est\s+[àa]\s+toi\b
  | \b(?:c['’]est|reste(?:nt)?)\s+[àa]\s+(?:{names})\b
  | (?<!\w)[àa]\s+toi\s+seule?\b
  | ^[ \t]*(?:[-*]\s+)?(?:\*\*\s*)?[àa]\s+toi\s*(?:\*\*\s*)?:
  | \btoi\s+seule?\s+(?:peux|peut|pourras)\s+(?!lire\b|voir\b)\w
  | \bil\s+n['’]y\s+a\s+que\s+toi\b
  | \bc['’]est\s+toi\s+qui\s+\w
  | \bton\s+(?:geste|clic)\b
  | \bde\s+ta\s+main\b
  | \btes\s+mains\b
  | \bte\s+revien(?:t|nent|dra|dront|drait)\b(?!\s+par\b)
  | \brevien(?:t|nent|dra|dront)\s+[àa]\s+toi\b
  | \bd[ée]pend(?:ent)?\s+(?:que\s+)?de\s+toi\b
  | \bne\s+peu(?:t|vent)\s+venir\s+que\s+de\s+toi\b
  | \bne\s+se\s+d[ée]cide(?:nt)?\s+que\s+par\s+toi\b
  | (?<!ne\s)\bt['’]appartien(?:t|nent|dra|dront)\b
  | \br[ée]serv[ée]e?s?\s+[àa]\s+toi\b
  | \b(?:go|gestes?|clics?|choix|d[ée]cisions?|seuils?)\s+[àa]\s+toi\b
  | \b(?:go|gestes?|clics?|choix|d[ée]cisions?|seuils?)\b[^.,;:\n]{0,30}\b(?:reste(?:nt)?|est|sont)\s+
    (?:bien\s+)?(?:le\s+tien|la\s+tienne|les\s+tien(?:ne)?s)\b
  | \bil\s+te\s+suffit\b
  | \bun\s+(?:tap|clic)\s+suffit\b
  | \b(?:dis|pr[ée]viens)[\s-]moi\s+(?:quand|une\s+fois|d[èe]s\s+que|lorsque)\b
  | (?<!\w)(?:[ée]l[ée]ment|item|t[âa]che)\s+POUR\s+TOI\s+(?:est\s+|reste\s+)?(?:d[ée]j[àa]\s+)?ouvert
  | \b(?:deviendra|devient|arrivera|t['’]arrive(?:ra)?)\s+(?:[^\s.,;:!?]+\s+){0,3}?POUR\s+TOI\b
  | \bil\s+me\s+faut\s+de\s+toi\b
  | \bj['’]attends\s+de\s+toi\b
  | \bexpect\s+from\s+you\b
  | \bj'ai\s+besoin\s+(?:que\s+tu|de\s+toi)\b
  | \bce\s+qu'il\s+(?:me\s+)?faut\s+de\s+toi\b
  | \btu\s+(?:dois|devras|devrais|peux|pourras|pourrais|n'as\s+qu'[àa]|as\s+juste\s+[àa]
           |auras\s+[àa]|vas\s+devoir|dois\s+juste|as\s+[àa])\s+(?:\w+\s+){0,2}?""" + FR_INF + r"""\b
  | \btu\s+""" + FR_PRES + r"""\b
  | \btu\s+(?:t['’]en\s+(?:occupes|charges)|t['’]y\s+colles|te\s+charges)\b
  | \btu\s+(?:le|la|les)\s+(?:prends|g[eè]res)\b
  | \bje\s+te\s+(?:le|la|les)\s+laisse\b
  | \bje\s+te\s+laisse\s+(?:faire|g[ée]rer|d[ée]cider|trancher|juger|choisir|t['’]en\s+occuper
       |la\s+main|le\s+soin)\b
  | (?:^[ \t]*|[.:;!?]\s+|(?<!CODE\s)\*\*\s*)""" + FR_IMP + r"""\s*""" + FR_OBJ + r"""
  | (?:^[ \t]*(?:[-*]|\d[.)])\s+)(?P<bullet>""" + FR_IMP + r""")\s*""" + FR_OBJ + r"""
  | \b(?:pour|si|quand|une\s+fois|d[èe]s\s+que|apr[èe]s|ensuite|sinon|alors)\b[^,.\n]{0,80},\s*"""
    + r"""(?P<sub>""" + FR_IMP + r""")\s*""" + FR_OBJ_CODE + r"""
  | \b""" + EN_VERB + r"""\b[^.\n]{0,60}(?<!for\s)\byourself\b
  | \bdo\s+(?:it|this|that|them|these|those)\s+yourself\b
  | \b(?:you|{names})""" + EN_MODAL + r"""\s+(?:\w+\s+){0,3}?""" + EN_VERB + r"""\b
  | \b(?:you|{names})\s+(?:\w+\s+){0,2}?manually\b
  | \b(?:I\s+need\s+you\s+to|need(?:ed)?\s+from\s+you|over\s+to\s+you|your\s+turn|left\s+for\s+you
       |(?:is|are)\s+(?:on|up\s+to)\s+you|(?:is|are)\s+your\s+call)\b
  | \b(?:can|could|will|would)\s+you\s+(?:please\s+)?(?:handle|take\s+care\s+of|deal\s+with
       |take\s+over|pick\s+(?:it|this|that)\s+up)\b
  | \bleave\s+(?:it|this|that|them|the\s+rest)\s+(?:to|with)\s+you\b
  | \b(?:is|are|stays?|remains?)\s+yours\b
  | \byours\s+(?:alone|to\s+\w)
  | \b(?:stays?|remains?)\s+with\s+you\b
  | \byour\s+gesture\b
  | \bthat['’]s\s+on\s+you\b
  | \bonly\s+you\s+can\b
  | (?<!what\s)\byou\s+(?:call|buy|accept|approve)\b
  | \b(?<!will\s)(?<!would\s)(?<!can\s)(?<!could\s)(?<!shall\s)(?<!['’]ll\s)
    (?:let\s+me\s+know|tell\s+me|ping\s+me)\s+(?:when|once|as\s+soon\s+as)\b
  | \byou(?:\s+(?:will|would|can|could)|['’]ll)\s+(?:let\s+me\s+know|tell\s+me|ping\s+me)
    \s+(?:when|once|as\s+soon\s+as)\b
  | (?:^|\n|[.:;!?]\s+|[-*]\s+|\d[.)]\s+|\*\*\s*)(?:then\s+|and\s+)?(?:paste|fill\s+(?:in|out)
       |tick|click|print|plug\s+in|type|log\s+in|sign\s+in|upload|retry|try\s+again|run)\b
       [^.\n]{0,60}\byour\b
""")

# Why only the user can, named in the same paragraph. The words of the asking contract (his own
# knowledge, a go on an irreversible or external gesture, a fact absent from every store), in
# English and French.
CLASS_RE = re.compile(r"\b(?:knowledge|connaissance|irr[ée]versibles?|external|externes?"
                      r"|absente?s?)\b", re.I)
# A check only a human may pass. "second factor" is the refusal's own example of one: until
# 2026-10-28 the refusal named it and this did not read it, so a re-answer that wrote "c'est ton
# second facteur" was refused again.
HUMAN_ONLY_RE = re.compile(
    r"\b(?:h?captcha|recaptcha|2fa|two[\s-]factor|second[\s-]fact(?:or|eur)s?"
    r"|deuxi[eè]me\s+facteur|double\s+authentification|otp|one[\s-]time\s+(?:code|password)"
    r"|verification\s+code|code\s+de\s+v[ée]rification|code\s+(?:par\s+)?sms|sms\s+code"
    r"|face\s*id|touch\s*id|passkey|biometric|biom[ée]tri\w*|security\s+key"
    r"|cl[ée]\s+de\s+s[ée]curit[ée]|yubikey)(?!\w)"
    r"|\b(?:forbid\w*|prohibit\w*|interdi\w+|breaks?|viol\w+)\b[^.\n]{0,60}"
    r"\b(?:terms|conditions|cgu|tos|automat\w*|bots?)\b", re.I)
# A negation just before the match, in the same clause: "rien a faire de ton cote", "none on your
# side". A colon, a semicolon or a question mark closes the clause: in "rien n'est parti: tu t'en
# occupes ?" the negation reports a fact and the hand-back follows it (2026-10-24).
NEGATED = re.compile(r"(?:\b(?:pas|plus|rien|jamais|aucun|aucune|sans|ni|no|not|never|nothing"
                     r"|none|without|nobody|personne)\b|n'|n\u2019|\b(?:don|doesn|won|didn|isn"
                     r"|aren)'?t\b)[^.:;!?\n]{0,25}$", re.I)
# "toi-meme" after a past tense, a relative clause or a preposition reports or describes:
# "tu l'as toi-meme retire", "un texte que tu as choisi toi-meme", "un message vers toi-meme",
# "ta discussion avec toi-meme" (the name of a messenger's chat with oneself), "alors que tu
# voulais payer toi-meme" (a wish of the user's, told in the imperfect).
REPORTED = re.compile(r"(?:\btu\s+(?:l'|les\s+|la\s+|y\s+)?(?:as|avais|avait|es|[ée]tais"
                      r"|voulais|souhaitais|comptais|pensais|pr[ée]f[ée]rais)"
                      r"(?:\s+\w+)?|\bl'as-tu(?:\s+\w+)?|\bas-tu(?:\s+\w+)?|\bque\s+tu\s+\w+"
                      r"|\bviens\s+d'\w+|\bvers|\bavec|\b[àa]|\bpour|\bde)\s*$", re.I)

# 2026-11-04, the input asked as a question (see the docstring). The closing points at asks made
# higher up in the message: "tes reponses aux 4 questions", "les deux questions", "the questions
# above". The gate then reads the whole message, not only its tail.
POINTER_RE = re.compile(
    r"\btes\s+(?:\d+\s+|deux\s+|trois\s+|quatre\s+|cinq\s+)?r[ée]ponses\b"
    r"|\b(?:les|ces|mes)\s+(?:\d+|deux|trois|quatre|cinq)\s+(?:r[ée]ponses|questions)\b"
    r"|\bquestions\s+ci-dessus\b"
    r"|\byour\s+(?:\d+\s+)?answers\b|\b(?:the|these|my)\s+(?:\d+|two|three|four|five)\s+questions\b"
    r"|\bquestions\s+above\b|\babove\s+questions\b", re.I)
# A fact a public page holds: the name, date, place, link or format of an event, a company, a
# page. Asked of the user as a question, it is searched first.
LOOKUP_THING = (r"(?:[ée]v[ée]nements?|[ée]v[èe]nements?|events?|conf[ée]rences?|confs?|meetups?"
                r"|salons?|hackathons?|webinaires?|webinars?|ateliers?|workshops?|soir[ée]es?"
                r"|[ée]ditions?|sommets?|summits?|forums?|keynotes?|talks?|journ[ée]es?|pages?"
                r"|sites?|websites?|articles?|vid[ée]os?|podcasts?|repos?|entreprises?|bo[îi]tes?"
                r"|soci[ée]t[ée]s?|startups?|companies|company|cabinets?|offres?|annonces?"
                r"|postings?|listings?|papers?|livres?|books?|programmes?|programs?|formations?"
                r"|courses?|cours|concours|awards?)")
LOOKUP_ATTR = (r"(?:nom(?:\s+exact)?|intitul[ée]|titre|dates?|lieu|adresse|lien|url|format"
               r"|organisat\w+|h[ôo]tes?|tarifs?|horaires?|ville|(?:exact\s+)?names?|titles?"
               r"|venue|location|address|links?|urls?|organi[sz]ers?|hosts?|city"
               r"|when\s+(?:it|this|that)\s+(?:was|is|took)"
               r"|quand\s+(?:c['’][ée]tait|(?:il|elle|[çc]a)\s+a\s+eu\s+lieu))")
LOOKUP_A = re.compile(r"\b" + LOOKUP_THING + r"\b[^?\n]{0,80}?\b" + LOOKUP_ATTR + r"\b", re.I)
LOOKUP_B = re.compile(r"\b" + LOOKUP_ATTR + r"\b[^?\n]{0,40}?\b" + LOOKUP_THING + r"\b", re.I)
QUESTION = re.compile(r"[^.!?\n]*\?")
YOU = re.compile(r"\b(?:tu|ton|ta|tes|toi|te|you|your)\b|\bt['’]", re.I)
# A question whose ask is a go on the agent's own next gesture ("..., je le lui donne ?", "je
# l'envoie ?", "j'y vais ?", "shall I send it?"): its last clause is a short first-person yes or
# no with no question word. A page attribute in the clauses before it is the context of the go
# ("L'organisatrice du hackathon demande ton numero, je le lui donne ?"), not a fact asked of
# the user. A last clause that asks for content ("je mets quoi ?") is no go.
GO_QUESTION = re.compile(
    r"(?:^|[,;:\u2026]|\bet\s|\balors\s|\bdonc\s|\bso\s|\band\s)\s*"
    r"(?:(?:je|on)\s+|j['’])(?:[\w-]+['’]?\s*){1,4}\?\s*$"
    r"|(?:^|[,;:]|\bso\s|\band\s)\s*(?:shall|should|can|may)\s+(?:I|we)\s+(?:[\w-]+['’]?\s*){1,5}\?\s*$",
    re.I)
GO_ASKS_FOR = re.compile(r"\b(?:quoi|quel(?:le)?s?|comment|o[uù]|quand|combien|lequel|laquelle"
                         r"|what|which|where|when|how|who)\b", re.I)
# A question about what the user wants is a preference, his to give: "quelle date veux-tu ?"
CHOICE = re.compile(r"\b(?:veux|voudrais|pr[ée]f[èe]res?|choisis|choisir|souhaites?|want|prefer"
                    r"|would\s+you\s+like|pick|choose)\b", re.I)
# The search said done, in the paragraph of the question: what lifts it.
SEARCHED_RE = re.compile(
    r"\bj['’]ai\s+(?:d[ée]j[àa]\s+|bien\s+)?(?:cherch[ée]|recherch[ée]|fouill[ée]|trouv[ée])"
    r"|\bje\s+n['’]ai\s+(?:rien\s+)?trouv[ée]|\bintrouvables?\b"
    r"|\baucune?\s+(?:page|r[ée]sultat|trace|annonce|mention)s?\b|\brien\s+(?:sur|dans)\s"
    r"|\bI\s+(?:already\s+)?(?:searched|looked\s+(?:it\s+)?up|looked\s+for|found)\b"
    r"|\b(?:couldn['’]t|could\s+not|didn['’]t|did\s+not)\s+find\b"
    r"|\bno\s+(?:page|result|listing|trace|mention)s?\b|\bnot\s+found\s+(?:on|in)\b"
    r"|\bnothing\s+(?:on|in)\s", re.I)
# Asking the user to confirm a reading the agent already wrote.
CONFIRM_RE = re.compile(
    r"\b(?:dis|dites)(?:[\s-]moi)?\s+si\s+c['’]est\s+(?:bien\s+)?(?:[çc]a\b|comme\s+[çc]a\b|juste\b"
    r"|correct\b|ce\s+que\b)"
    r"|\bpour\s+(?:me\s+)?dire\s+si\s+c['’]est\s+(?:bien\s+)?(?:[çc]a\b|comme\s+[çc]a\b|juste\b"
    r"|correct\b)"
    r"|\bc['’]est\s+bien\s+comme\s+[çc]a\s+que\s+tu\s+(?:le\s+)?vois\b"
    r"|\bis\s+(?:that|this)\s+how\s+you\s+see\s+it\b"
    r"|\btell\s+me\s+(?:if|whether)\s+(?:that|this)\s+(?:is|reads)\s+(?:right|correct|how\s+you)",
    re.I)
# The two classes that still lift a confirmation: a go on an irreversible or external gesture.
GO_CLASS_RE = re.compile(r"\b(?:irr[ée]versibles?|external|externes?)\b", re.I)
# An absence claimed over the agent's own files only is not the contract's absent (every store,
# ledger AND page): blanked before the class is read.
NARROW_ABSENT = re.compile(
    r"\babsente?s?\s+(?:de|d['’]|from|in|dans)\s+(?:tous\s+|toutes\s+|all\s+"
    r"|any\s+of\s+)?(?:mes|nos|my|our)\s+(?:propres\s+|own\s+)?(?:fichiers|files|notes|stores?"
    r"|ledgers?|docs?|documents?|donn[ée]es|data|repos?|d[ée]p[ôo]ts?|m[ée]moires?|memory"
    r"|sources)\b", re.I)
NARROW_ABSENT_WORDS = ("absent de tous mes fichiers", "absente de mes fichiers",
                       "absent from my files", "absent from our notes")

# What the refusal tells the model, kept in lists a test reads against the regexes above: every
# word the refusal says lifts a hand-back must be one CLASS_RE or HUMAN_ONLY_RE reads, and every
# word it says does not must be one neither reads (2026-10-28: the refusal named "a second factor",
# "a payment", "a preference" and "a commitment" as reasons, the gate read none of them, and
# sessions were refused again on the re-answer that used them).
CLASS_WORDS = ("knowledge", "connaissance", "irreversible", "irréversible", "irréversibles",
               "external", "externe", "externes", "absent", "absente")
HUMAN_ONLY_WORDS = ("captcha", "2FA", "two-factor", "second factor", "second facteur",
                    "one-time code", "code de vérification", "code SMS", "Face ID", "Touch ID",
                    "passkey", "biometric", "security key", "clé de sécurité")
NOT_A_CLASS = ("a payment", "un paiement", "a preference", "ta préférence", "a commitment",
               "un engagement", "your password", "ton mot de passe", "your money", "ton argent",
               "under your name", "à ton nom", "only you know", "toi seul le sais",
               "none of my files names any", "aucun de mes fichiers ne le dit", "ta règle",
               "your rule", "tu m'as demandé", "you asked me", "la machine ne paie jamais",
               "the machine never buys")
# A description with the user as its subject is read like an order: the shapes of the session
# that taught it (2026-10-27, an apostille and a consulate file, seven refusals, five of them a
# process or a calendar told with "tu" as the subject).
DESCRIPTIONS = ("si tu déposes le 28/10", "une fois payé, tu télécharges le PDF",
                "tu fais la demande en ligne")
# What the refusal adds for a question asking a fact a page holds, and for a reading put to the
# user to confirm (2026-11-04).
LOOKUP_TEXT = ("A question that asks the user for a fact a public page holds (the name, date, "
               "place, link or format of an event, a company, a page) is searched first: the web, "
               "the event's platform (Luma, Eventbrite, Meetup), the mailbox (its invitation or "
               "confirmation), the calendar. Use what you find and say where you found it. "
               "Knowledge or absent does not lift that question: what a page holds is neither "
               "the user's knowledge nor absent until a search came back empty, and only that "
               "search, said in its paragraph (\"j'ai cherché sur Luma, Eventbrite et dans la "
               "boîte : aucune trace\"), lifts it. What only the user's memory holds (what they "
               "did there, what struck them) stays theirs to give, with its class.")
CONFIRM_TEXT = ("Asking the user to confirm a reading you already wrote (\"dis-moi si c'est bien "
                "ça\", \"is that how you see it\") hands back a judgment that is in hand: write "
                "the sentence, the framing or the choice as yours, use it, and say the user "
                "can correct it. Knowledge does not lift it; a go on an irreversible or external "
                "gesture or a check only a human may pass still does.")
SENTENCE_MAX = 240
LISTED_MAX = 4

# The prime: what a refused session is told before each later closing is drafted. The shapes are
# the ones the 2026-10-30, 2026-11-03 and 2026-11-04 sessions were refused on, cut and
# neutralised; a test reads each against the gate (refused) and each rewrite (passes), so the
# prime never names a shape the gate allows or a rewrite it refuses. PRIME_NOT_READ are reasons
# those sessions wrote in their own words, each one in NOT_A_CLASS. PRIME_PROMPTS bounds the full
# text; past it the session keeps prime_short, the command alone, until it ends.
PRIME_SUFFIX = ".handback_gate.prime"
PRIME_PROMPTS = 12
PRIME_KEPT = 2
PRIME_SHAPES = ("No: you call the registry yourself", "quand ils arrivent, tu fais le geste 1",
                "tu signes le consentement et le certificat", "quand tu cliques sur Continue",
                "c'est toi qui le fermes depuis le tableau de bord",
                "alors que tu coches Yes sur leur politique IA",
                "Pour les voir, ouvre l'écran du serveur",
                "le choix te revient, puisque ta règle est que je n'écris rien sans ta demande",
                "ce geste reste le tien")
PRIME_REWRITES = ("au clic sur Continue, la page lance ses contrôles",
                  "le compte se ferme depuis le tableau de bord, à solde nul",
                  "quand ils arrivent, le geste 1 : ta signature, irréversible",
                  "le formulaire déclare Yes sur leur politique IA",
                  "je t'envoie les deux formulaires en captures",
                  "je bascule dès ton go, une écriture externe sur ton compte")
PRIME_NOT_READ = ("none of my files names any", "only you know", "ta règle", "tu m'as demandé")
PRIME_CHECKS = ("captcha", "2FA", "second factor", "passkey")
PRIME_EVENTS = ("UserPromptSubmit", "SessionStart")
SENTENCE_END = re.compile(r"[.!?](?=\s|$)|\n")

# The check: the gate handed to the producer as a command, this very script, so that a closing
# or a re-answer is read before it is shown (2026-11-03).
CHECK_FLAG = "--check"
# What a pass says (2026-11-10): a session checked "Réponse possible : oui, ..." (passes), then
# sent "Tu peux répondre oui, ..." (refused). The pass names the text it read as the one to send,
# and a refusal of a closing that differs from the last text checked in its turn says so first.
SEND_AS_CHECKED = ("Send this text as it is: a sentence reworded after this check is not "
                   "checked, and the Stop hook reads it again.")
CHECKED_RE = re.compile(r"handback_gate\.py['\"]?\s+--check\s+<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n"
                        r"(.*?)\n\1[ \t]*(?:\n|$)", re.S)
SCRIPT_PATH = os.path.abspath(__file__)

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


def sentence_of(para, start, end):
    """The sentence of `para` holding [start, end): from the stop or line break before it to the
    one after it, cut to SENTENCE_MAX characters around the span."""
    head = max((m.end() for m in SENTENCE_END.finditer(para[:start])), default=0)
    after = SENTENCE_END.search(para, end)
    stop = after.end() if after else len(para)
    if stop - head > SENTENCE_MAX:
        room = max(0, (SENTENCE_MAX - (end - start)) // 2)
        lo, hi = max(head, start - room), min(stop, end + room)
        return ("..." if lo > head else "") + " ".join(para[lo:hi].split()) + \
            ("..." if hi < stop else "")
    return " ".join(para[head:stop].split())


class Held(tuple):
    """One hand-back: (the phrase, its paragraph, its sentence), unpacked like a tuple, with its
    `kind`: "gesture" (HANDBACK_SRC), "lookup" (a fact a page holds, asked as a question) or
    "confirm" (a reading of the agent's put to the user to confirm)."""
    kind = "gesture"


def _held(kind, phrase, para, sentence):
    found = Held((phrase, para, sentence))
    found.kind = kind
    return found


def window(text):
    """What the gesture shapes are read on: the tail of `text`, or the whole message when that
    tail points at asks made higher up in it (POINTER_RE)."""
    text = text or ""
    tail = text[-TAIL_CHARS:]
    return text if POINTER_RE.search(prose(tail)) else tail


def has_class(para):
    """The paragraph names a word of the asking contract, an absence claimed over the agent's
    own files not counted (NARROW_ABSENT)."""
    return bool(CLASS_RE.search(NARROW_ABSENT.sub(" ", para)))


def lookups_in(text):
    """Every question of `text` that asks the user for a fact a public page holds (an event's,
    a company's or a page's name, date, place, link or format), its paragraph saying no search
    and no human-only check: a list of Held of kind "lookup". The whole message is read: a
    question is put to the user wherever it sits. Pure."""
    found = []
    for para in re.split(r"\n\s*\n", prose(text or "")):
        if SEARCHED_RE.search(para) or HUMAN_ONLY_RE.search(para):
            continue
        for q in QUESTION.finditer(para):
            asked = q.group(0)
            if not (LOOKUP_A.search(asked) or LOOKUP_B.search(asked)):
                continue
            if CHOICE.search(asked) or not (YOU.search(asked) or YOU.search(para)):
                continue
            go = GO_QUESTION.search(asked)
            if go and not GO_ASKS_FOR.search(go.group(0)):
                continue
            sentence = " ".join(asked.split()).lstrip("-*•0123456789.) ")
            found.append(_held("lookup", sentence[:160], para.strip(), sentence))
    return found


def confirms_in(text):
    """Every ask of `text` that the user confirm a reading the agent already wrote, its
    paragraph naming no go on an irreversible or external gesture and no human-only check: a
    list of Held of kind "confirm". Read on the whole message. Pure."""
    found = []
    for para in re.split(r"\n\s*\n", prose(text or "")):
        if GO_CLASS_RE.search(para) or HUMAN_ONLY_RE.search(para):
            continue
        for m in CONFIRM_RE.finditer(para):
            found.append(_held("confirm", " ".join(m.group(0).split()), para.strip(),
                               sentence_of(para, m.start(), m.end())))
    return found


def handbacks_in(text, cfg=None):
    """Every hand-back of `text` whose paragraph does not name why only the user can, in
    reading order: a list of Held, each (the phrase, its paragraph, its sentence). The gesture
    shapes are read on window(text), then the questions for a fact a page holds and the asks to
    confirm a reading, on the whole message. Pure."""
    text = text or ""
    tail = text[-TAIL_CHARS:]
    if tail.lstrip().startswith(("{", "[")) or text.lstrip().startswith(("{", "[")):
        return []
    rx = pattern(_settings(cfg))
    found = []
    for para in re.split(r"\n\s*\n", prose(window(text))):
        if has_class(para) or HUMAN_ONLY_RE.search(para):
            continue
        for m in rx.finditer(para):
            # an imperative's match opens on the stop before it ("Rien n'est parti. Colle le
            # jeton"): what precedes that stop is another clause
            if m.group(0)[:1] not in ".:;!?" \
                    and NEGATED.search(para[max(0, m.start() - 40):m.start()]):
                continue
            sub = m.groupdict().get("sub")
            if sub and FR_AMBIGUOUS.match(sub.strip()):
                head = max((e.end() for e in SENTENCE_END.finditer(para[:m.start()])), default=0)
                if SUBJECT_HEAD.match(para[head:m.start()]):
                    continue
            bullet = m.groupdict().get("bullet")
            if bullet and FR_AMBIGUOUS.match(bullet.strip()) and not SECOND_PERSON.search(para):
                continue
            if re.match(r"toi[\s-]m", m.group(0), re.I) \
                    and REPORTED.search(para[max(0, m.start() - 30):m.start()]):
                continue
            lead = len(m.group(0)) - len(m.group(0).lstrip(".:;!?* \t\n"))
            found.append(_held("gesture", " ".join(m.group(0).split()), para.strip(),
                               sentence_of(para, m.start() + lead, m.end())))
    return found + lookups_in(text) + confirms_in(text)


def handback_in(text, cfg=None):
    """(the hand-back phrase, its paragraph) for the first hand-back of `text` (handbacks_in)
    whose paragraph does not name why only the user can, else None. Pure."""
    found = handbacks_in(text, cfg)
    return found[0][:2] if found else None


def reason_text(found, cfg):
    """The block reason. `found` is what handbacks_in returned (a bare phrase is read as one
    hand-back with no sentence). It quotes the first phrase as ("..."), the shape a retraction
    gate reads, then each sentence the gate read, so that one re-answer rewrites them all, and
    names the exact words the gate reads as a reason."""
    if isinstance(found, str):
        found = [(found, "", "")]
    where = "".join("\n- (\"{}\") in: {}".format(p[:160], s.lstrip("-*• "))
                    for p, _, s in found[:LISTED_MAX] if s)
    if len(found) > LISTED_MAX:
        where += "\n- and {} more in the same closing.".format(len(found) - LISTED_MAX)
    text = ("The final message hands the user a gesture or an input (\"{}\")."
            .format(found[0][0][:160]))
    if where:
        text += (" Where the gate read it:" if len(found) == 1 else
                 " The gate read {} of them, rewrite them all in the same answer:"
                 .format(len(found))) + where + "\n"
    else:
        text += " "
    kinds = {getattr(f, "kind", "gesture") for f in found}
    if "lookup" in kinds:
        text += LOOKUP_TEXT + " "
    if "confirm" in kinds:
        text += CONFIRM_TEXT + " "
    text += ("Do it yourself "
             "first whenever your tools can: fill the form (a placeholder value where a field is "
             "not the user's to give, a throwaway phone number for instance), run the test or the "
             "command, tick the boxes, read the page or the mailbox, create the account, paste the "
             "link. Before saying a gesture is the user's, check what your tools can reach (a "
             "provider's API, a token you hold): an irreversible gesture they can perform is "
             "asked as a go and then performed by you, never handed over. "
             "Hand back only what the user alone holds, and say why in the same paragraph: "
             "KNOWLEDGE (a fact or a preference only the user has), an IRREVERSIBLE or EXTERNAL "
             "gesture that needs the user's go (a send, a payment, a public post, a commitment), "
             "a fact ABSENT from every store, file and page you can read, or a check only a human "
             "may pass (a captcha, a second factor, a biometric, a site whose terms forbid "
             "automation). A physical gesture is not a reason: find the path that does not need "
             "it. A description is read like an order: a step with the user as its subject ({}) "
             "hands it over even when it only tells a process or a calendar. Write who does it: "
             "you (\"je télécharge le PDF\"), the third party (\"le centre renvoie les "
             "originaux\"), no one when it is a date (\"après le dépôt du 28/10\"), or the user "
             "with the reason named when it is truly the user's. The gate reads the reason from "
             "these words only, in the same paragraph as the hand-back (a paragraph ends at a "
             "blank line): {}; or a check by its name: {}; or terms, conditions or CGU that "
             "forbid automation. {} alone are not read, and an absence claimed over your own "
             "files ({}) is not the class absent: absent means from every store AND every page. "
             "Another wording of whose it is (\"reste le tien\", \"t'appartient\") is the same "
             "hand-back, the word above is what lifts it. "
             "A closing that points at asks higher up (\"tes réponses aux 4 questions\") is read "
             "whole. A gesture already classified higher up needs its word again where it is "
             "mentioned again (a reco, an option, a next step; a table is one paragraph). Before "
             "the re-answer is shown, {}. Every later closing of this session is read the same "
             "way, a gesture mentioned again included, and so is a re-answer written for another "
             "hook's refusal. (handback-gate)").format(
                 ", ".join("\"{}\"".format(d) for d in DESCRIPTIONS),
                 ", ".join(CLASS_WORDS), ", ".join(HUMAN_ONLY_WORDS),
                 ", ".join("\"{}\"".format(w) for w in NOT_A_CLASS),
                 ", ".join("\"{}\"".format(w) for w in NARROW_ABSENT_WORDS[:2]), check_how())
    suffix = cfg.get("reason_suffix")
    return text + " " + suffix if suffix else text


def check_command():
    """The heredoc that opens a --check of this very script, ready to paste in a Bash call."""
    return "python3 {} {} <<'EOF'".format(shlex.quote(SCRIPT_PATH), CHECK_FLAG)


def check_how():
    """How to run the check, the words the prime and the refusal both use."""
    return ("pass it through the gate itself, the Stop hook's own reading with its settings: "
            "`{}`, then the text, then a line EOF (exit 0: it passes; exit 1: it lists each "
            "sentence the Stop hook would refuse, so rewrite them and check again). What you "
            "send is the text that passed, word for word: a sentence reworded after the check "
            "is a new draft, checked again before it is sent"
            ).format(check_command())


def check_report(text, cfg=None):
    """(exit code, report) for a draft: 0 when the Stop hook would let it end the turn, 1 with
    every hand-back it would refuse and its sentence, and what lifts each kind. The same
    handbacks_in, so the same window, lookups and confirmations. Pure."""
    found = handbacks_in(text, cfg)
    if not found:
        return 0, ("handback-gate --check: passes, the Stop hook would let this text end the "
                   "turn. " + SEND_AS_CHECKED)
    n = len(found)
    lines = ["handback-gate --check: the Stop hook would refuse this text, {} hand-back{} with "
             "no reason in {} paragraph:".format(n, "" if n == 1 else "s",
                                                 "its" if n == 1 else "their")]
    lines += ["- (\"{}\") in: {}".format(p[:160], s.lstrip("-*• ")) for p, _, s in found]
    kinds = {getattr(f, "kind", "gesture") for f in found}
    if "lookup" in kinds:
        lines.append(LOOKUP_TEXT)
    if "confirm" in kinds:
        lines.append(CONFIRM_TEXT)
    lines.append("Name the real actor (I, the third party, nobody), or keep it the user's and "
                 "write in that same paragraph one of: {}; or a check by its name: {}. A reason "
                 "in other words ({}) is not read, nor an absence claimed over your own files "
                 "({}). Another wording of whose it is (\"reste le tien\", \"t'appartient\") is "
                 "the same hand-back, the word above is what lifts it. Then check again.".format(
                     ", ".join(CLASS_WORDS), ", ".join(HUMAN_ONLY_WORDS),
                     ", ".join("\"{}\"".format(w) for w in PRIME_NOT_READ),
                     ", ".join("\"{}\"".format(w) for w in NARROW_ABSENT_WORDS[:2])))
    return 1, "\n".join(lines)


def on_check(argv):
    """--check [FILE]: the report on stdout and its exit code; a file that cannot be read or a
    bad configuration is exit 2 on stderr, never a pass."""
    i = argv.index(CHECK_FLAG)
    path = argv[i + 1] if len(argv) > i + 1 else None
    try:
        if path:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        else:
            text = sys.stdin.read()
        cfg = _settings(None)
        pattern(cfg)
    except (OSError, UnicodeDecodeError, config.ConfigError) as e:
        print("handback-gate --check: {}".format(e), file=sys.stderr)
        return 2
    code, report = check_report(text, cfg)
    print(report)
    return code


def prime_path(payload):
    return trace.session_file(trace.session_key(payload), PRIME_SUFFIX)


def prime_state(payload):
    """The session's refusal state: {"count", "sentences", "since"} (since = prompts submitted
    after the last refusal), or None in a session the gate never refused."""
    try:
        with open(prime_path(payload), encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and state.get("count") else None


def _write_prime(payload, state):
    with open(prime_path(payload), "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)


def record_refusal(payload, found):
    """One more refusal of this session: the count, the sentences it read, the prompt counter
    back to 0."""
    state = prime_state(payload) or {"count": 0, "sentences": []}
    kept = [s for s in state.get("sentences") or [] if isinstance(s, str)]
    for _, _, sentence in found:
        sentence = sentence.lstrip("-*• ")
        if sentence and sentence not in kept:
            kept.append(sentence)
    _write_prime(payload, {"count": int(state.get("count") or 0) + 1,
                           "sentences": kept[-PRIME_KEPT:], "since": 0})


def prime_text(state):
    """What the producer reads before it drafts a closing in a session the gate refused. Pure."""
    n = int(state.get("count") or 0)
    last = [s for s in state.get("sentences") or [] if isinstance(s, str)][-PRIME_KEPT:]
    quoted = "; ".join("\"{}\"".format(s[:SENTENCE_MAX].rstrip(".!? ")) for s in last)
    return ("handback-gate refused {} closing{} of this session: a gesture with the user as its "
            "subject and no reason in its paragraph.{} Before you end a turn here, write the "
            "closing, then {}; send it only once it passes. Your own reading misses what the "
            "gate reads: sessions primed with this rule were refused again on a reason written "
            "in other words ({}), on a step, an option, a reco, an argument, a process or a way "
            "to look at a page told with the user as the one who acts ({}), and on a reco "
            "repeating a gesture classified higher up. Each one names its real actor instead (I, "
            "the third party, nobody: {}), or carries IN THAT SAME PARAGRAPH a word the gate "
            "reads: {}; or a check by its name: {}. A gesture already classified higher up needs "
            "its word again where it is mentioned again, and a reason in the next paragraph is "
            "not read: a paragraph ends at a blank line, a table is one paragraph. A re-answer "
            "to another hook's refusal is read the same way. A question for a fact a page holds "
            "(an event's name, date or link) is searched first, and no class word lifts it, only "
            "the search said in its paragraph; a reading you wrote is used as yours, never put "
            "to the user to confirm. (handback-gate)"
            ).format(
                n, "" if n == 1 else "s",
                " Last refused: {}.".format(quoted) if quoted else "",
                check_how(),
                ", ".join("\"{}\"".format(s) for s in PRIME_NOT_READ),
                ", ".join("\"{}\"".format(s) for s in PRIME_SHAPES),
                ", ".join("\"{}\"".format(s) for s in PRIME_REWRITES),
                ", ".join(CLASS_WORDS), ", ".join(PRIME_CHECKS))


def prime_short(state):
    """What a session reads past PRIME_PROMPTS prompts after its last refusal: the command, which
    is what holds. Pure."""
    n = int(state.get("count") or 0)
    return ("handback-gate refused {} closing{} of this session. Before you end a turn here, "
            "write the closing, then {}; send it only once it passes. (handback-gate)"
            ).format(n, "" if n == 1 else "s", check_how())


def on_prime():
    """UserPromptSubmit, and SessionStart after a compaction or a resume: in a session the gate
    refused, hand the rule to the producer as context, the full text within PRIME_PROMPTS
    prompts of the last refusal and after a compaction or a resume, prime_short otherwise."""
    payload = trace.read_payload()
    event = payload.get("hook_event_name")
    if event not in PRIME_EVENTS:
        return 0
    if event == "SessionStart" and payload.get("source") not in ("compact", "resume"):
        return 0
    state = prime_state(payload)
    if not state:
        return 0
    if event == "UserPromptSubmit":
        state["since"] = int(state.get("since") or 0) + 1
        _write_prime(payload, state)
    late = event == "UserPromptSubmit" and int(state.get("since") or 0) > PRIME_PROMPTS
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": prime_short(state) if late else prime_text(state)}},
        ensure_ascii=False))
    return 0


def _is_prompt(row):
    """A row the user typed: a user row that is no tool result and no hook's feedback."""
    if row.get("type") != "user" or row.get("isSidechain") or row.get("isMeta"):
        return False
    content = (row.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
            return False
        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    return isinstance(content, str) and not content.lstrip().startswith("Stop hook feedback")


def last_checked(transcript_path):
    """The text the last --check of this script read in the current turn (after the last
    prompt the user typed), from its heredoc, or None."""
    checked = None
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"user"' not in line and "--check" not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if _is_prompt(row):
                    checked = None
                    continue
                if row.get("type") != "assistant" or row.get("isSidechain"):
                    continue
                for c in (row.get("message") or {}).get("content") or []:
                    if not (isinstance(c, dict) and c.get("type") == "tool_use"):
                        continue
                    cmd = (c.get("input") or {}).get("command")
                    m = CHECKED_RE.search(cmd) if isinstance(cmd, str) else None
                    if m:
                        checked = m.group(2)
    except (OSError, TypeError):
        return None
    return checked


def _flat(text):
    return " ".join((text or "").split())


def drift_text(found, final, checked):
    """What a refusal says first when the closing is not the text the turn's last --check read:
    the refused sentences that check never saw. '' when there is none. Pure."""
    if checked is None or _flat(checked) == _flat(final):
        return ""
    seen = _flat(prose(checked))
    new = [s.lstrip("-*• ") for _, _, s in found if _flat(s.lstrip("-*• ")) not in seen]
    if not new:
        return ""
    return ("This closing is not the text your --check passed: {} written after the check "
            "({}). Send the text the check passed, word for word, or check the new wording "
            "before it is sent.\n").format(
                "this sentence was" if len(new) == 1 else "these sentences were",
                "; ".join("\"{}\"".format(s[:SENTENCE_MAX]) for s in new[:LISTED_MAX]))


def on_stop():
    payload = trace.read_payload()
    if payload.get("hook_event_name") != "Stop":
        return 0
    cfg = trace.plugin_settings(payload, root=PLUGIN_ROOT)
    pattern(cfg)  # a bad name list or extra is a loud crash even on a clean message
    final = transcript.final_text(payload)
    found = handbacks_in(final, cfg)
    if not found:
        trace.chain_clear(HOOK, payload)
        return 0
    phrase = found[0][0]
    if not trace.chain_should_block(HOOK, payload, "handback: " + phrase):
        print(trace.cap_message(HOOK, "handback: " + phrase))
        return 0
    trace.witness("handback_gate", "Stop", "handback_no_class", payload)
    record_refusal(payload, found)
    drift = drift_text(found, final, last_checked(payload.get("transcript_path") or ""))
    print(trace.stop_block(drift + reason_text(found, cfg), payload, final))
    return 0


def main():
    trace.configure(sys.argv)
    if CHECK_FLAG in sys.argv:
        return on_check(sys.argv)
    if "--prime" in sys.argv:
        return trace.run(HOOK, "UserPromptSubmit", on_prime, TIMEOUT_S)
    if "--print" in sys.argv:
        payload = trace.read_payload() if not sys.stdin.isatty() else {}
        found = handback_in(transcript.final_text(payload), None)
        print("handback-gate: " + ("would refuse on \"{}\"".format(found[0]) if found
                                   else "nothing handed back, silent"))
        return 0
    return trace.run(HOOK, "Stop", on_stop, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
