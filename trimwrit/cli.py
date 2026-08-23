"""trimwrit: a harness rule enters with a test, and leaves when the test passes without it.

Six commands, one per step of the loop, plus the one nobody else ships:

  log        record a correction, verbatim, in an append-only ledger
  pending    which tags have earned a rule, by which of the two routes
  case       turn a correction into an eval case in the native `plugin eval` format
  integrate  write the rule into a target, with the case id and the incident attached
  run        run the cases, with the rule and without it, and report the delta
  prune      list the rules that no longer earn their place, with the numbers

The CLI is the only writer. Skills call it, they never edit the ledger or a case by hand, and
that is deliberate: a ledger that a model can edit in prose is a ledger that will disagree with
itself within a week.
"""
import argparse
import json
import os
import sys
import time

from . import cases as cases_mod
from . import integrate as integrate_mod
from . import ledger as ledger_mod
from . import prune as prune_mod
from . import runner as runner_mod
from .text import RefusedWrite, one_line

BAR = "-" * 72
DEFAULT_TARGETS = ("CLAUDE.md",)


def _today():
    return time.strftime("%Y-%m-%d")


def _targets(args):
    if args.target:
        return [t for t in args.target.split(",") if t]
    return list(DEFAULT_TARGETS)


# ------------------------------------------------------------------ log / pending / show


def cmd_log(args):
    row = ledger_mod.log(args.text, tag=args.tag, consequence=args.consequence,
                         session=args.session, source=args.source, root=args.root)
    print("logged correction {}  tag={}".format(row["id"], row["tag"] or "(none)"))
    if not row["tag"]:
        print("  no tag. An untagged correction never counts toward a rule, because a tag is the\n"
              "  claim that two incidents are the same incident, and only you can make it.\n"
              "  Fix with: trimwrit tag {} <tag>".format(row["id"]))
    ready = [p for p in ledger_mod.pending(root=args.root) if p[0] == row["tag"]]
    if ready:
        tag, n, route = ready[0]
        print("  tag {} now qualifies for a rule by the {} route (n={}).".format(tag, route, n))
        print("  next: trimwrit case {}".format(row["id"]))
    return 0


def cmd_tag(args):
    ledger_mod.tag(args.id, args.tag, root=args.root)
    print("correction {} tagged {}".format(args.id, args.tag))
    return 0


def cmd_pending(args):
    rows = ledger_mod.pending(root=args.root)
    if not rows:
        print("nothing pending. Every tagged correction has either become a rule or is still a\n"
              "single observation without a written consequence.")
        return 0
    print("{:<28} {:>3}  {}".format("tag", "n", "route"))
    print(BAR)
    for tag, n, route in rows:
        print("{:<28} {:>3}  {}".format(tag, n, route))
    return 0


def cmd_show(args):
    rows = ledger_mod.folded(root=args.root)
    if not rows:
        print("the ledger is empty")
        return 0
    for r in rows:
        if args.tag and r.get("tag") != args.tag:
            continue
        print("{}  {}  tag={}  case={}  rule={}".format(
            r["id"], r["at"][:10], r.get("tag") or "-", r.get("case") or "-",
            r.get("promoted") or "-"))
        print("     {}".format(r["text"]))
        if r.get("consequence"):
            print("     consequence: {}".format(r["consequence"]))
    return 0


# ------------------------------------------------------------------ case


