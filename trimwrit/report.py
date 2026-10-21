"""`report`: the whole life of one harness, read from its own ledgers, as counts and dates.

Three files feed it, and none is required beyond the correction ledger: the corrections
(`.trimwrit/ledger.jsonl`), the refusals the harness's doors logged, and the measured eval runs
(`evals/results/history.jsonl`). A stranger runs it on their own harness and gets the same shape
of numbers back, which is the point of making it a command and not a one-off script.

NO FREE TEXT, BY CONSTRUCTION

The correction ledger's `text`, `consequence` and `session` fields hold what a human actually
said, and that names real third parties. This module therefore never copies a field through:
each value in the output document is built here from a count, a date, a door name or a case
delta. The fields that carry prose are never even read into the output, so there is nothing to
filter and no filter to forget. A test plants a sentinel in every such field and fails if it
shows up anywhere.

DATES

Timestamps in these files mix offsets (`-0500`, `+0200`, `Z`, `.753Z`), and a string sort of
them gives the wrong first and last date. Every timestamp is parsed, converted to UTC, and only
then compared. One that does not parse still counts as a row and takes no part in a date.

A CASE'S FIGURES are the sum of the history rows under its current evidence key (the key of
its latest row that carries one; decide.py, WHY A KEY): `run --decide` writes one row per
round since 0.9.2, so the last row alone is one round of 3 runs. The pass rates, the run count
and the verdict come from that sum, the verdict decided on it by verdict.py, never copied from a
row. A case whose rows carry no key (written before 0.9.0) shows its latest measured row and
the latest verdict a row carries, as before. VERDICTS are counted over every case, one line.

A MALFORMED LINE is skipped and counted, never raised: blank, not JSON, or JSON that is not an
object (or lacks the key a row of its kind cannot do without). A damaged line must not throw
away the thousand good ones after it.
"""
import json
import math
import re
from datetime import datetime, timedelta, timezone

from . import decide as decide_mod
from . import ledger as ledger_mod
from . import verdict as verdict_mod

_TS = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?"
                 r"(Z|[+-]\d{2}:?\d{2})?$")
UNKNOWN_DOOR = "(unknown)"
UNNAMED_CASE = "(unnamed)"
# verdict.py's words, in the order the report prints them.
VERDICTS = (verdict_mod.KEEP, verdict_mod.HARMFUL, verdict_mod.INERT, verdict_mod.INERT_FAILING,
            verdict_mod.UNDECIDED)


def parse_ts(value):
    """An aware UTC datetime, or None. A timestamp with no offset is read as UTC."""
    if not isinstance(value, str):
        return None
    m = _TS.match(value.strip())
    if not m:
        return None
    y, mo, d, h, mi, s, frac, off = m.groups()
    micro = int((frac or "0")[:6].ljust(6, "0"))
    try:
        if off in (None, "Z"):
            tz = timezone.utc
        else:
            sign = -1 if off[0] == "-" else 1
            digits = off[1:].replace(":", "")
            tz = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), micro,
                        tzinfo=tz).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _read(path, required=()):
    """([objects], malformed count). Each object is a dict holding every key in `required`."""
    out = []
    malformed = 0
    # Binary, and decoded line by line: one invalid byte is one malformed line, not a crash
    # (UnicodeDecodeError is a ValueError, and no OSError).
    with open(path, "rb") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                malformed += 1
                continue
            try:
                obj = json.loads(raw.decode("utf-8"))
            except ValueError:
                malformed += 1
                continue
            if not isinstance(obj, dict) or any(k not in obj for k in required):
                malformed += 1
                continue
            out.append(obj)
    return out, malformed


def _span(stamps):
    parsed = [dt for dt in (parse_ts(s) for s in stamps) if dt]
    if not parsed:
        return None, None
    return _iso(min(parsed)), _iso(max(parsed))


def _corrections(path):
    raw, malformed = _read(path)
    usable = []
    for r in raw:
        kind = r.get("kind")
        if kind == ledger_mod.KIND_CORRECTION and isinstance(r.get("id"), str):
            usable.append(r)
        elif kind == "amend" and isinstance(r.get("id"), str) and isinstance(r.get("field"), str) \
                and "value" in r:
            usable.append(r)
        elif kind in (ledger_mod.KIND_PROMOTION, ledger_mod.KIND_REMOVAL):
            continue
        else:
            malformed += 1
    rows = ledger_mod.fold(usable)
    first, last = _span(r.get("at") for r in rows)
    tags = {r["tag"] for r in rows if isinstance(r.get("tag"), str) and r["tag"]}
    return {
        "count": len(rows),
        "first_at": first,
        "last_at": last,
        "tagged": sum(1 for r in rows if r.get("tag")),
        "distinct_tags": len(tags),
        "cased": sum(1 for r in rows if r.get("case") is not None),
        "promoted": sum(1 for r in rows if r.get("promoted") is not None),
        "malformed": malformed,
    }


