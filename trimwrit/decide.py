"""`trimwrit run --decide`: run a case a batch at a time until `verdict.decide` says something,
and keep the evidence across invocations so the next run starts where this one stopped.

WHY A LOOP AND NOT A RUN COUNT

The 21 cases measured before this existed rested on 3 runs per arm, mostly one batch of
2026-09-02, and three runs per arm can never decide: 3 of 3 against 0 of 3 gives `p_helps`
0.986, under the 0.99 line. Asking the operator to pick a run count was asking the question the
statistics are there to answer. So a case runs `BATCH` per arm, the verdict is recomputed, and
only the cases still undecided go again, until each one has a verdict or `MAX_RUNS` measured
runs per arm.

WHY UNMEASURED RUNS ARE COUNTED APART

Until 0.9.2 the cap counted attempted runs, so a week where every `claude -p` call failed (a
usage limit, an outage) ran every case to the cap with nothing measured, and the case was
closed for good. Now an unmeasured run (runner.Result.unmeasured) is spent against `--budget`,
since it started a session, but `MAX_RUNS` caps measured runs only. A round where no started
session was measured, across every case, stops the invocation (`stopped` "unmeasured", exit 4
in the CLI): the next round would fail the same way. A key whose unmeasured runs reach
`MAX_RUNS` in one arm is `unmeasurable` and not started again until its key changes, so one
broken case cannot spend the budget every week.

WHY A ROUND AT A TIME

`on_round` is called after every round with that round's runs only, and the CLI writes their
evidence and one history row per case there: a killed invocation keeps the rounds it finished,
and since a row carries one round's counts, summing the rows never counts a run twice.

WHY A KEY

A weekly job cannot buy 18 runs per arm for every rule in one sitting, so the counts must add up
across invocations, and they must only add up while they still describe the same experiment.
`evidence_key` hashes everything that makes two runs comparable: the case directory's bytes,
what the rule is, the two model ids, the pass line, the ablation mode and the sandbox note. With
`--ablate rule`, what the rule is means the blocks citing the case (`rules_sha256`, from
integrate.rules_sha256): edit one word of one of them and the key moves, so none of the old
counts leak into the new rule's verdict, while an edit elsewhere in the same file (the global
CLAUDE.md changes almost daily, and a prune deletes from it) keeps the evidence. With `--ablate
file` the whole file is the thing under test, so both arms' files are hashed whole.
`files_sha256` is kept on every row for information either way. `cli._append_history` writes
the key and the counts on every row, plain run or not, and `prior_counts` sums them back.

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
UNMEASURABLE = "unmeasurable"
STOPPED = "stopped"
# `Outcome.stopped` when a round measured nothing.
STOPPED_UNMEASURED = "unmeasured"


class Outcome(dict):
    """{case.path: state}, plus `stopped`: None, or STOPPED_UNMEASURED when the invocation
    stopped because a round measured no run."""

    stopped = None


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def files_sha256(with_files, without_files):
    """{"with": {name: sha256}, "without": {...}}: what each arm was seeded with, by hash, so a
    later `prune --apply` can tell whether the file still holds the rule that was measured."""
    return {ARM_WITH: {n: sha256_text(c) for n, c in sorted((with_files or {}).items())},
            ARM_WITHOUT: {n: sha256_text(c) for n, c in sorted((without_files or {}).items())}}


def _case_tree(path):
    """{relative path: sha256 of bytes} for every file under the case directory. A grader's
    pattern or proof sample changing is a different experiment, not only the prompt. A path
    with a component starting with `.` is skipped: a `.DS_Store` Finder drops is not one."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in filenames:
            if name.startswith("."):
                continue
            full = os.path.join(dirpath, name)
            with open(full, "rb") as fh:
                rel = os.path.relpath(full, path).replace(os.sep, "/")
                out[rel] = hashlib.sha256(fh.read()).hexdigest()
    return out


