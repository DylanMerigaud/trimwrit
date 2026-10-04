#!/usr/bin/env python3
"""git-hub-guard.py: PreToolUse(Bash) door that refuses destructive git on a protected main checkout.

WHY. A session working from the main checkout of a repository opted into worktree isolation
ran `git checkout -- .` or `git reset --hard` there and wiped the uncommitted work other
sessions and crons had in it. An Edit/Write guard cannot see it: git runs through Bash. This
door is the Bash half, and no marker file disarms it.

WHAT IS PROTECTED
  A protected repo is one listed in `protected_roots` or any repo whose toplevel carries
  `.claude/auto-worktree`. Its MAIN
  checkout is the main working tree (git-dir == git-common-dir), never a linked worktree.

WHAT IS REFUSED, on a protected MAIN checkout
  checkout with paths, `checkout .`, `checkout -f`, any branch switch (checkout or switch to anything
  but main); restore unless it is --staged only; reset --hard/--merge/--keep and any reset that
  moves HEAD to another commit; clean unless -n/--dry-run; stash except list and show; commit;
  merge unless --ff-only; pull unless --ff-only (and not --rebase); rebase, cherry-pick, revert,
  am, bisect; the plumbing equivalents (read-tree -u, checkout-index -f, rm -f, symbolic-ref HEAD,
  update-ref HEAD).
WHAT IS REFUSED anywhere inside a protected repo, worktrees included (the ref is shared)
  deleting, renaming or force-moving the main branch (branch -d/-D/-f/-m/-M/-c/-C, checkout -B
  main, switch -C main, worktree add -B main), update-ref on refs/heads/main, a force push whose
  destination is main (-f, --force, --force-with-lease, a `+` refspec, --mirror, --all with force),
  a push that deletes main, filter-branch and filter-repo.
EVERYTHING ELSE PASSES: every read-only git, any `-h`/`--help`, `commit`/`pull --dry-run`,
`git pull --ff-only` and `git merge --ff-only` (without --autostash, which stashes the hub's
edits), and a script that wraps git (not a git command line).

HOW THE TARGET IS FOUND. The command is parsed as shell (&&, ||, ;, |, &, newlines, subshells,
groups, if/while/for/case, (( )), [[ ]], $(...), backticks, heredocs, process substitution,
bash -c / sh -c / zsh -c, eval, text piped into a shell by echo/printf/cat, env/command/exec/
nohup/time prefixes, xargs and find -exec with their placeholders), starting from the payload's
cwd and following every `cd`/`pushd` (absolute, ~, $HOME, relative). A `cd` whose success is
uncertain keeps both directories in play; a directory an earlier `mkdir` or `git worktree add`
of the same command creates is taken as existing, and a worktree it creates is a linked one.
For each git call the door applies `-C`, `--git-dir`, `--work-tree`, GIT_DIR and GIT_WORK_TREE,
then asks git itself with `git rev-parse` (the only git it ever runs, besides
`git config --get alias.<verb>` for a verb that is not a builtin).
FAIL CLOSED: when the session runs inside a protected repo and git would be told where to act by
something the door cannot expand (a `cd` to a variable, `cd -`, popd, a `-C`/`--git-dir`/option
word from a variable, a hidden subcommand), or a force push, a push delete or an update-ref names
a ref it cannot expand, a verb that would be refused is refused. A command the parser cannot read
is scanned token by token and fails closed the same way. A read-only git is never refused.

REFUSAL: exit 2 and the reason on stderr (what Claude Code feeds back for PreToolUse), naming the
command, the checkout, why, and the safe path: a worktree, a PR, then a fast-forward.
Each refusal is witnessed once in the door ledger (trace.witness).

NO BYPASS: no environment variable, no marker file, no flag. A crash is loud through gatekit's
trace (health ledger, crash command, a systemMessage) and lets the call through: a broken hook
must never brick every session. `--home DIR` is the tests' fixture knob, an argument no
hooks.json passes.

Configuration (section `main-checkout-guard`): protected_roots, default_branch, note,
remedy_main_checkout, remedy_default_branch. Tests: tests/gates/test_main_checkout_guard.py.
"""
import os
import re
import shlex
import subprocess
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_ROOT)
from gatekit import trace  # noqa: E402

HOOK = "git-hub-guard.py"
DOOR = "git-hub-guard"
TIMEOUT_S = 8
GIT_TIMEOUT_S = 3
MAX_DEPTH = 8

PROTECTED_ROOTS = ()
MARKER = os.path.join(".claude", "auto-worktree")
HOME = os.path.expanduser("~")

MISSING = object()

NOTE = ""
REMEDY_HUB = ("work in a worktree (EnterWorktree, then the postenter script of the worktree-kit "
              "plugin), land the change on the default branch through a pull request, then "
              "fast-forward this checkout with `git pull --ff-only`. On the main checkout "
              "itself, `git pull --ff-only` and `git merge --ff-only` pass.")
REMEDY_REF = ("move the default branch only through a merged pull request: push YOUR branch "
              "(force is fine there), open the pull request, rebase it, merge it, then "
              "fast-forward the main checkout.")
DEFAULT_BRANCH = "main"
MAIN_REFS = {"main", "refs/heads/main", "heads/main"}


def configure_from(cfg):
    """Apply one plugin_settings() dict to the module globals the judges read."""
    global PROTECTED_ROOTS, DEFAULT_BRANCH, MAIN_REFS, NOTE, REMEDY_HUB, REMEDY_REF
    PROTECTED_ROOTS = tuple(os.path.realpath(os.path.expanduser(p))
                            for p in cfg.get("protected_roots") or ())
    DEFAULT_BRANCH = cfg.get("default_branch") or "main"
    MAIN_REFS = {DEFAULT_BRANCH, "refs/heads/" + DEFAULT_BRANCH, "heads/" + DEFAULT_BRANCH}
    NOTE = cfg.get("note") or ""
    REMEDY_HUB = cfg.get("remedy_main_checkout") or REMEDY_HUB
    REMEDY_REF = cfg.get("remedy_default_branch") or REMEDY_REF


class ParseError(Exception):
    """The command is not shell this parser can read."""


# ---------------------------------------------------------------------------------------------
# The shell parser. Just enough of bash and zsh to find every simple command, where it runs, and
# what nested code (command substitution, bash -c, eval, heredoc fed to a shell) it carries.
# ---------------------------------------------------------------------------------------------

class Word:
    __slots__ = ("parts", "raw", "quoted")

    def __init__(self):
        self.parts = []   # ("lit", text) ("tilde",) ("var", name) ("sub", node) ("dyn", raw)
        self.raw = ""
        self.quoted = False

    def literal(self):
        out = []
        for p in self.parts:
            if p[0] != "lit":
                return None
            out.append(p[1])
        return "".join(out)


class Simple:
    __slots__ = ("words", "assigns", "redir_words", "heredocs", "herestrings", "procsubs", "raw")

    def __init__(self):
        self.words = []
        self.assigns = []        # (name, Word)
        self.redir_words = []
        self.heredocs = []       # (body, delimiter_quoted)
        self.herestrings = []
        self.procsubs = []
        self.raw = ""


ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\+?)=")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PEEK_WORD = re.compile(r"[ \t]*([A-Za-z_{}!\[\]]+)(?=[ \t\n;&|()<>]|$)")
OPS = ("&&", "||", ";;&", ";;", ";&", "|&", ";", "|", "&", "(", ")", "\n")
META = " \t\n;&|()<>"
LEAD_KEYWORDS = {"if", "then", "elif", "else", "do", "while", "until", "!", "time"}
TIME_P = re.compile(r"[ \t]*-p(?=[ \t\n;&|()<>]|$)")
FOR_ARITH = re.compile(r"[ \t]*for[ \t]*\(\(")


