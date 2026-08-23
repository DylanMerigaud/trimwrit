"""The repo enforces its own headline rule on itself.

This is the dogfood test that matters most: a tool that refuses em-dashes in your files and
carries three of its own is not an instrument, it is an opinion. It has caught a real miss
already, in a comment inside tests/test_frontmatter.py, which the obvious shell one liner
(`grep -rn` with a `$'...'` pattern) reported as clean on the machine this was written on.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "tools"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_no_tracked_file_carries_a_bad_dash():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "no_bad_dashes", os.path.join(ROOT, "tools", "no-bad-dashes.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    hits = mod.scan(ROOT)
    assert hits == [], "bad dashes found: {}".format(hits)


def test_the_scanner_actually_finds_one(tmp_path):
    """Guards the failure mode of every check like this: reporting clean because it is broken."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "no_bad_dashes", os.path.join(ROOT, "tools", "no-bad-dashes.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "dirty.md").write_text("a" + chr(0x2014) + "b", encoding="utf-8")
    hits = mod.scan(str(tmp_path))
    assert [(h[0], h[2]) for h in hits] == [("dirty.md", "EM DASH")]
