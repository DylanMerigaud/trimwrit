"""trimwrit: a harness rule enters with a test, and leaves when the test passes without it.

Seven commands, one per step of the loop, plus the one nobody else ships, plus the one that
shows the whole loop as a canvas, plus the one that reads what a rule did after it shipped:

  log        record a correction, verbatim, in an append-only ledger
  pending    which tags have earned a rule, by which of the two routes
  case       turn a correction into an eval case in the native `plugin eval` format
  check      prove every grader can actually fire, before a run trusts it
  integrate  write the rule into a target, with the case id and the incident attached
  run        run the cases, with the rule and without it, and report the delta
  prune      list the rules that no longer earn their place, with the numbers
  viz        serialize the pipeline into a payload and open it as a local canvas
  stats      aggregate a ledger of rule evaluations and flag runtime candidates

The CLI is the only writer. Skills call it, they never edit the ledger or a case by hand, and
that is deliberate: a ledger that a model can edit in prose is a ledger that will disagree with
itself within a week.
"""
import argparse
import hashlib
import json
import os
import sys
import time
import webbrowser

from . import adopt as adopt_mod
from . import audit as audit_mod
from . import cases as cases_mod
from . import check as check_mod
from . import decide as decide_mod
from . import integrate as integrate_mod
from . import ledger as ledger_mod
from . import prune as prune_mod
from . import report as report_mod
from . import models as models_mod
from . import runner as runner_mod
from . import stats as stats_mod
from . import verdict as verdict_mod
from . import viz as viz_mod
from .text import RefusedWrite, one_line, write_text

BAR = "-" * 72
DEFAULT_TARGETS = ("CLAUDE.md",)


def _today():
    return time.strftime("%Y-%m-%d")


def _targets(args):
    if args.target:
        # `~/.claude/CLAUDE.md` is how the user writes a global rule file; joined under the root
        # as it stands it would be looked up in a directory literally named `~`.
        return [os.path.expanduser(t) for t in args.target.split(",") if t]
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


# Shorthand prefixes for --must-match/--must-not-match, each naming a `regex` grader's `match`
# value rather than a specific grader name, so one flag can target every forbid grader (or
# every require grader) of a case with a single sample.
_MATCH_SHORTHANDS = {"forbid": "not_contains", "require": "contains"}


def _split_match_arg(raw):
    """Split one --must-match/--must-not-match value on an optional `NAME=` prefix.

    Returns (target, text). `target` is `None` for the case wide meaning this flag has always
    had (every regex grader gets the sample); otherwise it is the literal text before the
    first `=`, either a grader's own `name` or the shorthand `forbid`/`require`.

    The prefix is only recognised when the `=` sits BEFORE the first space: a value with no
    `=` there keeps today's meaning, so nothing already written to call `trimwrit case`
    changes. This is what lets a sample's own prose contain an `=` (`the config is a=b, fix
    it`) without it being mistaken for a grader target: the first space in that string comes
    before any `=`.
    """
    head = raw.split(" ", 1)[0]
    if "=" not in head:
        return None, raw
    target, _, text = raw.partition("=")
    return target, text


