"""The project's waiver list and the strict check over every bank of a tree.

A project migrating its banks to the standard one rule at a time keeps the rules each bank does
not meet yet in `.trimwrit/bank-waivers.json` at its root:

  {"since": "2026-11-14", "why": "...",
   "banks": {"career/x-evals": ["no-holdout", ...]},     rules waived, per bank
   "custom_runners": {"career/y-evals": "why"},           banks keeping their own runner
   "hand_look": {"career/z-evals": "why"},                banks a person reads by hand
   "exclude": ["career/drafts/a"]}                        directories that are not banks

A bank path is relative to the directory holding `.trimwrit/`, with '/' separators. `bank.main`
reads the list (`project_waivers`): a refusal whose rule the list names for that bank is printed
as waived and never stops the run. `check_strict` reads it to report each bank as standard,
waived or failing, and the rules a bank no longer triggers (the list only shrinks).

Standard library only, Python 3.9 syntax.
"""
import json
import os

WAIVERS_DIR = ".trimwrit"
WAIVERS_FILE = "bank-waivers.json"
WAIVERS_KEYS = ("since", "why", "banks", "custom_runners", "hand_look", "exclude")


def _bank():
    from . import bank  # noqa: PLC0415  (lazy: bank.py imports this module at its end)
    return bank


def rel_of(path, root):
    """`path` relative to `root`, '/' separated (the key of a bank in the waiver list)."""
    rel = os.path.relpath(os.path.realpath(str(path)), os.path.realpath(str(root)))
    return rel.replace(os.sep, "/")


def find_waivers(bank_dir):
    """(path of the waiver list, the directory holding .trimwrit/) nearest `bank_dir`, walking
    up and stopping at the filesystem root or at a directory holding .git; (None, None) when
    there is none."""
    d = os.path.realpath(str(bank_dir))
    while True:
        path = os.path.join(d, WAIVERS_DIR, WAIVERS_FILE)
        if os.path.isfile(path):
            return path, d
        parent = os.path.dirname(d)
        if os.path.exists(os.path.join(d, ".git")) or parent == d:
            return None, None
        d = parent


def load_waivers(path):
    """The waiver list at `path`, every key present (empty when absent). BankError naming the
    problem when it does not read or is not shaped as the module docstring says."""
    b = _bank()
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        raise b.BankError("{}: the waiver list does not read ({})".format(path, exc))
    problems = waiver_problems(doc)
    if problems:
        raise b.BankError("{}: {}".format(path, "; ".join(problems)))
    out = {"since": doc.get("since"), "why": doc.get("why")}
    for key in ("banks", "custom_runners", "hand_look"):
        out[key] = dict(doc.get(key) or {})
    out["exclude"] = list(doc.get("exclude") or [])
    return out


def waiver_problems(doc):
    """[str] of what keeps `doc` from being a waiver list."""
    b = _bank()
    if not isinstance(doc, dict):
        return ["not a JSON object"]
    out = ["unknown key {!r}".format(k) for k in sorted(doc) if k not in WAIVERS_KEYS]
    banks = doc.get("banks", {})
    if not isinstance(banks, dict):
        out.append("`banks` is not {bank: [rules]}")
    else:
        for rel, rules in sorted(banks.items()):
            if not isinstance(rules, list) or not all(isinstance(r, str) for r in rules):
                out.append("`banks[{}]` is not a list of rule ids".format(rel))
                continue
            for r in rules:
                if r not in b.RULES:
                    out.append("`banks[{}]` names {!r}, not a rule of the standard".format(rel, r))
    for key in ("custom_runners", "hand_look"):
        v = doc.get(key, {})
        if not isinstance(v, dict) or not all(isinstance(x, str) for x in v.values()):
            out.append("`{}` is not {{bank: why}}".format(key))
    ex = doc.get("exclude", [])
    if not isinstance(ex, list) or not all(isinstance(x, str) for x in ex):
        out.append("`exclude` is not a list of directories")
    return out


def project_waivers(bank_dir):
    """(rel, rules waived for the bank at `bank_dir`) from the nearest waiver list: the rules of
    `banks[rel]`, plus bespoke-runner when `custom_runners` names the bank. (None, set()) when
    no list is found."""
    path, root = find_waivers(bank_dir)
    if path is None:
        return None, set()
    doc = load_waivers(path)
    rel = rel_of(bank_dir, root)
    rules = set(doc["banks"].get(rel, []))
    if rel in doc["custom_runners"]:
        rules.add("bespoke-runner")
    return rel, rules


# ------------------------------------------------------------------ the strict check

