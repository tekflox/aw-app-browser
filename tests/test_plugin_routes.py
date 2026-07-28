"""End-to-end test of the /status, /start, /stop routes through a real FastAPI
TestClient, with a fake ``ctx`` (commands/services/routes facades — the
pieces BrowserAppPlugin actually touches) and the Chromium binary lookup
monkeypatched (no real Chromium needed).

Run: .venv/aw/bin/python -m pytest tests/test_plugin_routes.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from browser_app import chrome_cmd, plugin  # noqa: E402


class FakeCommands:
    def __init__(self):
        self.installed = []

    def install_system_cli(self, name, installer, uninstall=None):
        self.installed.append((name, installer, uninstall))
        return {"cli": name, "installed": True}


class FakeServices:
    def __init__(self):
        self.registered = None
        self.running = False
        self.pid = None

    def register(self, service_id, start, autostart=False):
        self.registered = {"service_id": service_id, "start": start, "autostart": autostart}
        if autostart:
            self.running = True
            self.pid = 4242
        return {"service": service_id, "registered": True}

    def start(self, service_id):
        self.running = True
        self.pid = 4242
        return self.status(service_id)

    def stop(self, service_id):
        self.running = False
        self.pid = None
        return {"service": service_id, "running": False}

    def status(self, service_id):
        return {"service": service_id, "running": self.running, "pid": self.pid, "autostart": True}


class FakeRoutes:
    def register(self, subapp):
        self.subapp = subapp


class FakeCtx:
    def __init__(self, config=None, package_dir="/tmp"):
        self.commands = FakeCommands()
        self.services = FakeServices()
        self.routes = FakeRoutes()
        self.config = config or {}
        self.package_dir = package_dir


@pytest.fixture(autouse=True)
def fake_binary(monkeypatch):
    monkeypatch.setattr(chrome_cmd, "find_chromium_binary", lambda: "/usr/bin/chromium")


def _activated(config=None, package_dir=None):
    ctx = FakeCtx(config=config or {"port": 9444}, package_dir=package_dir or tempfile.mkdtemp())
    app = plugin.BrowserAppPlugin()
    asyncio.run(app.activate(ctx))
    return app, ctx


def test_activate_installs_chromium_via_commands_facade():
    _app, ctx = _activated()
    assert ctx.commands.installed == [("chromium", "scripts/install_chromium.sh", "scripts/uninstall.sh")]


def test_activate_registers_autostarted_service_on_configured_port():
    app, ctx = _activated(config={"port": 9555})
    assert ctx.services.registered["service_id"] == "chromium"
    assert ctx.services.registered["autostart"] is True
    assert "--remote-debugging-port=9555" in ctx.services.registered["start"]
    assert app.port == 9555


def test_status_route_reports_running_and_cdp_url(monkeypatch):
    app, ctx = _activated(config={"port": 9666})
    api = app._build_routes(ctx)
    tc = TestClient(api)

    async def fake_get(self, url, *a, **kw):
        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"Browser": "Chrome-headless"}

        return R()

    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    resp = tc.get("/status")
    body = resp.json()
    assert body["running"] is True
    assert body["port"] == 9666
    assert body["cdp_url"] == "http://127.0.0.1:9666"
    assert body["cdp_version"] == {"Browser": "Chrome-headless"}


def test_status_route_when_stopped():
    app, ctx = _activated()
    ctx.services.running = False
    ctx.services.pid = None
    api = app._build_routes(ctx)
    tc = TestClient(api)

    resp = tc.get("/status")
    body = resp.json()
    assert body["running"] is False
    assert body["cdp_url"] is None
    assert "cdp_version" not in body


def test_stop_and_start_routes_toggle_service():
    app, ctx = _activated()
    api = app._build_routes(ctx)
    tc = TestClient(api)

    resp = tc.post("/stop")
    assert resp.json()["running"] is False
    assert ctx.services.running is False

    resp = tc.post("/start")
    assert resp.json()["running"] is True
    assert ctx.services.running is True
