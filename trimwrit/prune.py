"""The piece none of the twenty-five neighbouring projects ship: taking a rule back out.

Two findings, and they are different kinds of evidence:

ORPHAN   the rule names a case, and the case is not there. Nothing can ever prove this rule is
         still needed, because the thing that would prove it does not exist. Cheap to detect,
         no model call, no money.

INERT    the case exists, it runs, it scores no better with the rule than without it, and it
         PASSES without the rule. The rule is not wrong, it is unnecessary: the model already
         does this. That is the finding that a growing CLAUDE.md never gets told, and it is the
         only honest reason to delete an instruction that everybody still agrees with.

INERT_FAILING  the same flat delta, but the case stays below the pass line in both arms,
         from 0.00/0.00 up to 0.89/0.89. The case is not reliably passed even with the rule,
         so the number cannot say the rule is unneeded: rewrite the rule or its case and
         re-run before deleting anything. `--apply` leaves the rule in place. The pass line is
         the one `trimwrit run` uses (`--threshold`, default 1.0).

HARMFUL  only from a decided verdict: the case passes less often with the rule than without it.
         The rule makes its case worse, so it is rewritten, never silently deleted.

An inert verdict is a measurement, so it carries its numbers. A finding that says "delete this"
without showing the two scores is an opinion with a command-line flag.

VERDICTS BEFORE MEANS

Until 0.9.0 the INERT finding read the mean of three runs per arm, and three runs cannot carry
it: 3 of 3 against 0 of 3 is still under the line verdict.py keeps, so a flat delta over three
runs could not honestly delete anything, and on the author's harness 42 rules entered and none
left. A summary written by `trimwrit run --decide` carries a `verdict` from pass
counts (verdict.py), and when it does the verdict alone decides: `inert` is INERT, `inert-fail`
INERT_FAILING, `harmful` HARMFUL, `keep` and `undecided` no finding at all. The details then show
`k/n` passes per arm and P(helps), not means. A summary with no verdict keeps the delta logic
above, and the CLI says it was measured with a fixed run count.
"""
import hashlib
import os

from . import cases as cases_mod
from . import integrate as integrate_mod
from . import verdict as verdict_mod

ORPHAN = "orphan"
INERT = "inert"
INERT_FAILING = "inert-fail"
HARMFUL = "harmful"
# The pass line `trimwrit run` uses by default (`--threshold`): a case passes when its score is
# at or above it. Prune reads it to tell a case that passes without the rule from one that fails
# in both arms, so the two must stay the same number.
PASS_LINE = 1.0
UNUSED_CASE = "unused-case"

FIXED_COUNT_LINE = ("measured with a fixed run count; `trimwrit run --decide` decides with "
                    "enough runs")
STALE_LINE = "the rule file changed since it was measured; run `trimwrit run --decide` again"

# The verdict strings of verdict.py, mapped to the finding each one is. `keep` and `undecided`
# are absent on purpose: a rule that helps, or one the numbers cannot judge yet, is no finding.
VERDICT_KINDS = {verdict_mod.INERT: INERT, verdict_mod.INERT_FAILING: INERT_FAILING,
                 verdict_mod.HARMFUL: HARMFUL}
VERDICT_WORDS = {
    INERT: "The model behaves without it, decided. Delete the rule and keep the case.",
    INERT_FAILING: ("The two arms are equivalent, but the case is not reliably passed without "
                    "the rule. Rewrite the rule or its case before deleting anything."),
    HARMFUL: "Decided, the rule makes its case worse: rewrite it.",
}


class Finding(object):
    def __init__(self, kind, rule=None, case=None, target=None, detail="", numbers=None):
        self.kind = kind
        self.rule = rule
        self.case = case
        self.target = target
        self.detail = detail
        self.numbers = numbers or {}

    def __repr__(self):
        return "<{} {} {}>".format(self.kind, self.rule or self.case, self.detail[:40])

    def line(self):
        head = "{:<12} {:<8}".format(self.kind, self.rule or self.case or "?")
        return "{} {}".format(head, self.detail)


