"""`trimwrit map`: every harness of every repo as one coverage tree, rendered into one page.

No model call, no network. Every tree is a fake laptop under `tmp_path`, with HOME pointed at it
so the global settings read are the fake ones, and every ledger line is written through
`ledger.log` and `ledger.promote`, the doors the rest of the suite already trusts.
"""
import json
import os

from trimwrit import ledger
from trimwrit import map as map_mod
from trimwrit.cli import main
from trimwrit.text import BAD_DASHES

CASE_PROMPT = "---\nname: 0001-x\nruns: 1\n---\n\nreplay the situation\n"
CASE_GRADER = ("---\ntype: regex\nname: forbids-x\npattern: 'x'\nmatch: not_contains\n"
               "target: last_message\n---\n")


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _laptop(tmp_path, monkeypatch):
    """~/Code/withcase: a case, a hook on a missing script, a hook on a present one, a plugin,
    a ledger with a pending tag, a relapsed tag and a live rule. ~/Code/nocase: a CLAUDE.md and
    nothing else. ~/.claude: a plugin and a hook on a project relative script it cannot
    resolve."""
    home = str(tmp_path)
    monkeypatch.setenv("HOME", home)
    a = os.path.join(home, "Code", "withcase")
    os.makedirs(os.path.join(a, ".git"))
    _write(os.path.join(a, "CLAUDE.md"), "# withcase\n\nbody\n")
    _write(os.path.join(a, "evals", "0001-x", "prompt.md"), CASE_PROMPT)
    _write(os.path.join(a, "evals", "0001-x", "graders", "g.md"), CASE_GRADER)
    _write(os.path.join(a, "hooks", "present.sh"), "#!/bin/sh\n")
    _write(os.path.join(a, ".claude", "settings.json"), json.dumps({
        "enabledPlugins": {"some-gate@market": True, "off-gate@market": False},
        "hooks": {"Stop": [{"matcher": "", "hooks": [
            {"type": "command", "command": "python3 $CLAUDE_PROJECT_DIR/hooks/gone.py"},
            {"type": "command", "command": "sh \"${CLAUDE_PROJECT_DIR}/hooks/present.sh\""},
            {"type": "command", "command": "echo no script here"},
        ]}]},
    }))
    ledger.log("first", tag="pending-tag", root=a)
    ledger.log("second", tag="pending-tag", root=a)
    ledger.log("one", tag="ruled-tag", root=a)
    ledger.log("two", tag="ruled-tag", root=a)
    ledger.promote("ruled-tag", "R0001", root=a)
    ledger.log("three, after the rule", tag="ruled-tag", root=a)
    ledger.log("four, after the rule", tag="ruled-tag", root=a)

    b = os.path.join(home, "Code", "nocase")
    os.makedirs(os.path.join(b, ".git"))
    _write(os.path.join(b, "CLAUDE.md"), "# nocase\n")

    _write(os.path.join(home, ".claude", "CLAUDE.md"), "# global\n")
    _write(os.path.join(home, ".claude", "settings.json"), json.dumps({
        "enabledPlugins": {"global-plugin@market": True},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "python3 $CLAUDE_PROJECT_DIR/x.py"},
            {"type": "command", "command": "python3 ~/scripts/also_gone.py"},
        ]}]},
    }))
    return home


def _find(doc, **kw):
    return [i for i in doc["items"] if all(i.get(k) == v for k, v in kw.items())]


def test_collector_files_rules_cases_doors_and_learning(tmp_path, monkeypatch):
    _laptop(tmp_path, monkeypatch)
    doc = map_mod.validate(map_mod.collect(laptop=True))
    assert doc["v"] == 1
    assert doc["errors"] == []
    assert list(doc["types"]) == ["rules", "doors", "learning"]

    (with_rule,) = _find(doc, project="withcase", type="rules", name="CLAUDE.md")
    assert with_rule["state"] == "ok" and with_rule["path"] == ["CLAUDE.md"]
    (no_case,) = _find(doc, project="nocase", type="rules")
    assert no_case["state"] == "no_case"
    assert "lines" in no_case["detail"]
    (case,) = _find(doc, project="withcase", path=["cases"])
    assert case["name"] == "0001-x" and case["state"] == "ok"
    (glob_rule,) = _find(doc, project="~/.claude", type="rules")
    assert glob_rule["state"] == "no_case"

    (plugin,) = _find(doc, project="withcase", type="doors", path=["plugins"])
    assert plugin["name"] == "some-gate@market" and plugin["state"] == "ok"
    (gone,) = _find(doc, project="withcase", name="gone.py")
    assert gone["state"] == "door_missing" and gone["path"] == ["hooks", "Stop"]
    (present,) = _find(doc, project="withcase", name="present.sh")
    assert present["state"] == "ok"
    assert len(_find(doc, project="withcase", type="doors")) == 3  # the echo hook is skipped

    assert [i["name"] for i in _find(doc, project="~/.claude", type="doors")] == [
        "global-plugin@market", "also_gone.py"]
    assert _find(doc, project="~/.claude", name="also_gone.py")[0]["state"] == "door_missing"

    (pending,) = _find(doc, type="learning", name="pending-tag")
    assert pending["state"] == "no_door" and pending["weight"] == 2
    states = sorted(i["state"] for i in _find(doc, type="learning", name="ruled-tag"))
    assert states == ["ok", "relapse"]


