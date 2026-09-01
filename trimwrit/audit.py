"""One table for every harness on the machine.

On 2026-09-01 an inventory of one laptop found 28 repositories with a CLAUDE.md, one global
`~/.claude/CLAUDE.md`, two `~/.claude/rules/*.md`, twelve skills, and eval cases in exactly TWO
places. Every other harness had zero cases, and nobody had decided that: it was simply never
visible. `prune` and `stats` both answer questions about ONE rule file at a time; this module
answers the question neither of them can, what is the state of every harness on the machine, so
a harness with no case, or a case not run in a month, is a row someone can read rather than a
fact nobody knows.

Everything here is read only. No git subprocess per file (a `.git` existence check is enough to
attribute a repo), no reading of any file beyond a harness file, the ledger, and the two results
files `trimwrit run` already writes.
"""
import json
import os
import time

from . import cases as cases_mod
from . import integrate as integrate_mod
from . import ledger as ledger_mod

BAR = "-" * 100
DEFAULT_STALE_DAYS = 30
MAX_DEPTH = 6

# Pruned from every walk, and each one for its own reason, not a generic "junk" catch-all.
EXCLUDE_DIRS = {
    "node_modules",   # vendored JS, never a harness
    ".git",           # internal plumbing, not content
    "worktrees",      # a worktree checkout of the SAME repo, would count one harness N times
    "__pycache__",    # compiled bytecode
    "dist",           # build output
    "build",          # build output
    ".venv",          # a virtualenv, not a harness
    "venv",
    "_archive",       # retired on purpose, resurrecting it as live would be the wrong read
    "plugins",        # a Claude Code plugin cache: OTHER people's harnesses, cached, not ours
}


def _skip_dir(name):
    # A `*.worktrees` directory (this repo's own convention, see the growth-cockpit CLAUDE.md)
    # is a sibling checkout, not a name match, so it needs its own test past the exact set above.
    return name in EXCLUDE_DIRS or name.endswith(".worktrees")


def _home():
    # A function, not a module constant: a constant frozen at import time would not see a test's
    # `monkeypatch.setenv("HOME", ...)`, and `--laptop` exists specifically to read that value.
    return os.path.expanduser("~")


def _walk_prefixed(root, max_depth=MAX_DEPTH):
    """`os.walk` under `root`, pruning `EXCLUDE_DIRS` (and any `*.worktrees` dir) before
    descending, and capped at `max_depth` levels below `root`. Yields `(dirpath, filenames)`.
    """
    root_abs = os.path.abspath(root)
    root_depth = root_abs.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root_abs):
        dirnames[:] = sorted(d for d in dirnames if not _skip_dir(d))
        depth = dirpath.rstrip(os.sep).count(os.sep) - root_depth
        if depth >= max_depth:
            dirnames[:] = []
        yield dirpath, filenames


def find_harness_files(root):
    """Every HARNESS FILE under `root`: `CLAUDE.md` at any depth, `*.md` directly inside a
    directory named `rules` whose parent is named `.claude`, and `SKILL.md` inside a directory
    whose parent is `skills` and grandparent is `.claude`.

    `~/.claude` needs no special case here. Its own basename IS `.claude`, so `~/.claude/rules`
    already matches "a `rules` dir whose parent is named `.claude`" the same way a repo's own
    `.claude/rules` does, and the same is true one level down for `skills/<name>/SKILL.md`.
    """
    out = []
    for dirpath, filenames in _walk_prefixed(root):
        if "CLAUDE.md" in filenames:
            out.append(os.path.join(dirpath, "CLAUDE.md"))

        parent = os.path.dirname(dirpath)
        if os.path.basename(dirpath) == "rules" and os.path.basename(parent) == ".claude":
            out.extend(os.path.join(dirpath, f) for f in sorted(filenames) if f.endswith(".md"))

        grandparent = os.path.dirname(parent)
        if ("SKILL.md" in filenames and os.path.basename(parent) == "skills"
                and os.path.basename(grandparent) == ".claude"):
            out.append(os.path.join(dirpath, "SKILL.md"))
    return out