def _route_match_args(raw_values, graders, key):
    """Apply a list of --must-match/--must-not-match values to `graders` (a list of grader
    spec dicts, mutated in place) or collect the case wide ones to hand to `write_case`.

    The incident this exists for: `write_case`'s case wide must_match/must_not_match lands on
    EVERY regex grader, so a case mixing --forbid and --require could not be built through the
    CLI at all (a forbid grader needs a BAD sample, a require grader needs a GOOD one, and one
    shared list cannot be both). Routing a targeted value straight onto the matching grader's
    own dict works because `write_case` already only fills in its case wide default for a
    grader that does not already carry the key (see its docstring); setting the key here first
    is what makes that skip happen.
    """
    case_wide = []
    for raw in raw_values or []:
        target, text = _split_match_arg(raw)
        if target is None:
            case_wide.append(text)
            continue
        match_type = _MATCH_SHORTHANDS.get(target)
        hit = False
        for g in graders:
            if g.get("type") != "regex":
                continue
            if match_type is not None:
                if g.get("match") != match_type:
                    continue
            elif g.get("name") != target:
                continue
            g.setdefault(key, [])
            g[key].append(text)
            hit = True
        if not hit:
            known = ", ".join(g["name"] for g in graders if g.get("type") == "regex")
            raise cases_mod.CaseError(
                "{}={!r}: no regex grader named {!r} in this case, and it is not forbid/"
                "require. Known regex grader(s): {}.".format(
                    target, text, target, known or "none"))
    return case_wide


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
        # Route each --must-match/--must-not-match onto the one grader its NAME= prefix
        # names, before write_case ever sees them; anything left over (no prefix) still goes
        # in as the case wide default, exactly as before. See _route_match_args.
        must_match = _route_match_args(args.must_match, graders, "must_match")
        must_not_match = _route_match_args(args.must_not_match, graders, "must_not_match")
        path = cases_mod.write_case(
            row["id"], title, prompt, graders, evals_dir=os.path.join(args.root, args.evals),
            tags=[t for t in (args.tags or "").split(",") if t] or ([row["tag"]] if row["tag"] else []),
            runs=args.runs, max_turns=args.max_turns,
            description=one_line(row["text"]),
            plugins=[p for p in (args.plugins or "").split(",") if p] or None,
            must_match=must_match, must_not_match=must_not_match)
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

    # Re-integrating a rule that is already promoted is a normal thing to do: you rewrite the
    # wording, or you add a second case to the marker. It is not a failure, and reporting it as
    # one (exit 1, "NOT promoted in the ledger") sent a correct command out looking broken.
    already_live = rule_id in ledger_mod.live_rules(root=args.root)
    try:
        if row.get("tag") and not already_live:
            ledger_mod.promote(row["tag"], rule_id, route=args.route,
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


def _unchecked_summary(reason, arms):
    """The stand-in `summarise()`-shaped dict for a case whose grader failed its own proof and
    was never run at all. `with` is `None` for the same reason a genuinely unmeasured run is
    `None` (see runner.summarise): there is no honest score to report, and the two situations
    share one accounting path in the code below rather than two."""
    return {"with": None, "without": None if runner_mod.ARM_WITHOUT in arms else 0.0,
           "delta": None, "runs": {a: 0 for a in arms},
           "unmeasured": {a: 0 for a in arms},
           "unmeasured_detail": {a: None for a in arms},
           "unchecked_reason": reason}


def _append_history(path, ts, case, summary, rule_files, targets, claude, model=None,
                    judge_model=None, model_usage=None, evidence=None):
    """One JSON line per case per `run` invocation, appended forever.

    `results/latest.json` (see `_save_results`) is overwritten on every run and answers "what is
    true now"; this answers "what has this rule's delta looked like across every run anyone has
    done", which `prune` cannot see and nothing else in this repo keeps. `target_sha256` pins
    which version of the rule file produced these numbers, computed from `rule_files` (what was
    actually seeded into the `with` arm) rather than from re-reading disk later, so a rewrite of
    the target between two runs is visible as a different hash on this line, not a silent one.

    Plain data, hashes and numbers and a case name that is already a filesystem-safe slug, so
    this bypasses `text.append_text`'s dash gate the same way `runner.write_evidence` does: the
    gate is for written copy, not for a machine ledger that cannot legitimately contain one.

    `evidence` adds the fields `run --decide` sums back (see decide.py): the evidence `key`,
    the `ablate` mode, this invocation's `passes`, `measured` and `attempted` per arm, and
    `files_sha256`; a decide row also carries its cumulative `verdict`, `p_helps` and
    `p_equivalent`. Every run writes them, so a plain run's evidence counts toward a decision.
    """
    row = {
        "ts": ts, "case": os.path.basename(case.path), "target": list(targets),
        "target_sha256": {name: hashlib.sha256(content.encode("utf-8")).hexdigest()
                          for name, content in sorted(rule_files.items())},
        "with": summary["with"], "without": summary.get("without"), "delta": summary["delta"],
        "runs": summary["runs"].get(runner_mod.ARM_WITH, 0),
        "unmeasured": summary.get("unmeasured", {}), "claude": claude,
        # `answered_by`: per model id Claude Code reported, how many runs it answered.
        "model": model, "judge_model": judge_model, "answered_by": dict(model_usage or {}),
    }
    row.update(evidence or {})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# (attribute, flag, the default `--decide` gives it): flags that mean nothing to a plain run.
DECIDE_ONLY = (("batch", "--batch", verdict_mod.BATCH),
               ("max_runs", "--max-runs", verdict_mod.MAX_RUNS),
               ("budget", "--budget", None),
               ("margin", "--margin", verdict_mod.MARGIN))


def _decide_refusal(args):
    """The sentence that refuses a `run` whose flags contradict each other, or None.

    `--batch`, `--max-runs`, `--budget` and `--margin` default to None so a plain run given one
    of them is refused instead of ignoring it: a `--budget 60` that a plain run silently drops
    would start every case's full run count. With `--decide`, an unset one takes verdict.py's
    default here.
    """
    if args.no_ablation and args.ablate == "rule":
        return ("refused: --no-ablation runs no without arm, so there is no rule for "
                "--ablate rule to take out.")
    if not args.decide:
        given = [flag for attr, flag, _ in DECIDE_ONLY if getattr(args, attr) is not None]
        if given:
            return "refused: {} only {} with --decide.".format(
                ", ".join(given), "applies" if len(given) == 1 else "apply")
        return None
    for attr, _, default in DECIDE_ONLY:
        if getattr(args, attr) is None:
            setattr(args, attr, default)
    if args.runs is not None:
        return ("refused: --decide chooses how many runs each case needs, so it cannot take "
                "--runs as well.")
    if args.no_ablation:
        return ("refused: --decide compares the arm with the rule to the arm without it, so it "
                "cannot run with --no-ablation.")
    if args.batch < 1 or args.max_runs < 1:
        return "refused: --batch and --max-runs must each be at least 1."
    if args.budget is not None and args.budget < 2 * args.batch:
        # A budget that cannot pay for one batch of one case would end in a run that started
        # nothing and exited 0, which reads as "all decided" in a weekly log.
        return ("refused: a budget of {} sessions cannot pay for one batch of one case ({} "
                "sessions, 2 x --batch).".format(args.budget, 2 * args.batch))
    return None


def _arm_files(args, found, rule_files):
    """({case.path: (with_files, without_files)}, {case.path: (kind, reason)}).

    `--ablate file` seeds every case with the whole rule files and the `without` arm with
    nothing, as `run` always has. `--ablate rule` seeds each case with only the targets citing
    it, and takes the citing blocks out of the `without` copy (runner.rule_arm_files); a case
    it cannot ablate is returned in the second dict and is not run.
    """
    files, skipped = {}, {}
    for case in found:
        if args.ablate != "rule":
            files[case.path] = (rule_files, {})
            continue
        try:
            files[case.path] = runner_mod.rule_arm_files(case, _targets(args), args.root)
        except runner_mod.AblationError as exc:
            skipped[case.path] = (exc.kind, str(exc))
    return files, skipped


def _skipped_payload(skipped):
    return [{"name": os.path.basename(path), "kind": kind, "reason": reason}
            for path, (kind, reason) in sorted(skipped.items())]


def _print_skipped(skipped):
    for path, (kind, reason) in sorted(skipped.items()):
        print("{:<36} not run, {}: {}".format(os.path.basename(path), kind, reason))


def _arms_payload(per_arm):
    return {arm: [{"passed": r.passed, "score": r.score, "error": r.error,
                   "unmeasured": r.unmeasured, "grades": r.grades, "model": r.model,
                   "model_usage": r.model_usage, "judge_model": r.judge_model,
                   "judge_model_usage": r.judge_model_usage} for r in rs]
            for arm, rs in per_arm.items()}


def _seen_usage(per_arm):
    """Per model id Claude Code reported, how many runs it answered."""
    seen = {}
    for rs in per_arm.values():
        for r in rs:
            for mid in r.model_usage:
                seen[mid] = seen.get(mid, 0) + 1
    return seen


DECIDE_WORDS = {
    verdict_mod.KEEP: "earns its place",
    verdict_mod.HARMFUL: "HARMFUL, worse with the rule",
    verdict_mod.INERT: "INERT, the model behaves without it",
    verdict_mod.INERT_FAILING: "INERT-FAIL, rewrite the rule or its case",
}


def _decide_words(state):
    word = DECIDE_WORDS.get(state["verdict"]["verdict"])
    if word:
        return word
    if state["status"] == decide_mod.BUDGET:
        return "undecided, budget spent"
    return "undecided after {} runs per arm".format(
        max(state["counts"]["attempted"].values()))


def _decide_summary(state, ablate, rules_sha256=None):
    """What latest.json holds for a case decided by `run --decide`, cumulative over every
    invocation under its key. `with`, `without` and `delta` are pass rates, so a reader of the
    old fields (viz, prune before it reads `verdict`) sees numbers on the same 0 to 1 scale.
    `rules_sha256` (rule mode) is what prune's stale guard compares against the file now."""
    v, counts = state["verdict"], state["counts"]
    with_rate, without_rate = v["with_rate"], v["without_rate"]
    return {
        "verdict": v["verdict"], "p_helps": v["p_helps"], "p_equivalent": v["p_equivalent"],
        "passes": counts["passes"], "measured": counts["measured"],
        "attempted": counts["attempted"], "key": state["key"], "ablate": ablate,
        "files_sha256": state["files_sha256"], "rules_sha256": rules_sha256,
        "with": with_rate, "without": without_rate,
        "delta": (with_rate - without_rate
                  if with_rate is not None and without_rate is not None else None),
        "status": state["status"], "spent": state["spent"],
    }


def _run_decide(args, runnable, blocked, skipped, files_by_case, keys, rule_files, judge,
                progress, evals_dir, history_path, ts, unproven_total, rules_by_case=None):
    """`run --decide`: the loop is decide.run_until_decided; this writes what it found.

    Evidence files and a history row only for a case that ran in this invocation (a case
    decided earlier adds nothing to sum), latest.json for every claimed case, run now or not,
    because `prune` reads its verdicts from there and a second invocation that runs nothing
    must still leave them in place. Exit 0: an undecided case is a result, not a failure.
    """
    arms = (runner_mod.ARM_WITH, runner_mod.ARM_WITHOUT)

    def run_batch(cases, n, start_by_case):
        return runner_mod.run_many(
            cases, rule_files, arms=arms, runs=n, claude=args.claude, judge=judge,
            progress=progress, isolate=not args.no_isolation, jobs=args.jobs, model=args.model,
            files_by_case=files_by_case, start_by_case=start_by_case)

    states = decide_mod.run_until_decided(
        runnable, files_by_case, keys, history_path, run_batch, args.threshold,
        batch=args.batch, max_runs=args.max_runs, budget=args.budget, margin=args.margin)

    ablate = args.ablate
    summaries, payload = {}, {"cases": [], "skipped": _skipped_payload(skipped),
                              "unchecked": [{"name": os.path.basename(p), "reason": r}
                                            for p, r in sorted(blocked.items())]}
    spent, wrote_evidence = 0, False
    for case in sorted(runnable, key=lambda c: os.path.basename(c.path)):
        base = os.path.basename(case.path)
        state = states[case.path]
        summary = _decide_summary(state, ablate, (rules_by_case or {}).get(case.path))
        summaries[base] = summary
        payload["cases"].append({"name": base, "summary": summary,
                                 "arms": _arms_payload(state["results"])})
        if not state["spent"]:
            continue
        spent += state["spent"]
        if not args.no_save:
            for rs in state["results"].values():
                for r in rs:
                    runner_mod.write_evidence(r, evals_dir, key=state["key"])
                    wrote_evidence = True
        evidence = {"key": state["key"], "ablate": ablate,
                    "files_sha256": state["files_sha256"],
                    "rules_sha256": summary["rules_sha256"],
                    "verdict": summary["verdict"], "p_helps": summary["p_helps"],
                    "p_equivalent": summary["p_equivalent"]}
        evidence.update(decide_mod.count(state["results"], args.threshold))
        _append_history(history_path, ts, case, runner_mod.summarise(state["results"]),
                        files_by_case[case.path][0], _targets(args), args.claude,
                        model=args.model, judge_model=args.judge_model,
                        model_usage=_seen_usage(state["results"]), evidence=evidence)

    budget = "none" if args.budget is None else args.budget
    if args.json:
        payload["sessions"] = spent
        payload["budget"] = args.budget
        _save_results(args, summaries, stream=sys.stderr)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    _print_skipped(skipped)
    row = "{:<36} {:<12} {:<15} {:<16} {}"
    print("\n" + row.format("case", "with", "without", "P(helps)", "verdict"))
    print(BAR)
    for case in sorted(runnable, key=lambda c: os.path.basename(c.path)):
        base, state = os.path.basename(case.path), states[case.path]
        s = summaries[base]
        passes, measured = s["passes"], s["measured"]
        # Rounded down, so 0.9997 never prints as a certainty of 1.000.
        helps = ("{:.3f}".format(int(s["p_helps"] * 1000) / 1000.0)
                 if measured["with"] and measured["without"] else "-")
        print(row.format(base, "with {}/{}".format(passes["with"], measured["with"]),
                         "without {}/{}".format(passes["without"], measured["without"]),
                         "P(helps) {}".format(helps), _decide_words(state)))
    for path, reason in sorted(blocked.items()):
        print("{:<36} UNCHECKED, grader failed its own proof: {}".format(
            os.path.basename(path), reason))
    print("\nsessions started: {} (budget {}; judge calls not counted)".format(spent, budget))
    _save_results(args, summaries)
    if unproven_total and not args.strict:
        print("{} unproven grader(s), run with --strict to refuse them.".format(unproven_total))
    if wrote_evidence:
        print("evidence written to {}".format(os.path.join(evals_dir, "results", "runs")))
    print("viz: run 'trimwrit viz --target {}' to see this as a canvas".format(_targets(args)[0]))
    return 0


def cmd_run(args):
    # Both ids are checked before anything is discovered or run: an alias would let the model
    # under test move between two replays while the history line says nothing changed.
    try:
        models_mod.require_full_id(args.model)
        models_mod.require_full_id(args.judge_model)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    refusal = _decide_refusal(args)
    if refusal:
        print(refusal, file=sys.stderr)
        return 2
    evals_dir = os.path.join(args.root, args.evals)
    found = cases_mod.discover(evals_dir)
    if args.case:
        found = [c for c in found if args.case in os.path.basename(c.path)]
    if not found:
        print("no cases in {}".format(evals_dir), file=sys.stderr)
        return 2
    if getattr(args, "sandbox_note", False):
        for case in found:
            case.meta["sandbox_note"] = True

    rule_files = _rule_files(args, args.root)
    if not rule_files and not args.no_ablation:
        print("no rule target found ({}). The `with` arm would be identical to the `without`\n"
              "arm, and the delta would be meaningless. Point --target at the file that carries\n"
              "the rules, or pass --no-ablation to run one arm only."
              .format(", ".join(_targets(args))), file=sys.stderr)
        return 2

    # Rule level ablation decides which files each case is seeded with, so it runs before
    # anything else looks at a case: a case no target claims is not under test at all.
    ablate = "none" if args.no_ablation else args.ablate
    files_by_case, skipped = _arm_files(args, found, rule_files)
    found = [c for c in found if c.path not in skipped]

    arms = (runner_mod.ARM_WITH,) if args.no_ablation else (runner_mod.ARM_WITH,
                                                            runner_mod.ARM_WITHOUT)
    judge = runner_mod.make_judge(args.claude, args.judge_model)

    def progress(case, arm, i, n):
        if not args.quiet:
            print("  {:<34} {:<8} run {}/{}".format(os.path.basename(case.path), arm, i, n),
                  file=sys.stderr)

    # Every selected case is checked BEFORE anything runs: a grader that cannot prove it can
    # fire is a hole in the measurement, and the incident this exists for (see check.py) is a
    # suite that ran to completion and reported a green board with the graders disconnected.
    # A hard failure always refuses the case; an unproven grader (no must_match/must_not_match
    # at all) only refuses it under --strict, and is a warning otherwise.
    runnable, blocked = [], {}
    unproven_total = 0
    for case in found:
        problems = check_mod.check_case(case)
        hard = [p for p in problems if p["kind"] == check_mod.FAILURE]
        unproven = [p for p in problems if p["kind"] == check_mod.UNPROVEN]
        unproven_total += len(unproven)
        blocking = hard + (unproven if args.strict else [])
        if blocking:
            blocked[case.path] = "; ".join(p["reason"] for p in blocking)
        else:
            runnable.append(case)

    history_path = os.path.join(evals_dir, "results", "history.jsonl")
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    runs_dir = os.path.join(evals_dir, "results", "runs")
    # Rule mode: the experiment is the citing blocks, so the key hashes them and not the whole
    # files (decide.py, WHY A KEY). None in file mode, where the whole file is under test.
    rules_by_case = {c.path: (integrate_mod.rules_sha256(
        _targets(args), os.path.basename(c.path), args.root) if ablate == "rule" else None)
        for c in found}
    keys = {c.path: decide_mod.evidence_key(
        c, files_by_case[c.path][0], files_by_case[c.path][1], args.model, args.judge_model,
        args.threshold, ablate, rules_sha256=rules_by_case[c.path]) for c in found}

    if args.decide:
        return _run_decide(args, runnable, blocked, skipped, files_by_case, keys, rule_files,
                           judge, progress, evals_dir, history_path, ts, unproven_total,
                           rules_by_case=rules_by_case)

    results_by_case = runner_mod.run_many(
        runnable, rule_files, arms=arms, runs=args.runs, claude=args.claude, judge=judge,
        progress=progress, isolate=not args.no_isolation, jobs=args.jobs, model=args.model,
        files_by_case=files_by_case)

    summaries, payload = {}, {"cases": []}
    if skipped:
        payload["skipped"] = _skipped_payload(skipped)
    failures, unmeasured_cases, wrote_evidence = 0, 0, False
    for case in found:
        base = os.path.basename(case.path)
        if case.path in blocked:
            summary = _unchecked_summary(blocked[case.path], arms)
            payload["cases"].append({"name": base, "summary": summary,
                                    "arms": {a: [] for a in arms}})
        else:
            per_arm = results_by_case[case.path]
            if not args.no_save:
                for rs in per_arm.values():
                    for r in rs:
                        runner_mod.write_evidence(r, evals_dir, key=keys[case.path])
                        wrote_evidence = True
            summary = runner_mod.summarise(per_arm)
            payload["cases"].append({"name": base, "summary": summary,
                                     "arms": _arms_payload(per_arm)})
        summaries[base] = summary

        if summary["with"] is None:
            unmeasured_cases += 1
            if args.fail_on_unmeasured:
                failures += 1
        elif summary["with"] < args.threshold:
            failures += 1

        per_arm = results_by_case.get(case.path) or {}
        evidence = {"key": keys[case.path], "ablate": ablate,
                    "files_sha256": decide_mod.files_sha256(*files_by_case[case.path]),
                    "rules_sha256": rules_by_case[case.path]}
        evidence.update(decide_mod.count(per_arm, args.threshold))
        _append_history(history_path, ts, case, summary, files_by_case[case.path][0],
                        _targets(args), args.claude, model=args.model,
                        judge_model=args.judge_model, model_usage=_seen_usage(per_arm),
                        evidence=evidence)

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1 if failures else 0

    _print_skipped(skipped)
    print("\n{:<36} {:>6} {:>9} {:>7}  verdict".format("case", "with", "without", "delta"))
    print(BAR)
    for base, s in sorted(summaries.items()):
        if s.get("unchecked_reason"):
            print("{:<36} {:>6} {:>9} {:>7}  UNCHECKED, grader failed its own proof: {}".format(
                base, "?", "?", "?", s["unchecked_reason"]))
            continue
        if args.no_ablation:
            if s["with"] is None:
                verdict = "UNMEASURED ({} run(s): {})".format(
                    s["unmeasured"]["with"], s["unmeasured_detail"]["with"])
                print("{:<36} {:>6} {:>9} {:>7}  {}".format(base, "?", "-", "-", verdict))
            else:
                verdict = "pass" if s["with"] >= args.threshold else "FAIL"
                print("{:<36} {:>6.2f} {:>9} {:>7}  {}".format(
                    base, s["with"], "-", "-", verdict))
            continue
        if s["with"] is None:
            verdict = "UNMEASURED ({} run(s): {})".format(
                s["unmeasured"]["with"], s["unmeasured_detail"]["with"])
            without_str = "{:.2f}".format(s["without"]) if s["without"] is not None else "?"
            print("{:<36} {:>6} {:>9} {:>7}  {}".format(base, "?", without_str, "?", verdict))
            continue
        without_str = "{:.2f}".format(s["without"]) if s["without"] is not None else "?"
        delta_str = "{:+.2f}".format(s["delta"]) if s["delta"] is not None else "?"
        if s["with"] < args.threshold:
            verdict = "FAIL, the rule is not holding"
        elif s["delta"] is None:
            verdict = "UNMEASURED, the without arm never produced a measured run"
        elif s["delta"] <= 0:
            verdict = "INERT, passes without the rule too"
        else:
            verdict = "earns its place"
        print("{:<36} {:>6.2f} {:>9} {:>7}  {}".format(
            base, s["with"], without_str, delta_str, verdict))

    if not args.no_ablation:
        print("\nA case that scores the same in both arms is measuring nothing. Run\n"
              "`trimwrit prune --results` to see which rules that makes deletable.")
        _save_results(args, summaries)
    if unmeasured_cases:
        print("\n{} case(s) unmeasured, they are not passes.".format(unmeasured_cases))
    if unproven_total and not args.strict:
        print("{} unproven grader(s), run with --strict to refuse them.".format(unproven_total))
    if not args.no_save and wrote_evidence:
        print("evidence written to {}".format(runs_dir))
    print("viz: run 'trimwrit viz --target {}' to see this as a canvas".format(_targets(args)[0]))
    return 1 if failures else 0


def _save_results(args, summaries, stream=None):
    path = os.path.join(args.root, args.evals, "results", "latest.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(summaries, fh, indent=2)
    print("results written to {}".format(path), file=stream or sys.stdout)


# ------------------------------------------------------------------ check


def cmd_check(args):
    evals_dir = os.path.join(args.root, args.evals)
    found = cases_mod.discover(evals_dir)
    if args.case:
        found = [c for c in found if args.case in os.path.basename(c.path)]
    if not found:
        print("no cases in {}".format(evals_dir), file=sys.stderr)
        return 2

    problems = []
    for case in found:
        problems.extend(check_mod.check_case(case))
    hard = [p for p in problems if p["kind"] == check_mod.FAILURE]
    unproven = [p for p in problems if p["kind"] == check_mod.UNPROVEN]
    failures = hard + unproven if args.strict else hard
    n_graders = sum(len(c.graders) for c in found)

    if args.json:
        print(json.dumps({"cases": len(found), "graders": n_graders,
                          "failures": failures, "unproven": unproven},
                         indent=2, ensure_ascii=False))
        return 1 if failures else 0

    for p in failures:
        print("{} / {}: {}".format(p["case"], p["grader"], p["reason"]))
    print("\n{} case(s), {} grader(s) checked, {} failure(s).".format(
        len(found), n_graders, len(failures)))
    if unproven and not args.strict:
        print("{} unproven grader(s), run with --strict to refuse them.".format(len(unproven)))
    return 1 if failures else 0


# ------------------------------------------------------------------ prune


def cmd_prune(args):
    summaries = None
    # `is not None`, not a truthiness test. `--results` with no value stores the empty string,
    # the empty string is falsy, and the whole inert branch was skipped in silence: prune
    # reported only orphans and said "nothing to prune" about a rule whose case scored the same
    # in both arms. The bug was invisible because the command still exited 0 with a reassuring
    # message, which is the failure mode this repo is about.
    if args.results is not None:
        path = args.results if os.path.exists(args.results) else os.path.join(
            args.root, args.evals, "results", "latest.json")
        if not os.path.exists(path):
            print("no results at {}. Run `trimwrit run` first: the inert finding is a\n"
                  "measurement and it needs numbers.".format(path), file=sys.stderr)
            return 2
        with open(path, encoding="utf-8") as fh:
            summaries = json.load(fh)

    findings = prune_mod.report(_targets(args), os.path.join(args.root, args.evals),
                                args.root, summaries, args.threshold)
    if not findings:
        print("nothing to prune. Every rule names a case that exists, and no case that ran\n"
              "says its rule is unneeded.")
        print("viz: run 'trimwrit viz --target {}' to see this as a canvas".format(_targets(args)[0]))
        return 0

    print("{:<12} {:<8} why".format("kind", "rule"))
    print(BAR)
    for f in findings:
        print(f.line())
    measured = (prune_mod.INERT, prune_mod.INERT_FAILING)
    if any(f.kind in measured and "verdict" not in f.numbers for f in findings):
        print("\n" + prune_mod.FIXED_COUNT_LINE + ".")

    if args.apply:
        # Every guard is decided before the first deletion: deleting one rule must not make the
        # next one look changed (file mode hashes the whole file).
        stale = {id(f): prune_mod.changed_since_measured(f, summaries.get(f.case), args.root,
                                                         targets=_targets(args))
                 for f in findings if f.kind == prune_mod.INERT and "verdict" in f.numbers}
        removed = 0
        for f in findings:
            # Only an orphan or a decided inert rule is deleted. An inert-fail rule is a rewrite
            # order (its case is not reliably passed), a harmful one too (its case is worse with
            # it), and an unused case names no rule.
            if f.kind not in (prune_mod.ORPHAN, prune_mod.INERT) or not f.rule or not f.target:
                continue
            if f.kind == prune_mod.INERT and "verdict" not in f.numbers:
                # A mean over a fixed run count is a signal, not a decision: three runs per arm
                # cannot tell inert from noise (verdict.py), so it is printed and kept.
                print("kept {}: {}".format(f.rule, prune_mod.FIXED_COUNT_KEPT))
                continue
            if "verdict" in f.numbers:
                if f.numbers.get("held_by"):
                    print("kept {}: it also cites {}, not decided inert.".format(
                        f.rule, ", ".join(f.numbers["held_by"])))
                    continue
                if stale[id(f)]:
                    print("kept {}: {}".format(f.rule, prune_mod.STALE_LINE))
                    continue
            if integrate_mod.drop_rule(f.target, f.rule, root=args.root):
                ledger_mod.remove(f.rule, f.detail, root=args.root)
                removed += 1
        print("\nremoved {} rule(s), each one recorded in the ledger with the reason.".format(removed))
    else:
        print("\nnothing was changed. Re-run with --apply to delete these rules and record the\n"
              "removal in the ledger.")
    if any(f.kind == prune_mod.INERT_FAILING for f in findings):
        print("an inert-fail rule is kept even with --apply: its case is not reliably passed\n"
              "even with the rule, so rewrite the rule or its case before deleting it.")
    if any(f.kind == prune_mod.HARMFUL for f in findings):
        print("a harmful rule is kept even with --apply: its case passes less often with it\n"
              "than without it, so rewrite it.")
    print("viz: run 'trimwrit viz --target {}' to see this as a canvas".format(_targets(args)[0]))
    return 0


# ------------------------------------------------------------------ viz


def cmd_viz(args):
    target = args.target or DEFAULT_TARGETS[0]
    doc = viz_mod.build_doc(target, root=args.root, evals_dir=args.evals)

    if args.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0

    payload = viz_mod.encode_payload(doc)
    viewer = viz_mod.viewer_path()

    if not os.path.exists(viewer):
        out_path = os.path.join(args.root, ledger_mod.LEDGER_DIR, "viz.json")
        write_text(out_path, json.dumps(doc, indent=2, ensure_ascii=False), "the viz payload")
        print("no viewer at {}. Build viz/dist/index.html first, or install a trimwrit release "
              "that carries it.".format(viewer))
        print("wrote the raw payload to {} instead.".format(out_path))
        return 0

    url = viz_mod.payload_url(viewer, payload)
    if args.no_open:
        print(url)
        return 0

    webbrowser.open(url)
    print("opened {} (the payload rides in the URL fragment, nothing left this machine)."
          .format(viewer))
    return 0


# ------------------------------------------------------------------ stats


def _label(row):
    return "{}/{}".format(row["surface"], row["rule"]) if row["surface"] else row["rule"]


def cmd_stats(args):
    try:
        result = stats_mod.compute(
            args.ledger, min_fires=args.min_fires, close_margin=args.close_margin,
            friction_fires=args.friction_fires, friction_rate=args.friction_rate)
    except OSError as exc:
        print("refused: cannot read {}: {}".format(args.ledger, exc), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    print("{} row(s) read, {} malformed (blank or not JSON, skipped).".format(
        result["total_rows"], result["malformed"]))

    if result["surfaces"]:
        print("\n{:<28} {:>6}".format("surface", "fires"))
        print(BAR)
        for surface, n in sorted(result["surfaces"].items(), key=lambda kv: (-kv[1], kv[0])):
            print("{:<28} {:>6}".format(surface, n))

    print("\n{} rule key(s) evaluated.".format(len(result["rules"])))

    t = result["thresholds"]
    print("\nDEAD WEIGHT (fires >= {}, 0 fail, 0 close call within {} of the line)".format(
        t["min_fires"], t["close_margin"]))
    print(BAR)
    if not result["dead_weight"]:
        print("none.")
    for r in result["dead_weight"]:
        print("{:<32} {:>5} fires   min margin {}".format(
            _label(r), r["fires"], r["min_margin"] if r["min_margin"] is not None else "-"))
    print("\nThese rules run on text a model GENERATED. Zero fails can mean the generator\n"
          "already internalized the rule, and deleting the rule is the only move that finds\n"
          "out, because that is what un-internalizes it. The strong case for deletion is a rule\n"
          "that is ALSO absent from every logged correction, which this command cannot check.")

    print("\nFRICTION (decided >= {}, fail rate >= {:.0%})".format(
        t["friction_fires"], t["friction_rate"]))
    print(BAR)
    if not result["friction"]:
        print("none.")
    for r in result["friction"]:
        print("{:<32} {:>5} decided   fail rate {:.3f}".format(
            _label(r), r["decided"], r["fail_rate"]))
    print("\nA rule that refuses this much of what it sees is either load-bearing or costing\n"
          "more than it protects. Which one it is stays a human call, not a number.")
    return 0


# ------------------------------------------------------------------ report


DEFAULT_HISTORY = os.path.join("evals", "results", "history.jsonl")


def cmd_report(args):
    ledger_path = args.ledger if args.ledger is not None else ledger_mod.path_for(".")
    history = args.history
    if history is None and os.path.exists(DEFAULT_HISTORY):
        history = DEFAULT_HISTORY
    try:
        doc = report_mod.build(ledger_path, args.refusals, history)
    except OSError as exc:
        shown = exc.filename if exc.filename is not None else ledger_path
        print("refused: cannot read {}: {}".format(shown, exc.strerror or exc), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0
    print(report_mod.render(doc, BAR))
    return 0


# ------------------------------------------------------------------ audit


def cmd_audit(args):
    result = audit_mod.compute(args.root, roots=args.roots, laptop=args.laptop,
                               stale_days=args.stale)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(audit_mod.render_table(result))
    return 0


# ------------------------------------------------------------------ adopt


def cmd_adopt(args):
    result = adopt_mod.compute(args.root, roots=args.roots, laptop=args.laptop,
                               dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(adopt_mod.render_report(result))
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
    cs.add_argument("--must-match", action="append",
                    help="text a regex grader's pattern must match, its proof it can fire. "
                         "With no `=` before the first space it applies to every regex "
                         "grader, same as always; prefix with a grader's own name or the "
                         "shorthand forbid=/require= to target only that grader (repeatable)")
    cs.add_argument("--must-not-match", action="append",
                    help="text a regex grader's pattern must NOT match. Same optional NAME=/ "
                         "forbid=/require= prefix as --must-match (repeatable)")
    cs.set_defaults(func=cmd_case)

    ck = sub.add_parser("check", help="prove every grader can actually fire, before a run "
                                      "trusts it")
    ck.add_argument("--case", help="substring filter on the case directory name")
    ck.add_argument("--strict", action="store_true",
                    help="a regex grader with no must_match/must_not_match is a failure, not "
                         "just a warning")
    ck.add_argument("--json", action="store_true")
    ck.set_defaults(func=cmd_check)

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
    rn.add_argument("--threshold", type=float, default=prune_mod.PASS_LINE)
    rn.add_argument("--no-ablation", action="store_true",
                    help="run the `with` arm only. Faster, and it proves nothing about the rule")
    rn.add_argument("--claude", default="claude")
    rn.add_argument("--model", default=models_mod.DEFAULT_CASE_MODEL,
                    help="full model id the cases run on (default {}); an alias is refused. "
                         "Known ids: {}".format(models_mod.DEFAULT_CASE_MODEL,
                                                ", ".join(models_mod.KNOWN_MODELS)))
    rn.add_argument("--judge-model", default=models_mod.DEFAULT_JUDGE_MODEL,
                    help="full model id for llm graders (default {}); an alias is refused"
                         .format(models_mod.DEFAULT_JUDGE_MODEL))
    rn.add_argument("--json", action="store_true")
    rn.add_argument("--quiet", action="store_true")
    rn.add_argument("--sandbox-note", action="store_true",
                    help="tell the model it runs in a scratch directory with no repository, so "
                         "it answers instead of hunting for files. Off by default: a case that "
                         "measures whether the model can tell it is being tested must never "
                         "carry it (same as `sandbox_note: true` in a case's front matter)")
    rn.add_argument("--no-isolation", action="store_true",
                    help="let the run inherit your own ~/.claude/CLAUDE.md. Off by default, "
                         "because it contaminates the baseline arm and every delta with it")
    rn.add_argument("--strict", action="store_true",
                    help="refuse a case whose regex grader has no must_match/must_not_match, "
                         "instead of just warning about it (see `trimwrit check`)")
    rn.add_argument("--fail-on-unmeasured", action="store_true",
                    help="a case unmeasured in the `with` arm counts toward the exit code. Off "
                         "by default: an API safeguard or a max_turns cutoff is not the rule "
                         "failing")
    rn.add_argument("--no-save", action="store_true",
                    help="do not write the per-run evidence files under results/runs/")
    rn.add_argument("--decide", action="store_true",
                    help="run each case {} runs per arm at a time until its verdict is decided "
                         "(keep, harmful, inert, inert-fail) or it reaches --max-runs; counts "
                         "add up across invocations while the case, the rule files and the "
                         "models are unchanged".format(verdict_mod.BATCH))
    rn.add_argument("--ablate", choices=("file", "rule"), default="file",
                    help="file: the without arm has no rule file (default). rule: both arms "
                         "seed the targets citing the case, the without arm minus the citing "
                         "rule blocks, so the verdict is about the rule alone")
    rn.add_argument("--batch", type=int,
                    help="--decide: runs per arm per round (default {})".format(verdict_mod.BATCH))
    rn.add_argument("--max-runs", type=int,
                    help="--decide: attempted runs per arm after which a case stays undecided "
                         "(default {})".format(verdict_mod.MAX_RUNS))
    rn.add_argument("--budget", type=int,
                    help="--decide: the most case sessions this invocation starts; judge calls "
                         "are not counted")
    rn.add_argument("--margin", type=float,
                    help="--decide: two pass rates closer than this are equivalent (default "
                         "{})".format(verdict_mod.MARGIN))
    rn.add_argument("--jobs", type=int, default=1,
                    help="run this many (case, arm, run) triples at once across ALL selected "
                         "cases. The table, JSON, evidence and history are identical to --jobs "
                         "1, just faster")
    rn.set_defaults(func=cmd_run)

    pr = sub.add_parser("prune", help="rules that no longer earn their place")
    pr.add_argument("--target", help="comma separated rule files (default CLAUDE.md)")
    pr.add_argument("--results", nargs="?", const="",
                    help="results json from `trimwrit run` (default evals/results/latest.json), "
                         "needed for the inert finding; `run --decide` writes the verdicts")
    pr.add_argument("--apply", action="store_true",
                    help="actually delete, and record it: orphans and rules decided inert by "
                         "`run --decide`. An inert-fail or harmful rule is never deleted, nor "
                         "an inert one from a fixed run count, nor one edited since it was "
                         "measured")
    pr.add_argument("--threshold", type=float, default=prune_mod.PASS_LINE,
                    help="the pass line the run used (default {}): a flat case at or above it "
                         "without the rule is inert, below it inert-fail".format(
                             prune_mod.PASS_LINE))
    pr.set_defaults(func=cmd_prune)

    vz = sub.add_parser("viz", help="serialize the pipeline into a canvas payload")
    vz.add_argument("--target", help="the rule file to build the graph against (default "
                                     "CLAUDE.md), one file, not a comma separated list")
    vz.add_argument("--json", action="store_true", help="print the raw JSON document, nothing "
                                                        "else, no browser")
    vz.add_argument("--no-open", action="store_true",
                    help="print the full file:// URL instead of opening a browser")
    vz.set_defaults(func=cmd_viz)

    st = sub.add_parser("stats", help="aggregate a ledger of rule evaluations, no deletion")
    st.add_argument("ledger", help="JSONL file, one rule evaluation per line or per gate")
    st.add_argument("--json", action="store_true", help="print the full aggregate as JSON")
    st.add_argument("--min-fires", type=int, default=stats_mod.DEAD_WEIGHT_MIN_FIRES,
                    help="dead weight: fires needed before a clean record counts for anything")
    st.add_argument("--close-margin", type=float, default=stats_mod.DEAD_WEIGHT_CLOSE_MARGIN,
                    help="dead weight: a margin at or under this is a close call")
    st.add_argument("--friction-fires", type=int, default=stats_mod.FRICTION_MIN_DECIDED,
                    help="friction: decided evaluations needed before a fail rate counts")
    st.add_argument("--friction-rate", type=float, default=stats_mod.FRICTION_MIN_RATE,
                    help="friction: fail rate at or above this flags the rule")
    st.set_defaults(func=cmd_stats)

    rp = sub.add_parser("report", help="the whole life of one harness from its own ledgers: "
                                       "counts and dates, never a correction's words")
    rp.add_argument("--ledger", help="the correction ledger (default .trimwrit/ledger.jsonl)")
    rp.add_argument("--refusals", help="JSONL of the refusals the harness's doors logged")
    rp.add_argument("--history", help="JSONL of measured eval runs (default "
                                      "evals/results/history.jsonl when that file exists)")
    rp.add_argument("--json", action="store_true", help="print the document as JSON")
    rp.set_defaults(func=cmd_report)

    au = sub.add_parser("audit", help="one table for every harness on the machine")
    au.add_argument("--roots", nargs="+", help="directories to scan (default: --root)")
    au.add_argument("--laptop", action="store_true", help="scan ~/.claude and ~/Code")
    au.add_argument("--json", action="store_true")
    au.add_argument("--stale", type=int, default=audit_mod.DEFAULT_STALE_DAYS,
                    help="days before a repo's last run counts as stale")
    au.set_defaults(func=cmd_audit)

    ad = sub.add_parser("adopt", help="bring the rules written before trimwrit existed into "
                                      "its registry, with no case")
    ad.add_argument("--roots", nargs="+", help="directories to scan (default: --root)")
    ad.add_argument("--laptop", action="store_true", help="scan ~/.claude and ~/Code")
    ad.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    ad.add_argument("--json", action="store_true")
    ad.set_defaults(func=cmd_adopt)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (ledger_mod.LedgerError, cases_mod.CaseError, integrate_mod.IntegrateError,
            RefusedWrite) as exc:
        print("refused: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
