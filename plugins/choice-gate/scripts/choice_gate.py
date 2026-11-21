#!/usr/bin/env python3
"""A choice the user stated in a session is applied, and stays applied after a compaction.

Why a hook and not a sentence in a CLAUDE.md (lesson s:session-feedback-dropped-not-applied,
eleven corrections from 2026-09-26 to 2026-11-20 in two projects): a long session builds a list
(a shopping list, a game polish pass, a day plan) over dozens of prompts and several compactions.
The user states a choice ("je pense prendre les assortiment de noix lidl", "Remove champomy",
"riesling + pinot noir"), the summary of the next compaction drops it, and a later version of the
list comes back with it undone. The user then repeats himself: "on a encore bcp de noix de cagout
alors quej ai dit un melange de tout?", "on prefera aldi pour les bouteilles que carrefour.
riselsing + pino noir i said". Nothing in the context carried his words any more.

Three hooks, one script:

- UserPromptSubmit (`--prompt`): every clause of a typed prompt that states a choice (a
  preference, an exclusion, a pick, a must, a repeat) is appended verbatim to a per-session
  ledger outside the context. A prompt that repeats an earlier choice ("i said", "j'ai dit", "on
  avait dit", "like i told you") gets the whole ledger back as context, and arms the Stop check
  below for this turn.
- SessionStart after a compaction or a resume (`--session`): the ledger is handed back verbatim,
  so the summary cannot be the only memory of what the user chose.
- Stop: the final message is refused when (a) a line keeps something the user excluded in this
  session ("Remove champomy", "pas de pack de coca cherry", "aldi pour les bouteilles que
  carrefour") with no removal word on that line, or (b) the user just repeated a choice and the
  answer names under half of the words of what he repeated. After three blocks in a row the turn
  ends (gatekit chain cap). A SubagentStop is ignored.
"""
import difflib
import hashlib
import json
import os
import re
import sys
import unicodedata

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_ROOT)
from gatekit import trace, transcript  # noqa: E402

HOOK = "stop_choice_gate.py"
TIMEOUT_S = 8
CLAUSE_MAX = 400
CONTEXT_MAX = 6000
INSIST_WORDS = 4
TERM_WORDS = 3
TYPED_MAX = 2500
ITEM_WORDS = 5
NOUN_BEFORE = {"de", "du", "le", "la", "les", "des", "un", "une", "the", "a", "taux"}
POSSESSIVE = {"ton", "ta", "tes", "mon", "ma", "mes", "son", "sa", "ses", "notre", "votre",
              "leur", "your", "my", "his", "her", "their", "our"}

NOT_A_PROMPT = ("<task-notification>", "<local-command", "<system-reminder>",
                "This session is being continued", "[Request interrupted",
                "Stop hook feedback:", "Base directory for this skill:", "<command-")

STOP = set("""
a au aux avec ce ces cet cette c ca dans de des du en et est il ils je j la le les l leur lui ma
mais me mes mon ne nous on ou par pas pour qu que qui sa se ses son sur ta te tes ton tu un une
vous y d s n m t elle elles eux tout tous toute toutes tres bien bon bcp aussi alors encore juste
fait faire peut etre plus moins comme quand si non oui ok ouais deja sinon peu genre truc trucs
the an and or of to in on for with is are be it this that these those you your my me we our
not no but so as at by from just also still then than very too before after again yes
said told dit avait ai great good nice were was had have
""".split())

CHOICE_SRC = (
    r"\b(?:prefer\w*|plutot|instead|rather|on prend|je prend\w*|prend\w*|on part sur|"
    r"on reste sur|reste sur|garde\w*|keep|use|utilise\w*|uniquement|only|toujours|tjr|"
    r"jamais|never|always|pas de|pas d'|sans|remove|drop|retire\w*|enleve\w*|supprime\w*|"
    r"vire\w*|kill|ajoute\w*|add|mets?|met|doi[st]|doivent|must|should|il faut|faut|"
    r"je veux|veut|want|j'aimerais|jaimerais|ideal\w*|mini\w*|max\w*|je pense|j'ai en tete|"
    r"on compte|compte pour|on se dit|non|wrong|pas bon|ca me va pas|remplace\w*|replace\w*)\b")
REPEAT_SRC = (
    r"(?:\b(?:i said|as i said|like i said|i told you|told you|i already said|"
    r"je\s+t\s*'?\s*ai\s+(?:deja\s+)?dit|"
    r"on\s+(?:l\s*'?\s*)?avait\s+(?:pas\s+|aussi\s+|deja\s+)?dit|on\s+a\s+dit|deja\s+dit)"
    r"|(?:\b|(?<=qu)|(?<=que))j\s*'?\s*ai\s+(?:deja\s+)?dit)\b")
