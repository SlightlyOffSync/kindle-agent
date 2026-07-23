#!/usr/bin/env python3
"""Watch one live Codex session transcript and mirror assistant messages.

Started by the SessionStart hook. Each watcher is keyed by session id and owns
one Codex parent process, so separate CLI agents can run concurrently without
sharing state. It exits when that process exits.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

from codex_transcript import flush_codex_transcript
from inbox_lib import INBOX

DEFAULT_IDLE_SECONDS = 300
POLL_SECONDS = 0.5


def process_is_alive(pid: int) -> bool:
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
    parser.add_argument("--transcript-path", default="")
    parser.add_argument("--cwd", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--idle-seconds", type=int, default=DEFAULT_IDLE_SECONDS)
    parser.add_argument("--no-push", action="store_true", help="Ingest without contacting Kindle")
    args = parser.parse_args()

    watch_dir = INBOX / ".codex-watch"
    watch_dir.mkdir(parents=True, exist_ok=True)
    lock_path = watch_dir / f"{args.session_id}.lock"
    lock = lock_path.open("a+", encoding="utf-8")
    if fcntl is not None:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0

    transcript_path = args.transcript_path
    last_activity = time.monotonic()
    last_size = -1
    try:
        while True:
            path = Path(transcript_path) if transcript_path else None
            if path is not None and path.is_file():
                size = path.stat().st_size
                if size != last_size:
                    last_size = size
                    last_activity = time.monotonic()
            added = flush_codex_transcript(
                session_id=args.session_id,
                transcript_path=transcript_path or None,
                cwd=args.cwd,
                model=args.model,
                push=not args.no_push,
            )
            if added:
                last_activity = time.monotonic()
            if args.parent_pid and not process_is_alive(args.parent_pid):
                return 0
            # A defensive exit for hosts where the hook's parent PID is not
            # the long-lived Codex process. A subsequent SessionStart/restart
            # will create a fresh watcher and continue from the watermark.
            if time.monotonic() - last_activity >= args.idle_seconds:
                return 0
            time.sleep(POLL_SECONDS)
    finally:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
