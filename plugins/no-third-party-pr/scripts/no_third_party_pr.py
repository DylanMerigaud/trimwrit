#!/usr/bin/env python3
"""PreToolUse hook on Bash: no pull request on a GitHub repository you do not own.

THE RULE: never open a pull request on a repository you do not own, for any purpose (a listing,
a fix, a typo). A pull request on someone else's repository is a public trace under your name.

WHAT IT REFUSES, whenever the target repository's owner is not one of `owners`:
  - `gh pr create` (and its alias `gh pr new`, and any gh alias of the config that expands to
    it), `--dry-run` excepted since it creates nothing;
  - `gh api` (and `hub api`) on repos/<owner>/<repo>/pulls with POST, given by -X/--method or
    implied by gh itself the moment a field (-f, -F) or --input is passed;
  - a GraphQL `createPullRequest` mutation (gh api graphql, curl to /graphql): its repository is
    an opaque node id the gate cannot resolve without the network, so it is refused whatever it
    names (fail closed); a PR on your own repo goes through `gh pr create --repo` instead;
  - curl and wget POSTing to api.github.com/repos/<owner>/<repo>/pulls (a data flag implies
    POST), `hub pull-request`, and inline interpreter code (python -c, node -e, ...) that opens a
    pull request (gh pr create, create_pull, pulls.create, createPullRequest, a POST to /pulls):
    its target is read from the code, and refused when the code names none.

THE TARGET, in gh's own order: -R/--repo (OWNER/REPO, HOST/OWNER/REPO or a URL), else GH_REPO when
the command itself sets or exports it, else the git remotes of the directory the command runs in
(`git config --local`, no network). ALL remotes, not only origin: with several remotes gh picks
`upstream` before `origin`, so a fork whose upstream is a third party opens the PR on the third
party. Every remote must be yours, or the target must be named with --repo. The {owner}/{repo}
placeholders of a gh api endpoint resolve the same way.

THE OWNERS. Section `no-third-party-pr`, key `owners` (GitHub logins, compared case-insensitively,
on the configured `hosts` only). When it is empty, the owner is the `user:` of the host blocks of
~/.config/gh/hosts.yml; when that is empty too, every pull request is refused.

COMPOUND COMMANDS. The command is parsed and evaluated by the shell reader of main-checkout-guard
(vendored as lib/git_hub_guard.py): &&, ||, ;, pipes, subshells, groups, if/for/while/case,
$(...), backticks, bash -c, sh -c, zsh -c, eval, text piped into a shell, env/command/nohup
prefixes, xargs and find -exec, every cd followed. This gate subclasses its evaluator and adds
what it does not read: gh, hub, curl, wget, inline interpreter code, and a shell script FILE run
with bash, sh or source (read and evaluated the same way).

FAIL CLOSED. A pull request whose target cannot be resolved is refused: an unexpandable --repo or
endpoint or method, a cd to a variable, a directory that is not a git checkout or has no remote, a
remote that is not on a configured host, a command the parser cannot read, a nesting deeper than
the reader follows, and a crash of the reader itself (recorded as a crash, and still refused). A
broken trimwrit-gates.json also refuses a command that may open a pull request, and says so. A
crash on a command that carries no pull request text lets the call through, recorded.

WHAT STAYS ALLOWED: every read (gh pr list/view/checks/diff on any repo, gh api GET, curl GET),
gh pr merge, comments on issues (an issue comment is not a pull request), and every PR on your
own repositories.

THE ONE EXEMPTION, an optional command (`exemption_command`, an argv list). For a target that is
not yours, the gate allows exactly one shape: ONE simple command `gh pr create -R owner/repo
--title T (--body B | --body-file F)` (no chaining, pipe, redirection, substitution, variable,
glob or comment; no --fill, --editor, --web, --template or --recover, which write a text the
command never saw). The command is then run with one JSON object on stdin
{"repo", "title", "body", "command"} and must print one JSON object {"ok": bool, "reason": str}
within 10 seconds; anything else is a refusal. A refusal burns nothing.

THE REFUSAL: a PreToolUse deny whose reason starts with "refused: third-party-pr" and names the
command, the owner/repo resolved (or why it could not be), why the exemption did not open it, the
rule, and what to do instead. Each refusal is witnessed once. NO BYPASS: no flag, no environment
variable, no allowlist, and an empty `owners` refuses every pull request. The only argument is
`--home DIR`, the test knob no hooks.json passes.

Configuration (section `no-third-party-pr`): owners, hosts, exemption_command, note,
reason_extra. Tests: tests/gates/test_no_third_party_pr.py.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_ROOT)
from gatekit import config, trace  # noqa: E402

HOOK = "third_party_pr_gate.py"
DOOR = "third-party-pr"
TIMEOUT_S = 8
GIT_TIMEOUT_S = 3
EXEMPTION_TIMEOUT_S = 10
PARSER_PATH = os.path.join(PLUGIN_ROOT, "lib", "git_hub_guard.py")
SCRIPT_MAX_BYTES = 256 * 1024

OWNERS = frozenset()
GITHUB_HOSTS = frozenset({"github.com", "www.github.com", "api.github.com", "ssh.github.com"})
EXEMPTION_COMMAND = ()
NOTE = ""
REASON_EXTRA = ""
MISSING = object()
NO_OWNER = "no owner is configured: set owners in trimwrit-gates.json"

# Text that makes a command worth reading at all (the reader is skipped otherwise).
PR_TEXT_RE = re.compile(r"\bpr\b|pulls|createPullRequest|create_pull|pull-request", re.I)
SCRIPT_RUN_RE = re.compile(r"\b(?:bash|sh|zsh|dash|ksh|source)\b|(?:^|[;&|(]\s*)\.\s+\S")
# Text that may OPEN a pull request: what an unreadable command or a crash is refused on.
CREATE_TEXT_RE = re.compile(r"\bpr\b\W{1,6}(?:create|new)\b|createPullRequest|create_pull\b|"
                            r"pulls\.create|pull-request|/pulls\b", re.I)
# Inline interpreter code that opens a pull request.
INLINE_PR_RE = re.compile(r"\bpr\b\W{1,6}(?:create|new)\b|createPullRequest|create_pull\b|"
                          r"pulls\.create|pull-request|\bpost\b.{0,200}?/pulls\b|"
                          r"/pulls\b.{0,200}?\bpost\b", re.I | re.S)
INLINE_REPO_RES = (
    re.compile(r"(?:-R|--repo)\W{1,6}(?:https?://github\.com/)?([\w.-]+)/([\w.-]+)"),
    re.compile(r"repos/([\w.{}:-]+)/([\w.{}:-]+)/pulls"),
    re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+)"),
    re.compile(r"get_repo\(\W*([\w.-]+)/([\w.-]+)"),
)
INTERPRETERS = frozenset({"python", "python2", "python3", "node", "ruby", "perl", "php", "deno",
                          "bun"})
CODE_FLAGS = frozenset({"-c", "-e", "-E", "--eval", "-p", "--print", "-r"})
# Commands that only print or search text: a URL and the word POST in their arguments send
# nothing (the shell reader already follows what they pipe into a shell).
PRINTERS = frozenset({"echo", "printf", "cat", "grep", "egrep", "rg", "ag", "sed", "awk", "jq",
                      "head", "tail", "less", "more", "wc", "sort", "uniq", "tee", "ls"})
PR_CREATE = frozenset({"create", "new"})

# The one command shape an exempted pull request may take.
TICKET_VALUE_FLAGS = {"-t": "title", "--title": "title", "-b": "body", "--body": "body",
                      "-F": "body_file", "--body-file": "body_file", "-R": "repo",
                      "--repo": "repo", "-B": None, "--base": None, "-H": None, "--head": None,
                      "-a": None, "--assignee": None, "-l": None, "--label": None, "-m": None,
                      "--milestone": None, "-p": None, "--project": None, "-r": None,
                      "--reviewer": None}
TICKET_BARE_FLAGS = frozenset({"-d", "--draft", "--no-maintainer-edit"})
# Outside quotes, any of these makes the command more than one simple command.
SHELL_SPECIALS = frozenset("$`;&|<>(){}*?[]#!\n\r")
ONE_SHAPE = ("ONE simple command: gh pr create -R owner/repo --title T --body-file F (or --body "
             "B), nothing chained, piped, redirected, substituted or globbed")

GH_API_FLAGS = {"-X": "method", "--method": "method", "-f": "field", "--raw-field": "field",
                "-F": "field", "--field": "field", "--input": "input", "--hostname": "hostname",
                "-H": None, "--header": None, "-q": None, "--jq": None, "-t": None,
                "--template": None, "-p": None, "--preview": None, "--cache": None}
CURL_FLAGS = {"-X": "method", "--request": "method", "--url": "url",
              "-d": "data", "--data": "data", "--data-raw": "data", "--data-binary": "data",
              "--data-urlencode": "data", "--data-ascii": "data", "--json": "data",
              "-F": "data", "--form": "data", "--form-string": "data", "-T": "upload",
              "--upload-file": "upload",
              "-H": None, "--header": None, "-u": None, "--user": None, "-o": None,
              "--output": None, "-A": None, "--user-agent": None, "-e": None, "--referer": None,
              "-b": None, "--cookie": None, "-c": None, "--cookie-jar": None, "-w": None,
              "--write-out": None, "--connect-timeout": None, "-m": None, "--max-time": None,
              "--retry": None, "-K": None, "--config": None, "-x": None, "--proxy": None,
              "-E": None, "--cert": None, "--cacert": None, "--resolve": None, "-r": None,
              "--range": None, "-D": None, "--dump-header": None, "--oauth2-bearer": None,
              "-Y": None, "-y": None, "--limit-rate": None, "-z": None}
WGET_FLAGS = {"--method": "method", "--post-data": "data", "--body-data": "data",
              "--post-file": "datafile", "--body-file": "datafile", "--header": None,
              "-O": None, "--output-document": None, "-o": None, "--output-file": None,
              "-U": None, "--user-agent": None}


def _load_parser():
    spec = importlib.util.spec_from_file_location("git_hub_guard_reader", PARSER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


try:
    _ghg = _load_parser()
    _LOAD_ERROR = None
except Exception as _exc:  # noqa: BLE001  decide() fails closed on a PR command without it
    _ghg = None
    _LOAD_ERROR = "{}: {}".format(type(_exc).__name__, _exc)


# -- targets ----------------------------------------------------------------------------------

def parse_repo(text):
    """(host, owner, name) from OWNER/REPO, HOST/OWNER/REPO, an https, ssh or scp-like git URL;
    None when the text is none of them."""
    s = (text or "").strip()
    if not s:
        return None
    m = re.match(r"^(?:[a-z][a-z0-9+.-]*://)?(?:[^@/\s]+@)?(?P<host>[^/:\s]+)[:/]"
                 r"(?P<owner>[^/\s]+)/(?P<name>[^/\s]+?)(?:\.git)?/?$", s, re.I)
    if m and "." in m.group("host"):
        return (m.group("host").lower(), m.group("owner"), m.group("name"))
    m = re.match(r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?$", s)
    if m:
        return ("github.com", m.group("owner"), m.group("name"))
    return None


def gh_hosts_users(path=None):
    """The `user:` of each host block of gh's hosts.yml, [] when unreadable."""
    path = path or os.path.expanduser(os.path.join("~", ".config", "gh", "hosts.yml"))
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return []
    return re.findall(r"^\s+user:\s*(\S+)", text, re.M)