MARKERS = ("RELEASE.json", "CORPUS.jsonl", "CODEBOOK.md")
BANK_SUFFIX = "-evals"
SKIP_DIRS = ("RUNS", "__pycache__")
SELF_CHECK_TIMEOUT = 120
# rules only a release run shows: never read as "no longer triggered"
RUN_ONLY_RULES = ("cost-not-recorded", "no-repeat")
# rules the static lint may miss (the grader mutation, constants assigned from an expression, a
# stray model id seen at run time): read as "no longer triggered" only after --self-check ran
SELF_CHECK_RULES = ("grader-cannot-fail", "model-not-pinned")
SELF_CHECK_ENV = "TRIMWRIT_BANK_SELF_CHECK"  # the same as bank_run.SELF_CHECK_ENV


def empty_waivers():
    return {"since": None, "why": None, "banks": {}, "custom_runners": {}, "hand_look": {},
            "exclude": []}


def discover(root, rel_dir=".", exclude=()):
    """(banks, excluded): the bank paths under root/rel_dir, relative to `root` and sorted, and
    the `exclude` directories met. A bank is a directory named *-evals, or one holding
    RELEASE.json, CORPUS.jsonl or CODEBOOK.md; a directory inside a bank counts only when it
    holds one of those files itself. RUNS/, __pycache__ and dot directories are skipped, and an
    excluded directory with everything under it."""
    root = os.path.abspath(str(root))
    base = os.path.join(root, rel_dir)
    skip = {e.strip("/") for e in exclude}
    banks, excluded = [], []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        rel = rel_of(dirpath, root)
        if rel in skip:
            excluded.append(rel)
            dirnames[:] = []
            continue
        inside = any(rel.startswith(bk + "/") for bk in banks)
        held = any(m in filenames for m in MARKERS)
        named = os.path.basename(dirpath).endswith(BANK_SUFFIX)
        if held or (named and not inside):
            banks.append(rel)
    return sorted(banks), sorted(excluded)


def _refusal(rule, bank_dir, what, fix):
    return _bank().Refusal(rule, bank_dir, what, fix).as_dict()


def run_self_check(bank_dir, cwd=None, timeout=SELF_CHECK_TIMEOUT):
    """[as_dict] of the refusals `python3 <bank>/run.py --self-check` prints on stderr (one JSON
    line each). A run that times out, or exits neither 0 nor 65 with refusals, is a
    bespoke-runner refusal saying so. The subprocess runs with TRIMWRIT_BANK_SELF_CHECK=1:
    bank.main then takes the self check path whatever argv its guard passes, and note_usage
    raises. A model call made at module level of run.py (at import, before bank.main) stays
    unguarded: the lint cannot see it and the environment cannot stop it."""
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415
    run_file = os.path.join(str(bank_dir), "run.py")
    try:
        p = subprocess.run([sys.executable, run_file, "--self-check"], cwd=cwd or str(bank_dir),
                           capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, **{SELF_CHECK_ENV: "1"}))
    except subprocess.TimeoutExpired:
        return [_refusal("bespoke-runner", str(bank_dir),
                         "run.py --self-check did not finish in {} s".format(timeout),
                         "make python3 {} --self-check read the bank only: no model call, no "
                         "network".format(run_file))]
    out = []
    for line in p.stderr.splitlines():
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict) and doc.get("refused") == "bank-standard":
            out.append(doc)
    if p.returncode == 0 or (p.returncode == 65 and out):
        return out
    last = (p.stderr.strip().splitlines() or ["no output"])[-1]
    return out + [_refusal("bespoke-runner", str(bank_dir),
                           "run.py --self-check exited {} ({})".format(p.returncode, last[:200]),
                           "make python3 {} --self-check exit 0 or 65 through bank.main"
                           .format(run_file))]


def _check(root, found, w, self_check, run=run_self_check):
    b = _bank()
    out = {}
    for rel in found:
        d = os.path.join(root, rel)
        refs = [r.as_dict() for r in b.lint(d)]
        ran = False
        if (self_check and os.path.isfile(os.path.join(d, "run.py"))
                and not any(r["rule"] == "bespoke-runner" for r in refs)):
            # a guard that is exactly sys.exit(bank.main(__file__)), run under
            # TRIMWRIT_BANK_SELF_CHECK=1: the library's self check, no model call; listed banks
            # too, so what bank.main still refuses at resolved values is never read as gone
            seen = {(r["rule"], r["what"]) for r in refs}
            for r in run(d, cwd=root):
                if (r["rule"], r["what"]) not in seen:
                    refs.append(r)
                    seen.add((r["rule"], r["what"]))
            ran = True
        if rel in w["custom_runners"]:
            refs = [r for r in refs if r["rule"] != "bespoke-runner"]
        listed = set(w["banks"].get(rel, []))
        triggered = {r["rule"] for r in refs}
        waived = sorted(triggered & listed)
        failing = [r for r in refs if r["rule"] not in listed]
        seeable = [r for r in listed if r not in RUN_ONLY_RULES
                   and (ran or r not in SELF_CHECK_RULES)]
        status = "failing" if failing else ("waived" if waived else "standard")
        out[rel] = {"status": status, "refusals": refs, "waived": waived,
                    "may_shrink": sorted(r for r in seeable if r not in triggered)}
    return out


