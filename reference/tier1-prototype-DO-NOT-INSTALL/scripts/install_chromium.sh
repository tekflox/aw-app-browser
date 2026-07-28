#!/usr/bin/env bash
# Installs Chromium into the workspace via apt. Idempotent — safe to re-run
# (on install, and on every reconcile pass after workspace recreation).
#
# Package name/binary matches tools/browser/Dockerfile's already-proven
# aw-browser container image (debian:bookworm-slim + `apt-get install
# chromium`), so this is known to resolve on Debian/Ubuntu bases.
set -euo pipefail

for bin in chromium chromium-browser; do
  if command -v "$bin" >/dev/null 2>&1; then
    echo "chromium already installed: $("$bin" --version)"
    exit 0
  fi
done

if ! command -v apt-get >/dev/null 2>&1; then
  echo "install_chromium.sh: no apt-get on this system — unsupported base image" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends chromium

for bin in chromium chromium-browser; do
  if command -v "$bin" >/dev/null 2>&1; then
    "$bin" --version
    exit 0
  fi
done

echo "install_chromium.sh: chromium package installed but no chromium/chromium-browser binary found on PATH" >&2
exit 1
