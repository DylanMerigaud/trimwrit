"""gatekit.transcript: the final text of a turn, for the Stop gates."""
import json


def last_assistant_text(transcript_path):
    """The text of the last main-thread assistant row of a Claude Code transcript, or ''."""
    last = ""
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"type":"assistant"' not in line and '"type": "assistant"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("type") != "assistant" or row.get("isSidechain"):
                    continue
                content = (row.get("message") or {}).get("content")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = "\n".join(x.get("text", "") for x in content
                                     if isinstance(x, dict) and x.get("type") == "text")
                else:
                    continue
                if text.strip():
                    last = text
    except OSError:
        return ""
    return last


def final_text(payload, reader=None):
    """`last_assistant_message` from the Stop payload first (the transcript is written
    asynchronously and can lag the current turn), the transcript only when it is absent."""
    msg = (payload or {}).get("last_assistant_message")
    if isinstance(msg, str) and msg.strip():
        return msg
    return (reader or last_assistant_text)((payload or {}).get("transcript_path") or "")
