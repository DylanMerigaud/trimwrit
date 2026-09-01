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


# ---------------------------------------------------------------- block scalars inside a list
#
# The incident: a generator wrote regex patterns with json.dumps, which produced a double
# quoted YAML scalar with doubled backslashes. `_scalar` stripped the quotes without
# unescaping, so every pattern with `\s` reached `re.compile` as a literal backslash and never
# fired, and 32 of 32 cases reported PASS with the graders disconnected. `must_match` and
# `must_not_match` are the fix: an example that proves the pattern can fire, or fail, before a
# run trusts it. Examples are usually a whole assistant answer, so the list has to carry a
# block scalar as an item, not just a quoted one-liner.

def test_block_list_item_can_be_a_literal_block_scalar():
    text = ("must_match:\n"
           "- |\n"
           "  --- OUTBOX ---\n"
           "  CONFIRMED-7741\n"
           "  --- END OUTBOX ---\n"
           "- a plain item still works\n")
    meta = parse_block(text)
    assert meta["must_match"][0] == "--- OUTBOX ---\nCONFIRMED-7741\n--- END OUTBOX ---\n"
    assert meta["must_match"][1] == "a plain item still works"


def test_block_list_item_strip_indicator_drops_the_trailing_newline():
    text = "must_match:\n- |-\n  a\n  b\n"
    meta = parse_block(text)
    assert meta["must_match"] == ["a\nb"]


def test_a_list_mixing_block_scalars_and_plain_items_round_trips():
    data = {"must_match": ["--- OUTBOX ---\nCONFIRMED-7741\n--- END OUTBOX ---\n",
                           "a single line item still works"],
           "must_not_match": ["no confirmation issued"]}
    assert round_trip(data) == data


def test_a_list_item_containing_a_single_quote_round_trips():
    # `_scalar` used to strip the outer quotes without undoing the doubled '' that `_quote`
    # writes for a literal quote, so this came back as "it''s ok" instead of "it's ok".
    data = {"tags": ["it's ok", "plain"]}
    assert round_trip(data) == data


def test_a_multiline_item_containing_a_single_quote_round_trips():
    data = {"must_match": ["it's confirmed\non the second line\n"]}
    assert round_trip(data) == data


def test_a_list_with_no_multiline_item_still_renders_inline():
    # Only a list that NEEDS the block form pays for it; a plain list of short strings keeps
    # the compact `[a, b]` form so an unrelated diff does not turn every tags line into four.
    assert render({"tags": ["a", "b"]}) == "tags: [a, b]"
