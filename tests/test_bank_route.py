"""trimwrit/route.py, trimwrit/facets.py, the slice keys of the RELEASE envelope, and `trimwrit
bank route|explain|facets`: reuse, extend or build a bank, which harnesses run on a key, and what
a bank covers per axis. Every registry here is a fixture module under a temp root, plugged in
through .trimwrit/bank.json, as a project plugs its own."""
import json
import os
import textwrap

import pytest

from trimwrit import bank, facets, route
from trimwrit.cli import main


# ------------------------------------------------------------------ the envelope's slices

def _released(tmp_path, **kw):
    d = tmp_path / "b"
    d.mkdir(exist_ok=True)
    (d / "CORPUS.jsonl").write_text('{"id": "x"}\n')
    return str(d), bank.write_release(str(d), passed=True, n=3, bar="accuracy >= 0.9",
                                      model=None, **kw)


def test_types_and_instances_ride_the_envelope_and_read_as_slices(tmp_path):
    d, doc = _released(tmp_path, types={"cold-mail": {"passed": True, "n": 40}},
                       instances={"acme": {"passed": False, "n": 12, "accuracy": 0.8}})
    assert bank.envelope_problems(doc) == []
    assert bank.released_slice(d, "types", "cold-mail") is True
    assert bank.released_slice(d, "instances", "acme") is False
    assert bank.released_slice(d, "instance", "acme") is False      # singular reads the same
    assert bank.released_slice(d, "instances", "other") is None
    assert bank.released_slice(str(tmp_path / "nowhere"), "types", "x") is None


def test_by_type_and_by_instance_are_read_as_aliases(tmp_path):
    d, _doc = _released(tmp_path, extra={"by_type": {"vendor-mail": {"n": 99, "passed": True}},
                                         "by_instance": {"acme/email": {"n": 5,
                                                                        "passed": False}}})
    assert bank.released_slice(d, "types", "vendor-mail") is True
    assert bank.released_slice(d, "instances", "acme/email") is False


def test_a_release_without_slices_still_carries_the_envelope(tmp_path):
    _d, doc = _released(tmp_path)
    assert "types" not in doc and bank.envelope_problems(doc) == []


@pytest.mark.parametrize("key,value,problem", [
    ("types", {"x": {"n": 3}}, "`types[x]` has no boolean passed"),
    ("instances", {"x": {"passed": "yes", "n": 3}}, "`instances[x]` has no boolean passed"),
    ("types", {"x": {"passed": True, "n": -1}}, "`types[x]` has no count n"),
    ("by_type", {"x": {"passed": True}}, "`by_type[x]` has no count n"),
    ("instances", ["x"], "`instances` is not an object"),
    ("types", {"x": True}, "`types[x]` is not an object"),
])
def test_a_malformed_slice_is_refused(tmp_path, key, value, problem):
    d = tmp_path / "b"
    d.mkdir()
    with pytest.raises(bank.BankError, match=r"has no|is not"):
        bank.write_release(str(d), passed=True, n=1, bar="a >= 0.9", model=None,
                           extra={key: value})
    doc = {"schema": 1, "passed": True, "at": "2026-11-10T00:00:00+00:00", "n": 1,
           "bar": "a", "model": None, "bank_sha": "0" * 12, "run": None, key: value}
    assert problem in bank.envelope_problems(doc)


