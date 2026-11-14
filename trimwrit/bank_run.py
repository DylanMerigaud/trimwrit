"""The run of a standard bank: `main(__file__)` is the whole runner a run.py holds.

A release run checks the bank (`self_check`: lint plus the grader mutation, the constants run.py
assigns from an expression checked at their imported values), locks the bar against the previous
release, answers every case through an engine (`ENGINES`, default `run_local`), grades, refuses a
run that recorded no cost or a model it does not declare, repeats a sample (`--repeats`), probes
a smaller model and a cheaper effort as the old scaffold did (bank.probe, bank.probe_effort),
scores, and writes a schema 2 RELEASE.json (the keys of bank.ENVELOPE_2).

THE USAGE SINK. A model call the bank makes reports itself with `note_usage(envelope)` (a claude
`--output-format json` envelope, or a normalized dict); `main` opens a `usage_scope()` around each
case's call, so the call is attributed to its case on whatever thread the engine runs it.

THE ENGINE SEAM. An engine is `run(cases, call, jobs) -> [(case, got, error)]`, in the order of
`cases`. `call(case)` is self contained: it opens its own usage scope around the cache lookup and
the answer and records that case's usage under a lock, so an engine may run it on any thread.

Standard library only, Python 3.9 syntax.
"""
import argparse
import contextlib
import datetime
import importlib
import json
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor


class _LazyBank(object):
    """trimwrit.bank, imported on first use: bank.py imports this module at its end, so a top
    level import here would fail when bank_run is imported first."""

    def __getattr__(self, name):
        from . import bank  # noqa: PLC0415
        return getattr(bank, name)


_bank = _LazyBank()

REPEATS = 1
REPEAT_SAMPLE = 8
SCHEMA_STANDARD = 2
STANDARD = 1
CACHE_FILE = "cache.jsonl"  # the same as bank.CACHE_FILE
DEFAULT_JOBS = 4
EXIT_ENGINE = 2
INSPECT_HINT = "pip install inspect-ai in a separate venv to run --engine inspect"
TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
# the claude envelope's modelUsage names -> the normalized counters
ENVELOPE_TOKENS = (("inputTokens", "input_tokens"), ("outputTokens", "output_tokens"),
                   ("cacheReadInputTokens", "cache_read_tokens"),
                   ("cacheCreationInputTokens", "cache_write_tokens"))
PHASES = ("main", "repeats", "probe")


# ------------------------------------------------------------------ the usage sink

_local = threading.local()


def _zero():
    return {"calls": 0, "cached": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.0, "models": set()}


def _int(v):
    try:
        return max(0, int(v)) if not isinstance(v, bool) else 0
    except (TypeError, ValueError):
        return 0


def _float(v):
    try:
        return max(0.0, float(v)) if not isinstance(v, bool) else 0.0
    except (TypeError, ValueError):
        return 0.0


@contextlib.contextmanager
def usage_scope():
    """A scope collecting the calls `note_usage` reports on this thread; yields its accumulator
    ({calls, cached, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
    cost_usd, models (a set)}). Scopes nest: a call counts in every open scope of its thread."""
    acc = _zero()
    stack = getattr(_local, "stack", None)
    if stack is None:
        stack = _local.stack = []
    stack.append(acc)
    try:
        yield acc
    finally:
        stack.remove(acc)


def _normalize(envelope):
    """(model ids, {token counters}, cost) of one reported call; never raises."""
    models, tokens, cost = [], dict.fromkeys(TOKEN_KEYS, 0), 0.0
    if not isinstance(envelope, dict):
        return models, tokens, cost
    usage = envelope.get("modelUsage")
    if isinstance(usage, dict):
        per_model_cost = 0.0
        for model, u in usage.items():
            if isinstance(model, str) and model:
                models.append(model)
            if not isinstance(u, dict):
                continue
            for src, dst in ENVELOPE_TOKENS:
                tokens[dst] += _int(u.get(src))
            per_model_cost += _float(u.get("costUSD"))
        total = envelope.get("total_cost_usd")
        cost = _float(total) if total is not None else per_model_cost
        return models, tokens, cost
    model = envelope.get("model")
    if isinstance(model, str) and model:
        models.append(model)
    for k in TOKEN_KEYS:
        tokens[k] = _int(envelope.get(k))
    cost = _float(envelope.get("cost_usd", envelope.get("total_cost_usd")))
    return models, tokens, cost