def _refusals(path):
    rows, malformed = _read(path)
    first, last = _span(r.get("at") for r in rows)
    by_door = {}
    for r in rows:
        door = r.get("door")
        door = door if isinstance(door, str) and door else UNKNOWN_DOOR
        by_door[door] = by_door.get(door, 0) + 1
    return {
        "count": len(rows),
        "first_at": first,
        "last_at": last,
        "took_effect": sum(1 for r in rows if r.get("took_effect") is True),
        "by_door": [[d, n] for d, n in sorted(by_door.items(), key=lambda kv: (-kv[1], kv[0]))],
        "malformed": malformed,
    }


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _is_unmeasured(row):
    """A run is unmeasured when its delta is not a number, or when it says some of its runs
    were: any truthy `unmeasured` (true, a positive count, a non-empty string), or the
    {"with": n, "without": m} tally the runner writes with a nonzero side. None of these is
    averaged in."""
    if _number(row.get("delta")) is None:
        return True
    flag = row.get("unmeasured")
    if isinstance(flag, dict):
        return any(_number(v) for v in flag.values())
    return bool(flag)


def _case_number(case_id):
    """Only the numeric prefix of a case id leaves this module. A case directory is the id plus
    a slug of the correction's own words, so the slug is free text."""
    m = re.match(r"\d+", case_id)
    return m.group(0) if m else UNNAMED_CASE


def _latest_by_case(rows, floor, keep=lambda r: True):
    latest = {}
    for r in rows:
        if not keep(r):
            continue
        dt = parse_ts(r.get("ts"))
        prev = latest.get(r["case"])
        if prev is None or (dt or floor) >= (prev[0] or floor):
            latest[r["case"]] = (dt, r)
    return latest


def _keyed(rows, floor):
    """{case: (dt of its latest keyed row, summed counts)} for every case with a keyed row that
    carries counts: the sum of its rows under the key of its latest such row."""
    with_counts = [r for r in rows if isinstance(r.get("key"), str)
                   and decide_mod.row_counts(r) is not None]
    out = {}
    for case, (dt, last) in _latest_by_case(with_counts, floor).items():
        total = decide_mod.zero_counts()
        for r in with_counts:
            if r["case"] == case and r["key"] == last["key"]:
                total = decide_mod.add_counts(total, decide_mod.row_counts(r))
        out[case] = (dt, total)
    return out


def _summed_entry(case, dt, total):
    """The figures of one keyed case, from its summed counts, or None when an arm has no
    measured run (no rate to show). The verdict is decided on the sum."""
    passes, measured = total["passes"], total["measured"]
    if not measured["with"] or not measured["without"]:
        return None
    w = passes["with"] / float(measured["with"])
    wo = passes["without"] / float(measured["without"])
    return {"case": _case_number(case), "with": w, "without": wo, "delta": w - wo,
            "runs": total["attempted"]["with"], "ts": _iso(dt),
            "passes": dict(passes), "measured": dict(measured),
            "attempted": dict(total["attempted"]), "verdict": decide_mod.judge(total)["verdict"]}


def _cases(path):
    rows, malformed = _read(path, required=("case",))
    named = [r for r in rows if isinstance(r["case"], str) and r["case"]]
    malformed += len(rows) - len(named)
    rows = named
    measured = [r for r in rows if not _is_unmeasured(r)]
    floor = datetime.min.replace(tzinfo=timezone.utc)
    keyed = _keyed(rows, floor)
    entries = []
    for case, (dt, total) in keyed.items():
        entry = _summed_entry(case, dt, total)
        if entry is not None:
            entries.append(entry)
    legacy = _latest_by_case([r for r in measured if r["case"] not in keyed], floor)
    for case, (dt, r) in legacy.items():
        runs = r.get("runs")
        entries.append({
            "case": _case_number(case),
            "with": _number(r.get("with")),
            "without": _number(r.get("without")),
            "delta": _number(r.get("delta")),
            "runs": runs if isinstance(runs, int) and not isinstance(runs, bool) else None,
            "ts": _iso(dt),
            "passes": None, "measured": None, "attempted": None,
            "verdict": r.get("verdict") if r.get("verdict") in VERDICTS else None,
        })
    entries.sort(key=lambda e: (-e["delta"], e["case"]))
    return {
        "measured": len(measured),
        "unmeasured": len(rows) - len(measured),
        "latest": entries,
        "verdicts": _verdicts(rows, floor, keyed),
        "malformed": malformed,
    }


