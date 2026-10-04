#!/usr/bin/env python3
"""PreToolUse hook: refuse to WRITE a forbidden dash into a file.

The Stop hook next to this one guards what Claude SAYS; this one guards what Claude STORES, and
it also covers headless runs where a Stop hook does not fire.

It has to cover Bash: in the session that produced it there were 0 Write calls, 0 Edit calls and
175 Bash calls carrying a heredoc or a redirect.

WHAT IS CHECKED
  Write         tool_input.content
  Edit          tool_input.new_string
  MultiEdit     every tool_input.edits[].new_string
  NotebookEdit  tool_input.new_source
  Bash          heredoc bodies in full, plus each command SEGMENT that redirects, tees, or
                edits in place. Segments are split on the shell separators, not on pipes, so
                `printf <dash> | tee f` is caught while `grep <dash> f` stays allowed. Auditing
                files for these dashes is legitimate, and a gate that blocks the audit gets
                removed the same day.

NO CODE-FENCE EXEMPTION: a dash in a file is a dash in a file. The rule's own escape is to write
the codepoint instead of the character: \\u2014 in JSON, YAML and JavaScript, chr(0x2014) in
Python, &mdash; in HTML. That keeps the file greppable for the literal character.

Every deny is counted, every error is loud: a crash or a timeout lets the write through and is
reported. No environment variable and no configuration key turns this gate off. The refused
characters are the `codepoints` key of the `no-em-dash` section of trimwrit-gates.json.
"""
import json
import os
import re
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_ROOT)
from gatekit import trace  # noqa: E402

HOOK = "no-em-dash-write.py"
TIMEOUT_S = 8

BAD = {0x2014: "em-dash", 0x2013: "en-dash", 0x2012: "figure dash", 0x2015: "horizontal bar"}

# Does this shell command put bytes into a file? Deliberately loose on the write side and silent
# on everything else: a false deny costs a blocked command, a false allow costs one dash on disk.
WRITES = re.compile(r">>?\s*\S|\btee\b|\bsed\b[^|]*-i|\bdd\b")

# Heredoc bodies are pulled out and judged whole: a body is content by definition and can
# contain the separators the segment splitter uses.
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1\n(.*?)^\s*\2\s*$",
                     re.DOTALL | re.MULTILINE)

# Everything else is judged PER SEGMENT, so `echo clean > a ; grep <dash> b` is allowed. Pipes
# are NOT separators: `printf <dash> | tee f` really does write the character.
SEGMENTS = re.compile(r"&&|\|\||;|\n")

FIELD = {
    "Write": "content",
    "Edit": "new_string",
    "NotebookEdit": "new_source",
}


def scan(text, bad):
    found = []
    for i, ch in enumerate(text):
        if ord(ch) in bad:
            name = BAD.get(ord(ch), "U+{:04X}".format(ord(ch)))
            snippet = text[max(0, i - 40):i + 40].replace("\n", " ")
            found.append((name, snippet.strip()))
    return found


def payload_text(tool, tool_input):
    """The string this call would persist, or None when there is nothing to check."""
    if tool in FIELD:
        value = tool_input.get(FIELD[tool])
        return value if isinstance(value, str) else None
    if tool == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            return None
        parts = [e["new_string"] for e in edits
                 if isinstance(e, dict) and isinstance(e.get("new_string"), str)]
        return "\n".join(parts) or None
    if tool == "Bash":
        cmd = tool_input.get("command")
        if not isinstance(cmd, str):
            return None
        parts = []
        rest = cmd
        for match in HEREDOC.finditer(cmd):
            parts.append(match.group(3))          # a heredoc body is content, always checked
            rest = rest.replace(match.group(0), " ")
        for segment in SEGMENTS.split(rest):
            if WRITES.search(segment):
                parts.append(segment)
        return "\n".join(parts) or None
    return None


def body():
    payload = trace.read_payload()
    bad = set(trace.plugin_settings(payload, root=PLUGIN_ROOT)["codepoints"])
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    text = payload_text(tool, tool_input)
    found = scan(text, bad) if text else []
    if not found:
        return 0

    where = "the command" if tool == "Bash" else "the content"
    lines = ["{} forbidden dash(es) in {} of this {} call. The write is refused.".format(
        len(found), where, tool)]
    for name, snippet in found[:5]:
        lines.append("  {}: ...{}...".format(name, snippet))
    if len(found) > 5:
        lines.append("  and {} more.".format(len(found) - 5))
    lines.append("Rewrite with a comma, a colon, parentheses, or a new sentence. If the "
                 "character is genuinely required, write it as an escape so the file stays "
                 "greppable: \\u2014 in JSON, YAML or JS, chr(0x2014) in Python, &mdash; in "
                 "HTML.")
    reason = "\n".join(lines)
    trace.witness("no-em-dash-write", "PreToolUse", "forbidden_dash_write", payload)
    json.dump({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }, sys.stdout)
    return 0


def main():
    trace.configure(sys.argv)
    return trace.run(HOOK, "PreToolUse", body, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
