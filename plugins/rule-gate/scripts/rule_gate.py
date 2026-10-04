#!/usr/bin/env python3
"""Stop hook: a final message does not announce a new behavioural rule with no door.

Why a hook and not a sentence in a CLAUDE.md: a rule written as a sentence (a cap, a pace, a
window, a never, a gate, a sequence) is read by the same session that is supposed to enforce it,
and that session decides whether to bother. The rule only holds when it ships as the CODE that
refuses, in the same commit as its text, and the sentence names the door. A session that wrote a
standing order into a memory file and a CLAUDE.md and ended its turn with no hook, no script and
no test built for it is the exact failure; the eval case `rule-announced-without-its-door`
replays it.

What it checks: the final assistant text, for a message that ANNOUNCES a new behavioural rule
without naming the file that enforces it.
  1. A RULE MARKER: "from now on", "desormais"/"dorenavant" (accented or not), a "rule:"/"regle:"
     label, "never again"/"plus jamais", "every X must"/"chaque X doit", "always"/"toujours"
     applied to a process of ours (paired with a first-person subject or a process verb in the
     same short span), or a heading starting with "Rule"/"Regle". Checked on the CLOSING BLOCK:
     the final TAIL_CHARS of the message, which is the whole message when it is shorter.
  2. NO ENFORCING PATH named anywhere in the WHOLE message: a path under one of the configured
     prefixes (`enforcer_path_prefixes`), the bare filename of a hook command read live from
     `<project>/.claude/settings.json`, or the name of a plugin enabled in that file or in the
     user settings (so "the claim-gate plugin enforces it" counts as a door).

Both checks run on the message with QUOTED VERBATIM stripped first: a line starting with "> " or
a span wrapped in `*"..."*` is somebody else's words, not an announcement. A marker is also only
counted in a sentence whose grammatical subject is a process of ours: a sentence that opens on a
vendor name followed by "rule/policy/limit/requires/..." (LinkedIn's rule, Stripe's policy)
describes THEIR rule, not one this session imposes, and is excluded.

The bounds: the re-answer of a stop chain is checked like the first answer; the gate lets go
after CHAIN_CAP blocks in a row (a `cap` row in the ledger and a systemMessage). A crash or a
timeout lets the turn end and is reported loudly. No environment variable and no configuration
key turns the gate off. A SubagentStop is another event and is ignored.

Configuration (section `rule-gate` of trimwrit-gates.json): `phrase_patterns_extra` adds regex
alternatives to the rule markers (compiled case-insensitive and verbose, so a space inside an
extra is written `\\s`), `vendor_names` and `vendor_names_extra` name the third parties whose own
rules are not ours, `enforcer_path_prefixes` lists the directories an enforcing path lives under,
`reason_suffix` is appended to the refusal.

Outputs:
    exit 0 with nothing on stdout                       the session ends normally
    exit 0 with {"decision": "block", "reason": ...}    the session continues with this message

Utilities:
    python3 rule_gate.py --print        what it would say now, blocking nothing
"""
import json
import os
import re
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_ROOT)
from gatekit import config, trace, transcript  # noqa: E402

HOOK = "stop_rule_gate.py"
TIMEOUT_S = 8
TAIL_CHARS = 1600

# A quoted verbatim is not an announcement of ours: a markdown blockquote line, or a span wrapped
# in *"..."*.
QUOTE_LINE_RE = re.compile(r"^>.*$", re.M)
QUOTE_SPAN_RE = re.compile(r'\*"[^"]*"\*')


def strip_quotes(text):
    text = QUOTE_SPAN_RE.sub(" ", text)
    text = QUOTE_LINE_RE.sub(" ", text)
    return text


def sentences(text):
    """Rough sentence split: good enough to scope a marker to its own subject."""
    return [s for s in re.split(r"(?<=[.!?])\s+|\n{2,}", text) if s.strip()]


# A behavioural-rule announcement, calibrated on the incident the eval case replays and on the
# vocabulary of a rule ("a cap, a pace, a window, a never, a gate, a sequence"). Kept in sync by
# hand with the forbid grader of the eval case.
PHRASE_SRC = r"""
    \bfrom\s+now\s+on\b
  | \bd[ée]sormais\b
  | \bd[oó]r[ée]navant\b
  | \bnever\s+again\b
  | \bplus\s+jamais\b
  | \bevery\b[^.!?\n]{0,60}?\bmust\b
  | \bchaque\b[^.!?\n]{0,60}?\bdoit\b
"""
PHRASE_FLAGS = re.I | re.X

