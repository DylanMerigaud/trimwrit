"""Whether a grader can prove it can fire, before any run trusts it.

The incident this exists for: a suite of 32 prompt injection cases, generated in the native
case format, was run through this repo's own runner. The second pass reported 32 of 32
resisted. The graders were dead. The generator wrote regex patterns with `json.dumps`, which
emits a double quoted YAML scalar with doubled backslashes, and `frontmatter._scalar` strips
the quotes without unescaping them, so every pattern containing `\\s` reached `re.compile` as a
literal backslash and matched nothing. A green board, and the instrument was disconnected. The
runner's own docstring warns against exactly this ("a grader that never fires is an instrument
that always says PASS") and it still happened, because nothing forced a grader to PROVE it can
fail before a run trusted it.

`check_case` is that proof. A regex grader may carry `must_match` and `must_not_match`, lists of
example texts the pattern is checked against directly, with no model call. The semantics are
about the PATTERN, never about the verdict a grader produces on a real run: for a
`not_contains` grader, a `must_match` example is text the run would FAIL on, and that is exactly
what proves the grader is connected.

This module never decides whether a problem should block a run. It classifies each one as a
hard `"failure"` (an unsupported grader type, a pattern that will not compile, an example that
contradicts what the grader claims to check) or an `"unproven"` grader (a regex grader with no
example at all, which is not wrong, just unverified), and the caller (`trimwrit check`,
`trimwrit run --strict`) decides what to do with each kind.
"""
import re

from . import runner as runner_mod

FAILURE = "failure"
UNPROVEN = "unproven"


def check_case(case):
    """Every problem found in `case`'s graders. [{"case", "grader", "kind", "reason"}, ...].

    A pure function on purpose: whether an "unproven" entry should refuse the case is a policy
    question (plain `check`/`run` warn about it, `--strict` refuses on it), not something this
    function can answer without knowing who is asking.
    """
    case_name = _case_name(case)
    problems = []
    for spec in case.graders:
        gtype = spec.get("type")
        name = spec.get("name") or "?"
        if gtype not in runner_mod.SUPPORTED:
            problems.append(_problem(case_name, name, FAILURE,
                                     "unsupported grader type {!r}".format(gtype)))
            continue
        if gtype != "regex":
            # must_match/must_not_match are about a PATTERN. A tool_used or file_exists grader
            # has no pattern to prove, and an llm grader's "proof" would be a real model call,
            # which is the expensive, wobbly thing this mechanical check exists to avoid.
            continue

        pattern_src = spec.get("pattern")
        if pattern_src is None:
            problems.append(_problem(case_name, name, FAILURE, "regex grader has no pattern"))
            continue
        try:
            pattern = re.compile(pattern_src, runner_mod._flags(spec))
        except re.error as exc:
            problems.append(_problem(case_name, name, FAILURE,
                                     "pattern does not compile: {}".format(exc)))
            continue

        must_match = spec.get("must_match") or []
        must_not_match = spec.get("must_not_match") or []
        # A sample is a whole answer, and the pattern sees exactly what a run would give it:
        # on `target: outbox` that is the inside of the outbox blocks and nothing else, so the
        # right must_not_match for a forbid grader is a correct answer that QUOTES the forbidden
        # thing outside the block while refusing it. Proving on the raw sample instead would
        # pass a pattern the run then fires on, which is the 0.3.0 dead-grader incident with the
        # sign flipped.
        for text in must_match:
            if not pattern.search(runner_mod.extract_target(spec, text)):
                problems.append(_problem(
                    case_name, name, FAILURE,
                    "must_match failed, the pattern did not match: {!r}".format(_short(text))))
        for text in must_not_match:
            if pattern.search(runner_mod.extract_target(spec, text)):
                problems.append(_problem(
                    case_name, name, FAILURE,
                    "must_not_match failed, the pattern matched: {!r}".format(_short(text))))

        if not must_match and not must_not_match:
            problems.append(_problem(
                case_name, name, UNPROVEN,
                "unproven grader: nothing shows this pattern can fire"))
    return problems


def _short(text, limit=80):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "..."


def _case_name(case):
    import os
    return getattr(case, "name", None) or os.path.basename(case.path)


def _problem(case_name, grader, kind, reason):
    return {"case": case_name, "grader": grader, "kind": kind, "reason": reason}
