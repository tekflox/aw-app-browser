# Browser — headless Chromium image for AW workspaces. Deliberately NOT the third-party
# ghcr.io/browserless/chromium image: this one carries the container flags,
# the CDP platform-override fingerprint daemon, and the Microsoft-core-font
# set the AW stack depends on, and is wired to tunnel through OUR cookie proxy
# and accept the aw-sync extensions — none of which browserless does.
#
# Published by .github/workflows/build.yml as ghcr.io/tekflox/aw-browser:latest.
FROM debian:bookworm-slim

# Chromium + Xvfb + VNC/noVNC + the minimal deps entrypoint-lite.sh needs.
RUN apt-get update && \
    apt-get install -y --no-install-recommends --fix-missing \
    chromium \
    chromium-sandbox \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    python3 \
    curl \
    procps \
    sudo \
    fontconfig \
    fonts-liberation \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user. Named `seluser` (uid 1200, browserless convention) to
# match the profile paths the entrypoint cleans up
# (/home/seluser/.config/chromium/Singleton*). Passwordless sudo because the
# entrypoint chowns the bind-mounted profile dir and installs fonts at runtime.
RUN groupadd -r -g 1200 seluser && \
    useradd -r -u 1200 -g seluser -m -d /home/seluser -s /bin/bash seluser && \
    echo 'seluser ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/seluser && \
    chmod 0440 /etc/sudoers.d/seluser

# Bake the assets the entrypoint expects under /opt/aw-browser/ so the image is
# self-contained.
COPY fonts/ /opt/aw-browser/fonts/
COPY platform-override.py /opt/aw-browser/platform-override.py

# Map Mac/Windows font names to the Liberation equivalents so
# document.fonts.check('12px Arial') etc. return true.
COPY fonts-aliases.conf /etc/fonts/conf.d/99-aw-aliases.conf

COPY entrypoint-lite.sh /opt/aw-browser/entrypoint-lite.sh
RUN chmod +x /opt/aw-browser/entrypoint-lite.sh

ENV DISPLAY=:99 \
    SCREEN_WIDTH=1504 \
    SCREEN_HEIGHT=846 \
    SCREEN_DEPTH=24 \
    NOVNC_PORT=7900

USER seluser
WORKDIR /home/seluser

# 9223 = CDP proxy (entrypoint republishes 127.0.0.1:9222 → 0.0.0.0:9223);
# 7900 = noVNC.
EXPOSE 9223 7900

ENTRYPOINT ["/opt/aw-browser/entrypoint-lite.sh"]