class Parser:
    def __init__(self, text):
        self.s = text
        self.n = len(text)
        self.i = 0
        self.pending = []

    # -- low level ----------------------------------------------------------------------------
    def peek(self, k=0):
        j = self.i + k
        return self.s[j] if j < self.n else ""

    def skip_blanks(self):
        while self.i < self.n:
            c = self.s[self.i]
            if c in " \t":
                self.i += 1
            elif c == "\\" and self.peek(1) == "\n":
                self.i += 2
            elif c == "#":
                while self.i < self.n and self.s[self.i] != "\n":
                    self.i += 1
            else:
                break

    def skip_ws_nl(self):
        while True:
            self.skip_blanks()
            if self.peek() == "\n":
                self.newline()
            else:
                return

    def newline(self):
        self.i += 1
        pending, self.pending = self.pending, []
        for doc in pending:
            lines = []
            while self.i < self.n:
                j = self.s.find("\n", self.i)
                if j < 0:
                    j = self.n
                line = self.s[self.i:j]
                self.i = min(j + 1, self.n)
                if (line.lstrip("\t") if doc["strip"] else line) == doc["delim"]:
                    break
                lines.append(line)
            doc["node"].heredocs.append(("\n".join(lines), doc["quoted"]))

    def peek_op(self):
        for op in OPS:
            if self.s.startswith(op, self.i):
                return op
        return None

    def peek_word(self):
        m = PEEK_WORD.match(self.s, self.i)
        return m.group(1) if m else None

    # -- grammar --------------------------------------------------------------------------------
    def parse_program(self):
        node = self.parse_list()
        self.skip_blanks()
        if self.i < self.n:
            raise ParseError("unexpected {!r} at {}".format(self.s[self.i], self.i))
        return node

    def parse_list(self, close_ops=(), close_words=()):
        items = []
        while True:
            self.skip_blanks()
            if self.i >= self.n:
                break
            if self.peek() == "\n":
                self.newline()
                continue
            op = self.peek_op()
            if op in close_ops:
                break
            if op == ";":
                self.i += 1
                continue
            if close_words and self.peek_word() in close_words:
                break
            andor = self.parse_andor()
            self.skip_blanks()
            op = self.peek_op()
            if op in (";", "&"):
                self.i += 1
                items.append((andor, op))
            elif op == "\n":
                self.newline()
                items.append((andor, ";"))
            else:
                items.append((andor, ";"))
                if op is None and self.i < self.n and not (
                        close_words and self.peek_word() in close_words):
                    raise ParseError("unexpected text at {}".format(self.i))
                if op is not None and op not in close_ops:
                    raise ParseError("unexpected {!r} at {}".format(op, self.i))
                break
        return ("list", items)

    def parse_andor(self):
        pipes = [self.parse_pipeline()]
        ops = []
        while True:
            self.skip_blanks()
            op = self.peek_op()
            if op not in ("&&", "||"):
                break
            self.i += 2
            self.skip_ws_nl()
            ops.append(op)
            pipes.append(self.parse_pipeline())
        return ("andor", pipes, ops)

    def parse_pipeline(self):
        cmds = [self.parse_command()]
        while True:
            self.skip_blanks()
            op = self.peek_op()
            if op not in ("|", "|&"):
                break
            self.i += len(op)
            self.skip_ws_nl()
            cmds.append(self.parse_command())
        return ("pipe", cmds)

    def parse_command(self):
        self.skip_blanks()
        # Reserved words that only prefix the next command: dropped here so that `then (`,
        # `do (`, `time (` and `! (` reach the subshell branch below.
        while self.peek_word() in LEAD_KEYWORDS:
            word = self.peek_word()
            self.skip_blanks()
            self.i += len(word)
            if word == "time":
                m = TIME_P.match(self.s, self.i)
                if m:
                    self.i = m.end()
            self.skip_ws_nl()
        if self.peek_word() == "for" and FOR_ARITH.match(self.s, self.i):
            self.skip_blanks()
            self.i += len("for")
            self.skip_blanks()
        if self.s.startswith("((", self.i):
            self.i = self.balanced(self.i, "(", ")")
            return self.parse_simple(redirs_only=True)
        if self.peek_word() == "[[":
            self.skip_test()
            return self.parse_simple(redirs_only=True)
        if self.peek() == "(":
            self.i += 1
            body = self.parse_list(close_ops=(")",))
            self.expect(")")
            return ("sub", body, self.parse_simple(redirs_only=True)[1])
        word = self.peek_word()
        if word == "{":
            self.skip_blanks()
            self.i += 1
            body = self.parse_list(close_words=("}",))
            self.skip_ws_nl()
            if self.peek_word() != "}":
                raise ParseError("unterminated {")
            self.skip_blanks()
            self.i += 1
            return ("group", body, self.parse_simple(redirs_only=True)[1])
        if word == "case":
            return self.parse_case()
        if word == "function":
            self.skip_blanks()
            self.read_word()
            self.skip_blanks()
            name = self.read_word()
            self.skip_blanks()
            if self.s.startswith("()", self.i):
                self.i += 2
            self.skip_ws_nl()
            return ("func", name.literal() if name else None, self.parse_command())
        return self.parse_simple()

    def skip_test(self):
        """`[[ ... ]]` is a test, not a command: skip to its closing `]]` (it may hold `(`, `<`,
        `&&`, a regex)."""
        self.skip_blanks()
        self.i += 2
        while self.i < self.n:
            c = self.s[self.i]
            if c in "'\"":
                end = self.s.find(c, self.i + 1)
                if end < 0:
                    raise ParseError("unterminated quote in [[")
                self.i = end + 1
                continue
            if c == "\\":
                self.i += 2
                continue
            if self.s.startswith("]]", self.i) and (self.i + 2 >= self.n
                                                   or self.s[self.i + 2] in META):
                self.i += 2
                return
            if c == "\n":
                break
            self.i += 1
        raise ParseError("unterminated [[")

    def procsub(self, node):
        """`<(...)` or `>(...)` at the cursor: a command that runs, recorded on `node`."""
        self.i += 2
        node.procsubs.append(self.parse_list(close_ops=(")",)))
        self.expect(")")

    def expect(self, ch):
        self.skip_blanks()
        if self.peek() != ch:
            raise ParseError("expected {!r} at {}".format(ch, self.i))
        self.i += 1

    def parse_case(self):
        self.skip_blanks()
        self.read_word()
        self.skip_blanks()
        subject = self.read_word()
        self.skip_ws_nl()
        word = self.read_word()
        if word is None or word.literal() != "in":
            raise ParseError("case without in")
        arms = []
        while True:
            self.skip_ws_nl()
            if self.i >= self.n:
                raise ParseError("unterminated case")
            if self.peek_word() == "esac":
                self.skip_blanks()
                self.read_word()
                break
            if self.peek() == "(":
                self.i += 1
            while True:
                if self.i >= self.n:
                    raise ParseError("unterminated case pattern")
                c = self.peek()
                if c == ")":
                    self.i += 1
                    break
                if c in "'\"":
                    self.read_word()
                    continue
                self.i += 1
            arms.append(self.parse_list(close_ops=(";;&", ";;", ";&"), close_words=("esac",)))
            self.skip_blanks()
            op = self.peek_op()
            if op in (";;&", ";;", ";&"):
                self.i += len(op)
        return ("case", subject, arms)

    def parse_simple(self, redirs_only=False):
        node = Simple()
        start = self.i
        while True:
            self.skip_blanks()
            if self.i >= self.n or self.peek() == "\n":
                break
            if self.try_redirect(node):
                continue
            op = self.peek_op()
            if op is not None:
                if op == "(" and not redirs_only and len(node.words) == 1 and not node.assigns:
                    m = re.compile(r"\([ \t]*\)").match(self.s, self.i)
                    if m:
                        self.i = m.end()
                        self.skip_ws_nl()
                        return ("func", node.words[0].literal(), self.parse_command())
                break
            if redirs_only:
                raise ParseError("word after a compound command at {}".format(self.i))
            word = self.read_word()
            if word is None:
                continue
            if not node.words and word.parts and word.parts[0][0] == "lit":
                m = ASSIGN.match(word.parts[0][1])
                if m and not word.raw.startswith(("'", '"')):
                    value = Word()
                    value.raw = word.raw[m.end():]
                    rest = word.parts[0][1][m.end():]
                    value.parts = ([("lit", rest)] if rest else []) + word.parts[1:]
                    if m.group(2):
                        value.parts = [("dyn", value.raw)]
                    node.assigns.append((m.group(1), value))
                    continue
            node.words.append(word)
        node.raw = self.s[start:self.i].strip()
        return ("simple", node)

    def try_redirect(self, node):
        j = self.i
        k = j
        while k < self.n and self.s[k].isdigit():
            k += 1
        nxt = self.s[k:k + 1]
        if k > j and nxt not in ("<", ">"):
            return False
        if self.s.startswith("<<<", k):
            self.i = k + 3
            node.herestrings.append(self.target_word())
            return True
        if self.s.startswith("<<", k):
            strip = self.s.startswith("<<-", k)
            self.i = k + (3 if strip else 2)
            word = self.target_word()
            delim = "".join(p[1] if p[0] == "lit" else "" for p in word.parts)
            if not delim:
                delim = word.raw.strip("'\"")
            self.pending.append({"delim": delim, "strip": strip, "quoted": word.quoted,
                                 "node": node})
            return True
        if k == j and (self.s.startswith("<(", j) or self.s.startswith(">(", j)):
            self.i = j
            self.procsub(node)
            return True
        if nxt in ("<", ">"):
            for op in (">>", ">|", ">&", "<&", "<>", ">", "<"):
                if self.s.startswith(op, k):
                    self.i = k + len(op)
                    break
            self.skip_blanks()
            if self.s.startswith("<(", self.i) or self.s.startswith(">(", self.i):
                self.procsub(node)          # `done < <(git diff ...)`, `exec > >(tee log)`
            else:
                node.redir_words.append(self.target_word())
            return True
        if k == j and self.s.startswith("&>", j):
            self.i = j + (3 if self.s.startswith("&>>", j) else 2)
            node.redir_words.append(self.target_word())
            return True
        return False

    def target_word(self):
        self.skip_blanks()
        if self.i >= self.n or self.peek() in META:
            raise ParseError("redirection without a target at {}".format(self.i))
        word = self.read_word()
        if word is None:
            raise ParseError("redirection without a target at {}".format(self.i))
        return word

    def read_word(self):
        word = Word()
        start = self.i
        lit = []

        def flush():
            if lit:
                word.parts.append(("lit", "".join(lit)))
                del lit[:]

        while self.i < self.n:
            c = self.s[self.i]
            if c in META:
                if c == "(" and lit and lit[-1] == "=":
                    flush()
                    end = self.balanced(self.i, "(", ")")
                    word.parts.append(("dyn", self.s[self.i:end]))
                    self.i = end
                    continue
                break
            if c == "#" and self.i == start:
                while self.i < self.n and self.s[self.i] != "\n":
                    self.i += 1
                return None
            if c == "\\":
                nxt = self.peek(1)
                self.i += 2 if nxt else 1
                if nxt and nxt != "\n":
                    lit.append(nxt)
                    word.quoted = True
                continue
            if c == "'":
                end = self.s.find("'", self.i + 1)
                if end < 0:
                    raise ParseError("unterminated single quote")
                lit.append(self.s[self.i + 1:end])
                word.quoted = True
                self.i = end + 1
                continue
            if c == "$" and self.peek(1) == "'":
                self.i += 2
                buf = []
                while True:
                    if self.i >= self.n:
                        raise ParseError("unterminated $'")
                    ch = self.s[self.i]
                    if ch == "\\" and self.i + 1 < self.n:
                        buf.append(self.s[self.i:self.i + 2])
                        self.i += 2
                        continue
                    if ch == "'":
                        self.i += 1
                        break
                    buf.append(ch)
                    self.i += 1
                try:
                    lit.append("".join(buf).encode("latin-1", "backslashreplace")
                               .decode("unicode_escape"))
                except (UnicodeDecodeError, UnicodeEncodeError):
                    lit.append("".join(buf))
                word.quoted = True
                continue
            if c == "$" and self.peek(1) == '"':
                self.i += 1
                continue
            if c == '"':
                self.i += 1
                flush()
                word.parts.extend(self.read_dq())
                word.quoted = True
                continue
            if c == "$":
                flush()
                word.parts.append(self.read_dollar())
                continue
            if c == "`":
                flush()
                word.parts.append(self.read_backtick())
                continue
            if c == "~" and self.i == start and self.peek(1) in ("", "/") + tuple(META):
                word.parts.append(("tilde",))
                self.i += 1
                continue
            lit.append(c)
            self.i += 1
        flush()
        word.raw = self.s[start:self.i]
        return word

    def read_dq(self):
        parts = []
        lit = []

        def flush():
            if lit:
                parts.append(("lit", "".join(lit)))
                del lit[:]

        while True:
            if self.i >= self.n:
                raise ParseError("unterminated double quote")
            c = self.s[self.i]
            if c == '"':
                self.i += 1
                break
            if c == "\\":
                nxt = self.peek(1)
                if nxt in ("$", "`", '"', "\\"):
                    lit.append(nxt)
                    self.i += 2
                    continue
                if nxt == "\n":
                    self.i += 2
                    continue
                lit.append(c)
                self.i += 1
                continue
            if c == "$":
                flush()
                parts.append(self.read_dollar())
                continue
            if c == "`":
                flush()
                parts.append(self.read_backtick())
                continue
            lit.append(c)
            self.i += 1
        flush()
        return parts

    def balanced(self, pos, open_ch, close_ch):
        """Index just past the bracket matching the one at `pos`, quotes skipped."""
        depth = 0
        j = pos
        while j < self.n:
            c = self.s[j]
            if c == "\\":
                j += 2
                continue
            if c in "'\"":
                end = self.s.find(c, j + 1)
                if end < 0:
                    raise ParseError("unterminated quote")
                j = end + 1
                continue
            if c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        raise ParseError("unbalanced {}".format(open_ch))

    def read_dollar(self):
        nxt = self.peek(1)
        if nxt == "(":
            if self.peek(2) == "(":
                end = self.balanced(self.i + 1, "(", ")")
                raw = self.s[self.i:end]
                self.i = end
                return ("dyn", raw)
            self.i += 2
            body = self.parse_list(close_ops=(")",))
            self.expect(")")
            return ("sub", body)
        if nxt == "{":
            end = self.balanced(self.i + 1, "{", "}")
            inner = self.s[self.i + 2:end - 1]
            self.i = end
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-=])(.*))?$", inner, re.S)
            if m:
                return ("var", m.group(1))
            return ("dyn", "${" + inner + "}")
        m = NAME.match(self.s, self.i + 1)
        if m:
            self.i = m.end()
            return ("var", m.group(0))
        if nxt and nxt in "0123456789@*#?$!-":
            self.i += 2
            return ("dyn", "$" + nxt)
        self.i += 1
        return ("lit", "$")

    def read_backtick(self):
        self.i += 1
        buf = []
        while True:
            if self.i >= self.n:
                raise ParseError("unterminated backtick")
            c = self.s[self.i]
            if c == "\\" and self.peek(1) in ("`", "\\", "$"):
                buf.append(self.peek(1))
                self.i += 2
                continue
            if c == "`":
                self.i += 1
                break
            buf.append(c)
            self.i += 1
        return ("sub", Parser("".join(buf)).parse_program())


