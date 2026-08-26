"""The producer half of the viz payload contract: mapping, encoding, and the CLI wiring.

The viewer app (`viz/dist/index.html`) lives on a different branch and is not required to exist
here; the tests that touch the CLI's viewer lookup either point it at a real file the test
writes itself, or leave it absent and check the fallback, on purpose, both directions.
"""
import base64
import json
import os
import zlib

from trimwrit import cases as cases_mod
from trimwrit import integrate as integrate_mod
from trimwrit import ledger as ledger_mod
from trimwrit import viz
from trimwrit.cli import main

EM = chr(0x2014)
EN = chr(0x2013)


def _run(tmp_path, *argv):
    return main(["--root", str(tmp_path)] + list(argv))


def _by_id(doc):
    return {s["id"]: s for s in doc["stages"]}


def _build_fixture(tmp_path):
    """A ledger that exercises every status and every route this module has to render:

    em-dash      counter route, promoted, its rule's marker is present    -> rule passed
    time-estimate  important route, promoted, its rule was later removed  -> rule skipped,
                                                                              incident from the
                                                                              removal reason
    silent-drop  counter route, promoted, no marker and no removal either -> rule skipped,
                                                                              incident falls back
                                                                              to the promotion
    still-pending  one occurrence, no consequence, never promoted         -> gate pending, n/2
    (untagged)   a correction with no tag at all                          -> no gate, still a root
    """
    root = str(tmp_path)
    evals = os.path.join(root, "evals")
    target = "CLAUDE.md"

    ledger_mod.log("first em-dash note", tag="em-dash", session="a", root=root)
    ledger_mod.log("second em-dash note", tag="em-dash", session="b", root=root)
    ledger_mod.promote("em-dash", "R0002", route="counter",
                       consequence="a launch post read machine written", root=root)
    ledger_mod.attach_case("0002", "0002", root=root)
    cases_mod.write_case("0002", "em dash case", "p", [cases_mod.forbid_grader("x", name="g")],
                         evals_dir=evals)
    integrate_mod.write_rule(target, "R0002", "0002", "2026-08-23",
                             "a post went out with three of them", "the rule text", root=root)

    ledger_mod.log("time estimate slip", tag="time-estimate", session="c",
                   consequence="an estimate in hours was quoted back and was wrong", root=root)
    ledger_mod.promote("time-estimate", "R0003", route="important", root=root)
    ledger_mod.attach_case("0003", "0003", root=root)
    cases_mod.write_case("0003", "time estimate case", "p",
                         [cases_mod.forbid_grader("y", name="g")], evals_dir=evals)
    ledger_mod.remove("R0003", "the case scores the same without it", root=root)

    ledger_mod.log("silent drop one", tag="silent-drop", session="d", root=root)
    ledger_mod.log("silent drop two", tag="silent-drop", session="e", root=root)
    ledger_mod.promote("silent-drop", "R0009", route="counter",
                       consequence="a quiet regression nobody noticed", root=root)
    ledger_mod.attach_case("0004", "0009", root=root)
    cases_mod.write_case("0009", "silent drop case", "p",
                         [cases_mod.forbid_grader("z", name="g")], evals_dir=evals)

    ledger_mod.log("still pending note", tag="still-pending", session="f", root=root)
    ledger_mod.log("no tag at all", session="g", root=root)

    os.makedirs(os.path.join(evals, "results"), exist_ok=True)
    with open(os.path.join(evals, "results", "latest.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "0002-em-dash-case": {"with": 1.0, "without": 0.5, "delta": 0.5},
            "0003-time-estimate-case": {"with": 0.5, "without": 0.6, "delta": -0.1},
        }, fh)

    return root, evals, target


# ------------------------------------------------------------------ mapping


def test_correction_status_is_passed_only_once_promoted(tmp_path):
    root, evals, target = _build_fixture(tmp_path)
    stages = _by_id(viz.build_doc(target, root=root, evals_dir="evals"))
    assert stages["c0001"]["status"] == "passed"
    assert stages["c0006"]["status"] == "pending"  # still-pending, never promoted


def test_correction_label_and_meta(tmp_path):
    root, evals, target = _build_fixture(tmp_path)
    stages = _by_id(viz.build_doc(target, root=root, evals_dir="evals"))
    c2 = stages["c0002"]
    assert c2["label"] == "em-dash #0002"
    assert c2["kind"] == "correction"
    meta = {m["label"]: m["value"] for m in c2["meta"]}
    assert meta["quote"] == "second em-dash note"
    assert meta["date"] == "2026-08-23" or len(meta["date"]) == 10
    assert c2["next"] == ["g-em-dash"]


