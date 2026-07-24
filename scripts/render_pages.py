#!/usr/bin/env python3
"""Render agent feed.md into grayscale PNG pages for Kindle Oasis 3 (1264x1680).

Uses mistune (real Markdown parser) → HTML → PyMuPDF Story pagination → PNG.
No hand-rolled Markdown grammar.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import fitz
import mistune
from PIL import Image

from inbox_lib import trim_feed

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "fonts"
DEFAULT_FEED = ROOT / "inbox" / "feed.md"
DEFAULT_OUT = ROOT / "inbox" / "pages"

# Oasis 3 framebuffer (from `fbink -e`)
WIDTH = 1264
HEIGHT = 1680
MARGIN_X = 52
MARGIN_TOP = 44
MARGIN_BOTTOM = 52

# CSS px on a 1264×1680 pt page ≈ screen pixels at 72dpi pixmap
SIZE_BODY = 34
SIZE_CODE = 30
SIZE_H1 = 46
SIZE_H2 = 40
SIZE_H3 = 36
DEFAULT_MAX_PAGES = 15


def max_pages_from_env() -> int:
    """Return the configured page-history limit, with a safe default."""
    raw = os.environ.get("KINDLE_AGENT_MAX_PAGES", str(DEFAULT_MAX_PAGES))
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_PAGES


def _font_face(filename: str, family: str, weight: str | None = None, style: str | None = None) -> str:
    path = FONT_DIR / filename
    if not path.exists():
        return ""
    extras = ""
    if weight:
        extras += f" font-weight: {weight};"
    if style:
        extras += f" font-style: {style};"
    # Paths are resolved via fitz.Archive(FONT_DIR)
    return f"@font-face {{ font-family: '{family}'; src: url('{filename}');{extras} }}"


def md_to_html(md: str) -> str:
    """Parse Markdown with mistune (GFM-ish plugins), wrap in Kindle CSS."""
    render = mistune.create_markdown(
        escape=True,
        plugins=[
            "table",
            "strikethrough",
            "url",
            "task_lists",
            "def_list",
            "abbr",
            "footnotes",
            "mark",
            "insert",
            "superscript",
            "subscript",
        ],
    )
    body = render(md or "")
    faces = "\n".join(
        x
        for x in [
            _font_face("NotoSerif-Regular.ttf", "KindleSerif"),
            _font_face("NotoSerif-Bold.ttf", "KindleSerif", weight="bold"),
            _font_face("NotoSerif-Italic.ttf", "KindleSerif", style="italic"),
            _font_face("DroidSansMono.ttf", "KindleMono"),
        ]
        if x
    )
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
{faces}
html, body {{
  margin: 0;
  padding: 0;
  color: #000;
  background: #fff;
  font-family: 'KindleSerif', serif;
  font-size: {SIZE_BODY}px;
  line-height: 1.4;
}}
h1, h2, h3, h4, h5, h6 {{
  font-family: 'KindleSerif', serif;
  font-weight: bold;
  margin: 0.6em 0 0.35em 0;
  line-height: 1.25;
}}
h1 {{ font-size: {SIZE_H1}px; }}
h2 {{ font-size: {SIZE_H2}px; }}
h3 {{ font-size: {SIZE_H3}px; }}
p, ul, ol, blockquote, pre, table {{
  margin: 0 0 0.55em 0;
}}
ul, ol {{ padding-left: 1.3em; }}
blockquote {{
  margin-left: 0;
  padding-left: 0.8em;
  border-left: 3px solid #999;
  color: #222;
}}
a {{ color: #000; text-decoration: underline; }}
code, pre {{
  font-family: 'KindleMono', monospace;
  font-size: {SIZE_CODE}px;
}}
code {{
  background: transparent;
  padding: 0;
}}
pre {{
  background: transparent;
  border-left: 3px solid #777;
  padding: 0.6em 0.7em;
  white-space: pre-wrap;
  word-break: break-word;
}}
pre code {{
  background: transparent;
  padding: 0;
}}
table {{
  border-collapse: collapse;
  width: 100%;
  font-size: {SIZE_CODE}px;
  font-family: 'KindleMono', monospace;
}}
th, td {{
  border: 1px solid #444;
  padding: 0.35em 0.5em;
  vertical-align: top;
  text-align: left;
}}
th {{
  background: transparent;
  font-weight: bold;
}}
hr {{
  border: none;
  border-top: 1px solid #888;
  margin: 0.8em 0;
}}
img {{
  max-width: 100%;
  height: auto;
}}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def html_to_png_pages(html: str, out_dir: Path, max_pages: int) -> tuple[int, int]:
    """Paginate HTML and write only the newest ``max_pages`` PNG pages.

    Returns ``(pages_written, source_pages)``. Retained pages are renumbered
    from one so the Kindle pager never needs to understand a sliding offset.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("page-*.png"):
        try:
            old.unlink()
        except FileNotFoundError:
            pass

    archive = fitz.Archive(str(FONT_DIR)) if FONT_DIR.is_dir() else None
    story = fitz.Story(html=html, archive=archive) if archive else fitz.Story(html=html)
    page_rect = fitz.Rect(0, 0, WIDTH, HEIGHT)
    where = fitz.Rect(
        MARGIN_X,
        MARGIN_TOP,
        WIDTH - MARGIN_X,
        HEIGHT - MARGIN_BOTTOM,
    )

    with tempfile.TemporaryDirectory(prefix="kindle-agent-pdf-") as tmp:
        pdf_path = Path(tmp) / "feed.pdf"
        writer = fitz.DocumentWriter(str(pdf_path))
        more = True
        pages_written = 0
        while more:
            device = writer.begin_page(page_rect)
            more, _filled = story.place(where)
            story.draw(device)
            writer.end_page()
            pages_written += 1
            # Safety: avoid infinite loop on pathological input
            if pages_written > 500:
                break
        writer.close()

        doc = fitz.open(str(pdf_path))
        source_pages = doc.page_count
        first_page = max(0, source_pages - max_pages)
        retained_pages = source_pages - first_page
        for output_index, source_index in enumerate(range(first_page, source_pages), start=1):
            pix = doc[source_index].get_pixmap(colorspace=fitz.csGRAY, alpha=False)
            img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
            if img.size != (WIDTH, HEIGHT):
                img = img.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
            img.save(out_dir / f"page-{output_index:04d}.png", format="PNG", optimize=True)
        doc.close()
    return retained_pages, source_pages


def render(feed_path: Path, out_dir: Path) -> dict:
    trim_feed(feed_path)
    md = feed_path.read_text(encoding="utf-8") if feed_path.exists() else ""
    html = md_to_html(md)
    max_pages = max_pages_from_env()
    pages, source_pages = html_to_png_pages(html, out_dir, max_pages)
    manifest = {
        "pages": pages,
        "source_pages": source_pages,
        "max_pages": max_pages,
        "width": WIDTH,
        "height": HEIGHT,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "feed_bytes": feed_path.stat().st_size if feed_path.exists() else 0,
        "feed_path": str(feed_path),
        "renderer": "mistune+pymupdf",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    feed = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FEED
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    manifest = render(feed, out)
    print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
