#!/usr/bin/env python3
"""Stop hook: a final message does not show an internal id standing alone.

Why a hook and not a sentence in a CLAUDE.md: "intake-068 jamais afficher les trucs comme ca pour
moi je comprend rien" became a rule of one user's CLAUDE.md on 2026-09-01 ("name the thing in my
words, an internal id may follow in parentheses only when I need it to run a command, and never
stands alone"). Six weeks later the closing block of a session still read "**c37 :** 5 lignes
dormantes" and "Prochaine etape : c37 et c43 quand tu veux", and the next message was "c37 et c43
c quoi". The codes the machine gives its items, commits, tasks and leads are clear to the agent
that has just read them in its tools; to the user they are noise, and the sentence alone loses to
that belief in the moment.

What it reads: the whole final message (`last_assistant_message` from the payload, the transcript
only when it lacks one), prose only. Fenced blocks, code spans holding a space (a command, which
needs the id to run), URLs, file paths, block quotes and quoted spans holding a space (a verbatim,
a draft for a third party) are dropped first. A one-token code span is read: `b6bef2fd` is an id
shown, backticks or not. A final message that is a JSON document (a headless worker's result) is
not prose and is not read.

What it refuses: a token of an id family (ID_FAMILIES: a git sha, a UUID, a ticket-like key such
as intake-068 or PROJ-123, plus the families the configuration adds) that no name precedes. It is
named when it sits inside a parenthesis and a word of prose stands between it and the previous id
of its line. "les 5 lignes dormantes (c37)" and "merged on main (b6bef2fd)" pass; "c37 : 5 lignes
dormantes", "c37 et c43 quand tu veux", "commit `b6bef2fd` is on main", "| intake-068 | 38/60 sent
|" are refused, and so is the second id of "cinq tests (intake-004, intake-195)", which names no
test ("intake 004 c'est quel bet?").

What is not an id: a label the message numbers itself, from 1, at the head of its list items
("a1.", "**c1**", "- T1:"), the lettered list a user answers with a code; a key whose prefix names
a standard or a version (NOT_A_KEY: SHA-256, ISO-8601, COVID-19); a year after a word of time
(pre-2025, FY-2026); the far end of a commit range `a..b`, named with its first. Nothing else
lifts it: no configuration key adds an exemption.

Measured before it shipped, on 38,071 turn-final messages of the user's own transcripts (all
projects, 50 days): the closing block his "c37 et c43 c quoi" answered is refused, and 788
messages (2.1 percent) with the default families, 1,187 (3.1 percent) with his three added. In
a hand-labelled random sample of 40 refusals, every one showed an id the user would have to look
up (a sha after "commit", an intake key heading a table row, a queue code heading a bullet, a
plan task id as a subject).

The bounds: the re-answer of a stop chain is checked like the first answer; the gate lets go after
CHAIN_CAP blocks in a row (a `cap` row in the ledger and a systemMessage). A crash or a timeout
lets the turn end and is reported loudly. No environment variable and no configuration key turns
the gate off. A SubagentStop is another event and is ignored.

Configuration (section `bare-id-gate` of trimwrit-gates.json): `id_patterns_extra` adds id
families, one regex each, read as a whole token (a user's own codes: `c\\d{1,3}` for the items of
a review queue, `T\\d{1,2}` for a plan's tasks); `reason_suffix` is appended to the refusal.

Outputs:
    exit 0 with nothing on stdout                       the session ends normally
    exit 0 with {"decision": "block", "reason": ...}    the session continues with this message

The UserPromptSubmit and SessionStart (compact, resume) entry, `--prime`, hands the plain-words
rule to the producer as additionalContext before it writes; it refuses nothing.

Utilities:
    python3 bare_id_gate.py --print       what it would say on the last message, blocking nothing
"""
import json
import os
import re
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_ROOT)
from gatekit import config, trace, transcript  # noqa: E402

HOOK = "bare_id_gate.py"
TIMEOUT_S = 8


def _cases(words):
    """Each word in lower case, capitalized and upper case: one source for Python and for the
    JavaScript engine that compiles the eval grader, which has no scoped case flag."""
    out = []
    for w in words:
        for v in (w, w.capitalize(), w.upper()):
            if v not in out:
                out.append(v)
    return "|".join(out)


# A key-shaped token that names a standard, a version or a program, not an item of ours.
NOT_A_KEY = r"(?:" + _cases(["covid", "sha", "iso", "rfc", "utf", "gpt", "ipv", "cve", "win",
                             "windows", "ubuntu", "python", "node", "ecma", "es", "top",
                             "fortune"]) + r")-"
