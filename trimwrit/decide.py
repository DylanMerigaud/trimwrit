"""`trimwrit run --decide`: run a case a batch at a time until `verdict.decide` says something,
and keep the evidence across invocations so the next run starts where this one stopped.

WHY A LOOP AND NOT A RUN COUNT

The 21 cases measured before this existed rested on 3 runs per arm, mostly one batch of
2026-09-02, and three runs per arm can never decide: 3 of 3 against 0 of 3 gives `p_helps`
0.986, under the 0.99 line. Asking the operator to pick a run count was asking the question the
statistics are there to answer. So a case runs `BATCH` per arm, the verdict is recomputed, and
only the cases still undecided go again, until each one has a verdict or `MAX_RUNS` attempted
runs per arm.

WHY A KEY

A weekly job cannot buy 18 runs per arm for every rule in one sitting, so the counts must add up
across invocations, and they must only add up while they still describe the same experiment.
`evidence_key` hashes everything that makes two runs comparable: the case directory's bytes,
both arms' seeded files, the two model ids, the pass line and the ablation mode. Edit one word
of the rule and the key moves, so none of the old counts leak into the new rule's verdict.
`cli._append_history` writes the key and the counts on every row, plain run or not, and
`prior_counts` sums them back.

`run_batch` is injected (the CLI passes a closure over `runner.run_many`) so the loop is tested
with synthetic results and no subprocess.
"""
import hashlib
import json
import os

from . import verdict
from .runner import ARM_WITH, ARM_WITHOUT

ARMS = (ARM_WITH, ARM_WITHOUT)
COUNTS = ("passes", "measured", "attempted")

DECIDED_EARLIER = "decided-earlier"
EXHAUSTED_EARLIER = "exhausted-earlier"
RAN = "ran"
BUDGET = "budget"


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def files_sha256(with_files, without_files):
    """{"with": {name: sha256}, "without": {...}}: what each arm was seeded with, by hash, so a
    later `prune --apply` can tell whether the file still holds the rule that was measured."""
    return {ARM_WITH: {n: sha256_text(c) for n, c in sorted((with_files or {}).items())},
            ARM_WITHOUT: {n: sha256_text(c) for n, c in sorted((without_files or {}).items())}}


