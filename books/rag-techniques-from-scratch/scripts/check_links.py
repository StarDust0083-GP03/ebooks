#!/usr/bin/env python3
"""Check local links, anchors, duplicate IDs, and staged exports in the built wiki."""

from __future__ import annotations

from html.parser import HTMLParser
import sys
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


class PageInspector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if "id" in d:
            self.ids.append(d["id"])
        for attr in ("href", "src"):
            if attr in d:
                self.links.append(d[attr])


def main() -> int:
    bad: list[tuple[Path, str]] = []
    parsed_pages: dict[Path, PageInspector] = {}
    for page in PAGES:
        if not page.exists():
            bad.append((page, "<missing page>"))
            continue
        insp = PageInspector()
        insp.feed(page.read_text(encoding="utf-8"))
        if len(insp.ids) != len(set(insp.ids)):
            seen = set()
            dups = [i for i in insp.ids if i in seen or seen.add(i)]
            bad.append((page.relative_to(ROOT), f"duplicate IDs: {dups[:5]}"))
        parsed_pages[page.resolve()] = insp

    for page_path, insp in parsed_pages.items():
        for link in insp.links:
            parts = urlsplit(link)
            if parts.scheme or parts.netloc or link.startswith("//"):
                continue
            raw = unquote(parts.path)
            target = (page_path.parent / raw).resolve() if raw else page_path
            try:
                target.relative_to(WIKI.resolve())
            except ValueError:
                bad.append((page_path.relative_to(ROOT), f"link escapes wiki/: {link}"))
                continue
            if not target.exists():
                bad.append((page_path.relative_to(ROOT), f"missing target: {link}"))
                continue
            if parts.fragment and target in parsed_pages:
                frag = unquote(parts.fragment)
                if frag not in parsed_pages[target].ids:
                    bad.append((page_path.relative_to(ROOT), f"missing anchor: {link}"))

    for label, path in (
        ("pdf", ROOT / "exports" / "rag-techniques-from-scratch.pdf"),
        ("epub", ROOT / "exports" / "rag-techniques-from-scratch.epub"),
        ("wiki pdf", WIKI / "exports" / "rag-techniques-from-scratch.pdf"),
        ("wiki epub", WIKI / "exports" / "rag-techniques-from-scratch.epub"),
    ):
        if not path.exists() or path.stat().st_size == 0:
            bad.append((Path("exports"), f"missing or empty {label}: {path.relative_to(ROOT)}"))

    if bad:
        for page, msg in bad:
            print(f"BROKEN {page}: {msg}")
        print(f"{len(bad)} broken check(s)")
        return 1
    print(f"OK: {len(PAGES)} pages, zero duplicate IDs, all wiki-contained links, anchors, and exports resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
