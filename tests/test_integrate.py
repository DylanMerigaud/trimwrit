import pytest

from trimwrit import integrate as I
from trimwrit.text import RefusedWrite


def test_a_rule_without_a_case_is_refused(tmp_path):
    with pytest.raises(I.IntegrateError) as exc:
        I.write_rule("CLAUDE.md", "R1", None, "2026-08-23", "an incident", "the rule",
                     root=str(tmp_path))
    assert "preference" in str(exc.value)


def test_a_rule_without_an_incident_is_refused(tmp_path):
    with pytest.raises(I.IntegrateError) as exc:
        I.write_rule("CLAUDE.md", "R1", "0001", "2026-08-23", "", "the rule", root=str(tmp_path))
    assert "delete this rule without fear" in str(exc.value)


def test_the_marker_carries_case_date_and_incident(tmp_path):
    I.write_rule("CLAUDE.md", "R1", "0001", "2026-08-23", "a post went out with three of them",
                 "Never use an em-dash.", root=str(tmp_path))
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "<!-- trimwrit: R1 case 0001, 2026-08-23: a post went out with three of them -->" in text
    assert "Never use an em-dash." in text


def test_markdown_gets_html_comments_and_everything_else_gets_hashes():
    assert I.comment_for("CLAUDE.md") == ("<!-- ", " -->")
    assert I.comment_for("hooks/check.sh") == ("# ", "")


def test_rewriting_the_same_rule_replaces_it(tmp_path):
    I.write_rule("CLAUDE.md", "R1", "0001", "2026-08-23", "i", "first text", root=str(tmp_path))
    res = I.write_rule("CLAUDE.md", "R1", "0001", "2026-08-24", "i", "second text",
                       root=str(tmp_path))
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert res["action"] == "replaced"
    assert "second text" in text and "first text" not in text
    assert text.count("trimwrit: R1") == 1


def test_read_rules_and_drop_rule(tmp_path):
    I.write_rule("CLAUDE.md", "R1", "0001", "2026-08-23", "incident one", "rule one",
                 root=str(tmp_path))
    I.write_rule("CLAUDE.md", "R2", "0002", "2026-08-23", "incident two", "rule two",
                 root=str(tmp_path))
    found = I.read_rules("CLAUDE.md", root=str(tmp_path))
    assert set(found) == {"R1", "R2"}
    assert found["R1"]["case"] == "0001"
    assert found["R1"]["incident"] == "incident one"
    assert I.drop_rule("CLAUDE.md", "R1", root=str(tmp_path))
    assert set(I.read_rules("CLAUDE.md", root=str(tmp_path))) == {"R2"}
    assert "rule one" not in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")