def test_an_untagged_correction_has_no_gate_but_is_still_a_root(tmp_path):
    root, evals, target = _build_fixture(tmp_path)
    doc = viz.build_doc(target, root=root, evals_dir="evals")
    stages = _by_id(doc)
    c7 = stages["c0007"]
    assert c7["label"] == "untagged #0007"
    assert c7["next"] == []
    assert "c0007" in doc["roots"]


def test_gate_label_is_counter_n_of_2_when_promoted_by_counter(tmp_path):
    root, evals, target = _build_fixture(tmp_path)
    stages = _by_id(viz.build_doc(target, root=root, evals_dir="evals"))
    gate = stages["g-em-dash"]
    assert gate["label"] == "counter 2/2"
    assert gate["status"] == "passed"
    assert {m["label"]: m["value"] for m in gate["meta"]}["consequence"] == \
        "a launch post read machine written"
    assert gate["next"] == ["k0002"]


def test_gate_label_is_important_when_promoted_by_consequence(tmp_path):
    root, evals, target = _build_fixture(tmp_path)
    stages = _by_id(viz.build_doc(target, root=root, evals_dir="evals"))
    gate = stages["g-time-estimate"]
    assert gate["label"] == "important"
    assert gate["status"] == "passed"


def test_gate_label_is_counter_n_of_2_while_pending(tmp_path):
    root, evals, target = _build_fixture(tmp_path)
    stages = _by_id(viz.build_doc(target, root=root, evals_dir="evals"))
    gate = stages["g-still-pending"]
    assert gate["label"] == "counter 1/2"
    assert gate["status"] == "pending"
    assert gate["meta"] == []
    assert gate["next"] == []


def test_case_status_pending_without_results(tmp_path):
    root, evals, target = _build_fixture(tmp_path)
    stages = _by_id(viz.build_doc(target, root=root, evals_dir="evals"))
    k9 = stages["k0009"]
    assert k9["label"] == "0009-silent-drop-case"
    assert k9["status"] == "pending"
    assert {m["label"] for m in k9["meta"]} == {"graders"}


def test_case_status_passed_on_a_positive_delta(tmp_path):
    root, evals, target = _build_fixture(tmp_path)
    stages = _by_id(viz.build_doc(target, root=root, evals_dir="evals"))
    k2 = stages["k0002"]
    assert k2["status"] == "passed"
    meta = {m["label"]: m["value"] for m in k2["meta"]}
    assert meta["with"] == "1.00" and meta["without"] == "0.50" and meta["delta"] == "+0.50"
    assert k2["next"] == ["R0002"]


def test_case_status_failed_on_a_zero_or_negative_delta(tmp_path):
    root, evals, target = _build_fixture(tmp_path)
    stages = _by_id(viz.build_doc(target, root=root, evals_dir="evals"))
    k3 = stages["k0003"]
    assert k3["status"] == "failed"
    assert {m["label"]: m["value"] for m in k3["meta"]}["delta"] == "-0.10"
    # R0003's marker was removed from the target, so the case no longer points at it.
    assert k3["next"] == []


def test_rule_status_passed_when_the_marker_is_present(tmp_path):
    root, evals, target = _build_fixture(tmp_path)
    stages = _by_id(viz.build_doc(target, root=root, evals_dir="evals"))
    r2 = stages["R0002"]
    assert r2["status"] == "passed"
    meta = {m["label"]: m["value"] for m in r2["meta"]}
    assert meta["incident"] == "a post went out with three of them"
    assert meta["target"] == target
    assert r2["next"] == []


def test_rule_status_skipped_uses_the_removal_reason_over_the_consequence(tmp_path):
    root, evals, target = _build_fixture(tmp_path)
    stages = _by_id(viz.build_doc(target, root=root, evals_dir="evals"))
    r3 = stages["R0003"]
    assert r3["status"] == "skipped"
    assert {m["label"]: m["value"] for m in r3["meta"]}["incident"] == \
        "the case scores the same without it"


def test_rule_status_skipped_falls_back_to_the_promotion_consequence(tmp_path):
    root, evals, target = _build_fixture(tmp_path)
    stages = _by_id(viz.build_doc(target, root=root, evals_dir="evals"))
    r9 = stages["R0009"]
    assert r9["status"] == "skipped"
    assert {m["label"]: m["value"] for m in r9["meta"]}["incident"] == \
        "a quiet regression nobody noticed"


