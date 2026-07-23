#!/usr/bin/env python3
"""Shared inbox/session helpers for kindle-agent."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
INBOX = Path(os.environ.get("KINDLE_AGENT_INBOX", ROOT / "inbox")).expanduser()
SESSIONS = INBOX / "sessions"
INDEX = INBOX / "index.json"
INGEST_LOCK = INBOX / ".ingest.lock"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def slug(value: str, fallback: str = "session") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return (cleaned[:64] or fallback)


def session_id_for(agent: str, conversation_id: str) -> str:
    return f"{slug(agent)}-{slug(conversation_id, 'unknown')}"


def session_dir(sid: str) -> Path:
    return SESSIONS / sid


def title_from_cwd(cwd: str) -> str:
    if not cwd:
        return "untitled"
    return Path(cwd.rstrip("/")).name or cwd


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def update_session_meta(
    sid: str,
    *,
    agent: str | None = None,
    conversation_id: str | None = None,
    cwd: str | None = None,
    model: str | None = None,
    title: str | None = None,
    pages: int | None = None,
    unread: bool | None = None,
    touch: bool = True,
) -> dict:
    """Update session meta.

    touch=True (default): bump updated_at. Used when new agent content arrives.
    touch=False: only refresh fields like page count — does not rewrite the
    library timestamp or force unread (full re-renders used to make every row
    show the same HH:MM and *).
    """
    d = session_dir(sid)
    d.mkdir(parents=True, exist_ok=True)
    meta_path = d / "meta.json"
    meta = read_json(meta_path, {})
    if not isinstance(meta, dict):
        meta = {}
    if not meta.get("id"):
        meta["id"] = sid
    if agent is not None:
        meta["agent"] = agent
    if conversation_id is not None:
        meta["conversation_id"] = conversation_id
    if cwd is not None:
        meta["cwd"] = cwd
    if model is not None:
        meta["model"] = model
    if title is not None:
        meta["title"] = title
    elif not meta.get("title"):
        meta["title"] = title_from_cwd(str(meta.get("cwd") or ""))
    if pages is not None:
        meta["pages"] = pages
    if unread is not None:
        meta["unread"] = unread
    if touch or not meta.get("updated_at"):
        meta["updated_at"] = utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
    write_json(meta_path, meta)
    return meta


def _hm_local(iso: str) -> str:
    """Library clock: local HH:MM, not raw UTC from the ISO string."""
    if not iso:
        return ""
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%H:%M")
    except Exception:
        if "T" in iso and len(iso) >= 16:
            return iso[11:16]
        return iso[-5:]


def rebuild_index() -> dict:
    SESSIONS.mkdir(parents=True, exist_ok=True)
    items = []
    for path in SESSIONS.iterdir():
        if not path.is_dir():
            continue
        # skip Finder junk / AppleDouble
        if path.name.startswith("."):
            continue
        meta = read_json(path / "meta.json", None)
        if not isinstance(meta, dict) or not meta.get("id"):
            continue
        manifest = read_json(path / "pages" / "manifest.json", {})
        if isinstance(manifest, dict) and "pages" in manifest:
            meta["pages"] = manifest["pages"]
        items.append(meta)
    items.sort(key=lambda m: m.get("updated_at") or "", reverse=True)
    index = {"updated_at": utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"), "sessions": items}
    write_json(INDEX, index)

    # BusyBox-friendly list: id|agent|title|HH:MM|pages|unread
    lines = []
    for m in items:
        hm = _hm_local(str(m.get("updated_at") or ""))
        title = str(m.get("title") or "untitled").replace("|", "/")
        agent = str(m.get("agent") or "?")
        pages = int(m.get("pages") or 0)
        unread = "1" if m.get("unread") else "0"
        lines.append(
            f"{m.get('id')}|{agent}|{title}|{hm}|{pages}|{unread}"
        )
    (INBOX / "sessions.tsv").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return index


def append_feed(sid: str, entry: str) -> Path:
    d = session_dir(sid)
    d.mkdir(parents=True, exist_ok=True)
    feed = d / "feed.md"
    legacy = INBOX / "feed.md"
    INBOX.mkdir(parents=True, exist_ok=True)

    def _write() -> None:
        with feed.open("a", encoding="utf-8") as f:
            f.write(entry)
        with legacy.open("a", encoding="utf-8") as f:
            f.write(entry)

    # Serialize appends across concurrent end-of-turn hooks.
    if fcntl is None:
        _write()
        return feed
    INGEST_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with INGEST_LOCK.open("a+", encoding="utf-8") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            _write()
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
    return feed


def ingest_agent_message(
    *,
    agent: str,
    text: str,
    conversation_id: str,
    cwd: str = "",
    model: str = "",
    title: str | None = None,
) -> str:
    """Append a clean entry, update session meta/index, return session id."""
    if not text or not str(text).strip():
        return ""
    sid = session_id_for(agent, conversation_id or "unknown")
    now = utc_now()
    local = now.astimezone().strftime("%H:%M")
    body = str(text).rstrip() + "\n"
    entry = f"**{agent}** · {local}\n\n{body}\n---\n\n"
    append_feed(sid, entry)
    update_session_meta(
        sid,
        agent=agent,
        conversation_id=conversation_id or "unknown",
        cwd=cwd,
        model=model,
        title=title,
        unread=True,
        touch=True,
    )
    rebuild_index()
    msg_dir = INBOX / "messages"
    msg_dir.mkdir(parents=True, exist_ok=True)
    (msg_dir / f"{now.strftime('%Y%m%dT%H%M%SZ')}_{slug(sid)}.md").write_text(
        entry, encoding="utf-8"
    )
    return sid


def trigger_push(session_id: str | None = None) -> None:
    """Request a push. Marks session dirty so the pusher drains late turns.

    If a push is already running, only mark dirty — the holder re-checks and
    drains so a 12-turn burst does not spawn 12 SSH round-trips.
    """
    push = ROOT / "scripts" / "push-to-kindle.sh"
    if not push.is_file():
        return
    INBOX.mkdir(parents=True, exist_ok=True)
    dirty = INBOX / ".push-dirty"
    try:
        with dirty.open("a", encoding="utf-8") as f:
            f.write(f"{session_id or '*'}\n")
    except Exception:
        pass
    # Another pusher holds the lock and will drain.
    if (INBOX / ".push.lock.d").exists():
        return
    cmd = [str(push)]
    if session_id:
        cmd.append(session_id)
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