def note_usage(envelope):
    """Count one model call in every open `usage_scope` of this thread: `envelope` is a claude
    `--output-format json` envelope (total_cost_usd, modelUsage {model: {inputTokens,
    outputTokens, cacheReadInputTokens, cacheCreationInputTokens, costUSD}}) or a normalized
    dict {model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd}.
    Outside any scope a no-op. Never raises: a malformed input counts the call, zero tokens."""
    try:
        stack = getattr(_local, "stack", None)
        if not stack:
            return
        try:
            models, tokens, cost = _normalize(envelope)
        except Exception:  # noqa: BLE001  (the sink never breaks the producer's call)
            models, tokens, cost = [], dict.fromkeys(TOKEN_KEYS, 0), 0.0
        for acc in stack:
            acc["calls"] += 1
            for k, v in tokens.items():
                acc[k] += v
            acc["cost_usd"] += cost
            acc["models"].update(models)
    except Exception:  # noqa: BLE001
        return


def _add(total, part):
    for k in ("calls", "cached") + TOKEN_KEYS + ("cost_usd",):
        total[k] += part.get(k, 0)
    total["models"].update(part.get("models", ()))
    return total


def _public(acc):
    """The JSON form of an accumulator: counters, cost rounded to 6, models a sorted list."""
    out = {k: acc[k] for k in ("calls", "cached") + TOKEN_KEYS}
    out["cost_usd"] = round(acc["cost_usd"], 6)
    out["models"] = sorted(acc["models"])
    return out


# ------------------------------------------------------------------ engines

def _error_text(exc):
    return "{}: {}".format(type(exc).__name__, exc)


def run_local(cases, call, jobs):
    """The default engine: `call` mapped over `cases` on `jobs` threads; [(case, got, error)] in
    the order of `cases`, error None or the text of what `call` raised (got None then)."""
    out = []
    with ThreadPoolExecutor(max_workers=max(1, int(jobs or 1))) as pool:
        futures = [(case, pool.submit(call, case)) for case in cases]
        for case, fut in futures:
            try:
                out.append((case, fut.result(), None))
            except Exception as exc:  # noqa: BLE001  (one failed answer is a wrong row)
                out.append((case, None, _error_text(exc)))
    return out


ENGINES = {"trimwrit": run_local}


def _engine(name, engine_impls):
    """(run, None) or (None, why) for the engine `name`."""
    impls = ENGINES if engine_impls is None else engine_impls
    if name in impls:
        return impls[name], None
    if name == "inspect":
        try:
            return importlib.import_module("trimwrit.bank_inspect").run, None
        except ImportError as exc:
            return None, "{} ({})".format(INSPECT_HINT, exc)
    return None, "engine {!r}: one of {}".format(name, ", ".join(sorted(set(impls) | {"inspect"})))


# ------------------------------------------------------------------ the cache