def case_ids(evals_dir="evals"):
    """The case id is the leading numeric field of the case directory name, which is how
    `trimwrit case` writes it and how a rule marker refers to it."""
    out = {}
    for c in cases_mod.discover(evals_dir):
        base = os.path.basename(c.path)
        out[base.split("-", 1)[0]] = c
        out[base] = c
    return out


def orphans(targets, evals_dir="evals", root="."):
    """Rules whose case is missing. One filesystem walk, no model, no cost."""
    known = case_ids(evals_dir)
    found = []
    for target in targets:
        for rule_id, meta in sorted(integrate_mod.read_rules(target, root).items()):
            # EVERY cited case must be missing before the rule is an orphan. A rule citing two
            # cases where one survives is still backed by evidence, and calling it an orphan
            # would delete a rule that a passing test is holding up.
            cited = integrate_mod.case_list(meta["case"])
            missing = [c for c in cited if c not in known]
            if missing and len(missing) == len(cited):
                found.append(Finding(
                    ORPHAN, rule=rule_id, case=meta["case"], target=target,
                    detail="{}:{} names case {}, which is not in {}/. Nothing can show this "
                           "rule still earns its place.".format(
                               target, meta["line"], ", ".join(missing), evals_dir)))
    return found


def unused_cases(targets, evals_dir="evals", root="."):
    """Cases that no rule points at. Not a deletion order: usually it means an integrate step
    was never run, and the fix is to write the rule, not to delete the case."""
    referenced = set()
    for target in targets:
        for meta in integrate_mod.read_rules(target, root).values():
            referenced.update(integrate_mod.case_list(meta["case"]))
            referenced.add(meta["case"])
    out = []
    for c in cases_mod.discover(evals_dir):
        base = os.path.basename(c.path)
        cid = base.split("-", 1)[0]
        if cid not in referenced and base not in referenced:
            out.append(Finding(
                UNUSED_CASE, case=base,
                detail="no rule in {} points at this case. Either integrate the rule it was "
                       "written for, or delete the case.".format(", ".join(targets) or "any target")))
    return out


def inert(summaries, targets, evals_dir="evals", root=".", pass_line=PASS_LINE):
    """Rules whose case scores no better with them than without.

    `summaries` is {case_dir_basename: runner.summarise(...) dict}. A flat delta is two
    different findings depending on the `without` arm against `pass_line`: at or above it the
    case passes without the rule (INERT, delete the rule); below it the case is not reliably
    passed even with the rule (INERT_FAILING, rewrite the rule or its case first). With a delta
    of zero or less, `without` below the line means `with` is below it too.
    """
    by_case = {}
    for target in targets:
        for rule_id, meta in integrate_mod.read_rules(target, root).items():
            for cid in integrate_mod.case_list(meta["case"]):
                by_case.setdefault(cid, []).append((rule_id, target, meta))

    known = case_ids(evals_dir)
    out = []
    for base, summary in sorted(summaries.items()):
        cid = base.split("-", 1)[0]
        holders = by_case.get(cid) or by_case.get(base) or []
        if "verdict" in summary:
            out.extend(_from_verdict(base, summary, holders, summaries, known))
            continue
        delta = summary.get("delta", 0.0)
        # A `None` delta means the case was unmeasured (see runner.summarise), not that it
        # scored the same in both arms. An unmeasured case has proven nothing either way, and
        # calling it INERT would be a false claim that the model already behaves.
        if delta is None or delta > 0:
            continue
        numbers = {"with": summary.get("with"), "without": summary.get("without"),
                   "delta": delta}
        without = summary.get("without")
        passes_without = without is not None and without >= pass_line
        kind = INERT if passes_without else INERT_FAILING
        if not holders:
            out.append(Finding(
                kind, case=base, numbers=numbers,
                detail="scores {:.2f} with the rule and {:.2f} without it (delta {:+.2f}), and "
                       "no rule claims it.".format(
                           summary.get("with", 0), summary.get("without", 0), delta)))
            continue
        if passes_without:
            verdict = ("The model already behaves. Delete the rule and keep the case.")
        else:
            verdict = ("The case is below the pass line ({:.2f}) in both arms, so it is not "
                       "reliably passed even with the rule. Rewrite the rule or its case and "
                       "re-run it before deleting the rule.".format(pass_line))
        for rule_id, target, meta in holders:
            out.append(Finding(
                kind, rule=rule_id, case=base, target=target, numbers=numbers,
                detail="case {} scores {:.2f} with {} and {:.2f} without it (delta {:+.2f}). "
                       "{}".format(base, summary.get("with", 0), rule_id,
                                   summary.get("without", 0), delta, verdict)))
    return out


