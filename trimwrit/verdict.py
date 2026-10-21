"""`prune` used to read a rule's worth off the mean of three runs per arm, and three runs
cannot carry that: 3 of 3 against 0 of 3 looks decisive and is not, and a mean hides how many
runs stand behind it. This module decides from pass counts instead, and says `undecided` until
the numbers are enough.

WHY A POSTERIOR AND NOT A T-TEST

A run either passes or fails, so each arm is a count of passes out of n, not a sample of
scores with a spread to test. Runs come three at a time, which is far below what a normal
approximation needs. And the numbers are looked at after every batch, which inflates the error
rate of any fixed-sample test run at each look. So each arm gets a Beta(1, 1) prior, the two
posteriors are combined on a fixed midpoint grid (no sampling, so a verdict is reproducible
and a test can pin it), and the lines are strict: `keep` and `harmful` need 0.99, `inert`
needs 0.975 that the two pass rates sit within the margin of each other.

WHAT THE STRICT LINES COST, MEASURED

`simulate` replays the rule: batches of 3 runs per arm, a look after each, at most 30 runs per
arm, 300 repetitions, seed 7, margin 0.2. With no true effect (0.5 against 0.5) it says `keep`
5 percent of the time, `harmful` 4 percent, and stays undecided 91 percent. Two perfect arms
(1.0 against 1.0) are `inert` every time, after 18 runs per arm. A large effect (1.0 against
0.33) is `keep` every time, after 8.5 runs per arm on average; 0.9 against 0.6 is `keep` 70
percent of the time. A true gap of exactly the margin (1.0 against 0.8) says `keep` about 55
percent of the time and is called equivalent 7 percent of the time. 0.9 against 0.9 reaches
`inert` 39 percent of the time, stays undecided about 60 percent, and wrongly decides `keep` or
`harmful` about 2 percent: a rule is proven useless only when the model is reliable without it.
"""
import math
import random

KEEP = "keep"
HARMFUL = "harmful"
INERT = "inert"
INERT_FAILING = "inert-fail"
UNDECIDED = "undecided"

BATCH = 3
MAX_RUNS = 30
HELPS_LINE = 0.99
EQUIV_LINE = 0.975
MARGIN = 0.2
# The without arm must pass at least this often for equivalence to mean "the rule is useless"
# rather than "the case fails either way".
RELIABLE = 0.8
GRID = 400

# (passes, runs, grid) -> (weights, running sums). `simulate` asks for the same few hundred
# pairs over and over.
_ARM = {}


def _arm(passes, runs, grid):
    key = (passes, runs, grid)
    hit = _ARM.get(key)
    if hit is not None:
        return hit
    logs = []
    for i in range(grid):
        x = (i + 0.5) / grid
        logs.append(passes * math.log(x) + (runs - passes) * math.log(1.0 - x))
    top = max(logs)
    raw = [math.exp(v - top) for v in logs]
    total = sum(raw)
    weights = [v / total for v in raw]
    cumulative = []
    running = 0.0
    for w in weights:
        running += w
        cumulative.append(running)
    _ARM[key] = (weights, cumulative)
    return _ARM[key]


def _check(passes, runs):
    if runs < 0 or passes < 0 or passes > runs:
        raise ValueError("%d passes out of %d runs is not a count" % (passes, runs))


def posterior(passes_with, n_with, passes_without, n_without, margin=MARGIN, grid=GRID):
    """P(with beats without) and P(the two pass rates differ by less than `margin`)."""
    _check(passes_with, n_with)
    _check(passes_without, n_without)
    weights, _ = _arm(passes_with, n_with, grid)
    other, cumulative = _arm(passes_without, n_without, grid)
    m = int(round(margin * grid))
    helps = 0.0
    equivalent = 0.0
    for i, w in enumerate(weights):
        below = cumulative[i - 1] if i > 0 else 0.0
        helps += w * (below + other[i] / 2.0)
        high = cumulative[min(i + m - 1, grid - 1)]
        low = cumulative[i - m] if i - m >= 0 else 0.0
        equivalent += w * (high - low)
    return {
        "p_helps": helps,
        "p_equivalent": equivalent,
        "with_mean": (passes_with + 1) / (n_with + 2),
        "without_mean": (passes_without + 1) / (n_without + 2),
    }


def decide(passes_with, n_with, passes_without, n_without, margin=MARGIN,
           helps_line=HELPS_LINE, equiv_line=EQUIV_LINE, reliable=RELIABLE):
    """The verdict, checked in this order: keep, harmful, inert or inert-fail, undecided."""
    post = posterior(passes_with, n_with, passes_without, n_without, margin=margin)
    with_rate = passes_with / n_with if n_with else None
    without_rate = passes_without / n_without if n_without else None
    if not n_with or not n_without:
        verdict = UNDECIDED
    elif post["p_helps"] >= helps_line:
        verdict = KEEP
    elif 1.0 - post["p_helps"] >= helps_line:
        verdict = HARMFUL
    elif post["p_equivalent"] >= equiv_line:
        verdict = INERT if without_rate >= reliable else INERT_FAILING
    else:
        verdict = UNDECIDED
    return {
        "verdict": verdict,
        "p_helps": post["p_helps"],
        "p_equivalent": post["p_equivalent"],
        "with_rate": with_rate,
        "without_rate": without_rate,
        "passes": {"with": passes_with, "without": passes_without},
        "measured": {"with": n_with, "without": n_without},
    }


def simulate(p_with, p_without, reps=300, seed=7, batch=BATCH, max_runs=MAX_RUNS, **lines):
    """Replay the sequential rule on invented arms: the error rates the docstring quotes."""
    rng = random.Random(seed)
    counts = {}
    runs_total = 0
    for _ in range(reps):
        k_with = k_without = n = 0
        while True:
            step = min(batch, max_runs - n)
            k_with += sum(1 for _ in range(step) if rng.random() < p_with)
            k_without += sum(1 for _ in range(step) if rng.random() < p_without)
            n += step
            out = decide(k_with, n, k_without, n, **lines)
            if out["verdict"] != UNDECIDED or n >= max_runs:
                break
        counts[out["verdict"]] = counts.get(out["verdict"], 0) + 1
        runs_total += n
    return {
        "shares": {v: c / reps for v, c in counts.items()},
        "mean_runs": runs_total / reps,
    }
