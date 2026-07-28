"""Entrypoint referenced by aw-app.json's runtime.entrypoint
("browser_app.plugin:BrowserAppPlugin").

The AW Browser is a SERVICE, not a CLI install — this app's whole point is
migrating the hard-coded aw-browser container (Chromium + CDP, see
tools/browser/entrypoint-lite.sh) onto the decoupled-apps F4 service
contribution point (``ctx.services``) instead. Flow:

* ``ctx.commands`` (``commands:install``) installs the Chromium package
  itself (journaled; reverted on uninstall via scripts/uninstall.sh).
* ``ctx.services`` (``service:manage``) registers + autostarts a managed
  Chromium subprocess listening for CDP on ``config.port`` (default 9333 —
  deliberately different from the shared aw-browser's 9222/9223 so this app
  can run alongside it without a collision; the two are NOT integrated yet,
  see README).
* ``ctx.routes`` (``routes:register``) exposes ``GET /status`` reporting
  whether the service is up and its CDP endpoint.
"""
from __future__ import annotations

import logging
import os

from . import chrome_cmd

log = logging.getLogger("aw_apps.browser")

SERVICE_ID = "chromium"


def _bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("false", "0", "")


class BrowserAppPlugin:
    async def activate(self, ctx) -> None:
        self.ctx = ctx
        ctx.commands.install_system_cli(
            "chromium", "scripts/install_chromium.sh", uninstall="scripts/uninstall.sh"
        )

        cfg = ctx.config or {}
        self.port = int(cfg.get("port") or chrome_cmd.DEFAULT_PORT)
        headless = _bool(cfg.get("headless"), True)
        width = int(cfg.get("window_width") or chrome_cmd.DEFAULT_WIDTH)
        height = int(cfg.get("window_height") or chrome_cmd.DEFAULT_HEIGHT)

        profile_dir = os.path.join(ctx.package_dir, ".chrome-profile")
        os.makedirs(profile_dir, exist_ok=True)

        binary = chrome_cmd.find_chromium_binary()
        start_cmd = chrome_cmd.build_command(
            binary=binary, port=self.port, profile_dir=profile_dir,
            headless=headless, width=width, height=height,
        )
        ctx.services.register(SERVICE_ID, start=start_cmd, autostart=True)
        log.info("aw-app-browser activated: chromium service on port %s (headless=%s)",
                  self.port, headless)

        ctx.routes.register(self._build_routes(ctx))

    async def deactivate(self) -> None:
        # Service stop + chromium package removal are driven by the
        # framework's journal reverse-replay (service:register + the
        # commands:install revert hook -> scripts/uninstall.sh).
        log.info("aw-app-browser deactivated")

    def _build_routes(self, ctx):
        from fastapi import FastAPI

        api = FastAPI()

        @api.get("/status")
        async def status():
            st = ctx.services.status(SERVICE_ID)
            running = bool(st.get("running"))
            result = {
                "running": running,
                "port": self.port,
                "pid": st.get("pid"),
                "cdp_url": f"http://127.0.0.1:{self.port}" if running else None,
            }
            if running:
                try:
                    import httpx
                    async with httpx.AsyncClient(timeout=2.0) as client:
                        r = await client.get(f"http://127.0.0.1:{self.port}/json/version")
                        r.raise_for_status()
                        result["cdp_version"] = r.json()
                except Exception as e:  # CDP not answering yet, or errored
                    result["cdp_error"] = str(e)
            return result

        @api.post("/start")
        async def start():
            return ctx.services.start(SERVICE_ID)

        @api.post("/stop")
        async def stop():
            return ctx.services.stop(SERVICE_ID)

        return api
