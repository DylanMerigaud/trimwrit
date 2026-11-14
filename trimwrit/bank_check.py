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
