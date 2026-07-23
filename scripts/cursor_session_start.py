#!/usr/bin/env python3
"""Cursor sessionStart hook: start a detached transcript watcher."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "inbox"
WATCHER = ROOT / "scripts" / "cursor_session_watch.py"


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    session_id = str(payload.get("session_id") or payload.get("conversation_id") or "")
    transcript_path = str(payload.get("transcript_path") or "")
    if not session_id or not transcript_path:
        return 0
    roots = payload.get("workspace_roots")
    cwd = str(roots[0]) if isinstance(roots, list) and roots else ""
    watch_dir = INBOX / ".cursor-watch"
    watch_dir.mkdir(parents=True, exist_ok=True)
    with (watch_dir / f"{session_id}.log").open("a", encoding="utf-8") as log:
        subprocess.Popen(
            [sys.executable, str(WATCHER), "--session-id", session_id, "--transcript-path", transcript_path,
             "--cwd", cwd, "--model", str(payload.get("model") or ""), "--parent-pid", str(os.getppid())],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
        )
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
