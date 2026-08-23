"""The piece none of the twenty-five neighbouring projects ship: taking a rule back out.

Two findings, and they are different kinds of evidence:

ORPHAN   the rule names a case, and the case is not there. Nothing can ever prove this rule is
         still needed, because the thing that would prove it does not exist. Cheap to detect,
         no model call, no money.

INERT    the case exists, it runs, and it scores the same with the rule and without it. The
         rule is not wrong, it is unnecessary: the model already does this. That is the finding
         that a growing CLAUDE.md never gets told, and it is the only honest reason to delete an
         instruction that everybody still agrees with.

An inert verdict is a measurement, so it carries its numbers. A finding that says "delete this"
without showing the two scores is an opinion with a command-line flag.
"""
import os

from . import cases as cases_mod
from . import integrate as integrate_mod

ORPHAN = "orphan"
INERT = "inert"
UNUSED_CASE = "unused-case"


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
            if meta["case"] not in known:
                found.append(Finding(
                    ORPHAN, rule=rule_id, case=meta["case"], target=target,
                    detail="{}:{} names case {}, which is not in {}/. Nothing can show this "
                           "rule still earns its place.".format(
                               target, meta["line"], meta["case"], evals_dir)))
    return found


def unused_cases(targets, evals_dir="evals", root="."):
    """Cases that no rule points at. Not a deletion order: usually it means an integrate step
    was never run, and the fix is to write the rule, not to delete the case."""
    referenced = set()
    for target in targets:
        for meta in integrate_mod.read_rules(target, root).values():
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


def inert(summaries, targets, evals_dir="evals", root="."):
    """Rules whose case scores no better with them than without.

    `summaries` is {case_dir_basename: runner.summarise(...) dict}.
    """
    by_case = {}
    for target in targets:
        for rule_id, meta in integrate_mod.read_rules(target, root).items():
            by_case.setdefault(meta["case"], []).append((rule_id, target, meta))

    out = []
    for base, summary in sorted(summaries.items()):
        cid = base.split("-", 1)[0]
        holders = by_case.get(cid) or by_case.get(base) or []
        delta = summary.get("delta", 0.0)
        if delta > 0:
            continue
        numbers = {"with": summary.get("with"), "without": summary.get("without"),
                   "delta": delta}
        if not holders:
            out.append(Finding(
                INERT, case=base, numbers=numbers,
                detail="scores {:.2f} with the rule and {:.2f} without it (delta {:+.2f}), and "
                       "no rule claims it.".format(
                           summary.get("with", 0), summary.get("without", 0), delta)))
            continue
        for rule_id, target, meta in holders:
            out.append(Finding(
                INERT, rule=rule_id, case=base, target=target, numbers=numbers,
                detail="case {} scores {:.2f} with {} and {:.2f} without it (delta {:+.2f}). "
                       "The model already behaves. Delete the rule and keep the case.".format(
                           base, summary.get("with", 0), rule_id, summary.get("without", 0),
                           delta)))
    return out


def report(targets, evals_dir="evals", root=".", summaries=None):
    """Every finding, cheapest evidence first."""
    out = orphans(targets, evals_dir, root)
    out.extend(unused_cases(targets, evals_dir, root))
    if summaries:
        out.extend(inert(summaries, targets, evals_dir, root))
    return out