def test_dropping_a_missing_rule_is_not_an_error(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# nothing here\n", encoding="utf-8")
    assert I.drop_rule("CLAUDE.md", "R9", root=str(tmp_path)) is None


def test_the_rule_text_is_gated(tmp_path):
    with pytest.raises(RefusedWrite):
        I.write_rule("CLAUDE.md", "R1", "0001", "2026-08-23", "i",
                     "never do this" + chr(0x2014) + "ever", root=str(tmp_path))


def test_prose_above_the_rules_is_preserved(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# House rules\n\nhand written prose.\n", encoding="utf-8")
    I.write_rule("CLAUDE.md", "R1", "0001", "2026-08-23", "i", "the rule", root=str(tmp_path))
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith("# House rules\n\nhand written prose.\n")


def test_short_marker_without_incident_is_read(tmp_path):
    """A marker whose incident moved to a history doc (`trimwrit: R0020 case 0020 -->`) is still
    a rule: before 2026-09-30 the reader required `, date: incident` and saw none of them."""
    from trimwrit import integrate as integ
    text = ("# t\n\n<!-- trimwrit: R0020 case 0020 -->\nthe rule\n\n"
            "<!-- trimwrit: R0065 case 0065+0066, 2026-09-30: an incident -->\nother rule\n")
    (tmp_path / "CLAUDE.md").write_text(text, encoding="utf-8")
    rules = integ.read_rules("CLAUDE.md", root=str(tmp_path))
    assert rules["R0020"]["case"] == "0020"
    assert rules["R0020"]["text"] == "the rule"
    assert rules["R0065"]["case"] == "0065+0066"
    assert rules["R0065"]["date"] == "2026-09-30"
    assert [r for r, _ in integ.rule_ids(text)] == ["R0020", "R0065"]


THREE_RULES = (
    "# Title\n\n"
    "<!-- trimwrit: R1 case 0001, 2026-08-23: one -->\nrule one\nmore one\n\n"
    "<!-- trimwrit: R2 case 0002, 2026-08-23: two -->\nrule two\n\n"
    "<!-- trimwrit: R3 case 0003 -->\nrule three\n  keep   spacing\n\n"
    "tail\n")


def test_strip_rules_removes_the_named_blocks_and_keeps_the_rest_byte_for_byte():
    new, removed = I.strip_rules(THREE_RULES, ["R1", "R2"])
    assert removed == ["R1", "R2"]
    assert new == ("# Title\n\n"
                   "<!-- trimwrit: R3 case 0003 -->\nrule three\n  keep   spacing\n\n"
                   "tail\n")


def test_strip_rules_ignores_ids_that_are_absent_and_reports_only_what_it_removed():
    new, removed = I.strip_rules(THREE_RULES, ["R9", "R3"])
    assert removed == ["R3"]
    assert "rule three" not in new and "rule one" in new
    assert I.strip_rules(THREE_RULES, []) == (THREE_RULES, [])


def test_strip_rules_does_not_confuse_r1_with_r10():
    text = "<!-- trimwrit: R10 case 0010 -->\nten\n\n<!-- trimwrit: R1 case 0001 -->\none\n"
    new, removed = I.strip_rules(text, ["R1"])
    assert removed == ["R1"] and "ten" in new and "one" not in new.replace("ten", "")


def test_drop_rule_returns_the_block_and_matches_strip_rules(tmp_path):
    (tmp_path / "CLAUDE.md").write_text(THREE_RULES, encoding="utf-8")
    block = I.drop_rule("CLAUDE.md", "R2", root=str(tmp_path))
    assert block == "<!-- trimwrit: R2 case 0002, 2026-08-23: two -->\nrule two"
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == \
        I.strip_rules(THREE_RULES, ["R2"])[0]


def test_citing_rules_matches_the_leading_number_and_the_full_name(tmp_path):
    (tmp_path / "CLAUDE.md").write_text(
        "<!-- trimwrit: R1 case 0027, 2026-08-23: a -->\none\n\n"
        "<!-- trimwrit: R2 case 0027-decision-taken-reopened, 2026-08-23: b -->\ntwo\n\n"
        "<!-- trimwrit: R3 case 0270 -->\nthree\n\n"
        "<!-- trimwrit: R4 case 0026+0027 -->\nfour\n\n"
        "<!-- trimwrit: R5 case 0027-other -->\nfive\n", encoding="utf-8")
    got = I.citing_rules("CLAUDE.md", "0027-decision-taken-reopened", root=str(tmp_path))
    assert got == ["R1", "R2", "R4"]


def test_citing_rules_of_a_missing_target_is_empty(tmp_path):
    assert I.citing_rules("nope.md", "0001-x", root=str(tmp_path)) == []


def test_rules_sha256_hashes_each_citing_block_across_targets(tmp_path):
    import hashlib
    import os

    root = str(tmp_path)
    os.makedirs(os.path.join(root, "sub"))
    I.write_rule("CLAUDE.md", "R1", "0001", "2026-10-21", "i", "one", root=root)
    I.write_rule("CLAUDE.md", "R2", "0002", "2026-10-21", "i", "two", root=root)
    I.write_rule("sub/CLAUDE.md", "R3", "0001-a+0003", "2026-10-21", "i", "three", root=root)
    got = I.rules_sha256(["CLAUDE.md", "sub/CLAUDE.md", "missing.md"], "0001-a", root)
    assert sorted(got) == ["R1", "R3"]
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    start, end = I.find_rule(text, "R1")
    block = "\n".join(text.split("\n")[start:end])
    assert got["R1"] == hashlib.sha256(block.encode("utf-8")).hexdigest()
    # The block is exactly what strip_rules takes out, give or take the blank lines after it.
    assert block in text and block not in I.strip_rules(text, ["R1"])[0]
