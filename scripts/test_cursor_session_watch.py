#!/usr/bin/env python3
"""Smoke test Cursor transcript watcher startup and live message ingestion."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WATCHER = ROOT / "scripts" / "cursor_session_watch.py"


def main() -> int:
    session_id = f"cursor-watch-smoke-{os.getpid()}"
    with tempfile.TemporaryDirectory() as temp:
        inbox = Path(temp) / "inbox"
        transcript = Path(temp) / "session.jsonl"
        transcript.write_text("", encoding="utf-8")
        env = {**os.environ, "KINDLE_AGENT_INBOX": str(inbox)}
        proc = subprocess.Popen(
            [sys.executable, str(WATCHER), "--session-id", session_id, "--transcript-path", str(transcript),
             "--idle-seconds", "1", "--no-push"],
            cwd=ROOT,
            env=env,
        )
        time.sleep(0.2)
        transcript.write_text(
            '{"role":"assistant","message":{"content":[{"type":"text","text":"Cursor watcher smoke message"}]}}\n',
            encoding="utf-8",
        )
        time.sleep(0.8)
        feed = inbox / "sessions" / f"cursor-{session_id}" / "feed.md"
        if not feed.exists() or "Cursor watcher smoke message" not in feed.read_text(encoding="utf-8"):
            proc.terminate()
            raise RuntimeError("watcher did not ingest the live Cursor transcript")
        if proc.wait(timeout=3) != 0:
            raise RuntimeError("watcher did not exit cleanly")
    print("PASS: Cursor watcher reads a live transcript and exits after inactivity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
