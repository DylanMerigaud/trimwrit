import pytest

from trimwrit import integrate as I
from trimwrit.text import RefusedWrite

# Any existing file: a rule written into a CLAUDE.md names the door that enforces it.
DOOR = I.__file__


def test_a_rule_without_a_case_is_refused(tmp_path):
    with pytest.raises(I.IntegrateError) as exc:
        I.write_rule("CLAUDE.md", "R1", None, "2026-08-23", "an incident", "the rule",
                     root=str(tmp_path), door=DOOR)
    assert "preference" in str(exc.value)


def test_a_rule_without_an_incident_is_refused(tmp_path):
    with pytest.raises(I.IntegrateError) as exc:
        I.write_rule("CLAUDE.md", "R1", "0001", "2026-08-23", "", "the rule", root=str(tmp_path),
                     door=DOOR)
    assert "delete this rule without fear" in str(exc.value)


def test_the_marker_carries_case_date_and_incident(tmp_path):
    I.write_rule("CLAUDE.md", "R1", "0001", "2026-08-23", "a post went out with three of them",
                 "Never use an em-dash.", root=str(tmp_path), door=DOOR)
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "<!-- trimwrit: R1 case 0001, 2026-08-23: a post went out with three of them -->" in text
    assert "Never use an em-dash." in text


def test_markdown_gets_html_comments_and_everything_else_gets_hashes():
    assert I.comment_for("CLAUDE.md") == ("<!-- ", " -->")
    assert I.comment_for("hooks/check.sh") == ("# ", "")


def test_rewriting_the_same_rule_replaces_it(tmp_path):
    I.write_rule("CLAUDE.md", "R1", "0001", "2026-08-23", "i", "first text", root=str(tmp_path),
                 door=DOOR)
    res = I.write_rule("CLAUDE.md", "R1", "0001", "2026-08-24", "i", "second text",
                       root=str(tmp_path), door=DOOR)
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert res["action"] == "replaced"
    assert "second text" in text and "first text" not in text
    assert text.count("trimwrit: R1") == 1


def test_read_rules_and_drop_rule(tmp_path):
    I.write_rule("CLAUDE.md", "R1", "0001", "2026-08-23", "incident one", "rule one",
                 root=str(tmp_path), door=DOOR)
    I.write_rule("CLAUDE.md", "R2", "0002", "2026-08-23", "incident two", "rule two",
                 root=str(tmp_path), door=DOOR)
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
                     "never do this" + chr(0x2014) + "ever", root=str(tmp_path), door=DOOR)


def test_prose_above_the_rules_is_preserved(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# House rules\n\nhand written prose.\n", encoding="utf-8")
    I.write_rule("CLAUDE.md", "R1", "0001", "2026-08-23", "i", "the rule", root=str(tmp_path),
                 door=DOOR)
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
        "<!-- trimwrit: R2 case 0027-sample-case, 2026-08-23: b -->\ntwo\n\n"
        "<!-- trimwrit: R3 case 0270 -->\nthree\n\n"
        "<!-- trimwrit: R4 case 0026+0027 -->\nfour\n\n"
        "<!-- trimwrit: R5 case 0027-other -->\nfive\n", encoding="utf-8")
    got = I.citing_rules("CLAUDE.md", "0027-sample-case", root=str(tmp_path))
    assert got == ["R1", "R2", "R4"]


def test_citing_rules_of_a_missing_target_is_empty(tmp_path):
    assert I.citing_rules("nope.md", "0001-x", root=str(tmp_path)) == []


def test_rules_sha256_hashes_each_citing_block_across_targets(tmp_path):
    import hashlib
    import os

    root = str(tmp_path)
    os.makedirs(os.path.join(root, "sub"))
    I.write_rule("CLAUDE.md", "R1", "0001", "2026-10-21", "i", "one", root=root, door=DOOR)
    I.write_rule("CLAUDE.md", "R2", "0002", "2026-10-21", "i", "two", root=root, door=DOOR)
    I.write_rule("sub/CLAUDE.md", "R3", "0001-a+0003", "2026-10-21", "i", "three", root=root,
                 door=DOOR)
    got = I.rules_sha256(["CLAUDE.md", "sub/CLAUDE.md", "missing.md"], "0001-a", root)
    assert sorted(got) == ["R1", "R3"]
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    start, end = I.find_rule(text, "R1")
    block = "\n".join(text.split("\n")[start:end])
    assert got["R1"] == hashlib.sha256(block.encode("utf-8")).hexdigest()
    # The block is exactly what strip_rules takes out, give or take the blank lines after it.
    assert block in text and block not in I.strip_rules(text, ["R1"])[0]


def _budget(tmp_path, data):
    import json
    (tmp_path / ".trimwrit").mkdir(exist_ok=True)
    (tmp_path / ".trimwrit" / "budget.json").write_text(json.dumps(data), encoding="utf-8")


