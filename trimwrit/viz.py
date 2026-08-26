"""Serialize the harness pipeline into the payload the viz web app reads.

`trimwrit viz` builds one graph, correction -> gate -> case -> rule, from data this package
already owns: the ledger (`ledger.py`), the eval cases (`cases.py`), the target file's rule
markers (`integrate.py`) and, when it exists, the last `trimwrit run`. It never re-parses any of
those on its own; every field below is read through the module that already owns that file.

The wire format is a contract shared with a separate web app (`viz/dist/index.html`, built by a
different agent on a different branch and not required to exist on this one). Both sides
implement it exactly, so nothing here should be "improved" without bumping DOC_VERSION on both:

    {"v": 1, "name": <target>, "generated_at": <iso8601>, "stages": [...], "roots": [...]}

Each stage is {"id", "kind", "label", "status", "meta": [{"label", "value"}, ...], "next": [...]}.
`kind` is one of correction, gate, case, rule. `status` is one of pending, passed, failed,
skipped. `roots` is every correction id: they are the only nodes with no incoming edge.

The transport is a URL fragment, not a request: `file://<abs viewer>#v1:<payload>`, where
`<payload>` is the JSON above, minified, deflated, and base64url encoded. Nothing is sent
anywhere; the fragment never leaves the browser that opens the file.
"""
import base64
import json
import os
import time
import zlib

from . import cases as cases_mod
from . import integrate as integrate_mod
from . import ledger as ledger_mod

DOC_VERSION = 1

STATUS_PENDING = "pending"
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

KIND_CORRECTION = "correction"
KIND_GATE = "gate"
KIND_CASE = "case"
KIND_RULE = "rule"

RESULTS_NAME = "latest.json"


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _correction_stage(row):
    tag = row.get("tag")
    label = "{} #{}".format(tag, row["id"]) if tag else "untagged #{}".format(row["id"])
    meta = [{"label": "date", "value": (row.get("at") or "")[:10]},
            {"label": "quote", "value": row.get("text", "")}]
    status = STATUS_PASSED if row.get("promoted") else STATUS_PENDING
    return {"id": "c{}".format(row["id"]), "kind": KIND_CORRECTION, "label": label,
            "status": status, "meta": meta, "next": ["g-{}".format(tag)] if tag else []}


def _gate_stage(tag, corrections_for_tag, promotion, unpromoted_n, case_ids):
    """One gate per tag. `promotion` is the ledger's promotion entry for the tag, or None when
    the tag has not earned a rule yet: everything else in this function is just picking the
    label and status that follow from which of those two states it is in.
    """
    if promotion:
        route = promotion.get("route")
        if route == ledger_mod.ROUTE_IMPORTANT:
            label = "important"
        else:
            label = "counter {}/2".format(len(promotion.get("corrections") or []))
        status = STATUS_PASSED
        consequence = promotion.get("consequence")
    else:
        label = "counter {}/2".format(unpromoted_n)
        status = STATUS_PENDING
        consequence = next((r.get("consequence") for r in corrections_for_tag
                            if r.get("consequence")), None)

    meta = [{"label": "consequence", "value": consequence}] if consequence else []
    return {"id": "g-{}".format(tag), "kind": KIND_GATE, "label": label, "status": status,
            "meta": meta, "next": ["k{}".format(cid) for cid in case_ids]}


def _case_stage(case, summary, rule_ids):
    base = os.path.basename(case.path)
    cid = base.split("-", 1)[0]
    meta = [{"label": "graders", "value": str(len(case.graders))}]
    if summary is None:
        status = STATUS_PENDING
    else:
        delta = summary.get("delta", 0.0)
        meta.append({"label": "with", "value": "{:.2f}".format(summary.get("with", 0.0))})
        meta.append({"label": "without", "value": "{:.2f}".format(summary.get("without", 0.0))})
        meta.append({"label": "delta", "value": "{:+.2f}".format(delta)})
        status = STATUS_PASSED if delta > 0 else STATUS_FAILED
    return {"id": "k{}".format(cid), "kind": KIND_CASE, "label": base, "status": status,
            "meta": meta, "next": sorted(rule_ids)}


