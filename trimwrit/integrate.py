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

A rule may cite SEVERAL cases, joined with `+`: `case 0001+0002`. That is not a convenience, it
is what the first real use of this tool asked for within the hour. One rule about a forbidden
character needs one case that replays it in prose and another that replays it in a commit
message, and a marker that holds a single id forces you to either drop a good case or leave it
showing up forever as unused.
"""
import hashlib
import json
import os
import re

from . import preamble
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


class OverBudget(IntegrateError):
    """The write would push an always-loaded file past its budget in `.trimwrit/budget.json`."""


class RuleTooLong(IntegrateError):
    """The rule block for an always-loaded file is longer than MAX_RULE_LINES."""


class NoDoor(IntegrateError):
    """A rule for an always-loaded file names no door, or a door that does not exist."""


class NoDescription(IntegrateError):
    """A SKILL.md target has no frontmatter description, so the skill can never be picked."""


# A rule kept in a file that loads every turn is its imperative, the name of its door and its
# marker. Three lines is what one imperative with its boundary and its exception needs; past
# that the text is a story, and a story is paid for on every turn of every session. A line is
# counted per LINE_CHARS characters, so one giant line cannot dodge the count.
MAX_RULE_LINES = 3
LINE_CHARS = 100
SKILL_FILE = "SKILL.md"
PROJECT_FILE = "CLAUDE.md"
PLUGIN_REGISTRY = os.path.join("plugins", "installed_plugins.json")
ROUTE = ("its story goes to the repository's history doc, and a rule that only matters in one "
         "situation goes to the SKILL.md of that situation (`trimwrit relocate <rule> --to "
         "<file>` moves one with its marker)")


def comment_for(path):
    return COMMENT.get(os.path.splitext(path)[1].lower(), DEFAULT_COMMENT)


CASE_SEP = "+"


def case_list(case_id):
    """`"0001+0002"` to `["0001", "0002"]`. One id stays a one element list."""
    return [c.strip() for c in str(case_id).split(CASE_SEP) if c.strip()]


def marker(path, rule_id, case_id, date, incident):
    open_c, close_c = comment_for(path)
    return "{}{} {} case {}, {}: {}{}".format(
        open_c, MARK_OPEN, rule_id, CASE_SEP.join(case_list(case_id)), date,
        one_line(incident), close_c)


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
    # The short form `trimwrit: R0020 case 0020 -->` (incident moved out to a history doc) is a
    # rule too: growth-cockpit's two CLAUDE.md files carried 13 of them on 2026-09-30 and this
    # reader counted zero, so prune reported every one of their cases as unused.
    return re.findall(re.escape(MARK_OPEN) + r"\s+(\S+)\s+case\s+([^,\s]+)", text)


def write_rule(target, rule_id, case_id, date, incident, rule_text, root=".", user_dir=None,
               door=None):
    """Append or replace one rule in `target`. Refuses without a case id.

    The refusal is the product. Any editor can add a line to a CLAUDE.md; the thing nobody has
    is a door that will not let an unjustified line through.

    Into an always-loaded file (see `always_loaded`) the rule must also name its `door`, the
    file or plugin that enforces it, and fit in MAX_RULE_LINES once the door is named. A
    SKILL.md needs a frontmatter description instead: it is what makes the skill fire.
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

    head = ""
    if os.path.basename(target) == SKILL_FILE:
        head, rule_text = _skill_head(target, existing, rule_text)
    elif always_loaded(root, target, user_dir):
        rule_text = _with_door(rule_text, _door_name(door, root, user_dir), door)
        _length_door(target, rule_text)
        rule_text = refuse_bad_dashes(rule_text, "the rule text")

    block = "{}\n{}".format(marker(target, rule_id, case_id, date, incident), rule_text)
    old_text, existing = existing, existing or head

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

    _budget_door(target, root, user_dir, path, old_text, new)
    write_text(path, new, "the rule target {}".format(target))
    return {"target": target, "rule": rule_id, "case": case_id, "action": action}


