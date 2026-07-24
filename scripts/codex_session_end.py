#!/usr/bin/env python3
"""Codex SessionEnd hook: ask this session's Kindle watcher to exit."""

from __future__ import annotations

import json
import sys

from inbox_lib import INBOX


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    session_id = str(payload.get("session_id") or "")
    if not session_id:
        return 0
    watch_dir = INBOX / ".codex-watch"
    watch_dir.mkdir(parents=True, exist_ok=True)
    (watch_dir / f"{session_id}.stop").touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
