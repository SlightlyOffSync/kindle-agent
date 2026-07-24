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

import fcntl

ROOT = Path(__file__).resolve().parents[1]
START_HOOK = ROOT / "scripts" / "codex_session_start.py"
END_HOOK = ROOT / "scripts" / "codex_session_end.py"


def lock_is_held(path: Path) -> bool:
    with path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return False


def wait_for_lock(path: Path, *, held: bool) -> bool:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if lock_is_held(path) is held:
            return True
        time.sleep(0.1)
    return False


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        inbox = Path(temp) / "inbox"
        transcript = Path(temp) / "session.jsonl"
        transcript.write_text("", encoding="utf-8")
        env = {
            **os.environ,
            "KINDLE_AGENT_INBOX": str(inbox),
            "KINDLE_AGENT_NO_PUSH": "1",
            "KINDLE_AGENT_TITLE_ENABLED": "0",
        }
        session_id = "watcher-smoke"
        payload = {
            "session_id": session_id,
            "transcript_path": str(transcript),
            "cwd": str(ROOT),
            "model": "smoke",
            "source": "resume",
        }
        subprocess.run(
            [sys.executable, str(START_HOOK)],
            cwd=ROOT,
            env=env,
            input=json.dumps(payload),
            text=True,
            check=True,
        )
        # The short-lived SessionStart command has exited. Its detached watcher
        # must remain alive and ingest content written afterward.
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
        feed = inbox / "sessions" / "codex-watcher-smoke" / "feed.md"
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if feed.exists() and "Watcher smoke message." in feed.read_text(encoding="utf-8"):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("watcher did not ingest the live Codex transcript")
        meta_path = inbox / "sessions" / "codex-watcher-smoke" / "meta.json"
        deadline = time.monotonic() + 2
        transcript_lines = 0
        while time.monotonic() < deadline:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            transcript_lines = int(meta.get("transcript_lines") or 0)
            if transcript_lines == 1:
                break
            time.sleep(0.05)
        if transcript_lines != 1:
            raise RuntimeError("Codex transcript line count was not recorded")

        subprocess.run(
            [sys.executable, str(END_HOOK)],
            cwd=ROOT,
            env=env,
            input=json.dumps({"session_id": session_id}),
            text=True,
            check=True,
        )
        lock = inbox / ".codex-watch" / f"{session_id}.lock"
        if not wait_for_lock(lock, held=False):
            raise RuntimeError("watcher did not exit after SessionEnd")

        # UserPromptSubmit uses the same starter to re-arm an idle/ended
        # session. It must clear the prior stop signal and acquire a new lock.
        subprocess.run(
            [sys.executable, str(START_HOOK)],
            cwd=ROOT,
            env=env,
            input=json.dumps(payload),
            text=True,
            check=True,
        )
        if not wait_for_lock(lock, held=True):
            log = inbox / ".codex-watch" / f"{session_id}.log"
            details = log.read_text(encoding="utf-8") if log.exists() else "(no log)"
            stop_exists = (inbox / ".codex-watch" / f"{session_id}.stop").exists()
            raise RuntimeError(
                f"UserPromptSubmit did not re-arm the watcher; "
                f"stop_exists={stop_exists}; log={details!r}"
            )
        subprocess.run(
            [sys.executable, str(END_HOOK)],
            cwd=ROOT,
            env=env,
            input=json.dumps({"session_id": session_id}),
            text=True,
            check=True,
        )
        if not wait_for_lock(lock, held=False):
            raise RuntimeError("re-armed watcher did not exit after SessionEnd")
    print("PASS: resume lifecycle survives hook exit, stops, and re-arms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
