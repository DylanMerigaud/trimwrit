from trimwrit import integrate as I
from trimwrit.cli import main


def _seed(tmp_path):
    I.write_rule("CLAUDE.md", "R1", "0001+0002", "2026-08-23", "incident one", "rule one",
                 root=str(tmp_path))
    I.write_rule("CLAUDE.md", "R2", "0003", "2026-08-23", "incident two", "rule two",
                 root=str(tmp_path))


def test_relocate_moves_the_block_and_names_the_cases(tmp_path, capsys):
    _seed(tmp_path)
    dest = "skills/x/SKILL.md"
    assert main(["relocate", "R1", "--to", dest, "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "tokens" in out
    assert "next: trimwrit run --case 0001" in out and "next: trimwrit run --case 0002" in out
    src = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "R1" not in src and "rule one" not in src and "rule two" in src
    moved = (tmp_path / dest).read_text(encoding="utf-8")
    assert "<!-- trimwrit: R1 case 0001+0002, 2026-08-23: incident one -->\nrule one" in moved


def test_relocate_into_a_hash_file_rewrites_the_marker(tmp_path, capsys):
    _seed(tmp_path)
    assert main(["--root", str(tmp_path), "relocate", "R2", "--to", "hooks/rule.sh"]) == 0
    moved = (tmp_path / "hooks" / "rule.sh").read_text(encoding="utf-8")
    assert moved == "# trimwrit: R2 case 0003, 2026-08-23: incident two\nrule two\n"
    assert I.read_rules("hooks/rule.sh", root=str(tmp_path))["R2"]["case"] == "0003"


def test_relocate_refuses_a_destination_that_already_holds_the_rule(tmp_path, capsys):
    _seed(tmp_path)
    I.write_rule("docs/h.md", "R1", "0001", "2026-08-23", "i", "old copy", root=str(tmp_path))
    before = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert main(["--root", str(tmp_path), "relocate", "R1", "--to", "docs/h.md"]) == 1
    assert "already holds" in capsys.readouterr().err
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == before


def test_relocate_a_rule_that_is_not_there(tmp_path, capsys):
    _seed(tmp_path)
    assert main(["--root", str(tmp_path), "relocate", "R9", "--to", "docs/h.md",
                 "--from", "CLAUDE.md,other.md"]) == 1
    assert "not found" in capsys.readouterr().err
    assert not (tmp_path / "docs" / "h.md").exists()
