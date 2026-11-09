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
# what bank_sha leaves out: the outputs of a run and caches, never its inputs
NOT_INPUTS = (RELEASE, RUNS, "__pycache__", ".pytest_cache")


class BankError(ValueError):
    """A bank file that cannot be read as written, or a release that breaks the envelope."""


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
    probe: it is not the run."""
    found = sorted(p for p in glob.glob(os.path.join(bank, RUNS, pattern)) if os.path.isfile(p)
                   and (not os.path.basename(p).endswith((PROBE_EXT, PROBE_EFFORT_EXT))
                        or "probe" in pattern))
    return found[-1] if found else None


# ------------------------------------------------------------------ the release

def envelope_problems(doc):
    """[str] of what keeps `doc` from carrying the envelope; [] when it does."""
    if not isinstance(doc, dict):
        return ["not a JSON object"]
    out = ["no `{}`".format(k) for k in ENVELOPE if k not in doc]
    if out:
        return out
    if doc["schema"] != SCHEMA:
        out.append("schema {!r}, expected {}".format(doc["schema"], SCHEMA))
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
    return out


def release_doc(bank, *, passed, n, bar, model, run=None, at=None, extra=None):
    """The RELEASE document of a run: the envelope, then `extra` (the bank's own keys). Raises
    BankError on a key of `extra` that would overwrite the envelope, or a broken envelope."""
    extra = dict(extra or {})
    clash = sorted(set(extra) & set(ENVELOPE))
    if clash:
        raise BankError("extra keys {} would overwrite the envelope: pass them as arguments"
                        .format(clash))
    doc = {"schema": SCHEMA, "passed": passed, "at": at or now_iso(), "n": n, "bar": bar,
           "model": model, "bank_sha": bank_sha(bank), "run": run}
    doc.update(extra)
    problems = envelope_problems(doc)
    if problems:
        raise BankError("{}: {}".format(bank, "; ".join(problems)))
    return doc


def write_release(bank, *, passed, n, bar, model, run=None, at=None, extra=None):
    """Write `bank`/RELEASE.json atomically (a reader never sees half a file) and return it."""
    doc = release_doc(bank, passed=passed, n=n, bar=bar, model=model, run=run, at=at,
                      extra=extra)
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
    reading a producer's door makes of its bank."""
    doc = read_release(bank)
    return doc is not None and not envelope_problems(doc) and doc["passed"] is True


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