def _find_repo(path, root):
    """Nearest ancestor of `path` holding a `.git` entry, file or directory (a worktree's `.git`
    is a FILE pointing at the real repo, and `os.path.exists` covers both), else `root` itself.
    The search never climbs past `root`: a harness with no `.git` between it and the root it was
    discovered under is not attributable to some narrower repo above that root.
    """
    root_abs = os.path.abspath(root)
    cur = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        if cur == root_abs:
            return root_abs
        parent = os.path.dirname(cur)
        if parent == cur:
            return root_abs
        cur = parent


def _display_path(path):
    """Relative to `~` when under it (so a laptop-wide report reads like a human wrote it), else
    the absolute path."""
    if path is None:
        return None
    home = os.path.abspath(_home())
    absf = os.path.abspath(path)
    if absf == home:
        return "~"
    prefix = home + os.sep
    if absf.startswith(prefix):
        return os.path.join("~", absf[len(prefix):])
    return absf


def _harness_record(path, repo_abspath):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    lines = text.splitlines()
    sections = sum(1 for ln in lines if ln.startswith("## "))
    # The exact marker `integrate.py` writes, read back with its own parser rather than a second
    # regex here: two readers of the same marker drifting apart is exactly the kind of thing
    # this command exists to notice, and it would be absurd for it to cause that itself.
    rules = len(integrate_mod.rule_ids(text))
    return {
        "path": _display_path(path),
        "abspath": os.path.abspath(path),
        "repo": _display_path(repo_abspath),
        "repo_abspath": repo_abspath,
        "lines": len(lines),
        "sections": sections,
        "rules": rules,
        "size_kb": round(os.path.getsize(path) / 1024.0, 1),
    }


def _find_evals_dir(repo):
    """The repo's own `evals/`, never a nested repo's.

    Found on the first laptop run, 2026-09-01: `~/Code/newsnakeproject` has no `.git`, so it
    fell back to `~/Code` as its repo, and this walk then descended into `~/Code/growth-cockpit`
    and reported that umbrella directory as holding 38 cases that belong to a repo of its own.
    A directory that carries its own `.git` is its own row in this table, so the walk stops at
    its door.
    """
    default = os.path.join(repo, "evals")
    if os.path.isdir(default):
        return default
    repo_abs = os.path.abspath(repo)
    for dirpath, _filenames in _walk_prefixed(repo):
        if os.path.basename(dirpath) == "evals" and _find_repo(dirpath, repo_abs) == repo_abs:
            return dirpath
    return None


def _count_cases(evals_dir):
    """`cases.discover` when it works: it is the same reader `trimwrit run` and `trimwrit prune`
    use, so a case this command counts is a case those commands would actually run. A directory
    can hold a `prompt.md` that is not a complete case yet (no `graders/`, mid front matter
    typo), and `discover` raises on the first one of those rather than skip it, which is right
    for a runner and wrong for an inventory: falling back to counting directories by presence
    of `prompt.md` alone means one malformed case never hides every other case in the same repo.
    """
    try:
        return len(cases_mod.discover(evals_dir))
    except Exception:
        pass
    count = 0
    for dirpath, _dirnames, filenames in os.walk(evals_dir):
        if "prompt.md" in filenames:
            count += 1
    return count


