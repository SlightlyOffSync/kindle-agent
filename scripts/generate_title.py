#!/usr/bin/env python3
"""Generate a short initial or periodically refreshed Kindle library title."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from inbox_lib import (
    read_json,
    rebuild_index,
    session_dir,
    trigger_push,
    update_session_meta,
)

DEFAULT_MODEL = "openai-codex/gpt-5.6-luna"
MAX_SOURCE_LINES = 300
MAX_EXCERPT_CHARS = 8_000


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)


def _useful(text: str) -> bool:
    rejected_prefixes = (
        "<environment_context>",
        "<permissions instructions>",
        "The following is the Codex agent history",
        "The following is the Codex agent history added",
    )
    return bool(text) and not text.startswith(rejected_prefixes)


def _tail_lines(path: Path, limit: int) -> list[str]:
    """Read the last JSONL rows without scanning the whole transcript."""
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        data = b""
        while position > 0 and data.count(b"\n") <= limit:
            size = min(64 * 1024, position)
            position -= size
            handle.seek(position)
            data = handle.read(size) + data
    return data.decode("utf-8", errors="replace").splitlines()[-limit:]


def transcript_excerpt(path: Path, *, tail: bool = False) -> str:
    """Extract only user/assistant conversation text from bounded JSONL rows."""
    messages: list[str] = []
    if tail:
        raw_limit = os.environ.get("KINDLE_AGENT_TITLE_TAIL_LINES", str(MAX_SOURCE_LINES))
        try:
            line_limit = max(50, int(raw_limit))
        except ValueError:
            line_limit = MAX_SOURCE_LINES
        source_lines = _tail_lines(path, line_limit)
    else:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            source_lines = []
            for line_number, line in enumerate(handle):
                if line_number >= MAX_SOURCE_LINES:
                    break
                source_lines.append(line)
    for line in source_lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            payload = row

        text = ""
        role = ""
        if payload.get("type") == "thread_goal_updated":
            goal = payload.get("goal")
            if isinstance(goal, dict):
                text = str(goal.get("objective") or "")
                role = "user"
        elif payload.get("type") == "user_message":
            text = str(payload.get("message") or "")
            role = "user"
        elif payload.get("type") == "message":
            role = str(payload.get("role") or row.get("role") or "")
            text = _content_text(payload.get("content"))
            if not text:
                message = payload.get("message")
                if isinstance(message, dict):
                    text = _content_text(message.get("content"))
        elif row.get("role") in {"user", "assistant"}:
            role = str(row.get("role"))
            message = row.get("message")
            if isinstance(message, dict):
                text = _content_text(message.get("content"))

        if role not in {"user", "assistant"} or not _useful(text):
            continue
        messages.append(f"{role}: {text}")
        if sum(len(item) for item in messages) >= MAX_EXCERPT_CHARS:
            break
    return "\n\n".join(messages)[:MAX_EXCERPT_CHARS]


def clean_title(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    title = lines[-1] if lines else ""
    title = re.sub(r"^[\s#>*_`\"']+", "", title)
    title = re.sub(r"[\s#>*_`\"']+$", "", title)
    title = re.sub(r"\b(?:codex|cursor|claude|opencode|pi)\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title)
    return title[:64].rstrip(" .,:;-")


def main() -> int:
    if len(sys.argv) != 5:
        return 2
    sid = sys.argv[1]
    transcript = Path(sys.argv[2])
    transcript_lines = int(sys.argv[3])
    mode = sys.argv[4]
    meta = read_json(session_dir(sid) / "meta.json", {})
    if not isinstance(meta, dict) or not transcript.is_file():
        return 0
    excerpt = transcript_excerpt(transcript, tail=mode == "refresh")
    if not excerpt:
        update_session_meta(sid, title_status="failed", touch=False)
        return 0

    prompt = (
        "Create an updated concise title for this coding-agent conversation. "
        "Output exactly one plain-text title, 3 to 8 words, at most 52 characters. "
        "Describe the current task direction, especially the recent messages; "
        "do not mention Codex, Cursor, agents, chats, or folders.\n\n"
        f"{excerpt}"
    )
    model = os.environ.get("KINDLE_AGENT_TITLE_MODEL", DEFAULT_MODEL)
    try:
        proc = subprocess.run(
            [
                "pi",
                "--model",
                model,
                "--thinking",
                "off",
                "--no-tools",
                "--no-session",
                "--no-context-files",
                "--no-extensions",
                "--no-skills",
                "-p",
            ],
            capture_output=True,
            input=prompt,
            text=True,
            timeout=90,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        proc = None

    title = clean_title(proc.stdout) if proc is not None and proc.returncode == 0 else ""
    if title:
        update_session_meta(
            sid,
            title=title,
            title_status="generated",
            title_at_lines=transcript_lines,
            touch=False,
        )
        rebuild_index()
        trigger_push(sid)
    else:
        update_session_meta(sid, title_status="failed", touch=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
