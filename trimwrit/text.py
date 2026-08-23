"""The writing gate, and the small string helpers everything else shares.

Every byte this tool writes to disk goes through `refuse_bad_dashes` first. That is not a style
preference dressed up as a check: the em-dash is the single most reliable tell that a line was
generated rather than written, and a tool whose whole job is to keep a harness honest cannot be
the thing that seeds it with tells. The gate is mechanical because the alternative, a note in a
README asking politely, is exactly the kind of rule this tool exists to delete.
"""
import re
import unicodedata

# U+2014 em-dash, U+2013 en-dash, U+2012 figure dash, U+2015 horizontal bar. The ASCII hyphen is
# fine and is not listed. U+2212 minus is left alone on purpose: it is a maths character, and
# refusing it would fail legitimate content.
BAD_DASHES = "".join(chr(c) for c in (0x2014, 0x2013, 0x2012, 0x2015))
"""em, en, figure, horizontal bar. Written as codepoints so this file itself stays
clean under `grep -rn` for the very characters it refuses, which is the check every
repo using this tool is expected to run in CI."""

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


class RefusedWrite(Exception):
    """Raised instead of writing. Carries the offending character and where it was found."""


def refuse_bad_dashes(payload, where="the text"):
    """Return `payload` unchanged, or refuse the whole write.

    Refusing is the point. An earlier draft stripped the character and carried on, which is
    worse than useless: the caller believes it wrote what it composed, the file quietly says
    something else, and nobody learns that the composer keeps producing em-dashes.
    """
    for i, ch in enumerate(payload):
        if ch in BAD_DASHES:
            line = payload.count("\n", 0, i) + 1
            raise RefusedWrite(
                "{} contains {} (U+{:04X}) at line {}. Rewrite the sentence with a comma, a "
                "colon, parentheses, or a full stop. Nothing is written until it is gone.".format(
                    where, unicodedata.name(ch, "?"), ord(ch), line))
    return payload


def write_text(path, payload, where=None):
    """The only door to disk in this package. Gated, and it creates parent directories."""
    import os
    refuse_bad_dashes(payload, where or str(path))
    parent = os.path.dirname(str(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(payload)
    return path


def append_text(path, payload, where=None):
    """Same gate, for the append-only ledger."""
    import os
    refuse_bad_dashes(payload, where or str(path))
    parent = os.path.dirname(str(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(payload)
    return path


def regex_escape_bad_dashes(pattern):
    """Rewrite the refused dashes in a REGEX as \\uXXXX escapes.

    Prose never gets this treatment, only grader patterns, and the distinction is the point. A
    case that checks "you used an em-dash" has to name the character somehow, and the two ways
    out are to exempt grader files from the gate or to write the character as an escape. The
    exemption loses more than it gains: `grep` for the literal character across the repo stops
    being a clean signal the moment one file is allowed to carry it. An escape keeps the repo
    greppable AND keeps the pattern exact, and both engines that will ever read it agree on the
    syntax (JavaScript RegExp in the native runner, Python `re` in ours).
    """
    for ch in BAD_DASHES:
        pattern = pattern.replace(ch, "\\u{:04X}".format(ord(ch)))
    return pattern


def slug(text, words=5):
    """A short, stable, filesystem-safe handle for a correction.

    Truncated to `words` because the slug lands in a directory name that a human reads in a
    diff, and a forty-character directory name is read by nobody.
    """
    clean = _SLUG_STRIP.sub("-", text.lower()).strip("-")
    parts = [p for p in clean.split("-") if p][:words]
    # Empty is a real answer and it is returned as such. An earlier version fell back to the
    # word "correction", which quietly named the em-dash grader `forbids-correction`: a file
    # whose name says nothing about what it forbids. Let the caller pick the fallback, since
    # only the caller knows what the string was supposed to be.
    return "-".join(parts)


def one_line(text):
    """Collapse to a single line. Used wherever a value has to survive a table or a comment."""
    return " ".join(str(text).split())