class _Cache(object):
    """RUNS/cache.jsonl: rows {"key", "got"}, read once per run; a fresh answer appended."""

    def __init__(self, bank_dir, key_fn):
        self.key_fn = key_fn
        self.path = os.path.join(bank_dir, _bank.RUNS, CACHE_FILE)
        self.rows = {}
        self.torn = 0
        self.lock = threading.Lock()
        if key_fn is None or not os.path.isfile(self.path):
            return
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    self.torn += 1
                    continue
                if not isinstance(row, dict) or "key" not in row or "got" not in row:
                    self.torn += 1
                    continue
                self.rows[_key_text(row["key"])] = row["got"]

    def lookup(self, case):
        """(hit, key, got): hit True when the cache holds case's key."""
        key = self.key_fn(case)
        text = _key_text(key)
        with self.lock:
            if text in self.rows:
                return True, key, self.rows[text]
        return False, key, None

    def store(self, key, got):
        try:
            line = json.dumps({"key": key, "got": got}, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            return  # an answer JSON cannot hold is answered afresh next time
        with self.lock:
            self.rows[_key_text(key)] = got
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")


def _key_text(key):
    return json.dumps(key, sort_keys=True, default=repr)


# ------------------------------------------------------------------ one phase

def _phase(engine, cases, answer, jobs, cache=None):
    """Answer `cases` through `engine`: {id: {"got", "error", "usage", "cached"}}. `cache` (a
    _Cache with a key function) is read and fed only when given (the main phase)."""
    store = {}
    lock = threading.Lock()

    def call(case):
        cached = False
        with usage_scope() as acc:
            try:
                if cache is not None and cache.key_fn is not None:
                    hit, key, got = cache.lookup(case)
                    if hit:
                        cached = True
                        return got
                    got = answer(case)
                    cache.store(key, got)
                    return got
                return answer(case)
            finally:
                with lock:
                    store[case["id"]] = (dict(acc, models=set(acc["models"])), cached)

    results = engine(cases, call, jobs)
    out = {}
    for case, got, error in results:
        usage, cached = store.get(case["id"], (_zero(), False))
        out[case["id"]] = {"got": None if error else got, "error": error, "usage": usage,
                           "cached": cached}
    for case in cases:
        if case["id"] not in out:
            out[case["id"]] = {"got": None, "error": "the engine returned no result",
                               "usage": store.get(case["id"], (_zero(), False))[0],
                               "cached": False}
    return out


def _grade(grade, case, got):
    """(right, extras, error) of one answer: grade returns a bool, or a dict with bool "right"
    (its other keys kept). A grader that raises makes the row wrong, its text in error."""
    try:
        res = grade(case, got)
    except Exception as exc:  # noqa: BLE001
        return False, {}, "grade: " + _error_text(exc)
    if isinstance(res, dict):
        extras = {k: v for k, v in res.items() if k != "right"}
        return res.get("right") is True, extras, None
    return bool(res), {}, None


def _right_only(grade):
    """grade as bank.probe reads it: a bool (a dict's "right")."""
    def g(case, got):
        res = grade(case, got)
        return res.get("right") is True if isinstance(res, dict) else bool(res)
    return g


# ------------------------------------------------------------------ the module and its cases

def _module(run_file, module):
    if module is not None:
        return module
    main_mod = sys.modules.get("__main__")
    f = getattr(main_mod, "__file__", None)
    if f and os.path.realpath(f) == os.path.realpath(run_file):
        return main_mod
    from .bank_lint import load_module  # noqa: PLC0415
    return load_module(run_file)


def _cases(bank_dir, module):
    from .bank_lint import resolve_case  # noqa: PLC0415
    fields = getattr(module, "FIELDS", None)
    fields = fields if isinstance(fields, dict) else {}
    label = getattr(module, "LABEL_SOURCE", None)
    label = label if isinstance(label, str) else None
    rows = _bank.corpus(bank_dir)
    canon = [resolve_case(r, fields) for r in rows]
    sp = _bank.splits(canon)
    return [resolve_case(r, fields, sp.get(c.get("id")), label) for r, c in zip(rows, canon)]


def _metrics(bar):
    return [k for k in bar if k != "n_min"] if isinstance(bar, dict) else []


def _default_score(metric):
    def score(rows):
        n = len(rows)
        right = sum(1 for r in rows if r["right"])
        return {metric: round(right / n, 4) if n else None, "right": right}
    return score


def _passed(bar, scores, n, gates):
    if n <= 0:
        return False
    for k, v in bar.items():
        if k == "n_min":
            if n < v:
                return False
            continue
        got = scores.get(k)
        if got is None or isinstance(got, bool) or not isinstance(got, (int, float)) or got < v:
            return False
    return all(v is True for v in gates.values())


def _today():
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def _write_rows(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, ensure_ascii=False, default=repr) + "\n")


def _refuse(refusals):
    for r in refusals:
        print(json.dumps(r.as_dict(), sort_keys=True), file=sys.stderr)
    return _bank.EXIT_REFUSED


def _parser():
    ap = argparse.ArgumentParser(description="run a standard eval bank (trimwrit bank.main)")
    ap.add_argument("--self-check", "--offline", dest="self_check", action="store_true",
                    help="lint and grader mutation only: no model call, nothing written")
    ap.add_argument("--ids", help="comma separated case ids (a partial run: no RELEASE.json)")
    ap.add_argument("--split", choices=("dev", "holdout"),
                    help="only the cases of this split (a partial run: no RELEASE.json)")
    ap.add_argument("--repeats", type=int, default=REPEATS,
                    help="passes over the repeat sample (default %(default)s)")
    ap.add_argument("--repeat-sample", type=int, default=REPEAT_SAMPLE,
                    help="cases each repeat asks (default %(default)s)")
    ap.add_argument("--no-probe", action="store_true",
                    help="skip the downshift probes (smaller model, cheaper effort)")
    ap.add_argument("--probe-sample", type=int, default=_bank.PROBE_SAMPLE,
                    help="cases the downshift probes ask (default %(default)s)")
    ap.add_argument("--jobs", type=int, default=None,
                    help="threads of the engine (default JOBS of run.py, else 4)")
    ap.add_argument("--engine", default="trimwrit", help="trimwrit (default) or inspect")
    ap.add_argument("--json", action="store_true", help="print the release JSON to stdout")
    return ap


