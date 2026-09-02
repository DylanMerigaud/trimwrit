import os

import pytest

from trimwrit import ledger as L


def test_log_and_read_back(tmp_path):
    row = L.log("you used an em-dash again", tag="em-dash", root=str(tmp_path))
    assert row["id"] == "0001"
    assert L.get("0001", root=str(tmp_path))["text"] == "you used an em-dash again"


def test_a_touched_empty_ledger_reads_as_no_corrections(tmp_path):
    # `trimwrit adopt` creates `.trimwrit/ledger.jsonl` as a bare touch (zero bytes) for a repo
    # that never had one, rather than skip the file entirely, so every other reader in this
    # module has to treat "exists, empty" the same as "does not exist yet": zero corrections,
    # never an error.
    path = L.path_for(root=str(tmp_path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").close()

    assert L.rows(root=str(tmp_path)) == []
    assert L.corrections(root=str(tmp_path)) == []
    assert L.pending(root=str(tmp_path)) == []
    assert L.folded(root=str(tmp_path)) == []


def test_ids_are_monotonic_and_padded(tmp_path):
    for _ in range(3):
        L.log("something", root=str(tmp_path))
    assert [r["id"] for r in L.folded(root=str(tmp_path))] == ["0001", "0002", "0003"]


def test_same_text_same_session_is_refused(tmp_path):
    # A resumed transcript replaying one correction must never fabricate an n=2, because n=2
    # writes a rule.
    L.log("stop doing that", tag="t", session="s1", root=str(tmp_path))
    with pytest.raises(L.LedgerError):
        L.log("stop doing that", tag="t", session="s1", root=str(tmp_path))


def test_same_text_different_session_is_a_real_second_occurrence(tmp_path):
    L.log("stop doing that", tag="t", session="s1", root=str(tmp_path))
    L.log("stop doing that", tag="t", session="s2", root=str(tmp_path))
    assert L.counts(root=str(tmp_path))["t"] == 2


def test_untagged_corrections_never_count(tmp_path):
    L.log("one", root=str(tmp_path))
    L.log("two", root=str(tmp_path))
    assert L.pending(root=str(tmp_path)) == []


def test_counter_route_needs_two(tmp_path):
    L.log("one", tag="t", session="a", root=str(tmp_path))
    with pytest.raises(L.LedgerError) as exc:
        L.promote("t", "R1", root=str(tmp_path))
    assert "counter route needs 2" in str(exc.value)
    L.log("two", tag="t", session="b", root=str(tmp_path))
    assert L.promote("t", "R1", root=str(tmp_path))["route"] == "counter"


def test_important_route_needs_a_written_consequence(tmp_path):
    L.log("one", tag="t", session="a", root=str(tmp_path))
    with pytest.raises(L.LedgerError) as exc:
        L.promote("t", "R1", route="important", root=str(tmp_path))
    assert "consequence" in str(exc.value)
    entry = L.promote("t", "R1", route="important", consequence="a client saw the wrong number",
                      root=str(tmp_path))
    assert entry["consequence"] == "a client saw the wrong number"


def test_consequence_on_the_correction_unlocks_the_important_route(tmp_path):
    L.log("one", tag="t", consequence="two mails went out with no fallback", session="a",
          root=str(tmp_path))
    assert L.pending(root=str(tmp_path)) == [("t", 1, "important")]


def test_promotion_clears_pending(tmp_path):
    L.log("a", tag="t", session="1", root=str(tmp_path))
    L.log("b", tag="t", session="2", root=str(tmp_path))
    L.promote("t", "R1", root=str(tmp_path))
    assert L.pending(root=str(tmp_path)) == []


def test_removal_is_written_into_the_same_ledger(tmp_path):
    L.log("a", tag="t", session="1", root=str(tmp_path))
    L.log("b", tag="t", session="2", root=str(tmp_path))
    L.promote("t", "R1", root=str(tmp_path))
    assert "R1" in L.live_rules(root=str(tmp_path))
    L.remove("R1", "the case passes without it", root=str(tmp_path))
    assert "R1" not in L.live_rules(root=str(tmp_path))


def test_removal_needs_a_reason(tmp_path):
    with pytest.raises(L.LedgerError):
        L.remove("R1", "  ", root=str(tmp_path))


def test_get_returns_the_folded_row_not_the_first_one(tmp_path):
    # The bug this guards: integrate asked for a correction, got the line as first written,
    # saw case=None, and refused a case that had been attached minutes earlier.
    L.log("a", tag="t", root=str(tmp_path))
    L.attach_case("0001", "0001", root=str(tmp_path))
    assert L.get("0001", root=str(tmp_path))["case"] == "0001"


def test_the_ledger_refuses_a_bad_dash(tmp_path):
    from trimwrit.text import RefusedWrite
    with pytest.raises(RefusedWrite):
        L.log("you wrote a" + chr(0x2014) + "dash", root=str(tmp_path))