REMOVE_SRC = (
    r"(?:\b(?:remove|drop|retire[rz]?|enleve[rz]?|supprime[rz]?|vire[rz]?)\s+"
    r"|(?:^|[,;:!]\s*|\bnon\s*,?\s*)(?:pas\s+de|pas\s+d'|sans|no|jamais\s+de)\s+)"
    r"(?P<t>[^.;:!?\n=]+)")
PREFER_SRC = (
    r"\b(?:prefer\w*|plutot)\b(?P<scope>[^.;!?\n]{0,80}?)\bque\s+"
    r"(?:le |la |les |l'|chez |du |des |de )?(?P<t>[^.;:!?,\n=]+)"
    r"|\b(?:plutot que|instead of|rather than)\s+(?:the |le |la |les |l'|du |des )?"
    r"(?P<t2>[^.;:!?,\n=]+)")
REMOVAL_CTX_SRC = (
    r"\b(?:retir\w*|enlev\w*|supprim\w*|sort\w*|remove\w*|dropp?\w*|plus de|plus aucun\w*|"
    r"la place|en moins|avant|before|etait|avai\w*|was|deleted|cut|vs|versus|compare\w*|que (?:la|le|les|l')|"
    r"sans|pas de|pas d'|aucun\w*|no|not|without|instead|au lieu|remplac\w*|replac\w*|"
    r"exclu\w*|plutot que|rather than|ni)\b")

CHOICE = re.compile(CHOICE_SRC)
REPEAT = re.compile(REPEAT_SRC)
REMOVE = re.compile(REMOVE_SRC)
PREFER = re.compile(PREFER_SRC)
REMOVAL_CTX = re.compile(REMOVAL_CTX_SRC)
ARROW_NO = re.compile(r"^\s*(?:non|no|nope|pas|remove|retire|enleve)\b|\bnon\s*\.?\s*$")
LISTED = re.compile(r"^\s*(?:[-*|>]|\d)|\d")
WORD = re.compile(r"[a-z0-9][a-z0-9'-]*")
SPLIT_ITEMS = re.compile(r",|\bni\b|\bet\b|\band\b|\bou\b|\bor\b|\+")


def fold(text):
    """Lowercase, accents stripped, curly apostrophes made straight."""
    text = (text or "").replace("’", "'").replace("‘", "'").lower()
    return "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))


def words(text):
    return [w.strip("'-") for w in WORD.findall(fold(text)) if w.strip("'-")]


def significant(text, cap=None):
    out = [w for w in words(text) if len(w) >= 3 and w not in STOP and not w.isdigit()]
    return out[:cap] if cap else out


def clauses(prompt):
    parts = re.split(r"(?<=[.!?])\s+|\n+", prompt or "")
    return [" ".join(p.split())[:CLAUSE_MAX] for p in parts if p.strip()]


def is_typed(prompt):
    """A prompt the user typed: not harness text, and not a machine brief or a paste (a headless
    worker's prompt runs to thousands of characters; his longest correction is 1,399)."""
    return bool((prompt or "").strip()) and len(prompt) <= TYPED_MAX \
        and not prompt.lstrip().startswith(NOT_A_PROMPT)


def is_choice(clause):
    f = fold(clause)
    return bool(CHOICE.search(f) or REPEAT.search(f))


def choice_clauses(prompt):
    """The clauses of a typed prompt that state a choice, verbatim."""
    if not is_typed(prompt):
        return []
    return [c for c in clauses(prompt) if is_choice(c)]


