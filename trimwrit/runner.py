"""A local runner for the native case format, and the ablation that makes a case mean something.

Why this exists at all, given that `claude plugin eval` exists:

1. It is gated. On this machine, Claude Code 2.1.241, `claude plugin eval .` answers
   "`plugin eval` is currently in early access" and runs nothing. A tool whose whole promise is
   "the rule is backed by a test" cannot ship with the test runner behind someone else's flag.
2. It cannot ablate the target we care most about. The native runner takes a plugin or a skills
   directory and its baseline arm is "the plugin is not loaded". The single most common place a
   harness rule actually lives is a CLAUDE.md, and no plugin flag will ever remove one. This
   runner ablates a FILE: it runs the case in a scratch directory with the rule file present,
   then again with it absent.

Both runners read the same files, so nothing here has to be undone when the native one opens up.

THE ABLATION IS THE POINT. A case run only with the rule in place tells you the model behaved,
not that the rule caused it. A case that passes in both arms is measuring nothing: either the
model already behaved without being told, or the grader cannot fail. Either way the rule is
unearned and `trimwrit prune` will say so.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

# What this runner can actually score. A grader type outside this set does NOT quietly pass:
# it is reported as unsupported and it fails the case, because the alternative is a suite that
# reports green while a third of its graders never ran.
SUPPORTED = ("regex", "file_exists", "tool_used", "tool_order", "llm")

# THE MOST IMPORTANT FLAG IN THIS FILE.
#
# Claude Code loads the user's own ~/.claude/CLAUDE.md into every session regardless of the
# working directory. Without this flag the `without` arm is not a baseline: it still carries
# whatever the operator has already told Claude, and a rule that duplicates one of those lines
# measures +0.00 and looks inert when it is simply redundant with a file the runner cannot see.
#
# This was not a theory. The first ablation run of this repo's own dogfood suite reported the
# em-dash case and the time-estimate case as INERT at exactly 1.00 in both arms, and the reason
# was that the machine running it has both rules in its user memory already. `--setting-sources
# project,local` drops the user layer and keeps the project layer, which is where the ablated
# file lives. Measured both ways before it was made the default: with a local CLAUDE.md present
# the model reports the rule, with the file absent it reports no such rule.
ISOLATE_ARGS = ("--setting-sources", "project,local")

ARM_WITH = "with"
ARM_WITHOUT = "without"

# Python's re does not take JS RegExp flags. Only the ones that mean the same thing in both
# engines are honoured; the rest are ignored on purpose rather than guessed at.
_FLAG_MAP = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}


class RunnerError(Exception):
    pass


class Result(object):
    def __init__(self, case, arm, run_index, final_text, tools, files, error=None):
        self.case = case
        self.arm = arm
        self.run_index = run_index
        self.final_text = final_text or ""
        self.tools = tools or []
        self.files = files or {}
        self.error = error
        self.grades = []

    @property
    def passed(self):
        return bool(self.grades) and all(g["passed"] for g in self.grades) and not self.error

    @property
    def score(self):
        if self.error or not self.grades:
            return 0.0
        total = sum(float(g.get("weight", 1)) for g in self.grades)
        got = sum(float(g.get("weight", 1)) for g in self.grades if g["passed"])
        return got / total if total else 0.0


def _flags(spec):
    out = 0
    for ch in str(spec.get("flags", "")):
        out |= _FLAG_MAP.get(ch, 0)
    return out


def _target_text(spec, result):
    """Which haystack a grader looks at. Defaults to the final assistant message, same as the
    native runner."""
    target = spec.get("target") or spec.get("focus") or "last_message"
    if target == "last_message":
        return result.final_text
    if target == "trace":
        return json.dumps(result.tools, ensure_ascii=False) + "\n" + result.final_text
    if target == "files":
        return "\n".join(sorted(result.files))
    if isinstance(target, str) and target.startswith("file:"):
        return "\n".join(v for k, v in sorted(result.files.items())
                         if _glob_match(target[5:], k))
    raise RunnerError("unsupported grader target {!r}".format(target))


def _glob_match(pattern, name):
    import fnmatch
    return fnmatch.fnmatch(name, pattern)


def grade_regex(spec, result):
    pattern = spec["pattern"]
    hay = _target_text(spec, result)
    hits = len(re.findall(pattern, hay, _flags(spec)))
    match = str(spec.get("match", "contains"))
    if match == "contains":
        ok, detail = hits > 0, "{} hit(s)".format(hits)
    elif match == "not_contains":
        ok, detail = hits == 0, "{} hit(s), wanted none".format(hits)
    elif match.startswith("count:"):
        want = int(match.split(":", 1)[1])
        ok, detail = hits == want, "{} hit(s), wanted {}".format(hits, want)
    else:
        raise RunnerError("unsupported regex match mode {!r}".format(match))
    return ok, detail


def grade_file_exists(spec, result):
    want = spec.get("exists", True)
    found = [f for f in result.files if _glob_match(spec["path"], f)]
    ok = bool(found) if want else not found
    return ok, "{} file(s) matching {}".format(len(found), spec["path"])


def grade_tool_used(spec, result):
    name = spec["tool"]
    calls = [t for t in result.tools if t.get("name") == name]
    if spec.get("input_match"):
        calls = [t for t in calls
                 if re.search(spec["input_match"], json.dumps(t.get("input", {})), re.I)]
    n = len(calls)
    lo = int(spec.get("min", 1))
    hi = spec.get("max")
    ok = n >= lo and (hi is None or n <= int(hi))
    return ok, "{} call(s) to {}, wanted min {}{}".format(
        n, name, lo, " max {}".format(hi) if hi is not None else "")


def grade_tool_order(spec, result):
    names = [t.get("name") for t in result.tools]
    before, after = spec["before"], spec["after"]
    if before not in names or after not in names:
        return False, "need both {} and {} in the trace".format(before, after)
    return names.index(before) < names.index(after), "{} then {}".format(before, after)


def grade_llm(spec, result, judge=None):
    """A judge, only where nothing mechanical reaches. It is the expensive, wobbly grader and it
    is last for that reason."""
    if judge is None:
        return None, "no judge available"
    verdict = judge(spec.get("criteria", ""), _target_text(spec, result))
    return verdict, "judge said {}".format("PASS" if verdict else "FAIL")


GRADERS = {"regex": grade_regex, "file_exists": grade_file_exists,
           "tool_used": grade_tool_used, "tool_order": grade_tool_order}


def apply_graders(case, result, judge=None):
    for spec in case.graders:
        gtype = spec.get("type")
        name = spec.get("name")
        weight = float(spec.get("weight", 1))
        if gtype == "llm":
            ok, detail = grade_llm(spec, result, judge)
            if ok is None:
                result.grades.append({"name": name, "type": gtype, "passed": False,
                                      "weight": weight,
                                      "detail": "llm grader needs a judge and none was available"})
                continue
        elif gtype in GRADERS:
            try:
                ok, detail = GRADERS[gtype](spec, result)
            except (KeyError, RunnerError) as exc:
                ok, detail = False, "grader is malformed: {}".format(exc)
        else:
            # Not silently skipped. A grader this runner cannot score is a hole in the
            # measurement, and a hole in the measurement has to look like a failure.
            ok, detail = False, "grader type {!r} is not supported by the local runner".format(gtype)
        result.grades.append({"name": name, "type": gtype, "passed": bool(ok),
                              "weight": weight, "detail": detail})
    return result


# ---------------------------------------------------------------- driving claude


def _parse_stream(raw):
    """Pull the final assistant text and the tool calls out of a stream-json transcript."""
    final, tools = "", []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "assistant":
            for block in ev.get("message", {}).get("content", []) or []:
                if block.get("type") == "tool_use":
                    tools.append({"name": block.get("name"), "input": block.get("input")})
                elif block.get("type") == "text" and block.get("text", "").strip():
                    final = block["text"]
        elif ev.get("type") == "result" and ev.get("result"):
            final = ev["result"]
    return final, tools


def run_once(case, arm, run_index, rule_files, extra_args=(), claude="claude", cwd_seed=None,
             isolate=True):
    """One run of one case in one arm, in a scratch directory.

    The scratch directory is the ablation. In the `with` arm the rule files are written into it,
    in the `without` arm they are not, and nothing else differs.
    """
    workdir = tempfile.mkdtemp(prefix="trimwrit-")
    try:
        if cwd_seed and os.path.isdir(cwd_seed):
            for entry in os.listdir(cwd_seed):
                src = os.path.join(cwd_seed, entry)
                dst = os.path.join(workdir, entry)
                shutil.copytree(src, dst) if os.path.isdir(src) else shutil.copy2(src, dst)
        if arm == ARM_WITH:
            for relpath, content in rule_files.items():
                dest = os.path.join(workdir, relpath)
                parent = os.path.dirname(dest)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(dest, "w", encoding="utf-8") as fh:
                    fh.write(content)

        before = _snapshot(workdir)
        cmd = [claude, "-p", case.prompt, "--output-format", "stream-json", "--verbose",
               "--max-turns", str(case.max_turns)]
        if isolate:
            cmd.extend(ISOLATE_ARGS)
        cmd.extend(extra_args)
        try:
            proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True,
                                  timeout=case.timeout_seconds)
        except subprocess.TimeoutExpired:
            return Result(case, arm, run_index, "", [], {},
                          error="timed out after {}s".format(case.timeout_seconds))
        except OSError as exc:
            return Result(case, arm, run_index, "", [], {},
                          error="could not run {}: {}".format(claude, exc))
        if proc.returncode != 0 and not proc.stdout.strip():
            return Result(case, arm, run_index, "", [], {},
                          error="claude exited {}: {}".format(proc.returncode,
                                                              proc.stderr.strip()[:200]))
        final, tools = _parse_stream(proc.stdout)
        files = _created(workdir, before)
        return Result(case, arm, run_index, final, tools, files)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _snapshot(root):
    out = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in filenames:
            out.add(os.path.relpath(os.path.join(dirpath, f), root))
    return out


def _created(root, before):
    """Files the run created, with their contents. Bounded, because a runaway run should not be
    able to load a repository's worth of text into memory to satisfy one grader."""
    out = {}
    for rel in sorted(_snapshot(root) - before):
        try:
            with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
                out[rel] = fh.read(200000)
        except OSError:
            out[rel] = ""
    return out


