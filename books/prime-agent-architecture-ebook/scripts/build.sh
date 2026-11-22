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
TMP_UDD="$(mktemp -d)"
trap 'rm -rf "$TMP_UDD"' EXIT
mkdir -p "$TMP_UDD/config" "$TMP_UDD/cache" "$TMP_UDD/data"
HOME="$TMP_UDD" XDG_CONFIG_HOME="$TMP_UDD/config" XDG_CACHE_HOME="$TMP_UDD/cache" \
  "$CHROME" --headless=new --disable-gpu --no-sandbox --disable-dev-shm-usage \
  --disable-crash-reporter --no-pdf-header-footer \
  --user-data-dir="$TMP_UDD/data" \
  --print-to-pdf=exports/prime-agent-architecture.pdf wiki/book.html
mkdir -p wiki/exports
cp exports/prime-agent-architecture.pdf exports/prime-agent-architecture.epub wiki/exports/
python3 scripts/check_links.py
python3 -m unittest discover -s examples -v
echo "OK: site, EPUB, PDF, links, and example tests"
