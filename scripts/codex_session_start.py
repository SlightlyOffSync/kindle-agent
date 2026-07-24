#!/usr/bin/env python3
"""Codex SessionStart hook: spawn one detached Kindle transcript watcher."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from inbox_lib import INBOX

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
WATCHER = ROOT / "scripts" / "codex_session_watch.py"


def lock_is_held(lock_path: Path) -> bool:
    """Return whether another watcher currently owns this session lock."""
    if fcntl is None:
        return False
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return False


def clear_stop_signal(watch_dir: Path, session_id: str) -> bool:
    """Let a prior SessionEnd watcher release its lock before restarting."""
    stop_path = watch_dir / f"{session_id}.stop"
    lock_path = watch_dir / f"{session_id}.lock"
    if not stop_path.exists():
        return True
    deadline = time.monotonic() + 3
    while lock_is_held(lock_path) and time.monotonic() < deadline:
        time.sleep(0.1)
    if lock_is_held(lock_path):
        return False
    try:
        stop_path.unlink()
    except FileNotFoundError:
        pass
    return True


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
    if not clear_stop_signal(watch_dir, session_id):
        return 0
    if lock_is_held(watch_dir / f"{session_id}.lock"):
        return 0
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
        str(int(payload.get("parent_pid") or 0)),
    ]
    if os.environ.get("KINDLE_AGENT_NO_PUSH") == "1":
        command.append("--no-push")
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
