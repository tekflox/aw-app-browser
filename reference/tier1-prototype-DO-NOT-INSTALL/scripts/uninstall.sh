#!/usr/bin/env bash
# Reverses install_chromium.sh. Called on app uninstall (journal replay per
# the ADR's Decision 7 — this script IS the revert action for the
# commands:install journal entry). The service itself is stopped separately
# by the runtime's service:register journal reverse-replay before this runs.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get remove -y --purge chromium chromium-browser || true
apt-get update -qq || true
