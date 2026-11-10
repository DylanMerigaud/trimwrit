"""The AXES a judgment varies on, what a bank's cases cover on each, the divergence rule that
decides when an axis earns its own slice, and the route per axis.

WHY. A router that answers reuse, extend or new for a KEY says nothing of the facets of what the
key judges (who reads it, in which language, for which industry), so a bank can pass its bar on
one audience in one language and be read as vouching for another. Asked on 2026-11-10: "faut un
framework qui soit extensible a d'autre truc et category de harness". Here every axis is one
entry of a registry the project plugs in, a bank's RELEASE.json declares what it covers, and an
axis earns its own slice or bank only when the bank's own numbers DIVERGE between its values.

THE AXES. `<root>/.trimwrit/bank.json` key "axes": "<path/to/module.py>:<callable>", the
callable (called with the root when it takes an argument) returning {name: Axis}. An Axis is a
name, a closed vocabulary (a tuple, or a zero argument callable returning one), a resolver
`resolve(subject)` -> a value or None, and one line saying how the axis can change a verdict. A
SUBJECT is a dict the project's resolvers read; its `facets` {axis: value} overrides any
resolver. Adding an axis is one entry.

COVERAGE. A bank's RELEASE.json carries `covers` {axis: [values]} and `by_facet` {axis: {value:
{"n", "metric"}}}, written through release_extra() into bank.write_release(extra=...) and
validated by bank.envelope_problems when present. A bank that writes neither has coverage
UNKNOWN on every axis (unknown is not a gap).

THE DIVERGENCE RULE (diverges): an axis is SPLIT only when at least two of its values each hold
MIN_N cases or more AND their metric spread exceeds GAP; otherwise its values stay POOLED in one
bank ("unmeasured" when fewer than two values reach MIN_N).

THE ROUTE PER AXIS (route_facets): given the banks that grade the subject (none: one "new"
entry, every axis "unknown"), every axis that resolves reads those banks' RELEASE: covered (its
value is in `covers`), pooled (covered, and the axis was measured and does not diverge), extend
(a bank declares the axis but not this value: add cases of it), unknown (no bank declares the
axis). A diverging axis says "split" in its why. Standard library only; nothing here writes a
store or calls a model.
"""
import os
from dataclasses import dataclass
from typing import Callable

from .bank import read_release
from .route import load_callable, _call_with_root

# MIN_N = 20 cases per value: under 20 a single wrong case moves a 0..1 metric by 5 points or
# more, so two small slices differ by noise alone. GAP = 0.10 on a 0..1 metric: bars sit between
# 0.85 and 0.95, so a 10 point spread is the distance between a bank that passes and one that
# fails; anything smaller reads inside the noise of two 20 case slices.
MIN_N = 20
GAP = 0.10
DECISIONS = ("covered", "extend", "new", "pooled", "unknown")


@dataclass(frozen=True)
class Axis:
    name: str
    vocabulary: object               # a tuple, or a zero argument callable returning one
    resolve: Callable                # (subject) -> a value of the vocabulary, or None
    why: str                         # one line: how this axis can change a verdict

    def values(self):
        v = self.vocabulary
        return tuple(v() if callable(v) else v)


def load_axes(root):
    """{name: Axis} from the callable `.trimwrit/bank.json` "axes" names. RouteError when the
    file or the key is missing."""
    axes = _call_with_root(load_callable(root, "axes"), root)
    return dict(axes)


def vector(subject, axes):
    """{axis: value} for every axis of `axes` that resolves on `subject`. A subject's `facets`
    {axis: value} overrides the resolver."""
    forced = subject.get("facets") or {}
    out = {}
    for name, ax in axes.items():
        v = forced.get(name) if name in forced else ax.resolve(subject)
        if v is not None:
            out[name] = v
    return out


# ------------------------------------------------------------------ coverage

