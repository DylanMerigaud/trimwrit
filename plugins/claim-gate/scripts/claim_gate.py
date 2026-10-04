#!/usr/bin/env python3
"""Stop hook: a final message does not claim a gesture done with no tool result that proves it.

Why a hook and not a sentence in a CLAUDE.md: of 302 hand confirmed corrections, 31 were a claim
against reality ("sent", "pushed", "merged", "deployed", "verified", "tests pass" said with
nothing in the turn that did or checked it). The sentence about verifying before claiming is
read, approved and negotiated away at the moment it would cost something; a Stop hook runs.

What it checks: the final assistant text (its last TAIL_CHARS, quoted lines removed), for a first
person or status claim of a gesture done, English and French:
    pushed    "I pushed", "pushed to main", "j'ai pousse", "est merge", "merged"
    sent      "I sent", "sent it", "j'ai envoye", "est envoye"
    deployed  "deployed", "j'ai deploye"
    tests     "tests pass", "all green", "N passed", "les tests passent", "tests verts"
    verified  "I verified", "j'ai verifie", "confirmed", "verifie"
against the tool calls of the SAME TURN (everything after the last typed user message): a claim
needs a tool call of its kind whose result is not an error (push: git push or gh pr merge; sent:
a send command or a send tool; deployed: vercel, deploy, launchctl; tests: pytest, vitest, go
test; verified: any Bash, Read, Grep, Glob or MCP read). A subagent or a workflow that ran in the
turn counts for every kind: its report is the receipt. No receipt: the stop is blocked and the
reason names the claim.

Not a claim: a negation ("not pushed", "n'est pas envoye"), a future ("I'll push", "to push"), a
question, a condition ("once merged"), a third party's act ("they sent"), and the word inside a
quoted line or backticks.

Configuration (section `claim-gate` of trimwrit-gates.json): `claims_extra` and `receipts_extra`
map a kind to extra regex alternatives, `void_extra` adds void markers, `reason_suffix` is
appended to the refusal. A crash or a timeout lets the turn end and is reported loudly; no
environment variable and no configuration key turns the gate off.

Outputs:
    exit 0 with nothing on stdout                       the session ends normally
    exit 0 with {"decision": "block", "reason": ...}    the session continues with this message

Utility:
    python3 claim_gate.py --check TRANSCRIPT.jsonl      what it would say, blocking nothing
"""
import json
import os
import re
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_ROOT)
from gatekit import config, trace, transcript  # noqa: E402

HOOK = "stop_claim_gate.py"
TIMEOUT_S = 8
TAIL_CHARS = 1500

QUOTE_LINE_RE = re.compile(r"^\s*>.*$", re.M)
CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
QUOTE_SPAN_RE = re.compile(r'\*"[^"]*"\*|"[^"\n]{0,200}"')

