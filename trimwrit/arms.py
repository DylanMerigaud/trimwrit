"""Arms: one rule, one situation, delivered four ways (none, prose, door, both).

WHY THIS EXISTS NEXT TO `runner.py`. The ablation there removes a whole FILE and runs in a
scratch directory with no project settings, so two questions it can never answer are:

1. What does ONE paragraph of a real instruction file do, while every other rule in that file
   still competes for attention? `ablate_lines` removes exactly the line ranges given and
   nothing else, so the arm without the paragraph is the real file minus one rule.
2. What does a HOOK do? A hook only runs when a settings file registers it, and the old runner
   writes none, so every replay it ever made was a prose-only replay. `prepare_workdir` copies
   the hook scripts into the scratch repository and registers them in its project
   `.claude/settings.json`, on the lifecycle event the caller names.

An arm is the pair (prose on or off, a list of hook registrations). The four classic arms are
built by `classic_arms`, but nothing here knows which rule is being tested.

ISOLATION IS AN ENVIRONMENT, NOT A FLAG. Measured while this module was written: a `claude -p`
started from inside another Claude Code session inherits `CLAUDE_CODE_SIMPLE=1` (bare mode) and
then loads NO project instruction file and NO project hook, silently; and `--setting-sources
project,local` alone does not remove the operator's own hooks registered by a plugin or the
user layer on every version. So a run gets `clean_env`: a minimal environment (no inherited
CLAUDE_* or ANTHROPIC_* variable, so no API key either) plus a fresh, empty `CLAUDE_CONFIG_DIR`
per run. The operator's user instruction file, user hooks, skills, plugins and MCP servers live
in the real config directory, which the run never sees. The project layer (the scratch
repository) is the only layer left, which is exactly the layer the arm controls.

CONFINEMENT. A replay here needs real tools (it edits files, runs tests, commits), unlike the
text-only replays of `runner.py`. `SANDBOX_SETTINGS` turns on Claude Code's own sandbox for
Bash (writes confined to the scratch directory, no network) and denies the file tools any read
under the operator's code, config and keychain directories. Measured on 2.1.285: `ls ~/Code`
and a write to the home directory both fail with "operation not permitted", a Read of a file
under ~/Code is refused by the permission layer, and a curl is refused by the sandbox.

A GRADER THAT CANNOT FAIL MEASURES NOTHING. `prove` runs a grader on one sample it must call a
violation and one it must not, and refuses to go on otherwise.
"""
import json
import os
import shutil
import subprocess
import time
import uuid

HOOK_DIR = os.path.join(".claude", "hooks")
DEFAULT_TOOLS = ("Bash", "Read", "Edit", "Write", "Glob", "Grep")

SANDBOX_SETTINGS = {
    "sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True,
                "allowUnsandboxedCommands": False},
    "permissions": {"deny": ["Read(~/Code/**)", "Read(~/.claude/**)", "Read(~/Library/**)",
                             "Read(~/.ssh/**)", "Read(~/.config/**)", "WebFetch", "WebSearch"]},
}

# Variables a run keeps from the parent. Everything else is dropped, on purpose: see the module
# docstring for the two variables that silently changed what a run loaded.
KEEP_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "SHELL")


class ArmsError(Exception):
    pass


# ---------------------------------------------------------------- the prose half


def ablate_lines(text, ranges):
    """`text` without the 1-based, inclusive line ranges in `ranges` ([(first, last), ...]).

    Refuses a range outside the file or two ranges that overlap: a pinned line range that no
    longer matches its file is a different experiment, and it must fail loudly, not remove
    whatever now sits at those numbers."""
    lines = text.splitlines(keepends=True)
    drop = set()
    for first, last in ranges:
        first, last = int(first), int(last)
        if first < 1 or last < first or last > len(lines):
            raise ArmsError("line range {}-{} is outside a file of {} lines".format(
                first, last, len(lines)))
        span = set(range(first, last + 1))
        if span & drop:
            raise ArmsError("line range {}-{} overlaps another range".format(first, last))
        drop |= span
    return "".join(line for i, line in enumerate(lines, 1) if i not in drop)