def evidence_key(case, with_files, without_files, model, judge_model, threshold, ablate,
                 rules_sha256=None):
    """The sha256 that names one experiment. `rules_sha256` (rule mode) stands in for both
    arms' files: the rule blocks are what differs between the arms, the rest of the file is
    context that may change without changing the experiment's subject."""
    # `sandbox_note` from the case's meta, where `run --sandbox-note` also sets it: a run told
    # it sits in a scratch directory is a different experiment, and the flag is not in the
    # case's bytes.
    doc = {"case": _case_tree(case.path), "model": model, "judge_model": judge_model,
           "threshold": float(threshold), "ablate": ablate,
           "sandbox_note": bool(case.meta.get("sandbox_note"))}
    if rules_sha256 is not None:
        doc["rules"] = dict(rules_sha256)
    else:
        doc["files"] = files_sha256(with_files, without_files)
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode("utf-8")).hexdigest()


def zero_counts():
    return {c: {a: 0 for a in ARMS} for c in COUNTS}


def count(per_arm, threshold):
    """Pass, measured and attempted counts per arm. A run passes when it is measured and its
    score reaches `threshold`; an unmeasured run (runner.Result.unmeasured) is attempted but
    never measured, so it neither helps nor hurts a rule."""
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


def _measured(counts):
    return max(counts["measured"][a] for a in ARMS)


def unmeasured(counts):
    """{arm: attempted minus measured}."""
    return {a: counts["attempted"][a] - counts["measured"][a] for a in ARMS}


def _unmeasurable(counts, max_runs):
    return max(unmeasured(counts).values()) >= max_runs


def run_until_decided(cases, files_by_case, keys, history_path, run_batch, threshold,
                      batch=verdict.BATCH, max_runs=verdict.MAX_RUNS, budget=None,
                      margin=verdict.MARGIN, on_round=None):
    """An Outcome, {case.path: state} for every case, after running the open ones batch by batch.

    A key already decided is `decided-earlier`, a key already at `max_runs` measured runs per
    arm is `exhausted-earlier`, and a key whose unmeasured runs reached `max_runs` in an arm is
    `unmeasurable`: none runs again until something in its key changes. Each round, the open
    cases run `min(batch, max_runs - measured)` per arm (one `run_batch` call per distinct
    size), then each is decided again on its cumulative counts, and `on_round(ran, states)` gets
    `{case.path: per_arm}` of that round's runs. `budget` caps the sessions this call starts,
    measured or not: before each round the open cases are taken in name order while they fit,
    and the first that does not fit closes with every case after it as `budget`. A round that
    started sessions and measured none closes every open case as `stopped` and sets
    `Outcome.stopped`.
    """
    if budget is not None and budget < 2 * batch:
        raise ValueError("a budget of {} cannot pay for one batch of one case ({} sessions)"
                         .format(budget, 2 * batch))
    rows = _read_rows(history_path)
    states, open_cases = Outcome(), []
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
        elif _measured(prior) >= max_runs:
            state["status"] = EXHAUSTED_EARLIER
        elif _unmeasurable(prior, max_runs):
            state["status"] = UNMEASURABLE
        else:
            open_cases.append(case)

    spent = 0
    stalled = set()
    while open_cases:
        sizes = {c.path: min(batch, max_runs - _measured(states[c.path]["counts"]))
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
        ran_now, started, measured = {}, 0, 0
        for n in sorted(groups, reverse=True):
            group = groups[n]
            # Run indexes follow the attempted count, so an unmeasured run's evidence file is
            # never overwritten by the next one.
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
                started += ran
                measured += sum(got["measured"].values())
                if ran:
                    ran_now[c.path] = per_arm
                else:
                    stalled.add(c.path)
        dead_round = started > 0 and measured == 0
        still = []
        for c in open_cases:
            state = states[c.path]
            state["verdict"] = judge(state["counts"], margin)
            # A batch that came back with no run at all would ask for the same batch forever.
            if state["verdict"]["verdict"] != verdict.UNDECIDED \
                    or _measured(state["counts"]) >= max_runs or c.path in stalled:
                state["status"] = RAN
            elif _unmeasurable(state["counts"], max_runs):
                state["status"] = UNMEASURABLE
            elif dead_round:
                state["status"] = STOPPED
            else:
                still.append(c)
        if on_round is not None and ran_now:
            on_round(ran_now, states)
        if dead_round:
            states.stopped = STOPPED_UNMEASURED
            break
        open_cases = still
    return states