def _metric_of(rows, metric):
    if callable(metric):
        return metric(rows)
    vals = [float(r[metric]) for r in rows if r.get(metric) is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def coverage(rows, axes, metric="right"):
    """(`covers` {axis: sorted values}, `by_facet` {axis: {value: {"n", "metric"}}}) over graded
    rows, each {"facets": {axis: value}, <metric>: bool or number}. `metric` is a row key whose
    mean is the metric (a bool counts 1 or 0), or a callable (rows) -> number. An axis no row
    carries a value of is LEFT OUT of both: its coverage is unknown, not a declared empty slice
    (which would read every value as missing, extend)."""
    covers, by_facet = {}, {}
    for ax in axes:
        groups = {}
        for r in rows:
            v = (r.get("facets") or {}).get(ax)
            if v is not None:
                groups.setdefault(str(v) if not isinstance(v, str) else v, []).append(r)
        if not groups:
            continue
        covers[ax] = sorted(groups)
        by_facet[ax] = {v: {"n": len(g), "metric": _metric_of(g, metric)}
                        for v, g in sorted(groups.items())}
    return covers, by_facet


def release_extra(rows, axes, metric="right"):
    """{"covers": ..., "by_facet": ...} to pass into bank.write_release(extra=...)."""
    covers, by_facet = coverage(rows, axes, metric)
    return {"covers": covers, "by_facet": by_facet}


def graded_rows(cases, rows, axes, subject_of):
    """[{"facets", "right"}] joining a run's rows to their cases by id, each case's facets
    resolved on `axes` ({name: Axis}) over `subject_of(case["input"])`. What a bank's run.py
    hands to release_extra."""
    by_id = {c["id"]: c for c in cases}
    out = []
    for r in rows:
        c = by_id.get(r.get("id"))
        if c is None:
            continue
        out.append({"facets": vector(subject_of(c.get("input")), axes),
                    "right": bool(r.get("right"))})
    return out


# ------------------------------------------------------------------ the divergence rule

def diverges(by_value, min_n=MIN_N, gap=GAP):
    """{"split", "verdict" (split | pooled | unmeasured), "values" (the measured ones), "spread",
    "why"} over {value: {"n", "metric"}}. Split only when two values or more each hold min_n
    cases and their metric spread exceeds gap."""
    measured = {v: d for v, d in (by_value or {}).items()
                if isinstance(d, dict) and (d.get("n") or 0) >= min_n
                and d.get("metric") is not None}
    if len(measured) < 2:
        return {"split": False, "verdict": "unmeasured", "values": sorted(measured),
                "spread": None,
                "why": "pooled, unmeasured: {} value(s) reach {} cases, two are needed to "
                       "compare".format(len(measured), min_n)}
    ms = [float(d["metric"]) for d in measured.values()]
    spread = round(max(ms) - min(ms), 4)
    split = spread > gap
    return {"split": split, "verdict": "split" if split else "pooled",
            "values": sorted(measured), "spread": spread,
            "why": ("split: spread {} over {} values of {}+ cases exceeds {}" if split else
                    "pooled: spread {} over {} values of {}+ cases is within {}").format(
                        spread, len(measured), min_n, gap)}


# ------------------------------------------------------------------ the route per axis

def _entry(axis, value, decision, bank, why):
    return {"axis": axis, "value": value, "decision": decision, "bank": bank, "why": why}


def route_facets(subject, axes, banks, releases=None, root="."):
    """[{"axis", "value", "decision", "bank", "why"}], one per axis of `axes` that resolves on
    `subject`, read against the RELEASE of each of `banks` (the banks that grade the subject, the
    first one first). `releases` {bank: RELEASE doc} replaces the disk reads under `root`."""
    vec = vector(subject, axes)
    banks = [b for b in banks or () if b]
    if not banks:
        out = [_entry("bank", subject.get("key") or "-", "new", None,
                      "no bank grades this subject: build one")]
        out += [_entry(ax, v, "unknown", None, "no bank for this subject")
                for ax, v in vec.items()]
        return out
    docs = {}
    for b in banks:
        if releases is not None and b in releases:
            docs[b] = releases[b] or {}
        else:
            docs[b] = read_release(os.path.join(root, b)) or {}
    out = []
    for ax, v in vec.items():
        declaring = [b for b in banks if isinstance(docs[b].get("covers"), dict)
                     and ax in docs[b]["covers"]]
        if not declaring:
            out.append(_entry(ax, v, "unknown", banks[0],
                              "{} declare no coverage of {}: unknown, nothing to pop".format(
                                  ", ".join(banks), ax)))
            continue
        hit = [b for b in declaring if v in docs[b]["covers"][ax]]
        b = hit[0] if hit else declaring[0]
        div = diverges(((docs[b].get("by_facet") or {}).get(ax)) or {})
        split = "; {} is split ({})".format(ax, div["why"]) if div["split"] else ""
        if hit and div["verdict"] == "pooled":
            out.append(_entry(ax, v, "pooled", b,
                              "{} covers {}={} and {}".format(b, ax, v, div["why"])))
        elif hit:
            out.append(_entry(ax, v, "covered", b, "{} covers {}={}".format(b, ax, v) + split))
        else:
            out.append(_entry(ax, v, "extend", b,
                              "{} slices {} but holds no case of {}: add cases of it".format(
                                  b, ax, v) + split))
    return out


def subject_banks(subject, registry, root=None):
    """The banks that grade `subject`: its own `banks`, else registry.facet_banks(subject) when
    the registry has one, else the bank its `key` routes to (reuse or extend; the route never
    asks a model here)."""
    if subject.get("banks"):
        return list(subject["banks"])
    if registry is None:
        return []
    fn = getattr(registry, "facet_banks", None)
    if callable(fn):
        return list(fn(subject) or ())
    key = subject.get("key")
    if not key:
        return []
    from .route import route  # noqa: PLC0415
    no_ask = _NoAsk(registry)
    r = route(key, subject.get("judges") or "", producer=subject.get("producer"),
              registry=no_ask, root=root)
    return [r["bank"]] if r["decision"] in ("reuse", "extend") and r.get("bank") else []


class _NoAsk:
    """A registry view without ask(): the facet route reads the deterministic steps only."""

    def __init__(self, reg):
        self._reg = reg
        self.root = getattr(reg, "root", None)

    def __getattr__(self, name):
        if name in ("ask", "prompt"):
            raise AttributeError(name)
        return getattr(self._reg, name)


def entry_line(e):
    return "{}={}  {}  {}  {}".format(e["axis"], e["value"], e["decision"], e["bank"] or "-",
                                      e["why"])