def test_a_write_past_the_budget_is_refused_and_nothing_is_written(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("x" * 235 + "\n", encoding="utf-8")
    _budget(tmp_path, {"CLAUDE.md": 110})
    with pytest.raises(I.OverBudget) as exc:
        I.write_rule("CLAUDE.md", "R1", "0001", "2026-08-23", "i", "y" * 100,
                     root=str(tmp_path), user_dir=str(tmp_path / "home"), door=DOOR)
    msg = str(exc.value)
    assert msg.startswith("over-budget:") and "budget 110" in msg and "SKILL.md" in msg
    assert isinstance(exc.value, I.IntegrateError)
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "x" * 235 + "\n"


def test_the_user_file_is_budgeted_by_its_tilde_name(tmp_path):
    udir = tmp_path / "home" / ".claude"
    udir.mkdir(parents=True)
    _budget(tmp_path, {"~/.claude/CLAUDE.md": 5})
    with pytest.raises(I.OverBudget):
        I.write_rule(str(udir / "CLAUDE.md"), "R1", "0001", "2026-08-23", "i", "y" * 100,
                     root=str(tmp_path), user_dir=str(udir), door=DOOR)


def _gate(tmp_path):
    """A door of known length under the root, so a line count does not depend on the checkout."""
    (tmp_path / "gate.py").write_text("", encoding="utf-8")
    return "gate.py"


def test_shrinking_a_rule_in_a_file_still_over_is_allowed(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("p" * 900 + "\n", encoding="utf-8")
    door = _gate(tmp_path)
    I.write_rule("CLAUDE.md", "R1", "0001", "2026-08-23", "i", "y" * 250, root=str(tmp_path),
                 door=door)
    _budget(tmp_path, {"CLAUDE.md": 50})
    res = I.write_rule("CLAUDE.md", "R1", "0001", "2026-08-23", "i", "y" * 200,
                       root=str(tmp_path), user_dir=str(tmp_path / "home"), door=door)
    assert res["action"] == "replaced"
    with pytest.raises(I.OverBudget):
        I.write_rule("CLAUDE.md", "R1", "0001", "2026-08-23", "i", "y" * 220,
                     root=str(tmp_path), user_dir=str(tmp_path / "home"), door=door)


def test_no_budget_no_refusal(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("p" * 5000 + "\n", encoding="utf-8")
    res = I.write_rule("CLAUDE.md", "R1", "0001", "2026-08-23", "i", "y" * 100,
                       root=str(tmp_path), user_dir=str(tmp_path / "home"), door=DOOR)
    assert res["action"] == "added"
    _budget(tmp_path, {"docs/other.md": 1})
    I.write_rule("CLAUDE.md", "R2", "0002", "2026-08-23", "i", "z" * 100,
                 root=str(tmp_path), user_dir=str(tmp_path / "home"), door=DOOR)


# ------------------------------------------------------------------ the shape doors (0.14.0)


def test_a_rule_over_three_lines_is_refused_in_an_always_loaded_file(tmp_path):
    door = _gate(tmp_path)
    I.write_rule("CLAUDE.md", "R1", "0001", "2026-11-10", "i", "one\ntwo\nthree gate.py",
                 root=str(tmp_path), user_dir=str(tmp_path / "home"), door=door)
    with pytest.raises(I.RuleTooLong) as exc:
        I.write_rule("CLAUDE.md", "R2", "0002", "2026-11-10", "i", "a\nb\nc\nd gate.py",
                     root=str(tmp_path), user_dir=str(tmp_path / "home"), door=door)
    msg = str(exc.value)
    assert msg.startswith("rule-too-long:") and "history doc" in msg
    assert "trimwrit relocate" in msg and "SKILL.md" in msg
    assert isinstance(exc.value, I.IntegrateError)
    assert "R2" not in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")


def test_one_giant_line_counts_as_many(tmp_path):
    assert I.rule_lines("x" * 100) == 1
    assert I.rule_lines("x" * 101) == 2
    assert I.rule_lines("a\n\nb") == 2
    with pytest.raises(I.RuleTooLong):
        I.write_rule("CLAUDE.md", "R1", "0001", "2026-11-10", "i", "gate.py " + "x" * 320,
                     root=str(tmp_path), user_dir=str(tmp_path / "home"), door=_gate(tmp_path))


def test_the_appended_door_counts_toward_the_length(tmp_path):
    door = _gate(tmp_path)
    with pytest.raises(I.RuleTooLong):
        I.write_rule("CLAUDE.md", "R1", "0001", "2026-11-10", "i", "a\nb\n" + "c" * 95,
                     root=str(tmp_path), user_dir=str(tmp_path / "home"), door=door)


def test_a_rule_with_no_door_or_a_missing_one_is_refused(tmp_path):
    for door in (None, "", "hooks/nope.py", "ghost-plugin"):
        with pytest.raises(I.NoDoor) as exc:
            I.write_rule("CLAUDE.md", "R1", "0001", "2026-11-10", "i", "the rule",
                         root=str(tmp_path), user_dir=str(tmp_path / "home"), door=door)
        assert str(exc.value).startswith("no-door:")
    assert not (tmp_path / "CLAUDE.md").exists()


def test_the_door_is_appended_unless_the_rule_already_names_it(tmp_path):
    door = _gate(tmp_path)
    home = str(tmp_path / "home")
    I.write_rule("CLAUDE.md", "R1", "0001", "2026-11-10", "i", "Never do X.",
                 root=str(tmp_path), user_dir=home, door=door)
    I.write_rule("CLAUDE.md", "R2", "0002", "2026-11-10", "i", "Never do Y (gate.py).",
                 root=str(tmp_path), user_dir=home, door=door)
    rules = I.read_rules("CLAUDE.md", root=str(tmp_path))
    assert rules["R1"]["text"] == "Never do X. Door: gate.py."
    assert rules["R2"]["text"] == "Never do Y (gate.py)."


def test_a_plugin_door_is_found_in_the_registry_or_under_plugins(tmp_path):
    import json
    home = tmp_path / "home"
    (home / "plugins").mkdir(parents=True)
    (home / "plugins" / "installed_plugins.json").write_text(json.dumps(
        {"version": 2, "plugins": {"no-em-dash@trimwrit": [{"scope": "user"}]}}),
        encoding="utf-8")
    (tmp_path / "plugins" / "local-gate" / ".claude-plugin").mkdir(parents=True)
    for i, door in enumerate(("no-em-dash", "no-em-dash@trimwrit", "local-gate")):
        I.write_rule("CLAUDE.md", "R{}".format(i), "0001", "2026-11-10", "i", "the rule",
                     root=str(tmp_path), user_dir=str(home), door=door)
    rules = I.read_rules("CLAUDE.md", root=str(tmp_path))
    assert rules["R1"]["text"] == "the rule Door: no-em-dash@trimwrit."
    with pytest.raises(I.NoDoor):
        I.write_rule("CLAUDE.md", "R9", "0001", "2026-11-10", "i", "the rule",
                     root=str(tmp_path), user_dir=str(home), door="no-em-dash@elsewhere")


def test_the_user_rules_dir_is_always_loaded(tmp_path):
    home = tmp_path / "home"
    (home / "rules").mkdir(parents=True)
    with pytest.raises(I.NoDoor):
        I.write_rule(str(home / "rules" / "x.md"), "R1", "0001", "2026-11-10", "i", "r",
                     root=str(tmp_path), user_dir=str(home))


def test_a_plain_doc_needs_no_door_and_no_length(tmp_path):
    I.write_rule("docs/history.md", "R1", "0001", "2026-11-10", "i", "a\nb\nc\nd\ne",
                 root=str(tmp_path), user_dir=str(tmp_path / "home"))
    assert "e" in I.read_rules("docs/history.md", root=str(tmp_path))["R1"]["text"]


def test_a_skill_with_no_description_is_refused(tmp_path):
    skill = tmp_path / "skills" / "x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: x\n---\n\n# x\n", encoding="utf-8")
    with pytest.raises(I.NoDescription) as exc:
        I.write_rule("skills/x/SKILL.md", "R1", "0001", "2026-11-10", "i", "the rule",
                     root=str(tmp_path), user_dir=str(tmp_path / "home"))
    assert str(exc.value).startswith("no-description:")
    with pytest.raises(I.NoDescription):
        I.write_rule("skills/new/SKILL.md", "R1", "0001", "2026-11-10", "i", "the rule",
                     root=str(tmp_path), user_dir=str(tmp_path / "home"))


def test_a_skill_needs_no_door_and_takes_a_long_rule(tmp_path):
    skill = tmp_path / "skills" / "x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: x\ndescription: Use when X.\n---\n\n# x\n",
                                    encoding="utf-8")
    res = I.write_rule("skills/x/SKILL.md", "R1", "0001", "2026-11-10", "i", "a\nb\nc\nd\ne",
                       root=str(tmp_path), user_dir=str(tmp_path / "home"))
    assert res["action"] == "added"


def test_a_new_skill_takes_its_frontmatter_from_the_rule_text(tmp_path):
    text = "---\nname: y\ndescription: >\n  Use when Y.\n---\nDo Y this way."
    I.write_rule("skills/y/SKILL.md", "R1", "0001", "2026-11-10", "i", text,
                 root=str(tmp_path), user_dir=str(tmp_path / "home"))
    written = (tmp_path / "skills" / "y" / "SKILL.md").read_text(encoding="utf-8")
    assert written.startswith("---\nname: y\ndescription: >\n  Use when Y.\n---\n")
    assert I.read_rules("skills/y/SKILL.md", root=str(tmp_path))["R1"]["text"] == "Do Y this way."