def configure_from(cfg):
    """Apply one plugin_settings() dict to the module globals the judges read."""
    global OWNERS, GITHUB_HOSTS, EXEMPTION_COMMAND, NOTE, REASON_EXTRA
    owners = [str(o).strip().lower() for o in cfg.get("owners") or () if str(o).strip()]
    if not owners:
        owners = [u.strip("'\"").lower() for u in gh_hosts_users()]
    OWNERS = frozenset(o for o in owners if o)
    GITHUB_HOSTS = frozenset(str(h).strip().lower() for h in cfg.get("hosts") or ())
    EXEMPTION_COMMAND = tuple(cfg.get("exemption_command") or ())
    NOTE = cfg.get("note") or ""
    REASON_EXTRA = cfg.get("reason_extra") or ""


def is_own(repo):
    host, owner, _name = repo
    return host in GITHUB_HOSTS and owner.lower() in OWNERS


def label(repo):
    host, owner, name = repo
    if host in GITHUB_HOSTS:
        host = "github.com"
    return "{}/{}/{}".format(host, owner, name)


def read_gh_aliases(path=None):
    """{alias: expansion} from gh's config file (the `aliases:` block), {} when unreadable."""
    out = {}
    path = path or os.path.expanduser(os.path.join("~", ".config", "gh", "config.yml"))
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return out
    inside = False
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            inside = line.strip() == "aliases:"
            continue
        if inside:
            key, sep, value = line.strip().partition(":")
            if sep and key.strip():
                out[key.strip().strip("'\"")] = value.strip().strip("'\"")
    return out