def _rule_stage(rule_id, marker_meta, promotion, removal_reason, target):
    if marker_meta:
        incident = marker_meta.get("incident", "")
        status = STATUS_PASSED
    else:
        incident = removal_reason or (promotion.get("consequence") if promotion else "") or ""
        status = STATUS_SKIPPED
    meta = [{"label": "incident", "value": incident}, {"label": "target", "value": target}]
    return {"id": rule_id, "kind": KIND_RULE, "label": rule_id, "status": status,
            "meta": meta, "next": []}


def _load_results(evals_path):
    path = os.path.join(evals_path, "results", RESULTS_NAME)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_doc(target, root=".", evals_dir="evals", now=None):
    """The whole document, per the contract at the top of this file.

    `target` is the rule file the graph is built against (the same argument `run` and `prune`
    call `--target`, one file, not the comma separated list those two accept).
    """
    all_rows = ledger_mod.rows(root=root)
    folded_rows = ledger_mod.folded(root=root)
    unpromoted_counts = ledger_mod.counts(root=root)

    corrections_by_tag = {}
    for r in folded_rows:
        tag = r.get("tag")
        if tag:
            corrections_by_tag.setdefault(tag, []).append(r)

    promotions = [r for r in all_rows if r.get("kind") == ledger_mod.KIND_PROMOTION]
    promotion_by_tag = {}
    for p in promotions:
        promotion_by_tag[p["tag"]] = p  # append order: a later promotion wins, same as live_rules

    removal_reason = {}
    for r in all_rows:
        if r.get("kind") == ledger_mod.KIND_REMOVAL:
            removal_reason[r.get("rule")] = r.get("reason")

    evals_path = os.path.join(root, evals_dir)
    rules_meta = integrate_mod.read_rules(target, root)
    rule_ids_by_case = {}
    for rule_id, meta in rules_meta.items():
        for cid in integrate_mod.case_list(meta["case"]):
            rule_ids_by_case.setdefault(cid, []).append(rule_id)
    results = _load_results(evals_path)

    stages = []
    roots = []

    for r in folded_rows:
        stages.append(_correction_stage(r))
        roots.append("c{}".format(r["id"]))

    for tag in sorted(corrections_by_tag):
        rows_for_tag = corrections_by_tag[tag]
        case_ids = sorted({r["case"] for r in rows_for_tag if r.get("case")})
        stages.append(_gate_stage(tag, rows_for_tag, promotion_by_tag.get(tag),
                                  unpromoted_counts.get(tag, 0), case_ids))

    for case in cases_mod.discover(evals_path):
        base = os.path.basename(case.path)
        cid = base.split("-", 1)[0]
        stages.append(_case_stage(case, results.get(base), rule_ids_by_case.get(cid, [])))

    # Every rule ever promoted, not only the ones still live: a pruned rule is exactly the
    # `skipped` case the contract asks for, and it can only be shown by looking past the
    # target file to the ledger's own promotion record.
    seen_rules = []
    for p in promotions:
        rid = p.get("rule")
        if rid and rid not in seen_rules:
            seen_rules.append(rid)
    for rule_id in seen_rules:
        stages.append(_rule_stage(rule_id, rules_meta.get(rule_id),
                                  next((p for p in promotions if p.get("rule") == rule_id), None),
                                  removal_reason.get(rule_id), target))

    return {"v": DOC_VERSION, "name": target, "generated_at": now or _now(),
            "stages": stages, "roots": roots}


def encode_payload(doc):
    """base64url(zlib(minified utf-8 json)). Exactly this, both sides depend on the exact
    algorithm: `DecompressionStream("deflate")` on the JS side reads a zlib-wrapped stream, which
    is what `zlib.compress` produces, not raw deflate."""
    blob = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(zlib.compress(blob)).decode("ascii")


def plugin_root():
    """Where THIS package's own repo or installed plugin lives, not `--root` (the project being
    tracked). Mirrors the fallback in `bin/trimwrit`: `CLAUDE_PLUGIN_ROOT` is set when Claude
    Code runs the plugin, and a plain clone derives the same path from this file's location."""
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        return env
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def viewer_path():
    return os.path.join(plugin_root(), "viz", "dist", "index.html")


def payload_url(viewer_abspath, payload):
    """`file:///<abs>#v1:<payload>`. The leading `/` of the third slash is the viewer path's own
    leading slash, not something added here."""
    return "file://{}#v{}:{}".format(os.path.abspath(viewer_abspath), DOC_VERSION, payload)