def _case_tree(path):
    """{relative path: sha256 of bytes} for every file under the case directory. A grader's
    pattern or proof sample changing is a different experiment, not only the prompt."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames.sort()
        for name in filenames:
            full = os.path.join(dirpath, name)
            with open(full, "rb") as fh:
                rel = os.path.relpath(full, path).replace(os.sep, "/")
                out[rel] = hashlib.sha256(fh.read()).hexdigest()
    return out


def evidence_key(case, with_files, without_files, model, judge_model, threshold, ablate):
    doc = {"case": _case_tree(case.path), "files": files_sha256(with_files, without_files),
           "model": model, "judge_model": judge_model, "threshold": float(threshold),
           "ablate": ablate}
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode("utf-8")).hexdigest()


def zero_counts():
    return {c: {a: 0 for a in ARMS} for c in COUNTS}


def count(per_arm, threshold):
    """Pass, measured and attempted counts per arm. A run passes when it is measured and its
    score reaches `threshold`; an unmeasured run (an API refusal, no final text) is attempted
    but never measured, so it neither helps nor hurts a rule."""
    out = zero_counts()
    for arm in ARMS:
        for r in per_arm.get(arm, []):
            out["attempted"][arm] += 1
            if r.unmeasured:
                continue
            out["measured"][arm] += 1
            if r.score >= threshold:
                out["passes"][arm] += 1
    return out


def add_counts(a, b):
    return {c: {arm: a[c][arm] + b[c][arm] for arm in ARMS} for c in COUNTS}


def _row_counts(row):
    """The counts of one history row, or None when the row does not carry them (a row written
    before this module existed carries only means, which no posterior can be built from)."""
    try:
        return {c: {a: int(row[c][a]) for a in ARMS} for c in COUNTS}
    except (KeyError, TypeError, ValueError):
        return None


def _read_rows(history_path):
    rows = []
    if not os.path.exists(history_path):
        return rows
    with open(history_path, encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _sum(rows, key):
    total = zero_counts()
    for row in rows:
        if row.get("key") != key:
            continue
        got = _row_counts(row)
        if got is not None:
            total = add_counts(total, got)
    return total


def prior_counts(history_path, key):
    return _sum(_read_rows(history_path), key)


def judge(counts, margin=verdict.MARGIN):
    return verdict.decide(counts["passes"][ARM_WITH], counts["measured"][ARM_WITH],
                          counts["passes"][ARM_WITHOUT], counts["measured"][ARM_WITHOUT],
                          margin=margin)


def _attempted(counts):
    return max(counts["attempted"][a] for a in ARMS)


def run_until_decided(cases, files_by_case, keys, history_path, run_batch, threshold,
                      batch=verdict.BATCH, max_runs=verdict.MAX_RUNS, budget=None,
                      margin=verdict.MARGIN):
    """{case.path: state} for every case, after running the open ones batch by batch.

    A key already decided is `decided-earlier` and a key already at `max_runs` attempted runs
    per arm is `exhausted-earlier`: neither runs again until something in its key changes. Each
    round, the open cases run `min(batch, max_runs - attempted)` per arm (one `run_batch` call
    per distinct size), then each is decided again on its cumulative counts. `budget` caps the
    sessions this call starts: before each round the open cases are taken in name order while
    they fit, and the first that does not fit closes with every case after it as `budget`.
    """
    if budget is not None and budget < 2 * batch:
        raise ValueError("a budget of {} cannot pay for one batch of one case ({} sessions)"
                         .format(budget, 2 * batch))
    rows = _read_rows(history_path)
    states, open_cases = {}, []
    for case in sorted(cases, key=lambda c: os.path.basename(c.path)):
        prior = _sum(rows, keys[case.path])
        with_files, without_files = files_by_case[case.path]
        state = {"key": keys[case.path], "prior": prior, "counts": prior,
                 "results": {a: [] for a in ARMS}, "verdict": judge(prior, margin),
                 "status": None, "spent": 0,
                 "files_sha256": files_sha256(with_files, without_files)}
        states[case.path] = state
        if state["verdict"]["verdict"] != verdict.UNDECIDED:
            state["status"] = DECIDED_EARLIER
        elif _attempted(prior) >= max_runs:
            state["status"] = EXHAUSTED_EARLIER
        else:
            open_cases.append(case)

    spent = 0
    stalled = set()
    while open_cases:
        sizes = {c.path: min(batch, max_runs - _attempted(states[c.path]["counts"]))
                 for c in open_cases}
        if budget is not None:
            fits = []
            for c in open_cases:
                if spent + sum(2 * sizes[f.path] for f in fits) + 2 * sizes[c.path] > budget:
                    break
                fits.append(c)
            for c in open_cases[len(fits):]:
                states[c.path]["status"] = BUDGET
            open_cases = fits
            if not open_cases:
                break
        groups = {}
        for c in open_cases:
            groups.setdefault(sizes[c.path], []).append(c)
        for n in sorted(groups, reverse=True):
            group = groups[n]
            start = {c.path: _attempted(states[c.path]["counts"]) for c in group}
            out = run_batch(group, n, start)
            for c in group:
                per_arm = out.get(c.path, {})
                state = states[c.path]
                got = count(per_arm, threshold)
                state["counts"] = add_counts(state["counts"], got)
                for arm in ARMS:
                    state["results"][arm].extend(per_arm.get(arm, []))
                ran = sum(got["attempted"].values())
                state["spent"] += ran
                spent += ran
                if not ran:
                    stalled.add(c.path)
        still = []
        for c in open_cases:
            state = states[c.path]
            state["verdict"] = judge(state["counts"], margin)
            # A batch that came back with no run at all would ask for the same batch forever.
            if state["verdict"]["verdict"] != verdict.UNDECIDED \
                    or _attempted(state["counts"]) >= max_runs or c.path in stalled:
                state["status"] = RAN
            else:
                still.append(c)
        open_cases = still
    return states
