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
