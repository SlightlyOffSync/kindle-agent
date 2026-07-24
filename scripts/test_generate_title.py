#!/usr/bin/env python3
"""Smoke test title excerpt sanitization without calling a model."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from generate_title import clean_title, transcript_excerpt


def main() -> int:
    rows = [
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "PRIVATE SYSTEM TEXT"}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Build a Kindle transcript reader"}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "output": "PRIVATE TOOL OUTPUT",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "I will improve the library metadata."}],
            },
        },
    ]
    rows.extend(
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": f"padding {index}"}],
            },
        }
        for index in range(320)
    )
    rows.append(
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Refresh session titles periodically"}],
            },
        }
    )
    with tempfile.TemporaryDirectory() as temp:
        transcript = Path(temp) / "session.jsonl"
        transcript.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
        excerpt = transcript_excerpt(transcript)
        tail_excerpt = transcript_excerpt(transcript, tail=True)
    if "Kindle transcript reader" not in excerpt or "library metadata" not in excerpt:
        raise RuntimeError("conversation text was not extracted")
    if "PRIVATE SYSTEM TEXT" in excerpt or "PRIVATE TOOL OUTPUT" in excerpt:
        raise RuntimeError("private non-conversation payload leaked into title excerpt")
    if "Refresh session titles periodically" not in tail_excerpt:
        raise RuntimeError("recent conversation text was not extracted from transcript tail")
    if "Build a Kindle transcript reader" in tail_excerpt:
        raise RuntimeError("tail title excerpt included stale early conversation text")
    if clean_title('## "Kindle Feed Metadata"') != "Kindle Feed Metadata":
        raise RuntimeError("title cleanup failed")
    if clean_title("Improve Codex Kindle Feed") != "Improve Kindle Feed":
        raise RuntimeError("agent name was not removed from title")
    print("PASS: title excerpts include conversation text only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
