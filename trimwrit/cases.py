"""Turn a correction into an eval case, in the format `claude plugin eval` already reads.

The format is not ours. `claude plugin eval` (early access as of August 2026) reads
`<eval dir>/**/case.yaml`, or `prompt.md` plus `graders/*.md`, and we write the second of those
verbatim. That choice is the whole distribution bet of this repo: the day the native runner goes
generally available, every case this tool has ever written runs under it with no migration, and
until that day `trimwrit run` reads the same files.

A case has exactly one job: REPLAY the situation that produced the bad output, and check the
correction mechanically. A case that merely asks "do you know the rule" measures recall of a
sentence, not behaviour, and it will pass forever while the behaviour rots.
"""
import os

from . import frontmatter
from .text import (one_line, refuse_bad_dashes, regex_escape_bad_dashes, slug,
                   write_text)

# The grader types the native runner accepts. `trimwrit run` implements the first four plus llm;
# see runner.SUPPORTED. Listing them here keeps the writer honest about what it may emit.
GRADER_TYPES = ("regex", "tool_used", "tool_order", "file_exists", "llm", "baseline")

# Mechanical graders, in the order we prefer them. A judge is the last resort, never the first:
# an llm grader costs money on every run, disagrees with itself across runs, and (worse) will
# happily rate a wrong answer as passing when the rubric is vague. Regex on a forbidden string
# is boring and it is right every time.
MECHANICAL = ("regex", "tool_used", "tool_order", "file_exists")


class CaseError(Exception):
    pass


def case_dir(case_id, title, evals_dir="evals"):
    return os.path.join(evals_dir, "{}-{}".format(case_id, slug(title) or "case"))


def write_case(case_id, title, prompt, graders, evals_dir="evals", tags=(), runs=3,
               max_turns=6, description=None, plugins=None, allowed_tools=None,
               timeout_seconds=None, must_match=(), must_not_match=()):
    """Write `prompt.md` and `graders/*.md`. Returns the case directory.

    `graders` is a list of dicts, each with at least `type` and `name`.

    `must_match`/`must_not_match` apply here as a CASE-WIDE default: any regex grader in
    `graders` that does not already carry its own (set via `forbid_grader`/`require_grader`)
    gets these attached. Most calls through `trimwrit case` write exactly one mechanical
    grader, so a single `--must-match` flag naming the proof for it is the common case; a
    caller building several regex graders with DIFFERENT proofs still can, by passing
    `must_match` on the individual `forbid_grader`/`require_grader` calls instead.
    """
    if not graders:
        raise CaseError(
            "a case with no grader is a prompt, not a case. It can never fail, so it can never "
            "tell you the rule stopped working. Give it --forbid, --require, --tool or --judge.")
    for g in graders:
        if g.get("type") not in GRADER_TYPES:
            raise CaseError("unknown grader type {!r}. Known: {}".format(
                g.get("type"), ", ".join(GRADER_TYPES)))

    d = case_dir(case_id, title, evals_dir)
    name = os.path.basename(d)

    fm = {"name": name}
    if description:
        fm["description"] = one_line(description)
    if tags:
        fm["tags"] = list(tags)
    if plugins:
        fm["plugins"] = list(plugins)
    fm["runs"] = runs
    fm["max_turns"] = max_turns
    if timeout_seconds:
        fm["timeout_seconds"] = timeout_seconds
    if allowed_tools:
        fm["allowed_tools"] = list(allowed_tools)

    body = refuse_bad_dashes(prompt.rstrip(), "the case prompt")
    write_text(os.path.join(d, "prompt.md"),
               "---\n{}\n---\n\n{}\n".format(frontmatter.render(fm), body),
               "the case prompt file")

    for g in graders:
        g = dict(g)
        if g.get("type") == "regex":
            if must_match and "must_match" not in g:
                g["must_match"] = list(must_match)
            if must_not_match and "must_not_match" not in g:
                g["must_not_match"] = list(must_not_match)
        gname = g.pop("name")
        write_text(os.path.join(d, "graders",
                                    "{}.md".format(slug(gname, words=6) or "grader")),
                   _render_grader(gname, g), "the grader file")
    return d


def _render_grader(name, spec):
    """A grader file is front matter plus, optionally, a note saying what it is for.

    The note is not decoration. Six months from now the question about every grader is "why does
    this exist and may I delete it", and the file that cannot answer gets deleted or, worse,
    kept out of fear.
    """
    note = spec.pop("note", None)
    fm = {"type": spec.pop("type"), "name": name}
    fm.update({k: v for k, v in spec.items() if v is not None})
    out = "---\n{}\n---\n".format(frontmatter.render(fm))
    if note:
        out += "\n{}\n".format(one_line(refuse_bad_dashes(note, "the grader note")))
    return out