def exclusions(clause):
    """[{"words": [...], "ctx": [...]}] the clause excludes: "remove X", "pas de X", "sans X",
    "X => non", "X plutot que Y" (Y), "prefer X pour Z que Y" (Y, only on a line naming Z). A
    segment that asks a question rules nothing."""
    out = []

    def add(raw, ctx, last_only=False):
        """The item named first (or last, for "X => non"), and the short items listed after it
        ("champomy, hedgehog"): an item of more than ITEM_WORDS words is a sentence, no item."""
        items = [i for i in SPLIT_ITEMS.split(raw) if significant(i)]
        picked = items[-1:] if last_only else items[:1] + [
            i for i in items[1:4] if len(words(i)) <= 2]
        for item in picked:
            if len(words(item)) <= ITEM_WORDS:
                out.append({"words": significant(item, TERM_WORDS), "ctx": ctx})

    if re.match(r"\s*(?:pk|pq|pourquoi|why)\b", fold(clause)):
        return []  # "PK RETIRE LES CARTE": a complaint about a removal, no order to remove
    segments = re.split(r"=>|->", fold(clause))
    for i, seg in enumerate(segments):
        question = seg.rstrip().endswith("?")
        if question:
            continue
        for m in REMOVE.finditer(seg):
            before = words(seg[:m.start()])[-1:]
            if before and before[0] in NOUN_BEFORE:
                continue  # "le taux de drop", "un drop": a noun, no order
            if seg[m.end():m.end() + 1] == "?" or \
                    (words(m.group("t"))[:1] and words(m.group("t"))[0] in POSSESSIVE):
                continue  # "(si il drop dofus?)", "sans ton choix": no item excluded
            add(m.group("t"), [])
        for m in PREFER.finditer(seg):
            if m.group("t"):
                add(m.group("t"), significant(m.group("scope"))[1:])  # first word: the pick
            elif m.group("t2"):
                add(m.group("t2"), [])
        if i and ARROW_NO.search(seg):
            add(segments[i - 1], [], last_only=True)
    return out


def insist_words(prompt):
    """The words of what a prompt repeats ("riselsing + pino noir i said" -> riselsing, pino,
    noir), with the clause, or ([], None) when it repeats nothing."""
    if not is_typed(prompt):
        return [], None
    for c in clauses(prompt):
        f = fold(c)
        m = REPEAT.search(f)
        if not m:
            continue
        after = significant(re.split(r"=>|->|[,;(]", f[m.end():])[0])
        before = significant(re.split(r"=>|->|[,;)]", f[:m.start()])[-1])
        picked = after[:INSIST_WORDS] if after else before[-INSIST_WORDS:]
        if picked:
            return picked, c
    return [], None


# -- storage ----------------------------------------------------------------------------------

def _key(session_id):
    return hashlib.sha256((session_id or "no-id").encode("utf-8")).hexdigest()[:16]


def ledger_path(session_id):
    return os.path.join(trace.data_dir(), "choices", _key(session_id) + ".jsonl")


def insist_path(session_id):
    return os.path.join(trace.data_dir(), "choices", _key(session_id) + ".insist.json")


def read_ledger(session_id):
    try:
        with open(ledger_path(session_id), encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except FileNotFoundError:
        return []


def append_ledger(session_id, texts):
    """Every clause of a typed prompt, flagged `choice` when it states one: the choices are
    handed back, every clause can take an exclusion back ("9 bouteilles de vin?" after "pas de
    bouteilles")."""
    if not texts:
        return
    path = ledger_path(session_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for t in texts:
            fh.write(json.dumps({"at": trace.now_iso(), "text": t, "choice": is_choice(t)},
                                ensure_ascii=False) + "\n")


def set_insist(session_id, ws, clause):
    path = insist_path(session_id)
    if not ws:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"words": ws, "clause": clause}, fh, ensure_ascii=False)


