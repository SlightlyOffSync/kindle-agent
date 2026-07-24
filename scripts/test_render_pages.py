#!/usr/bin/env python3
"""Regression test for orphan gray background bands across page breaks."""

from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image

from render_pages import html_to_png_pages, md_to_html


def main() -> int:
    section = """
Paragraph with `inline code` and enough prose to wrap naturally across lines.

```text
fenced code remains visually distinct without a filled background
```

| heading | value |
| --- | --- |
| alpha | beta |

---

"""
    with tempfile.TemporaryDirectory() as temp:
        pages_dir = Path(temp) / "pages"
        pages, source_pages = html_to_png_pages(md_to_html(section * 120), pages_dir, 15)
        if pages != 15 or source_pages <= 15:
            raise RuntimeError("fixture did not exercise retained-page pagination")
        for page in sorted(pages_dir.glob("page-*.png")):
            image = Image.open(page).convert("L")
            band_start = None
            for y in range(image.height):
                row = [image.getpixel((x, y)) for x in range(image.width)]
                light_gray = sum(215 <= pixel <= 240 for pixel in row)
                if light_gray > image.width * 0.7:
                    if band_start is None:
                        band_start = y
                    if y - band_start + 1 >= 5:
                        raise RuntimeError(
                            f"orphan gray band on {page.name} rows {band_start}-{y}"
                        )
                else:
                    band_start = None
    print("PASS: paginated Markdown contains no orphan gray bands")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