# A year after a word of time is a date: pre-2025, early-2025, FY-2026, DV-2027.
YEAR = r"(?:" + _cases(["pre", "post", "early", "mid", "late", "end", "start", "fy", "cy", "dv",
                        "h1", "h2", "q1", "q2", "q3", "q4", "spring", "summer", "fall",
                        "autumn", "winter"]) + r")-(?:19|20)\d\d(?![\w-])"

# Where a token starts and ends: not inside a word, a path, an address, an anchor or a key.
LEFT = r"(?<![\w#/@`-])(?<![^.]\.)"
RIGHT = r"(?![\w/-]|\.\w)"
# Each family is a whole token between LEFT and RIGHT.
ID_FAMILIES = [
    ("git sha", r"(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}"),
    ("uuid", r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"),
    ("key", r"(?!" + NOT_A_KEY + "|" + YEAR + r")[A-Za-z][A-Za-z_]+-\d{2,}"),
]

FENCE = re.compile(r"```.*?```", re.S)
CODE = re.compile(r"`([^`\n]{0,300})`")
URL = re.compile(r"(?:https?://|www\.)[^\s)>\]]+")
# A file path or a route: a slash, and a run of three letters somewhere in the token.
PATH = re.compile(r"[\w.~@:-]*/[\w./~@:-]+")
BLOCKQUOTE = re.compile(r"^[ \t]*>.*$", re.M)
QUOTED = re.compile("\"[^\"\\n]{0,400}\"|\u201c[^\u201d\\n]{0,400}\u201d"
                    "|\u00ab[^\u00bb\\n]{0,400}\u00bb")
# A list item headed by a label: "a1.", "- **c1** ...", "T1:", "1. b2)".
LABEL_HEAD = re.compile(r"^[ \t]*(?:[-*+][ \t]+|\d{1,2}[.)][ \t]+)?(?:\*\*|__)?"
                        r"([A-Za-z]{1,2})(\d{1,3})(?:(?:\*\*|__)(?=\s|[.):,])|[ \t]*[.):](?=\s|\*|_|$))",
                        re.M)
PROSE_WORD = re.compile(r"[^\W\d_]{3}")

_COMPILED = {}


def _strings(where, value):
    if not isinstance(value, list) or not all(isinstance(a, str) for a in value):
        raise config.ConfigError("bare-id-gate: {} must be a list of strings".format(where))
    return value


def families(cfg):
    """[(name, compiled)] for ID_FAMILIES and the configured `id_patterns_extra`. An extra that
    is not a string or does not compile is a config.ConfigError."""
    extras = tuple(_strings("id_patterns_extra", cfg.get("id_patterns_extra") or []))
    if extras not in _COMPILED:
        out = []
        for name, src in ID_FAMILIES + [("configured", e) for e in extras]:
            try:
                out.append((name, re.compile(LEFT + "(?:" + src + ")" + RIGHT)))
            except re.error as e:
                raise config.ConfigError("bare-id-gate id_patterns_extra {!r}: {}".format(src, e))
        _COMPILED[extras] = out
    return _COMPILED[extras]


def _settings(cfg):
    if cfg is not None:
        return cfg
    return trace.plugin_settings({"cwd": os.getcwd()}, root=PLUGIN_ROOT)


def _blank(m):
    return re.sub(r"[^\n]", " ", m.group(0))


def prose(text):
    """The text the gate reads, every character kept at its offset: fences, command spans, URLs,
    paths, block quotes and quoted spans holding a space blanked; a one-token code span loses
    only its backticks."""
    t = FENCE.sub(_blank, text or "")
    t = CODE.sub(lambda m: _blank(m) if re.search(r"\s", m.group(1))
                 else " " + m.group(1) + " ", t)
    t = URL.sub(_blank, t)
    t = PATH.sub(lambda m: _blank(m) if PROSE_WORD.search(m.group(0)) else m.group(0), t)
    t = BLOCKQUOTE.sub(_blank, t)
    return QUOTED.sub(lambda m: _blank(m) if re.search(r"\s", m.group(0)) else m.group(0), t)


def labels(text):
    """The labels a message numbers itself: every head of a list item whose prefix also heads
    an item numbered 1 in the same message."""
    by_prefix = {}
    for m in LABEL_HEAD.finditer(text):
        by_prefix.setdefault(m.group(1), set()).add(int(m.group(2)))
    return {p + str(n) for p, ns in by_prefix.items() if 1 in ns for n in ns}


def in_parenthesis(text, start):
    """True when the token at `start` sits inside a parenthesis opened earlier on its line."""
    depth = 0
    for ch in text[text.rfind("\n", 0, start) + 1:start]:
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
    return depth > 0


def bare_ids(text, cfg=None):
    """[(family, token)] for every id of `text` that no name precedes, in reading order, each
    token once. Pure.

    An id is named when it sits inside a parenthesis and a word of prose stands between it and
    the previous id of its line (the line start when it is the first): "les 5 lignes dormantes
    (c37)", "(commit b4ee70e4, journal dd6e14c4)". A list of ids in one parenthesis names only
    its first, "cinq tests (intake-004, intake-195)" leaves the second bare, since the user asked
    which bet that was; a commit range `a..b` takes the name of its first end."""
    if not text or text.lstrip().startswith(("{", "[")):
        return []
    t = prose(text)
    own = labels(t)
    hits = sorted((m.start(), m.end(), name, m.group(0))
                  for name, rx in families(_settings(cfg)) for m in rx.finditer(t)
                  if m.group(0) not in own)
    found, prev_end, prev_named = [], 0, False
    for start, end, name, tok in hits:
        scope = max(prev_end, t.rfind("\n", 0, start) + 1)
        named = in_parenthesis(t, start) and (
            bool(PROSE_WORD.search(t[scope:start]))
            or (prev_named and scope == prev_end and t[scope:start] in ("..", "...")))
        if not named:
            found.append((name, tok))
        prev_end, prev_named = end, named
    out, seen = [], set()
    for name, tok in found:
        if tok not in seen:
            seen.add(tok)
            out.append((name, tok))
    return out


def reason_text(found, cfg):
    shown = ", ".join("\"{}\"".format(tok) for _, tok in found[:5])
    text = ("The final message shows an internal id standing alone ({}). The user does not know "
            "the codes the machine gives its items, commits, tasks or leads. Rewrite it: name "
            "each thing in the user's words first (what it is, the person's name and company, "
            "the project's name, what waits and why), then put the id after that name in "
            "parentheses, and only when the user needs it to run a command or to answer: \"the "
            "five dormant rows (c37)\", \"merged on main (b6bef2fd)\". Never let an id head a "
            "line, a label, a table cell or a next step; drop it when nothing needs it. "
            "(bare-id-gate)".format(shown))
    suffix = cfg.get("reason_suffix")
    return text + " " + suffix if suffix else text


# ---- plain words -------------------------------------------------------------------------
# 2026-11-14, lesson s:explained-in-jargon-not-plain-words: four closing blocks in five days
# (a print margin, a quota queue, a sales video, a Cloudflare setup) drew "pas compris c'est quoi
# en mot simple le pb", "jai pas compris concrtement ca fait quoi 30 mots", "moi qui connais pas
# le truc... jai pas compris", "now? jai pas comrpis? peut pas faire sans worker cloudflare?".
# Each was correct and none said, in words a person who never saw the tool follows, what the
# thing does for the user. A long final message carries one line headed by a plain marker, that
# line says it in at most PLAIN_MAX_WORDS words, with no code, file, setting name or acronym.
MIN_WORDS = 80
PLAIN_MAX_WORDS = 40
# What the producer is told to aim at: the over-long lines of the lesson of 2026-11-25 ran 41 to
# 47 words, written to the limit with no counter; a target well under it is hit.
PLAIN_TARGET_WORDS = 25
# A reply that skips the line aims well under MIN_WORDS: told "under 80" one ran 83.
SHORT_TARGET_WORDS = 60
MARKERS = ["en clair", "en mots simples", "concr\u00e8tement", "concretement", "in plain words",
           "in plain terms", "plainly"]
MARKER = re.compile(r"^[ \t]*(?:[-*+][ \t]+)?(?:\*\*|__)?[ \t]*(?:" + "|".join(MARKERS) + r")"
                    r"[ \t]*(?:\*\*|__)?[ \t]*:[ \t]*(?:\*\*|__)?[ \t]*(.*)$", re.I | re.M)
# A new block of Markdown ends the plain line: a list item, a numbered item, a heading, a table.
NEW_BLOCK = re.compile(r"^[ \t]*(?:[-*+][ \t]+|\d{1,2}[.)][ \t]+|#{1,6}[ \t]|\|)")
WORD = re.compile(r"[^\W_]+(?:['\u2019][^\W_]+)?")
# Words a reader knows without the tool: units of money, time zones, countries, documents.
COMMON_CAPS = {"OK", "PDF", "US", "USA", "UK", "EU", "UE", "FR", "EN", "ES", "TVA", "TTC", "HT",
               "CEO", "CTO", "SMS", "TV", "AM", "PM", "UTC", "CST", "CET", "IA", "AI", "EUR",
               "USD", "PS", "RIB", "SIRET", "CV", "ID", "CDI", "CDD"}
# 2026-11-21, lesson r:loop:bare_id_gate:jargon_not_plain_words: "Touch ID" and "pas de CDI en
# France" were refused as acronyms in lines a French reader follows, and "le 25/11" as a path.
# A date or a fraction (digits and slashes only) is not a path.
DATE_LIKE = re.compile(r"^[\d./-]+$")
JARGON = [
    ("a code span", re.compile(r"`")),
    ("a link or a path", re.compile(r"https?://|www\.|[\w.~-]+/[\w.~/-]+")),
    ("a code name", re.compile(r"(?<![\w-])(?:[A-Za-z]\w*_\w+|[a-z]+[A-Z][A-Za-z]*\w*"
                               r"|\w+\.(?:py|sh|js|ts|json|jsonl|md|db|toml|ya?ml|html|txt))"
                               r"(?![\w-])")),
    ("a command flag", re.compile(r"(?<![\w-])--[a-z][\w-]*")),
    ("an acronym", re.compile(r"(?<![\w-])[A-Z]{2,6}s?(?![\w-])")),
]


# 2026-11-28, lesson s:explained-in-jargon-not-plain-words relapsed past 0.3.0 twice, each
# message holding a plain line the gate accepted. 2026-11-14: "En clair : livrer un fichier a un
# acheteur ne relance pas la banque ; ..." ("la banque" is the machine's eval bank) drew "pas tout
# a fait compris 80 char?". 2026-11-23: a report headed "Bets bloques en ce moment" listed three
# blocked bets in the tool's terms while its plain line spoke only of the new sending rule, and
# drew "pas comrpsi c'est stuck? c quoi les pb? on auras plus jamais le pb sur un bet?".
# So the plain line is refused on a word that names the machine's own parts, and a message that
# reports something blocked needs a plain line that says so.
HOUSE_WORDS = ["harness", "fixer", "fixers", "hook", "hooks", "gate", "gates", "worker",
               "workers", "builder", "pipeline", "scheduler", "backlog", "ledger", "eval",
               "evals", "autofix"]
# A word that says something is stuck, in the prose around the plain line.
STUCK = re.compile(r"(?<![\w-])(?:bloqu\w*|coinc\w*|stuck|blocked|blocker\w*|blocking)(?![\w-])",
                   re.I)
# What a plain line says about it: blocked, stuck, waiting, missing, refused, still to do, or
# that nothing is. A plain line that names the hold in other words ("deux informations manquent",
# "a safety pause keeps it", "two things need you") says it.
STUCK_SAID = re.compile(r"(?<![\w-])(?:bloqu\w*|coinc\w*|stuck|block\w*|attend\w*|wait\w*"
                        r"|en attente|manqu\w*|missing|refus\w*|rest(?:e|ent)|remain\w*"
                        r"|empêch\w*|pause\w*|needs? you)(?![\w-])", re.I)


def house_words(cfg):
    """The machine's own words a plain line may not hold: HOUSE_WORDS and `plain_jargon_extra`."""
    extra = _strings("plain_jargon_extra", (cfg or {}).get("plain_jargon_extra") or [])
    return HOUSE_WORDS + [w for w in extra if w]


def house_words_in(body, cfg):
    low = body.lower()
    return sorted({w for w in house_words(cfg)
                   if re.search(r"(?<![\w-])" + re.escape(w.lower()) + r"(?![\w-])", low)})


# A line the message gives to what is blocked: a heading, a line in bold alone, or the bold label
# heading a list item ("**Bets bloqués en ce moment**", "## Blockers", "- **Bloquant** : ...").
# A stuck word in passing ("le drive bloque le frais") is not a report of it: measured on 345
# long messages with a plain line, a stuck word anywhere in the prose drew 32 refusals, nearly all
# in passing; on a heading or a bold label it draws the few that report blocked things.
REPORT_LINE = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+.*|(?:\*\*|__)[^*_\n]+(?:\*\*|__)[ \t]*:?[ \t]*"
                         r"|(?:[-*+][ \t]+|\d{1,2}[.)][ \t]+)?(?:\*\*|__)[^*_\n]+(?:\*\*|__).*)$",
                         re.M)
