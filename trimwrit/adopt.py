"""Bring the rules written before trimwrit existed into its registry, with no case.

Dylan, 2026-09-01, on why: "je veux normaliser, optimiser, tracker, state, et self refine mes
harness. trimwrit me semble un bon moyen", then, on `audit` showing 24 of 28 repos with zero
cases: "ca sert pas qu'a mesurer. mais aussi a structurer, self improve etc...". The loop (log,
case, integrate, run, prune) only knows the rules it promoted itself, through the marker
`integrate.py` writes. Every rule written BEFORE trimwrit existed, which in most real harnesses
is nearly all of them, is invisible to it: not in `prune`, not in `viz`, not in `audit`'s rule
count. `adopt` reads every harness file the same way `audit` already does, splits it into rule
UNITS (a `## ` section, or the whole file when there is none), and records each one as
`adopted`: not measured, not backed by a case, but no longer nothing either. The day a
correction touches one of them, `case` and `integrate` upgrade it into the normal loop; until
then `audit` can show it as adopted and unproven instead of silently absent.

Deliberately reuses `audit`'s walk (`_resolve_roots`, `find_harness_files`, `_find_repo`)
rather than a second one: two walks of the same laptop drifting apart is exactly the kind of
bug `audit.py` exists to catch, and this module would be a strange place to reintroduce it.
"""
import hashlib
import json
import os
import time

from . import audit as audit_mod
from . import integrate as integrate_mod
from .text import write_text

ID_PREFIX = "A"

STATUS_ADOPTED = "adopted"
STATUS_PROMOTED = "promoted"
STATUS_GONE = "gone"

# Three lines, no more: what the directory is, and the two commands that own it. Kept short on
# purpose, the same reason a rule marker is one line: a file nobody reads teaches nothing.
EVALS_README = (
    "This directory holds this repo's trimwrit eval cases.\n"
    "`trimwrit case` writes a new case here.\n"
    "`trimwrit run` reads every case from here.\n"
)


def _today():
    # A function, not a module constant, for the same reason `audit._home()` is one: a frozen
    # value would not see a test that wants to control it, and every "first seen" date in the
    # registry has to come from whenever adopt actually ran, not whenever this module was
    # imported.
    return time.strftime("%Y-%m-%d")


def find_rule_units(text, default_heading):
    """Split `text` into rule units: `(heading, start_line, end_line, section_text)`, 1-based
    inclusive line numbers.

    A rule unit is a `## ` section, heading line through the line before the next `## ` or `# `
    heading, or EOF. Any text before the first `## ` section (a title, a paragraph of preamble)
    belongs to no unit, the same reading `audit._harness_record` already gives that content when
    it counts `sections` as `## ` lines only. A file with no `## ` heading at all is one unit,
    its whole body, headed by the first `# ` line if there is one, else `default_heading` (the
    file's own name, when even a title is missing).
    """
    lines = text.splitlines()
    n = len(lines)
    if n == 0:
        return []

    h2_idx = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    if not h2_idx:
        h1 = next((ln for ln in lines if ln.startswith("# ")), None)
        heading = h1[2:].strip() if h1 else default_heading
        return [(heading, 1, n, "\n".join(lines))]

    # A line starting with "## " never also starts with "# " (the third character differs, "#"
    # vs " "), so this one list safely carries both heading levels with no risk of double
    # counting a level-2 heading as a level-1 boundary too.
    heading_idx = [i for i, ln in enumerate(lines) if ln.startswith("## ") or ln.startswith("# ")]
    units = []
    for i in h2_idx:
        later = [j for j in heading_idx if j > i]
        end_i = (later[0] - 1) if later else n - 1
        heading = lines[i][3:].strip()
        units.append((heading, i + 1, end_i + 1, "\n".join(lines[i:end_i + 1])))
    return units


def _promoted_rules(section_text):
    """Rule ids a section's real trimwrit marker carries, read through `integrate.py`'s own
    parser, never a second regex here. Two readers of the same marker format drifting apart is
    exactly the failure mode `audit._harness_record` already refuses to risk for the same
    reason, and `adopt` would be the second place it could happen.
    """
    seen = []
    for rule_id, _case_id in integrate_mod.rule_ids(section_text):
        if rule_id not in seen:
            seen.append(rule_id)
    return seen