# ---------------------------------------------------------------- the door half


def hook_command(script, args=(), interpreter="python3"):
    """The command a settings file runs for a hook copied into the scratch repository."""
    parts = [interpreter, '"$CLAUDE_PROJECT_DIR/{}/{}"'.format(HOOK_DIR, script)]
    parts.extend(args)
    return " ".join(parts)


def hook_settings(registrations):
    """A `hooks` block for `.claude/settings.json`.

    `registrations` is a list of dicts: {"event": "Stop", "script": "x.py", "args": [...],
    "matcher": "Write|Edit" (optional), "timeout": 10 (optional)}. One settings entry per
    registration, in the order given, the shape Claude Code reads."""
    out = {}
    for reg in registrations:
        entry = {"hooks": [{"type": "command",
                            "command": hook_command(reg["script"], reg.get("args", ()),
                                                    reg.get("interpreter", "python3")),
                            "timeout": int(reg.get("timeout", 10))}]}
        if reg.get("matcher"):
            entry["matcher"] = reg["matcher"]
        out.setdefault(reg["event"], []).append(entry)
    return out


def classic_arms(door_registrations):
    """The four arms of a prose-and-door comparison: A0 (neither), AP (prose), AD (door),
    APD (both). Each value is (prose_on, registrations)."""
    regs = list(door_registrations)
    return {"A0": (False, []), "AP": (True, []), "AD": (False, regs), "APD": (True, regs)}


# ---------------------------------------------------------------- the scratch repository


def _git(workdir, *args):
    return subprocess.run(["git", "-C", workdir] + list(args), capture_output=True, text=True)


