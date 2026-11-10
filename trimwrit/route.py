"""Reuse, extend or build: where the eval harness of a judgment lives, and which harnesses run.

WHY. A project that builds an eval bank per judgment ends up with seven banks that check the same
thing on seven instances, each built from scratch, because nothing asked first whether a bank
already grades it. Asked on 2026-11-10: "il faudrait un harness pour voir si on recreer un
harness ou reuse un et comment on selection tout les harness qui s'execute... et ajouter ce
mechanisme a la lib". This module answers the first half (`route`: reuse, extend or new for a
key) and hands the second to the project (`layers`, printed by `trimwrit bank explain`).

THE REGISTRY. Everything project specific comes through a registry the project plugs in:
`<root>/.trimwrit/bank.json` holds {"registry": "<path/to/module.py>:<callable>"} (the path
relative to the root); `load_registry(root)` imports that module by its path and calls the
callable, with the root when it takes an argument. The result is any object with these methods
(a duck type, nothing to subclass):

  assignments()                  {key: bank}: the keys that already have a bank
  banks()                        {bank: judges line}: every bank a key may land in (a bank is a
                                 directory relative to the root; `codebook_judges` reads a line)
  families()                     {name: {"members": [bank], "home": bank, "why": str}}: banks
                                 that are instances of ONE judgment; a new instance extends the
                                 home (the largest member by CORPUS.jsonl lines when the home is
                                 gone from banks())
  producer_of(key)               a set of script names producing the key's output, or None
  family_of(key, judges, producer)
                                 the family name the key belongs to, or None
  layers(key)                    [{"layer", "name", "bank", "why"}, ...] in the order they run;
                                 a layer may carry "status" (shown when it has no bank:
                                 deterministic, exempt, debt) and "slice": ["types" or
                                 "instances", name] (its status is then that slice's verdict)

and optionally:

  ask(prompt, schema)            the project's own model call for the ambiguous case; returns
                                 {"decision": reuse|extend|new, "bank", "why"}. Without it an
                                 ambiguous route answers {"decision": "ask", ...} (exit 3).
  prompt(key, judges, producer, candidates)
                                 the question ask() receives (default: PROMPT below)
  covered(bank, key)             True when the bank's cases already hold this instance: a
                                 family or model "extend" then reads as "reuse"
  keys()                         every key whose producer is known (default: assignments())
  case_count(bank)               (default: the lines of <root>/<bank>/CORPUS.jsonl)
  never_candidates()             banks never offered as a candidate (a universal readback)
  extra_candidates(key, judges, producer)
                                 banks offered beside the token overlap (a model site the
                                 key's producer runs)
  stopwords()                    words added to STOPWORDS
  plumbing()                     script names that carry no judgment, dropped from producers
  root                           an attribute: the directory banks are relative to

THE ORDER (deterministic first; the first step that answers wins):
1. registry: the key is in assignments(): reuse its bank.
2. judges: the normalized judges line equals a bank's line: reuse it.
3. producer: the keys whose producer equals this key's all share ONE bank, and two keys or more
   use that bank: reuse it.
4. family: family_of() names a family with a member in banks(): extend its home, reuse it when
   covered(home, key).
5. candidates: banks sharing CANDIDATE_MIN_SHARED words or more with the judges line, and at
   least CANDIDATE_MIN_OVERLAP of the shorter line (a bank whose line is only its own name is
   read on its codebook's section 0, and is never one without it), plus extra_candidates().
   None: new (how "none").
6. ask: registry.ask on the candidates; a bank named outside them, or no decision, reads as new.
   No ask: decision "ask".

The thresholds are those of the router this generalizes, so a project's own bank of routing
cases keeps its numbers. Standard library only.
"""
import glob
import hashlib
import importlib.util
import inspect
import json
import os
import re
import sys

from .bank import BankError, envelope_problems, read_release, released_slice