def _build_entry(entry_id, rel, heading, start, end, sha, status, rules, first_seen, seen):
    """One registry row, fields in the order the spec defines them, so the file stays readable
    top to bottom and a diff of it reads like a diff, not a reshuffle. `rules` is present only
    when `status` is `promoted`: a `gone` unit that was once promoted does not keep claiming
    live rule ids, the same reason a removed rule leaves the ledger rather than the marker.
    """
    entry = {
        "id": entry_id,
        "file": rel,
        "heading": heading,
        "start": start,
        "end": end,
        "lines": (end - start + 1) if start is not None and end is not None else None,
        "sha256": sha,
        "status": status,
    }
    if status == STATUS_PROMOTED:
        entry["rules"] = rules
    entry["case"] = None
    entry["adopted"] = first_seen
    entry["seen"] = seen
    return entry


def plan(repo_abspath, harness_files, existing_registry):
    """Pure planning, no filesystem writes: compute the new registry for one repo from its
    harness files and the previous registry (`[]` on a first run). Kept pure so id stability and
    change detection are testable with no tmp_path and no disk at all, and so `apply()` is a
    thin, boring wrapper around this and the skeleton writes.

    Returns `(entries, counts)`, `entries` sorted by id and ready to write verbatim, `counts`
    with `harness_files`, `adopted`, `promoted`, `gone`, `changed`.
    """
    today = _today()
    by_key = {(e["file"], e["heading"]): e for e in existing_registry}
    seen_keys = set()
    changed = 0
    entries = []

    # Ids are never reused, including for a unit that is `gone`: as long as the registry keeps a
    # row for a rule at all, that row's id has to keep meaning the same rule unit.
    used_numbers = [int(e["id"][len(ID_PREFIX):]) for e in existing_registry
                    if str(e.get("id", "")).startswith(ID_PREFIX)
                    and e["id"][len(ID_PREFIX):].isdigit()]
    next_number = (max(used_numbers) + 1) if used_numbers else 1

    for harness_path in harness_files:
        rel = os.path.relpath(harness_path, repo_abspath)
        with open(harness_path, encoding="utf-8") as fh:
            text = fh.read()
        for heading, start, end, section_text in find_rule_units(
                text, os.path.basename(harness_path)):
            key = (rel, heading)
            seen_keys.add(key)
            sha = hashlib.sha256(section_text.encode("utf-8")).hexdigest()
            rules = _promoted_rules(section_text)
            status = STATUS_PROMOTED if rules else STATUS_ADOPTED

            prior = by_key.get(key)
            if prior is None:
                entry_id = "{}{:04d}".format(ID_PREFIX, next_number)
                next_number += 1
                first_seen = today
            else:
                if prior.get("sha256") != sha:
                    changed += 1
                entry_id = prior["id"]
                first_seen = prior.get("adopted", today)

            entries.append(_build_entry(entry_id, rel, heading, start, end, sha, status,
                                        rules, first_seen, today))

    # Anything previously tracked whose (file, heading) was not seen this run is `gone`, kept,
    # never deleted: a rule that vanished from the file is a fact the registry should keep, the
    # same argument `ledger.remove` already makes for a promoted rule that gets deleted.
    for key, prior in by_key.items():
        if key in seen_keys:
            continue
        rel, heading = key
        entries.append(_build_entry(prior["id"], rel, heading, prior.get("start"),
                                    prior.get("end"), prior.get("sha256"), STATUS_GONE,
                                    [], prior.get("adopted", today), today))

    entries.sort(key=lambda e: e["id"])
    counts = {
        "harness_files": len(harness_files),
        "adopted": sum(1 for e in entries if e["status"] == STATUS_ADOPTED),
        "promoted": sum(1 for e in entries if e["status"] == STATUS_PROMOTED),
        "gone": sum(1 for e in entries if e["status"] == STATUS_GONE),
        "changed": changed,
    }
    return entries, counts