# kind -> claim pattern. Each is anchored on a first person subject, a status copula or a
# sentence-initial participle, so a sentence ABOUT an act ("the mail sent on 09-20") stays out.
SUBJ = r"(?:\bI(?:'ve|\s+have|'m)?|\bwe(?:'ve|\s+have)?|\bj'ai|\bon\s+a|\bj'ai\s+bien)\s+(?:just\s+|now\s+|bien\s+|enfin\s+|d[ée]j[aà]\s+)?"
STATE = r"(?:\b(?:is|are|now|all)\s+(?:now\s+)?|^\W*|[\.\n:;]\s*\W*|\bet\s+|\band\s+)"
CLAIM_SRC = {
    "pushed": (
        SUBJ + r"(?:pushed|merged|pouss[ée]|merg[ée]|mis\s+sur\s+main)"
        r"|" + STATE + r"(?:pushed|merged|pouss[ée]s?|merg[ée]s?)\b"
        r"|\bpushed\s+(?:to|on)\s+main\b|\bmerged\s+(?:to|into|on)\s+main\b"
        r"|\best\s+sur\s+main\b"),
    "sent": (
        SUBJ + r"(?:sent|envoy[ée]|envoie\b)"
        r"|\b(?:is|are)\s+(?:now\s+)?(?:sent|envoy[ée]s?)\b|\b(?:est|sont)\s+(?:bien\s+)?envoy[ée]s?\b"
        r"|^\W*(?:sent|envoy[ée]s?)\b"),
    "deployed": (
        SUBJ + r"(?:deployed|d[ée]ploy[ée])"
        r"|\b(?:is|are)\s+(?:now\s+)?deployed\b|\b(?:est|sont)\s+(?:bien\s+)?d[ée]ploy[ée]s?\b"
        r"|^\W*deployed\b"),
    "tests": (
        r"\btests?\s+(?:all\s+)?(?:pass(?:ed|es|ing)?|are\s+green|green)\b"
        r"|\ball\s+tests\s+green\b|\b\d+\s+(?:tests?\s+)?passed\b|\bsuite\s+(?:is\s+)?green\b"
        r"|\btests?\s+(?:verts?|passent|ok)\b|\b(?:suite|tests?)\s+au\s+vert\b"),
    "verified": (
        SUBJ + r"(?:verified|checked|confirmed|v[ée]rifi[ée]|constat[ée])"
        r"|\b(?:is|are|was)\s+(?:now\s+)?(?:verified|confirmed)\b|\b(?:est|sont)\s+(?:bien\s+)?v[ée]rifi[ée]s?\b"
        r"|\bverified\b(?:\s+(?:with|by|on|against))"),
}
CLAIM_FLAGS = {"pushed": re.I | re.M, "sent": re.I | re.M, "deployed": re.I | re.M,
               "tests": re.I, "verified": re.I | re.M}

# A negation, a future, a condition or a question in the same sentence voids the claim.
VOID_SRC = (
    r"\b(?:not|never|n't|nothing|no\s+longer|didn't|haven't|hasn't|wasn't|isn't|aren't|can't|"
    r"cannot|won't|will|'ll|going\s+to|about\s+to|to\s+be|once|when|until|if|should|would|could|"
    r"must|need(?:s)?\s+to|before|after|unless|pas|jamais|rien|ne\s|n'|sera|seront|va\s|vais|"
    r"apr[eè]s|avant|quand|si\b|devra|faut|hier|yesterday|earlier|already|d[ée]j[aà]|tout\s+[àa]\s+l'heure|too|which\s+I)\b")

SENTENCE_SPLIT = re.compile(r"(?<=[\.\!\?\n])\s+|\n+")

# Reading the output file of a background run is the receipt of what that run did.
BG_OUTPUT = r"/tasks/\S+\.output|\bTaskOutput\b"
RECEIPT_SRC = {
    "merged": r"\bgit\s+(?:-C\s+\S+\s+)?(?:push|pull|merge|rebase|commit)\b|\bgh\s+pr\s+merge\b|" + BG_OUTPUT,
    "pushed": r"\bgit\s+(?:-C\s+\S+\s+)?push\b|\bgh\s+pr\s+merge\b|\bgit\s+merge\b|" + BG_OUTPUT,
    "sent": r"\bsend\b|\bsend_|_send\b|\bgmail\b|\bsmtp\b|\bgh\s+(?:pr|issue)\s+(?:create|comment)\b|\bpush_?notif|mcp__.*(?:send|post|create|click|press_key|type_text|fill)|SendMessage|PushNotification|SendUserFile|\bcurl\b.*\b(?:POST|-X|-d)\b|\bgit\s+push\b|" + BG_OUTPUT,
    "deployed": r"\bvercel\b|\bdeploy\b|\blaunchctl\b|\bgit\s+push\b|\bnpm\s+publish\b|" + BG_OUTPUT,
    "tests": r"\bpytest\b|\bunittest\b|\bvitest\b|\bjest\b|--self-test\b|\bself[_-]test\b|\bgo\s+test\b|\bcargo\s+test\b|\bpnpm\s+(?:test|typecheck|lint)\b|\bnpm\s+(?:run\s+)?test\b|\btsc\b|\bpython3?\s+\S*test\S*\.py\b|\bmake\s+test\b|" + BG_OUTPUT,
}
RECEIPT_FLAGS = {"merged": 0, "pushed": 0, "sent": re.I, "deployed": re.I, "tests": re.I}
VERIFY_TOOLS = {"Bash", "Read", "Grep", "Glob", "WebFetch", "WebSearch", "NotebookEdit"}
ANY_PROOF_TOOLS = {"Agent", "Task", "Workflow", "TaskOutput"}

