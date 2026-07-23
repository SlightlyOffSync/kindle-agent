#!/usr/bin/env python3
"""Watch one Cursor session transcript until its parent process exits."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

from cursor_transcript import flush_cursor_transcript
from inbox_lib import INBOX


def alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--transcript-path", required=True)
    parser.add_argument("--cwd", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--idle-seconds", type=int, default=300)
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()
    watch_dir = INBOX / ".cursor-watch"
    watch_dir.mkdir(parents=True, exist_ok=True)
    lock_path = watch_dir / f"{args.session_id}.lock"
    lock = lock_path.open("a+", encoding="utf-8")
    if fcntl is not None:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
    last_activity, last_size = time.monotonic(), -1
    try:
        while True:
            path = Path(args.transcript_path)
            if path.is_file() and path.stat().st_size != last_size:
                last_size, last_activity = path.stat().st_size, time.monotonic()
            if flush_cursor_transcript(session_id=args.session_id, transcript_path=args.transcript_path, cwd=args.cwd, model=args.model, push=not args.no_push):
                last_activity = time.monotonic()
            if args.parent_pid and not alive(args.parent_pid):
                return 0
            if time.monotonic() - last_activity >= args.idle_seconds:
                return 0
            time.sleep(0.5)
    finally:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
        lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