def heredoc_subs(text):
    """The command substitutions of an unquoted-delimiter heredoc body: the shell runs them."""
    p = Parser(text)
    subs = []
    try:
        while p.i < p.n:
            c = p.s[p.i]
            if c == "\\":
                p.i += 2
            elif c == "$" and p.peek(1) == "(" and p.peek(2) != "(":
                subs.append(p.read_dollar()[1])
            elif c == "`":
                subs.append(p.read_backtick()[1])
            else:
                p.i += 1
    except ParseError:
        pass
    return subs


# ---------------------------------------------------------------------------------------------
# What git sees: one probe per (directory, git-dir, work-tree), answered by `git rev-parse`.
# ---------------------------------------------------------------------------------------------

def same(a, b):
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except (OSError, ValueError):
        return False


# Every variable `git rev-parse --local-env-vars` prints (git 2.x), plus the discovery knobs
# that also pick the repository. Hardcoded: no extra process, and no read of the environment.
_LOCAL_GIT_VARS = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CEILING_DIRECTORIES", "GIT_COMMON_DIR",
    "GIT_CONFIG", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
    "GIT_DIR", "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_GRAFT_FILE", "GIT_IMPLICIT_WORK_TREE",
    "GIT_INDEX_FILE", "GIT_INTERNAL_SUPER_PREFIX", "GIT_NAMESPACE", "GIT_NO_REPLACE_OBJECTS",
    "GIT_OBJECT_DIRECTORY", "GIT_PREFIX", "GIT_REPLACE_REF_BASE", "GIT_SHALLOW_FILE",
    "GIT_WORK_TREE")


def git_argv_prefix():
    """The argv prefix (`env -u VAR ...`) that runs git without the variables that select a
    repository, so the command's own words, not what the hook inherited, decide it. It returns
    an argv prefix, NOT an environment mapping: do not pass it as `env=`. It unsets git's
    repository-local variables only (not every GIT_*), so the probe sees the same config
    environment (GIT_CONFIG_GLOBAL, GIT_CONFIG_NOSYSTEM, GIT_SSH...) as the user's command. A
    gate source reads no environment variable beyond three, hence `env -u` by name."""
    out = ["env"]
    for name in _LOCAL_GIT_VARS:
        out += ["-u", name]
    return out


