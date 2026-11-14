"""An eval bank: labeled cases, a grader, a bar declared before the first run, and a RELEASE.json
the producer's door reads. One kit for all of them, so no bank starts from a blank page.

A rule case (`trimwrit case`) replays a correction to see whether a RULE still moves behaviour.
A bank measures a repeated JUDGMENT or GENERATION (a classifier, a judge, a drafter): the same
call made on many inputs, graded against their right answers. The repository this kit comes from
held 43 hand made banks: the JSONL loader copied a dozen times, 15 RELEASE.json files in 15
shapes, and a door reading `passed` that missed the bank writing `released: false`.

THE RELEASE ENVELOPE. Every RELEASE.json written here carries the same eight keys, then the
bank's own keys beside them (a producer's door keeps reading those):

  schema    1
  passed    true or false: the bar of CODEBOOK.md section 2 holds on this run
  at        ISO 8601 UTC of the run
  n         the number of cases graded
  bar       the bar as declared (a dict of thresholds, or a sentence)
  model     the model id graded, or null for a deterministic bank
  bank_sha  12 hex of the bank's own files (cases, codebook, graders) the run read
  run       the run's name under RUNS/, or null

THE DOWNSHIFT PROBE. A bank that grades a top-tier model (`SMALLER` names the ladder: a fable
or an opus) also asks, by default, ONE smaller model the same question on a small deterministic
sample of its cases (`probe`), so the owner sees at once whether the cheaper model would hold the
bar. On 2026-11-08 about 64 banks graded their producer on opus overnight, thousands of calls,
55 percent of an account's weekly window in 12 hours, with nothing saying whether sonnet would
have done. The probe's per-case rows go to RUNS/<stamp>.probe.jsonl and its summary under the key
`downshift` of RELEASE.json:

  model     the smaller model probed
  n         the cases sampled (up to 20 by default, the same ones on every run: sorted by a sha
            of the case id, among the cases the main run graded)
  right     how many the smaller model got right; rate is right / n
  big_rate  the graded model's rate on the SAME sampled cases, read from the main run's rows
  agree     the sampled cases where both are right or both are wrong
  diffs     [{id, big_right, small_right, small_got}] for every case where they differ

A call of the smaller model that raises counts as wrong, its error text in small_got; it never
aborts the run. The probe never changes `passed`: it informs a downshift, it does not grade the
producer. A sonnet or haiku bank, a deterministic one, `--offline` and `--no-probe` probe nothing.

THE EFFORT PROBE. The same question one rung down the other axis: a bank whose producer runs at
an effort above medium (`EFFORT_LADDER`: xhigh to high, high to medium) also asks the SAME model
at the next cheaper effort (`probe_effort`), on the SAME sampled cases as the model probe. Its
rows go to RUNS/<stamp>.effort-probe.jsonl and its summary under `downshift.effort` of
RELEASE.json: RELEASE.json `downshift` keeps the model probe's keys above when there is one, and
gains the key `effort`:

  downshift.effort.effort      the cheaper effort probed (big_effort: the one the producer runs)
  downshift.effort.model       the model, the same for both sides
  downshift.effort.n           the cases sampled, chosen as the model probe chooses them
  downshift.effort.right       how many the cheaper effort got right; rate is right / n
  downshift.effort.big_rate    the producer's effort's rate on the SAME cases, from the main run
  downshift.effort.agree       the sampled cases where both are right or both are wrong
  downshift.effort.diffs       [{id, big_right, small_right, small_got}], small = the cheaper effort

A bank with no model probe (a sonnet at high effort) writes `downshift` as {"effort": {...}}
alone. Same rules: a raising call is wrong, never an abort, `passed` never moves; a bank at
medium or lower, a deterministic one, `--offline` and `--no-probe` probe no effort.

SLICES. A bank that grades several message types or several instances of one judgment (a bank
per family, one slice per member) says so in two optional keys, each {name: {"passed": bool,
"n": count, ...}}:

  types       the verdict per message type the bank reads
  instances   the verdict per instance (a bet, a project, a row) the bank holds as a slice

`write_release(types=..., instances=...)` writes them (or `extra`, never both for one key), and
`released_slice(bank, kind, name)` reads one back: True, False, or None when absent. The names
`by_type` and `by_instance`, which banks written before 0.15.0 carry, are read as aliases and
validated the same way. The facet keys `covers` {axis: [values]} and `by_facet` {axis: {value:
{"n", "metric"}}} (trimwrit/facets.py) are validated when present.

THE STANDARD. A standard bank's run.py declares constants and hooks, then hands its run to the
library; it holds no runner of its own:

  MODEL         a full model id (FULL_ID_RE, no alias, no "latest"), or None (deterministic)
  EFFORT        one of EFFORTS or "model-default" when MODEL is set, None when it is not
  BAR           {metric: threshold in [0, 1]}, optional n_min; the same text in CODEBOOK.md s.2
                ("metric >= value"; a metric named *_max is an upper bound: "metric <= value",
                passed needs the score at or under it)
  LABEL_SOURCE  who labeled the rows that carry no label_source (never the judged model)
  FIELDS        optional {canonical: row key} for id, input, expected, why, split, label_source
  JOBS, WAIVE   optional: threads, and dated 90-day waivers of WAIVABLE_IN_BANK rules
  answer(case, model=MODEL, effort=EFFORT) and grade(case, got); optional score, ideal, mutate,
  cache_key, stamp
  if __name__ == "__main__": sys.exit(bank.main(__file__))

answer() never writes into the bank directory outside RUNS/: the release names the inputs as they
stood when the run started (inputs_sha), so a file answer() leaves beside CORPUS.jsonl voids the
release the moment it is written. A model call reports itself with note_usage(envelope) on the
thread that runs answer().

Each hole is a Refusal (exit EXIT_REFUSED, 65) naming one of RULES and its exact fix. `lint`
(trimwrit/bank_lint.py, static, never imports the bank) refuses bespoke-runner, model-not-pinned,
corpus-untyped, label-by-judged-model, corpus-too-small (under MIN_CASES), no-holdout (fewer than
max(HOLDOUT_MIN, n / 5) holdout cases, see `splits`), bar-moved and
corpus-changed-since-release. `self_check` adds grader-cannot-fail: the grader must accept the
right answer and refuse a wrong one (`mutate`, else `default_mutation`) on up to MUTATION_SAMPLE
cases. `bank.main` refuses at run time cost-not-recorded, no-repeat and the resolved values lint
could not read.

`trimwrit bank new <slug>` lays out a bank on this kit; `trimwrit bank check` reads every
RELEASE.json under a directory against the envelope.

The module is standard library only, so a bank's run.py can import it from a checkout, the plugin
cache or a pip install alike.
"""
import datetime
import glob
import hashlib
import json
import os
import re
import tempfile