CONFIG = os.path.join(".trimwrit", "bank.json")
DECISIONS = ("reuse", "extend", "new")
CANDIDATE_MIN_SHARED = 2
CANDIDATE_MIN_OVERLAP = 0.34
STOPWORDS = frozenset("""
a an the and or of to in on at by for from with into onto as is are be been it its it's this
that these those his her their our your one each every any all no not does do did what which
whether who when how why than then there here own codebook codebooks evals eval bank row rows
daily weekly claude result results
right wrong good bad new old same other another output outputs text texts line lines
""".split())

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {"decision": {"type": "string", "enum": list(DECISIONS)},
                   "bank": {"type": ["string", "null"]},
                   "why": {"type": "string"}},
    "required": ["decision", "bank", "why"]}

PROMPT = """You decide where the eval harness of ONE repeated judgment lives. An eval bank is a
set of real cases with their right answer, graders and a bar; each bank grades ONE judgment.

THE JUDGMENT TO HARNESS
key: {key}
what it judges: {judges}
its producer scripts: {producer}

CANDIDATE BANKS (the only banks you may name)
{candidates}

Answer one of:
- reuse: that bank ALREADY grades this judgment (the same decision, by the same producer, on
  this kind of input).
- extend: that bank grades the SAME judgment made by the same producer code on ANOTHER instance,
  so this key joins it as a slice with its own real cases.
- new: anything else. Another producer, another reader, another decision, another kind of input
  is another judgment, even on the same topic. When in doubt, answer new: merging two different
  judgments into one bank is the costly error, a duplicate bank is cheap.
A shared word, a shared topic or a shared channel is not the same judgment.
Name the bank exactly as written above for reuse or extend; null for new. `why`: one sentence.
"""


class RouteError(BankError):
    """A registry or an axes module that cannot be loaded as .trimwrit/bank.json names it."""


# ------------------------------------------------------------------ the plug

def config(root):
    """The dict of <root>/.trimwrit/bank.json. RouteError naming the file when it is absent or
    not a JSON object."""
    path = os.path.join(root, CONFIG)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except OSError:
        raise RouteError("{} not found: write it with {{\"registry\": "
                         "\"<path/to/module.py>:<callable>\"}} (and \"axes\" for facets)"
                         .format(path)) from None
    except ValueError as exc:
        raise RouteError("{}: not JSON ({})".format(path, exc)) from None
    if not isinstance(doc, dict):
        raise RouteError("{}: not a JSON object".format(path))
    return doc


def load_callable(root, key):
    """The callable `.trimwrit/bank.json`[key] names ("<module.py>:<callable>", the path relative
    to `root`), its module imported by path."""
    doc = config(root)
    spec = doc.get(key)
    if not isinstance(spec, str) or ":" not in spec:
        raise RouteError("{}: no `{}` of the form \"<path/to/module.py>:<callable>\"".format(
            os.path.join(root, CONFIG), key))
    rel, _, name = spec.rpartition(":")
    path = rel if os.path.isabs(rel) else os.path.join(root, rel)
    if not os.path.isfile(path):
        raise RouteError("{}: `{}` names {}, which does not exist".format(
            os.path.join(root, CONFIG), key, rel))
    mod_name = "trimwrit_plug_" + hashlib.sha256(
        os.path.abspath(path).encode("utf-8")).hexdigest()[:16]
    module = sys.modules.get(mod_name)
    if module is None:
        spec_ = importlib.util.spec_from_file_location(mod_name, path)
        module = importlib.util.module_from_spec(spec_)
        sys.modules[mod_name] = module
        try:
            spec_.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(mod_name, None)
            raise
    fn = getattr(module, name, None)
    if not callable(fn):
        raise RouteError("{}: `{}` names {}, which {} has no callable of".format(
            os.path.join(root, CONFIG), key, name, rel))
    return fn


def _call_with_root(fn, root):
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    return fn(root) if params else fn()