def read_registry(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                # A malformed line is not this command's job to repair, only to not crash on;
                # the next real run rewrites the file whole from what it can still read.
                continue
    return out


def write_registry(path, entries):
    # Rewritten whole, sorted by id, every run: the file is meant to be diffed in git, and a
    # rewrite-whole is what keeps that diff showing only what actually changed.
    lines = [json.dumps(e, ensure_ascii=False) for e in entries]
    payload = "\n".join(lines) + ("\n" if lines else "")
    write_text(path, payload, "the rules registry")


def discover_repo_harnesses(root=".", roots=None, laptop=False):
    """Every repo `audit.compute()` would find, grouped by the repo that owns each harness
    file, in the SAME order `find_harness_files` returns them (root before its subdirectories,
    subdirectories sorted): ids depend on that order being stable run to run, and re-sorting it
    here by full path would put a dotfile directory like `.claude/rules` ahead of `CLAUDE.md`,
    which is not the order a human adopting their own repo would expect.
    """
    discovery_roots = audit_mod._resolve_roots(root, roots, laptop)
    seen = set()
    by_repo = {}
    for r in discovery_roots:
        for path in audit_mod.find_harness_files(r):
            absf = os.path.abspath(path)
            if absf in seen:
                continue
            seen.add(absf)
            repo_abspath = audit_mod._find_repo(absf, r)
            by_repo.setdefault(repo_abspath, []).append(absf)
    return by_repo


def _write_skeleton(repo_abspath):
    """Create what the loop needs to exist, and never touch what is already there. Overwriting
    an existing ledger or evals dir would be `adopt` destroying evidence on the way to recording
    some, the one thing every write door in this package already refuses to do.
    """
    created = {"ledger": False, "evals_readme": False}
    ledger_path = os.path.join(repo_abspath, ".trimwrit", "ledger.jsonl")
    if not os.path.exists(ledger_path):
        # Zero bytes on purpose, a touch rather than an init: `ledger.rows` already reads a
        # MISSING file as no corrections, and it reads an EMPTY one the same way (the `for line
        # in fh` loop simply yields nothing), so a bare touch is enough for the whole ledger
        # module to treat this repo as having zero corrections instead of erroring on one it
        # cannot parse.
        write_text(ledger_path, "", "the ledger skeleton")
        created["ledger"] = True
    evals_dir = os.path.join(repo_abspath, "evals")
    if not os.path.isdir(evals_dir):
        write_text(os.path.join(evals_dir, "README.md"), EVALS_README,
                  "the evals skeleton README")
        created["evals_readme"] = True
    return created


def _public_repo_report(r):
    return {k: v for k, v in r.items() if k != "repo_abspath"}


def compute(root=".", roots=None, laptop=False, dry_run=False):
    """Discover, plan, and (unless `dry_run`) apply, for every repo the walk finds. Returns
    `{"repos": [...], "summary": {...}}`, the same dict backing both the report and `--json` so
    the two can never disagree, the same reason `audit.compute` is shaped this way.
    """
    by_repo = discover_repo_harnesses(root, roots, laptop)
    repo_reports = []
    totals = {"harness_files": 0, "adopted": 0, "promoted": 0, "gone": 0, "changed": 0}

    for repo_abspath in sorted(by_repo, key=audit_mod._display_path):
        harness_files = by_repo[repo_abspath]
        rules_path = os.path.join(repo_abspath, ".trimwrit", "rules.jsonl")
        existing_registry = read_registry(rules_path)
        new_registry, counts = plan(repo_abspath, harness_files, existing_registry)

        skeleton = {"ledger": False, "evals_readme": False}
        if not dry_run:
            skeleton = _write_skeleton(repo_abspath)
            write_registry(rules_path, new_registry)

        for k in totals:
            totals[k] += counts[k]

        repo_reports.append({
            "repo": audit_mod._display_path(repo_abspath),
            "repo_abspath": repo_abspath,
            "harness_files": counts["harness_files"],
            "units": len(new_registry),
            "adopted": counts["adopted"],
            "promoted": counts["promoted"],
            "gone": counts["gone"],
            "changed": counts["changed"],
            "registry": new_registry,
            "skeleton": skeleton,
        })

    summary = {
        "repo_count": len(repo_reports),
        "harness_files": totals["harness_files"],
        "units": sum(r["units"] for r in repo_reports),
        "adopted": totals["adopted"],
        "promoted": totals["promoted"],
        "gone": totals["gone"],
        "changed": totals["changed"],
    }
    return {
        "repos": [_public_repo_report(r) for r in repo_reports],
        "summary": summary,
    }


def render_report(result):
    out = []
    for r in result["repos"]:
        out.append("{}  {} harness file(s)  {} adopted, {} promoted, {} gone, {} changed "
                   "since last run".format(r["repo"], r["harness_files"], r["adopted"],
                                            r["promoted"], r["gone"], r["changed"]))
    s = result["summary"]
    out.append("")
    out.append("{} repo(s), {} harness file(s), {} rule unit(s) tracked ({} adopted, "
               "{} promoted, {} gone), {} changed since last run.".format(
                   s["repo_count"], s["harness_files"], s["units"], s["adopted"],
                   s["promoted"], s["gone"], s["changed"]))
    return "\n".join(out)
