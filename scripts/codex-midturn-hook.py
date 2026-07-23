#!/usr/bin/env python3
"""Codex PostToolUse / Stop hook: capture mid-turn assistant commentary + final reply."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from codex_transcript import flush_codex_transcript  # noqa: E402
from inbox_lib import INBOX, utc_now  # noqa: E402

HOOK_LOG = INBOX / ".codex-hook.log"


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        print("{}")
        return 0

    event = str(payload.get("hook_event_name") or "")
    session_id = str(
        payload.get("session_id")
        or payload.get("thread_id")
        or payload.get("conversation_id")
        or ""
    )
    transcript = payload.get("transcript_path")
    transcript_path = str(transcript) if isinstance(transcript, str) else None
    cwd = str(payload.get("cwd") or "")
    model = str(payload.get("model") or "")
    fallback = payload.get("last_assistant_message")
    fallback_text = fallback if isinstance(fallback, str) else None

    try:
        INBOX.mkdir(parents=True, exist_ok=True)
        with HOOK_LOG.open("a", encoding="utf-8") as log:
            log.write(
                f"{utc_now().isoformat()} event={event} session={session_id[:16]} "
                f"transcript={'yes' if transcript_path else 'no'} "
                f"fallback_len={len(fallback_text) if fallback_text else 0}\n"
            )
    except Exception:
        pass

    try:
        # PostToolUse: only transcript tail (mid-turn commentary).
        # Stop: transcript tail + last_assistant_message fallback.
        n = flush_codex_transcript(
            session_id=session_id or "unknown",
            transcript_path=transcript_path,
            cwd=cwd,
            model=model,
            fallback_text=fallback_text if event == "Stop" else None,
            push=True,
        )
        try:
            with HOOK_LOG.open("a", encoding="utf-8") as log:
                log.write(f"{utc_now().isoformat()} ingested={n} event={event}\n")
        except Exception:
            pass
    except Exception:
        pass

    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
