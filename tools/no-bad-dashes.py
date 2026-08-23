#!/usr/bin/env python3
"""Fail if any tracked file carries an em-dash, en-dash, figure dash or horizontal bar.

Run it in CI. Run it before a commit. It exists because the obvious shell one liner does not
work: on macOS with zsh, a `grep -rn` written over the two characters themselves reported a
clean tree while three em-dashes were sitting in a test file inside it. A check that silently
passes is worse than no check, so this one is a program with an exit code and a test of its
own.

It has since caught itself the same way, which is why `tracked()` looks the way it does. The
first version trusted `git ls-files`; that command succeeds with EMPTY output in a repository
with nothing staged yet, and the scanner cheerfully reported a clean tree after reading zero
files. An empty file list is now no answer at all rather than a pass.
"""
import os
import subprocess
import sys
import unicodedata

BAD = (0x2014, 0x2013, 0x2012, 0x2015)
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".ico", ".woff", ".woff2"}


def walk(root):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        found.extend(os.path.join(dirpath, f) for f in filenames)
    return found


def tracked(root):
    """Git's list when git has one, otherwise a walk.

    An EMPTY list from `git ls-files` is not an answer, it is a repository with nothing staged,
    and returning it would make this whole script a no-op that exits 0. Fall through to the walk.
    """
    try:
        out = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True,
                             check=True).stdout
        listed = [os.path.join(root, line) for line in out.splitlines() if line]
        if listed:
            return listed
    except (subprocess.CalledProcessError, OSError):
        pass
    return walk(root)


def scan(root="."):
    hits = []
    for path in tracked(root):
        if os.path.splitext(path)[1].lower() in SKIP_EXT or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (UnicodeDecodeError, OSError):
            continue
        for i, ch in enumerate(text):
            if ord(ch) in BAD:
                hits.append((os.path.relpath(path, root), text.count("\n", 0, i) + 1,
                             unicodedata.name(ch, "?")))
    return hits


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    root = argv[0] if argv else "."
    hits = scan(root)
    for path, line, name in hits:
        print("{}:{}: {}".format(path, line, name))
    if hits:
        print("\n{} bad dash(es). Use a comma, a colon, parentheses, or a new sentence."
              .format(len(hits)))
        return 1
    print("no em-dash, en-dash, figure dash or horizontal bar in any tracked file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
