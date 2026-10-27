"""Every harness of every repo as one coverage tree, in one self contained page.

`audit` answers how many harnesses exist and which repos have no case, as a table. `viz` draws
the loop of ONE rule file. Neither shows the whole machine as something a person can fold open
on a phone: project, then the kind of harness, then whatever sub classification that kind has,
down to the item, each item with a state that says whether it is covered and, when it is not,
what to do about it.

This module builds that tree as a plain document and renders it into a page with the document
embedded, no network, so the page can be opened from disk or published anywhere as is:

    {"v": 1, "name": str, "generated_at": iso,
     "types": {code: label},                 # display order is dict order
     "states": {code: {"label", "gap", "soft", "reco"}},
     "items": [{"project", "type", "path": [str, ...], "name", "state", "detail", "weight",
                "reco"}],
     "errors": [str]}

`path` is the sub classification under the type, any depth, empty for an item that sits right
under its type. A state with `soft: true` is a gap shown in amber and left out of the top gaps.
An item's own `reco` overrides its state's.

The collector reads three families from the same repos `audit` finds (same `--roots` and
`--laptop`, same exclusions, same backing repo rule): rule files and cases, doors (enabled
plugins and hooks from the Claude Code settings), and learning (the tags of each ledger, pending
or turned into a live rule). A project adds its own families by writing a document of its own
and passing it as `--extra`: types, states, items and errors are merged, later wins on a key.

Read only, like `audit`: no file is written but the page asked for.
"""
import json
import os
import shlex
import time

from . import audit as audit_mod
from . import ledger as ledger_mod
from .text import BAD_DASHES

DOC_VERSION = 1
DEFAULT_NAME = "Harness map"
GLOBAL_PROJECT = "~/.claude"
TEMPLATE_NAME = "map_page.html"
PLACEHOLDER = "/*__DATA__*/null"

TYPES = {
    "rules": "Rules",
    "doors": "Doors",
    "learning": "Learning",
}


def _state(label, gap, reco="", soft=False):
    return {"label": label, "gap": gap, "soft": soft, "reco": reco}


STATES = {
    "ok": _state("covered", False),
    "no_case": _state("no case", True, "write a trimwrit case that replays what the rule "
                                       "prevents, otherwise nothing says the rule still does "
                                       "anything"),
    "no_door": _state("no door", True, "the tag has earned a rule: turn a correction into a case "
                                       "(trimwrit case), then write the rule (trimwrit "
                                       "integrate)"),
    "relapse": _state("relapse", True, "corrected again after its rule landed: read the new "
                                       "corrections and harden the rule or its door"),
    "door_missing": _state("door missing", True, "the hook's script is not on this machine, so "
                                                 "the door never runs: restore the script or "
                                                 "remove the hook"),
    "unfiled": _state("unfiled", True, "file this item under a known type and state"),
}

# A token of a hook command ending in one of these is the script the hook runs.
SCRIPT_EXT = (".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl")
PROJECT_DIR_VARS = ("${CLAUDE_PROJECT_DIR}", "$CLAUDE_PROJECT_DIR")
DOOR_MISSING_WEIGHT = 3


class MapError(Exception):
    """A document that cannot be read at all (the file named by --from)."""


def item(project, type_, name, state, path=None, detail="", weight=1, reco=""):
    return {"project": project, "type": type_, "path": list(path or []), "name": name,
            "state": state, "detail": detail, "weight": weight, "reco": reco}


def project_name(display):
    """`~/.claude` keeps its name, every other repo is its directory name: the tree is read on a
    phone, and `~/Code/` in front of every project is noise."""
    display = display.rstrip("/") or display
    if display in (GLOBAL_PROJECT, "~"):
        return display
    return os.path.basename(display) or display


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def empty_doc(name=DEFAULT_NAME):
    return {"v": DOC_VERSION, "name": name, "generated_at": _now_iso(),
            "types": dict(TYPES), "states": {k: dict(v) for k, v in STATES.items()},
            "items": [], "errors": []}


# -- rules: harness files and cases ----------------------------------------------------------------