def read_insist(session_id):
    try:
        with open(insist_path(session_id), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# -- judging ----------------------------------------------------------------------------------

def live_exclusions(entries):
    """The exclusions of the ledger still standing: one the user named again later outside an
    exclusion (he took it back) is dropped. Each carries the clause that ruled it."""
    live = []
    for e in entries:
        text = e.get("text") or ""
        own = exclusions(text) if e.get("choice", True) else []
        tokens = words(text)
        own_terms = [x["words"] for x in own]
        live = [x for x in live
                if x["words"] in own_terms or not all(_has(tokens, w) for w in x["words"])]
        for x in own:
            live.append(dict(x, clause=text))
    return live


def _has(tokens, w):
    """`w` among the tokens, or a plural or a singular of it ("bouteille", "bouteilles"); never
    a longer word that merely starts the same ("fami" is not "familiers", "archi" not "arche")."""
    return any(t == w or (t.startswith(w) and len(t) - len(w) <= 2) or
               (len(t) >= 4 and w.startswith(t) and len(w) - len(t) <= 1) for t in tokens)


def _near(tokens, w):
    if _has(tokens, w):
        return True
    return any(abs(len(t) - len(w)) <= 3 and
               difflib.SequenceMatcher(None, t, w).ratio() >= 0.75 for t in tokens)


def kept_exclusion(final_text, live):
    """(exclusion, line) of the first line of the final message that keeps something the user
    excluded, or None. Only a line that lists (a bullet, a table row, a quantity or a price) can
    keep an item; a line with a removal word ("retire", "sans", "plus de", "instead") is a line
    saying it went."""
    for line in (final_text or "").splitlines():
        f = fold(line)
        if not f.strip() or REMOVAL_CTX.search(f) or not LISTED.search(f):
            continue
        tokens = words(f)
        for x in live:
            if all(_has(tokens, w) for w in x["words"]) and \
                    (not x["ctx"] or any(_has(tokens, c) for c in x["ctx"])):
                return x, line.strip()
    return None


def insist_unapplied(final_text, insist):
    """The repeated words the final message does not name, when it names under half of them."""
    if not insist or not insist.get("words"):
        return []
    tokens = words(final_text)
    missing = [w for w in insist["words"] if not _near(tokens, w)]
    need = (len(insist["words"]) + 1) // 2
    return missing if len(insist["words"]) - len(missing) < need else []


def ledger_context(entries):
    lines, size = [], 0
    for e in reversed([e for e in entries if e.get("choice", True)]):
        line = "- {} \"{}\"".format((e.get("at") or "")[:16], e.get("text") or "")
        if size + len(line) > CONTEXT_MAX:
            break
        lines.append(line)
        size += len(line)
    return ("choice-gate: the choices the user stated in this session, verbatim, oldest first. A "
            "later one on the same item overrides an earlier one; a summary may have dropped "
            "them, this ledger did not. Every list, plan or version you produce applies each "
            "one still standing:\n" + "\n".join(reversed(lines)))


# -- hooks ------------------------------------------------------------------------------------

def on_prompt():
    payload = trace.read_payload()
    trace.plugin_settings(payload, root=PLUGIN_ROOT)
    prompt = payload.get("prompt") or ""
    if not is_typed(prompt):
        return 0
    sid = trace.session_key(payload)
    append_ledger(sid, clauses(prompt))
    ws, clause = insist_words(prompt)
    set_insist(sid, ws, clause)
    if ws:
        context = ("choice-gate: the user is repeating a choice he already stated (\"{}\"): an "
                   "earlier answer dropped it. Apply it in full now, and every other choice "
                   "below, and say in the answer what changed for it.\n\n{}").format(
                       clause, ledger_context(read_ledger(sid)))
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                 "additionalContext": context}},
                         ensure_ascii=False))
    return 0


def on_session():
    payload = trace.read_payload()
    trace.plugin_settings(payload, root=PLUGIN_ROOT)
    if payload.get("source") not in ("compact", "resume"):
        return 0
    entries = read_ledger(trace.session_key(payload))
    if any(e.get("choice", True) for e in entries):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": ledger_context(entries)}},
                         ensure_ascii=False))
    return 0


def judge(final_text, entries, insist):
    """(reason class, reason) of a refusal, or None."""
    hit = kept_exclusion(final_text, live_exclusions(entries))
    if hit:
        x, line = hit
        return "excluded_choice_kept", (
            "The final message keeps what the user excluded in this session: the line \"{}\" "
            "names \"{}\", and the user said \"{}\". Apply his choice in the list, plan or page "
            "itself (every copy of it), then answer again. If he took it back later, quote "
            "where. (choice-gate)").format(line[:200], " ".join(x["words"]), x["clause"])
    missing = insist_unapplied(final_text, insist)
    if missing:
        return "repeated_choice_unapplied", (
            "The user just repeated a choice he had already stated (\"{}\") and the answer does "
            "not name it ({} missing). Apply exactly what he said, everywhere it applies, and "
            "say what changed for it. (choice-gate)").format(insist["clause"],
                                                             ", ".join(missing))
    return None


def on_stop():
    payload = trace.read_payload()
    if payload.get("hook_event_name") != "Stop":
        return 0
    cfg = trace.plugin_settings(payload, root=PLUGIN_ROOT)
    sid = trace.session_key(payload)
    text = transcript.final_text(payload)
    verdict = judge(text, read_ledger(sid), read_insist(sid))
    if not verdict:
        trace.chain_clear(HOOK, payload)
        return 0
    kind, reason = verdict
    if not trace.chain_should_block(HOOK, payload, kind):
        print(trace.cap_message(HOOK, kind))
        return 0
    trace.witness("stop_choice_gate", "Stop", kind, payload)
    suffix = cfg.get("reason_suffix")
    print(trace.stop_block(reason + (" " + suffix if suffix else ""), payload, text))
    return 0


def main():
    trace.configure(sys.argv)
    if "--prompt" in sys.argv:
        return trace.run(HOOK, "UserPromptSubmit", on_prompt, TIMEOUT_S)
    if "--session" in sys.argv:
        return trace.run(HOOK, "SessionStart", on_session, TIMEOUT_S)
    return trace.run(HOOK, "Stop", on_stop, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
