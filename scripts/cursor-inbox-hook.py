#!/usr/bin/env python3
"""Project copy of the Cursor afterAgentResponse hook (same as ~/.cursor/hooks/)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from inbox_lib import INBOX, ingest_agent_message, trigger_push, utc_now  # noqa: E402

HOOK_LOG = INBOX / ".hook.log"


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        print("{}")
        return 0

    try:
        INBOX.mkdir(parents=True, exist_ok=True)
        with HOOK_LOG.open("a", encoding="utf-8") as log:
            text_preview = payload.get("text")
            text_len = len(text_preview) if isinstance(text_preview, str) else -1
            log.write(
                f"{utc_now().isoformat()} invoked keys={sorted(payload.keys())} text_len={text_len}\n"
            )
    except Exception:
        pass

    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        print("{}")
        return 0

    try:
        conversation_id = str(payload.get("conversation_id") or "unknown")
        model = str(payload.get("model") or "")
        cwd = ""
        roots = payload.get("workspace_roots")
        if isinstance(roots, list) and roots:
            cwd = str(roots[0])

        sid = ingest_agent_message(
            agent="cursor",
            text=text,
            conversation_id=conversation_id,
            cwd=cwd,
            model=model,
        )
        if sid:
            trigger_push(sid)
    except Exception:
        pass

    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