def _harness_kind(rel):
    parts = rel.replace(os.sep, "/").split("/")
    if parts[-1] == "SKILL.md":
        return "skills"
    if len(parts) >= 2 and parts[-2] == "rules":
        return "rules"
    return "CLAUDE.md"


def _case_dirs(evals_dir):
    """Every case directory under `evals_dir`, walked the way `cases.discover` walks it, without
    loading a case: a malformed one is still a case in an inventory."""
    out = []
    for root, dirs, files in os.walk(evals_dir):
        dirs[:] = [d for d in sorted(dirs) if d not in ("results", "graders")]
        if "prompt.md" in files:
            out.append(os.path.relpath(root, evals_dir))
    return sorted(out)


def rule_items(audit):
    out = []
    repos = {r["repo"]: r for r in audit["repos"]}
    for h in audit["harnesses"]:
        repo = h["repo"]
        rel = h["path"][len(repo) + 1:] if h["path"].startswith(repo + "/") else h["path"]
        cases = (repos.get(repo) or {}).get("cases") or 0
        out.append(item(project_name(repo), "rules", rel, "ok" if cases else "no_case",
                        path=[_harness_kind(rel)],
                        detail="{} lines, {} promoted rule(s)".format(h["lines"], h["rules"]),
                        weight=max(1, h["lines"] // 100)))
    for r in audit["repos"]:
        if not r.get("evals_dir"):
            continue
        evals_dir = os.path.expanduser(r["evals_dir"])
        for case in _case_dirs(evals_dir):
            out.append(item(project_name(r["repo"]), "rules", case, "ok", path=["cases"],
                            detail="trimwrit case in {}".format(r["evals_dir"])))
    return out


# -- doors: enabled plugins and hooks ---------------------------------------------------------------

def hook_script(command, project_dir=None):
    """The absolute path of the script a hook command runs, or None when it names no script or
    names one that cannot be resolved here (a `$VAR` other than CLAUDE_PROJECT_DIR, or a
    project relative path in the global settings, where no project is known)."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for tok in tokens:
        tok = tok.strip("'\";")
        if not tok.endswith(SCRIPT_EXT):
            continue
        for var in PROJECT_DIR_VARS:
            if var in tok:
                if project_dir is None:
                    return None
                tok = tok.replace(var, project_dir)
        if "$" in tok:
            return None
        tok = os.path.expanduser(tok)
        if not os.path.isabs(tok):
            if project_dir is None:
                return None
            tok = os.path.join(project_dir, tok)
        return os.path.normpath(tok)
    return None


def _settings_items(project, settings, project_dir):
    out = []
    plugins = settings.get("enabledPlugins")
    if isinstance(plugins, dict):
        for plugin, on in sorted(plugins.items()):
            if on:
                out.append(item(project, "doors", plugin, "ok", path=["plugins"],
                                detail="plugin enabled"))
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return out
    for event, groups in hooks.items():
        for g in groups if isinstance(groups, list) else []:
            if not isinstance(g, dict):
                continue
            for h in g.get("hooks") or []:
                cmd = h.get("command") if isinstance(h, dict) else None
                if not isinstance(cmd, str):
                    continue
                script = hook_script(cmd, project_dir)
                if script is None:
                    continue
                found = os.path.exists(script)
                out.append(item(
                    project, "doors", os.path.basename(script),
                    "ok" if found else "door_missing", path=["hooks", event],
                    detail="matcher {}, {}".format(g.get("matcher") or "all",
                                                   audit_mod._display_path(script)),
                    weight=1 if found else DOOR_MISSING_WEIGHT))
    return out


def _read_settings(path, errors):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        errors.append("doors: {}: {}".format(audit_mod._display_path(path), str(exc)[:200]))
        return None
    if not isinstance(data, dict):
        errors.append("doors: {}: not a JSON object".format(audit_mod._display_path(path)))
        return None
    return data


def door_items(audit, errors):
    out = []
    home = audit_mod._home()
    global_settings = _read_settings(os.path.join(home, ".claude", "settings.json"), errors)
    if global_settings is not None:
        out.extend(_settings_items(GLOBAL_PROJECT, global_settings, None))
    for r in audit["repos"]:
        repo_dir = os.path.expanduser(r["repo"])
        settings = _read_settings(os.path.join(repo_dir, ".claude", "settings.json"), errors)
        if settings is not None:
            out.extend(_settings_items(project_name(r["repo"]), settings, repo_dir))
    return out


# -- learning: the tags of each ledger --------------------------------------------------------------

def learning_items(audit):
    """Per repo with a ledger: each pending tag is a gap (`no_door`, or `relapse` when the tag
    already has a live rule and was corrected again), each live rule's tag is covered."""
    out = []
    for r in audit["repos"]:
        repo_dir = os.path.expanduser(r["repo"])
        if not os.path.exists(ledger_mod.path_for(repo_dir)):
            continue
        project = project_name(r["repo"])
        live = ledger_mod.live_rules(root=repo_dir)
        ruled = {}
        for rule_id, entry in live.items():
            ruled.setdefault(entry.get("tag"), []).append(rule_id)
        for tag, n, route in ledger_mod.pending(root=repo_dir):
            if tag in ruled:
                out.append(item(project, "learning", tag, "relapse", path=["tags"],
                                detail="{} correction(s) since rule {}".format(
                                    n, ", ".join(sorted(ruled[tag]))), weight=n))
            else:
                out.append(item(project, "learning", tag, "no_door", path=["tags"],
                                detail="{} correction(s), qualified on the {} route".format(
                                    n, route), weight=n))
        for rule_id, entry in sorted(live.items()):
            out.append(item(project, "learning", entry.get("tag") or rule_id, "ok",
                            path=["tags"], detail="rule {}, {} route".format(
                                rule_id, entry.get("route") or "?")))
    return out


# -- assembly ---------------------------------------------------------------------------------------

def collect(root=".", roots=None, laptop=False, name=DEFAULT_NAME):
    doc = empty_doc(name)
    try:
        audit = audit_mod.compute(root, roots=roots, laptop=laptop)
    except Exception as exc:                                              # noqa: BLE001
        doc["errors"].append("audit: {}: {}".format(type(exc).__name__, str(exc)[:300]))
        return doc
    sources = (("rules", lambda: rule_items(audit)),
               ("doors", lambda: door_items(audit, doc["errors"])),
               ("learning", lambda: learning_items(audit)))
    for label, fn in sources:
        try:
            doc["items"].extend(fn())
        except Exception as exc:                                          # noqa: BLE001
            doc["errors"].append("{}: {}: {}".format(label, type(exc).__name__, str(exc)[:300]))
    return doc


def load(path):
    """A document from a file. A file that is not JSON, or not an object, is refused: there is
    nothing to render."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise MapError("cannot read the document {}: {}".format(path, exc))
    if not isinstance(data, dict):
        raise MapError("the document {} is not a JSON object".format(path))
    return data


def merge(doc, extra, label="extra"):
    """Fold `extra` into `doc` in place: types and states are updated (later wins), items and
    errors appended. A malformed extra is an error on the page, never a crash."""
    if not isinstance(extra, dict):
        doc.setdefault("errors", []).append("{}: not a JSON object, skipped".format(label))
        return doc
    if extra.get("v", DOC_VERSION) != DOC_VERSION:
        doc.setdefault("errors", []).append("{}: document version {!r}, this renderer reads {}"
                                            .format(label, extra.get("v"), DOC_VERSION))
    for key in ("types", "states"):
        part = extra.get(key)
        if part is None:
            continue
        if isinstance(part, dict) and isinstance(doc.get(key), dict):
            doc[key].update(part)
        else:
            doc.setdefault("errors", []).append("{}: {} is not an object, skipped".format(
                label, key))
    for key in ("items", "errors"):
        part = extra.get(key)
        if part is None:
            continue
        if isinstance(part, list):
            doc.setdefault(key, [])
            if not isinstance(doc[key], list):
                doc[key] = []
            doc[key].extend(part)
        else:
            doc.setdefault("errors", []).append("{}: {} is not a list, skipped".format(
                label, key))
    return doc


def _str(value, default=""):
    return value if isinstance(value, str) else (default if value is None else str(value))


def validate(doc):
    """A clean copy of `doc` the page can trust, every problem listed in its `errors`.

    An item with an unknown type or state is kept as it is (the page shows its raw code and
    counts an unknown state as a gap) and named in the errors; a field of the wrong shape is
    coerced; an entry that is not an object is dropped and named."""
    errors = [_str(e) for e in doc.get("errors") or []] if isinstance(
        doc.get("errors"), list) else ["errors is not a list, dropped"]
    if doc.get("v") != DOC_VERSION:
        errors.append("document version {!r}, this renderer reads {}".format(
            doc.get("v"), DOC_VERSION))
    types = doc.get("types")
    if not isinstance(types, dict):
        errors.append("types is not an object, replaced by the default types")
        types = dict(TYPES)
    types = {_str(k): _str(v, _str(k)) for k, v in types.items()}
    states_in = doc.get("states")
    if not isinstance(states_in, dict):
        errors.append("states is not an object, replaced by the default states")
        states_in = STATES
    states = {}
    for code, s in states_in.items():
        if not isinstance(s, dict):
            errors.append("state {!r} is not an object, read as a gap".format(code))
            s = {}
        states[_str(code)] = {"label": _str(s.get("label"), _str(code)),
                              "gap": bool(s.get("gap", False)), "soft": bool(s.get("soft", False)),
                              "reco": _str(s.get("reco"))}
    items = []
    raw_items = doc.get("items")
    if not isinstance(raw_items, list):
        errors.append("items is not a list, no item read")
        raw_items = []
    for n, it in enumerate(raw_items):
        if not isinstance(it, dict):
            errors.append("item {} is not an object, dropped".format(n))
            continue
        name = _str(it.get("name"), "(unnamed)")
        project = _str(it.get("project"))
        if not project:
            errors.append("item {} ({}): no project, filed under (no project)".format(n, name))
            project = "(no project)"
        where = "item {} ({} / {})".format(n, project, name)
        path = it.get("path")
        if path is None:
            path = []
        elif isinstance(path, str):
            path = [path]
        elif not isinstance(path, list):
            errors.append("{}: path is not a list, ignored".format(where))
            path = []
        type_ = _str(it.get("type"))
        if type_ not in types:
            errors.append("{}: unknown type {!r}".format(where, type_))
        state = _str(it.get("state"))
        if state not in states:
            errors.append("{}: unknown state {!r}".format(where, state))
        weight = it.get("weight", 1)
        if not isinstance(weight, (int, float)) or isinstance(weight, bool):
            errors.append("{}: weight {!r} is not a number, read as 1".format(where, weight))
            weight = 1
        items.append({"project": project, "type": type_, "path": [_str(p) for p in path],
                      "name": name, "state": state, "detail": _str(it.get("detail")),
                      "weight": int(weight), "reco": _str(it.get("reco"))})
    return {"v": DOC_VERSION, "name": _str(doc.get("name"), DEFAULT_NAME) or DEFAULT_NAME,
            "generated_at": _str(doc.get("generated_at"), _now_iso()), "types": types,
            "states": states, "items": items, "errors": errors}


def is_gap(doc, it):
    s = doc["states"].get(it["state"])
    return True if s is None else s["gap"]


def counts(doc):
    gaps = [i for i in doc["items"] if is_gap(doc, i)]
    return {"items": len(doc["items"]), "gaps": len(gaps), "errors": len(doc["errors"])}


def template_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), TEMPLATE_NAME)


def embed(doc):
    """The document as a JS literal safe inside a <script>: every `<` becomes `\\u003c`, valid in
    JSON and in JS alike, so no `</script` and no `<!--` can close or bend the element. The
    dashes this package refuses to write (`text.BAD_DASHES`) are escaped the same way: a name in
    the data may carry one, and the page must still be written, showing it as it is."""
    out = json.dumps(doc, ensure_ascii=False).replace("<", "\\u003c")
    for ch in BAD_DASHES:
        out = out.replace(ch, "\\u{:04x}".format(ord(ch)))
    return out


def render(doc):
    with open(template_path(), encoding="utf-8") as fh:
        page = fh.read()
    if PLACEHOLDER not in page:
        raise MapError("the page template {} has no {} placeholder".format(
            template_path(), PLACEHOLDER))
    return page.replace(PLACEHOLDER, embed(doc), 1)