# ------------------------------------------------------------------ main

def main(run_file, argv=None, module=None, engine_impls=None):
    """Run the standard bank whose run.py is `run_file` (its __file__); return the exit code:
    0 passed, 1 below the bar, 65 refused (each Refusal as JSON on stderr), 2 no such engine.
    `module` is run.py imported (default: __main__ when it is run_file, else imported by path);
    `engine_impls` stands in for ENGINES (tests)."""
    from .bank_lint import _dated_bar_line, self_check  # noqa: PLC0415
    args = _parser().parse_args(argv)
    run_file = os.path.abspath(str(run_file))
    bank_dir = os.path.dirname(run_file)
    t0 = time.monotonic()
    try:
        module = _module(run_file, module)
    except Exception:  # noqa: BLE001  (self_check names an import that raises)
        return _refuse(self_check(bank_dir))

    # 1. the self check (lint at the resolved values, the grader mutation)
    refusals = self_check(bank_dir, module=module)
    if args.self_check:
        if refusals:
            _refuse(refusals)
            print(json.dumps({"self_check": "refused",
                              "rules": sorted({r.rule for r in refusals})}, sort_keys=True))
            return _bank.EXIT_REFUSED
        print(json.dumps({"self_check": "ok"}))
        return 0
    # a release whose inputs changed is refused to its readers (lint, bank.released), never to
    # the run: the rerun is that refusal's fix
    refusals = [r for r in refusals if r.rule != "corpus-changed-since-release"]
    partial = bool(args.ids or args.split)
    if not partial and args.repeats < 1:
        refusals.append(_bank.Refusal(
            "no-repeat", bank_dir,
            "--repeats {}: a release run repeats a sample at least once".format(args.repeats),
            "rerun python3 {} without --repeats (default {})".format(run_file, REPEATS)))
    model = getattr(module, "MODEL", None)
    effort = getattr(module, "EFFORT", None)
    bar = getattr(module, "BAR", None)
    score_fn = getattr(module, "score", None)
    metrics = _metrics(bar)
    if not callable(score_fn) and isinstance(bar, dict) and len(metrics) > 1:
        refusals.append(_bank.Refusal(
            "bar-moved", bank_dir,
            "BAR names {} metrics; define score(rows) returning each".format(len(metrics)),
            "add 'def score(rows):' to {} returning {{{}}}".format(
                run_file, ", ".join("\"{}\": ...".format(m) for m in metrics))))
    if refusals:
        return _refuse(refusals)
    if not callable(score_fn):
        score_fn = _default_score(metrics[0])

    # 2. the bar lock against the previous release
    today = _today()
    bar_since = today
    prev = _bank.read_release(bank_dir)
    if prev is not None and not partial:
        if prev.get("schema") == 2:
            same = prev.get("bar_sha") == _bank.bar_sha(bar)
            since = prev.get("bar_since") if same else None
        elif isinstance(prev.get("bar"), dict):
            same = _bank.bar_sha(prev["bar"]) == _bank.bar_sha(bar)
            since = str(prev.get("at", ""))[:10] if same else None
        else:
            same, since = True, None  # a schema 1 bar written as a sentence: no lock
        if not same:
            book = ""
            try:
                with open(os.path.join(bank_dir, "CODEBOOK.md"), encoding="utf-8") as fh:
                    book = fh.read()
            except OSError:
                pass
            if not _dated_bar_line(book, prev.get("at")):
                return _refuse([_bank.Refusal(
                    "bar-moved", bank_dir,
                    "BAR {!r} differs from the released bar {!r} and CODEBOOK.md has no dated "
                    "bar line since {}".format(bar, prev.get("bar"), str(prev.get("at"))[:10]),
                    "add a line '- {}: bar changed from {!r} to {!r}, because ...' under "
                    "CODEBOOK.md section 3, then rerun python3 {}".format(
                        today, prev.get("bar"), bar, run_file))])
        if since:
            bar_since = since

    # 3. the main phase
    engine_name = args.engine
    engine, why = _engine(engine_name, engine_impls)
    if engine is None:
        print(why, file=sys.stderr)
        return EXIT_ENGINE
    jobs = args.jobs if args.jobs is not None else getattr(module, "JOBS", DEFAULT_JOBS)
    answer, grade = module.answer, module.grade
    inputs = _bank.inputs_sha(bank_dir)
    cases = _cases(bank_dir, module)
    if args.ids:
        want = {x.strip() for x in args.ids.split(",") if x.strip()}
        cases = [c for c in cases if str(c.get("id")) in want]
    if args.split:
        cases = [c for c in cases if c.get("split") == args.split]
    cache = _Cache(bank_dir, getattr(module, "cache_key", None)
                   if callable(getattr(module, "cache_key", None)) else None)
    main_res = _phase(engine, cases, answer, jobs, cache)

    # 4. grade each row
    rows = []
    phase_usage = {p: _zero() for p in PHASES}
    for case in cases:
        res = main_res[case["id"]]
        right, extras, gerr = (False, {}, None) if res["error"] else _grade(grade, case, res["got"])
        usage = res["usage"]
        _add(phase_usage["main"], usage)
        if res["cached"]:
            phase_usage["main"]["cached"] += 1
        row = dict(extras)
        row.update({"id": case["id"], "split": case.get("split"), "got": res["got"],
                    "right": right, "cached": res["cached"],
                    "usage": {k: v for k, v in _public(usage).items() if k != "cached"}})
        if res["error"] or gerr:
            row["error"] = res["error"] or gerr
        rows.append(row)
    n = len(rows)
    right_n = sum(1 for r in rows if r["right"])
    print("main: n {}, right {}, calls {}, cached {}{}".format(
        n, right_n, phase_usage["main"]["calls"], phase_usage["main"]["cached"],
        ", cache: {} torn lines skipped".format(cache.torn) if cache.torn else ""),
        file=sys.stderr)

    # 5. the cost check
    refusals = []
    noted = phase_usage["main"]["models"]
    if model is None and phase_usage["main"]["calls"]:
        refusals.append(_bank.Refusal(
            "model-not-pinned", bank_dir,
            "a deterministic bank (MODEL = None) called a model ({} calls{})".format(
                phase_usage["main"]["calls"],
                ": " + ", ".join(sorted(noted)) if noted else ""),
            "set 'MODEL = \"<the full model id answer() calls>\"' and EFFORT in {}".format(
                run_file)))
    if model is not None:
        silent = [str(r["id"]) for r in rows
                  if not r["cached"] and "error" not in r and r["usage"]["calls"] == 0]
        if silent:
            refusals.append(_bank.Refusal(
                "cost-not-recorded", bank_dir,
                "{} answered cases recorded no model call (first: {})".format(
                    len(silent), ", ".join(silent[:5])),
                "call the model through a path that calls trimwrit.bank.note_usage(envelope), "
                "e.g. growth-cockpit career/evals-core/headless.py"))
        stray = sorted(m for m in noted if not m.startswith(model))
        if stray:
            refusals.append(_bank.Refusal(
                "model-not-pinned", bank_dir,
                "MODEL is {!r} but answer() called {}".format(model, ", ".join(stray)),
                "make answer() call the model it is given (model=MODEL) in {}".format(run_file)))
    if refusals:
        return _refuse(refusals)

    score_rows = rows
    scores = dict(score_fn(score_rows))
    gates = scores.pop("gates", None) or {}
    passed = _passed(bar, scores, n, gates)
    summary = {"passed": passed, "n": n}
    summary.update({m: scores.get(m) for m in metrics})
    if partial:
        summary["partial"] = True
        print(json.dumps(summary, sort_keys=True))
        return 0 if passed else 1

    stamp = _bank.run_stamp()
    _write_rows(_bank.run_path(bank_dir, stamp), rows)

    # 6. the repeats (fresh, no cache)
    by_id = {r["id"]: r for r in rows}
    sample = _bank.probe_sample(cases, args.repeat_sample)
    flips = 0
    rates = [round(sum(1 for c in sample if by_id[c["id"]]["right"]) / len(sample), 4)
             if sample else None]
    for _ in range(args.repeats):
        res = _phase(engine, sample, answer, jobs)
        ok = 0
        for case in sample:
            r = res[case["id"]]
            _add(phase_usage["repeats"], r["usage"])
            right = False if r["error"] else _grade(grade, case, r["got"])[0]
            ok += right
            flips += right != by_id[case["id"]]["right"]
        rates.append(round(ok / len(sample), 4) if sample else None)
    checks = len(sample) * args.repeats
    flip_rate = round(flips / checks, 4) if checks else 0.0
    real = [x for x in rates if x is not None]
    repeats = {"k": args.repeats, "sample": len(sample), "ids": [c["id"] for c in sample],
               "flips": flips, "flip_rate": flip_rate, "rates": rates,
               "stdev": round(statistics.pstdev(real), 4) if real else 0.0}
    print("repeats: k {} on {} cases, flips {}".format(args.repeats, len(sample), flips),
          file=sys.stderr)
    if model is None and flips:
        return _refuse([_bank.Refusal(
            "no-repeat", bank_dir,
            "a deterministic bank answered differently on a repeat ({} flips of {})".format(
                flips, checks),
            "make answer() in {} a pure function of the case (no state, no clock, no "
            "randomness), or declare the MODEL it calls".format(run_file))])

    # 7. the probes, as the old scaffold
    downshift = None
    if not args.no_probe:
        probe_acc = phase_usage["probe"]

        def counted(case, **kw):
            with usage_scope() as acc:
                try:
                    return answer(case, **kw)
                finally:
                    _add(probe_acc, acc)

        g = _right_only(grade)
        if _bank.smaller(model) is not None:
            probe_rows = []
            downshift = _bank.probe(cases, rows, counted, g, model, sample=args.probe_sample,
                                    out=probe_rows)
            _write_rows(_bank.run_path(bank_dir, stamp, _bank.PROBE_EXT), probe_rows)
            print(_bank.probe_line(downshift), file=sys.stderr)
        if _bank.cheaper_effort(effort) is not None:
            effort_rows = []
            eff = _bank.probe_effort(cases, rows, counted, g, model, effort,
                                     sample=args.probe_sample, out=effort_rows)
            if eff is not None:
                downshift = downshift or {}
                downshift["effort"] = eff
                _write_rows(_bank.run_path(bank_dir, stamp, _bank.PROBE_EFFORT_EXT), effort_rows)
                print(_bank.probe_effort_line(eff), file=sys.stderr)

    # 8. the score per split
    split_scores = {}
    for name in ("dev", "holdout"):
        part = [r for r in rows if r["split"] == name]
        if part:
            s = dict(score_fn(part))
            s.pop("gates", None)
            s["n"] = len(part)
        else:
            s = {"n": 0}
        split_scores[name] = s

    # 9. the schema 2 release
    total = _zero()
    for p in PHASES:
        _add(total, phase_usage[p])
    usage = _public(total)
    usage["phases"] = {p: {k: v for k, v in _public(phase_usage[p]).items() if k != "models"}
                       for p in PHASES}
    doc = {"schema": SCHEMA_STANDARD, "passed": passed, "at": _bank.now_iso(), "n": n,
           "bar": bar, "model": model, "bank_sha": _bank.bank_sha(bank_dir), "run": stamp,
           "effort": effort, "inputs_sha": inputs, "bar_sha": _bank.bar_sha(bar),
           "bar_since": bar_since, "usage": usage, "repeats": repeats, "splits": split_scores,
           "engine": engine_name, "standard": STANDARD}
    reserved = set(_bank.ENVELOPE_2) | {"gates", "downshift"}
    clash = sorted(set(scores) & reserved)
    if clash:
        raise _bank.BankError("score() keys {} would overwrite the release".format(clash))
    doc.update(scores)
    if gates:
        doc["gates"] = gates
    stamp_fn = getattr(module, "stamp", None)
    if callable(stamp_fn):
        extra = dict(stamp_fn() or {})
        clash = sorted(set(extra) & (reserved | set(scores)))
        if clash:
            raise _bank.BankError("stamp() keys {} would overwrite the release".format(clash))
        doc.update(extra)
    if downshift is not None:
        doc["downshift"] = downshift
    doc["wall_s"] = round(time.monotonic() - t0, 1)
    problems = _bank.envelope_problems(doc)
    if problems:
        raise _bank.BankError("{}: {}".format(bank_dir, "; ".join(problems)))
    _bank._write_release_file(bank_dir, doc)
    if args.json:
        print(json.dumps(doc, sort_keys=True))
    else:
        print(json.dumps(summary, sort_keys=True))
    return 0 if passed else 1
