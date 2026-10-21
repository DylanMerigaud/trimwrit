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


def on_stop():
    payload = trace.read_payload()
    if payload.get("hook_event_name") != "Stop":
        return 0
    cfg = trace.plugin_settings(payload, root=PLUGIN_ROOT)
    families(cfg)  # a bad extra is a loud crash even on a clean message
    found = bare_ids(transcript.final_text(payload), cfg)
    if not found:
        trace.chain_clear(HOOK, payload)
        return 0
    detail = "bare id: " + ", ".join(tok for _, tok in found[:5])
    if not trace.chain_should_block(HOOK, payload, detail):
        print(trace.cap_message(HOOK, detail))
        return 0
    trace.witness("bare_id_gate", "Stop", "internal_id_bare", payload)
    print(json.dumps({"decision": "block", "reason": reason_text(found, cfg)},
                     ensure_ascii=False))
    return 0


def main():
    trace.configure(sys.argv)
    if "--print" in sys.argv:
        payload = trace.read_payload() if not sys.stdin.isatty() else {}
        found = bare_ids(transcript.final_text(payload), None)
        print("bare-id-gate: " + ("would refuse on {}".format(", ".join(t for _, t in found))
                                  if found else "no bare id, silent"))
        return 0
    return trace.run(HOOK, "Stop", on_stop, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
