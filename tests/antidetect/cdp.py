#!/usr/bin/env python3
"""Raw CDP client for the anti-detection battery.

Deliberately NOT the devctl / mini-browser / playwright MCP tools: their
screenshot tools return a path inside *their own* container
(`/tmp/devctl/shot-*.png`), which does not exist on the workspace filesystem,
so evidence captured through them cannot be attached to anything. This client
talks to `aw-app-browser:9223` (container/cdp_proxy.py's host-rewriting proxy)
and decodes `Page.captureScreenshot`'s base64 itself.

Only stdlib + `websockets` — the same dependency devctl's own cdp.py uses, so
nothing new has to be installed to run the battery.
"""
from __future__ import annotations

import asyncio
import base64
import json
import socket
import urllib.request

CDP_HOST = "aw-app-browser"
CDP_PORT = 9223

# Resolve the container name once and reuse the address. Podman's embedded DNS
# intermittently returns "Name or service not known" for a container that is up
# the whole time, and a battery that takes minutes hits that often enough to
# ruin a run — two baselines died this way. aw-app-devctl's own cdp.py resolves
# to an IP for the same reason. The CDP proxy rewrites the Host header, so an
# IP works exactly as well as the name here.
_CACHED_IP: str | None = None


def _host() -> str:
    global _CACHED_IP
    if _CACHED_IP:
        return _CACHED_IP
    last = None
    for _ in range(5):
        try:
            _CACHED_IP = socket.gethostbyname(CDP_HOST)
            return _CACHED_IP
        except Exception as e:      # transient podman DNS
            last = e
    raise RuntimeError(f"cannot resolve {CDP_HOST}: {last}")


def endpoint() -> str:
    return f"{_host()}:{CDP_PORT}"


