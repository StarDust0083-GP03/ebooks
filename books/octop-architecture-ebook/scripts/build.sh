#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d wiki/node_modules ]; then
  npm ci --prefix wiki
fi
python3 scripts/make_diagrams.py
node wiki/build.mjs
python3 scripts/make_epub.py

CHROME="${CHROME:-chromium}"
CHROME_TMP="$(mktemp -d "${PWD}/.tmp-chrome.XXXXXX")"
trap 'rm -rf "$CHROME_TMP"' EXIT
XDG_CONFIG_HOME="$CHROME_TMP" XDG_CACHE_HOME="$CHROME_TMP" "$CHROME" \
  --headless=new --disable-gpu --no-sandbox --no-pdf-header-footer \
  --user-data-dir="$CHROME_TMP/profile" \
  --print-to-pdf=exports/octop-architecture.pdf wiki/book.html

mkdir -p wiki/exports
cp exports/octop-architecture.pdf exports/octop-architecture.epub wiki/exports/
python3 scripts/check_links.py
python3 -m unittest discover -s examples -v
echo "OK: site, EPUB, PDF, links, zero duplicate IDs, and 18 teaching tests"