def _flag(args, i, table):
    """(kind, value, consumed) when args[i] is a flag of `table` (exact, `--long=v` or `-Xv`),
    else (None, None, 0). A missing value is None (unknown)."""
    a = args[i]
    if a is None:
        return None, None, 0
    if a in table:
        return table[a], (args[i + 1] if i + 1 < len(args) else None), 2
    if a.startswith("--") and "=" in a:
        name, _, value = a.partition("=")
        if name in table:
            return table[name], value, 1
        return None, None, 0
    if len(a) > 2 and a[0] == "-" and a[1] != "-" and a[:2] in table:
        return table[a[:2]], a[2:], 1
    if len(a) > 2 and a[0] == "-" and a[1:].isalpha() and ("-" + a[-1]) in table:
        # combined short flags whose last one takes the value: `curl -sX POST`
        return table["-" + a[-1]], (args[i + 1] if i + 1 < len(args) else None), 2
    return None, None, 0


def _script_arg(args):
    """The script FILE a shell runs (`bash x.sh`), None for -c, -s, stdin or an unknown word."""
    i = 0
    while i < len(args):
        a = args[i]
        if a is None or a == "-":
            return None
        if a == "--":
            return args[i + 1] if i + 1 < len(args) else None
        if a in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            i += 2
            continue
        if a.startswith("--"):
            i += 1
            continue
        if len(a) > 1 and a[0] in "-+":
            if "c" in a[1:] or "s" in a[1:]:
                return None
            i += 1
            continue
        return a
    return None


def _inline_targets(text):
    out = []
    for rx in INLINE_REPO_RES:
        for owner, name in rx.findall(text or ""):
            if "{" in owner or owner.startswith(":") or "{" in name or name.startswith(":"):
                continue
            repo = ("github.com", owner, name.rstrip(".'\""))
            if repo not in out:
                out.append(repo)
    return out


