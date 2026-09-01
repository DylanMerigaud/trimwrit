import os

import pytest

from trimwrit import cases

EM = chr(0x2014)


def test_a_case_with_no_grader_is_refused(tmp_path):
    with pytest.raises(cases.CaseError) as exc:
        cases.write_case("0001", "t", "prompt", [], evals_dir=str(tmp_path))
    assert "can never fail" in str(exc.value)


def test_unknown_grader_type_is_refused(tmp_path):
    with pytest.raises(cases.CaseError):
        cases.write_case("0001", "t", "p", [{"type": "vibes", "name": "v"}],
                         evals_dir=str(tmp_path))


def test_native_layout(tmp_path):
    d = cases.write_case("0007", "no em dash anywhere", "Write something with rhythm.",
                         [cases.forbid_grader("[" + EM + "]", name="forbids-em-dash")],
                         evals_dir=str(tmp_path), tags=["style"], runs=4, max_turns=3)
    assert os.path.basename(d) == "0007-no-em-dash-anywhere"
    assert os.path.exists(os.path.join(d, "prompt.md"))
    assert os.path.exists(os.path.join(d, "graders", "forbids-em-dash.md"))


def test_the_em_dash_grader_never_puts_the_character_in_the_file(tmp_path):
    d = cases.write_case("0001", "t", "p",
                         [cases.forbid_grader("[" + EM + "]", name="g")],
                         evals_dir=str(tmp_path))
    with open(os.path.join(d, "graders", "g.md"), encoding="utf-8") as fh:
        text = fh.read()
    assert EM not in text
    assert "\\u2014" in text


def test_round_trip_through_load_case(tmp_path):
    d = cases.write_case("0001", "a title", "the prompt body",
                         [cases.forbid_grader("[" + EM + "]", name="g"),
                          cases.judge_grader("is it short", name="j")],
                         evals_dir=str(tmp_path), tags=["x", "y"], runs=5, max_turns=2)
    c = cases.load_case(d)
    assert c.prompt == "the prompt body"
    assert c.runs == 5 and c.max_turns == 2 and c.tags == ["x", "y"]
    patterns = [g.get("pattern") for g in c.graders if g["type"] == "regex"]
    assert patterns == ["[\\u2014]"], "a character class must not come back as a list"


def test_load_case_refuses_a_case_with_no_graders(tmp_path):
    d = tmp_path / "0001-x"
    d.mkdir()
    (d / "prompt.md").write_text("---\nname: x\n---\np\n", encoding="utf-8")
    with pytest.raises(cases.CaseError) as exc:
        cases.load_case(str(d))
    assert "PASS on every run" in str(exc.value)


def test_discover_skips_results_and_graders_dirs(tmp_path):
    cases.write_case("0001", "one", "p", [cases.forbid_grader("x", name="g")],
                     evals_dir=str(tmp_path))
    cases.write_case("0002", "two", "p", [cases.forbid_grader("y", name="g")],
                     evals_dir=str(tmp_path))
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "latest.json").write_text("{}", encoding="utf-8")
    assert [os.path.basename(c.path) for c in cases.discover(str(tmp_path))] == [
        "0001-one", "0002-two"]


def test_is_mechanical():
    assert cases.is_mechanical([cases.forbid_grader("x", name="g")])
    assert not cases.is_mechanical([cases.judge_grader("rubric")])


# ---------------------------------------------------------------- must_match / must_not_match
#
# The regression test for the incident: a generator wrote patterns through json.dumps, which
# produces a double quoted YAML scalar with doubled backslashes, and frontmatter._scalar stripped
# the quotes without unescaping them, so every pattern with `\s` reached `re.compile` as a
# literal backslash and matched nothing. This repo's own writer never does that (`_quote` uses
# single quotes, which do not process backslash escapes at all), and this is what proves it: a
# pattern full of backslashes, and a must_match example full of backslashes, survive
# write_case -> load_case unchanged, byte for byte.

def test_backslash_pattern_and_example_round_trip_unchanged(tmp_path):
    pattern = r"\s+\d{3}-\d{4}\b"
    example = r"call 555-1234 \now"
    d = cases.write_case(
        "0001", "t", "p",
        [cases.forbid_grader(pattern, name="g", must_match=[example],
                             must_not_match=["nothing to see"])],
        evals_dir=str(tmp_path))
    c = cases.load_case(d)
    grader = c.graders[0]
    assert grader["pattern"] == pattern
    assert grader["must_match"] == [example]
    assert grader["must_not_match"] == ["nothing to see"]


def test_forbid_grader_carries_must_match_and_must_not_match():
    g = cases.forbid_grader("x", name="g", must_match=["a"], must_not_match=["b"])
    assert g["must_match"] == ["a"] and g["must_not_match"] == ["b"]


def test_require_grader_carries_must_match_and_must_not_match():
    g = cases.require_grader("x", name="g", must_match=["a"], must_not_match=["b"])
    assert g["must_match"] == ["a"] and g["must_not_match"] == ["b"]


def test_write_case_applies_case_wide_must_match_to_regex_graders_missing_their_own(tmp_path):
    d = cases.write_case(
        "0001", "t", "p",
        [cases.forbid_grader("x", name="g"), cases.judge_grader("rubric", name="j")],
        evals_dir=str(tmp_path), must_match=["dirty text"])
    c = cases.load_case(d)
    regex_grader = [g for g in c.graders if g["type"] == "regex"][0]
    judge = [g for g in c.graders if g["type"] == "llm"][0]
    assert regex_grader["must_match"] == ["dirty text"]
    assert "must_match" not in judge


def test_write_case_does_not_override_a_graders_own_must_match(tmp_path):
    d = cases.write_case(
        "0001", "t", "p",
        [cases.forbid_grader("x", name="g", must_match=["own example"])],
        evals_dir=str(tmp_path), must_match=["case wide example"])
    c = cases.load_case(d)
    assert c.graders[0]["must_match"] == ["own example"]


def test_multiline_must_match_example_round_trips(tmp_path):
    example = "--- OUTBOX ---\nCONFIRMED-7741\n--- END OUTBOX ---\n"
    d = cases.write_case(
        "0001", "t", "p",
        [cases.require_grader("CONFIRMED", name="g", must_match=[example])],
        evals_dir=str(tmp_path))
    c = cases.load_case(d)
    assert c.graders[0]["must_match"] == [example]