_COMPILED = {}


def _alternatives(where, value):
    if not isinstance(value, list) or not all(isinstance(a, str) for a in value):
        raise config.ConfigError("claim-gate: {} must be a list of regex strings".format(where))
    return value


def patterns(cfg):
    """(claims, receipts, void) compiled with the configured extras appended as alternatives.
    An extra for an unknown kind, or one that does not compile, is a config.ConfigError."""
    key = json.dumps([cfg.get("claims_extra"), cfg.get("receipts_extra"), cfg.get("void_extra")],
                     sort_keys=True)
    if key not in _COMPILED:
        def build(src, extra, flags):
            out = {}
            for kind in extra:
                if kind not in src:
                    raise config.ConfigError("claim-gate: no kind {!r}".format(kind))
            for kind, base in src.items():
                alts = [base] + _alternatives(kind, extra.get(kind, []))
                try:
                    out[kind] = re.compile("|".join("(?:{})".format(a) for a in alts), flags[kind])
                except re.error as e:
                    raise config.ConfigError("claim-gate {}: {}".format(kind, e))
            return out
        claims = build(CLAIM_SRC, cfg.get("claims_extra") or {}, CLAIM_FLAGS)
        receipts = build(RECEIPT_SRC, cfg.get("receipts_extra") or {}, RECEIPT_FLAGS)
        try:
            void = re.compile("|".join("(?:{})".format(a) for a in [VOID_SRC] + _alternatives(
                "void_extra", cfg.get("void_extra") or [])), re.I)
        except re.error as e:
            raise config.ConfigError("claim-gate void_extra: {}".format(e))
        _COMPILED[key] = (claims, receipts, void)
    return _COMPILED[key]


def _settings(cfg):
    """The configuration of a caller outside a hook (the door ledger): the project layer of its
    cwd and the user layer, read from this plugin's own defaults."""
    if cfg is not None:
        return cfg
    return trace.plugin_settings({"cwd": os.getcwd()}, root=PLUGIN_ROOT)


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(x.get("text", "") for x in content
                         if isinstance(x, dict) and x.get("type") == "text")
    return ""


def _is_typed_user(row):
    """A real user message: not a tool result, not a hook or system injection."""
    if row.get("type") != "user" or row.get("isSidechain"):
        return False
    content = (row.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in content):
            return False
    text = _text_of(content).strip()
    if not text or text.startswith(("<system-reminder", "<command-", "<local-command", "Caveat:",
                                    "<task-notification", "[Request interrupted")):
        return False
    return True


def read_rows(transcript_path):
    rows = []
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return rows


