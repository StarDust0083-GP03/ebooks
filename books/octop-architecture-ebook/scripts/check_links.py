#!/usr/bin/env python3
"""Check local links, referenced assets, staged exports, and duplicate IDs."""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "wiki"
PAGES = [
    WIKI / "index.html",
    WIKI / "search.html",
    WIKI / "book.html",
    *sorted((WIKI / "chapters").glob("*.html")),
]
LINK = re.compile(r'(?:href|src)="([^"]+)"')
ID_ATTR = re.compile(r'\bid="([^"]+)"')


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
    bad: list[tuple[Path, str]] = []
    for page in PAGES:
        if not page.exists():
            bad.append((page, "<missing page>"))
            continue
        html = page.read_text(encoding="utf-8")
        for link in LINK.findall(html):
            if not target_ok(page, link):
                bad.append((page.relative_to(ROOT), link))
        counts = Counter(ID_ATTR.findall(html))
        for elem_id, count in sorted(counts.items()):
            if count > 1:
                bad.append((page.relative_to(ROOT), f"duplicate id={elem_id!r} ({count}x)"))

    for label, path in (
        ("pdf", ROOT / "exports" / "octop-architecture.pdf"),
        ("epub", ROOT / "exports" / "octop-architecture.epub"),
        ("staged pdf", WIKI / "exports" / "octop-architecture.pdf"),
        ("staged epub", WIKI / "exports" / "octop-architecture.epub"),
    ):
        if not path.exists() or path.stat().st_size == 0:
            bad.append((Path("exports"), f"missing or empty {label}: {path.relative_to(ROOT)}"))

    if bad:
        for page, detail in bad:
            print(f"BROKEN {page}: {detail}")
        print(f"{len(bad)} issue(s) found")
        return 1
    print(f"OK: {len(PAGES)} pages, all local links & exports resolve, zero duplicate IDs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
