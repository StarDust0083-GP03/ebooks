"""Verify generated HTML links, anchors, single-file book, EPUB 3 SVG properties, PDF, and prose rules."""
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_STORED

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / "wiki"


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
    files = [WIKI / "index.html", WIKI / "search.html", WIKI / "book.html", ROOT / "ebook.html"]
    chapter_htmls = sorted((WIKI / "chapters").glob("*.html"))
    assert len(chapter_htmls) == 19, f"expected 19 chapter htmls, got {len(chapter_htmls)}"
    files.extend(chapter_htmls)

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
            if parsed.fragment and target in pages:
                assert unquote(parsed.fragment) in pages[target].ids, f"missing anchor {value} from {path.name}"

    for single in (WIKI / "book.html", ROOT / "ebook.html"):
        offline = pages[single.resolve()]
        assert not offline.script_sources, f"single-file HTML {single.name} has external scripts"

    for ch in chapter_htmls:
        slug = ch.stem
        md = (ROOT / "book/chapters" / f"{slug}.md").read_text(encoding="utf-8")
        original_urls = set(re.findall(r"\]\((https?://[^)]+)\)", md))
        rendered_urls = set(pages[ch.resolve()].links)
        assert original_urls <= rendered_urls, f"external source URL rewritten in {slug}"

    nb_meta = json.loads((ROOT / "sources/notebooks.json").read_text(encoding="utf-8"))
    assert nb_meta["commit"] == "2fd7bd0ffe5b8520cfd1a6ee75fcb86fc41bf8c6"
    assert len(nb_meta["notebooks"]) == 47
    assert len(nb_meta["runnable_scripts"]) == 21

    diagrams = sorted((WIKI / "assets/diagrams").glob("*.svg"))
    assert len(diagrams) == 6, f"expected 6 SVG diagrams, got {len(diagrams)}"

    for epub_path in (ROOT / "exports/rag-techniques-from-scratch.epub", ROOT / "RAG工程实战.epub"):
        assert epub_path.exists() and epub_path.stat().st_size > 10000
        with ZipFile(epub_path) as z:
            assert z.infolist()[0].filename == "mimetype"
            assert z.infolist()[0].compress_type == ZIP_STORED
            assert z.read("mimetype") == b"application/epub+zip"
            xml_files = {name: ET.fromstring(z.read(name)) for name in z.namelist()
                         if name.endswith((".xhtml", ".opf", ".xml"))}
            names = set(z.namelist())
            ids = {name: {e.attrib["id"] for e in tree.iter() if "id" in e.attrib}
                   for name, tree in xml_files.items()}
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
            assert len(spine) == 20  # cover + 19 chapters
            assert all(n.attrib["idref"] in manifest for n in spine)
            assert any(n.attrib.get("properties") == "nav" for n in manifest_items)
            svg_declared = 0
            for item_id, attrs in manifest.items():
                href = attrs["href"]
                if href.endswith(".xhtml"):
                    raw = z.read(f"OEBPS/{href}").decode("utf-8")
                    if "<svg " in raw:
                        assert "svg" in (attrs.get("properties") or "").split(), f"Missing properties='svg' on {href}"
                        svg_declared += 1
            assert svg_declared == 6, f"expected 6 SVG chapters in EPUB, found {svg_declared}"

    pdf_path = ROOT / "exports/rag-techniques-from-scratch.pdf"
    assert pdf_path.exists() and pdf_path.stat().st_size > 50000, "PDF missing or too small"
    assert pdf_path.read_bytes()[:4] == b"%PDF", "Invalid PDF header"

    prose = "\n".join(
        re.sub(r"```[\s\S]*?```", "", p.read_text(encoding="utf-8"))
        for p in (ROOT / "book/chapters").glob("*.md")
        if p.stem != "appendix-code"
    )
    assert not re.search(r"TODO|TBD|Lorem ipsum", prose), "placeholder in prose"
    assert "\u2014" not in prose and "\u2013" not in prose, "em-dash or en-dash found in prose"

    print(
        f"PASS: {len(files)} HTML pages, 6 SVG diagrams, 47 notebooks + 21 scripts, "
        f"EPUB 3 ({svg_declared} SVG chapters declared), PDF ({pdf_path.stat().st_size} bytes), zero dashes"
    )


if __name__ == "__main__":
    main()