BOLD = re.compile(r"(?:\*\*|__)([^*_\n]+)(?:\*\*|__)")


def stuck_unsaid(text, bodies):
    """The stuck word a heading or a bold label of the prose holds when no plain line says what
    is blocked, else None."""
    t = prose(MARKER.sub(_blank, FENCE.sub(_blank, text or "")))
    for line in REPORT_LINE.finditer(t):
        head = line.group(0)
        if not head.lstrip().startswith("#"):
            head = " ".join(BOLD.findall(head))
        m = STUCK.search(head)
        if m:
            if any(STUCK_SAID.search(b) for b in bodies):
                return None
            return m.group(0)
    return None


def word_count(text):
    return len(WORD.findall(prose(text)))


def plain_lines(text):
    """The bodies of the lines headed by a plain marker, a paragraph each (the marker line and
    the lines that follow it up to a blank line), fences left out. A list, a heading or a table
    glued under a finished line is another block (2026-11-22: a 15 word line above the bullets
    of a plan was read as 163 words); under a line that is empty or ends with a colon it is what
    that line says, and is read with it."""
    t = FENCE.sub(_blank, text or "")
    out = []
    for m in MARKER.finditer(t):
        body = [m.group(1)]
        for line in t[m.end():].split("\n")[1:]:
            if not line.strip() or MARKER.match(line):
                break
            said = " ".join(body).strip(" \t*_")
            if NEW_BLOCK.match(line) and said and not said.endswith(":"):
                break
            body.append(line)
        out.append(" ".join(b.strip() for b in body).strip(" *_"))
    return out