RULE_LABEL_RE = re.compile(r"\b(?:rule|r[eè]gle)\s*:", re.I)

# "always"/"toujours" is only a rule marker when it is applied to a process of ours: paired,
# in the same short span, with a first-person subject or a verb that names a process action.
ALWAYS_RE = re.compile(r"""
    \b(?:i(?:'ll|\s+will)?|we|claude|this\s+session|this\s+gate|this\s+hook|this\s+door)\b
    [^.!?\n]{0,60}?\b(?:always|toujours)\b
  | \b(?:always|toujours)\b[^.!?\n]{0,60}?
    \b(?:run|runs|check|checks|refuse|refuses|block|blocks|verify|verifies|enforce|enforces|
        ensure|ensures|commit|commits|gate|gates|route|routes|name|names|ask|asks|do|does)\b
""", re.I | re.X)

HEADING_RE = re.compile(r"^#{1,6}\s*(?:rule|r[eè]gle)\b", re.I | re.M)

_COMPILED = {}


def _strings(where, value):
    if not isinstance(value, list) or not all(isinstance(a, str) and a for a in value):
        raise config.ConfigError("rule-gate: {} must be a list of non-empty strings".format(where))
    return value


def compiled(cfg):
    """(phrase, vendor_subject, path) built from the configuration: the extras appended to the
    phrase markers as alternatives (each wrapped, so a stray comment cannot swallow the base
    ones), the vendor names and the path prefixes escaped. An extra that does not compile, or a
    value of the wrong type, is a config.ConfigError."""
    key = json.dumps([cfg.get(k) for k in ("phrase_patterns_extra", "vendor_names",
                                           "vendor_names_extra", "enforcer_path_prefixes")])
    if key not in _COMPILED:
        extras = _strings("phrase_patterns_extra", cfg.get("phrase_patterns_extra") or [])
        src = PHRASE_SRC.rstrip() + "".join("\n  | (?:{}\n)".format(a) for a in extras)
        try:
            phrase = re.compile(src, PHRASE_FLAGS)
        except re.error as e:
            raise config.ConfigError("rule-gate phrase_patterns_extra: {}".format(e))
        vendors = (_strings("vendor_names", cfg.get("vendor_names") or [])
                   + _strings("vendor_names_extra", cfg.get("vendor_names_extra") or []))
        prefixes = [p.strip("/") for p in
                    _strings("enforcer_path_prefixes", cfg.get("enforcer_path_prefixes") or [])]
        if not prefixes or not all(prefixes):
            raise config.ConfigError("rule-gate: enforcer_path_prefixes needs at least one "
                                     "directory and no bare slash")
        vendor = None
        if vendors:
            # A third party's own rule, not ours: excluded so describing LinkedIn's cap is never
            # counted as an announcement.
            vendor = re.compile(
                r"^\s*(?:the\s+)?(?:" + "|".join(re.escape(n) for n in vendors) + r")\b"
                r"[^.!?\n]{0,80}?\b(?:rule|rules|policy|policies|limit|limits|cap|caps|"
                r"requires?|allows?|rejects?|bans?|restricts?|throttles?)\b", re.I)
        # `(?<!\w)` rather than `\b`: a leading literal dot (".claude") is itself a non-word
        # character, so `\b` right before it never fires after whitespace or punctuation.
        path = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(p) for p in prefixes)
                          + r")/[\w./-]*\w")
        _COMPILED[key] = (phrase, vendor, path)
    return _COMPILED[key]


def _settings(cfg):
    """The configuration of a caller outside a hook: the project layer of its cwd and the user
    layer, read from this plugin's own defaults."""
    if cfg is not None:
        return cfg
    return trace.plugin_settings({"cwd": os.getcwd()}, root=PLUGIN_ROOT)