SCHEMA = 1
ENVELOPE = ("schema", "passed", "at", "n", "bar", "model", "bank_sha", "run")
CORPUS = "CORPUS.jsonl"
RELEASE = "RELEASE.json"
RUNS = "RUNS"
PROBE_EXT = ".probe.jsonl"
# the answer cache of a standard bank (bank.main, a run.py defining cache_key): RUNS/cache.jsonl
CACHE_FILE = "cache.jsonl"
# the downshift ladder: a top-tier model graded by a bank -> the ONE smaller model its probe asks.
# Only top-tier models are here: a sonnet or a haiku bank is already the cheap one, no probe.
SMALLER = {
    "claude-fable-5-1": "claude-opus-5-5",
    "claude-fable-5": "claude-opus-5-5",
    "claude-opus-5-5": "claude-sonnet-5-5",
    "claude-opus-5": "claude-sonnet-5-5",
    "claude-opus-4-8": "claude-sonnet-5-5",
}
PROBE_SAMPLE = 20
PROBE_EFFORT_EXT = ".effort-probe.jsonl"
# the effort ladder: the effort a bank's producer runs at -> the ONE cheaper effort its probe
# asks of the same model. Medium and below are not here: medium is already the default.
EFFORT_LADDER = {"xhigh": "high", "high": "medium"}
# the optional slice keys of a release, each with the alias older banks write
SLICES = {"types": "by_type", "instances": "by_instance"}
SLICE_KEYS = ("types", "by_type", "instances", "by_instance")
# what bank_sha leaves out: the outputs of a run and caches, never its inputs
NOT_INPUTS = (RELEASE, RUNS, "__pycache__", ".pytest_cache")


class BankError(ValueError):
    """A bank file that cannot be read as written, or a release that breaks the envelope."""


# ------------------------------------------------------------------ the standard: refusals

RULES = ("bar-moved", "grader-cannot-fail", "label-by-judged-model", "no-holdout",
         "corpus-too-small", "bespoke-runner", "corpus-changed-since-release",
         "model-not-pinned", "cost-not-recorded", "no-repeat", "corpus-untyped")
# the rules a run.py WAIVE may name (dated, 90 days); every other rule holds in every bank
WAIVABLE_IN_BANK = ("corpus-too-small", "no-holdout")
EXIT_REFUSED = 65
# a full model id: words joined by '-', ending in version numbers (claude-sonnet-5-5); an alias
# (opus, sonnet, claude-opus) or a "latest" pointer is not one
FULL_ID_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*-\d+(-\d+)*$")
MIN_CASES = 30
HOLDOUT_MIN = 5
HOLDOUT_SHARE = 0.25
MUTATION_SAMPLE = 20
# what inputs_sha leaves out besides NOT_INPUTS: a bank's own tests are not its inputs
TEST_FILE_RE = re.compile(r"^(test_.*\.py|.*_test\.py|conftest\.py)$")


class Refusal(BankError):
    """One hole of the standard in one bank: the `rule` (one of RULES), the `bank`, `what` is
    wrong, and the exact `fix` (a command, or an edit naming the file and the line)."""

    def __init__(self, rule, bank, what, fix):
        if rule not in RULES:
            raise ValueError("rule {!r}: one of {}".format(rule, ", ".join(RULES)))
        self.rule = rule
        self.bank = str(bank)
        self.what = what
        self.fix = fix
        super().__init__(str(self))

    def __str__(self):
        return "{}: {}: {} (fix: {})".format(self.bank, self.rule, self.what, self.fix)

    def __repr__(self):
        return "Refusal({!r}, {!r}, {!r}, {!r})".format(self.rule, self.bank, self.what, self.fix)

    def __eq__(self, other):
        return isinstance(other, Refusal) and self.as_dict() == other.as_dict()

    def __hash__(self):
        return hash((self.rule, self.bank, self.what, self.fix))

    def as_dict(self):
        return {"refused": "bank-standard", "rule": self.rule, "bank": self.bank,
                "what": self.what, "fix": self.fix}


# ------------------------------------------------------------------ cases, shas, runs

