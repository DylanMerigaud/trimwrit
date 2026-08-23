"""Write a rule into a target file, and never without the case that justifies it.

The comment attached to every rule is not documentation, it is the mitigation.
"Why Does CLAUDE.md Keep Growing? Catastrophic Remembering" (arXiv 2608.11095, 11 August 2026)
measured +226% growth across 1867 repositories and found the asymmetry that causes it: adding an
instruction is cheap, deleting one is frightening, because the deleter cannot tell what the
instruction was protecting against. The same paper measured the fix. Instructions carrying the
reason they exist were removable: 99.3% of the surplus was cut in a controlled test.

So the rule marker carries three things a future reader needs in order to dare delete it: the
case id (run it), the date, and the incident in one line.

    <!-- trimwrit case 0007, 2026-08-23: two mails went to CEOs with no fallback slot -->
"""
import os
import re

from .text import one_line, refuse_bad_dashes, write_text

MARK_OPEN = "trimwrit:"
# Comment syntax by target. Markdown gets HTML comments, everything else gets a hash. The marker
# has to survive being read by a model, so it must look like a comment in the target's own
# language rather than a convention only this tool knows.
COMMENT = {
    ".md": ("<!-- ", " -->"),
    ".markdown": ("<!-- ", " -->"),
}
DEFAULT_COMMENT = ("# ", "")


class IntegrateError(Exception):
    pass


def comment_for(path):
    return COMMENT.get(os.path.splitext(path)[1].lower(), DEFAULT_COMMENT)


def marker(path, rule_id, case_id, date, incident):
    open_c, close_c = comment_for(path)
    return "{}{} {} case {}, {}: {}{}".format(
        open_c, MARK_OPEN, rule_id, case_id, date, one_line(incident), close_c)


def find_rule(text, rule_id):
    """(start, end) line indices of an existing rule block, or None."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if MARK_OPEN in line and " {} case ".format(rule_id) in line:
            j = i + 1
            while j < len(lines) and lines[j].strip():
                j += 1
            return i, j
    return None


def rule_ids(text):
    """Every trimwrit rule id present in a target, in order of appearance."""
    return re.findall(re.escape(MARK_OPEN) + r"\s+(\S+)\s+case\s+(\S+?),", text)


def write_rule(target, rule_id, case_id, date, incident, rule_text, root="."):
    """Append or replace one rule in `target`. Refuses without a case id.

    The refusal is the product. Any editor can add a line to a CLAUDE.md; the thing nobody has
    is a door that will not let an unjustified line through.
    """
    if not case_id:
        raise IntegrateError(
            "a rule with no case is a preference. Generate the case first with `trimwrit case "
            "<correction-id>`, then integrate against it. This tool has exactly one rule of its "
            "own and that is it.")
    if not incident:
        raise IntegrateError(
            "the incident line is mandatory: it is what lets a future reader delete this rule "
            "without fear. Say in one line what went wrong, once, on a real day.")

    rule_text = refuse_bad_dashes(rule_text.strip(), "the rule text")
    path = os.path.join(root, target)
    existing = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            existing = fh.read()

    block = "{}\n{}".format(marker(target, rule_id, case_id, date, incident), rule_text)

    found = find_rule(existing, rule_id)
    if found:
        lines = existing.split("\n")
        start, end = found
        lines[start:end] = block.split("\n")
        new = "\n".join(lines)
        action = "replaced"
    else:
        sep = "" if not existing else ("\n" if existing.endswith("\n\n")
                                       else ("\n\n" if existing.endswith("\n") else "\n\n"))
        new = existing + sep + block + "\n"
        action = "added"

    write_text(path, new, "the rule target {}".format(target))
    return {"target": target, "rule": rule_id, "case": case_id, "action": action}


def drop_rule(target, rule_id, root="."):
    """Remove one rule block. Returns the text that was removed, or None."""
    path = os.path.join(root, target)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    found = find_rule(text, rule_id)
    if not found:
        return None
    lines = text.split("\n")
    start, end = found
    removed = "\n".join(lines[start:end])
    while end < len(lines) and not lines[end].strip():
        end += 1
    del lines[start:end]
    write_text(path, "\n".join(lines), "the rule target {}".format(target))
    return removed


def read_rules(target, root="."):
    """{rule_id: {"case":..., "date":..., "incident":..., "text":...}} for one target."""
    path = os.path.join(root, target)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    out = {}
    pat = re.compile(re.escape(MARK_OPEN) + r"\s+(?P<rule>\S+)\s+case\s+(?P<case>[^,]+),\s*"
                     r"(?P<date>[0-9-]+):\s*(?P<incident>.*?)\s*(?:-->|$)")
    for i, line in enumerate(lines):
        m = pat.search(line)
        if not m:
            continue
        j = i + 1
        body = []
        while j < len(lines) and lines[j].strip():
            body.append(lines[j])
            j += 1
        out[m.group("rule")] = {"case": m.group("case").strip(), "date": m.group("date"),
                                "incident": m.group("incident").strip(),
                                "text": "\n".join(body), "line": i + 1}
    return out
