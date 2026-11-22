"""Check generated HTML links, anchors, SVGs, PDF, source inventory and EPUB 3 packaging."""
from html.parser import HTMLParser
import base64
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_STORED

ROOT = Path(__file__).resolve().parents[1]


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.links = [], []
        self.script_sources = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        for attr in ("href", "src"):
            if attr in attrs:
                self.links.append(attrs[attr])
        if tag == "script" and "src" in attrs:
            self.script_sources.append(attrs["src"])


def main():
    entries = json.loads((ROOT / "wiki/epub-input.json").read_text(encoding="utf-8"))["chapters"]
    expected = len(entries)

    files = [ROOT / "wiki/index.html", ROOT / "wiki/search.html", ROOT / "wiki/book.html"]
    files.extend(sorted((ROOT / "wiki/chapters").glob("*.html")))
    pages = {}
    for path in files:
        page = Page()
        page.feed(path.read_text(encoding="utf-8"))
        assert len(page.ids) == len(set(page.ids)), f"duplicate ID in {path.name}"
        pages[path.resolve()] = page
    for path, page in pages.items():
        for value in page.links:
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc:
                continue
            target = (path.parent / unquote(parsed.path)).resolve() if parsed.path else path
            assert target.exists(), f"missing target {value} from {path.name}"
            if parsed.fragment and target in pages and parsed.fragment != "resume":
                assert unquote(parsed.fragment) in pages[target].ids, f"missing anchor {value} from {path.name}"

    offline = pages[(ROOT / "wiki/book.html").resolve()]
    assert not offline.script_sources, "single-file HTML has external scripts"
    diagram_payloads = {p.read_bytes() for p in (ROOT / "book/diagrams").glob("*.svg")}
    assert len(diagram_payloads) >= 6, f"expected >= 6 diagrams, got {len(diagram_payloads)}"
    embedded = [
        base64.b64decode(link.split(",", 1)[1], validate=True)
        for link in offline.links
        if link.startswith("data:image/svg+xml;base64,")
    ]
    assert set(embedded) == diagram_payloads, "offline diagrams differ from source SVGs"
    for payload in embedded:
        tree = ET.fromstring(payload)
        svg_ids = {e.attrib["id"] for e in tree.iter() if "id" in e.attrib}
        refs = [
            ref
            for e in tree.iter()
            for value in e.attrib.values()
            for ref in re.findall(r'url\(\s*[\"\x27]?#([^\s)\"\x27]+)', value)
        ]
        assert set(refs) <= svg_ids, f"broken SVG marker references: {set(refs) - svg_ids}"

    for entry in entries:
        md = (ROOT / "book/chapters" / (entry["slug"] + ".md")).read_text(encoding="utf-8")
        original_urls = set(re.findall(r"\]\((https?://[^)]+)\)", md))
        rendered = pages[(ROOT / "wiki/chapters" / (entry["slug"] + ".html")).resolve()].links
        rendered_urls = {x for x in rendered if x.startswith("http")}
        assert original_urls <= rendered_urls, f"external source URL rewritten in {entry['slug']}"

    inventory = json.loads((ROOT / "sources/inventory.json").read_text(encoding="utf-8"))
    assert inventory.get("tag") == "release-1.3.0-rc.2", "unexpected inventory tag"
    assert len(inventory["files"]) >= 90, "source inventory too small"
    for relative, meta in inventory["files"].items():
        assert meta["sha256"] and meta["url"].startswith("https://github.com/"), relative
        assert meta["lines"] > 0, relative

    pdf_path = ROOT / "exports/opensandbox-architecture.pdf"
    wiki_pdf = ROOT / "wiki/exports/opensandbox-architecture.pdf"
    wiki_epub = ROOT / "wiki/exports/opensandbox-architecture.epub"
    assert pdf_path.exists() and pdf_path.stat().st_size > 50_000, "missing or empty PDF export"
    assert wiki_pdf.exists() and wiki_pdf.stat().st_size == pdf_path.stat().st_size, "wiki/exports PDF out of sync"
    assert wiki_epub.exists() and wiki_epub.stat().st_size > 10_000, "wiki/exports EPUB missing"

    with ZipFile(ROOT / "exports/opensandbox-architecture.epub") as z:
        assert z.infolist()[0].filename == "mimetype"
        assert z.infolist()[0].compress_type == ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"
        xml_files = {
            name: ET.fromstring(z.read(name))
            for name in z.namelist()
            if name.endswith((".xhtml", ".opf", ".xml", ".svg"))
        }
        names = set(z.namelist())
        ids = {
            name: {e.attrib["id"] for e in tree.iter() if "id" in e.attrib}
            for name, tree in xml_files.items()
        }
        for name, tree in xml_files.items():
            for element in tree.iter():
                for attr in ("href", "src", "full-path"):
                    value = element.attrib.get(attr)
                    if not value:
                        continue
                    link = urlsplit(value)
                    if link.scheme or link.netloc:
                        continue
                    base = PurePosixPath(name).parent if attr != "full-path" else PurePosixPath()
                    target = str(base / unquote(link.path)) if link.path else name
                    assert target in names, f"EPUB missing resource {value} from {name}"
                    if link.fragment:
                        assert unquote(link.fragment) in ids.get(target, set()), f"EPUB missing anchor {value}"
        ns = {"opf": "http://www.idpf.org/2007/opf"}
        opf = xml_files["OEBPS/content.opf"]
        manifest_items = opf.findall("opf:manifest/opf:item", ns)
        manifest = {n.attrib["id"]: n.attrib for n in manifest_items}
        spine = opf.findall("opf:spine/opf:itemref", ns)
        assert len(spine) == expected
        assert all(n.attrib["idref"] in manifest for n in spine)
        assert any(n.attrib.get("properties") == "nav" for n in manifest_items)
        svg_declared = 0
        for item in manifest_items:
            href = item.attrib["href"]
            if not href.endswith(".xhtml") or href == "nav.xhtml":
                continue
            raw_xhtml = z.read(f"OEBPS/{href}").decode("utf-8")
            has_inline_svg = "<svg " in raw_xhtml or ":svg " in raw_xhtml
            props = set(item.attrib.get("properties", "").split())
            if has_inline_svg:
                assert "svg" in props, f"EPUB chapter {href} has inline SVG without properties='svg'"
                svg_declared += 1
        assert svg_declared >= 4, f"expected >= 4 SVG chapters declared in EPUB, got {svg_declared}"

    prose_parts = []
    for p in sorted((ROOT / "book/chapters").glob("*.md")):
        if p.stem == "appendix-code":
            continue
        in_fence = False
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if not in_fence:
                assert "\u2014" not in line and "\u2013" not in line, f"dash found in {p.name}: {line}"
                prose_parts.append(line)
    prose = "\n".join(prose_parts)
    assert not re.search(r"TODO|TBD|Lorem ipsum", prose), "placeholder in prose"
    print(
        f"PASS: {len(files)} HTML pages, {len(diagram_payloads)} SVGs, "
        f"{len(inventory['files'])} source entries ({inventory['tag']}), "
        f"{expected} EPUB spine chapters ({svg_declared} with properties='svg'), "
        f"PDF ({pdf_path.stat().st_size // 1024} KB), zero em/en dashes"
    )


if __name__ == "__main__":
    main()
