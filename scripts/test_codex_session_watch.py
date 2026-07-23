#!/usr/bin/env python3
"""Smoke test Codex session watcher startup and process-scoped shutdown."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WATCHER = ROOT / "scripts" / "codex_session_watch.py"


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        inbox = Path(temp) / "inbox"
        transcript = Path(temp) / "session.jsonl"
        transcript.write_text("", encoding="utf-8")
        env = {**os.environ, "KINDLE_AGENT_INBOX": str(inbox)}
        proc = subprocess.Popen(
            [
                sys.executable,
                str(WATCHER),
                "--session-id",
                "watcher-smoke",
                "--transcript-path",
                str(transcript),
                "--idle-seconds",
                "1",
                "--no-push",
            ],
            cwd=ROOT,
            env=env,
        )
        time.sleep(0.2)
        transcript.write_text(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "id": "watcher-message",
                        "content": [{"text": "Watcher smoke message."}],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        time.sleep(0.4)
        feed = inbox / "sessions" / "codex-watcher-smoke" / "feed.md"
        if not feed.exists() or "Watcher smoke message." not in feed.read_text(encoding="utf-8"):
            proc.terminate()
            raise RuntimeError("watcher did not ingest the live Codex transcript")
        proc.wait(timeout=3)
        if proc.returncode != 0:
            raise SystemExit(f"watcher exited {proc.returncode}")
    print("PASS: watcher reads a live transcript and exits after inactivity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