def http_json(path: str, method: str = "GET", timeout: float = 10.0):
    """Call the CDP HTTP endpoint. Returns parsed JSON, or raw text."""
    req = urllib.request.Request(
        f"http://{endpoint()}{path}", method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode()
    return json.loads(body) if body.strip().startswith(("{", "[")) else body


def list_targets():
    return http_json("/json")


def new_target_via_http(url: str = "about:blank"):
    """Create a page target exactly the way aw-app-devctl and
    aw-app-mini-browser do — `/json/new?<url>` on the CDP HTTP endpoint
    (devctl_app/cdp.py:95, mini_browser_app/cdp.py:95). Used by the daemon
    coverage check, which has to exercise the CREATION path those two use,
    not just a target they reuse."""
    try:
        return http_json(f"/json/new?{url}", method="PUT")
    except Exception:
        # Chrome < 111 only accepted GET here.
        return http_json(f"/json/new?{url}")


def close_target(target_id: str) -> None:
    try:
        http_json(f"/json/close/{target_id}")
    except Exception:
        pass


class CDPSession:
    """One WebSocket to one CDP target (page-level, or browser-level).

    Async context manager: `async with CDPSession(ws_url) as s: ...`. Closing
    it detaches completely — which is what the detached side-channel
    measurement in sidechannel.py relies on.
    """

    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self._ws = None
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self._events: list[dict] = []
        self._waiters: list[tuple[str, asyncio.Future]] = []

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def connect(self) -> None:
        try:
            from websockets.asyncio.client import connect
        except ImportError:  # websockets < 14
            from websockets import connect  # type: ignore
        # max_size=None: a full-page screenshot's base64 blows past the 1 MiB
        # default frame limit and the connection would just die.
        self._ws = await connect(self.ws_url, max_size=None, ping_interval=None)
        self._reader = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
            self._reader = None
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        fut.set_result(msg)
                elif "method" in msg:
                    self._events.append(msg)
                    for method, fut in list(self._waiters):
                        if method == msg["method"] and not fut.done():
                            fut.set_result(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def send(self, method: str, params: dict | None = None,
                   timeout: float = 30.0) -> dict:
        self._id += 1
        cmd_id = self._id
        fut = asyncio.get_event_loop().create_future()
        self._pending[cmd_id] = fut
        await self._ws.send(json.dumps(
            {"id": cmd_id, "method": method, "params": params or {}}))
        try:
            msg = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            raise RuntimeError(f"CDP timeout: {method}")
        if "error" in msg:
            raise RuntimeError(f"CDP error on {method}: {msg['error']}")
        return msg.get("result", {})

    def events(self, method: str) -> list[dict]:
        return [e for e in self._events if e["method"] == method]

    async def wait_for(self, method: str, timeout: float) -> dict | None:
        for e in self._events:
            if e["method"] == method:
                return e
        fut = asyncio.get_event_loop().create_future()
        self._waiters.append((method, fut))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._waiters = [w for w in self._waiters if w[1] is not fut]

    # ── page helpers ────────────────────────────────────────────────────────

    async def navigate(self, url: str, load_timeout: float = 45.0,
                       settle: float = 2.0) -> dict:
        """Navigate and report how it went.

        Returns {status, error, url}. `status` is the main-document HTTP
        status when we saw one — that is what lets the battery tell a detector
        that is DOWN (502) apart from one that FAILED us, which the three-way
        verdict in sites.py depends on.

        Careful: we enable Page and Network but never Runtime or Log. Enabling
        Runtime here would create the very CDP console side-channel that
        sidechannel.py is trying to measure.
        """
        self._events.clear()
        await self.send("Page.enable")
        await self.send("Network.enable")
        await self.send("Page.navigate", {"url": url})
        await self.wait_for("Page.loadEventFired", timeout=load_timeout)
        await asyncio.sleep(settle)

        status, error = None, None
        for e in self.events("Network.responseReceived"):
            p = e.get("params", {})
            if p.get("type") == "Document":
                status = p.get("response", {}).get("status")
                break
        for e in self.events("Network.loadingFailed"):
            if e.get("params", {}).get("type") == "Document":
                error = e["params"].get("errorText")
                break
        try:
            final = await self.evaluate("location.href")
        except Exception:
            final = None
        return {"status": status, "error": error, "url": final}

    async def evaluate(self, expression: str, await_promise: bool = False,
                       timeout: float = 30.0):
        r = await self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
            "allowUnsafeEvalBlockedByCSP": True,
        }, timeout=timeout)
        if r.get("exceptionDetails"):
            desc = r["exceptionDetails"].get("exception", {}).get(
                "description", str(r["exceptionDetails"]))
            raise RuntimeError(f"JS exception: {desc}")
        return r.get("result", {}).get("value")

    async def screenshot(self, path) -> str | None:
        """Capture a PNG and write the decoded bytes to `path` ourselves."""
        try:
            r = await self.send("Page.captureScreenshot",
                                {"format": "png"}, timeout=60.0)
        except Exception:
            return None
        data = r.get("data")
        if not data:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))
        return str(path)


def _rewrite_ws(ws: str) -> str:
    """Point a webSocketDebuggerUrl at the resolved IP, whatever host Chrome or
    the proxy put in it."""
    ep = endpoint()
    for stale in (f"localhost:{CDP_PORT}", f"127.0.0.1:{CDP_PORT}",
                  f"{CDP_HOST}:{CDP_PORT}", "localhost:9222", "127.0.0.1:9222"):
        ws = ws.replace(stale, ep)
    return ws


async def open_page(url: str = "about:blank") -> tuple[CDPSession, str]:
    """Create a fresh page target and attach to it. Returns (session, id)."""
    t = new_target_via_http(url)
    ws = t.get("webSocketDebuggerUrl")
    if not ws:
        raise RuntimeError(f"no webSocketDebuggerUrl in {t}")
    # Chrome hands back its own view of the host; the cdp_proxy rewrites it to
    # the external host:port, but be defensive if that ever regresses.
    ws = _rewrite_ws(ws)
    s = CDPSession(ws)
    await s.connect()
    return s, t["id"]
