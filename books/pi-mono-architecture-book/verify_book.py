"""本地出版物与教学模型结构检查脚本。"""
from pathlib import Path
from html.parser import HTMLParser
from collections import Counter
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).parent
versions = json.loads((ROOT / 'sources/versions.json').read_text())
VERSION = versions['target_version']


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.links, self.images = [], [], []
        self.chapters = self.svg = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if 'id' in a:
            self.ids.append(a['id'])
            if tag == 'h1' and a['id'].startswith('chapter-'):
                self.chapters += 1
        if tag == 'a' and 'href' in a:
            self.links.append(a['href'])
        if tag == 'img':
            self.images.append(a.get('src', ''))
        if tag == 'svg':
            self.svg += 1


html_text = (ROOT / 'index.html').read_text()
page = Page()
page.feed(html_text)
dups = [k for k, v in Counter(page.ids).items() if v > 1]
assert not dups, f'duplicate IDs in index.html: {dups}'
assert page.chapters == 12, f'expected 12 chapters, got {page.chapters}'
assert page.svg == 13, f'expected 13 inline SVGs, got {page.svg}'
for href in page.links:
    if href.startswith('#'):
        assert href[1:] in page.ids, f'broken anchor: {href}'
    elif not re.match(r'https?://', href):
        assert not href.startswith('/'), f'non-relative local link: {href}'
        assert (ROOT / href.split('#')[0]).is_file(), f'missing local link target: {href}'
assert '{{' not in html_text

# Check SVGs
ns = {'s': 'http://www.w3.org/2000/svg'}
svg_list = sorted((ROOT / 'diagrams').glob('*.svg'))
assert len(svg_list) == 13, f'expected 13 SVG files, got {len(svg_list)}'
for svg in svg_list:
    raw_svg = svg.read_text()
    assert '\u2014' not in raw_svg and '\u2013' not in raw_svg, f'em/en-dash in {svg.name}'
    root = ET.parse(svg).getroot()
    assert root.get('role') == 'img', svg.name
    assert root[0].tag.endswith('title'), svg.name
    assert root.find('s:desc', ns) is not None, svg.name
    ids = {el.get('id') for el in root.iter() if el.get('id')}
    assert all(x in ids for x in root.get('aria-labelledby', '').split()), svg.name

# Check EPUB 3 structure, XML validity, and properties="svg"
with zipfile.ZipFile(ROOT / 'pi-architecture.zh-CN.epub') as z:
    assert z.namelist()[0] == 'mimetype'
    assert z.getinfo('mimetype').compress_type == zipfile.ZIP_STORED
    assert z.read('mimetype') == b'application/epub+zip'
    for name in z.namelist():
        if name.endswith(('.xml', '.xhtml', '.opf', '.svg')):
            ET.fromstring(z.read(name))
    opf = ET.fromstring(z.read('OEBPS/content.opf'))
    pns = {'p': 'http://www.idpf.org/2007/opf'}
    items = {i.get('id'): i for i in opf.findall('p:manifest/p:item', pns)}
    for item in items.values():
        href = item.get('href')
        full = 'OEBPS/' + href
        assert full in z.namelist(), full
        if item.get('media-type') == 'application/xhtml+xml':
            body_text = z.read(full).decode('utf-8')
            props = set((item.get('properties') or '').split())
            if '<svg ' in body_text:
                assert 'svg' in props, f'{href} embeds <svg> without properties="svg"'
    for item in opf.findall('p:spine/p:itemref', pns):
        assert item.get('idref') in items
    x = ET.fromstring(z.read('OEBPS/book.xhtml'))
    book_id_list = [el.get('id') for el in x.iter() if el.get('id')]
    epub_dups = [k for k, v in Counter(book_id_list).items() if v > 1]
    assert not epub_dups, f'duplicate IDs in EPUB book.xhtml: {epub_dups}'
    bookids = set(book_id_list)
    for el in x.iter():
        href = el.get('href', '')
        if href.startswith('#'):
            assert href[1:] in bookids, f'broken EPUB anchor: {href}'
        src = el.get('src', '')
        if src:
            assert 'OEBPS/' + src in z.namelist(), f'missing EPUB asset: {src}'
    nav = ET.fromstring(z.read('OEBPS/nav.xhtml'))
    for el in nav.iter():
        href = el.get('href', '')
        if href.startswith('book.xhtml#'):
            assert href.split('#')[1] in bookids, f'broken EPUB nav link: {href}'
    assert f'v{VERSION}' in z.read('OEBPS/content.opf').decode('utf-8')
    book_xhtml = z.read('OEBPS/book.xhtml').decode('utf-8')
    for phrase in ('PI_CODEMODE_MAX_WORKERS', 'StreamingJsonParser', 'readonly string[]', 'pi-schema'):
        assert phrase in book_xhtml, f'missing in EPUB: {phrase}'

# Check v1.6.0 content & zero em/en-dash in prose chapters and README
for md_path in [*sorted((ROOT / 'chapters').glob('*.md')), ROOT / 'README.md', ROOT / 'OUTLINE.md']:
    txt = md_path.read_text()
    assert '\u2014' not in txt, f'em-dash found in {md_path.name}'
    assert '\u2013' not in txt, f'en-dash found in {md_path.name}'

assert f'本版核查版本 v{VERSION}' in html_text
assert f'https://github.com/earendil-works/pi/tree/v{VERSION}' in html_text
for phrase in (
    'PI_CODEMODE_MAX_WORKERS',
    'reserveTokens',
    'images: [photo]',
    'StreamingJsonParser',
    'readonly string[]',
    'PI_SELF_UPDATE_COMMAND',
    'gpt-6.2-sol',
    'pi-schema',
    's40',
):
    assert phrase in html_text, f'missing v1.6.0 material: {phrase}'

source_md = (ROOT / 'chapters/03-contracts-and-integration.md').read_text()
script = (ROOT / 'examples/scripts/06-classify-image.js').read_text().split('\n', 1)[1].strip()
assert script in source_md, 'classifier code in the chapter differs from the runnable example'

# Run both Python unit tests and Node checks
subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT / 'examples'), '-v'], check=True)
subprocess.run(['node', str(ROOT / 'examples/run-checks.mjs')], check=True)

# Check PDF header and non-empty size
pdf_path = ROOT / 'pi-architecture.zh-CN.pdf'
pdf_bytes = pdf_path.read_bytes()
assert pdf_bytes.startswith(b'%PDF-'), 'invalid PDF header'
assert len(pdf_bytes) > 100_000, f'PDF unexpectedly small: {len(pdf_bytes)} bytes'

print('PASS: 12 chapters, 13 accessible SVGs, zero em/en-dashes, unique IDs, relative links, EPUB 3 XML + properties="svg", 20 Python tests, 6 Node scripts, valid PDF.')