def test_a_slice_given_twice_is_refused(tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    with pytest.raises(bank.BankError, match="twice"):
        bank.write_release(str(d), passed=True, n=1, bar="a >= 0.9", model=None,
                           types={"x": {"passed": True, "n": 1}},
                           extra={"types": {"x": {"passed": True, "n": 1}}})


def test_facet_keys_are_validated_when_present(tmp_path):
    base = {"schema": 1, "passed": True, "at": "2026-11-10T00:00:00+00:00", "n": 1,
            "bar": "a", "model": None, "bank_sha": "0" * 12, "run": None}
    good = dict(base, covers={"lang": ["en", "fr"]},
                by_facet={"lang": {"en": {"n": 4, "metric": 0.75}, "fr": {"n": 0,
                                                                         "metric": None}}})
    assert bank.envelope_problems(good) == []
    assert "`covers[lang]` is not a list of values" in bank.envelope_problems(
        dict(base, covers={"lang": "en"}))
    assert "`by_facet[lang][en]` has no count n" in bank.envelope_problems(
        dict(base, by_facet={"lang": {"en": {"metric": 1.0}}}))
    assert "`by_facet[lang][en]` has no metric number" in bank.envelope_problems(
        dict(base, by_facet={"lang": {"en": {"n": 3, "metric": "high"}}}))


# ------------------------------------------------------------------ the fixture registry

REGISTRY_PY = '''
import os

ROOT = None
ASKED = []


class Reg:
    def __init__(self, root, answer=None):
        self.root = root
        self.answer = answer

    def assignments(self):
        return {"acme/email": "banks/cold-a", "brick/email": "banks/cold-b",
                "acme/seo": "banks/seo", "brick/seo": "banks/seo",
                "type:cold-mail": "banks/cold-reader"}

    def banks(self):
        return {"banks/cold-a": "first touch figures against the record of acme",
                "banks/cold-b": "first touch figures against the record of brick",
                "banks/seo": "weekly seo report against the search console readouts",
                "banks/cold-reader": "the cold mail read against its reader's job",
                "banks/router": "router"}

    def families(self):
        return {"cold-figures": {"members": ["banks/cold-a", "banks/cold-b"],
                                 "home": "banks/cold-a", "why": "one judgment per bet"}}

    def producer_of(self, key):
        return {"acme/seo": {"seo_reading.py"}, "brick/seo": {"seo_reading.py"},
                "zeta/seo": {"seo_reading.py"}, "acme/email": {"check.py"}}.get(key)

    def family_of(self, key, judges, producer):
        if key.endswith("/email") and "first touch" in (judges or ""):
            return "cold-figures"
        return None

    def covered(self, bank, key):
        return key.split("/")[0] == "brick" and bank == "banks/cold-a"

    def layers(self, key):
        out = [{"layer": 1, "name": "dash gate", "bank": None, "status": "deterministic",
                "why": "no long dash"}]
        out.append({"layer": 2, "name": "type cold-mail", "bank": "banks/cold-reader",
                    "why": "the reader of the type", "slice": ["types", "cold-mail"]})
        if key in self.assignments():
            out.append({"layer": 3, "name": "row " + key, "bank": self.assignments()[key],
                        "why": "the bank of the row"})
        return out


class AskingReg(Reg):
    def ask(self, prompt, schema):
        ASKED.append((prompt, schema))
        return self.answer


def make(root):
    return Reg(root)


def make_asking(root):
    return AskingReg(root, answer={"decision": "reuse", "bank": "banks/seo",
                                   "why": "the same weekly report"})
'''

AXES_PY = '''
from trimwrit.facets import Axis


def axes():
    return {"lang": Axis("lang", ("en", "fr", "de"), lambda s: (s.get("meta") or {}).get("lang"),
                         "a judge reads idiom per language"),
            "audience": Axis("audience", ("cto", "ceo"), lambda s: s.get("audience"),
                             "a cto and a ceo reject for different reasons")}
'''


def _project(tmp_path, registry="make", axes=True):
    root = tmp_path / "proj"
    (root / ".trimwrit").mkdir(parents=True)
    (root / "tools").mkdir()
    (root / "tools" / "reg.py").write_text(textwrap.dedent(REGISTRY_PY))
    (root / "tools" / "axes.py").write_text(textwrap.dedent(AXES_PY))
    conf = {"registry": "tools/reg.py:" + registry}
    if axes:
        conf["axes"] = "tools/axes.py:axes"
    (root / ".trimwrit" / "bank.json").write_text(json.dumps(conf))
    for b, title in (("banks/cold-a", "First touch figures against the record of acme"),
                     ("banks/cold-b", "First touch figures against the record of brick"),
                     ("banks/seo", "Weekly seo report against the search console readouts"),
                     ("banks/cold-reader", "The cold mail read against its reader's job"),
                     ("banks/router", None)):
        d = root / b
        d.mkdir(parents=True)
        (d / "CORPUS.jsonl").write_text("".join('{"id": "%d"}\n' % i for i in range(3)))
        if title:
            (d / "CODEBOOK.md").write_text("# {}: the codebook\n\nBody.\n".format(title))
    return root


def _reg(root, name="make"):
    return route.load_registry(str(root)) if name == "make" else route.load_registry(
        str(root), callable_name=name)


# ------------------------------------------------------------------ the registry plug

def test_load_registry_reads_bank_json_and_calls_the_callable_with_the_root(tmp_path):
    root = _project(tmp_path)
    reg = route.load_registry(str(root))
    assert reg.root == str(root)
    assert "banks/seo" in reg.banks()


def test_a_missing_bank_json_names_the_file_and_the_key(tmp_path):
    with pytest.raises(route.RouteError, match=r"\.trimwrit/bank\.json.*registry"):
        route.load_registry(str(tmp_path))
    (tmp_path / ".trimwrit").mkdir()
    (tmp_path / ".trimwrit" / "bank.json").write_text("{}")
    with pytest.raises(route.RouteError, match=r"no `registry`"):
        route.load_registry(str(tmp_path))
    (tmp_path / ".trimwrit" / "bank.json").write_text('{"registry": "nope.py:make"}')
    with pytest.raises(route.RouteError, match="nope.py"):
        route.load_registry(str(tmp_path))


def test_codebook_judges_reads_the_h1(tmp_path):
    root = _project(tmp_path)
    assert route.codebook_judges(str(root / "banks" / "seo")) == (
        "Weekly seo report against the search console readouts")
    assert route.codebook_judges(str(root / "banks" / "router")) == "router"


# ------------------------------------------------------------------ route, step by step

def test_step_1_registry(tmp_path):
    reg = _reg(_project(tmp_path))
    r = route.route("acme/email", "anything", registry=reg)
    assert (r["decision"], r["bank"], r["how"], r["family"]) == (
        "reuse", "banks/cold-a", "registry", "cold-figures")
    assert set(r) >= {"decision", "bank", "family", "how", "why"}


def test_step_2_judges_equality(tmp_path):
    reg = _reg(_project(tmp_path))
    r = route.route("other/x", "  The cold MAIL read against its reader's job ", registry=reg)
    assert (r["decision"], r["bank"], r["how"]) == ("reuse", "banks/cold-reader", "judges")


def test_step_3_producer(tmp_path):
    reg = _reg(_project(tmp_path))
    r = route.route("zeta/seo", "something unrelated", registry=reg)
    assert (r["decision"], r["bank"], r["how"]) == ("reuse", "banks/seo", "producer")
    # an explicit producer argument outranks the registry's, a path and a site key read as
    # their basename
    r = route.route("new/seo", "something unrelated", producer="lib/seo_reading.py:RUN",
                    registry=reg)
    assert (r["decision"], r["bank"]) == ("reuse", "banks/seo")
    # a key with the same producer and no bank of its own breaks the agreement: no reuse
    reg.keys = lambda: list(reg.assignments()) + ["zeta/seo"]
    r = route.route("new/seo", "something unrelated", producer="seo_reading.py", registry=reg)
    assert (r["decision"], r["how"]) == ("new", "none")


def test_step_4_family_extends_the_home_or_reuses_it_when_covered(tmp_path):
    reg = _reg(_project(tmp_path))
    r = route.route("newbet/email", "first touch figures for newbet", registry=reg)
    assert (r["decision"], r["bank"], r["how"], r["family"]) == (
        "extend", "banks/cold-a", "family", "cold-figures")
    reg2 = _reg(_project(tmp_path / "2"))
    reg2.assignments = lambda: {}
    r = route.route("brick/email", "first touch", registry=reg2)
    assert (r["decision"], r["bank"]) == ("reuse", "banks/cold-a")


def test_step_4_home_gone_falls_to_the_largest_member(tmp_path):
    root = _project(tmp_path)
    reg = _reg(root)
    reg.families = lambda: {"cold-figures": {"members": ["banks/cold-a", "banks/cold-b"],
                                             "home": "banks/gone", "why": "w"}}
    (root / "banks" / "cold-b" / "CORPUS.jsonl").write_text('{"id": 1}\n' * 9)
    r = route.route("newbet/email", "first touch", registry=reg)
    assert r["bank"] == "banks/cold-b"


def test_step_5_no_candidate_is_new(tmp_path):
    reg = _reg(_project(tmp_path))
    r = route.route("x/y", "a wholly different judgment about invoices", registry=reg)
    assert (r["decision"], r["bank"], r["how"]) == ("new", None, "none")


def test_step_5_candidates_by_token_overlap_without_ask_answers_ask(tmp_path):
    reg = _reg(_project(tmp_path))
    r = route.route("x/y", "monthly seo report from search console data", registry=reg)
    assert r["decision"] == "ask" and r["bank"] is None and r["how"] == "candidates"
    assert r["candidates"][0] == "banks/seo"


def test_candidates_use_the_thresholds_and_skip_a_bare_slug(tmp_path):
    reg = _reg(_project(tmp_path))
    assert route.CANDIDATE_MIN_SHARED == 2 and route.CANDIDATE_MIN_OVERLAP == 0.34
    cands = route.candidates(reg, "seo report search", root=reg.root)
    assert [c[0] for c in cands] == ["banks/seo"]
    assert cands[0][1] == ["report", "search", "seo"] and cands[0][2] == 1.0
    # one shared word is not enough
    assert route.candidates(reg, "seo invoices ledger", root=reg.root) == []
    # banks/router's line is its own name and it has no codebook: never a candidate
    assert all(c[0] != "banks/router" for c in route.candidates(reg, "router router",
                                                                 root=reg.root))


def test_step_6_ask_is_the_registrys_model_call(tmp_path):
    root = _project(tmp_path)
    import importlib  # noqa: PLC0415
    reg = route.load_registry(str(root), callable_name="make_asking")
    mod = importlib.import_module(type(reg).__module__)
    r = route.route("x/y", "monthly seo report from search console data", registry=reg)
    assert (r["decision"], r["bank"], r["how"]) == ("reuse", "banks/seo", "model")
    prompt, schema = mod.ASKED[-1]
    assert "banks/seo" in prompt and schema["required"] == ["decision", "bank", "why"]
    reg.answer = {"decision": "reuse", "bank": "banks/elsewhere", "why": "w"}
    assert route.route("x/y", "monthly seo report from search console data",
                       registry=reg)["decision"] == "new"
    reg.answer = {"decision": "maybe", "bank": None, "why": "w"}
    assert route.route("x/y", "monthly seo report from search console data",
                       registry=reg)["decision"] == "new"


# ------------------------------------------------------------------ the CLI

def _cli(root, *argv):
    return main(["--root", str(root), "bank"] + list(argv))


def test_cli_route_prints_and_exits_0_or_3(tmp_path, capsys):
    root = _project(tmp_path)
    assert _cli(root, "route", "acme/email", "--judges", "x") == 0
    assert capsys.readouterr().out.startswith("reuse banks/cold-a (registry, family cold-figures)")
    assert _cli(root, "route", "newbet/email", "--judges", "first touch", "--json") == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "extend"
    assert _cli(root, "route", "x/y", "--judges", "monthly seo report search console") == 3
    assert "ask" in capsys.readouterr().out
    # --root after the subcommand reads the same
    assert main(["bank", "route", "acme/email", "--judges", "x", "--root", str(root)]) == 0


def test_cli_route_without_bank_json_exits_2(tmp_path, capsys):
    assert _cli(tmp_path, "route", "a/b", "--judges", "x") == 2
    assert ".trimwrit/bank.json" in capsys.readouterr().err


def test_cli_explain_prints_layers_with_their_release_status(tmp_path, capsys):
    root = _project(tmp_path)
    bank.write_release(str(root / "banks" / "cold-a"), passed=True, n=3, bar="a >= 0.9",
                       model=None)
    bank.write_release(str(root / "banks" / "cold-reader"), passed=True, n=3, bar="a >= 0.9",
                       model=None, types={"cold-mail": {"passed": False, "n": 3}})
    assert _cli(root, "explain", "acme/email") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("1 dash gate  -  deterministic")
    assert lines[1].startswith("2 type cold-mail  banks/cold-reader  failing")
    assert lines[2].startswith("3 row acme/email  banks/cold-a  released")
    assert _cli(root, "explain", "brick/email", "--json") == 0
    layers = json.loads(capsys.readouterr().out)
    assert layers[2]["bank"] == "banks/cold-b" and layers[2]["status"] == "unreleased"


def test_explain_reads_a_broken_envelope_as_invalid(tmp_path):
    root = _project(tmp_path)
    (root / "banks" / "seo" / "RELEASE.json").write_text('{"released": true}')
    assert route.bank_status(str(root), "banks/seo") == "invalid"


# ------------------------------------------------------------------ facets: the axes

def _two(a, b, n):
    return {"a": {"n": n, "metric": a}, "b": {"n": n, "metric": b}}


def test_the_divergence_constants_are_declared():
    assert facets.MIN_N == 20 and facets.GAP == 0.10


@pytest.mark.parametrize("by_value,verdict", [
    (_two(0.95, 0.65, 30), "split"),
    (_two(0.95, 0.65, 5), "unmeasured"),
    (_two(0.95, 0.90, 50), "pooled"),
    ({b: {"n": 200, "metric": 1.0} for b in ("x", "y", "z")}, "pooled"),
])
def test_diverges_on_the_four_shapes(by_value, verdict):
    d = facets.diverges(by_value)
    assert d["verdict"] == verdict
    assert d["split"] is (verdict == "split")
    assert d["why"]


def test_one_value_over_min_n_is_unmeasured():
    d = facets.diverges({"a": {"n": 50, "metric": 1.0}, "b": {"n": 19, "metric": 0.0}})
    assert d["verdict"] == "unmeasured" and d["values"] == ["a"]


def test_coverage_counts_each_value_and_its_metric():
    rows = ([{"facets": {"lang": "en"}, "right": True}] * 3
            + [{"facets": {"lang": "en"}, "right": False}]
            + [{"facets": {"lang": "fr"}, "right": True}] * 2
            + [{"facets": {}, "right": True}])
    covers, by = facets.coverage(rows, ("lang", "industry"))
    assert covers == {"lang": ["en", "fr"]}
    assert by["lang"] == {"en": {"n": 4, "metric": 0.75}, "fr": {"n": 2, "metric": 1.0}}
    assert "industry" not in covers and "industry" not in by
    extra = facets.release_extra(rows, ("lang",))
    assert set(extra) == {"covers", "by_facet"}


def test_coverage_takes_a_numeric_score_or_a_callable():
    rows = [{"facets": {"lang": "en"}, "score": 0.5}, {"facets": {"lang": "en"}, "score": 1.0}]
    assert facets.coverage(rows, ("lang",), "score")[1]["lang"]["en"]["metric"] == 0.75
    assert facets.coverage(rows, ("lang",), lambda rs: len(rs))[1]["lang"]["en"]["metric"] == 2


def test_release_extra_writes_through_the_envelope(tmp_path):
    rows = [{"facets": {"lang": "en"}, "right": True}]
    d = tmp_path / "b"
    d.mkdir()
    doc = bank.write_release(str(d), passed=True, n=1, bar="a >= 0.9", model=None,
                             extra=facets.release_extra(rows, ("lang",)))
    assert doc["covers"] == {"lang": ["en"]} and bank.envelope_problems(doc) == []


def test_vector_resolves_and_facets_override(tmp_path):
    axes = facets.load_axes(str(_project(tmp_path)))
    assert axes["lang"].values() == ("en", "fr", "de")
    assert facets.vector({"meta": {"lang": "fr"}}, axes) == {"lang": "fr"}
    assert facets.vector({"meta": {"lang": "fr"}, "facets": {"lang": "de",
                                                             "audience": "cto"}}, axes) == {
        "lang": "de", "audience": "cto"}
    callable_vocab = facets.Axis("mood", lambda: ("calm", "loud"), lambda s: None, "w")
    assert callable_vocab.values() == ("calm", "loud")


def test_a_missing_axes_key_is_a_clear_error(tmp_path):
    root = _project(tmp_path, axes=False)
    with pytest.raises(route.RouteError, match="no `axes`"):
        facets.load_axes(str(root))


RELEASES = {"banks/cold-reader": {
    "covers": {"lang": ["en", "fr"], "audience": ["ceo"]},
    "by_facet": {"lang": {"en": {"n": 40, "metric": 1.0}, "fr": {"n": 30, "metric": 0.98}},
                 "audience": {"ceo": {"n": 40, "metric": 1.0}}}}}


def _entry(entries, axis):
    return next(e for e in entries if e["axis"] == axis)


def test_route_facets_covered_pooled_extend_unknown(tmp_path):
    axes = facets.load_axes(str(_project(tmp_path)))
    axes["industry"] = facets.Axis("industry", ("fintech",), lambda s: s.get("industry"), "w")
    out = facets.route_facets({"meta": {"lang": "fr"}, "audience": "cto",
                               "industry": "fintech"}, axes, banks=["banks/cold-reader"],
                              releases=RELEASES)
    assert _entry(out, "lang")["decision"] == "pooled"
    assert _entry(out, "audience")["decision"] == "extend"
    assert _entry(out, "industry")["decision"] == "unknown"
    assert {e["decision"] for e in out} <= set(facets.DECISIONS)


def test_route_facets_covered_and_split_when_the_axis_diverges(tmp_path):
    axes = facets.load_axes(str(_project(tmp_path)))
    rel = {"b": {"covers": {"lang": ["en", "fr"]},
                 "by_facet": {"lang": {"en": {"n": 30, "metric": 0.95},
                                       "fr": {"n": 30, "metric": 0.65}}}}}
    e = _entry(facets.route_facets({"meta": {"lang": "en"}}, axes, banks=["b"], releases=rel),
               "lang")
    assert e["decision"] == "covered" and "split" in e["why"]


def test_route_facets_new_with_no_bank(tmp_path):
    axes = facets.load_axes(str(_project(tmp_path)))
    out = facets.route_facets({"meta": {"lang": "en"}}, axes, banks=[], releases={})
    assert out[0]["axis"] == "bank" and out[0]["decision"] == "new"
    assert _entry(out, "lang")["decision"] == "unknown"


def test_an_axis_no_case_carries_routes_unknown_not_extend(tmp_path):
    axes = facets.load_axes(str(_project(tmp_path)))
    covers, by = facets.coverage([{"facets": {"lang": "en"}, "right": True}],
                                 ("lang", "audience"))
    out = facets.route_facets({"audience": "cto"}, axes, banks=["b"],
                              releases={"b": {"covers": covers, "by_facet": by}})
    assert _entry(out, "audience")["decision"] == "unknown"


def test_cli_facets_routes_a_key_through_the_registry(tmp_path, capsys):
    root = _project(tmp_path)
    bank.write_release(str(root / "banks" / "cold-a"), passed=True, n=3, bar="a >= 0.9",
                       model=None, extra={"covers": {"lang": ["en"]},
                                          "by_facet": {"lang": {"en": {"n": 3,
                                                                       "metric": 1.0}}}})
    subject = json.dumps({"key": "acme/email", "meta": {"lang": "fr"}})
    assert _cli(root, "facets", subject, "--json") == 0
    out = json.loads(capsys.readouterr().out)
    assert _entry(out, "lang")["decision"] == "extend"
    assert _entry(out, "lang")["bank"] == "banks/cold-a"
    assert _cli(root, "facets", "nowhere/x") == 0
    assert "new" in capsys.readouterr().out