def over_length(body, n):
    """The over-length why, naming the cut: sessions trimmed 44 words to 41 and 42 and were
    refused again, because they counted by eye. Name how many words go and quote the ones past
    the limit, so the next line is counted for them."""
    words = list(WORD.finditer(body))
    tail = body[words[PLAIN_MAX_WORDS].start():].strip()
    return ("{} words, over {}: cut at least {} (the words past the {}th are \"{}\"); write it "
            "again at about {} words rather than trimming to the limit: keep what changed and "
            "what waits on him, drop a clause, a list item or a number"
            .format(n, PLAIN_MAX_WORDS, n - PLAIN_MAX_WORDS, PLAIN_MAX_WORDS, tail,
                    PLAIN_TARGET_WORDS))


def jargon_in(body, cfg=None):
    """[why] for one plain line: too long, empty, or holding a word only the tool's user reads."""
    why = []
    n = len(WORD.findall(body))
    if n == 0:
        why.append("it is empty")
    if n > PLAIN_MAX_WORDS:
        why.append(over_length(body, n))
    for name, rx in JARGON:
        hits = [m.group(0) for m in rx.finditer(body)
                if not (name == "an acronym" and m.group(0).rstrip("s") in COMMON_CAPS)
                and not (name == "a link or a path" and DATE_LIKE.match(m.group(0)))]
        if hits:
            why.append("{} ({})".format(name, ", ".join(sorted(set(hits))[:3])))
    ids = [m.group(0) for _, rx in families(_settings(cfg)) for m in rx.finditer(body)]
    if ids:
        why.append("an internal id ({})".format(", ".join(ids[:3])))
    house = house_words_in(body, _settings(cfg))
    if house:
        why.append("a word that names the machine's own parts ({}): say what that part does "
                   "for him instead".format(", ".join(house[:3])))
    return why


