#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 scripts/make_diagrams.py
node wiki/build.mjs

mkdir -p exports wiki/exports
TMP_CHROME="$(mktemp -d /tmp/osb-chrome-XXXXXX)"
trap 'rm -rf "$TMP_CHROME"' EXIT

HOME=/tmp XDG_CONFIG_HOME=/tmp/xdg-cfg XDG_CACHE_HOME=/tmp/xdg-cache \
  chromium --headless=new --disable-gpu --no-sandbox \
  --disable-dev-shm-usage --disable-crash-reporter \
  --user-data-dir="$TMP_CHROME" \
  --no-pdf-header-footer \
  --print-to-pdf="$ROOT/exports/opensandbox-architecture.pdf" \
  "file://$ROOT/wiki/book.html"

cp "$ROOT/exports/opensandbox-architecture.pdf" "$ROOT/wiki/exports/opensandbox-architecture.pdf"
cp "$ROOT/exports/opensandbox-architecture.epub" "$ROOT/wiki/exports/opensandbox-architecture.epub"

python3 wiki/verify.py
python3 examples/mini_osb.py
python3 -m unittest discover -s examples -v