def prepare_workdir(workdir, template_dir, instruction_text, registrations=(), hook_files=None,
                    instruction_name="CLAUDE.md", sandbox=True, git_init=True, setup=None):
    """Build one run's scratch repository and return its base commit (or None).

    The template is copied, the instruction text written as the project instruction file, the
    hook files copied under `.claude/hooks/` (only when a registration needs them) and
    registered, and the whole thing committed once so that every commit the run makes can be
    told apart from the situation it started in. The instruction file and the settings file are
    part of that base commit; nothing else differs between arms.

    `setup`, when given, is a shell script run in the fresh repository before the base commit,
    for a situation that needs a history (earlier commits, a broken main) and not only files.
    It runs from its own path, never copied into the repository."""
    os.makedirs(workdir, exist_ok=True)
    if template_dir:
        for entry in os.listdir(template_dir):
            src, dst = os.path.join(template_dir, entry), os.path.join(workdir, entry)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
    if instruction_text is not None:
        with open(os.path.join(workdir, instruction_name), "w", encoding="utf-8") as fh:
            fh.write(instruction_text)
    settings = dict(SANDBOX_SETTINGS) if sandbox else {}
    if registrations:
        hook_files = hook_files or {}
        os.makedirs(os.path.join(workdir, HOOK_DIR), exist_ok=True)
        for name, path in sorted(hook_files.items()):
            shutil.copy2(path, os.path.join(workdir, HOOK_DIR, name))
        for reg in registrations:
            if reg["script"] not in hook_files:
                raise ArmsError("hook {} is registered but no file was given".format(
                    reg["script"]))
        settings["hooks"] = hook_settings(registrations)
    if settings:
        os.makedirs(os.path.join(workdir, ".claude"), exist_ok=True)
        with open(os.path.join(workdir, ".claude", "settings.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
    if not git_init:
        return None
    _git(workdir, "init", "-q")
    _git(workdir, "config", "user.name", "replay")
    _git(workdir, "config", "user.email", "replay@localhost")
    _git(workdir, "config", "commit.gpgsign", "false")
    if setup:
        done = subprocess.run(["bash", setup], cwd=workdir, capture_output=True, text=True)
        if done.returncode != 0:
            raise ArmsError("setup {} failed: {}".format(setup, done.stderr.strip()[:300]))
    _git(workdir, "add", "-A")
    _git(workdir, "commit", "-q", "-m", "situation")
    return _git(workdir, "rev-parse", "HEAD").stdout.strip() or None


# ---------------------------------------------------------------- driving claude


def clean_env(config_dir, token=None, extra=None):
    """The environment of one run. See the module docstring: nothing CLAUDE_* or ANTHROPIC_*
    is inherited, the config directory is the run's own, and the credential, when given, is a
    subscription OAuth token (never an API key)."""
    env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
    env["CLAUDE_CONFIG_DIR"] = config_dir
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    for k, v in (extra or {}).items():
        env[k] = v
    return env


def build_cmd(prompt, model, session_id, tools=DEFAULT_TOOLS, max_turns=None,
              permission_mode="acceptEdits", claude="claude", extra_args=()):
    if not model:
        raise ArmsError("a run names its model explicitly; the default moves under you")
    cmd = [claude, "-p", prompt, "--model", model, "--session-id", session_id,
           "--output-format", "stream-json", "--verbose", "--include-hook-events",
           "--strict-mcp-config", "--permission-mode", permission_mode,
           "--tools", ",".join(tools)]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    cmd.extend(extra_args)
    return cmd


def run_claude(cmd, workdir, env, timeout_s, stream_path):
    """Run one `claude -p`, stream its output to `stream_path`, return (exit, wall_s, killed).
    A run past its wall cap is killed and KEPT: the caller grades what it left."""
    start = time.time()
    killed = False
    with open(stream_path, "w", encoding="utf-8") as out, \
            open(stream_path + ".stderr", "w", encoding="utf-8") as err:
        proc = subprocess.Popen(cmd, cwd=workdir, env=env, stdout=out, stderr=err,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            code = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            killed = True
            try:
                os.killpg(proc.pid, 15)
            except OSError:
                pass
            try:
                code = proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, 9)
                code = proc.wait()
    return code, round(time.time() - start, 1), killed


# ---------------------------------------------------------------- reading a run


def _hook_decision(output):
    """(kind, reason) for one hook's stdout: kind is "block" for a Stop-style block, "deny" for
    a PreToolUse denial, else None. A block declared inside `hookSpecificOutput` is still a
    block the hook ISSUED; whether Claude Code honoured it is a separate question (took_effect),
    which is the point of reading both shapes."""
    text = (output or "").strip()
    if not text.startswith("{"):
        return None, None
    try:
        data = json.loads(text)
    except ValueError:
        return None, None
    spec = data.get("hookSpecificOutput") or {}
    if data.get("decision") == "block" or spec.get("decision") == "block":
        return "block", data.get("reason") or spec.get("reason") or ""
    if spec.get("permissionDecision") == "deny":
        return "deny", spec.get("permissionDecisionReason") or ""
    return None, None


def parse_stream(lines):
    """Everything a grader and the outcome table need from one stream-json transcript.

    `stops` is the list of stop attempts in order: the closing text the turn tried to end on,
    and the Stop hooks' decisions on it. A new stop attempt starts at the first assistant text
    after a Stop hook answered. `tools` is every tool call with its result and whether a
    PreToolUse hook denied it."""
    info = {"model": None, "cli_version": None, "session_id": None, "texts": [], "tools": [],
            "stops": [], "denials": [], "result": None, "num_turns": None,
            "output_tokens": None, "is_error": None, "api_error_status": None}
    last_text = ""
    open_stop = None
    by_id = {}
    for raw in lines:
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        kind, sub = ev.get("type"), ev.get("subtype")
        if kind == "system" and sub == "init":
            info["model"] = ev.get("model")
            info["cli_version"] = ev.get("claude_code_version")
            info["session_id"] = ev.get("session_id")
        elif kind == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    last_text = block["text"]
                    info["texts"].append(last_text)
                    open_stop = None
                elif block.get("type") == "tool_use":
                    call = {"id": block.get("id"), "name": block.get("name"),
                            "input": block.get("input"), "result": None, "is_error": None,
                            "denied": False}
                    by_id[call["id"]] = call
                    info["tools"].append(call)
                    open_stop = None
        elif kind == "user":
            content = (ev.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_result" and block.get("tool_use_id") in by_id:
                    call = by_id[block["tool_use_id"]]
                    body = block.get("content")
                    if isinstance(body, list):
                        body = "\n".join(str(x.get("text", "")) for x in body
                                         if isinstance(x, dict))
                    call["result"] = str(body or "")[:4000]
                    call["is_error"] = bool(block.get("is_error"))
        elif kind == "system" and sub == "hook_response":
            event = ev.get("hook_event")
            decision, reason = _hook_decision(ev.get("output") or ev.get("stdout"))
            if event == "Stop":
                if open_stop is None:
                    open_stop = {"text": last_text, "hooks": []}
                    info["stops"].append(open_stop)
                open_stop["hooks"].append({"name": ev.get("hook_name"), "decision": decision,
                                           "reason": reason, "exit_code": ev.get("exit_code"),
                                           "outcome": ev.get("outcome")})
            elif event == "PreToolUse" and decision == "deny":
                info["denials"].append({"reason": reason})
        elif kind == "result":
            info["result"] = ev.get("result")
            info["num_turns"] = ev.get("num_turns")
            info["output_tokens"] = (ev.get("usage") or {}).get("output_tokens")
            info["is_error"] = ev.get("is_error")
            info["api_error_status"] = ev.get("api_error_status")
    # A denied call's result carries the hook's reason: that is how a denial is tied to its call
    # without trusting the order hook events arrive in.
    for den in info["denials"]:
        head = (den["reason"] or "")[:60]
        for call in info["tools"]:
            if not call["denied"] and head and call["result"] and head in call["result"]:
                call["denied"] = True
                den["tool_id"] = call["id"]
                break
    info["closing_text"] = info["stops"][-1]["text"] if info["stops"] else (
        info["result"] or last_text or "")
    if info["result"] and not info["stops"]:
        info["closing_text"] = info["result"]
    return info


def blocks(info):
    """Every refusal a door issued in the run: Stop blocks and PreToolUse denials."""
    n = sum(1 for s in info["stops"] for h in s["hooks"] if h["decision"] == "block")
    return n + len(info["denials"])


def stop_blocks_took_effect(info, violates):
    """[bool] per Stop block: after the block, the run produced a NEW closing text that
    `violates` (the prose grader, never the door) no longer flags. A block after which the run
    never tried to stop again, or stopped on the same text, did not take effect."""
    out = []
    stops = info["stops"]
    for i, stop in enumerate(stops):
        for h in stop["hooks"]:
            if h["decision"] != "block":
                continue
            nxt = stops[i + 1] if i + 1 < len(stops) else None
            out.append(bool(nxt) and nxt["text"] != stop["text"] and not violates(nxt["text"]))
    return out


def write_target(call):
    """What a write-capable call writes to: a file path, or the command for Bash."""
    inp = call.get("input") or {}
    return inp.get("file_path") or inp.get("notebook_path") or (
        "bash" if call.get("name") == "Bash" else None)


def payload_of(call):
    inp = call.get("input") or {}
    for key in ("content", "new_string", "new_source", "command"):
        if isinstance(inp.get(key), str):
            return inp[key]
    return ""


def denials_took_effect(info, violates_payload):
    """[bool] per PreToolUse denial: the next ACCEPTED call to the same target (the same file,
    or the next Bash call for a denied Bash call) does not carry what `violates_payload` flags.
    No later accepted call to that target means it did not take effect."""
    out = []
    tools = info["tools"]
    for i, call in enumerate(tools):
        if not call["denied"]:
            continue
        target = write_target(call)
        nxt = None
        for later in tools[i + 1:]:
            if later["denied"] or later.get("is_error"):
                continue
            if write_target(later) == target:
                nxt = later
                break
        out.append(bool(nxt) and not violates_payload(payload_of(nxt)))
    return out


def new_commits(workdir, base):
    """Messages and patches of the commits the run made on top of `base`."""
    if not base:
        return []
    log = _git(workdir, "log", "--format=%H", "{}..HEAD".format(base)).stdout.split()
    out = []
    for sha in reversed(log):
        msg = _git(workdir, "log", "-1", "--format=%B", sha).stdout
        patch = _git(workdir, "show", "--format=", sha).stdout
        out.append({"sha": sha, "message": msg, "patch": patch[:200000]})
    return out


def tree_files(workdir, max_bytes=200000):
    """Every file in the scratch repository after the run, outside .git, with its text."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(workdir):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in filenames:
            path = os.path.join(dirpath, f)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    out[os.path.relpath(path, workdir)] = fh.read(max_bytes)
            except OSError:
                out[os.path.relpath(path, workdir)] = ""
    return out


# ---------------------------------------------------------------- one run, end to end


def run_arm(prompt, template_dir, instruction_text, registrations, hook_files, model, out_dir,
            token=None, timeout_s=1800, tools=DEFAULT_TOOLS, max_turns=None, claude="claude",
            scratch_root="/tmp", env_extra=None, keep_workdir=False, setup=None):
    """One run of one arm. Everything it leaves goes under `out_dir`: the stream, stderr, the
    run's own transcript as Claude Code wrote it, and `run.json` (the parsed info, the scratch
    tree after the run, the commits it made). Returns (info, workdir_state)."""
    os.makedirs(out_dir, exist_ok=True)
    run_root = os.path.join(scratch_root, "arms-" + uuid.uuid4().hex[:12])
    workdir = os.path.join(run_root, "repo")
    config_dir = os.path.join(run_root, "config")
    tmp_dir = os.path.join(run_root, "tmp")
    os.makedirs(config_dir)
    os.makedirs(tmp_dir)
    base = prepare_workdir(workdir, template_dir, instruction_text, registrations, hook_files,
                           setup=setup)
    session_id = str(uuid.uuid4())
    env = clean_env(config_dir, token, dict(env_extra or {}, TMPDIR=tmp_dir))
    cmd = build_cmd(prompt, model, session_id, tools, max_turns, claude=claude)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    stream = os.path.join(out_dir, "stream.jsonl")
    code, wall, killed = run_claude(cmd, workdir, env, timeout_s, stream)
    with open(stream, encoding="utf-8", errors="replace") as fh:
        info = parse_stream(fh)
    with open(stream + ".stderr", encoding="utf-8", errors="replace") as fh:
        stderr = fh.read()[-4000:]
    info.update({"ts": started, "exit_code": code, "wall_s": wall, "killed": killed,
                 "session_id_requested": session_id, "model_requested": model,
                 "stderr_tail": stderr, "base_commit": base})
    state = {"files": tree_files(workdir), "commits": new_commits(workdir, base)}
    for dirpath, _dirs, filenames in os.walk(os.path.join(config_dir, "projects")):
        for f in filenames:
            if f.endswith(".jsonl"):
                shutil.copy2(os.path.join(dirpath, f), os.path.join(out_dir, "transcript-" + f))
    with open(os.path.join(out_dir, "run.json"), "w", encoding="utf-8") as fh:
        json.dump({"info": info, "state": state}, fh, ensure_ascii=False, indent=1)
    if not keep_workdir:
        shutil.rmtree(run_root, ignore_errors=True)
    return info, state


# ---------------------------------------------------------------- proving a grader


def prove(grader, must_flag, must_pass):
    """Run `grader` (text -> bool, True means violation) on a sample it must flag and one it
    must not. Returns a list of failures; empty means the grader can fail and can pass."""
    failures = []
    if not grader(must_flag):
        failures.append("the grader did not flag the violating sample")
    if grader(must_pass):
        failures.append("the grader flagged the clean sample")
    return failures