def _numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _read_history(evals_dir):
    """`(newest ts, mean with-score of the newest run)` from `results/history.jsonl`, or
    `(None, None)`. One row per case per run: rows sharing the newest `ts` are one run, and its
    mean `with` score is what a human wants without opening the file.
    """
    path = os.path.join(evals_dir, "results", "history.jsonl")
    if not os.path.exists(path):
        return None, None
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict) and "ts" in obj:
                    rows.append(obj)
    except OSError:
        return None, None
    if not rows:
        return None, None
    try:
        newest_ts = max(r["ts"] for r in rows)
    except TypeError:
        # Mixed types in the `ts` column (some numeric, some string) cannot be compared. A
        # malformed history is not this command's job to repair, only to not crash on.
        return None, None
    withs = [r["with"] for r in rows if r.get("ts") == newest_ts and _numeric(r.get("with"))]
    mean_with = round(sum(withs) / len(withs), 2) if withs else None
    return newest_ts, mean_with


def _ledger_stats(repo):
    path = ledger_mod.path_for(repo)
    if not os.path.exists(path):
        return None
    return {
        "corrections": len(ledger_mod.corrections(root=repo)),
        "pending_promotions": len(ledger_mod.pending(root=repo)),
    }


def _repo_record(repo_abspath):
    evals_dir = _find_evals_dir(repo_abspath)
    cases = _count_cases(evals_dir) if evals_dir else 0
    ledger_stats = _ledger_stats(repo_abspath)

    last_run, last_with = (None, None)
    if evals_dir:
        last_run, last_with = _read_history(evals_dir)
        if last_run is None:
            latest = os.path.join(evals_dir, "results", "latest.json")
            if os.path.exists(latest):
                last_run = os.path.getmtime(latest)

    return {
        "repo": _display_path(repo_abspath),
        "repo_abspath": repo_abspath,
        "evals_dir": _display_path(evals_dir) if evals_dir else None,
        "cases": cases,
        "corrections": ledger_stats["corrections"] if ledger_stats else 0,
        "pending_promotions": ledger_stats["pending_promotions"] if ledger_stats else 0,
        "last_run": last_run,
        "last_with": last_with,
    }


def _age_days(ts):
    return (time.time() - ts) / 86400.0


def _is_stale(repo_rec, stale_days):
    """A repo with zero cases is already named in its own bucket; folding it into stale too
    would print the same repo twice for two different reasons. A repo with cases that has NEVER
    run is worse than one that ran a while ago, so it counts as stale regardless of the
    threshold: there is no `--stale` value under which "never measured" should read as fresh.
    """
    if repo_rec["cases"] <= 0:
        return False
    if repo_rec["last_run"] is None:
        return True
    return _age_days(repo_rec["last_run"]) > stale_days


def _resolve_roots(root, roots, laptop):
    """Explicit `--roots` wins, `--laptop` is next, the plain `--root` is the default. A root
    that does not exist is skipped rather than raised on: `os.walk` of a missing path yields
    nothing anyway, and `--laptop` is exactly the case where one of the two is often absent (a
    machine with no `~/Code`, a fresh account with no `~/.claude` yet).
    """
    if roots:
        candidates = list(roots)
    elif laptop:
        home = _home()
        candidates = [os.path.join(home, ".claude"), os.path.join(home, "Code")]
    else:
        candidates = [root]
    out = []
    for c in candidates:
        c = os.path.abspath(os.path.expanduser(c))
        if os.path.isdir(c):
            out.append(c)
    return out


def _public_harness(h):
    return {"repo": h["repo"], "path": h["path"], "lines": h["lines"], "sections": h["sections"],
            "rules": h["rules"], "size_kb": h["size_kb"]}


def _public_repo(rr):
    return {"repo": rr["repo"], "evals_dir": rr["evals_dir"], "cases": rr["cases"],
            "corrections": rr["corrections"], "pending_promotions": rr["pending_promotions"],
            "last_run": rr["last_run"], "last_with": rr["last_with"]}


