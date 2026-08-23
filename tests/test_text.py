import pytest

from trimwrit.text import (BAD_DASHES, RefusedWrite, refuse_bad_dashes,
                           regex_escape_bad_dashes, slug, write_text)

EM = chr(0x2014)
EN = chr(0x2013)


@pytest.mark.parametrize("ch", list(BAD_DASHES))
def test_every_listed_dash_is_refused(ch):
    with pytest.raises(RefusedWrite):
        refuse_bad_dashes("before" + ch + "after", "payload")


def test_ascii_hyphen_and_minus_are_fine():
    assert refuse_bad_dashes("well-formed, 3 - 2 = 1", "x")


def test_refusal_names_the_character_and_the_line():
    with pytest.raises(RefusedWrite) as exc:
        refuse_bad_dashes("one\ntwo" + EM, "the payload")
    msg = str(exc.value)
    assert "EM DASH" in msg and "U+2014" in msg and "line 2" in msg


def test_nothing_is_written_when_refused(tmp_path):
    target = tmp_path / "out.md"
    with pytest.raises(RefusedWrite):
        write_text(str(target), "a" + EM + "b")
    assert not target.exists(), "a refused write must leave no file behind"


def test_regex_escape_keeps_the_pattern_matching():
    import re
    escaped = regex_escape_bad_dashes("[" + EM + EN + "]")
    assert EM not in escaped and "\\u2014" in escaped
    assert re.search(escaped, "a" + EM + "b")
    assert re.search(escaped, "a" + EN + "b")
    assert not re.search(escaped, "a-b")


def test_slug_returns_empty_rather_than_a_misleading_word():
    assert slug("[" + EM + "]") == ""
    assert slug("You used an em dash again in the summary") == "you-used-an-em-dash"