def repo_root(payload):
    """The git toplevel above the payload cwd (a directory holding `.git`, file or directory),
    else CLAUDE_PROJECT_DIR, else None."""
    here = os.path.abspath((payload or {}).get("cwd") or os.getcwd())
    while True:
        if os.path.exists(os.path.join(here, ".git")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return os.environ.get("CLAUDE_PROJECT_DIR") or None


def _json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _enabled_plugins(data):
    enabled = data.get("enabledPlugins")
    if not isinstance(enabled, dict):
        return set()
    return {k.split("@")[0] for k, v in enabled.items() if v is True and isinstance(k, str)}


def user_settings_path():
    base = trace.home() or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "settings.json")


def load_hook_basenames(root):
    """Names that count as a door: the bare filename of every hook command in
    `<root>/.claude/settings.json`, read live so a hook added or renamed there is picked up,
    plus the plugins enabled in it and in the user settings."""
    names = set()
    project = _json(os.path.join(root, ".claude", "settings.json")) if root else {}
    for entries in (project.get("hooks") or {}).values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            for h in (entry.get("hooks", []) if isinstance(entry, dict) else []):
                cmd = (h.get("command") if isinstance(h, dict) else None) or ""
                names.update(re.findall(r"[\w][\w.\-]*\.(?:py|sh)\b", cmd))
    names |= _enabled_plugins(project)
    names |= _enabled_plugins(_json(user_settings_path()))
    return names


def path_named(text, root, cfg):
    if compiled(cfg)[2].search(text):
        return True
    for name in load_hook_basenames(root):
        if re.search(r"(?<![\w.\-])" + re.escape(name) + r"(?![\w.\-])", text):
            return True
    return False


def find_marker(window, cfg):
    """The matched marker text in `window`, honouring the vendor-subject exclusion, or None.
    A heading is always ours (a heading is not a description of a third party's rule); every
    other marker is scoped to the sentence it appears in."""
    phrase, vendor, _path = compiled(cfg)
    m = HEADING_RE.search(window)
    if m:
        return m.group(0).strip()
    for s in sentences(window):
        if vendor and vendor.match(s.strip()):
            continue
        for rx in (phrase, RULE_LABEL_RE, ALWAYS_RE):
            m = rx.search(s)
            if m:
                return m.group(0)
    return None


def rule_without_door(text, root, cfg=None):
    """The matched rule-announcement marker when the closing block announces a new behavioural
    rule with no enforcing path named anywhere in the message, else None. `root` is the project
    root whose settings name hooks and plugins (or None)."""
    cfg = _settings(cfg)
    stripped = strip_quotes(text or "")
    if not stripped.strip():
        return None
    marker = find_marker(stripped[-TAIL_CHARS:], cfg)
    if not marker:
        return None
    if path_named(stripped, root, cfg):
        return None
    return marker


def reason_text(marker, cfg):
    prefixes = ", ".join(p.strip("/") + "/" for p in cfg.get("enforcer_path_prefixes"))
    text = ('The closing block announces a new behavioural rule ("{}") with no enforcing path '
            'named anywhere in the message. A cap, a pace, a window, a never or a gate is '
            'delivered as the code that holds it. Name the file under {} that refuses when the '
            'rule is broken, or build it before ending. (rule-gate)'.format(marker, prefixes))
    suffix = cfg.get("reason_suffix")
    return text + " " + suffix if suffix else text


def on_stop():
    payload = trace.read_payload()
    if payload.get("hook_event_name") != "Stop":
        return 0
    cfg = trace.plugin_settings(payload, root=PLUGIN_ROOT)
    compiled(cfg)  # a bad extra is a loud crash even on a message with no marker
    marker = rule_without_door(transcript.final_text(payload), repo_root(payload), cfg)
    if not marker:
        trace.chain_clear(HOOK, payload)
        return 0
    if not trace.chain_should_block(HOOK, payload, "rule marker: " + marker):
        print(trace.cap_message(HOOK, "rule marker: " + marker))
        return 0
    trace.witness("stop_rule_gate", "Stop", "rule_no_door", payload)
    print(json.dumps({"decision": "block", "reason": reason_text(marker, cfg)},
                     ensure_ascii=False))
    return 0


def main():
    trace.configure(sys.argv)
    if "--print" in sys.argv:
        print("rule_gate: would check the final message for a rule announced with no door.")
        return 0
    return trace.run(HOOK, "Stop", on_stop, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