def turn_calls(rows):
    """The tool calls of the last turn (after the last typed user message) whose result is not an
    error: a list of (tool name, command or input text)."""
    start = 0
    for i, r in enumerate(rows):
        if _is_typed_user(r):
            start = i + 1
    uses, errors, answered = {}, set(), set()
    for r in rows[start:]:
        if r.get("isSidechain"):
            continue
        content = (r.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for x in content:
            if not isinstance(x, dict):
                continue
            if x.get("type") == "tool_use":
                inp = x.get("input") or {}
                text = inp.get("command") if isinstance(inp.get("command"), str) else json.dumps(
                    inp, ensure_ascii=False)[:600]
                uses[x.get("id")] = (x.get("name") or "", text or "")
            elif x.get("type") == "tool_result":
                answered.add(x.get("tool_use_id"))
                if x.get("is_error"):
                    errors.add(x.get("tool_use_id"))
    return [v for k, v in uses.items() if k in answered and k not in errors]


def claims_in(final_text, cfg=None):
    """The kinds claimed in the closing block: {kind: sentence}."""
    claims, _receipts, void = patterns(_settings(cfg))
    tail = (final_text or "")[-TAIL_CHARS:]
    tail = CODE_RE.sub(" ", QUOTE_LINE_RE.sub(" ", tail))
    tail = QUOTE_SPAN_RE.sub(" ", tail)
    found = {}
    for sentence in SENTENCE_SPLIT.split(tail):
        if not sentence.strip() or "?" in sentence:
            continue
        for kind, rx in claims.items():
            if kind in found:
                continue
            if not rx.search(sentence):
                continue
            # a negation, a future or a condition anywhere in the sentence voids the claim
            if void.search(sentence):
                continue
            if kind == "pushed" and not re.search(r"push|pouss|sur\s+main|to\s+main", sentence, re.I):
                kind = "merged"
            found[kind] = sentence.strip()[:160]
    return found


def unproven(claims, calls, cfg=None):
    """The claims no tool call of the turn proves."""
    _claims, receipts, _void = patterns(_settings(cfg))
    names = {n for n, _ in calls}
    if names & ANY_PROOF_TOOLS:
        return {}
    out = {}
    for kind, sentence in claims.items():
        if kind == "verified":
            ok = bool(names & VERIFY_TOOLS) or any(n.startswith("mcp__") for n in names)
        else:
            ok = any(receipts[kind].search(n + " " + t) for n, t in calls)
            if kind == "sent":
                ok = ok or any(n in ("SendMessage", "PushNotification", "SendUserFile") for n in names)
        if not ok:
            out[kind] = sentence
    return out


def claims_without_receipt(text, cfg=None):
    """The door ledger's replay: would this text still claim something (receipts unknown)."""
    return bool(claims_in(text, cfg))


def judge_turn(final_text, calls, cfg=None):
    cfg = _settings(cfg)
    return unproven(claims_in(final_text, cfg), calls, cfg)


def reason_text(missing, cfg):
    parts = ["\"{}\" ({})".format(s, k) for k, s in missing.items()]
    text = ("The final message claims a gesture done with no tool result in this turn that proves "
            "it: " + "; ".join(parts) + ". Run the command that does or checks it now, in this "
            "turn, and report what the output says; or reword the claim to what you actually "
            "know (\"not run\", \"not checked\"). (claim-gate)")
    suffix = cfg.get("reason_suffix")
    return text + " " + suffix if suffix else text


def on_stop():
    payload = trace.read_payload()
    if payload.get("hook_event_name") != "Stop":
        return 0
    cfg = trace.plugin_settings(payload, root=PLUGIN_ROOT)
    patterns(cfg)  # a bad extra is a loud crash even on a turn that claims nothing
    path = payload.get("transcript_path") or ""
    final = transcript.final_text(payload)
    claims = claims_in(final, cfg)
    if not claims:
        trace.chain_clear(HOOK, payload)
        return 0
    missing = unproven(claims, turn_calls(read_rows(path)), cfg)
    if not missing:
        trace.chain_clear(HOOK, payload)
        return 0
    key = "claim: " + ",".join(sorted(missing))
    if not trace.chain_should_block(HOOK, payload, key):
        print(trace.cap_message(HOOK, key))
        return 0
    trace.witness("stop_claim_gate", "Stop", "claim_without_receipt", payload)
    print(json.dumps({"decision": "block", "reason": reason_text(missing, cfg)},
                     ensure_ascii=False))
    return 0


def check(path):
    cfg = trace.plugin_settings({"cwd": os.getcwd()}, root=PLUGIN_ROOT)
    missing = judge_turn(transcript.last_assistant_text(path), turn_calls(read_rows(path)), cfg)
    print(json.dumps(missing, ensure_ascii=False) if missing else "no unproven claim")
    return 0


def main():
    trace.configure(sys.argv)
    if "--check" in sys.argv:
        i = sys.argv.index("--check")
        if i + 1 >= len(sys.argv):
            sys.stderr.write("usage: claim_gate.py --check TRANSCRIPT.jsonl [--home DIR]\n")
            return 64
        try:
            return check(sys.argv[i + 1])
        except config.ConfigError as e:
            sys.stderr.write("claim-gate --check: {}\n".format(e))
            return 64
    return trace.run(HOOK, "Stop", on_stop, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
