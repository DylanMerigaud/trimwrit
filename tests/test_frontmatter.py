import pytest

from trimwrit.frontmatter import FrontmatterError, parse_block, render, round_trip, split

EM = chr(0x2014)


def test_split_returns_meta_and_body():
    meta, body = split("---\nname: x\nruns: 2\n---\nthe prompt\nsecond line\n")
    assert meta == {"name": "x", "runs": 2}
    assert body == "the prompt\nsecond line\n"


def test_no_fence_means_all_body():
    meta, body = split("just a prompt")
    assert meta == {} and body == "just a prompt"


def test_unclosed_fence_raises():
    with pytest.raises(FrontmatterError):
        split("---\nname: x\nstill going\n")


def test_inline_list_and_bools_and_block_scalar():
    meta = parse_block("tags: [a, b]\nflag: yes\ncriteria: |\n  line one\n  line two")
    assert meta["tags"] == ["a", "b"]
    assert meta["flag"] is True
    assert meta["criteria"] == "line one\nline two"


def test_block_list():
    assert parse_block("tools:\n- Bash\n- Read")["tools"] == ["Bash", "Read"]


def test_character_class_survives_the_round_trip():
    # The bug this guards: a bare character class is an inline SEQUENCE in YAML, so the
    # grader loaded as a one-element list and could never fire.
    pattern = "[\\u2014\\u2013]"
    out = round_trip({"type": "regex", "pattern": pattern})
    assert out["pattern"] == pattern
    assert isinstance(out["pattern"], str)


def test_quoting_uses_single_quotes_so_escapes_stay_literal():
    # A double-quoted YAML scalar resolves the escape to a real em-dash, which would put the
    # refused character into the file after all.
    text = render({"pattern": "[\\u2014]"})
    assert text == "pattern: '[\\u2014]'"
    assert EM not in text


@pytest.mark.parametrize("value", ["yes", "no", "3", "3.5", "[a]", "a: b", " lead", "trail "])
def test_ambiguous_scalars_round_trip_as_strings(value):
    assert round_trip({"k": value})["k"] == value
