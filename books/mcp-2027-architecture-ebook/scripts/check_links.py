#!/usr/bin/env python3
"""Check local links, duplicate IDs in book.html, and referenced assets in the built wiki."""

from __future__ import annotations

import collections
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "wiki"
PAGES = [WIKI / "index.html", WIKI / "search.html", WIKI / "book.html", *sorted((WIKI / "chapters").glob("*.html"))]
LINK = re.compile(r'(?:href|src)="([^"]+)"')
ID_RE = re.compile(r'\bid="([^"]+)"')


def target_ok(page: Path, link: str) -> bool:
    parts = urlsplit(link)
    if parts.scheme or link.startswith("#") or link.startswith("//"):
        return True
    raw = unquote(parts.path)
    if not raw:
        return True
    target = (page.parent / raw).resolve()
    try:
        target.relative_to(ROOT)
    except ValueError:
        return False
    return target.exists()


def main() -> int:
    bad = []
    for page in PAGES:
        if not page.exists():
            bad.append((page, "<missing page>"))
            continue
        content = page.read_text(encoding="utf-8")
        for link in LINK.findall(content):
            if not target_ok(page, link):
                bad.append((page.relative_to(ROOT), link))

    # Check duplicate IDs in offline single-file book.html
    book_html = (WIKI / "book.html").read_text(encoding="utf-8")
    counts = collections.Counter(ID_RE.findall(book_html))
    dups = [k for k, v in counts.items() if v > 1]
    for d in dups:
        bad.append((Path("wiki/book.html"), f"duplicate id={d!r}"))

    for label, path in (
        ("pdf", ROOT / "exports" / "mcp-2027-architecture.pdf"),
        ("epub", ROOT / "exports" / "mcp-2027-architecture.epub"),
        ("wiki pdf", WIKI / "exports" / "mcp-2027-architecture.pdf"),
        ("wiki epub", WIKI / "exports" / "mcp-2027-architecture.epub"),
    ):
        if not path.exists() or path.stat().st_size == 0:
            bad.append((Path("exports"), f"missing or empty {label}"))

    if bad:
        for page, link in bad:
            print(f"BROKEN {page}: {link}")
        print(f"{len(bad)} broken reference(s)")
        return 1
    print(f"OK: {len(PAGES)} pages, zero duplicate IDs in book.html, all local links and exports resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