_Base = _ghg.Guard if _ghg is not None else object


class PrGuard(_Base):
    """git-hub-guard's evaluator, reading pull request creation instead of git verbs."""

    def __init__(self, session_cwd):
        super().__init__(session_cwd)
        self.found = []
        self.remote_cache = {}
        self.aliases = None

    # -- entry --------------------------------------------------------------------------------
    def check(self, command):
        self.eval_code(command, frozenset([self.session_cwd]), _ghg.Env(), 0)
        return self.found

    def refuse(self, klass, command, target, why):
        item = {"class": klass, "command": " ".join(str(command or "").split())[:300],
                "target": target, "why": why}
        if item not in self.found:
            self.found.append(item)

    # -- what git-hub-guard reads, adapted --------------------------------------------------
    def git(self, *args, **kwargs):
        """git verbs are git-hub-guard's business; a push opens no pull request."""
        return None

    def eval_code(self, code, S, env, depth):
        if depth > _ghg.MAX_DEPTH:
            if CREATE_TEXT_RE.search(code or ""):
                self.refuse("unresolved_target", code, None,
                            "nested deeper than the door reads")
            return S, S
        return super().eval_code(code, S, env, depth)

    def token_scan(self, code, error):
        if CREATE_TEXT_RE.search(code or ""):
            self.refuse("unparsable_command", code, None,
                        "the door could not parse the command ({}); write it so a shell "
                        "reader can follow it".format(error))

    def shell(self, args, S, env, stdin, depth):
        super().shell(args, S, env, stdin, depth)
        path = _script_arg(args)
        if path:
            self.script_file(path, S, env.copy(), depth)

    def run(self, words, S, env, cmd_env, stdin, depth, raw):
        head = self._head(words, S, env, cmd_env)
        if head is not None:
            name, rest, S2, cenv = head
            if name == "gh":
                self.gh(rest, S2, env, cenv, depth, raw)
                return S, S
            if name == "hub":
                self.hub(rest, S2, env, cenv, depth, raw)
                return S, S
            if name in ("curl", "wget"):
                self.http(name, rest, S2, raw)
                return S, S
            if name in INTERPRETERS:
                self.inline(rest, raw)
                return S, S
            if name in ("source", ".") and rest:
                self.script_file(rest[0][0], S2, env, depth)
                return S, S
            if name is not None and name not in PRINTERS:
                self.generic(rest, raw)
        return super().run(words, S, env, cmd_env, stdin, depth, raw)

    def _head(self, words, S, env, cmd_env):
        words = list(words)
        while words and words[0][0] in _ghg.KEYWORDS:
            dropped = words[0][0]
            words = words[1:]
            if dropped == "time" and words and words[0][0] == "-p":
                words = words[1:]
        if words and words[0][0] == "--":
            words = words[1:]
        if not words:
            return None
        first = words[0][0]
        if first in ("for", "select", "function") or (
                first is not None and (first in env.aliases or first in env.funcs)):
            return None
        cenv = dict(cmd_env)
        words, S2 = self.strip_prefixes(words, S, env, cenv)
        if not words:
            return None
        value, raw0 = words[0]
        name = os.path.basename(value) if value is not None else None
        if name is None:
            m = re.search(r"\b(gh|hub|curl|wget)\b", raw0 or "")
            name = m.group(1) if m else None
        return name, words[1:], S2, cenv

    # -- gh -------------------------------------------------------------------------------------
    def gh_aliases(self):
        if self.aliases is None:
            self.aliases = read_gh_aliases()
        return self.aliases

    def gh(self, rest, S, env, cenv, depth, raw):
        args = [v for v, _ in rest]
        if not args:
            return
        aliases = self.gh_aliases()
        if args[0] is not None and args[0] in aliases:
            expansion = aliases[args[0]]
            if expansion.startswith("!"):
                self.eval_code(expansion[1:], S, env.copy(), depth + 1)
                return
            try:
                args = shlex.split(expansion) + args[1:]
            except ValueError:
                args = [None] + args[1:]
        head = args[0]
        if head is None:
            if re.search(r"\b(?:create|new|api|graphql|pulls)\b", raw or ""):
                self.refuse("unresolved_target", raw, None,
                            "the gh subcommand is not a literal the door can read")
            return
        if head == "pr":
            self.gh_pr(args[1:], S, env, cenv, raw)
        elif head == "api":
            self.api(args[1:], S, env, cenv, raw)

    def gh_pr(self, args, S, env, cenv, raw):
        repo, sub, dry = MISSING, MISSING, False
        table = {"-R": "repo", "--repo": "repo"}
        i = 0
        while i < len(args):
            kind, value, n = _flag(args, i, table)
            if n:
                repo = value
                i += n
                continue
            a = args[i]
            if a is None:
                if sub is MISSING:
                    sub = None
            elif a == "--dry-run":
                dry = True
            elif not a.startswith("-") and sub is MISSING:
                sub = a
            i += 1
        if sub is MISSING or (sub is not None and sub not in PR_CREATE):
            return
        if dry and sub is not None:
            return
        self.judge(self.targets(repo, S, env, cenv), raw)

    def api(self, args, S, env, cenv, raw):
        method, endpoint, hostname = MISSING, MISSING, MISSING
        fields, inputs = [], []
        i = 0
        while i < len(args):
            kind, value, n = _flag(args, i, GH_API_FLAGS)
            if n:
                if kind == "method":
                    method = value
                elif kind == "field":
                    fields.append(value)
                elif kind == "input":
                    inputs.append(value)
                elif kind == "hostname":
                    hostname = value
                i += n
                continue
            a = args[i]
            if a is None:
                if endpoint is MISSING:
                    endpoint = None
            elif not (a.startswith("-") and a != "-") and endpoint is MISSING:
                endpoint = a
            i += 1
        if method is MISSING:
            method = "POST" if (fields or inputs) else "GET"
        elif method is not None:
            method = method.upper()
        if method not in (None, "POST") or endpoint is MISSING:
            return
        if endpoint is None:
            if re.search(r"pulls|graphql", raw or "", re.I):
                self.refuse("unresolved_target", raw, None,
                            "the gh api endpoint is not a literal the door can read")
            return
        path = endpoint.split("?", 1)[0].split("#", 1)[0]
        host = "github.com"
        m = re.match(r"^https?://([^/]+)(/.*)?$", path, re.I)
        if m:
            host = m.group(1).lower()
            path = re.sub(r"^/api/v3", "", m.group(2) or "")
        if hostname is not MISSING:
            host = (hostname or "").lower() or None
        path = path.strip("/")
        if path == "graphql":
            self.graphql(fields + inputs, inputs, S, raw)
            return
        pm = re.fullmatch(r"repos/([^/]+)/([^/]+)/pulls", path)
        if not pm:
            return
        owner, name = pm.groups()
        if owner in ("{owner}", ":owner") or name in ("{repo}", ":repo"):
            targets = self.targets(MISSING, S, env, cenv)
        elif host is None:
            targets = [(None, "the gh api --hostname is not a literal the door can read")]
        else:
            targets = [((host, owner, name), "the gh api endpoint")]
        self.judge(targets, raw)

    def graphql(self, fields, files, S, raw):
        texts, unknown = [], False
        for f in fields:
            if f is None:
                unknown = True
                continue
            _key, _, value = f.partition("=")
            value = value or f
            if value.startswith("@") or f in files:
                text = self.read_file(value[1:] if value.startswith("@") else value, S)
                if text is None:
                    unknown = True
                else:
                    texts.append(text)
            else:
                texts.append(value)
        blob = "\n".join(texts)
        if "createPullRequest" in blob or (unknown and "createPullRequest" in (raw or "")):
            self.refuse("unresolved_target", raw, None,
                        "a GraphQL createPullRequest names its repository by an opaque node id "
                        "the door cannot resolve without the network")

    # -- hub, curl, wget, inline code, other tools ----------------------------------------------
    def hub(self, rest, S, env, cenv, depth, raw):
        args = [v for v, _ in rest]
        if not args:
            return
        if args[0] == "api":
            self.api(args[1:], S, env, cenv, raw)
            return
        if args[0] is None and "pull-request" in (raw or ""):
            self.refuse("unresolved_target", raw, None, "the hub subcommand is not a literal")
            return
        if args[0] != "pull-request":
            return
        base = MISSING
        i = 1
        while i < len(args):
            kind, value, n = _flag(args, i, {"-b": "base", "--base": "base"})
            if n:
                base = value
                i += n
                continue
            i += 1
        if base is None:
            self.judge([(None, "the hub --base value is not a literal the door can read")], raw)
        elif base is not MISSING and ":" in base:
            self.judge([(("github.com", base.split(":", 1)[0], "*"), "hub --base")], raw)
        else:
            self.judge(self.targets(MISSING, S, env, cenv), raw)

    def http(self, name, rest, S, raw):
        args = [v for v, _ in rest]
        table = CURL_FLAGS if name == "curl" else WGET_FLAGS
        method, urls, data, files, unknown = MISSING, [], [], [], False
        get = False
        i = 0
        while i < len(args):
            kind, value, n = _flag(args, i, table)
            if n:
                if kind == "method":
                    method = value
                elif kind == "url":
                    urls.append(value)
                elif kind in ("data", "datafile"):
                    data.append(value)
                    if kind == "datafile":
                        files.append(value)
                elif kind == "upload" and method is MISSING:
                    method = "PUT"
                i += n
                continue
            a = args[i]
            if a in ("-G", "--get"):
                get = True
            elif a is None:
                unknown = True
            elif not a.startswith("-"):
                urls.append(a)
            i += 1
        if method is MISSING:
            method = "GET" if get or not data else "POST"
        elif method is not None:
            method = method.upper()
        if method not in (None, "POST"):
            return
        if unknown and not urls and re.search(r"pulls|graphql", raw or "", re.I):
            self.refuse("unresolved_target", raw, None, "the URL is not a literal the door can read")
            return
        for url in urls:
            if url is None:
                if re.search(r"pulls|graphql", raw or "", re.I):
                    self.refuse("unresolved_target", raw, None,
                                "the URL is not a literal the door can read")
                continue
            u = url if "://" in url else "https://" + url
            m = re.match(r"^https?://([^/?#]+)(/[^?#]*)?", u, re.I)
            if not m:
                continue
            host = m.group(1).lower().split("@")[-1]
            path = re.sub(r"^/api/v3", "", m.group(2) or "").strip("/")
            if host != "api.github.com" and "/api/v3" not in (m.group(2) or ""):
                continue
            if path == "graphql":
                fields = []
                for d in data:
                    if d is not None and d.startswith("@"):
                        fields.append(d)
                    else:
                        fields.append(None if d is None else "data=" + d)
                self.graphql(fields, files, S, raw)
                continue
            pm = re.fullmatch(r"repos/([^/]+)/([^/]+)/pulls", path)
            if pm:
                gh_host = "github.com" if host == "api.github.com" else host
                self.judge([((gh_host, pm.group(1), pm.group(2)), "the URL")], raw)

    def inline(self, rest, raw):
        """`python3 -c CODE`, `node -e CODE`: the code flag counts only before the script or
        module, where the interpreter reads it (after it, a `-c` belongs to the script)."""
        args = [v for v, _ in rest]
        codes = []
        i = 0
        while i < len(args):
            a = args[i]
            if a in CODE_FLAGS:
                codes.append(args[i + 1] if i + 1 < len(args) else None)
                break
            if a is None or a == "-m" or not a.startswith("-"):
                break
            i += 1
        for code in codes:
            text = code if code is not None else raw
            if not INLINE_PR_RE.search(text or ""):
                continue
            repos = _inline_targets(text)
            if not repos:
                self.refuse("unresolved_target", raw, None,
                            "inline code opens a pull request and names no repository the door "
                            "can read")
            for repo in repos:
                if not is_own(repo):
                    self.refuse("third_party_pr", raw, label(repo), "inline code")

    def generic(self, rest, raw):
        """Any other tool (httpie, xh, ...) that names a GitHub pulls endpoint AND says POST."""
        args = [v for v, _ in rest]
        if not any(a is not None and a.upper() in ("POST", "--METHOD=POST") for a in args):
            return
        for a in args:
            m = re.search(r"api\.github\.com/repos/([^/\s]+)/([^/\s?#]+)/pulls/?(?:[?#]|$)",
                          a or "")
            if m:
                self.judge([(("github.com", m.group(1), m.group(2)), "the URL")], raw)

    # -- files and remotes ----------------------------------------------------------------------
    def read_file(self, path, S):
        if not path or path == "-":
            return None
        for cwd in S:
            full = os.path.expanduser(path)
            if not os.path.isabs(full):
                if cwd is None:
                    continue
                full = os.path.join(cwd, full)
            try:
                if os.path.isfile(full) and os.path.getsize(full) <= SCRIPT_MAX_BYTES:
                    with open(full, encoding="utf-8", errors="replace") as fh:
                        return fh.read()
            except OSError:
                continue
        return None

    def script_file(self, path, S, env, depth):
        if not path:
            return
        text = self.read_file(path, S)
        if text is not None:
            self.eval_code(text, S, env, depth + 1)

    def remotes(self, cwd):
        """[(name, url)] of the checkout `cwd` is in, [] when it has none, None when `cwd` is not
        a git checkout git can read. Local config only, no network."""
        if cwd in self.remote_cache:
            return self.remote_cache[cwd]
        out = None
        if os.path.isdir(cwd):
            try:
                p = subprocess.run(_ghg.git_argv_prefix() + [
                    "git", "-C", cwd, "config", "--local", "--get-regexp",
                    r"^remote\..*\.url$"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                   timeout=GIT_TIMEOUT_S)
                if p.returncode == 0:
                    out = []
                    for line in p.stdout.splitlines():
                        key, _, url = line.partition(" ")
                        name = key[len("remote."):-len(".url")]
                        out.append((name, url.strip()))
                elif p.returncode == 1:
                    out = []
            except (OSError, subprocess.SubprocessError):
                out = None
        self.remote_cache[cwd] = out
        return out

    def targets(self, repo, S, env, cenv):
        """[(repo or None, where it came from)] in gh's order: --repo, GH_REPO, the remotes."""
        if repo is not MISSING:
            return [self._parsed(repo, "--repo")]
        if "GH_REPO" in cenv:
            return [self._parsed(cenv["GH_REPO"], "GH_REPO")]
        if "GH_REPO" in env.exported:
            return [self._parsed(env.vars.get("GH_REPO"), "GH_REPO")]
        out = []
        for cwd in S:
            if cwd is None:
                out.append((None, "the directory the command runs in is not a literal the door "
                                  "can read"))
                continue
            remotes = self.remotes(cwd)
            if remotes is None:
                out.append((None, "{} is not a git checkout the door can read".format(cwd)))
            elif not remotes:
                out.append((None, "{} has no git remote".format(cwd)))
            else:
                for name, url in remotes:
                    parsed = parse_repo(url)
                    out.append((parsed, "remote {} of {}".format(name, cwd)) if parsed else
                               (None, "remote {} ({}) is not a repository the door can read"
                                .format(name, url)))
        return out

    @staticmethod
    def _parsed(value, source):
        if value is None:
            return (None, "the {} value is not a literal the door can read".format(source))
        parsed = parse_repo(value)
        if parsed is None:
            return (None, "{} {!r} is not OWNER/REPO".format(source, value))
        return (parsed, source)

    def judge(self, targets, raw):
        for repo, source in targets:
            if repo is None:
                self.refuse("unresolved_target", raw, None, source)
            elif not is_own(repo):
                self.refuse("third_party_pr", raw, label(repo), "target from " + source)


# -- the exemption, the one way through ---------------------------------------------------------

def simple_words(command):
    """The words of a command that is ONE simple command with only literal words, else None."""
    quote, escaped = None, False
    for ch in command or "":
        if escaped:
            escaped = False
            continue
        if quote == "'":
            if ch == "'":
                quote = None
            continue
        if ch == "\\":
            escaped = True
            continue
        if quote == '"':
            if ch == '"':
                quote = None
            elif ch in "$`":
                return None
            continue
        if ch in "'\"":
            quote = ch
        elif ch in SHELL_SPECIALS:
            return None
    if quote or escaped:
        return None
    try:
        return shlex.split(command)
    except ValueError:
        return None


def parse_ticketed_create(words):
    """({"repo", "title", "body" or "body_file"}, None) for a gh pr create a ticket may open, else
    (None, why)."""
    if not words or os.path.basename(words[0]) != "gh" or len(words) < 2 or words[1] != "pr":
        return None, "a ticketed pull request is " + ONE_SHAPE
    spec, sub, i, args = {}, None, 0, words[2:]
    while i < len(args):
        a = args[i]
        if a.startswith("--") and "=" in a:
            name, value, used = a.split("=", 1) + [1]
        elif a in TICKET_VALUE_FLAGS:
            name, value, used = a, (args[i + 1] if i + 1 < len(args) else None), 2
        elif len(a) > 2 and a[0] == "-" and a[1] != "-" and a[:2] in TICKET_VALUE_FLAGS:
            name, value, used = a[:2], a[2:], 1
        elif a in TICKET_BARE_FLAGS:
            i += 1
            continue
        elif not a.startswith("-") and sub is None:
            sub = a
            i += 1
            continue
        else:
            return None, ("{!r} is not a flag a ticketed pull request may carry (--fill, "
                          "--editor, --web, --template and --recover write a text the ticket "
                          "never saw)".format(a))
        if name not in TICKET_VALUE_FLAGS or value is None:
            return None, "{} has no literal value".format(name)
        key = TICKET_VALUE_FLAGS[name]
        if key:
            if key in spec:
                return None, "{} is given twice".format(name)
            spec[key] = value
        i += used
    if sub not in PR_CREATE:
        return None, "not a gh pr create"
    if "repo" not in spec:
        return None, "a ticketed pull request names its repository with -R/--repo"
    if "title" not in spec:
        return None, "a ticketed pull request carries its --title"
    if ("body" in spec) == ("body_file" in spec):
        return None, "a ticketed pull request carries exactly one of --body and --body-file"
    if spec.get("body_file") == "-":
        return None, "a ticketed body is a file or a literal, never stdin"
    return spec, None


def exemption(cmd, repo, title, body, command):
    """(ok, reason) from the configured exemption command; any failure is a refusal."""
    if not cmd:
        return False, "no exemption command is configured"
    try:
        out = subprocess.run([config.expand(w) for w in cmd], input=json.dumps(
            {"repo": repo, "title": title, "body": body, "command": command}),
            capture_output=True, text=True, timeout=EXEMPTION_TIMEOUT_S)
        answer = json.loads(out.stdout or "{}")
        if not isinstance(answer, dict):
            return False, "the exemption command failed: the answer is not a JSON object"
        return bool(answer.get("ok")), str(answer.get("reason") or "no reason given")
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return False, "the exemption command failed: {}".format(e)


def ticket_gate(found, command, cwd):
    """[] when the exemption command opens this third-party pull request, else `found` with why
    it did not."""
    if not EXEMPTION_COMMAND:
        return found
    def note(why):
        return [dict(f, ticket=why) for f in found]
    if any(f["class"] != "third_party_pr" for f in found):
        return found
    targets = {f["target"] for f in found}
    if len(targets) != 1:
        return note("one command opens at most one ticketed pull request")
    words = simple_words(command)
    if words is None:
        return note("a ticketed pull request is " + ONE_SHAPE)
    spec, why = parse_ticketed_create(words)
    if spec is None:
        return note(why)
    repo = parse_repo(spec["repo"])
    if repo is None or label(repo) != next(iter(targets)):
        return note("the --repo of the command is not the target the door resolved")
    body = spec.get("body")
    if body is None:
        path = os.path.expanduser(spec["body_file"])
        path = path if os.path.isabs(path) else os.path.join(cwd or os.getcwd(), path)
        try:
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
        except (OSError, UnicodeDecodeError):
            return note("the --body-file {} cannot be read".format(spec["body_file"]))
    ok, reason = exemption(EXEMPTION_COMMAND, "{}/{}".format(repo[1], repo[2]), spec["title"],
                           body, command)
    if ok:
        return []
    return [dict(f, ticket=reason, **{"class": "ticket_refused"}) for f in found]


# -- the decision -----------------------------------------------------------------------------

def needs_reading(command, aliases=None):
    if PR_TEXT_RE.search(command) or SCRIPT_RUN_RE.search(command):
        return True
    names = [re.escape(a) for a in (aliases or {})]
    return bool(names) and bool(re.search(r"\bgh\s+(?:{})\b".format("|".join(names)), command))


def decide(command, cwd):
    """The refusals for one Bash command run from `cwd`, [] when it may run. Pure but for the
    local `git config` reads and the files the command names."""
    if not isinstance(command, str) or not command.strip():
        return []
    if not needs_reading(command, read_gh_aliases()):
        return []
    try:
        if _ghg is None:
            raise RuntimeError("the shell reader did not load: " + str(_LOAD_ERROR))
        found = PrGuard(os.path.normpath(cwd or os.getcwd())).check(command)
        return ticket_gate(found, command, cwd) if found else found
    except Exception as exc:  # noqa: BLE001  a PR command is refused, anything else re-raised
        if not CREATE_TEXT_RE.search(command):
            raise
        return [{"class": "door_crash", "command": " ".join(command.split())[:300],
                 "target": None,
                 "why": "the door crashed while reading the command ({}: {}); a command that "
                        "may open a pull request is refused until the door is fixed".format(
                            type(exc).__name__, str(exc)[:200])}]


def reason_text(found):
    owners = ", ".join(sorted(OWNERS)) or "nobody ({})".format(NO_OWNER)
    lines = ["refused: third-party-pr. Nothing ran."]
    for f in found[:4]:
        lines.append("")
        lines.append("Command: " + f["command"])
        if f["target"]:
            lines.append("Target: {} ({}), not a repository of {}.".format(
                f["target"], f["why"], owners))
        else:
            lines.append("Target: unresolved ({}). The gate fails closed.".format(f["why"]))
        if f.get("ticket"):
            lines.append("Ticket: {}.".format(f["ticket"].rstrip(".")))
    if not OWNERS:
        lines += ["", NO_OWNER[0].upper() + NO_OWNER[1:] + "; every pull request is refused "
                  "until then."]
    rule = ("Rule: never open a pull request on a repository you do not own. A pull request on "
            "someone else's repository is a public trace under your name.")
    if NOTE:
        rule += " (" + NOTE + ")"
    lines += ["", rule]
    if REASON_EXTRA:
        lines += ["", REASON_EXTRA]
    lines += [
        "",
        "Do instead: keep the change on your side (a local branch, or a repository you own) and "
        "report what it would change; the owner of the account decides. No bypass: no flag, no "
        "environment variable, no allowlist. (no-third-party-pr)",
    ]
    return "\n".join(lines)


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason}}))
    sys.exit(0)