def check_strict(root, rel_dir="career", waivers_path=None, self_check=True, banks=None,
                 run=run_self_check):
    """The strict check of every bank under root/rel_dir (`discover`), or of the bank paths
    `banks` (relative to root) when given, against the waiver list `waivers_path` (default
    root/.trimwrit/bank-waivers.json when it exists). Each bank: bank.lint; a bank whose static
    lint shows no bespoke-runner (its guard is exactly sys.exit(bank.main(__file__))) also runs
    `run.py --self-check` (run_self_check) when `self_check`. A custom runner's bespoke-runner refusals are dropped; a
    refusal of a rule the list waives for that bank is waived, any other is failing. Returns
    {"banks": {rel: {"status": "standard"|"waived"|"failing"|"excluded", "refusals":
    [as_dict], "waived": [rules], "may_shrink": [rules listed but no longer triggered]}},
    "counts": {...}, "waiver_length": rules in the list, "waived_banks": banks in the list,
    "stale_waivers": [listed banks under rel_dir whose directory is gone], "waivers": path or
    None}."""
    root = os.path.abspath(str(root))
    if waivers_path is None:
        default = os.path.join(root, WAIVERS_DIR, WAIVERS_FILE)
        waivers_path = default if os.path.isfile(default) else None
    w = load_waivers(waivers_path) if waivers_path else empty_waivers()
    if banks is None:
        found, excluded = discover(root, rel_dir, w["exclude"])
    else:
        found, excluded = sorted({rel_of(os.path.join(root, b), root) for b in banks}), []
    result = _check(root, found, w, self_check, run=run)
    for rel in excluded:
        result[rel] = {"status": "excluded", "refusals": [], "waived": [], "may_shrink": []}
    stale = []
    if banks is None:
        prefix = rel_of(os.path.join(root, rel_dir), root)
        stale = sorted(rel for rel in w["banks"] if rel not in result
                       and not os.path.isdir(os.path.join(root, rel)) and (
                           prefix == "." or rel == prefix or rel.startswith(prefix + "/")))
    st = [e["status"] for e in result.values()]
    counts = {"banks": len(found), "standard": st.count("standard"), "waived": st.count("waived"),
              "failing": st.count("failing"), "excluded": st.count("excluded"),
              "waived_rules": sum(len(e["waived"]) for e in result.values()),
              "custom_runners": sum(1 for rel in found if rel in w["custom_runners"]),
              "hand_look": sum(1 for rel in found if rel in w["hand_look"]),
              "may_shrink": sum(len(e["may_shrink"]) for e in result.values()) + len(stale)}
    return {"banks": result, "counts": counts,
            "waiver_length": sum(len(v) for v in w["banks"].values()),
            "waived_banks": len(w["banks"]), "stale_waivers": stale, "waivers": waivers_path}


def waivers_from(result, since, why):
    """A waiver list holding, per bank of a `check_strict` result, the rules it triggers."""
    banks = {rel: sorted({r["rule"] for r in e["refusals"]})
             for rel, e in result["banks"].items() if e["refusals"]}
    return {"since": since, "why": why, "banks": banks, "custom_runners": {}, "hand_look": {},
            "exclude": []}


def shrink(doc, result):
    """(doc without the rules `result` says a bank no longer triggers, and without a bank left
    with none or no longer found, [(rel, rule)] removed)."""
    doc = json.loads(json.dumps(doc))
    banks = doc.setdefault("banks", {})
    removed = []
    for rel, e in sorted(result["banks"].items()):
        for rule in e["may_shrink"]:
            if rule in banks.get(rel, []):
                banks[rel].remove(rule)
                removed.append((rel, rule))
        if rel in banks and not banks[rel]:
            del banks[rel]
    for rel in result.get("stale_waivers", []):
        for rule in banks.pop(rel, []):
            removed.append((rel, rule))
    return doc, removed


def write_waivers(path, doc):
    """Write the waiver list: sorted keys, indent 1, ASCII."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="ascii") as fh:
        fh.write(json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=True) + "\n")
    os.replace(tmp, path)
