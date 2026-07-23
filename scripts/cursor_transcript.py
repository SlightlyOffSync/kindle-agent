#!/usr/bin/env python3
"""Tail Cursor session transcripts and ingest assistant text updates."""

from __future__ import annotations

import json
import re
from pathlib import Path

from inbox_lib import INBOX, ingest_agent_message, trigger_push

STATE_DIR = INBOX / ".cursor-tail"


def _load_state(session_id: str) -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{session_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return {"offset": int(data.get("offset") or 0), "seen_ids": list(data.get("seen_ids") or [])[-400:]}


def _save_state(session_id: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{session_id}.json").write_text(
        json.dumps(state, indent=2) + "\n", encoding="utf-8"
    )


def _items(chunk: str) -> list[dict]:
    result: list[dict] = []
    for line in chunk.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("role") != "assistant":
            continue
        message = row.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        text = "\n".join(
            part["text"].strip()
            for part in content
            if isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
            and part["text"].strip()
        ).strip()
        if not text or text == "[REDACTED]":
            continue
        item_id = str(row.get("id") or "")
        if not item_id and isinstance(message, dict):
            item_id = str(message.get("id") or "")
        result.append({"id": item_id or "h:" + re.sub(r"\s+", " ", text)[:96], "text": text})
    return result


def flush_cursor_transcript(*, session_id: str, transcript_path: str, cwd: str = "", model: str = "", push: bool = True) -> int:
    """Ingest assistant text appended to one Cursor transcript."""
    path = Path(transcript_path)
    if not session_id or not path.is_file():
        return 0
    state = _load_state(session_id)
    offset = min(int(state["offset"]), path.stat().st_size)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(offset)
        chunk = handle.read()
        state["offset"] = handle.tell()
    seen = set(state["seen_ids"])
    added = 0
    sid = ""
    for item in _items(chunk):
        if item["id"] in seen:
            continue
        sid = ingest_agent_message(agent="cursor", text=item["text"], conversation_id=session_id, cwd=cwd, model=model)
        seen.add(item["id"])
        added += 1
    state["seen_ids"] = list(seen)[-400:]
    _save_state(session_id, state)
    if added and push:
        trigger_push(sid or f"cursor-{session_id}")
    return added
