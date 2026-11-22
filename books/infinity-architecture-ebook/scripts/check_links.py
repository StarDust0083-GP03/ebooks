#!/usr/bin/env python3
"""Check local links, duplicate IDs in book.html, and referenced exports in the built wiki."""

from __future__ import annotations

import collections
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
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
    bad: list[tuple[Path, str]] = []
    for page in PAGES:
        if not page.exists():
            bad.append((page, "<missing page>"))
            continue
        html_text = page.read_text(encoding="utf-8")
        for link in LINK.findall(html_text):
            if not target_ok(page, link):
                bad.append((page.relative_to(ROOT), link))
        if page.name == "book.html":
            ids = ID_RE.findall(html_text)
            dups = [k for k, v in collections.Counter(ids).items() if v > 1]
            if dups:
                bad.append((page.relative_to(ROOT), f"duplicate IDs: {dups[:5]}"))

    pdf_path = ROOT / "exports" / "infinity-architecture.pdf"
    epub_path = ROOT / "exports" / "infinity-architecture.epub"
    for label, path in (("pdf", pdf_path), ("epub", epub_path)):
        if not path.exists() or path.stat().st_size == 0:
            bad.append((Path("exports"), f"missing or empty {label}"))

    if epub_path.exists():
        with zipfile.ZipFile(epub_path) as z:
            opf = ET.fromstring(z.read("OEBPS/content.opf"))
            ns = {"opf": "http://www.idpf.org/2007/opf"}
            for item in opf.findall(".//opf:manifest/opf:item", ns):
                href = item.attrib.get("href", "")
                if href.endswith(".xhtml") and href not in ("nav.xhtml", "cover.xhtml"):
                    data = z.read(f"OEBPS/{href}").decode("utf-8")
                    has_svg = "<svg " in data
                    props = item.attrib.get("properties", "").split()
                    if has_svg and "svg" not in props:
                        bad.append((Path(href), "contains <svg> without properties=\"svg\""))

    if bad:
        for page, link in bad:
            print(f"BROKEN {page}: {link}")
        print(f"{len(bad)} broken reference(s)")
        return 1
    print(f"OK: {len(PAGES)} pages, zero duplicate IDs in book.html, EPUB SVG properties & all exports verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
