#!/usr/bin/env python3
"""Fail if a tracked file carries an email address or a phone number, or if a published
snapshot carries a key that holds free text.

`docs/production/` holds `trimwrit report --json` taken on the author's own harness. That
command builds every value from a count, a date, a door name or a case number, and never copies
a correction's words through. This scanner is the second lock, for the day a snapshot is made
some other way or a file elsewhere picks up a real person's contact:

- an email address anywhere in a tracked file, unless it is in ALLOW below;
- a phone number anywhere in a tracked file, in an international shape (a plus sign and a
  country code, then groups split by one separator used throughout, or one unbroken run of 8 to
  15 digits), the French national shape (ten digits in pairs) or the North American one;
- a `text`, `consequence`, `session`, `session_id` or `reason` key in any file under
  `docs/production/`: those are the fields of the ledgers that hold what a human said.

It reads files the way `no-bad-dashes.py` does, and refuses the same way: an empty `git
ls-files` falls through to a walk, and a scan that read zero files is no answer, so it exits 2
instead of reporting a clean tree.
"""
import os
import re
import subprocess
import sys

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".ico", ".woff", ".woff2"}
PUBLISHED = "docs/production/"

# Addresses already in the tree that name no third party. Each one says why. An entry no
# tracked file carries any more is removed (a test fails until it is).
ALLOW = {
    "t@example.com": "the git identity of throwaway test repositories; example.com is reserved "
                     "for documentation and names nobody",
    "git@github.com": "the SSH user of every GitHub remote URL, in the tests of the "
                      "no-third-party-pr gate; it is a protocol login, not a person",
}

EMAIL = re.compile(r"(?<![\w.%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*"
                   r"\.[A-Za-z]{2,}(?![\w-])")
PHONES = (
    # International, one separator used throughout. The group after the country code may be a
    # single digit (a French mobile), every later group has 2 to 4.
    re.compile(r"(?<![\w+])\+\d{1,3}([ .-])\(?\d{1,4}\)?(?:\1\d{2,4}){2,5}(?!\w)"),
    # International, unbroken.
    re.compile(r"(?<![\w+])\+\d{8,15}(?!\w)"),
    # French national: a zero, a digit from 1 to 9, then four pairs, one separator throughout.
    re.compile(r"(?<![\w.])0[1-9]([ .-])\d{2}(?:\1\d{2}){3}(?![\w.])"),
    # North American: three digits in parentheses or before a hyphen, then three, then four.
    re.compile(r"(?<!\w)(?:\(\d{3}\) ?|\d{3}-)\d{3}-\d{4}(?!\w)"),
)
FREE_TEXT_KEY = re.compile(r'"(text|consequence|session|session_id|reason)"\s*:')


class NothingRead(Exception):
    """The scan read zero files. That is a broken check, not a clean tree."""


def walk(root):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        found.extend(os.path.join(dirpath, f) for f in filenames)
    return found


def tracked(root):
    """Git's list when git has one, otherwise a walk. An EMPTY list from `git ls-files` is a
    repository with nothing staged, not an answer: fall through to the walk."""
    try:
        out = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True,
                             check=True).stdout
        listed = [os.path.join(root, line) for line in out.splitlines() if line]
        if listed:
            return listed
    except (subprocess.CalledProcessError, OSError):
        pass
    return walk(root)


def _texts(root):
    """(relative path with forward slashes, text) for every readable text file."""
    read = 0
    for path in tracked(root):
        if os.path.splitext(path)[1].lower() in SKIP_EXT or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (UnicodeDecodeError, OSError):
            continue
        read += 1
        yield os.path.relpath(path, root).replace(os.sep, "/"), text
    if not read:
        raise NothingRead(root)


def _line(text, index):
    return text.count("\n", 0, index) + 1


def _raw_hits(root):
    for rel, text in _texts(root):
        for m in EMAIL.finditer(text):
            yield rel, _line(text, m.start()), "email", m.group(0)
        for pattern in PHONES:
            for m in pattern.finditer(text):
                yield rel, _line(text, m.start()), "phone", m.group(0)
        if rel.startswith(PUBLISHED):
            for m in FREE_TEXT_KEY.finditer(text):
                yield rel, _line(text, m.start()), "key", m.group(1)


def addresses(root="."):
    """Every email-shaped string in the tree, allowlisted or not."""
    return {value for _, _, kind, value in _raw_hits(root) if kind == "email"}


def scan(root="."):
    """[(path, line, kind, value)]. Raises NothingRead when no file could be read."""
    hits = [h for h in _raw_hits(root) if not (h[2] == "email" and h[3] in ALLOW)]
    return sorted(set(hits))


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    root = argv[0] if argv else "."
    try:
        hits = scan(root)
    except NothingRead:
        print("read zero files under {}: that is no answer, refusing to call it clean"
              .format(root))
        return 2
    for path, line, kind, value in hits:
        print("{}:{}: {} {}".format(path, line, kind, value))
    if hits:
        print("\n{} hit(s). Remove the address, the number or the key; an address that names "
              "no third party goes in ALLOW with its reason.".format(len(hits)))
        return 1
    print("no email address, phone number or free-text snapshot key in any tracked file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
