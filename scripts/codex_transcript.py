#!/usr/bin/env python3
"""Tail Codex session transcripts for mid-turn assistant commentary.

Codex Stop only fires when a turn ends. Between tools the model often emits
`role=assistant` / `phase=commentary` messages that never hit Stop. PostToolUse
(+ Stop as a final drain) walks the transcript from a byte watermark and ingests
new assistant text into the Kindle inbox.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from inbox_lib import (
    INBOX,
    count_file_lines,
    ingest_agent_message,
    maybe_generate_title,
    rebuild_index,
    session_id_for,
    trigger_push,
    update_session_meta,
)

STATE_DIR = INBOX / ".codex-tail"
MIN_CHARS = 1


def _extract_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    bits: list[str] = []
    for part in content:
        if isinstance(part, str):
            bits.append(part)
            continue
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            bits.append(text)
    return "\n".join(bits).strip()


def _load_state(session_id: str) -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{session_id}.json"
    if not path.exists():
        return {"offset": 0, "seen_ids": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"offset": 0, "seen_ids": []}
    if not isinstance(data, dict):
        return {"offset": 0, "seen_ids": []}
    return {
        "offset": int(data.get("offset") or 0),
        "seen_ids": list(data.get("seen_ids") or [])[-400:],
        "transcript_lines": data.get("transcript_lines"),
    }


def _save_state(session_id: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{session_id}.json"
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _assistant_items_from_chunk(chunk: str) -> list[dict]:
    items: list[dict] = []
    for line in chunk.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("type") != "response_item":
            continue
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("type") != "message" or payload.get("role") != "assistant":
            continue
        text = _extract_text(payload.get("content"))
        if not text or "encrypted_content" in text:
            continue
        if len(text) < MIN_CHARS:
            continue
        msg_id = str(payload.get("id") or "")
        if not msg_id:
            # Stable-ish fallback for id-less rows
            msg_id = "h:" + re.sub(r"\s+", " ", text)[:96]
        items.append(
            {
                "id": msg_id,
                "text": text,
                "phase": payload.get("phase") or "",
            }
        )
    return items


def flush_codex_transcript(
    *,
    session_id: str,
    transcript_path: str | None,
    cwd: str = "",
    model: str = "",
    fallback_text: str | None = None,
    push: bool = True,
) -> int:
    """Ingest new assistant messages since last watermark. Returns count added."""
    if not session_id:
        return 0

    state = _load_state(session_id)
    seen = set(state.get("seen_ids") or [])
    added = 0
    sid = ""

    path = Path(transcript_path) if transcript_path else None
    if path is not None and path.is_file():
        size = path.stat().st_size
        offset = int(state.get("offset") or 0)
        if offset > size:
            offset = 0
        with path.open("r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            chunk = f.read()
            new_offset = f.tell()
        previous_lines = state.get("transcript_lines")
        if not isinstance(previous_lines, int) or offset == 0:
            transcript_lines = count_file_lines(path)
        else:
            transcript_lines = previous_lines + chunk.count("\n")
        state["transcript_lines"] = transcript_lines
        for item in _assistant_items_from_chunk(chunk):
            if item["id"] in seen:
                continue
            sid = ingest_agent_message(
                agent="codex",
                text=item["text"],
                conversation_id=session_id,
                cwd=cwd,
                model=model,
            )
            seen.add(item["id"])
            added += 1
        state["offset"] = new_offset
        sid = sid or session_id_for("codex", session_id)
        update_session_meta(
            sid,
            agent="codex",
            conversation_id=session_id,
            cwd=cwd,
            model=model,
            transcript_lines=transcript_lines,
            touch=False,
        )
        maybe_generate_title(sid, path, transcript_lines)

    # Stop fallback: final message may not yet be in transcript, or turn had no tools.
    if fallback_text and isinstance(fallback_text, str) and fallback_text.strip():
        fb = fallback_text.strip()
        fb_id = "stop:" + re.sub(r"\s+", " ", fb)[:120]
        if fb_id not in seen and not any(
            (s.endswith(fb[-80:]) if len(fb) > 80 else s.endswith(fb))
            for s in list(seen)[-20:]
            if isinstance(s, str)
        ):
            # Also skip if identical to the last ingested body (cheap)
            already = False
            for old in list(seen)[-5:]:
                if isinstance(old, str) and old.startswith("h:") and fb[:90] in old:
                    already = True
                    break
            if not already:
                sid = ingest_agent_message(
                    agent="codex",
                    text=fb,
                    conversation_id=session_id,
                    cwd=cwd,
                    model=model,
                )
                seen.add(fb_id)
                added += 1

    state["seen_ids"] = list(seen)[-400:]
    _save_state(session_id, state)

    if added and push and sid:
        rebuild_index()
        trigger_push(sid)
    elif added and push:
        trigger_push(f"codex-{session_id}")
    return added
