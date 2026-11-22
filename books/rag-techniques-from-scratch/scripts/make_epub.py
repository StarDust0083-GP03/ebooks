#!/usr/bin/env python3
"""Build a valid EPUB 3 from the already-rendered chapter HTML."""

from __future__ import annotations

import html
import re
import shutil
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "wiki"
BOOK = ROOT / "book"
OUT = ROOT / "exports" / "rag-techniques-from-scratch.epub"
LEGACY_OUT = ROOT / "RAG工程实战.epub"
TITLE = "RAG 工程实战：从文档问答到可验证的检索系统"
AUTHOR = "独立源码研究"
UID = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, "rag-techniques-from-scratch-2026"))


def sections() -> list[tuple[str, str, str]]:
    summary = (BOOK / "SUMMARY.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\d+\. \[([^\]]+)\]\(chapters/([^)]+)\)", summary, re.M)
    rows += re.findall(r"^- \[([^\]]+)\]\(chapters/([^)]+)\)", summary, re.M)
    out = []
    for i, (title, filename) in enumerate(rows, 1):
        slug = filename[:-3]
        out.append((f"ch{i:02d}", title.replace("`", ""), slug))
    return out


ALLOWED = set("""p br strong em code pre ul ol li blockquote h1 h2 h3 h4 h5 h6 a
table thead tbody tr th td div span hr img section figure figcaption details summary
header svg g path rect line circle ellipse polygon polyline text tspan marker defs title""".split())


def clean(body: str, slug_to_cid: dict[str, str]) -> str:
    body = re.sub(r'<nav class="chapter-nav"[\s\S]*?</nav>', "", body)

    # Rewrite intra-chapter .html links to EPUB chXX.xhtml filenames
    def fix_href(m: re.Match[str]) -> str:
        target = m.group(1)
        frag = m.group(2) or ""
        if target in slug_to_cid:
            return f'href="{slug_to_cid[target]}.xhtml{frag}"'
        return m.group(0)

    body = re.sub(r'href="([a-z0-9-]+)\.html(#[^"]*)?"', fix_href, body)

    def tag(match: re.Match[str]) -> str:
        name = match.group(1).lower()
        if name not in ALLOWED:
            return html.escape(match.group(0))
        return match.group(0)

    body = re.sub(r"</?([A-Za-z][A-Za-z0-9:-]*)(?:\s[^>]*)?/?>", tag, body)
    body = re.sub(r"<br\s*>", "<br/>", body)
    body = re.sub(r"<(hr|img)([^>/]*?)\s*>", r"<\1\2/>", body)
    return body


def xhtml(title: str, body: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN" lang="zh-CN">
<head><meta charset="utf-8"/><title>{html.escape(title)}</title>
<link rel="stylesheet" type="text/css" href="style.css"/></head>
<body>{body}</body></html>"""


def main() -> None:
    css = (WIKI / "assets" / "reader.css").read_text(encoding="utf-8")
    items = sections()
    slug_to_cid = {slug: cid for cid, _, slug in items}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    spine = []
    nav_items = []
    files: dict[str, str] = {}
    svg_chapters = 0
    for cid, title, slug in items:
        source = (WIKI / "chapters" / f"{slug}.html").read_text(encoding="utf-8")
        match = re.search(r'<article class="chapter">([\s\S]*?)</article>', source)
        body = clean(match.group(1) if match else source, slug_to_cid)
        doc = xhtml(title, body)
        ET.fromstring(doc)  # Fail fast if any chapter is not well-formed XHTML
        files[f"{cid}.xhtml"] = doc
        has_svg = "<svg " in body
        if has_svg:
            svg_chapters += 1
        properties = ' properties="svg"' if has_svg else ""
        manifest.append(f'<item id="{cid}" href="{cid}.xhtml" media-type="application/xhtml+xml"{properties}/>')
        spine.append(f'<itemref idref="{cid}"/>')
        nav_items.append(f'<li><a href="{cid}.xhtml">{html.escape(title)}</a></li>')

    nav_doc = xhtml("目录", f'<nav epub:type="toc" id="toc"><h1>目录</h1><ol>{"".join(nav_items)}</ol></nav>')
    nav_doc = nav_doc.replace(
        '<html xmlns="http://www.w3.org/1999/xhtml"',
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"',
        1,
    )
    ET.fromstring(nav_doc)
    files["nav.xhtml"] = nav_doc

    cover_doc = xhtml(
        "封面",
        '<section class="volume-cover"><p>源码驱动的 RAG 工程实战 · 2026-11 重编版</p>'
        '<h1>RAG 工程实战</h1><h2>从文档问答到可验证的检索系统</h2>'
        '<p>独立源码研究 · NirDiamant/RAG_Techniques 2fd7bd0ffe5b（2026-11-02）· 2026-11-22</p></section>',
    )
    ET.fromstring(cover_doc)
    files["cover.xhtml"] = cover_doc
    files["style.css"] = css

    manifest += [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>',
        '<item id="css" href="style.css" media-type="text/css"/>',
    ]
    spine = ['<itemref idref="cover"/>'] + spine

    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="uid">{UID}</dc:identifier>
<dc:title>{html.escape(TITLE)}</dc:title>
<dc:creator>{AUTHOR}</dc:creator>
<dc:language>zh-CN</dc:language>
<dc:date>2026-11-22</dc:date>
<meta property="dcterms:modified">2026-11-22T00:00:00Z</meta>
</metadata>
<manifest>
{"".join(manifest)}
</manifest>
<spine>
{"".join(spine)}
</spine>
</package>"""
    ET.fromstring(opf)

    container = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

    with zipfile.ZipFile(OUT, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        for name, data in files.items():
            z.writestr(f"OEBPS/{name}", data, compress_type=zipfile.ZIP_DEFLATED)

    shutil.copy2(OUT, LEGACY_OUT)
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size} bytes, {len(items)} chapters, {svg_chapters} SVG chapters)")


if __name__ == "__main__":
    main()
