#!/usr/bin/env bash
# Standalone test — no framework runtime required. Run this INSIDE the
# aw-workspace container (as root) to prove Chromium actually installs and
# comes up with a working CDP endpoint.
#
# Usage (from inside the container, with this repo copied in):
#   bash tests/standalone_test.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== install_chromium.sh =="
bash scripts/install_chromium.sh

BIN="$(command -v chromium || command -v chromium-browser)"
echo "== chromium binary: $BIN =="
"$BIN" --version

PORT=9333
PROFILE_DIR="$(mktemp -d)"
echo "== starting chromium headless on port $PORT (profile: $PROFILE_DIR) =="
"$BIN" \
  --headless=new --no-sandbox --disable-dev-shm-usage --disable-gpu \
  --remote-debugging-port="$PORT" --remote-debugging-address=0.0.0.0 \
  --remote-allow-origins='*' --user-data-dir="$PROFILE_DIR" \
  --window-size=1440,900 --no-first-run --disable-extensions \
  --disable-default-apps --disable-sync --disable-translate \
  about:blank &
CHROME_PID=$!

echo "== waiting for CDP endpoint =="
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${PORT}/json/version" > /dev/null 2>&1; then
    echo "CDP ready on port ${PORT}:"
    curl -s "http://127.0.0.1:${PORT}/json/version"
    echo
    kill "$CHROME_PID" 2>/dev/null || true
    echo "OK: chromium installed and CDP endpoint answered"
    exit 0
  fi
  sleep 1
done

echo "FAIL: CDP endpoint did not come up within 30s" >&2
kill "$CHROME_PID" 2>/dev/null || true
exit 1