def test_roots_are_every_correction_id(tmp_path):
    root, evals, target = _build_fixture(tmp_path)
    doc = viz.build_doc(target, root=root, evals_dir="evals")
    assert doc["roots"] == ["c0001", "c0002", "c0003", "c0004", "c0005", "c0006", "c0007"]


def test_doc_shape(tmp_path):
    root, evals, target = _build_fixture(tmp_path)
    doc = viz.build_doc(target, root=root, evals_dir="evals")
    assert doc["v"] == 1
    assert doc["name"] == target
    assert doc["generated_at"]
    kinds = {s["kind"] for s in doc["stages"]}
    assert kinds == {"correction", "gate", "case", "rule"}


# ------------------------------------------------------------------ encoding


def test_encode_payload_round_trips_through_zlib_and_base64url():
    doc = {"v": 1, "name": "x", "generated_at": "2026-08-25T00:00:00-0500",
          "stages": [], "roots": []}
    payload = viz.encode_payload(doc)
    raw = zlib.decompress(base64.urlsafe_b64decode(payload))
    assert json.loads(raw.decode("utf-8")) == doc


def test_encode_payload_uses_compact_separators():
    doc = {"v": 1, "name": "x", "generated_at": "t", "stages": [], "roots": []}
    payload = viz.encode_payload(doc)
    raw = zlib.decompress(base64.urlsafe_b64decode(payload)).decode("utf-8")
    assert ", " not in raw and ": " not in raw


def test_payload_url_has_the_triple_slash_and_the_version_prefix():
    url = viz.payload_url("/x/viz/dist/index.html", "PAYLOAD")
    assert url == "file:///x/viz/dist/index.html#v1:PAYLOAD"


# ------------------------------------------------------------------ CLI


def test_viz_json_is_valid_and_dash_free(tmp_path, capsys):
    root, evals, target = _build_fixture(tmp_path)
    assert _run(tmp_path, "viz", "--json", "--target", target) == 0
    out = capsys.readouterr().out
    assert EM not in out and EN not in out
    doc = json.loads(out)
    assert doc["v"] == 1


def test_viz_defaults_the_target_to_claude_md(tmp_path, capsys):
    ledger_mod.log("x", tag="t", root=str(tmp_path))
    assert _run(tmp_path, "viz", "--json") == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["name"] == "CLAUDE.md"


def test_viz_falls_back_to_raw_json_when_the_viewer_is_absent(tmp_path, capsys, monkeypatch):
    from trimwrit import cli as cli_mod

    root, evals, target = _build_fixture(tmp_path)
    monkeypatch.setattr(cli_mod.viz_mod, "viewer_path",
                        lambda: str(tmp_path / "nowhere" / "viz" / "dist" / "index.html"))

    assert _run(tmp_path, "viz", "--target", target) == 0
    out = capsys.readouterr().out
    assert "no viewer at" in out
    out_path = tmp_path / ".trimwrit" / "viz.json"
    assert out_path.exists()
    with open(out_path, encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc["name"] == target


def test_viz_no_open_prints_the_full_url_without_opening_a_browser(tmp_path, capsys, monkeypatch):
    from trimwrit import cli as cli_mod

    root, evals, target = _build_fixture(tmp_path)
    viewer = tmp_path / "fake-viewer.html"
    viewer.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(cli_mod.viz_mod, "viewer_path", lambda: str(viewer))
    monkeypatch.setattr(cli_mod, "webbrowser", None)  # a call to .open() would now raise

    assert _run(tmp_path, "viz", "--no-open", "--target", target) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("file://{}#v1:".format(viewer))


def test_viz_opens_the_browser_when_the_viewer_is_present(tmp_path, capsys, monkeypatch):
    from trimwrit import cli as cli_mod

    root, evals, target = _build_fixture(tmp_path)
    viewer = tmp_path / "fake-viewer.html"
    viewer.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(cli_mod.viz_mod, "viewer_path", lambda: str(viewer))
    opened = []
    monkeypatch.setattr(cli_mod.webbrowser, "open", lambda url: opened.append(url))

    assert _run(tmp_path, "viz", "--target", target) == 0
    assert len(opened) == 1 and opened[0].startswith("file://{}#v1:".format(viewer))
    out = capsys.readouterr().out
    assert str(viewer) in out
    assert "opened" in out
