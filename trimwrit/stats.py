"""`prune` answers whether the eval still needs a rule. `stats` answers the complementary
runtime question: what did the rule DO in production. It aggregates a JSONL ledger of rule
evaluations and flags two candidate classes for a human to decide on. It never deletes
anything, and the closest it gets to a verdict is a label, dead weight or friction, on a count.

TWO SHAPES, ONE FILE

Two things already write a ledger like this, and neither matches the other. A FLAT row is one
evaluation, one line: ``{"rule": ..., "status": "PASS"|"FAIL"|"SKIP", "margin": number|null}``.
A NESTED verdict row is one prompt scored against several rules at once, the shape a private
repo's own judge already logs: ``{"gates": [{"gate": ..., "status": ..., "margin": ...}, ...],
"surface": "..."}``. Both are read from the same file, auto-detected per line, because asking
every producer to agree on one schema before this tool can read what they already wrote is not
a real option. In a nested row the rule name is the ``gate`` field, and the aggregation key is
``(surface or "", rule)``.

DEAD WEIGHT is fired at least ``min_fires`` times, has never failed once, and has never come
close to failing either. It is a deletion CANDIDATE, nothing more, and the caveat matters more
than the count. A rule like this runs on text a model GENERATES, and a clean record can mean
the generator already internalized the rule rather than that the rule does nothing: deleting it
is the one move that would find out, because that is what un-internalizes it. The strong case
for deletion is a rule that is ALSO absent from every logged correction, which this module has
no way to check on its own; it only ever hands the human two numbers and a caveat.

FRICTION is decided on at least ``friction_fires`` times and refuses at least ``friction_rate``
of them. That is either a load-bearing wall or a rule that costs more than it protects, and the
split between the two is a human call, deliberately left out of this module.
"""
import json
import statistics

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

# Deletion candidate: fired a lot, never failed, never came close.
DEAD_WEIGHT_MIN_FIRES = 15
DEAD_WEIGHT_CLOSE_MARGIN = 2

# Load-bearing-or-costly candidate: decided on a lot, refuses a quarter or more of them.
FRICTION_MIN_DECIDED = 8
FRICTION_MIN_RATE = 0.25


class RuleStats(object):
    """One ``(surface, rule)`` key's tally. ``margins`` holds only the numeric ones: a row with
    no margin, or a non numeric one, still counts toward ``fires`` and nothing else."""

    def __init__(self, surface, rule):
        self.surface = surface
        self.rule = rule
        self.fires = 0
        self.fail = 0
        self.skip = 0
        self.margins = []

    @property
    def decided(self):
        return self.fires - self.skip

    @property
    def fail_rate(self):
        if self.decided <= 0:
            return 0.0
        return round(self.fail / self.decided, 3)

    @property
    def median_margin(self):
        if not self.margins:
            return None
        return round(statistics.median(self.margins), 1)

    @property
    def min_margin(self):
        return min(self.margins) if self.margins else None

    def close_calls(self, close_margin):
        return sum(1 for m in self.margins if m <= close_margin)

    def is_dead_weight(self, min_fires, close_margin):
        return (self.fires >= min_fires and self.fail == 0
                and self.close_calls(close_margin) == 0)

    def is_friction(self, friction_fires, friction_rate):
        return self.decided >= friction_fires and self.fail_rate >= friction_rate

    def to_dict(self, min_fires, close_margin, friction_fires, friction_rate):
        return {
            "surface": self.surface,
            "rule": self.rule,
            "fires": self.fires,
            "fail": self.fail,
            "skip": self.skip,
            "decided": self.decided,
            "fail_rate": self.fail_rate,
            "margins": list(self.margins),
            "median_margin": self.median_margin,
            "min_margin": self.min_margin,
            "close_calls": self.close_calls(close_margin),
            "dead_weight": self.is_dead_weight(min_fires, close_margin),
            "friction": self.is_friction(friction_fires, friction_rate),
        }


def _numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _row_evaluations(obj):
    """One parsed JSON line to a list of ``(surface, rule, status, margin)`` tuples. Empty for
    anything that is not one of the two known shapes, which the caller counts as malformed
    rather than aggregating a key nobody asked for."""
    if not isinstance(obj, dict):
        return []
    gates = obj.get("gates")
    if isinstance(gates, list):
        surface = obj.get("surface") or ""
        out = []
        for gate in gates:
            if isinstance(gate, dict) and gate.get("gate"):
                out.append((surface, gate["gate"], gate.get("status"), gate.get("margin")))
        return out
    if obj.get("rule"):
        surface = obj.get("surface") or ""
        return [(surface, obj["rule"], obj.get("status"), obj.get("margin"))]
    return []


def read_ledger(path):
    """The JSONL file to ``(evaluations, total rows, malformed count)``.

    A blank line, a line that is not JSON, or a line that is JSON but matches neither the flat
    nor the nested shape, is malformed and skipped, never raised. A ledger this module cannot
    fully read is still worth aggregating the rest of, and a crash on line one would throw away
    every row after it for the sake of one bad line.
    """
    evaluations = []
    total = 0
    malformed = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                malformed += 1
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            row_evals = _row_evaluations(obj)
            if not row_evals:
                malformed += 1
                continue
            total += 1
            evaluations.extend(row_evals)
    return evaluations, total, malformed


def compute(path, min_fires=DEAD_WEIGHT_MIN_FIRES, close_margin=DEAD_WEIGHT_CLOSE_MARGIN,
           friction_fires=FRICTION_MIN_DECIDED, friction_rate=FRICTION_MIN_RATE):
    """Read `path` and return the full aggregate as a plain, JSON serialisable dict.

    Both `trimwrit stats --json` and the human summary read from this one return value, so the
    two can never disagree about a number.
    """
    evaluations, total, malformed = read_ledger(path)

    by_key = {}
    surfaces = {}
    for surface, rule, status, margin in evaluations:
        stats = by_key.setdefault((surface, rule), RuleStats(surface, rule))
        stats.fires += 1
        if status == FAIL:
            stats.fail += 1
        elif status == SKIP:
            stats.skip += 1
        if _numeric(margin):
            stats.margins.append(margin)
        if surface:
            surfaces[surface] = surfaces.get(surface, 0) + 1

    rules = [stats.to_dict(min_fires, close_margin, friction_fires, friction_rate)
             for key, stats in sorted(by_key.items())]
    dead_weight = sorted((r for r in rules if r["dead_weight"]), key=lambda r: -r["fires"])
    friction = sorted((r for r in rules if r["friction"]), key=lambda r: -r["fail_rate"])

    return {
        "total_rows": total,
        "malformed": malformed,
        "surfaces": surfaces,
        "thresholds": {
            "min_fires": min_fires,
            "close_margin": close_margin,
            "friction_fires": friction_fires,
            "friction_rate": friction_rate,
        },
        "rules": rules,
        "dead_weight": dead_weight,
        "friction": friction,
    }
