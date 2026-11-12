"""resume_policy: whether a failed turn is worth a "continue", read from the session's transcript.

The StopFailure payload carries a category and, in practice, an EMPTY error_details: on
2026-11-04 a session flagged by the API's safeguards came in as plain `invalid_request` and was
resumed 991 times, each "continue" failing the same way three seconds later. The error text lives
in the transcript (the last main-thread assistant row with isApiErrorMessage), so the verdict is
read there.

Verdicts, in order:
- never: authentication or billing (a credential matter), a safeguards or usage-policy flag (the
  same request is refused again), a request too large to send (it never shrinks by itself).
- invalid_request otherwise: at most one resume in a row per session.
- transient (rate_limit, overloaded, server_error, unknown, network): resumed without end, by
  Dylan's standing order of 2026-09-19 (memory retry-forever-everywhere), but past
  TRANSIENT_FAST consecutive failures the delay doubles per failure, up to MAX_DELAY_S, so a
  failure that loops costs one keystroke an hour instead of one a minute.
- any other category: resumed while fewer than BREAK_AFTER consecutive failures, then left.

"Consecutive" means failures of this session with no successful assistant row between them,
counted from the transcript tail. When no transcript can be read, a per-session counter in the
state directory stands in (reset after RESET_S without a failure).

Usage: resume_policy.py <state_dir>, the StopFailure payload on stdin. Prints shell assignments
VERDICT (resume|skip), REASON, COUNT and FACTOR (the delay multiplier). Never raises.
"""
import json
import os
import re
import shlex
import sys
import time

BREAK_AFTER = 3        # a non-transient failure three times in a row, after Claude Code's own
                       # retries each time, is deterministic: a fourth keystroke changes nothing.
TRANSIENT_FAST = 5     # five quick retries (5 to 10 minutes at 60 to 120 s) cover a blip.
MAX_DELAY_S = 3600
RESET_S = 6 * 3600
TAIL_BYTES = 4 * 1024 * 1024

CREDENTIAL = re.compile(r"^(authentication_failed|billing|insufficient|invalid_api_key"
                        r"|account_on_hold|oauth_org_not_allowed|cloud_credential_error)")
TRANSIENT = re.compile(r"^(rate_limit|overloaded|server_error|unknown|api_error|timeout)$"
                       r"|connection|network|timeout", re.I)
SAFEGUARDS = re.compile(r"safeguards flagged|anthropic\.com/legal/aup|usage policy", re.I)
TOO_LARGE = re.compile(r"prompt is too long|request too large|exceeds? the (model'?s )?context"
                       r"|context (window|length) exceeded|input length and `?max_tokens`? exceed"
                       r"|could not process image|image exceeds", re.I)


def transcript_path(payload):
    path = payload.get("transcript_path")
    if isinstance(path, str) and path:
        return os.path.expanduser(path)
    session, cwd = payload.get("session_id"), payload.get("cwd")
    if not (isinstance(session, str) and session and isinstance(cwd, str) and cwd):
        return ""
    slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    return os.path.join(os.path.expanduser("~"), ".claude", "projects", slug, session + ".jsonl")


def _text(row):
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(x.get("text", "") for x in content
                         if isinstance(x, dict) and x.get("type") == "text")
    return ""


def read_tail(path):
    """(consecutive API error rows at the end of the main thread, the last error's text), or
    None when the transcript cannot be read."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - TAIL_BYTES))
            data = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    lines = data.splitlines()
    if size > TAIL_BYTES and lines:
        lines = lines[1:]
    count, last_error = 0, ""
    for line in lines:
        if '"assistant"' not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("type") != "assistant" or row.get("isSidechain"):
            continue
        if row.get("isApiErrorMessage"):
            count += 1
            last_error = _text(row) or str(row.get("error") or "")
        else:
            count, last_error = 0, ""
    return count, last_error


def state_count(state_dir, session):
    """The fallback counter: this failure included, reset after RESET_S of quiet."""
    if not session:
        return 1
    path = os.path.join(state_dir, "failures-" + re.sub(r"[^A-Za-z0-9_.-]", "_", session) + ".json")
    now = time.time()
    count = 0
    try:
        with open(path, encoding="utf-8") as fh:
            got = json.load(fh)
        if now - float(got.get("at", 0)) < RESET_S:
            count = int(got.get("count", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    count += 1
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"count": count, "at": now}, fh)
    except OSError:
        pass
    return count


def decide(payload, state_dir):
    """(verdict, reason, consecutive count, delay factor)."""
    category = str(payload.get("error") or "").strip()
    details = str(payload.get("error_details") or "")
    session = str(payload.get("session_id") or "")
    fallback = state_count(state_dir, session)
    tail = read_tail(transcript_path(payload))
    if tail is None:
        count, error_text = fallback, ""
    else:
        # The hook is async and the transcript can lag: this failure counts at least once.
        count, error_text = max(1, tail[0]), tail[1]
    text = details + "\n" + error_text

    if CREDENTIAL.match(category):
        return "skip", category + " is a credential or billing matter", count, 1
    if SAFEGUARDS.search(text):
        return "skip", "safeguards flag (the same request is refused again)", count, 1
    if TOO_LARGE.search(text):
        return "skip", "request too large (a retry sends the same size)", count, 1
    if category == "invalid_request":
        if count > 1:
            return "skip", "invalid_request {} times in a row (one retry only)".format(count), count, 1
        return "resume", "invalid_request, first in a row", count, 1
    if not category or TRANSIENT.search(category):
        factor = 2 ** (count - TRANSIENT_FAST) if count > TRANSIENT_FAST else 1
        return "resume", "transient, {} in a row".format(count), count, min(factor, MAX_DELAY_S)
    if count >= BREAK_AFTER + 1:
        return ("skip", "loop breaker: {} {} failures in a row with no successful turn".format(
            count, category), count, 1)
    return "resume", "{} in a row".format(count), count, 1


def main():
    state_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.claude/trimwrit-gates")
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            payload = {}
    except ValueError:
        payload = {}
    try:
        verdict, reason, count, factor = decide(payload, state_dir)
    except Exception as e:  # a policy bug must not cost the resume of a network cut
        verdict, reason, count, factor = "resume", "policy error {}: {}".format(type(e).__name__, e), 1, 1
    for name, value in (("VERDICT", verdict), ("REASON", reason.replace("\n", " ")[:200]),
                        ("COUNT", str(count)), ("FACTOR", str(factor))):
        print("{}={}".format(name, shlex.quote(value)))


if __name__ == "__main__":
    main()