def _counts(summary):
    """`k/n with, k/n without, P(helps) p`, the numbers a verdict stands on. P(helps) is
    rounded down, so 0.9997 never prints as a certainty of 1.000."""
    passes = summary.get("passes") or {}
    measured = summary.get("measured") or {}
    p = summary.get("p_helps")
    return "{}/{} with, {}/{} without, P(helps) {}".format(
        passes.get("with", 0), measured.get("with", 0), passes.get("without", 0),
        measured.get("without", 0),
        "{:.3f}".format(int(p * 1000) / 1000.0) if isinstance(p, (int, float)) else "-")


def _summary_for(cid, summaries):
    for base, summary in summaries.items():
        if base == cid or base.split("-", 1)[0] == cid:
            return base, summary
    return None, None


def _held_by(meta, base, summaries, known):
    """The other cases this rule cites that are not decided inert. R0065 cites 0065 and 0066:
    a rule proven useless on one case may still be holding the other up, so it stays until
    every case it cites that exists says `inert`."""
    held = []
    for cited in integrate_mod.case_list(meta["case"]):
        if cited not in known:
            continue
        other, summary = _summary_for(cited, summaries)
        if other == base:
            continue
        if summary is None or summary.get("verdict") != verdict_mod.INERT:
            held.append(other or os.path.basename(known[cited].path))
    return sorted(set(held))


def _from_verdict(base, summary, holders, summaries, known):
    kind = VERDICT_KINDS.get(summary.get("verdict"))
    if kind is None:
        return []
    numbers = {"verdict": summary["verdict"], "passes": summary.get("passes"),
               "measured": summary.get("measured"), "p_helps": summary.get("p_helps"),
               "p_equivalent": summary.get("p_equivalent"), "held_by": []}
    counts = _counts(summary)
    if not holders:
        return [Finding(kind, case=base, numbers=numbers,
                        detail="case {}: {}, and no rule claims it.".format(base, counts))]
    out = []
    for rule_id, target, meta in holders:
        held = _held_by(meta, base, summaries, known)
        detail = "case {}: {}. {}".format(base, counts, VERDICT_WORDS[kind])
        if held and kind == INERT:
            detail += " It stays while it also cites {}, not decided inert.".format(", ".join(held))
        out.append(Finding(kind, rule=rule_id, case=base, target=target,
                           numbers=dict(numbers, held_by=held), detail=detail))
    return out


def read_target(target, root="."):
    """The target's text, or None when it cannot be read."""
    try:
        with open(os.path.join(root, target), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def changed_since_measured(finding, summary, root=".", text=None):
    """True when the finding's target no longer hashes to what the `with` arm seeded.

    A verdict is about the file as it was measured. Delete a rule out of a file that has been
    edited since, and the deletion rests on a measurement of some other text. A summary that
    never recorded the file cannot vouch for it either, so that is True too. `text` is the
    target as the caller read it (the CLI reads every target before its first deletion); by
    default it is read from disk now.
    """
    seeded = ((summary or {}).get("files_sha256") or {}).get("with") or {}
    want = seeded.get(os.path.basename(finding.target or ""))
    if text is None:
        text = read_target(finding.target, root) if finding.target else None
    if not want or text is None:
        return True
    return hashlib.sha256(text.encode("utf-8")).hexdigest() != want


def report(targets, evals_dir="evals", root=".", summaries=None, pass_line=PASS_LINE):
    """Every finding, cheapest evidence first."""
    out = orphans(targets, evals_dir, root)
    out.extend(unused_cases(targets, evals_dir, root))
    if summaries:
        out.extend(inert(summaries, targets, evals_dir, root, pass_line))
    return out