def make_judge(claude="claude", model=None):
    """A judge built on `claude -p`. Returns None when the binary is missing, and the caller
    turns that into a failing grader rather than a silent pass."""
    if not shutil.which(claude):
        return None

    def judge(criteria, text):
        prompt = ("You are grading one output against one rule. Answer with exactly PASS or "
                  "FAIL and nothing else.\n\nRULE:\n{}\n\nOUTPUT:\n{}".format(criteria, text))
        cmd = [claude, "-p", prompt, "--output-format", "json"]
        cmd.extend(ISOLATE_ARGS)
        if model:
            cmd.extend(["--model", model])
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            payload = json.loads(proc.stdout or "{}")
            answer = str(payload.get("result", "")).strip().upper()
        except (ValueError, OSError, subprocess.TimeoutExpired):
            return False
        return answer.startswith("PASS")

    return judge


def run_case(case, rule_files, arms=(ARM_WITH, ARM_WITHOUT), runs=None, claude="claude",
             judge=None, extra_args=(), cwd_seed=None, progress=None, isolate=True):
    """Run one case across the requested arms. Returns {arm: [Result, ...]}."""
    n = runs or case.runs
    out = {}
    for arm in arms:
        out[arm] = []
        for i in range(n):
            if progress:
                progress(case, arm, i + 1, n)
            res = run_once(case, arm, i, rule_files, extra_args, claude, cwd_seed, isolate)
            apply_graders(case, res, judge)
            out[arm].append(res)
    return out


def summarise(per_arm):
    """The two numbers that decide whether a rule keeps its place.

    `delta` is the whole verdict: score with the rule minus score without it. At or below zero
    the rule changed nothing that the case can see.
    """
    def mean(rs):
        return sum(r.score for r in rs) / len(rs) if rs else 0.0
    with_score = mean(per_arm.get(ARM_WITH, []))
    without_score = mean(per_arm.get(ARM_WITHOUT, []))
    return {"with": with_score, "without": without_score,
            "delta": with_score - without_score,
            "runs": {a: len(v) for a, v in per_arm.items()}}