def cmd_case(args):
    row = ledger_mod.get(args.id, root=args.root)
    prompt = args.prompt
    if args.prompt_file:
        with open(args.prompt_file, encoding="utf-8") as fh:
            prompt = fh.read()
    if not prompt:
        print("a case needs the prompt that REPLAYS the situation, not a description of it.\n"
              "Pass --prompt or --prompt-file. The prompt should be what you would type to get\n"
              "the bad output again, with the rule absent.", file=sys.stderr)
        return 2

    # Graders are named after the TAG, not after a slug of the pattern. A pattern slug is
    # unreadable when the pattern is punctuation (a character class slugs to nothing) and
    # merely ugly the rest of the time, and the file name is what a reviewer reads first.
    stem = row.get("tag") or "correction-{}".format(row["id"])
    graders = []
    for i, pattern in enumerate(args.forbid or [], 1):
        suffix = "" if len(args.forbid) == 1 else "-{}".format(i)
        graders.append(cases_mod.forbid_grader(
            pattern, name="forbids-{}{}".format(stem, suffix),
            note="From correction {}: {}".format(row["id"], row["text"])))
    for i, pattern in enumerate(args.require or [], 1):
        suffix = "" if len(args.require) == 1 else "-{}".format(i)
        graders.append(cases_mod.require_grader(
            pattern, name="requires-{}{}".format(stem, suffix),
            note="From correction {}: {}".format(row["id"], row["text"])))
    for tool in args.tool or []:
        graders.append(cases_mod.tool_grader(tool))
    if args.judge:
        graders.append(cases_mod.judge_grader(
            args.judge, name="judge-{}".format(stem),
            note="From correction {}: {}".format(row["id"], row["text"])))

    if graders and not cases_mod.is_mechanical(graders):
        print("note: this case has only a judge. A judge costs money on every run and disagrees\n"
              "      with itself. If any part of the correction can be checked with a string,\n"
              "      add --forbid or --require and let the judge cover only the rest.")

    title = args.title or row["text"]
    try:
        path = cases_mod.write_case(
            row["id"], title, prompt, graders, evals_dir=os.path.join(args.root, args.evals),
            tags=[t for t in (args.tags or "").split(",") if t] or ([row["tag"]] if row["tag"] else []),
            runs=args.runs, max_turns=args.max_turns,
            description=one_line(row["text"]),
            plugins=[p for p in (args.plugins or "").split(",") if p] or None)
    except cases_mod.CaseError as exc:
        print("refused: {}".format(exc), file=sys.stderr)
        return 2

    base = os.path.basename(path)
    ledger_mod.attach_case(row["id"], base.split("-", 1)[0], root=args.root)
    print("wrote {}".format(path))
    print("  prompt.md and {} grader(s). This is the native `claude plugin eval` layout, so the\n"
          "  same files run under `trimwrit run` today and under `claude plugin eval` the day it\n"
          "  opens up.".format(len(graders)))
    print("  next: trimwrit run --case {}   then   trimwrit integrate {} --rule \"...\""
          .format(base, row["id"]))
    return 0


# ------------------------------------------------------------------ integrate


def cmd_integrate(args):
    row = ledger_mod.get(args.id, root=args.root)
    case_id = args.case or row.get("case")
    if not case_id:
        print("correction {} has no case yet. Generate it first:\n"
              "  trimwrit case {} --prompt \"...\" --forbid \"...\"\n"
              "A rule with no case is a preference, and this tool does not write preferences."
              .format(row["id"], row["id"]), file=sys.stderr)
        return 2

    rule_id = args.rule_id or "R{}".format(row["id"])
    incident = args.incident or row.get("consequence") or row["text"]
    target = args.target or DEFAULT_TARGETS[0]

    try:
        res = integrate_mod.write_rule(target, rule_id, case_id, args.date or _today(),
                                       incident, args.rule, root=args.root)
    except (integrate_mod.IntegrateError, RefusedWrite) as exc:
        print("refused: {}".format(exc), file=sys.stderr)
        return 2

    route = args.route
    try:
        if row.get("tag"):
            ledger_mod.promote(row["tag"], rule_id, route=route,
                               consequence=args.incident or row.get("consequence"),
                               root=args.root)
    except ledger_mod.LedgerError as exc:
        print("rule written to {}, but NOT promoted in the ledger: {}".format(target, exc),
              file=sys.stderr)
        return 1

    ledger_mod.attach_rule(row["id"], rule_id, root=args.root)
    print("{} {} in {} (case {})".format(res["action"], rule_id, target, case_id))
    print("  the marker carries the case id, the date and the incident, which is what lets the\n"
          "  next reader delete it without guessing what it protected.")
    return 0


# ------------------------------------------------------------------ run


def _rule_files(args, root):
    """{relative path: contents} for the arm that has the rule.

    Whole files, not fragments. The ablation asks "does this file change the behaviour", and a
    file half present is a third condition nobody asked about.
    """
    out = {}
    for target in _targets(args):
        path = os.path.join(root, target)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            out[os.path.basename(target)] = fh.read()
    return out


