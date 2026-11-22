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
TMP_PROF="$(pwd)/.chrome-tmp-$$"
mkdir -p "$TMP_PROF"
trap 'rm -rf "$TMP_PROF"' EXIT
HOME="$TMP_PROF" XDG_CONFIG_HOME="$TMP_PROF" XDG_CACHE_HOME="$TMP_PROF" \
  "$CHROME" --headless=new --disable-gpu --no-sandbox --no-pdf-header-footer \
  --user-data-dir="$TMP_PROF/profile" \
  --print-to-pdf=exports/rag-techniques-from-scratch.pdf wiki/book.html

mkdir -p wiki/exports
cp exports/rag-techniques-from-scratch.pdf exports/rag-techniques-from-scratch.epub wiki/exports/

python3 scripts/check_links.py
python3 wiki/verify.py
python3 -m unittest discover -s examples -v
echo "OK: diagrams, site, book.html, EPUB, PDF, links, verify, and 20 unit tests"