def compute(root=".", roots=None, laptop=False, stale_days=DEFAULT_STALE_DAYS):
    """Discover, aggregate, and return the whole payload: `{"harnesses": [...], "repos": [...],
    "summary": {...}}`. The same dict backs both the table and `--json`, so the two can never
    disagree about a number, the same reason `stats.compute` is structured this way.
    """
    discovery_roots = _resolve_roots(root, roots, laptop)

    seen = set()
    harnesses = []
    for r in discovery_roots:
        for path in find_harness_files(r):
            absf = os.path.abspath(path)
            if absf in seen:
                # Two overlapping roots (`--roots ~/Code ~/Code/growth-cockpit`) would otherwise
                # double count the same file, and a count is the entire point of this command.
                continue
            seen.add(absf)
            repo_abspath = _find_repo(absf, r)
            harnesses.append(_harness_record(absf, repo_abspath))

    repo_abspaths = sorted({h["repo_abspath"] for h in harnesses}, key=_display_path)
    repo_records = {p: _repo_record(p) for p in repo_abspaths}

    harnesses_sorted = sorted(harnesses, key=lambda h: (h["repo"], h["path"]))

    zero_cases = sorted(rr["repo"] for rr in repo_records.values() if rr["cases"] == 0)
    stale = sorted(rr["repo"] for rr in repo_records.values() if _is_stale(rr, stale_days))
    unmeasured = sorted({h["path"] for h in harnesses
                         if h["rules"] > 0
                         and repo_records[h["repo_abspath"]]["evals_dir"] is None})

    summary = {
        "harness_count": len(harnesses),
        "repo_count": len(repo_records),
        "stale_days": stale_days,
        "zero_cases": zero_cases,
        "stale": stale,
        "unmeasured_rules": unmeasured,
    }
    return {
        "harnesses": [_public_harness(h) for h in harnesses_sorted],
        "repos": [_public_repo(rr) for rr in sorted(repo_records.values(),
                                                     key=lambda r: r["repo"])],
        "summary": summary,
    }


def _fmt_date(ts):
    if ts is None:
        return "-"
    try:
        return time.strftime("%Y-%m-%d", time.localtime(ts))
    except (TypeError, ValueError, OSError):
        return "-"


def render_table(result):
    """The human readable report: one row per harness file, then the summary block the command
    exists for. Kept here rather than in cli.py so the two output modes, this and `--json`, are
    both thin readers of the exact same `compute()` payload.
    """
    repo_index = {r["repo"]: r for r in result["repos"]}
    out = ["{:<26} {:<44} {:>6} {:>8} {:>5} {:>5} {:>10} {:>6}".format(
        "repo", "harness", "lines", "sections", "rules", "cases", "last run", "with"), BAR]
    for h in result["harnesses"]:
        rr = repo_index.get(h["repo"], {})
        last_with = rr.get("last_with")
        out.append("{:<26} {:<44} {:>6} {:>8} {:>5} {:>5} {:>10} {:>6}".format(
            h["repo"], h["path"], h["lines"], h["sections"], h["rules"], rr.get("cases", 0),
            _fmt_date(rr.get("last_run")), "{:.2f}".format(last_with) if last_with is not None
            else "-"))

    s = result["summary"]
    out.append("")
    out.append("{} harness file(s) in {} repo(s)".format(s["harness_count"], s["repo_count"]))

    out.append("")
    out.append("{} repo(s) with zero cases, the number this command exists for:".format(
        len(s["zero_cases"])))
    for r in s["zero_cases"]:
        out.append("  {}".format(r))
    if not s["zero_cases"]:
        out.append("  none.")

    out.append("")
    out.append("{} repo(s) stale (last run older than {} day(s)):".format(
        len(s["stale"]), s["stale_days"]))
    for r in s["stale"]:
        out.append("  {}".format(r))
    if not s["stale"]:
        out.append("  none.")

    out.append("")
    out.append("{} harness(es) with integrated rules but no evals dir, promoted with nothing "
               "to measure them:".format(len(s["unmeasured_rules"])))
    for r in s["unmeasured_rules"]:
        out.append("  {}".format(r))
    if not s["unmeasured_rules"]:
        out.append("  none.")

    return "\n".join(out)