def plain_words_miss(text, cfg=None):
    """None when `text` needs no plain line or holds a good one, else the reason as a string.
    Pure. A message under MIN_WORDS words of prose or a JSON document needs none."""
    if not text or text.lstrip().startswith(("{", "[")) or word_count(text) < MIN_WORDS:
        return None
    bodies = plain_lines(text)
    if not bodies:
        return "missing"
    whys = [jargon_in(b, cfg) for b in bodies]
    if not any(not w for w in whys):
        return "; ".join(whys[0])
    stuck = stuck_unsaid(text, bodies)
    if stuck:
        return ("the message reports something blocked (\"{}\") and the plain line never says "
                "it: say there what is blocked, why, and whether it can happen again, or that "
                "nothing is".format(stuck))
    return None


def plain_reason(miss):
    head = ("The final message explains at length and never says, in plain words, what it is "
            "about." if miss == "missing" else
            "The plain-words line of the final message is not plain: {}.".format(miss))
    return (head + " The user often does not know the tool, the setting or the mechanism you "
            "just worked in, and a correct message he cannot follow forces him to ask \"what "
            "does it do, concretely\". Add ONE line headed \"En clair :\" (a French reply) or "
            "\"In plain words:\" (an English one), at most {} words, that a person who never saw "
            "the tool understands: what the thing is or what went wrong, what it changes for him "
            "(money, time, a site up or down, a mail sent or not), and whether he has anything to "
            "do. No code, no file or setting name, no command, no acronym, no id; name a tool by "
            "what it does (\"the service that serves our pages\", not \"the CDN Worker\"). "
            "When the message reports something blocked, the plain line says what is blocked, "
            "why, and whether it can happen again. "
            "(bare-id-gate, plain words)".format(PLAIN_MAX_WORDS))


