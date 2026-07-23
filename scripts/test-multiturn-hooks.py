#!/usr/bin/env python3
"""Simulate many Codex end-of-turn hooks before a human reads the Kindle."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "codex-stop-hook.py"
SESSION = "multiturn-smoke"
SID = f"codex-{SESSION}"
FEED = ROOT / "inbox" / "sessions" / SID / "feed.md"
MARKER = f"MULTITURN-{int(time.time())}"
N = 12


def fire_turn(i: int) -> None:
    payload = {
        "hook_event_name": "Stop",
        "session_id": SESSION,
        "cwd": str(ROOT),
        "model": "test-multiturn",
        "stop_hook_active": False,
        "last_assistant_message": (
            f"{MARKER} turn {i}/{N}\n\n"
            f"Simulated agent turn {i}. Humans may not read until much later."
        ),
    }
    proc = subprocess.run(
        ["/usr/bin/python3", str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        print("hook failed", i, proc.stderr, file=sys.stderr)


def wait_idle(timeout: float = 120.0) -> None:
    lock = ROOT / "inbox" / ".push.lock.d"
    deadline = time.time() + timeout
    # First wait for any push to start, then for lock to clear.
    time.sleep(0.5)
    while time.time() < deadline:
        if not lock.exists():
            # settle: another hook may still spawn a pusher
            time.sleep(1.0)
            if not lock.exists():
                return
        time.sleep(0.4)
    raise SystemExit("timeout waiting for push drain")


def main() -> int:
    print(f"firing {N} rapid Codex Stop hooks marker={MARKER}")
    t0 = time.time()
    for i in range(1, N + 1):
        fire_turn(i)
        # tight spacing like a fast agent loop
        time.sleep(0.05)
    print(f"hooks returned in {time.time() - t0:.2f}s; waiting for push drain…")
    wait_idle()
    text = FEED.read_text(encoding="utf-8") if FEED.exists() else ""
    found = [i for i in range(1, N + 1) if f"{MARKER} turn {i}/{N}" in text]
    missing = [i for i in range(1, N + 1) if i not in found]
    print(f"local feed bytes={len(text)} found={len(found)}/{N} missing={missing}")
    if missing:
        return 1

    # Verify Kindle copy
    import os

    env_path = ROOT / "config" / "kindle.env"
    env = {}
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    os.environ["SSHPASS"] = env.get("KINDLE_SSH_PASSWORD", "")
    if not os.environ["SSHPASS"]:
        print("skip remote verify: KINDLE_SSH_PASSWORD not set in config/kindle.env")
        return 0
    remote = (
        f"wc -c /mnt/us/agentfeed/sessions/{SID}/feed.md; "
        f"grep -c '{MARKER}' /mnt/us/agentfeed/sessions/{SID}/feed.md; "
        f"cat /mnt/us/agentfeed/sessions/{SID}/pages/manifest.json; "
        f"ls /mnt/us/agentfeed/sessions/{SID}/pages/page-*.png 2>/dev/null | wc -l"
    )
    proc = subprocess.run(
        [
            "sshpass",
            "-e",
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            f"UserKnownHostsFile={ROOT}/config/known_hosts",
            "-p",
            env.get("KINDLE_SSH_PORT", "22"),
            f"{env.get('KINDLE_SSH_USER', 'root')}@{env.get('KINDLE_SSH_HOST')}",
            remote,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    print("kindle:")
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        return 1
    # expect N marker lines (one per turn)
    lines = proc.stdout.strip().splitlines()
    grep_count = int(lines[1]) if len(lines) > 1 else 0
    if grep_count < N:
        print(f"FAIL kindle only has {grep_count}/{N} marker hits", file=sys.stderr)
        return 1
    print("PASS: all turns ingested + pushed before human read")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