def load_registry(root, callable_name=None):
    """The project's Registry: the callable `.trimwrit/bank.json` "registry" names, called with
    `root` when it takes an argument. `callable_name` picks another callable of the same module.
    Sets `.root` on it when it has none."""
    fn = load_callable(root, "registry")
    if callable_name:
        fn = getattr(sys.modules[fn.__module__], callable_name)
    reg = _call_with_root(fn, root)
    if getattr(reg, "root", None) is None:
        try:
            reg.root = root
        except AttributeError:
            pass
    return reg


# ------------------------------------------------------------------ reading banks

def _first_heading(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("#"):
                    return line.lstrip("#").strip()
    except OSError:
        pass
    return ""


def slug_of(bank):
    return os.path.basename(str(bank).rstrip("/"))


def codebook_judges(bank_dir):
    """A bank's judges line: the H1 of its CODEBOOK.md (`# <judges>: the codebook`, README.md
    else) without "the codebook"; the directory's name when it has neither."""
    h1 = _first_heading(os.path.join(bank_dir, "CODEBOOK.md")) or _first_heading(
        os.path.join(bank_dir, "README.md"))
    line = re.sub(r"[:,]?\s*the codebook(\s+of)?\b\.?|:\s*CODEBOOK\b", " ", h1, flags=re.I)
    return " ".join(line.split()).strip(" :,") or slug_of(bank_dir)


def codebook_summary(bank_dir):
    """The first sentence of section 0 of a CODEBOOK.md (its why), else of the paragraph under
    its H1; "" when none. What a bank whose title is only its own name says it grades."""
    try:
        with open(os.path.join(bank_dir, "CODEBOOK.md"), encoding="utf-8",
                  errors="replace") as fh:
            text = fh.read()
    except OSError:
        return ""
    m = re.search(r"^##\s*0\.[^\n]*\n+(.+?)(\n\s*\n|$)", text, re.M | re.S)
    if not m:
        m = re.search(r"\A\s*#[^\n]*\n+(?!#)(.+?)(\n\s*\n|$)", text, re.S)
    if not m:
        return ""
    para = " ".join(m.group(1).split())
    cut = re.search(r"(?<=[a-z0-9)`])\.\s", para)
    return (para[:cut.start() + 1] if cut else para)[:240]


def case_count(bank_dir):
    try:
        with open(os.path.join(bank_dir, "CORPUS.jsonl"), encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


def normalize(text):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def _stem(w):
    """A plural read as its singular ("bounces" is "bounce"), nothing more."""
    return w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w


def tokens(text, stopwords=STOPWORDS):
    return {_stem(w) for w in normalize(text).split() if len(w) >= 3 and w not in stopwords
            and not w.isdigit()}


def _opt(reg, name, default, *args):
    fn = getattr(reg, name, None)
    return fn(*args) if callable(fn) else default


def _root(reg, root=None):
    return root or getattr(reg, "root", None) or "."


def producer_set(producer, plumbing=frozenset()):
    """A producer argument (a site key `file.py:CONST`, a comma list, or an iterable of scripts)
    as a frozenset of basenames, `plumbing` out; None stays None."""
    if producer is None:
        return None
    if isinstance(producer, str):
        items = [p.strip() for p in producer.split(",") if p.strip()]
    else:
        items = list(producer)
    out = set()
    for p in items:
        p = str(p).split(":", 1)[0] if re.match(r"^[^:]+\.(py|sh|mjs):", str(p)) else str(p)
        out.add(os.path.basename(p))
    return frozenset(out - set(plumbing))


# ------------------------------------------------------------------ the route

def _out(decision, bank, how, why, family=None, **extra):
    d = {"decision": decision, "bank": bank, "family": family, "how": how, "why": why}
    d.update(extra)
    return d


def _family_of_bank(families, bank):
    for name, fam in sorted(families.items()):
        if bank in (fam.get("members") or ()):
            return name
    return None


def candidates(reg, judges, root=None):
    """[(bank, shared words, overlap, False)] of the banks whose line shares the judges line's
    words, the closest first (the shape of the router this generalizes)."""
    root = _root(reg, root)
    stop = STOPWORDS | frozenset(_opt(reg, "stopwords", ()))
    never = set(_opt(reg, "never_candidates", ()))
    jt = tokens(judges, stop)
    out = []
    for bank, line in reg.banks().items():
        if bank in never:
            continue
        if line == slug_of(bank):
            line = codebook_summary(os.path.join(root, bank))
            if not line:
                continue
        bt = tokens(line, stop)
        shared = jt & bt
        overlap = len(shared) / min(len(jt), len(bt)) if jt and bt else 0.0
        if len(shared) >= CANDIDATE_MIN_SHARED and overlap >= CANDIDATE_MIN_OVERLAP:
            out.append((bank, sorted(shared), round(overlap, 3), False))
    return sorted(out, key=lambda c: (-c[2], -len(c[1]), c[0]))


def default_prompt(key, judges, producer, cands, reg):
    lines = []
    assigned = reg.assignments()
    for bank, shared, overlap, _flag in cands:
        users = ", ".join(sorted(k for k, b in assigned.items() if b == bank)[:8]) or "none"
        lines.append('- {}: judges "{}"; used by {}; shares {}'.format(
            bank, reg.banks().get(bank, ""), users, ", ".join(shared) or "no word"))
    return PROMPT.format(key=key or "(none)", judges=judges or "(none)",
                         producer=", ".join(sorted(producer)) if producer else "(unknown)",
                         candidates="\n".join(lines))


def route(key, judges, producer=None, registry=None, root=None):
    """{"decision": reuse|extend|new|ask, "bank", "family", "how", "why"} for `key` judging
    `judges` (see the module docstring for the order). `producer` (a comma list, a site key
    `file.py:CONST`, or an iterable) replaces registry.producer_of(key). An ambiguous route
    carries "candidates"; without registry.ask its decision is "ask"."""
    reg = registry
    if reg is None:
        raise RouteError("route() needs a registry: load_registry(root)")
    root = _root(reg, root)
    judges = " ".join((judges or "").split())
    banks = reg.banks()
    families = reg.families() or {}
    assigned = reg.assignments()
    plumbing = frozenset(_opt(reg, "plumbing", ()))
    # 1. registry
    b = assigned.get(key) if key else None
    if b:
        return _out("reuse", b, "registry", "{} is already graded by {} in the registry"
                    .format(key, b), _family_of_bank(families, b))
    if producer is not None:
        prod = producer_set(producer, plumbing)
    else:
        raw = reg.producer_of(key) if key else None
        prod = producer_set(raw, plumbing) if raw is not None else None
    # 2. judges
    nj = normalize(judges)
    if nj:
        for bank, line in sorted(banks.items()):
            if normalize(line) == nj:
                return _out("reuse", bank, "judges", "{} judges exactly this line: {}"
                            .format(bank, line), _family_of_bank(families, bank))
    # 3. producer
    if key and prod:
        keys = _opt(reg, "keys", None) or list(assigned)
        same = []
        for k in keys:
            if k == key:
                continue
            raw = reg.producer_of(k)
            if raw is not None and producer_set(raw, plumbing) == prod:
                same.append(k)
        same = sorted(same)
        shared = {assigned.get(k) for k in same}
        if same and len(shared) == 1 and None not in shared:
            bank = shared.pop()
            if bank in banks and sum(1 for v in assigned.values() if v == bank) >= 2:
                return _out("reuse", bank, "producer",
                            "keys {} run the same producer scripts ({}) and share {}".format(
                                ", ".join(same), ", ".join(sorted(prod)), bank),
                            _family_of_bank(families, bank))
    # 4. family
    fname = reg.family_of(key, judges, prod) if key else None
    fam = families.get(fname) if fname else None
    if fam:
        members = [m for m in fam.get("members") or () if m in banks]
        if members:
            count = getattr(reg, "case_count", None)
            if not callable(count):
                def count(m):
                    return case_count(os.path.join(root, m))
            home = fam.get("home") if fam.get("home") in members else max(
                members, key=lambda m: (count(m), m))
            why = "family {}: {}".format(fname, fam.get("why") or "")
            if _opt(reg, "covered", False, home, key):
                return _out("reuse", home, "family",
                            why + "; its cases already hold this instance", fname)
            return _out("extend", home, "family", why, fname)
    # 5. candidates
    cands = candidates(reg, judges, root)
    have = {c[0] for c in cands}
    never = set(_opt(reg, "never_candidates", ()))
    for extra in _opt(reg, "extra_candidates", (), key, judges, prod) or ():
        if extra in banks and extra not in have and extra not in never:
            cands.append((extra, [], 0.0, False))
            have.add(extra)
    if not cands:
        return _out("new", None, "none", "no bank shares this judgment's words")
    names = [c[0] for c in cands]
    # 6. ask
    ask = getattr(reg, "ask", None)
    if not callable(ask):
        return _out("ask", None, "candidates",
                    "ambiguous: {} share this judgment's words and the registry has no ask()"
                    .format(", ".join(names)), candidates=names)
    if callable(getattr(reg, "prompt", None)):
        prompt = reg.prompt(key, judges, prod, cands)
    else:
        prompt = default_prompt(key, judges, prod, cands, reg)
    ans = ask(prompt, ROUTE_SCHEMA) or {}
    decision = ans.get("decision")
    bank = ans.get("bank")
    why = str(ans.get("why") or "")[:400]
    if decision not in DECISIONS:
        return _out("new", None, "model", "the model answered no decision ({!r}): new"
                    .format(decision), candidates=names)
    if decision == "new":
        return _out("new", None, "model", why, candidates=names)
    if bank not in names:
        return _out("new", None, "model",
                    "the model named {!r}, not among the candidates: read as new ({})"
                    .format(bank, why), candidates=names)
    if decision == "extend" and _opt(reg, "covered", False, bank, key):
        decision, why = "reuse", "{} (its cases already hold this instance: reuse)".format(why)
    return _out(decision, bank, "model", why, _family_of_bank(families, bank),
                candidates=names)


def route_line(res):
    """One human line of a route result."""
    return "{} {} ({}{}): {}".format(
        res["decision"], res.get("bank") or "-", res["how"],
        ", family {}".format(res["family"]) if res.get("family") else "", res["why"])


# ------------------------------------------------------------------ explain

def bank_status(root, bank, slice_=None):
    """released / failing / unreleased / invalid of `bank` (relative to `root`) read through the
    kit; with `slice_` (["types" or "instances", name]) that slice's verdict, the bank's own
    when the release holds no such slice."""
    d = os.path.join(root, bank)
    doc = read_release(d)
    if doc is None:
        if os.path.isfile(os.path.join(d, "RELEASE.json")):
            return "invalid"
        return "unreleased"
    if envelope_problems(doc):
        return "invalid"
    if slice_:
        got = released_slice(d, slice_[0], slice_[1])
        if got is not None:
            return "released" if got else "failing"
    return "released" if doc["passed"] is True else "failing"


def explain(key, registry, root=None):
    """registry.layers(key), each layer with its "status" read through the kit when it has a
    bank (its own "status", else "-", when it has none)."""
    root = _root(registry, root)
    out = []
    for layer in registry.layers(key) or ():
        x = dict(layer)
        if x.get("bank"):
            x["status"] = bank_status(root, x["bank"], x.get("slice"))
        else:
            x["status"] = x.get("status") or "-"
        out.append(x)
    return out


def layer_line(x):
    return "{} {}  {}  {}  {}".format(x.get("layer"), x.get("name"), x.get("bank") or "-",
                                      x["status"], x.get("why") or "")


def bank_dirs(root, pattern="**/CODEBOOK.md"):
    """{bank relative to root: judges line} of every directory under `root` holding a
    CODEBOOK.md: a helper a registry's banks() can return."""
    out = {}
    for p in sorted(glob.glob(os.path.join(root, pattern), recursive=True)):
        d = os.path.dirname(p)
        out[os.path.relpath(d, root)] = codebook_judges(d)
    return out
