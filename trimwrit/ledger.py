"""The append-only ledger of corrections, and the two routes out of it.

One file, JSON Lines, never rewritten in place. Append-only is not tidiness: the whole claim of
this tool is that a rule can point at the incident that created it, and an incident you can edit
after the fact is not evidence. Promotion and removal are recorded as NEW lines that reference
an earlier id, so the history of a rule reads forward.

THE TWO PROMOTION ROUTES, and there are two because there are two kinds of evidence:

  counter    n=2 occurrences of the same tag. One is an observation, two is a pattern. The
             threshold is 2 and not 3 because a human only corrects you a handful of times
             about any one thing before giving up on you, so n=3 amounts to never promoting.

  important  n=1, when the consequence is written down. Some corrections are expensive the
             first time, and waiting for a second occurrence means paying twice on purpose.
             The consequence is MANDATORY on this route, and it is stored with the entry:
             read six months later, "promoted at n=1" without its reason is indistinguishable
             from a threshold quietly bypassed.

Both routes are borrowed from a corrections ledger that has been running on a real corpus since
2026-07-29 rather than invented here.
"""
import json
import os
import time

from .text import append_text, one_line, refuse_bad_dashes

LEDGER_DIR = ".trimwrit"
LEDGER_NAME = "ledger.jsonl"

PROMOTION_THRESHOLD = 2

# Every kind of line the ledger carries. Kept explicit so a reader of the file can tell what
# happened without reading this module.
KIND_CORRECTION = "correction"
KIND_PROMOTION = "promotion"
KIND_REMOVAL = "removal"

ROUTE_COUNTER = "counter"
ROUTE_IMPORTANT = "important"
ROUTES = (ROUTE_COUNTER, ROUTE_IMPORTANT)


class LedgerError(Exception):
    """A refusal, always with the command that would clear it."""


def path_for(root="."):
    return os.path.join(root, LEDGER_DIR, LEDGER_NAME)


def rows(root=".", path=None):
    p = path or path_for(root)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def corrections(root=".", path=None):
    return [r for r in rows(root, path) if r.get("kind") == KIND_CORRECTION]


def _next_id(existing):
    """Zero-padded and monotonic. The id lands in a directory name, a rule comment and a git
    diff, so it has to sort the way a human expects and never be reused."""
    used = [int(r["id"]) for r in existing if r.get("kind") == KIND_CORRECTION
            and str(r.get("id", "")).isdigit()]
    return "{:04d}".format(max(used) + 1 if used else 1)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def log(text, tag=None, consequence=None, session=None, source="manual",
        root=".", path=None, now=None):
    """Append one correction. Returns the stored row.

    A correction is what the human actually said, verbatim. Not your summary of it: the summary
    is the diagnosis, it belongs in the case, and if the two are stored in the same field the
    ledger stops being evidence.
    """
    text = one_line(refuse_bad_dashes(text, "the correction text"))
    if not text:
        raise LedgerError("a correction with no text is not a correction")
    if consequence is not None:
        consequence = one_line(refuse_bad_dashes(consequence, "the consequence"))

    existing = rows(root, path)
    # A correction repeated verbatim in the same session is almost always a retry or a resumed
    # transcript, and counting it twice fabricates an n=2 that no human ever produced. That is
    # the one thing this ledger must never do, because n=2 writes a rule.
    for r in existing:
        if (r.get("kind") == KIND_CORRECTION and r.get("text") == text
                and r.get("session") and r.get("session") == session):
            raise LedgerError(
                "correction {} in session {} already says exactly this. Logging it twice would "
                "fabricate a second occurrence and promote a rule nobody asked for twice. Use "
                "`trimwrit show` to see it.".format(r["id"], session))

    row = {
        "kind": KIND_CORRECTION,
        "id": _next_id(existing),
        "at": now or _now(),
        "text": text,
        "tag": tag,
        "consequence": consequence,
        "session": session,
        "source": source,
        "case": None,
        "rule": None,
        "promoted": None,
    }
    append_text(path or path_for(root), json.dumps(row, ensure_ascii=False) + "\n",
                "the ledger line")
    return row


def get(cid, root=".", path=None):
    """Always the FOLDED row. Reading the raw correction line here was a real bug: `integrate`
    asked for correction 0002, got the line as first written, saw `case: None`, and refused a
    case that `trimwrit case` had attached by amendment minutes earlier. Anything that asks the
    ledger a question wants the current answer, not the first one."""
    for r in folded(root, path):
        if r["id"] == str(cid):
            return r
    raise LedgerError("no correction {} in the ledger. `trimwrit show` lists what is there."
                      .format(cid))


def _amend(cid, field, value, root=".", path=None):
    """Amendments are appended, then folded when the ledger is read.

    Rewriting the line in place would be simpler and would destroy the only property that makes
    this file worth keeping.
    """
    row = {"kind": "amend", "id": str(cid), "at": _now(), "field": field, "value": value}
    append_text(path or path_for(root), json.dumps(row, ensure_ascii=False) + "\n",
                "the ledger amendment")
    return row