def cmd_run(args):
    evals_dir = os.path.join(args.root, args.evals)
    found = cases_mod.discover(evals_dir)
    if args.case:
        found = [c for c in found if args.case in os.path.basename(c.path)]
    if not found:
        print("no cases in {}".format(evals_dir), file=sys.stderr)
        return 2

    rule_files = _rule_files(args, args.root)
    if not rule_files and not args.no_ablation:
        print("no rule target found ({}). The `with` arm would be identical to the `without`\n"
              "arm, and the delta would be meaningless. Point --target at the file that carries\n"
              "the rules, or pass --no-ablation to run one arm only."
              .format(", ".join(_targets(args))), file=sys.stderr)
        return 2

    arms = (runner_mod.ARM_WITH,) if args.no_ablation else (runner_mod.ARM_WITH,
                                                            runner_mod.ARM_WITHOUT)
    judge = runner_mod.make_judge(args.claude, args.judge_model)

    def progress(case, arm, i, n):
        if not args.quiet:
            print("  {:<34} {:<8} run {}/{}".format(os.path.basename(case.path), arm, i, n),
                  file=sys.stderr)

    summaries, payload = {}, {"cases": []}
    failures = 0
    for case in found:
        per_arm = runner_mod.run_case(case, rule_files, arms=arms, runs=args.runs,
                                      claude=args.claude, judge=judge, progress=progress,
                                      isolate=not args.no_isolation)
        summary = runner_mod.summarise(per_arm)
        base = os.path.basename(case.path)
        summaries[base] = summary
        payload["cases"].append({"name": base, "summary": summary, "arms": {
            arm: [{"passed": r.passed, "score": r.score, "error": r.error,
                   "grades": r.grades} for r in rs] for arm, rs in per_arm.items()}})
        if summary["with"] < args.threshold:
            failures += 1

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1 if failures else 0

    print("\n{:<36} {:>6} {:>9} {:>7}  verdict".format("case", "with", "without", "delta"))
    print(BAR)
    for base, s in sorted(summaries.items()):
        if args.no_ablation:
            verdict = "pass" if s["with"] >= args.threshold else "FAIL"
            print("{:<36} {:>6.2f} {:>9} {:>7}  {}".format(base, s["with"], "-", "-", verdict))
            continue
        if s["with"] < args.threshold:
            verdict = "FAIL, the rule is not holding"
        elif s["delta"] <= 0:
            verdict = "INERT, passes without the rule too"
        else:
            verdict = "earns its place"
        print("{:<36} {:>6.2f} {:>9.2f} {:>+7.2f}  {}".format(
            base, s["with"], s["without"], s["delta"], verdict))

    if not args.no_ablation:
        print("\nA case that scores the same in both arms is measuring nothing. Run\n"
              "`trimwrit prune --results` to see which rules that makes deletable.")
        _save_results(args, summaries)
    return 1 if failures else 0


