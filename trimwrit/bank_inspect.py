"""The optional Inspect AI engine of a standard bank: `run.py --engine inspect`.

Inspect AI is never a dependency of trimwrit: this module imports `inspect_ai` inside its
functions only, and nothing in trimwrit imports this module at load time (bank_run.py reaches it
through importlib when `--engine inspect` is asked). Install it in a separate venv:
`python3 -m venv v && v/bin/pip install inspect-ai`, then `v/bin/python <bank>/run.py --engine
inspect`.

`run(cases, call, jobs, log_dir=None)` keeps the contract of bank_run.run_local: one
(case, got, error) per case, in the order of `cases`; an exception in `call` is the error text
and got None. Inspect only schedules the calls (max_samples = jobs) and writes its log: each
case is one Sample, the solver runs `call(case)` on a worker thread (asyncio.to_thread, so
note_usage still attributes the case's model calls to it), and the scorer records the answer
without grading it (the grade stays the bank's). The model handed to Inspect is
`mockllm/model`: the bank's own answer calls its model, Inspect never does.

`LAST_LOG` is the path of the log the last run wrote (None before any run or when Inspect
wrote none); bank.main writes it as `engine_log` in the release.
"""
import json
import os
import tempfile

LAST_LOG = None
NO_SAMPLE = "inspect returned no sample for {}"


def _error_text(exc):
    return "{}: {}".format(type(exc).__name__, exc)


def _json(got):
    try:
        return json.dumps(got, sort_keys=True, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):
        return repr(got)


def available():
    """None when inspect_ai imports, else the text of the ImportError."""
    try:
        import inspect_ai  # noqa: F401, PLC0415
    except ImportError as exc:
        return str(exc) or "inspect_ai cannot be imported"
    return None


def run(cases, call, jobs, log_dir=None):
    """`call` mapped over `cases` through one Inspect eval; [(case, got, error)] in the order
    of `cases`. A sample the log does not hold is an error row (NO_SAMPLE)."""
    global LAST_LOG
    import asyncio  # noqa: PLC0415

    import inspect_ai  # noqa: PLC0415
    from inspect_ai.dataset import MemoryDataset, Sample  # noqa: PLC0415
    from inspect_ai.scorer import Score, mean, scorer  # noqa: PLC0415
    from inspect_ai.solver import solver  # noqa: PLC0415

    cases = list(cases)
    LAST_LOG = None
    if not cases:
        return []
    # the answers themselves stay in this process (the log holds their JSON text): a got keeps
    # its Python type, as run_local gives it
    answers = {}

    @solver(name="trimwrit_bank_answer")
    def answer_case():
        async def solve(state, generate):
            idx = state.metadata["idx"]
            try:
                got = await asyncio.to_thread(call, cases[idx])
            except Exception as exc:  # noqa: BLE001  (one failed answer is a wrong row)
                answers[idx] = (None, _error_text(exc))
                state.metadata["error"] = _error_text(exc)
                state.metadata["got"] = None
            else:
                answers[idx] = (got, None)
                state.metadata["got"] = _json(got)
            return state
        return solve

    @scorer(name="trimwrit_bank_record", metrics=[mean()])
    def record():
        async def score(state, target):
            return Score(value=1, answer=state.metadata.get("got"),
                         explanation=state.metadata.get("error"))
        return score

    dataset = MemoryDataset([Sample(id=str(case.get("id")), input=str(case.get("id")),
                                    metadata={"idx": i})
                             for i, case in enumerate(cases)])
    task = inspect_ai.Task(dataset=dataset, solver=answer_case(), scorer=record(),
                           name="trimwrit_bank")
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    else:
        log_dir = tempfile.mkdtemp(prefix="trimwrit-inspect-")
    logs = inspect_ai.eval(task, model="mockllm/model", max_samples=max(1, int(jobs or 1)),
                           log_dir=log_dir, display="none")
    seen = set()
    for log in logs or []:
        if getattr(log, "location", None):
            LAST_LOG = str(log.location)
        for sample in getattr(log, "samples", None) or []:
            seen.add(str(sample.id))
    out = []
    for i, case in enumerate(cases):
        if str(case.get("id")) in seen and i in answers:
            got, error = answers[i]
            out.append((case, got, error))
        else:
            out.append((case, None, NO_SAMPLE.format(case.get("id"))))
    return out
