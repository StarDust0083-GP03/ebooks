#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d "wiki/node_modules" ]; then
  npm ci --prefix wiki
fi
python3 scripts/make_diagrams.py
node wiki/build.mjs
python3 scripts/make_epub.py
CHROME="${CHROME:-chromium}"
CHROME_TMP="$(mktemp -d "${PWD}/.chrome-tmp.XXXXXX")"
trap 'rm -rf "$CHROME_TMP"' EXIT
HOME="$CHROME_TMP" XDG_CONFIG_HOME="$CHROME_TMP" XDG_CACHE_HOME="$CHROME_TMP" \
  "$CHROME" --headless=new --disable-gpu --no-sandbox --no-pdf-header-footer \
  --user-data-dir="$CHROME_TMP/profile" \
  --print-to-pdf=exports/mcp-2027-architecture.pdf wiki/book.html
rm -rf "$CHROME_TMP"
mkdir -p wiki/exports
cp exports/mcp-2027-architecture.pdf exports/mcp-2027-architecture.epub wiki/exports/
python3 scripts/check_links.py
python3 -m unittest discover -s examples -v
echo "OK: site, EPUB, PDF, links, and example tests"