def forbid_grader(pattern, name=None, note=None, flags="i", target="last_message",
                  must_match=(), must_not_match=()):
    """The workhorse. Most corrections are "stop doing X", and "stop doing X" is a regex.

    `must_match`/`must_not_match` are the grader's proof that its own pattern can fire: see
    `trimwrit check`. For a `not_contains` grader like this one, a `must_match` example is text
    the run would FAIL on, which is exactly what proves the pattern is still connected. Naming
    them the same as the outcome they cause on a real run (not the outcome of the CHECK) is
    deliberate: the semantics are about the PATTERN, never about the verdict.
    """
    spec = {"type": "regex", "name": name or "forbids-{}".format(slug(pattern, 4) or "pattern"),
            "pattern": regex_escape_bad_dashes(pattern), "match": "not_contains",
            "flags": flags, "target": target, "note": note}
    if must_match:
        spec["must_match"] = list(must_match)
    if must_not_match:
        spec["must_not_match"] = list(must_not_match)
    return spec


def require_grader(pattern, name=None, note=None, flags="i", target="last_message",
                   must_match=(), must_not_match=()):
    spec = {"type": "regex", "name": name or "requires-{}".format(slug(pattern, 4) or "pattern"),
            "pattern": regex_escape_bad_dashes(pattern), "match": "contains",
            "flags": flags, "target": target, "note": note}
    if must_match:
        spec["must_match"] = list(must_match)
    if must_not_match:
        spec["must_not_match"] = list(must_not_match)
    return spec


def tool_grader(tool, name=None, note=None, min_calls=1, max_calls=None, input_match=None):
    spec = {"type": "tool_used", "name": name or "uses-{}".format(slug(tool, 3)),
            "tool": tool, "min": min_calls, "note": note}
    if max_calls is not None:
        spec["max"] = max_calls
    if input_match:
        spec["input_match"] = input_match
    return spec


def judge_grader(criteria, name="judge", note=None, focus="last_message"):
    return {"type": "llm", "name": name, "criteria": criteria, "focus": focus, "note": note}


def is_mechanical(graders):
    return any(g.get("type") in MECHANICAL for g in graders)


# ---------------------------------------------------------------- reading cases back


class Case(object):
    def __init__(self, path, meta, prompt, graders):
        self.path = path
        self.meta = meta
        self.prompt = prompt
        self.graders = graders

    @property
    def name(self):
        return self.meta.get("name") or os.path.basename(self.path)

    @property
    def runs(self):
        return int(self.meta.get("runs", 3))

    @property
    def max_turns(self):
        return int(self.meta.get("max_turns", 10))

    @property
    def timeout_seconds(self):
        return int(self.meta.get("timeout_seconds", 300))

    @property
    def tags(self):
        t = self.meta.get("tags") or []
        return list(t) if isinstance(t, (list, tuple)) else [t]

    def __repr__(self):
        return "<Case {} graders={}>".format(self.name, len(self.graders))


def load_case(path):
    """Read one case directory. `prompt.md` plus `graders/*.md`, the native layout."""
    prompt_path = os.path.join(path, "prompt.md")
    if not os.path.exists(prompt_path):
        raise CaseError("{} has no prompt.md".format(path))
    with open(prompt_path, encoding="utf-8") as fh:
        meta, body = frontmatter.split(fh.read())

    graders = []
    gdir = os.path.join(path, "graders")
    if os.path.isdir(gdir):
        for fname in sorted(os.listdir(gdir)):
            if not fname.endswith(".md"):
                continue
            with open(os.path.join(gdir, fname), encoding="utf-8") as fh:
                gmeta, gbody = frontmatter.split(fh.read())
            if "type" not in gmeta:
                raise CaseError("{}: grader front matter has no `type`".format(fname))
            gmeta.setdefault("name", fname[:-3])
            gmeta["_note"] = gbody.strip()
            graders.append(gmeta)
    if not graders:
        raise CaseError(
            "{} has no graders. It would report PASS on every run including the runs where the "
            "model does the exact thing the case exists to catch.".format(path))
    return Case(path, meta, body.strip(), graders)


def discover(evals_dir="evals"):
    """Every case under `evals_dir`, sorted. A directory holding prompt.md is a case."""
    out = []
    if not os.path.isdir(evals_dir):
        return out
    for root, dirs, files in os.walk(evals_dir):
        dirs[:] = [d for d in sorted(dirs) if d not in ("results", "graders")]
        if "prompt.md" in files:
            out.append(load_case(root))
    return sorted(out, key=lambda c: c.path)