def run_git(argv, cwd):
    try:
        r = subprocess.run(git_argv_prefix() + list(argv), cwd=cwd, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


class Target:
    """A resolved git target. Worst-case stand-in: see WORST below."""

    def __init__(self, base, run_dir, git_dir, common, top):
        self.base = base
        self.run_dir = run_dir
        self.git_dir = git_dir
        self.common = common
        self.top = top
        self.main_top = os.path.dirname(common) if os.path.basename(common) == ".git" else None
        cands = [c for c in (self.main_top, top) if c]
        self.is_hub = any(same(c, r) for c in cands for r in PROTECTED_ROOTS)
        self.has_marker = any(os.path.isfile(os.path.join(c, MARKER)) for c in cands)
        self.protected_repo = self.is_hub or self.has_marker
        self.is_main = same(git_dir, common) or bool(
            top and self.main_top and same(top, self.main_top))
        self.protected_main = self.protected_repo and self.is_main
        self._branch = MISSING
        self._push = MISSING

    def label(self):
        where = self.top or self.main_top or self.common
        why = "a configured protected root" if self.is_hub else "its toplevel carries .claude/auto-worktree"
        if self.protected_main:
            return "{}, the MAIN checkout of a protected repo ({})".format(where, why)
        return "{}, inside a protected repo ({})".format(where, why)

    def rev_parse(self, *args):
        return run_git(self.base + ["rev-parse"] + list(args), self.run_dir)

    def branch(self):
        if self._branch is MISSING:
            out = self.rev_parse("--abbrev-ref", "HEAD")
            self._branch = out.strip() if out else None
        return self._branch

    def is_commit(self, arg):
        if arg is None:
            return True
        return self.rev_parse("--verify", "--quiet", "--end-of-options", arg + "^{commit}") \
            is not None

    def push_dest(self):
        if self._push is MISSING:
            out = self.rev_parse("--symbolic-full-name", "@{push}")
            self._push = out.strip() if out else None
        return self._push


class Worst:
    """What an unresolved target could be: a protected main checkout on main."""
    protected_repo = True
    protected_main = True
    is_hub = False

    def label(self):
        return "a directory the door could not resolve"

    def branch(self):
        return DEFAULT_BRANCH

    def is_commit(self, arg):
        return True

    def push_dest(self):
        return None


WORST = Worst()


# ---------------------------------------------------------------------------------------------
# The rules. A judge takes the git arguments after the verb and a target, and returns
# (reason_class, why, remedy) or None. Arguments the shell could not expand are None.
# ---------------------------------------------------------------------------------------------

def opt_is(name, full):
    """Exact, or a long-option abbreviation git would accept (parse-options takes unique
    prefixes). Used for the DANGEROUS options only: a wrong match over-refuses, never allows."""
    if name == full:
        return True
    return name.startswith("--") and full.startswith("--") and len(name) >= 4 \
        and full.startswith(name)


def has_prefix(opts, *fulls):
    return any(opt_is(n, f) for n, _ in opts for f in fulls)


def has_exact(opts, *fulls):
    """For the options that make a command SAFE: exact spelling only, so `--ff` is never read
    as `--ff-only`."""
    return any(n == f or n.startswith(f + "=") for n, _ in opts for f in fulls)


def parse_opts(args, short_val="", long_val=()):
    opts, pos, dd = [], [], None
    i = 0
    while i < len(args):
        a = args[i]
        if a is None:
            pos.append(None)
            i += 1
            continue
        if a == "--":
            dd = args[i + 1:]
            break
        if a.startswith("--"):
            name, eq, val = a.partition("=")
            if not eq and any(opt_is(name, lv) for lv in long_val):
                val = args[i + 1] if i + 1 < len(args) else None
                i += 1
            opts.append((name, val if (eq or val) else None))
            i += 1
            continue
        if a.startswith("-") and len(a) > 1:
            j = 1
            while j < len(a):
                ch = a[j]
                if ch in short_val:
                    val = a[j + 1:]
                    if not val:
                        val = args[i + 1] if i + 1 < len(args) else None
                        i += 1
                    opts.append(("-" + ch, val))
                    break
                opts.append(("-" + ch, None))
                j += 1
            i += 1
            continue
        pos.append(a)
        i += 1
    return opts, pos, dd


QUIET = ("-q", "--quiet", "--progress", "--no-progress")


def hub_wt(why):
    return ("hub_worktree_destroy", why, REMEDY_HUB)


def hub_hist(why):
    return ("hub_history_write", why, REMEDY_HUB)


def hub_switch(why):
    return ("hub_branch_switch", why, REMEDY_HUB)


def ref(why):
    return ("main_ref_rewrite", why, REMEDY_REF)


def j_checkout(args, t):
    opts, pos, dd = parse_opts(args, short_val="bB",
                               long_val=("--conflict", "--pathspec-from-file", "--orphan"))
    # An unexpandable -B name passes: git itself refuses to force-reset a branch checked out in
    # another worktree, and the hub, on main, is one.
    if t.protected_repo and any(n == "-B" and v in MAIN_REFS for n, v in opts):
        return ref("`git checkout -B main` force-resets the main branch")
    if not t.protected_main:
        return None
    quiet = all(n in QUIET for n, _ in opts)
    if dd is None and quiet and (not pos or pos == [DEFAULT_BRANCH]):
        return None
    if dd is not None or len(pos) > 1 or has_prefix(opts, "--pathspec-from-file"):
        return hub_wt("`git checkout` with paths overwrites the uncommitted edits in those paths")
    if has_prefix(opts, "-f", "--force", "-m", "--merge"):
        return hub_wt("`git checkout -f` throws away every uncommitted edit")
    return hub_switch("`git checkout <branch>` takes the hub off main, and `git checkout <path>` "
                      "overwrites that path's uncommitted edits")


def j_switch(args, t):
    opts, pos, dd = parse_opts(args, short_val="cC",
                               long_val=("--create", "--force-create", "--orphan", "--conflict"))
    if t.protected_repo and any((n == "-C" or opt_is(n, "--force-create"))
                                and v in MAIN_REFS for n, v in opts):
        return ref("`git switch -C main` force-resets the main branch")
    if not t.protected_main:
        return None
    if dd is None and pos == [DEFAULT_BRANCH] and all(n in QUIET for n, _ in opts):
        return None
    if has_prefix(opts, "-f", "--force", "--discard-changes", "-m", "--merge"):
        return hub_wt("`git switch --discard-changes` throws away every uncommitted edit")
    return hub_switch("`git switch` takes the hub off main, which must stay on main")


def j_restore(args, t):
    if not t.protected_main:
        return None
    opts, _pos, _dd = parse_opts(args, short_val="s",
                                 long_val=("--source", "--pathspec-from-file", "--conflict"))
    if has_exact(opts, "-S", "--staged") and not has_prefix(opts, "-W", "--worktree"):
        return None
    return hub_wt("`git restore` without --staged overwrites the working copy of those paths")


def j_reset(args, t):
    if not t.protected_main:
        return None
    opts, pos, dd = parse_opts(args, long_val=("--pathspec-from-file",))
    if has_prefix(opts, "--hard", "--merge", "--keep"):
        return hub_wt("`git reset --hard|--merge|--keep` throws away uncommitted edits")
    if dd is not None or has_prefix(opts, "--pathspec-from-file"):
        return None
    if not pos or pos[0] in ("HEAD", "@"):
        return None
    if len(pos) == 1 and t.is_commit(pos[0]):
        return hub_hist("`git reset <commit>` moves the hub's main to another commit")
    return None


def j_clean(args, t):
    if not t.protected_main:
        return None
    opts, _pos, _dd = parse_opts(args, short_val="e", long_val=("--exclude",))
    if has_exact(opts, "-n", "--dry-run"):
        return None
    return hub_wt("`git clean` deletes untracked files, and on the hub those hold work nobody "
                  "has committed yet")


def j_stash(args, t):
    if not t.protected_main:
        return None
    first = args[0] if args else "push"
    if first in ("list", "show"):
        return None
    return hub_wt("`git stash` lifts uncommitted work off the hub's tree, and the stash stack is "
                  "shared with every worktree")


def j_history(why, readonly=(), readonly_flags=()):
    def judge(args, t):
        if not t.protected_main:
            return None
        if args and args[0] in readonly:
            return None
        if any(a in readonly_flags for a in args):
            return None
        return hub_hist(why)
    return judge


def j_merge(args, t):
    if not t.protected_main:
        return None
    opts, _pos, _dd = parse_opts(args, short_val="smXF",
                                 long_val=("--strategy", "--strategy-option", "--message",
                                           "--file", "--into-name", "--cleanup"))
    if has_exact(opts, "--ff-only") and not has_prefix(
            opts, "--no-ff", "--squash", "--abort", "--quit", "--continue", "--autostash"):
        return None
    return hub_hist("`git merge` without --ff-only writes a merge commit on the hub's main")


def j_pull(args, t):
    if not t.protected_main:
        return None
    opts, _pos, _dd = parse_opts(args, short_val="sX",
                                 long_val=("--strategy", "--strategy-option"))
    if has_exact(opts, "--dry-run"):
        return None                     # pull returns right after its dry-run fetch
    rebase = any(n == "-r" or (opt_is(n, "--rebase")
                               and (v or "true").lower() not in ("false", "no", "off", "0"))
                 for n, v in opts)
    if has_exact(opts, "--ff-only") and not rebase and not has_prefix(
            opts, "--no-ff", "--squash", "--autostash"):
        return None
    return hub_hist("`git pull` without --ff-only can merge into or rebase the hub's main")


def j_read_tree(args, t):
    if not t.protected_main:
        return None
    opts, _pos, _dd = parse_opts(args, long_val=("--prefix", "--index-output",
                                                 "--exclude-per-directory"))
    if has_exact(opts, "-u"):
        return hub_wt("`git read-tree -u` rewrites the working tree")
    return None


def j_checkout_index(args, t):
    if not t.protected_main:
        return None
    opts, _pos, _dd = parse_opts(args, long_val=("--prefix", "--stage"))
    if has_prefix(opts, "-f", "--force"):
        return hub_wt("`git checkout-index -f` overwrites uncommitted edits")
    return None


def j_rm(args, t):
    if not t.protected_main:
        return None
    opts, _pos, _dd = parse_opts(args, long_val=("--pathspec-from-file",))
    if has_prefix(opts, "-f", "--force") and not has_exact(opts, "--cached"):
        return hub_wt("`git rm -f` deletes files together with their uncommitted edits")
    return None


def j_symbolic_ref(args, t):
    if not t.protected_main:
        return None
    opts, pos, _dd = parse_opts(args, short_val="m")
    if has_prefix(opts, "-d", "--delete") or len(pos) >= 2:
        return hub_switch("`git symbolic-ref HEAD <ref>` takes the hub off main")
    return None


def j_update_ref(args, t):
    if not t.protected_repo:
        return None
    opts, pos, dd = parse_opts(args, short_val="m")
    if has_exact(opts, "--stdin"):
        return ref("`git update-ref --stdin` can move main and the door cannot read its input")
    target = pos[0] if pos else (dd[0] if dd else "")
    if target is None or target in MAIN_REFS:
        return ref("`git update-ref` on main moves main outside a PR")
    if t.protected_main and target == "HEAD":
        return ref("`git update-ref HEAD` on the hub moves main outside a PR")
    return None


def j_branch(args, t):
    if not t.protected_repo:
        return None
    opts, pos, dd = parse_opts(args, short_val="u",
                               long_val=("--set-upstream-to", "--contains", "--no-contains",
                                         "--merged", "--no-merged", "--points-at", "--sort",
                                         "--format"))
    pos = pos + list(dd or [])        # `git branch -D -- main`
    delete = has_prefix(opts, "-d", "-D", "--delete")
    force = has_prefix(opts, "-f", "-D", "-M", "-C", "--force")
    move = has_prefix(opts, "-m", "-M", "--move")
    copy = has_prefix(opts, "-c", "-C", "--copy")
    if delete and any(p in MAIN_REFS for p in pos if p is not None):
        return ref("`git branch -d/-D main` deletes the main branch")
    if move:
        if any(p in MAIN_REFS for p in pos if p is not None):
            return ref("`git branch -m/-M` renames main or overwrites it")
        if len(pos) == 1 and t.branch() == DEFAULT_BRANCH:
            return ref("`git branch -m <name>` renames the checked-out main")
        return None
    if copy:
        if pos and pos[-1] in MAIN_REFS:
            return ref("`git branch -c/-C <x> main` overwrites main")
        return None
    if force and pos and pos[0] in MAIN_REFS:
        return ref("`git branch -f main` force-moves main")
    return None




def push_dest_is_default(dest):
    return re.match(r"^refs/remotes/[^/]+/" + re.escape(DEFAULT_BRANCH) + "$", dest) is not None


def j_push(args, t):
    if not t.protected_repo:
        return None
    opts, pos, dd = parse_opts(args, short_val="o",
                               long_val=("--repo", "--push-option", "--receive-pack", "--exec"))
    if has_exact(opts, "-n", "--dry-run"):
        return None
    if has_prefix(opts, "--mirror"):
        return ref("`git push --mirror` force-updates every remote ref, main included")
    force = has_prefix(opts, "-f", "--force", "--force-with-lease")
    delete = has_prefix(opts, "-d", "--delete")
    specs = list(pos if any(opt_is(n, "--repo") for n, _ in opts) else pos[1:])
    specs += list(dd or [])
    for spec in specs:
        if spec is None:
            if force or delete:
                return ref("a force push or a delete toward a ref the door cannot expand (it may "
                           "be main)")
            continue
        plus = spec.startswith("+")
        s = spec[1:] if plus else spec
        if delete:
            if s in MAIN_REFS:
                return ref("`git push --delete main` deletes the remote main")
            continue
        src, colon, dst = s.partition(":")
        if not colon:
            dst = src
        if colon and not src and dst in MAIN_REFS:
            return ref("`git push <remote> :main` deletes the remote main")
        if colon and not src and not dst and (force or plus):
            return ref("`git push --force <remote> :` force-pushes every matching branch, main "
                       "included")
        if dst in ("HEAD", "@") or (not colon and src in ("HEAD", "@")):
            dst = t.branch()
            if dst == "HEAD":
                dst = None
        if (force or plus) and (dst is None or dst in MAIN_REFS or "*" in dst):
            return ref("a force push whose destination is (or may be) main rewrites the main "
                       "every worktree and the hub build on")
    if not specs and force:
        if has_prefix(opts, "--all", "--branches"):
            return ref("`git push --all --force` force-pushes main too")
        dest = t.push_dest()
        if dest is not None:
            if push_dest_is_default(dest):
                return ref("`git push --force` from main force-pushes the remote main")
        elif t.branch() in (None, DEFAULT_BRANCH):
            return ref("`git push --force` from main force-pushes the remote main")
    return None


def j_worktree(args, t):
    if not t.protected_repo or not args or args[0] != "add":
        return None
    opts, _pos, _dd = parse_opts(args[1:], short_val="bB", long_val=("--reason",))
    if any(n == "-B" and v in MAIN_REFS for n, v in opts):
        return ref("`git worktree add -B main` force-resets the main branch")
    return None


def j_filter(args, t):
    if not t.protected_repo:
        return None
    return ref("history rewriting tools rewrite every branch, main included")


JUDGES = {
    "checkout": j_checkout,
    "switch": j_switch,
    "restore": j_restore,
    "reset": j_reset,
    "clean": j_clean,
    "stash": j_stash,
    "commit": j_history("`git commit` writes straight onto the hub's main instead of a PR",
                        readonly_flags=("--dry-run",)),
    "merge": j_merge,
    "pull": j_pull,
    "rebase": j_history("`git rebase` rewrites the hub's main",
                        readonly_flags=("--show-current-patch",)),
    "cherry-pick": j_history("`git cherry-pick` writes straight onto the hub's main"),
    "revert": j_history("`git revert` writes straight onto the hub's main"),
    "am": j_history("`git am` writes straight onto the hub's main",
                    readonly_flags=("--show-current-patch",)),
    "bisect": j_history("`git bisect` checks other commits out on the hub",
                        readonly=("log", "view", "visualize", "help")),
    "read-tree": j_read_tree,
    "checkout-index": j_checkout_index,
    "rm": j_rm,
    "symbolic-ref": j_symbolic_ref,
    "update-ref": j_update_ref,
    "branch": j_branch,
    "push": j_push,
    "worktree": j_worktree,
    "filter-branch": j_filter,
    "filter-repo": j_filter,
}

BUILTINS = set(JUDGES) | set("""
add annotate apply archive blame bundle cat-file check-attr check-ignore check-mailmap
check-ref-format cherry citool clone column commit-graph commit-tree config count-objects
credential describe diff diff-files diff-index diff-tree difftool fast-export fast-import fetch
fetch-pack fmt-merge-msg for-each-ref for-each-repo format-patch fsck gc get-tar-commit-id grep
gui hash-object help index-pack init instaweb interpret-trailers log ls-files ls-remote ls-tree
mailinfo mailsplit maintenance merge-base merge-file merge-index merge-tree mergetool mktag mktree
multi-pack-index mv name-rev notes pack-objects pack-refs patch-id prune prune-packed range-diff
reflog remote repack replace request-pull rerere rev-list rev-parse send-email shortlog show
show-branch show-index show-ref sparse-checkout stage status stripspace submodule tag
unpack-file unpack-objects update-index update-server-info var verify-commit verify-pack
verify-tag version whatchanged write-tree
""".split())

# Every verb a judge can refuse, for the token scan of a command the parser cannot read.
REFUSABLE = set(JUDGES)


def judge(verb, args, target):
    fn = JUDGES.get(verb)
    if fn is None or any(a in ("-h", "--help") for a in args):
        return None                     # every git subcommand prints its usage and stops
    return fn(list(args), target)


# ---------------------------------------------------------------------------------------------
# The evaluator: walks the parsed command with the SET of directories each step may run in.
# ---------------------------------------------------------------------------------------------

KEYWORDS = {"if", "then", "elif", "else", "fi", "do", "done", "while", "until", "{", "}", "!",
            "time", "esac"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
PREFIXES = {"command", "builtin", "exec", "nohup", "noglob", "nocorrect", "sudo", "nice",
            "timeout", "gtimeout", "caffeinate", "stdbuf", "env"}
MAX_DIRS = 32        # a chain of `cd` into missing directories doubles the set at every `;`
XARGS_VALUED = {"-n", "-L", "-P", "-s", "-d", "-E", "-a", "-R", "-S", "--max-args",
                "--max-lines", "--max-procs", "--max-chars", "--delimiter", "--arg-file"}


def canon(path):
    """A path the way the filesystem will see it: its existing part resolved, the rest kept."""
    tail = []
    p = os.path.normpath(path) if path else path
    while p and p != os.path.dirname(p) and not os.path.exists(p):
        p, name = os.path.split(p)
        tail.append(name)
    base = os.path.realpath(p) if p else p
    return os.path.join(base, *reversed(tail)) if tail else base


def unescape(text):
    """The escapes printf and zsh's echo turn into the separators a shell reads."""
    return text.replace("\\n", "\n").replace("\\t", "\t")


def printf_text(words):
    fmt = words[0] if words else ""
    args = list(words[1:])
    out = re.sub(r"%[sb]", lambda m: args.pop(0) if args else "", fmt.replace("%%", "\x00"))
    return unescape(out.replace("\x00", "%"))


def substitute(cmd, repl, item, shell):
    """Replace an xargs or find placeholder in a command's words. Unknown item: a shell's code
    gets a variable the door cannot expand (so its `cd` stays unresolved), any other word
    becomes unknown."""
    out = []
    for value, raw in cmd:
        if value is None or repl not in value:
            out.append((value, raw))
        elif item is not None:
            out.append((value.replace(repl, item), raw))
        elif shell:
            out.append((value.replace(repl, '"$__PLACEHOLDER__"'), raw))
        else:
            out.append((None, raw))
    return out


class PendingWorktree:
    """A linked worktree that the same command creates (`git worktree add ../x && cd ../x`):
    it does not exist yet, and it is never a main checkout."""
    protected_main = False
    is_hub = False

    def __init__(self, parent, path, branch):
        self.protected_repo = parent.protected_repo
        self.path = path
        self._branch = branch

    def label(self):
        return "{}, a linked worktree this command creates".format(self.path)

    def branch(self):
        return self._branch

    def is_commit(self, arg):
        return True

    def push_dest(self):
        return None


class Env:
    def __init__(self, variables=None, exported=None, funcs=None, aliases=None):
        self.vars = dict(variables or {})
        self.exported = set(exported or ())
        self.funcs = dict(funcs or {})
        self.aliases = dict(aliases or {})

    def copy(self):
        return Env(self.vars, self.exported, self.funcs, self.aliases)


class Finding:
    def __init__(self, verdict, command, where, note=None):
        self.klass, self.why, self.remedy = verdict
        self.command = command
        self.where = where
        self.note = note

    def key(self):
        return (self.klass, self.command, self.where, self.note)


class Guard:
    def __init__(self, session_cwd):
        self.session_cwd = session_cwd
        self.cache = {}
        self.alias_cache = {}
        self.created = {}        # canon path -> "dir" or "worktree", made earlier in the command
        self.wt_branch = {}      # canon path of a pending worktree -> its -b/-B branch
        self.findings = []
        self.unresolved = []     # (verdict, command, cause)
        self.unparsable = []     # (verdict, command)

    # -- entry --------------------------------------------------------------------------------
    def check(self, command):
        self.eval_code(command, frozenset([self.session_cwd]), Env(), 0)
        if self.unresolved or self.unparsable:
            if self.session_protected(command):
                for verdict, cmd, cause in self.unresolved:
                    self.add(Finding(("unresolved_target",) + verdict[1:], cmd, WORST.label(),
                                     note=cause))
                for verdict, cmd in self.unparsable:
                    self.add(Finding(("unparsable_command",) + verdict[1:], cmd,
                                     "a command the door could not parse",
                                     note="the command could not be parsed, so every git verb in "
                                          "it is judged as if it ran on the hub"))
        return self.findings

    def add(self, finding):
        if all(f.key() != finding.key() for f in self.findings):
            self.findings.append(finding)

    def session_protected(self, command):
        t = self.probe(os.path.realpath(self.session_cwd), MISSING, MISSING)
        if t is not None and t.protected_repo:
            return True
        return bool(self.unparsable) and any(r in command for r in PROTECTED_ROOTS)

    def eval_code(self, code, S, env, depth):
        if depth > MAX_DEPTH:
            return S, S
        try:
            node = Parser(code).parse_program()
        except (ParseError, RecursionError) as e:
            self.token_scan(code, str(e))
            return S | {None}, S | {None}
        return self.eval(node, S, env, depth)

    # -- nodes --------------------------------------------------------------------------------
    def eval(self, node, S, env, depth, stdin=None):
        kind = node[0]
        if kind == "list":
            cur = S
            for andor, sep in node[1]:
                if sep == "&":
                    self.eval(andor, cur, env.copy(), depth)
                else:
                    ok, fail = self.eval(andor, cur, env, depth)
                    cur = self.bound(ok | fail)
            return cur, cur
        if kind == "andor":
            ok, fail = self.eval(node[1][0], S, env, depth)
            for op, pipe in zip(node[2], node[1][1:]):
                if op == "&&":
                    nok, nfail = self.eval(pipe, ok, env, depth) if ok else (frozenset(),
                                                                             frozenset())
                    ok, fail = nok, fail | nfail
                else:
                    nok, nfail = self.eval(pipe, fail, env, depth) if fail else (frozenset(),
                                                                               frozenset())
                    ok, fail = ok | nok, nfail
            return self.bound(ok), self.bound(fail)
        if kind == "pipe":
            cmds = node[1]
            if len(cmds) == 1:
                return self.eval(cmds[0], S, env, depth)
            prev_text = None
            for cmd in cmds[:-1]:
                self.eval(cmd, S, env.copy(), depth, stdin=prev_text)
                prev_text = self.echo_text(cmd, S, env)
            ok, fail = self.eval(cmds[-1], S, env, depth, stdin=prev_text)
            return S | ok, S | fail
        if kind == "sub":
            self.eval(node[1], S, env.copy(), depth)
            self.eval_simple(node[2], S, env, depth)
            return S, S
        if kind == "group":
            out, _ = self.eval(node[1], S, env, depth)
            self.eval_simple(node[2], S, env, depth)
            return out, out
        if kind == "case":
            if node[1] is not None:
                self.expand(node[1], S, env, depth)
            out = S
            for arm in node[2]:
                ok, fail = self.eval(arm, S, env, depth)
                out = out | ok | fail
            return out, out
        if kind == "func":
            if node[1]:
                env.funcs[node[1]] = node[2]
            return S, S
        if kind == "simple":
            return self.eval_simple(node[1], S, env, depth, stdin)
        return S, S

    def bound(self, S):
        """Keep the directory set small: past MAX_DIRS, a missing directory folds onto its
        nearest existing parent (git answers the same there), a pending worktree root stays
        itself, and whatever is left past the cap becomes unresolved."""
        if len(S) <= MAX_DIRS:
            return S
        out = set()
        for p in S:
            while p and p != os.path.dirname(p) and not os.path.isdir(p) \
                    and self.created.get(canon(p)) != "worktree":
                p = os.path.dirname(p)
            out.add(p)
        if len(out) > MAX_DIRS:
            out = set(sorted(out, key=lambda c: c or "")[:MAX_DIRS]) | {None}
        return frozenset(out)

    def echo_text(self, node, S, env):
        """`echo "git ..." | bash`: the text a command on the right of the pipe reads."""
        if node[0] != "simple":
            return None
        return self.text_of(node[1], S, env)

    def text_of(self, simple, S, env, words=None):
        """What `echo`, `printf`, or `cat` fed by a heredoc prints, or None. `words`, when
        given, are the already expanded words (expanding twice per level of `$(...)` nesting
        made the cost double at every level)."""
        if not simple.words:
            return None
        if words is None:
            words = [self.expand(w, S, env, 0, evaluate=False) for w in simple.words]
        if any(w is None for w in words):
            return None
        head = os.path.basename(words[0])
        if head == "echo":
            args = words[1:]
            while args and re.fullmatch(r"-[neE]+", args[0]):
                args = args[1:]
            return unescape(" ".join(args))
        if head == "printf":
            return printf_text(words[1:])
        if head == "cat" and all(w == "-" for w in words[1:]):
            if simple.heredocs:
                return simple.heredocs[-1][0]
            if simple.herestrings:
                return self.expand(simple.herestrings[-1], S, env, 0, evaluate=False)
        return None

    # -- words --------------------------------------------------------------------------------
    def expand(self, word, S, env, depth, evaluate=True):
        """The word's value, or None when it depends on something the door cannot know. Every
        command substitution inside is evaluated as code (it runs, whatever its value)."""
        out = []
        known = True
        for part in word.parts:
            kind = part[0]
            if kind == "lit":
                out.append(part[1])
            elif kind == "tilde":
                out.append(HOME)
            elif kind == "var":
                value = self.var(part[1], S, env)
                if value is None:
                    known = False
                else:
                    out.append(value)
            elif kind == "sub":
                if evaluate:
                    self.eval(part[1], S, env.copy(), depth + 1)
                value = self.sub_value(part[1], S, env)
                if value is None:
                    known = False
                else:
                    out.append(value)
            else:
                known = False
        return "".join(out) if known else None

    def var(self, name, S, env):
        if name == "PWD":
            return next(iter(S)) if len(S) == 1 else None
        if name in env.vars:
            return env.vars[name]
        if name == "HOME":
            return HOME
        return None

    def sub_value(self, node, S, env):
        """The value of `$(pwd)`, `$(git rev-parse ...)`, `$(git branch --show-current)` (they
        say where a command goes) and of `$(cat <<EOF ...)`, `$(echo ...)`, `$(printf ...)`
        (they can carry code for `bash -c` or `eval`); anything else is unknown."""
        simple = unwrap(node)
        if simple is None:
            return None
        words = [self.expand(w, S, env, 0, evaluate=False) for w in simple.words]
        text = self.text_of(simple, S, env, words)
        if text is not None:
            return text.rstrip("\n")
        if len(S) != 1 or None in S:
            return None
        cwd = next(iter(S))
        if not words or any(w is None for w in words):
            return None
        if words == ["pwd"]:
            return cwd
        if os.path.basename(words[0]) != "git":
            return None
        args = words[1:]
        base = cwd
        while len(args) >= 2 and args[0] == "-C":
            base = os.path.join(base, args[1])
            args = args[2:]
        if not os.path.isdir(base) or not args:
            return None
        if args[0] == "rev-parse":
            out = run_git(["git", "rev-parse"] + args[1:], base)
        elif args == ["branch", "--show-current"]:
            out = run_git(["git", "rev-parse", "--abbrev-ref", "HEAD"], base)
            out = "" if out and out.strip() == "HEAD" else out
        else:
            return None
        if out is None:
            return None
        out = out.rstrip("\n")
        return out if "\n" not in out else None

    def words_of(self, node, S, env, depth):
        """[(value, raw)] with unquoted variables word-split the way bash would."""
        out = []
        for w in node.words:
            value = self.expand(w, S, env, depth)
            if (value is not None and len(w.parts) == 1 and w.parts[0][0] == "var"
                    and not w.quoted and value.split() != [value]):
                out.extend((piece, w.raw) for piece in value.split())
            else:
                out.append((value, w.raw))
        return out

    # -- simple commands ----------------------------------------------------------------------
    def eval_simple(self, node, S, env, depth, stdin=None):
        assigns = [(name, self.expand(w, S, env, depth)) for name, w in node.assigns]
        words = self.words_of(node, S, env, depth)
        for w in node.redir_words:
            self.expand(w, S, env, depth)
        herestrings = [self.expand(w, S, env, depth) for w in node.herestrings]
        for proc in node.procsubs:
            self.eval(proc, S, env.copy(), depth + 1)
        for body, quoted in node.heredocs:
            if not quoted:
                for sub in heredoc_subs(body):
                    self.eval(sub, S, env.copy(), depth + 1)
        if node.heredocs:
            stdin = node.heredocs[-1][0]
        elif herestrings:
            stdin = herestrings[-1]
        if not words:
            for name, value in assigns:
                env.vars[name] = value
            return S, S
        return self.run(words, S, env, dict(assigns), stdin, depth, node.raw)

    def run(self, words, S, env, cmd_env, stdin, depth, raw):
        while words and words[0][0] in KEYWORDS:
            dropped = words[0][0]
            words = words[1:]
            if dropped == "time" and words and words[0][0] == "-p":
                words = words[1:]
        if words and words[0][0] == "--":
            words = words[1:]                    # `time -p -- git ...`
        if not words:
            return S, S
        head = words[0][0]
        if head in ("for", "select"):
            if len(words) > 1 and words[1][0]:
                env.vars[words[1][0]] = None
            return S, S
        if head == "function":
            return S, S
        if head in env.aliases and depth < MAX_DEPTH:
            try:
                expansion = [(w, w) for w in shlex.split(env.aliases[head])]
            except ValueError:
                expansion = []
            return self.run(expansion + words[1:], S, env, cmd_env, stdin, depth + 1, raw)
        if head in env.funcs and depth < MAX_DEPTH:
            return self.eval(env.funcs[head], S, env, depth + 1)

        words, S = self.strip_prefixes(words, S, env, cmd_env)
        if not words:
            return S, S
        value, raw0 = words[0]
        name = os.path.basename(value) if value is not None else None
        args = [w for w, _ in words[1:]]
        if name is None and re.search(r"\bgit\b", raw0 or ""):
            name = "git"
        if name in ("cd", "pushd", "chdir"):
            return self.cd(args, S)
        if name == "popd":
            return frozenset([None]), S
        if name == "git":
            self.git(words[1:], S, env, cmd_env, depth, raw)
            return S, S
        if name == "xargs":
            self.xargs(words[1:], S, env, cmd_env, stdin, depth, raw)
            return S, S
        if name == "mkdir":
            self.mkdir(args, S)
            return S, S
        if name in SHELLS:
            self.shell(args, S, env, stdin, depth)
            return S, S
        if name == "eval":
            if args and all(a is not None for a in args):
                return self.eval_code(" ".join(args), S, env, depth + 1)
            return S, S
        if name in ("export", "declare", "typeset", "local", "readonly"):
            self.declare(words[1:], env, export=(name == "export" or "-x" in args))
            return S, S
        if name == "unset":
            for a in args:
                if a and not a.startswith("-"):
                    env.vars[a] = ""
            return S, S
        if name == "read":
            for a in args:
                if a and NAME.fullmatch(a):
                    env.vars[a] = None
            return S, S
        if name == "alias":
            for a in args:
                if a and "=" in a:
                    k, _, v = a.partition("=")
                    env.aliases[k] = v
            return S, S
        if name == "find":
            self.find_exec(words[1:], S, env, depth)
            return S, S
        if name == "watch":
            rest = [a for a in args if a is not None]
            while rest and rest[0].startswith("-"):
                rest = rest[2:] if rest[0] in ("-n", "--interval") else rest[1:]
            if rest:
                self.eval_code(" ".join(rest), S, env.copy(), depth + 1)
            return S, S
        return S, S

    def strip_prefixes(self, words, S, env, cmd_env):
        while words:
            value = words[0][0]
            name = os.path.basename(value) if value else None
            if name not in PREFIXES:
                break
            rest = words[1:]
            if name == "env":
                while rest:
                    a, a_raw = rest[0]
                    if a is None:
                        m = ASSIGN.match(a_raw or "")
                        if not m:
                            break
                        cmd_env[m.group(1)] = None     # `env X=$unknown git ...`
                        rest = rest[1:]
                        continue
                    if a in ("-i", "-", "-0", "-v", "--ignore-environment", "--null"):
                        rest = rest[1:]
                    elif a in ("-u", "--unset", "-P"):
                        rest = rest[2:]
                    elif a in ("-C", "--chdir"):
                        target = rest[1][0] if len(rest) > 1 else None
                        S = self.cd([target], S)[0]
                        rest = rest[2:]
                    elif a.startswith("--chdir="):
                        S = self.cd([a.split("=", 1)[1]], S)[0]
                        rest = rest[1:]
                    elif a in ("-S", "--split-string"):
                        split = rest[1][0] if len(rest) > 1 else None
                        try:
                            pieces = shlex.split(split) if split else []
                        except ValueError:
                            pieces = []
                        rest = [(p, p) for p in pieces] + rest[2:]
                    elif ASSIGN.match(a):
                        k, _, v = a.partition("=")
                        cmd_env[k] = v
                        rest = rest[1:]
                    elif a.startswith("-"):
                        rest = rest[1:]
                    else:
                        break
            elif name == "command":
                if rest and rest[0][0] in ("-v", "-V"):
                    return [], S
                while rest and rest[0][0] in ("-p", "--"):
                    rest = rest[1:]
            elif name == "exec":
                while rest and rest[0][0] and rest[0][0].startswith("-"):
                    rest = rest[2:] if rest[0][0] == "-a" else rest[1:]
            elif name in ("nice",):
                while rest and rest[0][0] and rest[0][0].startswith("-"):
                    rest = rest[2:] if rest[0][0] == "-n" else rest[1:]
            elif name in ("timeout", "gtimeout"):
                while rest and rest[0][0] and rest[0][0].startswith("-"):
                    rest = rest[2:] if rest[0][0] in ("-s", "-k") else rest[1:]
                rest = rest[1:]
            elif name == "sudo":
                while rest and rest[0][0] and rest[0][0].startswith("-"):
                    flag = rest[0][0]
                    rest = rest[2:] if flag in ("-u", "-g", "-C", "-p", "-U", "-r", "-t",
                                                "-h", "-D") else rest[1:]
            elif name == "caffeinate":
                while rest and rest[0][0] and rest[0][0].startswith("-"):
                    rest = rest[2:] if rest[0][0] in ("-t", "-w") else rest[1:]
            elif name == "stdbuf":
                while rest and rest[0][0] and rest[0][0].startswith("-"):
                    flag = rest[0][0]
                    rest = rest[2:] if flag in ("-i", "-o", "-e") else rest[1:]
            else:
                while rest and rest[0][0] and rest[0][0].startswith("-"):
                    rest = rest[1:]
            words = rest
        return words, S

    def declare(self, words, env, export):
        for value, raw in words:
            text = value if value is not None else raw
            if not text or text.startswith("-"):
                continue
            k, eq, v = text.partition("=")
            if not NAME.fullmatch(k):
                continue
            if eq:
                env.vars[k] = v if value is not None else None
            if export:
                env.exported.add(k)

    def cd(self, args, S):
        pos = [a for a in args if not (a and re.fullmatch(r"-[LPe@qs]+", a))]
        if pos and pos[0] == "--":
            pos = pos[1:]
        if not pos:
            target = HOME
        elif len(pos) > 1 or pos[0] is None or pos[0] == "-" or re.fullmatch(r"[+-]\d+", pos[0]):
            target = None
        else:
            target = pos[0]
        ok, fail = set(), set()
        for cwd in S:
            if target is None or (cwd is None and not os.path.isabs(target)):
                ok.add(None)
                fail.add(cwd)
                continue
            path = os.path.normpath(os.path.join(cwd, target) if cwd else target)
            ok.add(path)
            if not os.path.isdir(path) and not self.will_exist(path):
                fail.add(cwd)
        return frozenset(ok), frozenset(fail)

    def will_create(self, path, kind, branch=None):
        """Remember a directory an earlier step of the same command creates."""
        p = canon(path)
        if not p or os.path.exists(p):
            return
        self.created[p] = kind
        if kind == "worktree":
            self.wt_branch[p] = branch
        p = os.path.dirname(p)
        while p and p != os.path.dirname(p) and not os.path.exists(p) and p not in self.created:
            self.created[p] = "dir"
            p = os.path.dirname(p)

    def will_exist(self, path):
        c = canon(path)
        if c in self.created:
            return True
        return any(k == "worktree" and c.startswith(w + os.sep) for w, k in self.created.items())

    def pending_worktree(self, path):
        c = canon(path)
        for w, k in self.created.items():
            if k == "worktree" and (c == w or c.startswith(w + os.sep)):
                return w
        return None

    def mkdir(self, args, S):
        skip = False
        for a in args:
            if skip:
                skip = False
                continue
            if a is None:
                continue
            if a in ("-m", "--mode"):
                skip = True
                continue
            if a.startswith("-"):
                continue
            for cwd in S:
                if cwd is None and not os.path.isabs(a):
                    continue
                self.will_create(os.path.normpath(os.path.join(cwd, a) if cwd else a), "dir")

    def xargs(self, words, S, env, cmd_env, stdin, depth, raw):
        """xargs runs its command with items read from stdin: known when an `echo`, `printf` or
        heredoc feeds it, unknown otherwise."""
        repl = None
        rest = list(words)
        while rest and rest[0][0] and rest[0][0].startswith("-"):
            flag = rest[0][0]
            if flag in ("-I", "-J"):
                repl = rest[1][0] if len(rest) > 1 else None
                rest = rest[2:]
            elif flag.startswith(("-I", "-J")):
                repl = flag[2:]
                rest = rest[1:]
            elif flag in ("-i", "--replace"):
                repl = "{}"
                rest = rest[1:]
            elif flag.startswith("--replace="):
                repl = flag.split("=", 1)[1]
                rest = rest[1:]
            elif flag.startswith("-i"):
                repl = flag[2:]
                rest = rest[1:]
            elif flag in XARGS_VALUED:
                rest = rest[2:]
            else:
                rest = rest[1:]
        if not rest:
            return
        label = raw
        if repl is None:
            if stdin is not None:
                extra = [(item, item) for item in stdin.split()[:64]]
            else:
                extra = [(None, "")]
            self.run(rest + extra, S, env.copy(), dict(cmd_env), None, depth + 1, label)
            return
        items = ([line.strip() for line in stdin.splitlines() if line.strip()][:16]
                 if stdin is not None else [None])
        shell = os.path.basename(rest[0][0] or "") in SHELLS
        for item in items or [None]:
            self.run(substitute(rest, repl, item, shell), S, env.copy(), dict(cmd_env), None,
                     depth + 1, label)

    def shell(self, args, S, env, stdin, depth):
        want_c = False
        read_stdin = False              # `-s` or a lone `-`: the code comes from stdin, and
        i = 0                           # whatever follows is positional parameters
        while i < len(args):
            a = args[i]
            if a is None:
                return
            if a == "-":
                read_stdin = True
                i += 1
                break
            if a == "--":
                i += 1
                break
            if a in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
                i += 2
                continue
            if a.startswith("--"):
                i += 1
                continue
            if len(a) > 1 and a[0] in "-+":
                if "c" in a[1:]:
                    want_c = True
                if "s" in a[1:]:
                    read_stdin = True
                i += 1
                continue
            break
        rest = args[i:]
        if want_c:
            if rest and rest[0] is not None:
                self.eval_code(rest[0], S, env.copy(), depth + 1)
        elif (read_stdin or not rest) and stdin:
            self.eval_code(stdin, S, env.copy(), depth + 1)

    def find_exec(self, words, S, env, depth):
        """`find ROOT... -exec CMD {} ;`: `{}` is a path under a root, so it stands for the root
        (the same repository); `-execdir` runs inside the roots."""
        roots = []
        i = 0
        while i < len(words) and not (words[i][0] or "-").startswith(("-", "(", "!", ")")):
            roots.append(words[i][0])
            i += 1
        roots = roots or ["."]
        while i < len(words):
            flag = words[i][0]
            if flag in ("-exec", "-execdir", "-ok", "-okdir"):
                j = i + 1
                cmd = []
                while j < len(words) and words[j][0] not in (";", "+"):
                    cmd.append(words[j])
                    j += 1
                if cmd:
                    label = " ".join(r for _, r in cmd)
                    shell = os.path.basename(cmd[0][0] or "") in SHELLS
                    for root in roots[:8]:
                        if flag in ("-exec", "-ok"):
                            self.run(substitute(cmd, "{}", root, shell), S, env.copy(), {}, None,
                                     depth + 1, label)
                        else:
                            where = frozenset(
                                None if root is None or (c is None and not os.path.isabs(root))
                                else os.path.normpath(os.path.join(c, root) if c else root)
                                for c in S)
                            self.run(substitute(cmd, "{}", None, shell), where, env.copy(), {},
                                     None, depth + 1, label)
                i = j
            i += 1

    # -- git ----------------------------------------------------------------------------------
    def git(self, words, S, env, cmd_env, depth, raw):
        """`words` are the (value, raw) pairs after `git`; a value is None when unexpandable."""
        args = [v for v, _ in words]
        raws = [r for _, r in words]
        genv = {k: env.vars.get(k) for k in env.exported}
        genv.update(cmd_env)
        git_dir = genv.get("GIT_DIR", MISSING)
        work_tree = genv.get("GIT_WORK_TREE", MISSING)
        dirs = []
        inline_alias = {}
        unknown_global = False
        i = 0
        verb = MISSING
        while i < len(args):
            a = args[i]
            if a is None:
                spelled = raws[i] or ""
                if spelled.startswith("--git-dir"):
                    git_dir = None
                elif spelled.startswith("--work-tree"):
                    work_tree = None
                else:
                    unknown_global = True        # `git $OPTS status`: it may hold a -C
                i += 1
                continue
            nxt = args[i + 1] if i + 1 < len(args) else None
            if a == "-C":
                dirs.append(nxt)
                i += 2
            elif a in ("--git-dir", "--work-tree", "--namespace", "--super-prefix",
                       "--config-env", "--attr-source"):
                if a == "--git-dir":
                    git_dir = nxt
                elif a == "--work-tree":
                    work_tree = nxt
                i += 2
            elif a.startswith("--git-dir="):
                git_dir = a.split("=", 1)[1]
                i += 1
            elif a.startswith("--work-tree="):
                work_tree = a.split("=", 1)[1]
                i += 1
            elif a == "-c":
                if nxt and nxt.startswith("alias."):
                    k, _, v = nxt[len("alias."):].partition("=")
                    inline_alias[k] = v
                i += 2
            elif a in ("--version", "-v", "--help", "-h", "--html-path", "--man-path",
                       "--info-path", "--exec-path"):
                return
            elif a.startswith("-"):
                i += 1
            else:
                verb = a
                break
        if verb is MISSING:
            return
        if unknown_global:
            dirs.append(None)
            if verb not in BUILTINS:
                verb = None                      # `git $X -- .`: the verb itself may be hidden
        rest = args[i + 1:]
        if verb is not None and verb not in BUILTINS:
            alias = inline_alias.get(verb) or self.alias(verb, S, dirs)
            if not alias or depth >= MAX_DEPTH:
                return
            if alias.startswith("!"):
                tail = " ".join(shlex.quote(x) for x in rest if x is not None)
                self.eval_code(alias[1:] + " " + tail, S, env.copy(), depth + 1)
                return
            try:
                expansion = shlex.split(alias)
            except ValueError:
                return
            prefix = []
            for d in dirs:
                prefix += [("-C", "-C"), (d, d or "")]
            self.git(prefix + [(w, w) for w in expansion] + list(words[i + 1:]), S, env,
                     cmd_env, depth + 1, raw)
            return
        if verb == "worktree" and rest and rest[0] == "add":
            self.record_worktree(rest[1:], S, dirs)
        worst = judge(verb, rest, WORST) if verb is not None else (
            "hub_worktree_destroy", "a git subcommand the door cannot expand", REMEDY_HUB)
        if worst is None:
            return
        display = raw.strip() or "git " + " ".join(a if a is not None else "?" for a in args)
        for cwd in sorted(S, key=lambda c: c or ""):
            cause = None
            if cwd is None:
                cause = "a `cd` the door could not follow (a variable, `cd -`, popd)"
            elif any(d is None for d in dirs):
                cause = "`git -C` (or a git option) with a value the door could not expand"
            elif git_dir is None or work_tree is None:
                cause = "GIT_DIR or GIT_WORK_TREE with a value the door could not expand"
            elif verb is None:
                cause = "a git subcommand the door could not expand"
            if cause:
                self.unresolved.append((worst, display, cause))
                continue
            base = self.git_base(cwd, dirs)
            pending = None
            if git_dir is MISSING and work_tree is MISSING and not os.path.exists(base):
                pending = self.pending_worktree(base)
            if pending:
                parent = self.probe(os.path.dirname(pending), MISSING, MISSING)
                if parent is None:
                    continue
                target = PendingWorktree(parent, pending, self.wt_branch.get(pending))
            else:
                gd = git_dir if git_dir is MISSING else os.path.join(base, git_dir)
                wt = work_tree if work_tree is MISSING else os.path.join(base, work_tree)
                target = self.probe(base, gd, wt)
            if target is None:
                continue
            verdict = judge(verb, rest, target)
            if verdict:
                self.add(Finding(verdict, display, target.label()))

    @staticmethod
    def git_base(cwd, dirs):
        base = os.path.realpath(cwd)
        for d in dirs:
            base = os.path.realpath(os.path.join(base, d))
        return base

    def record_worktree(self, args, S, dirs):
        """`git worktree add <path>`: a later `cd <path>` or `git -C <path>` lands in a linked
        worktree that does not exist yet."""
        opts, pos, _dd = parse_opts(args, short_val="bB", long_val=("--reason",))
        if not pos or pos[0] is None or any(d is None for d in dirs):
            return
        branch = next((v for n, v in opts if n in ("-b", "-B")), None)
        for cwd in S:
            if cwd is not None:
                self.will_create(os.path.join(self.git_base(cwd, dirs), pos[0]), "worktree",
                                 branch)

    def alias(self, verb, S, dirs):
        """The alias git would expand `verb` to, read with ONE `git config --get-regexp` per
        directory (a spawn per unknown verb cost 5 s for 300 of them)."""
        for cwd in S:
            if cwd is None or any(d is None for d in dirs):
                continue
            base = cwd
            for d in dirs:
                base = os.path.join(base, d)
            while base and not os.path.isdir(base):
                base = os.path.dirname(base)
            base = base or "/"
            if base not in self.alias_cache:
                table = {}
                out = run_git(["git", "config", "--get-regexp", r"^alias\."], base) or ""
                for line in out.splitlines():
                    key, _, value = line.partition(" ")
                    table[key[len("alias."):]] = value.strip()
                self.alias_cache[base] = table
            if verb in self.alias_cache[base]:
                return self.alias_cache[base][verb]
        return None

    def probe(self, base, git_dir, work_tree):
        run_dir = base
        while run_dir and run_dir != "/" and not os.path.isdir(run_dir):
            run_dir = os.path.dirname(run_dir)
        key = (run_dir, git_dir, work_tree)     # every missing child of a dir answers the same
        if key in self.cache:
            return self.cache[key]
        argv = ["git"]
        if git_dir is not MISSING:
            argv += ["--git-dir", git_dir]
        if work_tree is not MISSING:
            argv += ["--work-tree", work_tree]
        target = None
        out = run_git(argv + ["rev-parse", "--path-format=absolute", "--git-dir",
                              "--git-common-dir"], run_dir or "/")
        lines = out.splitlines() if out else []
        if len(lines) >= 2:
            top = run_git(argv + ["rev-parse", "--show-toplevel"], run_dir or "/")
            target = Target(argv, run_dir or "/", lines[0], lines[1],
                            top.strip() if top and top.strip() else None)
        self.cache[key] = target
        return target

    # -- the fallback for what the parser cannot read -----------------------------------------
    def token_scan(self, code, error):
        for segment in re.split(r"[;&|()\n`]|\$\(", code):
            try:
                tokens = shlex.split(segment)
            except ValueError:
                tokens = segment.split()
            for k, tok in enumerate(tokens):
                if os.path.basename(tok) != "git":
                    continue
                rest = tokens[k + 1:]
                j = 0
                while j < len(rest):
                    if rest[j] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
                        j += 2
                        continue
                    if rest[j].startswith("-"):
                        j += 1
                        continue
                    if rest[j] in REFUSABLE:
                        verdict = judge(rest[j], rest[j + 1:], WORST)
                        if verdict:
                            self.unparsable.append((verdict, " ".join(tokens[k:])))
                    break


def unwrap(node):
    """The single simple command of a `$(...)`, or None."""
    while node[0] in ("list", "andor", "pipe"):
        inner = node[1]
        if len(inner) != 1:
            return None
        node = inner[0][0] if node[0] == "list" else inner[0]
    if node[0] != "simple":
        return None
    return node[1]


# ---------------------------------------------------------------------------------------------
# The door.
# ---------------------------------------------------------------------------------------------

def trunk_text(text):
    """The reasons are written for a trunk called main. Say the configured default branch
    instead, except in "main checkout" and "main working tree", where main means the primary
    working tree and not a branch."""
    if DEFAULT_BRANCH == "main":
        return text
    return re.sub(r"\bmain\b(?! (?:checkout|working tree))", lambda m: DEFAULT_BRANCH, text)


def render(findings):
    lines = ["git-hub-guard REFUSED this Bash call. Nothing ran."]
    for f in findings:
        lines.append("")
        lines.append("Command: " + f.command)
        lines.append("Target: " + f.where)
        if f.note:
            lines.append("Unresolved: " + f.note + ". This session runs inside a protected "
                         "repo, so the door fails closed; spell the directory out literally.")
        lines.append("Why: " + trunk_text(f.why) + ".")
        lines.append("Do instead: " + f.remedy)
    lines.append("")
    if NOTE:
        lines.append("Why this door exists: " + NOTE.rstrip() + ("" if NOTE.rstrip().endswith(
            (".", "!", "?")) else "."))
    lines.append("There is no bypass (no environment variable, no marker file, no flag), and "
                 ".claude/auto-worktree-off does not disarm this door.")
    return "\n".join(lines) + "\n"


def body():
    payload = trace.read_payload()
    configure_from(trace.plugin_settings(payload, root=PLUGIN_ROOT))
    if payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    command = tool_input.get("command")
    if not isinstance(command, str) or "git" not in command:
        return 0
    cwd = payload.get("cwd") or os.getcwd()
    findings = Guard(os.path.normpath(cwd)).check(command)
    if not findings:
        return 0
    trace.witness(DOOR, "PreToolUse", findings[0].klass, payload)
    sys.stderr.write(render(findings))
    return 2


def main():
    trace.configure(sys.argv)
    return trace.run(HOOK, "PreToolUse", body, TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(main())