# 2026-11-25, the same lesson after 0.2.1: five sessions, 31 plain-words refusals over 29 turns.
# The refusal text works (28 of those turns hold one such refusal, the next answer passes); what
# leaked was the FIRST answer of each turn, since nothing the producer reads just before it
# writes carries the rule. So the rule goes to the producer with every prompt, and after a
# compaction or a resume, whose continuation turn opens with no prompt.
PRIME_EVENTS = ("UserPromptSubmit", "SessionStart")


def prime_text():
    return ("Before you send the reply that ends this turn: if it runs past {} words, however "
            "short the question was, it carries ONE line headed \"En clair :\" (French reply) or "
            "\"In plain words:\" (English), about {} words, never over {}, followed by a blank "
            "line: what it is, what it changes for the user, what they have to do. No code, file, "
            "command, acronym, id or word naming the machine's parts (harness, fixer, bank); when "
            "the message reports something blocked, that line says what is blocked and why. "
            "Or answer in under {} words and skip the line. "
            "(bare-id-gate)".format(MIN_WORDS, PLAIN_TARGET_WORDS, PLAIN_MAX_WORDS,
                                     SHORT_TARGET_WORDS))


def on_prime():
    payload = trace.read_payload()
    event = payload.get("hook_event_name")
    if event not in PRIME_EVENTS:
        return 0
    if event == "SessionStart" and payload.get("source") not in ("compact", "resume"):
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event,
                                             "additionalContext": prime_text()}},
                     ensure_ascii=False))
    return 0


def on_stop():
    payload = trace.read_payload()
    if payload.get("hook_event_name") != "Stop":
        return 0
    cfg = trace.plugin_settings(payload, root=PLUGIN_ROOT)
    families(cfg)  # a bad extra is a loud crash even on a clean message
    final = transcript.final_text(payload)
    found = bare_ids(final, cfg)
    miss = plain_words_miss(final, cfg)
    if not found and not miss:
        trace.chain_clear(HOOK, payload)
        return 0
    details, reasons = [], []
    if found:
        details.append("bare id: " + ", ".join(tok for _, tok in found[:5]))
        reasons.append(reason_text(found, cfg))
    if miss:
        details.append("plain words: " + miss)
        reasons.append(plain_reason(miss))
    detail = "; ".join(details)
    if not trace.chain_should_block(HOOK, payload, detail):
        print(trace.cap_message(HOOK, detail))
        return 0
    if found:
        trace.witness("bare_id_gate", "Stop", "internal_id_bare", payload)
    if miss:
        trace.witness("bare_id_gate", "Stop", "jargon_not_plain_words", payload)
    print(trace.stop_block(" ".join(reasons), payload, final))
    return 0


def main():
    trace.configure(sys.argv)
    if "--prime" in sys.argv:
        return trace.run(HOOK, "UserPromptSubmit", on_prime, TIMEOUT_S)
    if "--print" in sys.argv:
        payload = trace.read_payload() if not sys.stdin.isatty() else {}
        final = transcript.final_text(payload)
        found = bare_ids(final, None)
        miss = plain_words_miss(final, None)
        print("bare-id-gate: " + ("would refuse on {}".format(", ".join(t for _, t in found))
                                  if found else "no bare id, silent"))
        print("bare-id-gate plain words: " + ("would refuse, " + miss if miss else "silent"))
        return 0
    return trace.run(HOOK, "Stop", on_stop, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
