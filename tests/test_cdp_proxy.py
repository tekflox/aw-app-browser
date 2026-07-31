#!/usr/bin/env python3
"""Unit test for container/cdp_proxy.py — no real Chrome, no container needed.

Runs the proxy against a fake backend standing in for Chrome's
remote-debugging HTTP server and proves two things:

1. A discovery request (GET /json/version) sent with an *external* Host
   header (the one a caller in another container actually uses, e.g.
   "aw-app-browser:9223") is accepted — the proxy rewrites Host to
   "localhost:<port>" before forwarding, since Chrome's real server rejects
   any Host header that isn't localhost/an IP (DNS-rebinding protection).
   The response body's "localhost:<port>" refs (webSocketDebuggerUrl) come
   back rewritten to the external host:port, so a WS client can actually
   reach it.
2. A WebSocket upgrade request with the same external Host header is also
   accepted (same Host rewrite, then upgrade passthrough).

Run: python3 tests/test_cdp_proxy.py
"""
from __future__ import annotations

import asyncio
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "container"))

FAKE_CHROME_PORT = 19222
PROXY_PORT = 19223
EXTERNAL_HOST_PORT = b"aw-app-browser:19223"


async def _fake_chrome(reader, writer):
    """Stands in for Chrome's remote-debugging HTTP server: rejects any Host
    header that isn't localhost, mirrors it back in a discovery response's
    webSocketDebuggerUrl otherwise — same shape as the real thing."""
    data = await reader.read(4096)
    is_upgrade = b"upgrade: websocket" in data.lower()
    if b"Host: localhost:%d" % FAKE_CHROME_PORT not in data:
        body = b"Host header is specified and is not an IP address or localhost."
        writer.write(b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
        await writer.drain()
        writer.close()
        return
    if is_upgrade:
        writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
        await writer.drain()
        writer.close()
        return
    body = b'{"webSocketDebuggerUrl":"ws://localhost:%d/devtools/browser/abc"}' % FAKE_CHROME_PORT
    resp = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body)
    writer.write(resp)
    await writer.drain()
    writer.close()


async def _run() -> None:
    os.environ["AW_CDP_PROXY_PORT"] = str(PROXY_PORT)
    os.environ["AW_CDP_EXTERNAL_HOST_PORT"] = EXTERNAL_HOST_PORT.decode()
    import cdp_proxy
    cdp_proxy.TARGET_PORT = FAKE_CHROME_PORT

    fake_server = await asyncio.start_server(_fake_chrome, "127.0.0.1", FAKE_CHROME_PORT)
    proxy_server = await asyncio.start_server(cdp_proxy.handle, "0.0.0.0", PROXY_PORT)
    async with fake_server, proxy_server:
        asyncio.create_task(fake_server.serve_forever())
        asyncio.create_task(proxy_server.serve_forever())
        await asyncio.sleep(0.1)

        reader, writer = await asyncio.open_connection("127.0.0.1", PROXY_PORT)
        writer.write(b"GET /json/version HTTP/1.1\r\nHost: %s\r\n\r\n" % EXTERNAL_HOST_PORT)
        await writer.drain()
        resp = await reader.read(4096)
        assert b"200 OK" in resp, resp
        assert EXTERNAL_HOST_PORT in resp, resp
        assert (b"localhost:%d" % FAKE_CHROME_PORT) not in resp, resp
        writer.close()
        print("discovery request accepted, response body host rewritten: PASS")

        reader2, writer2 = await asyncio.open_connection("127.0.0.1", PROXY_PORT)
        writer2.write(
            b"GET /devtools/browser/abc HTTP/1.1\r\n"
            b"Host: %s\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 13\r\n\r\n" % EXTERNAL_HOST_PORT
        )
        await writer2.drain()
        resp2 = await reader2.read(4096)
        assert b"101" in resp2, resp2
        writer2.close()
        print("websocket upgrade accepted: PASS")


def test_cdp_proxy_rewrites_host_header_and_response_body():
    # Plain sync wrapper (not `async def test_...`) — the CI installs bare
    # pytest, no pytest-asyncio plugin to await a native async test.
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
    print("ALL PASS")