def folded(root=".", path=None):
    """Corrections with their amendments applied, in id order. This is what every reader wants."""
    by_id = {}
    for r in rows(root, path):
        if r.get("kind") == KIND_CORRECTION:
            by_id[r["id"]] = dict(r)
        elif r.get("kind") == "amend" and r["id"] in by_id:
            by_id[r["id"]][r["field"]] = r["value"]
    return [by_id[k] for k in sorted(by_id)]


def attach_case(cid, case_id, root=".", path=None):
    get(cid, root, path)
    return _amend(cid, "case", case_id, root, path)


def attach_rule(cid, rule_id, root=".", path=None):
    get(cid, root, path)
    return _amend(cid, "rule", rule_id, root, path)


def tag(cid, tag_value, root=".", path=None):
    get(cid, root, path)
    return _amend(cid, "tag", tag_value, root, path)


def counts(root=".", path=None):
    """{tag: n} over corrections not yet promoted. Untagged corrections do not count: a tag is
    the claim that two incidents are the same incident, and only a human makes that claim."""
    out = {}
    for r in folded(root, path):
        t = r.get("tag")
        if t and not r.get("promoted"):
            out[t] = out.get(t, 0) + 1
    return out


def pending(root=".", path=None):
    """Every tag that has earned a rule and has not been given one, with the route that earned it.

    Returns [(tag, n, route)]. A tag qualifies on the counter route at n>=2, or on the important
    route at n=1 when at least one of its corrections carries a written consequence.
    """
    out = []
    folded_rows = folded(root, path)
    for t, n in sorted(counts(root, path).items()):
        if n >= PROMOTION_THRESHOLD:
            out.append((t, n, ROUTE_COUNTER))
            continue
        has_consequence = any(r.get("tag") == t and r.get("consequence") and not r.get("promoted")
                              for r in folded_rows)
        if has_consequence:
            out.append((t, n, ROUTE_IMPORTANT))
    return out


def promote(tag_value, rule_id, route=ROUTE_COUNTER, consequence=None, root=".", path=None):
    """Mark every unpromoted correction of a tag as having become `rule_id`. Refuses when the
    route is not earned, because a promotion rule you can talk your way past is a suggestion."""
    if route not in ROUTES:
        raise LedgerError("route must be one of {}".format(", ".join(ROUTES)))
    rowset = folded(root, path)
    matching = [r for r in rowset if r.get("tag") == tag_value and not r.get("promoted")]
    if not matching:
        raise LedgerError("no unpromoted correction carries the tag {}".format(tag_value))
    n = len(matching)

    if route == ROUTE_COUNTER and n < PROMOTION_THRESHOLD:
        stored = next((r["consequence"] for r in matching if r.get("consequence")), None)
        raise LedgerError(
            "tag {} has {} occurrence(s), the counter route needs {}. If the consequence is "
            "serious the first time, use --route important --consequence \"<what it cost>\"{}."
            .format(tag_value, n, PROMOTION_THRESHOLD,
                    " (one is already on the ledger: {})".format(stored) if stored else ""))

    if route == ROUTE_IMPORTANT:
        consequence = consequence or next(
            (r["consequence"] for r in matching if r.get("consequence")), None)
        if not consequence:
            raise LedgerError(
                "the important route needs the consequence in writing: what did this cost, once, "
                "in the real world. Irritation is not a consequence. Pass --consequence \"...\".")
        consequence = one_line(refuse_bad_dashes(consequence, "the consequence"))

    entry = {
        "kind": KIND_PROMOTION,
        "at": _now(),
        "tag": tag_value,
        "rule": rule_id,
        "route": route,
        "consequence": consequence,
        "corrections": [r["id"] for r in matching],
    }
    append_text(path or path_for(root), json.dumps(entry, ensure_ascii=False) + "\n",
                "the promotion line")
    for r in matching:
        _amend(r["id"], "promoted", rule_id, root, path)
    return entry


def remove(rule_id, reason, root=".", path=None):
    """Record that a rule was deleted, in the SAME file as its promotion.

    A second file for removals would give two answers to "is this rule live", and the tool would
    have reproduced the drift it exists to stop.
    """
    reason = one_line(refuse_bad_dashes(reason, "the removal reason"))
    if not reason:
        raise LedgerError("a rule is never removed without a reason on the record")
    entry = {"kind": KIND_REMOVAL, "at": _now(), "rule": rule_id, "reason": reason}
    append_text(path or path_for(root), json.dumps(entry, ensure_ascii=False) + "\n",
                "the removal line")
    return entry


def live_rules(root=".", path=None):
    """{rule_id: promotion entry} for rules promoted and not since removed."""
    live = {}
    for r in rows(root, path):
        if r.get("kind") == KIND_PROMOTION:
            live[r["rule"]] = r
        elif r.get("kind") == KIND_REMOVAL:
            live.pop(r.get("rule"), None)
    return live