def test_hook_script_resolution():
    assert map_mod.hook_script("python3 $CLAUDE_PROJECT_DIR/a.py", "/r") == "/r/a.py"
    assert map_mod.hook_script("bash scripts/b.sh --x", "/r") == "/r/scripts/b.sh"
    assert map_mod.hook_script("python3 $CLAUDE_PROJECT_DIR/a.py", None) is None
    assert map_mod.hook_script("python3 $OTHER/a.py", "/r") is None
    assert map_mod.hook_script("echo hi", "/r") is None
    assert map_mod.hook_script("node /abs/c.js", None) == "/abs/c.js"


def test_cli_json_and_page(tmp_path, monkeypatch, capsys):
    _laptop(tmp_path, monkeypatch)
    assert main(["map", "--laptop", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert any(i["state"] == "door_missing" for i in doc["items"])

    out = str(tmp_path / "page.html")
    assert main(["map", "--laptop", "--out", out]) == 0
    line = capsys.readouterr().out
    assert "item(s)" in line and "gap(s)" in line
    page = open(out, encoding="utf-8").read()
    assert map_mod.PLACEHOLDER not in page
    assert "gone.py" in page


def test_map_needs_an_output(capsys):
    assert main(["map"]) == 2


def test_extra_merges_types_states_items_and_later_wins(tmp_path, capsys):
    base = map_mod.empty_doc()
    base["items"].append(map_mod.item("p", "rules", "CLAUDE.md", "ok"))
    first = {"v": 1, "types": {"banks": "Eval banks", "rules": "Rule files"},
             "states": {"never_run": {"label": "never run", "gap": True, "soft": True,
                                      "reco": "run it once"}},
             "items": [{"project": "p", "type": "banks", "path": ["Paris", "bet"],
                        "name": "bank-a", "state": "never_run", "detail": "", "weight": 4,
                        "reco": ""}],
             "errors": ["banks: one source unread"]}
    second = {"v": 1, "types": {"banks": "Banks"}}
    paths = []
    for n, d in enumerate((base, first, second)):
        p = tmp_path / "d{}.json".format(n)
        p.write_text(json.dumps(d), encoding="utf-8")
        paths.append(str(p))

    assert main(["map", "--from", paths[0], "--extra", paths[1], paths[2], "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["types"]["banks"] == "Banks"          # the later extra wins
    assert doc["types"]["rules"] == "Rule files"     # an extra overrides a base key
    assert list(doc["types"])[:3] == ["rules", "doors", "learning"]
    assert doc["states"]["never_run"]["soft"] is True
    assert [i["name"] for i in doc["items"]] == ["CLAUDE.md", "bank-a"]
    assert doc["items"][1]["path"] == ["Paris", "bet"]
    assert doc["errors"] == ["banks: one source unread"]


def test_unknown_state_and_type_are_errors_not_crashes(tmp_path):
    doc = map_mod.empty_doc()
    doc["items"] += [
        {"project": "p", "type": "rules", "name": "a", "state": "mystery"},
        {"project": "p", "type": "nowhere", "name": "b", "state": "ok", "path": "x",
         "weight": "heavy"},
        "not an item",
    ]
    clean = map_mod.validate(doc)
    assert len(clean["items"]) == 2
    errors = " | ".join(clean["errors"])
    assert "unknown state 'mystery'" in errors
    assert "unknown type 'nowhere'" in errors
    assert "weight 'heavy'" in errors
    assert "item 2 is not an object" in errors
    assert clean["items"][1]["path"] == ["x"] and clean["items"][1]["weight"] == 1
    assert map_mod.is_gap(clean, clean["items"][0])    # an unknown state counts as a gap
    assert map_mod.counts(clean)["gaps"] == 1

    out = tmp_path / "page.html"
    src = tmp_path / "doc.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    assert main(["map", "--from", str(src), "--out", str(out)]) == 0
    assert "unknown state" in out.read_text(encoding="utf-8")


def test_unreadable_from_is_refused(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert main(["map", "--from", str(bad), "--json"]) == 2
    assert "refused" in capsys.readouterr().err


def test_render_escapes_script_close_and_bad_dashes():
    doc = map_mod.empty_doc()
    doc["items"].append(map_mod.item(
        "p", "rules", "</script><script>alert(1)</script> <!-- a" + BAD_DASHES[0] + "b",
        "ok"))
    page = map_mod.render(map_mod.validate(doc))
    data = page.split("const RAW = ", 1)[1].split(";\n", 1)[0]
    assert "</script" not in data.lower()
    assert "<!--" not in data
    assert page.lower().count("</script") == 1     # only the template's own closing tag
    assert not any(ch in page for ch in BAD_DASHES)
    assert json.loads(data)["items"][0]["name"].startswith("</script>")


def test_template_has_no_external_script_and_one_placeholder():
    page = open(map_mod.template_path(), encoding="utf-8").read()
    assert page.count(map_mod.PLACEHOLDER) == 1
    assert "<script src" not in page


def test_render_puts_the_name_in_the_static_title():
    doc = map_mod.validate({"v": 1, "name": "A & <B>", "items": []})
    assert "<title>A &amp; &lt;B&gt;</title>" in map_mod.render(doc)
    assert "<title>Harness map</title>" in map_mod.render(map_mod.validate({"v": 1, "items": []}))