def _save_results(args, summaries):
    path = os.path.join(args.root, args.evals, "results", "latest.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(summaries, fh, indent=2)
    print("results written to {}".format(path))


# ------------------------------------------------------------------ prune


def cmd_prune(args):
    summaries = None
    if args.results:
        path = args.results if os.path.exists(args.results) else os.path.join(
            args.root, args.evals, "results", "latest.json")
        if not os.path.exists(path):
            print("no results at {}. Run `trimwrit run` first: the inert finding is a\n"
                  "measurement and it needs numbers.".format(path), file=sys.stderr)
            return 2
        with open(path, encoding="utf-8") as fh:
            summaries = json.load(fh)

    findings = prune_mod.report(_targets(args), os.path.join(args.root, args.evals),
                                args.root, summaries)
    if not findings:
        print("nothing to prune. Every rule names a case that exists, and every case that ran\n"
              "scored better with its rule than without it.")
        return 0

    print("{:<12} {:<8} why".format("kind", "rule"))
    print(BAR)
    for f in findings:
        print(f.line())

    if args.apply:
        removed = 0
        for f in findings:
            if f.kind == prune_mod.UNUSED_CASE or not f.rule or not f.target:
                continue
            if integrate_mod.drop_rule(f.target, f.rule, root=args.root):
                ledger_mod.remove(f.rule, f.detail, root=args.root)
                removed += 1
        print("\nremoved {} rule(s), each one recorded in the ledger with the reason.".format(removed))
    else:
        print("\nnothing was changed. Re-run with --apply to delete these rules and record the\n"
              "removal in the ledger.")
    return 0


# ------------------------------------------------------------------ parser


def main(argv=None):
    p = argparse.ArgumentParser(prog="trimwrit", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=".", help="repository root (default: the cwd)")
    p.add_argument("--evals", default="evals", help="directory holding the eval cases")
    sub = p.add_subparsers(dest="cmd", required=True)

    lg = sub.add_parser("log", help="record a correction, verbatim")
    lg.add_argument("text", help="what the human actually said, not your summary of it")
    lg.add_argument("--tag", help="the handle that makes two incidents the same incident")
    lg.add_argument("--consequence", help="what it cost, once, in the real world. Unlocks the "
                                          "important route at n=1")
    lg.add_argument("--session", help="session id, used to refuse a duplicate on a retry")
    lg.add_argument("--source", default="manual")
    lg.set_defaults(func=cmd_log)

    tg = sub.add_parser("tag", help="attach a tag to a correction already logged")
    tg.add_argument("id")
    tg.add_argument("tag")
    tg.set_defaults(func=cmd_tag)

    pd = sub.add_parser("pending", help="tags that have earned a rule, and by which route")
    pd.set_defaults(func=cmd_pending)

    sh = sub.add_parser("show", help="the ledger, folded")
    sh.add_argument("--tag")
    sh.set_defaults(func=cmd_show)

    cs = sub.add_parser("case", help="turn a correction into a native eval case")
    cs.add_argument("id")
    cs.add_argument("--prompt", help="the prompt that REPLAYS the situation")
    cs.add_argument("--prompt-file")
    cs.add_argument("--title", help="short title, used for the directory name")
    cs.add_argument("--forbid", action="append", help="regex the output must NOT contain")
    cs.add_argument("--require", action="append", help="regex the output MUST contain")
    cs.add_argument("--tool", action="append", help="a tool the run must call")
    cs.add_argument("--judge", help="rubric for an llm grader. Last resort, it costs money")
    cs.add_argument("--tags", help="comma separated")
    cs.add_argument("--plugins", help="comma separated plugin paths for the native runner")
    cs.add_argument("--runs", type=int, default=3)
    cs.add_argument("--max-turns", type=int, default=6)
    cs.set_defaults(func=cmd_case)

    ig = sub.add_parser("integrate", help="write the rule into a target, with its case attached")
    ig.add_argument("id")
    ig.add_argument("--rule", required=True, help="the rule text as it will be read by a model")
    ig.add_argument("--target", help="file to write into (default CLAUDE.md)")
    ig.add_argument("--case", help="case id, if not the one already on the correction")
    ig.add_argument("--rule-id", help="default R<correction id>")
    ig.add_argument("--incident", help="one line: what went wrong, once, on a real day")
    ig.add_argument("--date", help="default today")
    ig.add_argument("--route", default=ledger_mod.ROUTE_COUNTER, choices=ledger_mod.ROUTES)
    ig.set_defaults(func=cmd_integrate)

    rn = sub.add_parser("run", help="run the cases with the rule and without it")
    rn.add_argument("--case", help="substring filter on the case directory name")
    rn.add_argument("--target", help="comma separated rule files to ablate (default CLAUDE.md)")
    rn.add_argument("--runs", type=int, help="override the per-case run count")
    rn.add_argument("--threshold", type=float, default=1.0)
    rn.add_argument("--no-ablation", action="store_true",
                    help="run the `with` arm only. Faster, and it proves nothing about the rule")
    rn.add_argument("--claude", default="claude")
    rn.add_argument("--judge-model", help="model for llm graders")
    rn.add_argument("--json", action="store_true")
    rn.add_argument("--quiet", action="store_true")
    rn.add_argument("--no-isolation", action="store_true",
                    help="let the run inherit your own ~/.claude/CLAUDE.md. Off by default, "
                         "because it contaminates the baseline arm and every delta with it")
    rn.set_defaults(func=cmd_run)

    pr = sub.add_parser("prune", help="rules that no longer earn their place")
    pr.add_argument("--target", help="comma separated rule files (default CLAUDE.md)")
    pr.add_argument("--results", nargs="?", const="", help="results json from `trimwrit run`, "
                                                           "needed for the inert finding")
    pr.add_argument("--apply", action="store_true", help="actually delete, and record it")
    pr.set_defaults(func=cmd_prune)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (ledger_mod.LedgerError, cases_mod.CaseError, integrate_mod.IntegrateError,
            RefusedWrite) as exc:
        print("refused: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
