"""The repo publishes numbers from a real harness, so it checks itself for what must not leave it.

`docs/production/` holds a snapshot of `trimwrit report --json` taken on the author's own
machine. The report builds every value from a count, a date, a door name or a case number, and
this scanner is the second lock: it fails the suite on an email address or a phone number in any
tracked file (outside a short allowlist), and on a key that carries free text in any file under
`docs/production/`.

The sample address and phone number below are assembled at run time, so this file does not trip
the scanner it tests.
"""
import importlib.util
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMAIL = "someone" + "@" + "example.org"
PHONE = "+" + "33 6 12 34 56 78"


def _scanner():
    spec = importlib.util.spec_from_file_location(
        "no_private_data", os.path.join(ROOT, "tools", "no-private-data.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    return env


def _repo(tmp_path, files, stage=True):
    """A throwaway git repository holding `files` ({relative path: text}), staged by default."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=_env())
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if stage:
        subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, env=_env())
    return str(tmp_path)


def _kinds(hits):
    return [(h[0], h[2]) for h in hits]


def test_no_tracked_file_carries_private_data():
    hits = _scanner().scan(ROOT)
    assert hits == [], "private data found: {}".format(hits)


def test_an_email_address_in_a_tracked_file_fails(tmp_path, capsys):
    mod = _scanner()
    root = _repo(tmp_path, {"notes.md": "write to {} today\n".format(EMAIL)})
    assert _kinds(mod.scan(root)) == [("notes.md", "email")]
    assert mod.main([root]) == 1


def test_a_phone_number_in_a_tracked_file_fails(tmp_path):
    mod = _scanner()
    root = _repo(tmp_path, {"lead.txt": "call {}\n".format(PHONE)})
    assert _kinds(mod.scan(root)) == [("lead.txt", "phone")]
    assert mod.main([root]) == 1


@pytest.mark.parametrize("key", ["text", "consequence", "session", "reason", "session_id"])
def test_a_free_text_key_under_docs_production_fails(tmp_path, key):
    mod = _scanner()
    root = _repo(tmp_path, {"docs/production/snap.json": '{{"{}": "x"}}\n'.format(key)})
    assert _kinds(mod.scan(root)) == [("docs/production/snap.json", "key")]
    assert mod.main([root]) == 1


def test_the_same_key_elsewhere_is_not_a_hit(tmp_path):
    """Only the published snapshots are held to the key rule: code everywhere reads `text`."""
    mod = _scanner()
    root = _repo(tmp_path, {"trimwrit/ledger.py": 'row = {"text": "x"}\n'})
    assert mod.scan(root) == []


def test_an_unstaged_repository_is_still_read(tmp_path):
    """`git ls-files` succeeds with EMPTY output before anything is staged; that is no answer."""
    mod = _scanner()
    root = _repo(tmp_path, {"notes.md": EMAIL + "\n"}, stage=False)
    assert _kinds(mod.scan(root)) == [("notes.md", "email")]


def test_an_empty_file_list_is_refused(tmp_path):
    """Reading zero files is not a clean tree. The scanner refuses rather than pass."""
    mod = _scanner()
    with pytest.raises(mod.NothingRead):
        mod.scan(str(tmp_path))
    assert mod.main([str(tmp_path)]) == 2


def test_numbers_that_only_look_like_a_phone_are_not_hits(tmp_path):
    """Deltas, scores, versions and package specs fill this README; none is a phone number."""
    mod = _scanner()
    root = _repo(tmp_path, {"README.md": (
        "delta +1.00 0.17 0.83, then +0.83\n"
        "34,665 turn-final messages, 2,813 refusals\n"
        "react@18.3.1 and trimwrit@trimwrit\n"
        "2026-10-21T08:00:00Z, +0200 and -0500\n")})
    assert mod.scan(root) == []


def test_an_allowlisted_address_passes(tmp_path):
    mod = _scanner()
    allowed = sorted(mod.ALLOW)[0]
    root = _repo(tmp_path, {"t.py": "email = {}\n".format(allowed)})
    assert mod.scan(root) == []


def test_every_allowlist_entry_has_a_reason_and_is_still_in_the_tree():
    """An allowlist only shrinks: an entry nothing uses any more is a hole left open."""
    mod = _scanner()
    found = mod.addresses(ROOT)
    for address, why in mod.ALLOW.items():
        assert why.strip(), address
        assert address in found, "{} is allowlisted but no tracked file carries it".format(address)