def body():
    payload = trace.read_payload()
    if payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
        return 0
    command = tool_input["command"]
    try:
        configure_from(trace.plugin_settings(payload, root=PLUGIN_ROOT))
    except config.ConfigError as exc:
        # Fail closed: a broken configuration cannot tell whose repository this is.
        if not (PR_TEXT_RE.search(command) or SCRIPT_RUN_RE.search(command)):
            raise
        detail = "config error: {}".format(exc)
        trace.trace(HOOK, "PreToolUse", "crash", payload, detail=detail,
                    autofix=trace.autofix(HOOK, ok=False, detail=detail))
        trace.witness(DOOR, "PreToolUse", "config_error", payload)
        deny("refused: third-party-pr. Nothing ran.\n\nCommand: {}\n\nThe configuration is "
             "broken ({}), so the gate cannot tell whose repository this is and fails closed. "
             "Fix trimwrit-gates.json (user layer ~/.claude/trimwrit-gates.json, project layer "
             "<project>/.claude/trimwrit-gates.json). No bypass. (no-third-party-pr)".format(
                 " ".join(command.split())[:300], str(exc)[:300]))
    found = decide(command, payload.get("cwd") or os.getcwd())
    if not found:
        return 0
    crash = [f for f in found if f["class"] == "door_crash"]
    if crash:
        detail = crash[0]["why"]
        trace.trace(HOOK, "PreToolUse", "crash", payload, detail=detail,
                    autofix=trace.autofix(HOOK, ok=False, detail=detail))
    trace.witness(DOOR, "PreToolUse", found[0]["class"], payload)
    deny(reason_text(found))
    return 0


def main():
    trace.configure(sys.argv)
    return trace.run(HOOK, "PreToolUse", body, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