def load(path):
    """The cases of a JSONL file: one JSON object per non-blank line. A line that is not an
    object raises BankError naming the file and the line, never a silent skip."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for no, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise BankError("{}:{}: not JSON ({})".format(path, no, exc)) from None
            if not isinstance(row, dict):
                raise BankError("{}:{}: a {}, not an object".format(path, no, type(row).__name__))
            out.append(row)
    return out


def corpus(bank):
    """The cases of `bank`/CORPUS.jsonl."""
    return load(os.path.join(bank, CORPUS))


def sha(*parts):
    """12 hex of sha256 over `parts`, in order: a path to a file is read as bytes, anything else
    is its text. For a prompt_sha, a grader_sha, a label_sha."""
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, (str, os.PathLike)) and os.path.isfile(p):
            with open(p, "rb") as fh:
                h.update(fh.read())
        else:
            h.update(str(p).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:12]


def input_files(bank):
    """The bank's own files a run reads, sorted, relative to the bank: every file under it but
    the run outputs (RELEASE.json, RUNS/) and caches."""
    out = []
    for dirpath, dirnames, filenames in os.walk(bank):
        dirnames[:] = sorted(d for d in dirnames if d not in NOT_INPUTS)
        for f in filenames:
            if f in NOT_INPUTS or f.endswith(".pyc") or f.startswith(".RELEASE."):
                continue
            out.append(os.path.relpath(os.path.join(dirpath, f), bank))
    return sorted(out)


def bank_sha(bank):
    """12 hex naming the bank's inputs as they stand: each file's relative path and bytes."""
    h = hashlib.sha256()
    for rel in input_files(bank):
        h.update(rel.encode("utf-8") + b"\0")
        with open(os.path.join(bank, rel), "rb") as fh:
            h.update(fh.read())
        h.update(b"\0")
    return h.hexdigest()[:12]


def inputs_sha(bank):
    """12 hex naming the bank's inputs as `bank_sha` does, leaving out the bank's own tests
    (test_*.py, *_test.py, conftest.py): a test added beside a released corpus does not void the
    release, an edited CORPUS.jsonl does."""
    h = hashlib.sha256()
    bank = str(bank)
    for rel in input_files(bank):
        if TEST_FILE_RE.match(os.path.basename(rel)):
            continue
        h.update(rel.encode("utf-8") + b"\0")
        with open(os.path.join(bank, rel), "rb") as fh:
            h.update(fh.read())
        h.update(b"\0")
    return h.hexdigest()[:12]


def bar_sha(bar):
    """12 hex naming a bar: sha of its JSON with sorted keys, every number but n_min read as a
    float, so {"x": 0} and {"x": 0.0} name the same bar."""
    if isinstance(bar, dict):
        bar = {k: (float(v) if k != "n_min" and isinstance(v, (int, float))
                   and not isinstance(v, bool) else v) for k, v in bar.items()}
    return sha(json.dumps(bar, sort_keys=True))


def splits(cases):
    """{id: "dev" | "holdout"} of `cases`: a case's own `split` ("test" is read as "holdout"),
    else derived and stable: the cases without one, sorted by (sha(str(id)), str(id)), the first
    quarter (rounded up) are "holdout", the rest "dev"."""
    out = {}
    without = []
    for c in cases:
        s = c.get("split")
        if s == "test":
            s = "holdout"
        if s in ("dev", "holdout"):
            out[c.get("id")] = s
        else:
            without.append(c)
    without.sort(key=lambda c: (sha(str(c.get("id"))), str(c.get("id"))))
    cut = -(-len(without) // int(round(1 / HOLDOUT_SHARE)))
    for i, c in enumerate(without):
        out.setdefault(c.get("id"), "holdout" if i < cut else "dev")
    return out


def _flip(value):
    """A wrong answer of the same type as `value` (default_mutation's fallback)."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    if isinstance(value, str):
        return "" if value else "mutated"
    if isinstance(value, list):
        return [] if value else ["mutated"]
    if isinstance(value, dict):
        if not value:
            return {"mutated": True}
        return {k: _flip(v) for k, v in value.items()}
    return "mutated"


def _dump(value):
    return json.dumps(value, sort_keys=True, default=repr)


def default_mutation(case, ideal, ideals):
    """A wrong answer for `case` whose right answer is `ideal`: the most frequent of `ideals`
    (the corpus's right answers) that differs from `ideal`, ties broken by the smallest JSON;
    when none differs, a flip of `ideal`'s type (str: "" or "mutated", bool: not, number: + 1,
    None: "mutated", list: [] or ["mutated"], dict: each value flipped)."""
    counts = {}
    for v in ideals:
        try:
            if v == ideal:
                continue
        except Exception:  # noqa: BLE001  (an incomparable answer differs)
            pass
        key = _dump(v)
        if key in counts:
            counts[key][0] += 1
        else:
            counts[key] = [1, v]
    if counts:
        best = min(counts, key=lambda k: (-counts[k][0], k))
        return counts[best][1]
    return _flip(ideal)


def now_iso(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")


def run_stamp(now=None):
    """The name of a run file: 20261103T091500 (UTC), so names sort by time."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")


def run_path(bank, stamp, ext=".jsonl"):
    """`bank`/RUNS/<stamp><ext>, its directory created."""
    d = os.path.join(bank, RUNS)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, stamp + ext)


def latest_run(bank, pattern="*"):
    """The newest file of `bank`/RUNS matching `pattern`, or None. A probe file
    (<stamp>.probe.jsonl, <stamp>.effort-probe.jsonl) is left out unless `pattern` names the
    probe: it is not the run. The answer cache (CACHE_FILE) is never a run."""
    found = sorted(p for p in glob.glob(os.path.join(bank, RUNS, pattern)) if os.path.isfile(p)
                   and os.path.basename(p) != CACHE_FILE
                   and (not os.path.basename(p).endswith((PROBE_EXT, PROBE_EFFORT_EXT))
                        or "probe" in pattern))
    return found[-1] if found else None


# ------------------------------------------------------------------ the release

# the keys a schema 2 release (bank.main, trimwrit/bank_run.py) adds to ENVELOPE
ENVELOPE_2 = ENVELOPE + ("effort", "inputs_sha", "bar_sha", "bar_since", "usage", "wall_s",
                         "repeats", "splits", "engine", "standard")
USAGE_COUNTERS = ("calls", "cached", "input_tokens", "output_tokens", "cache_read_tokens",
                  "cache_write_tokens", "cost_usd")
HEX12_RE = re.compile(r"^[0-9a-f]{12}$")


def envelope_problems(doc):
    """[str] of what keeps `doc` from carrying the envelope; [] when it does. Schema 1 is the
    eight keys above; schema 2 (a standard bank's release, bank.main) also every key of
    ENVELOPE_2, typed."""
    if not isinstance(doc, dict):
        return ["not a JSON object"]
    keys = ENVELOPE_2 if doc.get("schema") == 2 else ENVELOPE
    out = ["no `{}`".format(k) for k in keys if k not in doc]
    if out:
        return out
    if doc["schema"] == 2:
        out += _schema2_problems(doc)
    elif doc["schema"] != SCHEMA:
        out.append("schema {!r}, expected {} or 2".format(doc["schema"], SCHEMA))
    if not isinstance(doc["passed"], bool):
        out.append("`passed` is not true or false")
    if not isinstance(doc["n"], int) or isinstance(doc["n"], bool) or doc["n"] < 0:
        out.append("`n` is not a count")
    if doc["bar"] in (None, "", {}, []):
        out.append("`bar` is empty: declare it in CODEBOOK.md section 2 before the first run")
    if doc["model"] is not None and not isinstance(doc["model"], str):
        out.append("`model` is neither a model id nor null")
    if not isinstance(doc["bank_sha"], str) or len(doc["bank_sha"]) != 12:
        out.append("`bank_sha` is not 12 hex (bank_sha)")
    try:
        datetime.datetime.fromisoformat(doc["at"])
    except (TypeError, ValueError):
        out.append("`at` is not an ISO date")
    if doc["run"] is not None and not isinstance(doc["run"], str):
        out.append("`run` is neither a run name nor null")
    for key in SLICE_KEYS:
        if key in doc:
            out += _slice_problems(key, doc[key])
    if "covers" in doc:
        out += _covers_problems(doc["covers"])
    if "by_facet" in doc:
        out += _by_facet_problems(doc["by_facet"])
    return out


def _is_count(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_full_id(model):
    return isinstance(model, str) and bool(FULL_ID_RE.match(model)) and "latest" not in model


def _usage_problems(where, u):
    if not isinstance(u, dict):
        return ["`{}` is not an object".format(where)]
    out = []
    for k in USAGE_COUNTERS:
        v = u.get(k)
        if k == "cost_usd":
            if not _is_number(v) or v < 0:
                out.append("`{}.cost_usd` is not a number".format(where))
        elif not _is_count(v):
            out.append("`{}.{}` is not a count".format(where, k))
    return out


def _schema2_problems(doc):
    """[str] of what keeps a schema 2 `doc` (every key of ENVELOPE_2 present) typed."""
    out = []
    model, effort = doc["model"], doc["effort"]
    if model is not None and not _is_full_id(model):
        out.append("`model` {!r} is neither null nor a full model id".format(model))
    if (model is None) != (effort is None):
        out.append("`effort` {!r} with `model` {!r}: effort is null iff model is null".format(
            effort, model))
    elif effort is not None and effort not in EFFORTS + ("model-default",):
        out.append("`effort` {!r} is not one of {}".format(effort, ", ".join(EFFORTS)))
    for key in ("inputs_sha", "bar_sha"):
        if not isinstance(doc[key], str) or not HEX12_RE.match(doc[key]):
            out.append("`{}` is not 12 hex".format(key))
    try:
        datetime.date.fromisoformat(doc["bar_since"])
    except (TypeError, ValueError):
        out.append("`bar_since` is not an ISO date")
    usage = doc["usage"]
    out += _usage_problems("usage", usage)
    if isinstance(usage, dict):
        if not isinstance(usage.get("models"), list):
            out.append("`usage.models` is not a list")
        phases = usage.get("phases")
        if not isinstance(phases, dict):
            out.append("`usage.phases` is not an object")
        else:
            for ph in ("main", "repeats", "probe"):
                out += _usage_problems("usage.phases." + ph, phases.get(ph))
    if not _is_number(doc["wall_s"]) or doc["wall_s"] < 0:
        out.append("`wall_s` is not a number of seconds")
    rep = doc["repeats"]
    if not isinstance(rep, dict):
        out.append("`repeats` is not an object")
    else:
        k = rep.get("k")
        if not _is_count(k) or k < 1:
            out.append("`repeats.k` is not an int >= 1")
        for key in ("sample", "flips"):
            if not _is_count(rep.get(key)):
                out.append("`repeats.{}` is not a count".format(key))
        for key in ("ids", "rates"):
            if not isinstance(rep.get(key), list):
                out.append("`repeats.{}` is not a list".format(key))
    if not isinstance(doc["splits"], dict):
        out.append("`splits` is not an object")
    if not isinstance(doc["engine"], str) or not doc["engine"]:
        out.append("`engine` is not an engine name")
    if not _is_count(doc["standard"]) or doc["standard"] < 1:
        out.append("`standard` is not a version number")
    if "gates" in doc and not (isinstance(doc["gates"], dict)
                               and all(isinstance(v, bool) for v in doc["gates"].values())):
        out.append("`gates` is not {name: true or false}")
    return out


def _slice_problems(key, value):
    if not isinstance(value, dict):
        return ["`{}` is not an object".format(key)]
    out = []
    for name, entry in value.items():
        if not isinstance(entry, dict):
            out.append("`{}[{}]` is not an object".format(key, name))
            continue
        if not isinstance(entry.get("passed"), bool):
            out.append("`{}[{}]` has no boolean passed".format(key, name))
        if not _is_count(entry.get("n")):
            out.append("`{}[{}]` has no count n".format(key, name))
    return out


def _covers_problems(value):
    if not isinstance(value, dict):
        return ["`covers` is not an object"]
    return ["`covers[{}]` is not a list of values".format(axis)
            for axis, vals in value.items() if not isinstance(vals, list)]


def _by_facet_problems(value):
    if not isinstance(value, dict):
        return ["`by_facet` is not an object"]
    out = []
    for axis, by_value in value.items():
        if not isinstance(by_value, dict):
            out.append("`by_facet[{}]` is not an object".format(axis))
            continue
        for v, d in by_value.items():
            where = "`by_facet[{}][{}]`".format(axis, v)
            if not isinstance(d, dict):
                out.append(where + " is not an object")
                continue
            if not _is_count(d.get("n")):
                out.append(where + " has no count n")
            m = d.get("metric")
            if m is not None and (isinstance(m, bool) or not isinstance(m, (int, float))):
                out.append(where + " has no metric number")
    return out


def release_doc(bank, *, passed, n, bar, model, run=None, at=None, extra=None, types=None,
                instances=None):
    """The RELEASE document of a run: the envelope, then `extra` (the bank's own keys), then the
    slices `types` and `instances` when given. Raises BankError on a key of `extra` that would
    overwrite the envelope, a slice given both ways, or a broken envelope (a malformed slice
    included)."""
    extra = dict(extra or {})
    clash = sorted(set(extra) & set(ENVELOPE))
    if clash:
        raise BankError("extra keys {} would overwrite the envelope: pass them as arguments"
                        .format(clash))
    for key, value in (("types", types), ("instances", instances)):
        if value is None:
            continue
        if key in extra:
            raise BankError("`{0}` given twice, as {0}= and in extra: pass it once".format(key))
        extra[key] = value
    doc = {"schema": SCHEMA, "passed": passed, "at": at or now_iso(), "n": n, "bar": bar,
           "model": model, "bank_sha": bank_sha(bank), "run": run}
    doc.update(extra)
    problems = envelope_problems(doc)
    if problems:
        raise BankError("{}: {}".format(bank, "; ".join(problems)))
    return doc


def write_release(bank, *, passed, n, bar, model, run=None, at=None, extra=None, types=None,
                  instances=None):
    """Write `bank`/RELEASE.json atomically (a reader never sees half a file) and return it.
    `types` and `instances` are the slices of the module docstring."""
    doc = release_doc(bank, passed=passed, n=n, bar=bar, model=model, run=run, at=at,
                      extra=extra, types=types, instances=instances)
    return _write_release_file(bank, doc)


def _write_release_file(bank, doc):
    """Write `doc` to `bank`/RELEASE.json atomically, ASCII, indent 1, sorted keys; return it.
    The one writer of both schemas (write_release, bank_run's schema 2 release)."""
    fd, tmp = tempfile.mkstemp(dir=bank, prefix=".RELEASE.", suffix=".tmp")
    try:
        # ASCII on disk: a verbatim the bank carries keeps its characters as \u escapes, so the
        # file stays greppable and never holds a raw long dash
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True, ensure_ascii=True)
            fh.write("\n")
        os.replace(tmp, os.path.join(bank, RELEASE))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return doc


def read_release(bank):
    """`bank`/RELEASE.json, or None when absent or unreadable."""
    try:
        with open(os.path.join(bank, RELEASE), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def released(bank):
    """True only when the bank's RELEASE.json carries the envelope and says passed: the one
    reading a producer's door makes of its bank. A schema 2 release also needs the bank's
    inputs unchanged since the run (`inputs_sha`): an edited corpus voids it."""
    doc = read_release(bank)
    if doc is None or envelope_problems(doc) or doc["passed"] is not True:
        return False
    return doc["schema"] != 2 or doc["inputs_sha"] == inputs_sha(bank)


def released_slice(bank, kind, name):
    """The verdict of one slice of `bank`'s release: `kind` is "types" or "instances" (the
    singular reads the same), `name` the type or the instance. True or False as the slice says
    (read under `types`, else its alias `by_type`; `instances`, else `by_instance`), None when
    the release is absent, breaks the envelope, or holds no such slice."""
    key = {"type": "types", "instance": "instances"}.get(kind, kind)
    if key not in SLICES:
        raise BankError("slice kind {!r}: types or instances".format(kind))
    doc = read_release(bank)
    if doc is None or envelope_problems(doc):
        return None
    for k in (key, SLICES[key]):
        entry = doc.get(k)
        if isinstance(entry, dict) and isinstance(entry.get(name), dict):
            passed = entry[name].get("passed")
            return passed if isinstance(passed, bool) else None
    return None


def check_tree(root):
    """[(bank directory relative to `root`, [problems])] for every RELEASE.json under `root`."""
    out = []
    for p in sorted(glob.glob(os.path.join(root, "**", RELEASE), recursive=True)):
        b = os.path.dirname(p)
        doc = read_release(b)
        out.append((os.path.relpath(b, root) or ".",
                    envelope_problems(doc) if doc is not None else ["unreadable"]))
    return out


# ------------------------------------------------------------------ the downshift probe

def smaller(model):
    """The ONE smaller model a bank grading `model` probes, or None (a model off the ladder,
    a sonnet, a haiku, or a deterministic bank's None)."""
    return SMALLER.get(model) if isinstance(model, str) else None


def probe_sample(cases, sample=PROBE_SAMPLE):
    """Up to `sample` of `cases`, in a stable order: sorted by a sha of each case's id, so two
    runs on the same corpus pick the same cases, and a case added later does not reshuffle the
    others."""
    keyed = sorted(cases, key=lambda c: (sha(str(c.get("id"))), str(c.get("id"))))
    return keyed[:max(0, int(sample))]


def _compare(cases, rows, call, grade, sample, out, tag):
    """The sampled cases asked through `call(case)` and compared with the main run's `rows`:
    (n, right, big_right, agree, diffs). `tag` is merged into each row given to `out`."""
    big = {r["id"]: bool(r["right"]) for r in rows}
    picked = probe_sample([c for c in cases if c.get("id") in big], sample)
    right = agree = 0
    diffs = []
    for case in picked:
        try:
            got = call(case)
            ok = bool(grade(case, got))
        except Exception as exc:  # noqa: BLE001  (one failed call is a wrong answer, not a crash)
            got, ok = "error: {}: {}".format(type(exc).__name__, exc), False
        big_ok = big[case["id"]]
        right += ok
        if ok == big_ok:
            agree += 1
        else:
            diffs.append({"id": case["id"], "big_right": big_ok, "small_right": ok,
                          "small_got": got})
        if out is not None:
            out.append(dict({"id": case["id"]}, **tag, got=got, right=ok, big_right=big_ok))
    big_right = sum(1 for c in picked if big[c["id"]])
    return len(picked), right, big_right, agree, diffs


def probe(cases, rows, answer, grade, model, sample=PROBE_SAMPLE, out=None):
    """Ask the model below `model` (`smaller`) the sampled cases and compare with the main run.

    `rows` are the main run's rows ({"id", "right", ...}); only cases they graded are sampled, so
    `big_rate` is the graded model's rate on exactly the cases the smaller one answered.
    `answer(case, model=...)` is the bank's own answer function, `grade(case, got)` its grader.
    A call (or a grading) that raises counts as wrong for the smaller model, the error text in
    small_got; it never aborts. `out`, a list, receives the per-case rows (for
    RUNS/<stamp>.probe.jsonl). Returns the `downshift` dict of the module docstring, or None when
    `model` has no smaller model."""
    small = smaller(model)
    if small is None:
        return None
    n, right, big_right, agree, diffs = _compare(
        cases, rows, lambda case: answer(case, model=small), grade, sample, out,
        {"model": small})
    return {"model": small, "big_model": model, "n": n, "right": right,
            "rate": round(right / n, 4) if n else None, "big_right": big_right,
            "big_rate": round(big_right / n, 4) if n else None, "agree": agree,
            "diffs": diffs}


def probe_line(d):
    """One human line of a `probe` dict, e.g. 'probe claude-sonnet-5-5 on 20 cases: 18/20 vs
    claude-opus-5-5 19/20, 2 diffs (ids a, b)'."""
    ids = ", ".join(str(x["id"]) for x in d["diffs"])
    return "probe {} on {} cases: {}/{} vs {} {}/{}, {} diffs{}".format(
        d["model"], d["n"], d["right"], d["n"], d["big_model"], d["big_right"], d["n"],
        len(d["diffs"]), " (ids {})".format(ids) if ids else "")


def cheaper_effort(effort):
    """The ONE cheaper effort a bank whose producer runs at `effort` probes, or None (medium,
    low, an unknown effort, or None)."""
    return EFFORT_LADDER.get(effort) if isinstance(effort, str) else None


def probe_effort(cases, rows, answer, grade, model, effort, sample=PROBE_SAMPLE, out=None):
    """Ask the SAME `model` one effort lower (`cheaper_effort`) the sampled cases, the very cases
    `probe` samples, and compare with the main run.

    `answer(case, model=..., effort=...)` is the bank's answer function; the effort probe passes
    both keywords (the model probe passes only `model`, so an older answer(case, model=MODEL)
    keeps working there). A call that raises counts as wrong, its error text in small_got.
    `out`, a list, receives the per-case rows (for RUNS/<stamp>.effort-probe.jsonl). Returns the
    `downshift.effort` dict of the module docstring, or None when `effort` has no cheaper rung,
    the bank grades no model, or nothing was sampled."""
    low = cheaper_effort(effort)
    if low is None or model is None:
        return None
    n, right, big_right, agree, diffs = _compare(
        cases, rows, lambda case: answer(case, model=model, effort=low), grade, sample, out,
        {"model": model, "effort": low})
    if not n:
        return None
    return {"effort": low, "big_effort": effort, "model": model, "n": n, "right": right,
            "rate": round(right / n, 4), "big_right": big_right,
            "big_rate": round(big_right / n, 4), "agree": agree, "diffs": diffs}


def probe_effort_line(d):
    """One human line of a `probe_effort` dict, e.g. 'probe effort high on 20 cases: 18/20 vs
    xhigh 19/20 (claude-opus-5-5), 2 diffs (ids a, b)'."""
    ids = ", ".join(str(x["id"]) for x in d["diffs"])
    return "probe effort {} on {} cases: {}/{} vs {} {}/{} ({}), {} diffs{}".format(
        d["effort"], d["n"], d["right"], d["n"], d["big_effort"], d["big_right"], d["n"],
        d["model"], len(d["diffs"]), " (ids {})".format(ids) if ids else "")


# ------------------------------------------------------------------ the scaffold

SLUG_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
BAR_RE = re.compile(r"^\s*([a-z_]+)\s*>=\s*(0(?:\.\d+)?|1(?:\.0+)?)\s*$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
# how a bank's run.py and test import this kit: from an installed trimwrit by default; a
# repository that wraps the kit passes its own block (it must bind the name `bank`)
DEFAULT_IMPORT = "from trimwrit import bank  # noqa: E402"
EFFORTS = ("low", "medium", "high", "xhigh", "max")

CODEBOOK = """# {judges}: the codebook

Bank `{bank_rel}`, laid out {today} by `trimwrit bank new` (trimwrit/bank.py).
Producer: {producer}.

## 0. Why this judgment

(What decision the producer takes on this judgment, what a wrong answer costs, and who reads it.
Fill before the first case.)

## 1. The cases

One JSON object per line of CORPUS.jsonl: `id` (unique), `input` (what the producer reads, as it
read it), `expected` (the right answer), `why` (one line: the evidence for that answer). Real
inputs from what the producer already saw; enough of them that the bar reads the failures that
matter, the hard ones included.

## 2. The bar, declared {today} BEFORE the first run

{bar_metric} >= {bar_value} over every case of CORPUS.jsonl (`BAR` in run.py). A run below it
writes `passed: false` in RELEASE.json; the producer's door reads `bank.released()`.

## 3. Changes (dated, with their reason)

- {today}: laid out.
"""

RUN = '''#!/usr/bin/env python3
"""run.py: grade {judges_lc} on {bank_rel}/CORPUS.jsonl against the bar of CODEBOOK.md section 2;
writes RUNS/<stamp>.jsonl and RELEASE.json (the RELEASE envelope of trimwrit/bank.py).

    python3 {bank_rel}/run.py [--offline] [--no-probe] [--probe-sample N]

--offline grades each case's `expected` as if the producer had answered it: it checks the grader
and the corpus, never the producer.

When MODEL is a top-tier model (bank.SMALLER), a live run then asks ONE smaller model the same
question on a deterministic sample of the cases (--probe-sample, default 20): its rows go to
RUNS/<stamp>.probe.jsonl, its summary under `downshift` in RELEASE.json, one line to stderr.

When EFFORT is above medium (bank.EFFORT_LADDER), a live run also asks the SAME model one effort
lower on the same sampled cases: its rows go to RUNS/<stamp>.effort-probe.jsonl, its summary under
`downshift.effort` in RELEASE.json, one line to stderr. No probe ever changes `passed`.
--no-probe skips both.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
{path_block}
{import_block}

MODEL = {model_literal}
# the effort the producer runs at (low, medium, high, xhigh): above medium, the effort probe asks
# MODEL one rung lower too
EFFORT = {effort_literal}
BAR = {{"{bar_metric}": {bar_value}}}


def answer(case, model=MODEL, effort=EFFORT):
    """The producer's answer on one case, from `model` at `effort`. Call the producer's own
    function here, the same code path production runs, with `model` in place of its model id and
    `effort` in place of its effort: the downshift probe passes a smaller model
    (bank.smaller(MODEL)), the effort probe a cheaper effort (bank.cheaper_effort(EFFORT)), to see
    whether either would hold the bar. A
    model call runs lean (claude -p --bare or --safe-mode): a one-shot that loads the whole
    harness costs ten times the tokens for the same answer."""
    raise NotImplementedError("call the producer on case['input']")


def grade(case, got):
    """True when `got` is the right answer for `case`. Deterministic where possible."""
    return got == case["expected"]


def score(rows):
    n = len(rows)
    right = sum(1 for r in rows if r["right"])
    return {{"{bar_metric}": round(right / n, 4) if n else None, "right": right}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\\n")[0])
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--no-probe", action="store_true",
                    help="skip the downshift probes (smaller model, cheaper effort)")
    ap.add_argument("--probe-sample", type=int, default=bank.PROBE_SAMPLE,
                    help="cases the downshift probe asks (default %(default)s)")
    args = ap.parse_args(argv)
    cases = bank.corpus(HERE)
    stamp = bank.run_stamp()
    rows = []
    for case in cases:
        got = case["expected"] if args.offline else answer(case)
        rows.append({{"id": case["id"], "got": got, "right": bool(grade(case, got))}})
    s = score(rows)
    passed = bool(cases) and all(s[k] is not None and s[k] >= v for k, v in BAR.items())
    if args.offline:
        print(json.dumps(dict(s, n=len(rows), passed=passed), sort_keys=True))
        return 0 if passed else 1
    with open(bank.run_path(HERE, stamp), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\\n")
    extra = dict(s)
    if not args.no_probe and bank.smaller(MODEL) is not None:
        probe_rows = []
        extra["downshift"] = bank.probe(cases, rows, answer, grade, MODEL,
                                        sample=args.probe_sample, out=probe_rows)
        with open(bank.run_path(HERE, stamp, bank.PROBE_EXT), "w", encoding="utf-8") as fh:
            for r in probe_rows:
                fh.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\\n")
        print(bank.probe_line(extra["downshift"]), file=sys.stderr)
    if not args.no_probe and bank.cheaper_effort(EFFORT) is not None:
        effort_rows = []
        eff = bank.probe_effort(cases, rows, answer, grade, MODEL, EFFORT,
                                sample=args.probe_sample, out=effort_rows)
        if eff is not None:
            extra.setdefault("downshift", {{}})["effort"] = eff
            with open(bank.run_path(HERE, stamp, bank.PROBE_EFFORT_EXT), "w",
                      encoding="utf-8") as fh:
                for r in effort_rows:
                    fh.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\\n")
            print(bank.probe_effort_line(eff), file=sys.stderr)
    doc = bank.write_release(HERE, passed=passed, n=len(rows), bar=BAR, model=MODEL, run=stamp,
                             extra=extra)
    print(json.dumps(doc, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
'''

TEST = '''"""{bank_rel}: the corpus and the grader, offline (no producer, no model call).

    python3 -m pytest -q {bank_rel}/test_{module}_bank.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
{path_block}
{import_block}
import run  # noqa: E402


def test_the_corpus_has_cases_each_with_its_answer():
    cases = bank.corpus(HERE)
    assert cases, "CORPUS.jsonl is empty: a bank holds real cases with their right answer"
    ids = [c.get("id") for c in cases]
    assert len(set(ids)) == len(ids) and all(ids), "every case has a unique id"
    for c in cases:
        assert "input" in c and "expected" in c and c.get("why"), c.get("id")


def test_the_grader_accepts_every_expected_answer():
    for c in bank.corpus(HERE):
        assert run.grade(c, c["expected"]), c["id"]


def test_the_bar_is_the_codebook_bar():
    with open(os.path.join(HERE, "CODEBOOK.md"), encoding="utf-8") as fh:
        book = fh.read()
    for metric, value in run.BAR.items():
        assert "{{}} >= {{}}".format(metric, value) in book
'''


def plan(slug, judges, bar, bank_rel, model=None, producer=None, today=None,
         import_block=DEFAULT_IMPORT, path_block="", effort="medium"):
    """{path relative to the repository: text} of a new bank at `bank_rel`. Raises BankError on
    a bad slug, bar, model or effort."""
    if not SLUG_RE.match(slug or ""):
        raise BankError("slug {!r}: lowercase words joined by '-'".format(slug))
    m = BAR_RE.match(bar or "")
    if not m:
        raise BankError("bar {!r}: '<metric> >= <value between 0 and 1>', e.g. "
                        "'accuracy >= 0.9'".format(bar))
    if model is not None and not MODEL_RE.match(model):
        raise BankError("model {!r}: the model id the producer calls, or none".format(model))
    if effort not in EFFORTS:
        raise BankError("effort {!r}: one of {}".format(effort, ", ".join(EFFORTS)))
    if not (judges or "").strip():
        raise BankError("--judges: one line saying what the bank judges")
    if "bank" not in import_block:
        raise BankError("the import block must bind the name `bank`")
    judges = judges.strip().rstrip(".")
    module = slug.replace("-", "_")
    fill = dict(judges=judges, judges_lc=judges[:1].lower() + judges[1:], bank_rel=bank_rel,
                today=today or datetime.date.today().isoformat(), bar_metric=m.group(1),
                bar_value=float(m.group(2)), module=module, model_literal=repr(model), effort_literal=repr(effort),
                producer=producer or "(name the file and the function that makes the call)",
                import_block=import_block, path_block=path_block)
    return {bank_rel + "/CODEBOOK.md": CODEBOOK.format(**fill),
            bank_rel + "/CORPUS.jsonl": "",
            bank_rel + "/run.py": RUN.format(**fill),
            bank_rel + "/test_{}_bank.py".format(module): TEST.format(**fill)}


def lay_out(root, files):
    """Write `files` (from plan) under `root`. Refuses (BankError) when the bank's directory
    exists: a scaffold never overwrites a bank. Returns the paths written."""
    from .text import write_text  # noqa: PLC0415  (refuses a long dash in what it writes)
    dirs = {os.path.dirname(rel) for rel in files}
    for d in dirs:
        if os.path.exists(os.path.join(root, d)):
            raise BankError("{} exists: nothing written".format(d))
    written = []
    for rel, text in sorted(files.items()):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_text(path, text, where=rel)
        if rel.endswith("/run.py"):
            os.chmod(path, 0o755)
        written.append(rel)
    return written


# ------------------------------------------------------------------ the standard: re-exports
# at the very end, after every definition, so the import cycle with bank_lint resolves

from .bank_lint import declared, lint, load_module, resolve_case, self_check  # noqa: E402,F401
from .bank_run import (ENGINES, REPEAT_SAMPLE, REPEATS, SCHEMA_STANDARD, STANDARD,  # noqa: E402,F401
                       main, note_usage, run_local, usage_scope)