def always_loaded(root, target, user_dir=None):
    """True when `target` is read into every session: a piece of the preamble, any file named
    CLAUDE.md (a nested one loads with every file read under it), or a budgeted key."""
    if os.path.basename(target) == PROJECT_FILE:
        return True
    if preamble.piece_name(root, target, user_dir) is not None:
        return True
    try:
        return preamble.budget_for(root, target, user_dir) is not None
    except ValueError as exc:
        raise IntegrateError(str(exc))


def rule_lines(text):
    """The lines `text` costs, a line longer than LINE_CHARS counting once per LINE_CHARS."""
    return sum(-(-len(line) // LINE_CHARS) for line in text.split("\n") if line.strip())


def _length_door(target, rule_text):
    n = rule_lines(rule_text)
    if n > MAX_RULE_LINES:
        raise RuleTooLong(
            "rule-too-long: {} loads every turn, and this rule is {} lines (at most {}, a line "
            "counted per {} characters). Keep there the imperative and the name of its door; "
            "{}.".format(target, n, MAX_RULE_LINES, LINE_CHARS, ROUTE))


def _registered_plugins(user_dir):
    """The plugin names (without @marketplace) in Claude Code's installed_plugins.json."""
    path = os.path.join(preamble._user_dir(user_dir), PLUGIN_REGISTRY)
    try:
        with open(path, encoding="utf-8") as fh:
            keys = json.load(fh).get("plugins") or {}
    except (OSError, ValueError, AttributeError):
        return set(), set()
    return set(keys), {k.split("@", 1)[0] for k in keys}


def _door_name(door, root, user_dir):
    """The short name a reader greps for: the path's basename, or the plugin name.

    A rule that names no enforcing file or plugin is a wish: nothing refuses the behaviour it
    describes, so the line will be paid for on every turn and broken anyway. The door has to
    exist, so a typo cannot pass for a door.
    """
    if not door or not door.strip():
        raise NoDoor("no-door: a rule in an always-loaded file names the file or plugin that "
                     "enforces it. Pass --door <path or plugin>.")
    door = door.strip()
    path = os.path.expanduser(door)
    if not os.path.isabs(path):
        path = os.path.join(root, path)
    if os.path.exists(path):
        return os.path.basename(os.path.normpath(door))
    full, names = _registered_plugins(user_dir)
    name = door.split("@", 1)[0]
    local = os.path.isdir(os.path.join(root, "plugins", name, ".claude-plugin"))
    if door in full or ("@" not in door and name in names) or local:
        return name
    raise NoDoor("no-door: {} is neither a path under {} nor an installed plugin (looked in {} "
                 "and plugins/<name>/.claude-plugin). Name a door that exists.".format(
                     door, root, os.path.join(preamble._user_dir(user_dir), PLUGIN_REGISTRY)))


def _with_door(rule_text, name, door):
    """`rule_text`, with " Door: <door>." added when it does not already name its door."""
    if name in rule_text:
        return rule_text
    return "{} Door: {}.".format(rule_text, door.strip())


_DESCRIPTION = re.compile(r"^description:[ \t]*(.*)$", re.M)


def _has_description(text):
    """True when `text` opens with a frontmatter block holding a non-empty description."""
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end == -1:
        return False
    block = text[3:end]
    m = _DESCRIPTION.search(block)
    if not m:
        return False
    value = m.group(1).strip().strip("\"'").strip()
    if value in ("|", ">", "|-", ">-", ""):
        rest = block[m.end():].split("\n")[1:]
        return bool(rest) and rest[0][:1] in (" ", "\t") and bool(rest[0].strip())
    return True


def _skill_head(target, existing, rule_text):
    """(head, rule text) for a SKILL.md, refused when the skill has no description.

    A skill is picked by its description alone: a rule written into a SKILL.md the model never
    loads is a rule nobody reads. A new file may carry its frontmatter in the rule text; it is
    then written first, above the marker, where Claude Code looks for it.
    """
    source = existing or rule_text
    if not _has_description(source):
        raise NoDescription(
            "no-description: {} has no frontmatter `description`, and a skill is picked by its "
            "description alone. Open the file with ---, `description: <when it fires>`, ---."
            .format(target))
    if existing:
        return "", rule_text
    end = rule_text.find("\n---", 3)
    close = rule_text.find("\n", end + 4)
    if close == -1:
        return rule_text + "\n", ""
    return rule_text[:close + 1], rule_text[close + 1:].strip()

def _budget_door(target, root, user_dir, path, old_text, new_text):
    """Refuse a write that grows a budgeted, always-loaded piece past its budget.

    The size is the piece's AFTER the write (every rules file counts toward `~/.claude/rules`).
    Rewording a rule shorter is always let through, even when the file is still over: it does
    not grow, and refusing it would block the very edit that brings the file back down.
    """
    try:
        found = preamble.budget_for(root, target, user_dir)
    except ValueError as exc:
        raise IntegrateError(str(exc))
    if not found:
        return
    key, budget = found
    real = os.path.realpath(path)
    others = 0
    for other in preamble.piece_files(root, target, user_dir):
        if os.path.realpath(other) != real and os.path.exists(other):
            with open(other, encoding="utf-8") as fh:
                others += preamble.tokens(fh.read())
    before = others + preamble.tokens(old_text)
    after = others + preamble.tokens(new_text)
    if after > budget and after > before:
        raise OverBudget(
            "over-budget: {} would go from {} to {} tokens, budget {} ({}). Put the rule "
            "instead in a skill of its situation (a SKILL.md whose description names the "
            "trigger), or the story in the repository's history doc, keeping in the "
            "always-loaded file one line naming the door.".format(
                key, before, after, budget, preamble.BUDGET_FILE))


def _cut(text, rule_id):
    """(text without the block, the block) for one rule, or None when `text` has no such rule.

    The block is the marker line down to the first blank line; the blank lines after it go with
    it, so the neighbours do not end up separated by a double gap.
    """
    found = find_rule(text, rule_id)
    if not found:
        return None
    lines = text.split("\n")
    start, end = found
    removed = "\n".join(lines[start:end])
    while end < len(lines) and not lines[end].strip():
        end += 1
    del lines[start:end]
    return "\n".join(lines), removed


def strip_rules(text, rule_ids):
    """(new text, ids actually removed): `text` with each named rule block taken out.

    Pure, so the rule level ablation (`--ablate rule`) can build the `without` arm's file with no
    disk write: the measurement must be about the rule, and the same CLAUDE.md minus exactly the
    citing blocks is the only difference between the two arms. An id the text does not hold is
    skipped, not an error. Everything outside the removed blocks stays byte for byte.
    """
    removed_ids = []
    for rule_id in rule_ids:
        cut = _cut(text, rule_id)
        if cut:
            text = cut[0]
            removed_ids.append(rule_id)
    return text, removed_ids


def drop_rule(target, rule_id, root="."):
    """Remove one rule block. Returns the text that was removed, or None."""
    path = os.path.join(root, target)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    cut = _cut(text, rule_id)
    if not cut:
        return None
    write_text(path, cut[0], "the rule target {}".format(target))
    return cut[1]


def citing_rules(target, case_base, root=".", live=None):
    """The ids of the rules in `target` whose marker cites the case `case_base`.

    A marker cites a case by its leading number (`0027`) or by the full directory name
    (`0027-sample-case`); `case 0270` does not cite `0027`, because the ids are
    compared whole, never as prefixes. A multi case marker (`0065+0066`) cites each.

    `live` (the rule ids promoted in `--root`'s ledger, see ledger.live_rules) keeps only the
    markers this root owns: a rule promoted in another ledger cites a case number of THAT
    root, and the same number here is another case (see `foreign`). None keeps every marker.
    """
    wanted = {case_base.split("-", 1)[0], case_base}
    return [rule_id for rule_id, info in read_rules(target, root).items()
            if wanted & set(case_list(info["case"]))
            and (live is None or rule_id in live)]


def foreign(targets, root=".", live=None):
    """[{"target", "rule", "case"}] for every marker in `targets` whose rule is not in `live`.

    The incident (review of 0.9.1): the global ~/.claude/CLAUDE.md holds `R0002 case 0002`,
    promoted in the global ledger, whose case 0002 is a dash case; one repository measured with
    that file as a target has its own, unrelated 0002. Matched by number, `--ablate rule` ran
    the repository's case against the global rule, and an inert verdict let `prune --apply`
    delete the global rule and write the removal into the wrong ledger. A foreign marker is
    listed, never claimed, never deleted, never an orphan. `live` None means none is foreign.
    """
    if live is None:
        return []
    out = []
    for target in targets:
        for rule_id, info in sorted(read_rules(os.path.expanduser(target), root).items(),
                                    key=lambda kv: kv[1]["line"]):
            if rule_id not in live:
                out.append({"target": target, "rule": rule_id, "case": info["case"]})
    return out


def rule_block(text, rule_id):
    """The text of one rule block, marker line down to the first blank line, or None.

    The same bounds `_cut` (so `strip_rules` and `drop_rule`) removes, without the blank lines
    after it: the block is what the rule level ablation takes out, so it is what a verdict is
    about, and what `rules_sha256` hashes.
    """
    found = find_rule(text, rule_id)
    if not found:
        return None
    start, end = found
    return "\n".join(text.split("\n")[start:end])


def rules_sha256(targets, case_base, root=".", live=None):
    """{rule_id: sha256 of its block} for every rule citing `case_base`, across `targets`.

    The union `runner.rule_arm_files` strips from the without arm. In rule mode the evidence
    key and prune's stale guard hash these blocks, not the whole files: the global CLAUDE.md
    changes almost daily, and hashing it whole would reset every rule's count on any edit and
    let one deletion make every other rule of the file stale. `live` filters as in
    `citing_rules`, so run and prune hash the same blocks.
    """
    out = {}
    for target in targets:
        target = os.path.expanduser(target)
        path = os.path.join(root, target)
        if not os.path.exists(path):
            continue
        ids = citing_rules(target, case_base, root, live)
        if not ids:
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for rule_id in ids:
            block = rule_block(text, rule_id)
            if block is not None:
                out[rule_id] = hashlib.sha256(block.encode("utf-8")).hexdigest()
    return out


def read_rules(target, root="."):
    """{rule_id: {"case":..., "date":..., "incident":..., "text":...}} for one target."""
    path = os.path.join(root, target)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return read_rules_text(fh.read())


def read_rules_text(text):
    """`read_rules` on a string: the same dict, for a block or a file already read."""
    lines = text.split("\n")
    out = {}
    # The date and incident are optional: the short marker form keeps them in a history doc
    # instead (see rule_ids).
    pat = re.compile(re.escape(MARK_OPEN) + r"\s+(?P<rule>\S+)\s+case\s+(?P<case>[^,]+?)"
                     r"(?:,\s*(?P<date>[0-9-]+):\s*(?P<incident>.*?))?\s*(?:-->|$)")
    for i, line in enumerate(lines):
        m = pat.search(line)
        if not m:
            continue
        j = i + 1
        body = []
        while j < len(lines) and lines[j].strip():
            body.append(lines[j])
            j += 1
        out[m.group("rule")] = {"case": m.group("case").strip(), "date": m.group("date") or "",
                                "incident": (m.group("incident") or "").strip(),
                                "text": "\n".join(body), "line": i + 1}
    return out
