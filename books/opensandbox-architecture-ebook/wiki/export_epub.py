"""Package rendered chapters as EPUB 3 with inline SVG and properties='svg'."""
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import uuid
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED

ROOT = Path(__file__).resolve().parents[1]
XHTML = "http://www.w3.org/1999/xhtml"
EPUB = "http://www.idpf.org/2007/ops"
SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", XHTML)
ET.register_namespace("epub", EPUB)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class Fragment(HTMLParser):
    def __init__(self, diagram_dir: Path):
        super().__init__(convert_charrefs=True)
        self.diagram_dir = diagram_dir
        self.root = ET.Element(f"{{{XHTML}}}div")
        self.stack = [self.root]
        self.has_svg = False

    def handle_starttag(self, tag, attrs):
        attr_map = {k: v or k for k, v in attrs}
        if tag == "img" and "src" in attr_map and attr_map["src"].endswith(".svg"):
            svg_name = Path(attr_map["src"]).name
            svg_path = self.diagram_dir / svg_name
            if svg_path.exists():
                ET.SubElement(self.stack[-1], f"{{{XHTML}}}span", {"data-inline-svg": svg_name})
                self.has_svg = True
                return
        node = ET.SubElement(self.stack[-1], f"{{{XHTML}}}{tag}", attr_map)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        expected = f"{{{XHTML}}}{tag}"
        if len(self.stack) > 1 and self.stack[-1].tag == expected:
            self.stack.pop()
        elif tag not in VOID:
            raise ValueError(f"unbalanced HTML: {tag}")

    def handle_data(self, data):
        node = self.stack[-1]
        if len(node):
            node[-1].tail = (node[-1].tail or "") + data
        else:
            node.text = (node.text or "") + data


def xhtml(title: str, fragment: str, diagram_dir: Path) -> tuple[bytes, bool]:
    parser = Fragment(diagram_dir)
    parser.feed(fragment)
    if len(parser.stack) != 1:
        raise ValueError("unclosed HTML element")
    page = ET.Element(f"{{{XHTML}}}html", {"lang": "zh-CN", "{http://www.w3.org/XML/1998/namespace}lang": "zh-CN"})
    head = ET.SubElement(page, f"{{{XHTML}}}head")
    ET.SubElement(head, f"{{{XHTML}}}title").text = title
    ET.SubElement(head, f"{{{XHTML}}}meta", {"charset": "utf-8"})
    ET.SubElement(head, f"{{{XHTML}}}link", {"rel": "stylesheet", "href": "book.css", "type": "text/css"})
    body = ET.SubElement(page, f"{{{XHTML}}}body")
    body.append(parser.root)
    raw = ET.tostring(page, encoding="utf-8", xml_declaration=True).decode("utf-8")
    if parser.has_svg:
        def _inject(match):
            svg_text = (diagram_dir / match.group(1)).read_text(encoding="utf-8").strip()
            return svg_text
        raw = re.sub(r'<span data-inline-svg="([^"]+)"\s*/>', _inject, raw)
    return raw.encode("utf-8"), parser.has_svg


def main():
    payload = json.loads((ROOT / "wiki/epub-input.json").read_text(encoding="utf-8"))
    chapters = payload["chapters"]
    slugs = {c["slug"] for c in chapters}
    diagram_dir = ROOT / "wiki/assets/diagrams"

    def links(match):
        base, sep, fragment = match[1].partition("#")
        if base.endswith(".html") and base.removesuffix(".html") in slugs:
            return 'href="' + base[:-5] + '.xhtml' + (sep + fragment if sep else '') + '"'
        return match[0]

    rendered = []
    for c in chapters:
        html = re.sub(r'href="([^"]+)"', links, c["html"])
        data, has_svg = xhtml(c["title"], html, diagram_dir)
        rendered.append((c, data, has_svg))

    toc = ''.join(f'<li><a href="{c["slug"]}.xhtml">{escape(c["title"])}</a></li>' for c in chapters)
    nav, _ = xhtml("全书目录", f'<nav epub:type="toc" id="toc"><h1>全书目录</h1><ol>{toc}</ol></nav>', diagram_dir)
    nav = nav.replace(b'<html xmlns="', b'<html xmlns:epub="http://www.idpf.org/2007/ops" xmlns="', 1)

    uid = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, "opensandbox-architecture-book-release-1.3.0-rc.2-45043e7f0df2"))
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest_items = []
    svg_chapter_count = 0
    for i, (c, _data, has_svg) in enumerate(rendered):
        prop_attr = ' properties="svg"' if has_svg else ""
        if has_svg:
            svg_chapter_count += 1
        manifest_items.append(
            f'<item id="c{i}" href="{c["slug"]}.xhtml"{prop_attr} media-type="application/xhtml+xml"/>'
        )
    manifest = "".join(manifest_items)
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))

    opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="book-id">{uid}</dc:identifier>
<dc:title>{escape(payload['title'])}：{escape(payload['subtitle'])}</dc:title>
<dc:language>zh-CN</dc:language><dc:description>基于 opensandbox-group/OpenSandbox release-1.3.0-rc.2（45043e7f0df2）源码与官方文档独立编写的中文架构电子书。</dc:description>
<meta property="dcterms:modified">{modified}</meta></metadata>
<manifest><item id="nav" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/><item id="css" href="book.css" media-type="text/css"/>{manifest}</manifest>
<spine>{spine}</spine></package>'''

    css = (
        'body{font-family:serif;line-height:1.75;margin:1em}'
        'h1{font-size:1.65em}h2{font-size:1.3em;margin-top:1.8em}h3{font-size:1.1em}'
        'pre{font-size:.8em;white-space:pre-wrap;overflow-wrap:anywhere;border:1px solid #ccc;padding:.7em}'
        'code{font-family:monospace}img,svg{max-width:100%;height:auto;display:block}'
        'figure.diagram{margin:1.4em 0}figcaption{font-size:.85em;color:#555;margin-top:.5em}'
        'table{border-collapse:collapse;font-size:.85em}td,th{border-bottom:1px solid #ccc;padding:.4em;text-align:left}'
        'a{color:#29564c}blockquote{margin-left:1em;border-left:2px solid #ccc;padding-left:1em}'
    )
    container = (
        '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    (ROOT / "exports").mkdir(exist_ok=True)
    path = ROOT / "exports/opensandbox-architecture.epub"
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/book.css", css)
        z.writestr("OEBPS/nav.xhtml", nav)
        for c, data, _has_svg in rendered:
            z.writestr(f'OEBPS/{c["slug"]}.xhtml', data)
    print(f"EPUB: {path.name}, {len(chapters)} chapters, {svg_chapter_count} inline-SVG chapters")


if __name__ == "__main__":
    main()
