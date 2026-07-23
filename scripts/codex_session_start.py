#!/usr/bin/env python3
"""Codex SessionStart hook: spawn one detached Kindle transcript watcher."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WATCHER = ROOT / "scripts" / "codex_session_watch.py"
INBOX = ROOT / "inbox"


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
    log_path = watch_dir / f"{session_id}.log"
    command = [
        sys.executable,
        str(WATCHER),
        "--session-id",
        session_id,
        "--transcript-path",
        str(payload.get("transcript_path") or ""),
        "--cwd",
        str(payload.get("cwd") or ""),
        "--model",
        str(payload.get("model") or ""),
        "--parent-pid",
        str(int(payload.get("parent_pid") or os.getppid())),
    ]
    with log_path.open("a", encoding="utf-8") as log:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