def _verdicts(rows, floor, keyed=None):
    """{verdict: count}, one per case.

    A keyed case counts the verdict decided on the sum of its current key (`keyed`, from
    _keyed). Any other case counts the latest row that carries a verdict: an `undecided` row
    followed by a `keep` row is one rule kept, not one of each, and a plain row after it
    carries no verdict and leaves it standing. Only the five words of verdict.py leave this
    module, so a damaged row cannot carry text out.
    """
    keyed = keyed or {}
    out = {}
    for _, total in keyed.values():
        v = decide_mod.judge(total)["verdict"]
        out[v] = out.get(v, 0) + 1
    latest = _latest_by_case(rows, floor,
                             lambda r: r["case"] not in keyed and r.get("verdict") in VERDICTS)
    for _, r in latest.values():
        out[r["verdict"]] = out.get(r["verdict"], 0) + 1
    return out


def build(ledger_path, refusals_path=None, history_path=None):
    """The report document. A missing optional input is None, a missing or unreadable file raises
    OSError (the CLI turns that into a refusal)."""
    return {
        "corrections": _corrections(ledger_path),
        "refusals": _refusals(refusals_path) if refusals_path is not None else None,
        "cases": _cases(history_path) if history_path is not None else None,
    }


def _span_line(block):
    return "{} to {}".format(block["first_at"] or "-", block["last_at"] or "-")


def render(doc, bar="-" * 72):
    """The document as text: a header line of counts, then one block per section."""
    c = doc["corrections"]
    parts = [
        "{} correction(s), {} refusal(s), {} measured case run(s).".format(
            c["count"],
            doc["refusals"]["count"] if doc["refusals"] else "-",
            doc["cases"]["measured"] if doc["cases"] else "-"),
        "\nCORRECTIONS\n" + bar,
        "{} from {}".format(c["count"], _span_line(c)),
        "{} tagged, {} distinct tag(s)".format(c["tagged"], c["distinct_tags"]),
        "{} with a case, {} promoted".format(c["cased"], c["promoted"]),
        "{} malformed line(s) skipped".format(c["malformed"]),
    ]
    r = doc["refusals"]
    parts.append("\nREFUSALS\n" + bar)
    if r is None:
        parts.append("no refusals file given.")
    else:
        parts.append("{} from {}".format(r["count"], _span_line(r)))
        parts.append("{} took effect, {} malformed line(s) skipped".format(
            r["took_effect"], r["malformed"]))
        parts.append("\n{:<40} {:>6}".format("door", "n"))
        for door, n in r["by_door"]:
            parts.append("{:<40} {:>6}".format(door, n))
    k = doc["cases"]
    parts.append("\nCASES\n" + bar)
    if k is None:
        parts.append("no history file given.")
    else:
        parts.append("{} measured run(s), {} unmeasured (counted apart, never averaged), "
                     "{} malformed line(s) skipped".format(
                         k["measured"], k["unmeasured"], k["malformed"]))
        parts.append(_verdicts_line(k.get("verdicts") or {}))
        if k["latest"]:
            parts.append("\n{:<44} {:>6} {:>8} {:>7} {:>5}".format(
                "case (summed under its key)", "with", "without", "delta", "runs"))
            for e in k["latest"]:
                parts.append("{:<44} {:>6} {:>8} {:>+7.2f} {:>5}".format(
                    e["case"][:44], _fmt(e["with"]), _fmt(e["without"]), e["delta"],
                    e["runs"] if e["runs"] is not None else "-"))
    return "\n".join(parts)


def _verdicts_line(counts):
    head = "verdicts (latest per case): "
    if not counts:
        return head + "none yet, `trimwrit run --decide` writes them"
    return head + ", ".join("{} {}".format(v, counts[v]) for v in VERDICTS if counts.get(v))


def _fmt(value):
    return "-" if value is None else "{:.2f}".format(value)
