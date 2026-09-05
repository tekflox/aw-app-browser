#!/bin/bash

set -e

# ── Fix chrome profile directory ownership ────────────────────────────────────
# The profile dir is bind-mounted from the host (./data/chrome-profile).
# It may be owned by the host's ubuntu user (uid=1000) rather than seluser
# (uid=1200). Chromium crashes with SIGTRAP when it can't write its crashpad
# database into the profile dir (Linux kernel blocks userfaultfd for non-root
# unless vm.unprivileged_userfaultfd=1). Ensure seluser owns the directory.
CHROME_PROFILE_DIR="${HOME}/.config/chromium"
if [ -d "${CHROME_PROFILE_DIR}" ]; then
    PROFILE_OWNER=$(stat -c '%u' "${CHROME_PROFILE_DIR}" 2>/dev/null || echo "0")
    MY_UID=$(id -u)
    if [ "${PROFILE_OWNER}" != "${MY_UID}" ]; then
        echo "Fixing chrome profile ownership (${PROFILE_OWNER} → ${MY_UID})..."
        sudo chown -R "${MY_UID}:$(id -g)" "${CHROME_PROFILE_DIR}" 2>/dev/null || true
    fi
fi

# ── Install Microsoft Core Fonts on first run (fingerprint matching) ──────────
# Fonts live in /opt/aw-browser/fonts/ (bind-mounted from ./tools/browser/fonts/).
# No internet required — instant copy on every container recreation.
FONT_DEST="/usr/share/fonts/truetype/msttcorefonts"
if [ ! -f "${FONT_DEST}/Arial.ttf" ]; then
    echo "Installing Microsoft Core Fonts from /opt/aw-browser/fonts/ ..."
    sudo mkdir -p "${FONT_DEST}"
    if [ -d /opt/aw-browser/fonts ] && ls /opt/aw-browser/fonts/*.ttf >/dev/null 2>&1; then
        sudo cp /opt/aw-browser/fonts/*.ttf "${FONT_DEST}/"
        sudo fc-cache -f 2>/dev/null || true
        echo "  $(ls ${FONT_DEST}/*.ttf | wc -l) fonts installed."
    else
        echo "WARNING: /opt/aw-browser/fonts/ not found — fonts unavailable"
    fi
fi

# ── Timezone: match the proxy's exit IP ───────────────────────────────────────
# Intl.DateTimeFormat().resolvedOptions().timeZone against the geolocation of
# the egress IP is a comparison CreepJS and iphey both make directly, and the
# container's UTC default contradicted a US exit IP.
#
# Setting TZ on the Chrome process is the honest fix: the page, its workers and
# Date all agree, with no patched function anywhere for a checker to notice.
# Emulation.setTimezoneOverride would only reach page targets and leave workers
# reporting UTC — a fresh contradiction in place of the old one.
#
# Resolved from the live exit IP rather than hardcoded, so it follows the proxy
# if egress ever moves. Set AW_BROWSER_TIMEZONE to pin it explicitly. If nothing
# resolves we leave the container default rather than guess.
resolve_timezone() {
    if [ -n "${AW_BROWSER_TIMEZONE:-}" ]; then
        echo "${AW_BROWSER_TIMEZONE}"
        return
    fi
    python3 - <<'PY'
import json, os, urllib.request
proxy = "http://%s:9124" % os.environ.get("AW_WORKSPACE_HOST", "127.0.0.1")
opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
for url in ("http://ip-api.com/json/?fields=status,timezone",
            "https://ipwho.is/",
            "https://ipapi.co/json/"):
    try:
        data = json.loads(opener.open(url, timeout=8).read().decode())
    except Exception:
        continue
    tz = data.get("timezone")
    if isinstance(tz, dict):          # ipwho.is nests it as {"id": "..."}
        tz = tz.get("id")
    if tz and os.path.exists("/usr/share/zoneinfo/" + tz):
        print(tz)
        break
PY
}

RESOLVED_TZ="$(resolve_timezone 2>/dev/null || true)"
if [ -n "${RESOLVED_TZ}" ]; then
    export TZ="${RESOLVED_TZ}"
    echo "Timezone: ${TZ} (resolved from the proxy's exit IP)"
else
    echo "Timezone: could not resolve from the exit IP — keeping the container default (${TZ:-UTC})"
fi

export DISPLAY=:99
SCREEN_WIDTH="${SCREEN_WIDTH:-1504}"
SCREEN_HEIGHT="${SCREEN_HEIGHT:-846}"
SCREEN_DEPTH="${SCREEN_DEPTH:-24}"

mkdir -p /tmp/.X11-unix
sudo chmod 1777 /tmp/.X11-unix 2>/dev/null || chmod 1777 /tmp/.X11-unix 2>/dev/null || true
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null || true

echo "Starting Xvfb on :99 (${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH})..."
Xvfb :99 -screen 0 "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH}" -ac +extension GLX +render -noreset &
XVFB_PID=$!
sleep 2

if ! kill -0 $XVFB_PID 2>/dev/null; then
    echo "ERROR: Xvfb failed to start. Retrying without GLX..."
    Xvfb :99 -screen 0 "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH}" -ac -noreset &
    XVFB_PID=$!
    sleep 2
fi

CHROMIUM=""
for bin in /usr/bin/chromium-base /usr/bin/chromium /usr/bin/chromium-browser /usr/lib/chromium/chromium; do
    if [ -x "$bin" ]; then
        CHROMIUM="$bin"
        break
    fi
done
if [ -z "$CHROMIUM" ]; then
    echo "ERROR: Chromium binary not found"
    exit 1
fi
echo "Using Chromium: $CHROMIUM"

CHROME_ARGS=(
    --remote-debugging-address=0.0.0.0
    --remote-debugging-port=9222
    --remote-allow-origins=*
    --no-first-run
    --use-gl=angle
    --use-angle=swiftshader
    --enable-unsafe-swiftshader
    --ignore-gpu-blocklist
    --disable-dev-shm-usage
    # --disable-background-networking removed: it blocked Service Workers (Telegram Web K)
    --disable-component-update
    --disable-extensions
    --disable-default-apps
    --disable-translate
    --disable-sync
    --disable-domain-reliability
    # Force compositing flush before CDP screenshot capture
    --run-all-compositor-stages-before-draw
    --force-device-scale-factor=1
    --force-color-profile=srgb
    --start-maximized
    --window-size="${SCREEN_WIDTH},${SCREEN_HEIGHT}"
    # No --user-agent override. It used to force a macOS UA here while
    # platform-override.py separately forced the same string through
    # Emulation.setUserAgentOverride — two places that had to move together or
    # the UA and the Client Hints would disagree, which is itself a detection
    # signal. Chromium's own Linux UA is already correct and honest, so both
    # overrides are gone and there is nothing left to keep in step.
    --accept-lang=en-US,pt-BR,pt,en
    # Two deployment shapes reach aw-app-proxy differently:
    #  - legacy shared-netns dev (`network_mode: container:aw-sandbox`): this
    #    container shares aw-sandbox's loopback, so 127.0.0.1:9124 reaches
    #    proxy.py directly.
    #  - Tier-2 (podman, own bridge netns): this container is NOT in the
    #    workspace's netns, so 127.0.0.1 is only its own loopback. The
    #    aw-workspace runtime injects AW_WORKSPACE_HOST (the workspace's
    #    name on their shared podman network — see containers.py) so Chrome
    #    can reach the in-process aw-app-proxy there instead.
    # Earlier this used host.docker.internal:9124, which only worked when
    # Docker injected the host-gateway hostname for an own-netns container —
    # under shared netns there's no such /etc/hosts entry, so that always
    # failed with ERR_PROXY_CONNECTION_FAILED.
    --proxy-server="${AW_WORKSPACE_HOST:-127.0.0.1}:9124"
    "--proxy-bypass-list=<-loopback>"
    # --proxy-server only covers Chrome's HTTP(S)/TCP path. WebRTC ICE/STUN
    # candidate gathering runs over the OS network stack directly and ignores
    # it, leaking this container's own direct-to-internet IP alongside the
    # proxied one — a residential-HTTP/datacenter-WebRTC mismatch that reads
    # as a textbook proxy/automation signal. This forces ICE to only gather
    # candidates reachable through the proxied path (so it fails closed
    # instead of leaking a direct host candidate).
    --force-webrtc-ip-handling-policy=disable_non_proxied_udp
)

start_chrome() {
    # Remove stale singleton locks left over from the previous container run.
    # These live in the bind-mounted profile dir and would cause Chrome to refuse
    # to start (or show a "Chrome didn't shut down correctly" prompt).
    rm -f /home/seluser/.config/chromium/SingletonLock \
          /home/seluser/.config/chromium/SingletonSocket \
          /home/seluser/.config/chromium/SingletonCookie 2>/dev/null || true

    echo "Starting Chromium..."
    "$CHROMIUM" "${CHROME_ARGS[@]}" about:blank &
    CHROME_PID=$!

    for i in $(seq 1 30); do
        if curl -sf http://127.0.0.1:9222/json/version > /dev/null 2>&1; then
            echo "CDP ready on port 9222."
            break
        fi
        sleep 1
    done
}

# The override daemon is the ONLY thing keeping pages fingerprint-patched, and
# it used to be launched with a bare `&` from inside start_chrome with nothing
# watching it: if it died while Chrome stayed up, every page silently unpatched
# and nothing anywhere said so — a durable false-green.
#
# Supervise it once, OUTSIDE the Chrome restart loop. It already waits for CDP
# on startup and exits when the CDP session ends, so a plain restart loop rides
# through a Chrome restart on its own. Starting it from start_chrome instead
# would leak a second supervisor on every Chrome restart.
supervise_platform_override() {
    while true; do
        PYTHONUNBUFFERED=1 python3 /opt/aw-browser/platform-override.py
        echo "platform-override daemon exited — restarting in 3s"
        sleep 3
    done
}

# Selenium Grid intentionally disabled — CDP on port 9222 is used directly
# (Selenium adds ~200MB RAM and ~1.5% CPU overhead with no benefit for Playwright/CDP usage)

echo "Starting x11vnc..."
# -defer 50 -wait 50: cap updates to ~20fps, reduces CPU load
# -nowait_bog: skip frames when server is busy instead of blocking
x11vnc -display :99 -forever -shared -nopw -rfbport 5900 -quiet -defer 50 -wait 50 -nowait_bog &
sleep 1

VNC_PORT="${NOVNC_PORT:-7900}"
if [ -d /opt/bin ]; then
    /opt/bin/noVNC/utils/novnc_proxy --vnc localhost:5900 --listen ${VNC_PORT} &
elif command -v websockify &>/dev/null; then
    websockify --web /usr/share/novnc ${VNC_PORT} localhost:5900 &
else
    echo "WARNING: noVNC not available"
fi

echo "Starting CDP proxy (0.0.0.0:9223 → 127.0.0.1:9222)..."
# HTTP-aware (not a raw byte forward) — Chrome's remote-debugging endpoint
# rejects any Host header that isn't localhost/an IP, so a plain TCP relay
# can't be reached by hostname from another container. See cdp_proxy.py's
# module docstring for the full Host-header + response-body rewrite story.
python3 /opt/aw-browser/cdp_proxy.py &

echo ""
echo "======================================"
echo "  Browser container ready (no Selenium)"
echo "  CDP:   http://0.0.0.0:9223 → 127.0.0.1:9222"
echo "  noVNC: http://0.0.0.0:${VNC_PORT}"
echo "======================================"

start_chrome
supervise_platform_override &
while true; do
    wait $CHROME_PID 2>/dev/null || true
    echo "Chrome exited, restarting in 3s..."
    sleep 3
    start_chrome
done
