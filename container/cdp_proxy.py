"""HTTP-aware CDP proxy: 0.0.0.0:9223 -> 127.0.0.1:9222.

Chrome's remote-debugging HTTP endpoints reject any Host header that isn't
localhost/an IP (DNS-rebinding protection) — a plain byte-forwarding TCP
proxy can't satisfy that AND still be reachable by hostname from other
containers (e.g. the MCP Gateway container calling
http://aw-app-browser:9223). This rewrites the inbound Host header to
"localhost:9222" so Chrome accepts the request, then rewrites the JSON
discovery response's "localhost:9222"/"127.0.0.1:9222" refs
(webSocketDebuggerUrl, devtoolsFrontendUrl) to the external host:port
callers actually use, so the follow-up WebSocket connection routes back
through this same proxy instead of trying (and failing) to reach Chrome's
own loopback directly. WebSocket upgrade requests get the same Host
rewrite, then a raw bidirectional splice for the actual framed traffic.
"""
import asyncio
import os

TARGET_HOST = "127.0.0.1"
TARGET_PORT = 9222
LISTEN_PORT = int(os.environ.get("AW_CDP_PROXY_PORT", "9223"))
EXTERNAL_HOST_PORT = os.environ.get("AW_CDP_EXTERNAL_HOST_PORT", "aw-app-browser:9223").encode()


async def _read_head(reader):
    """Read until the blank line that ends an HTTP head, splitting head/body."""
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = await reader.read(4096)
        if not chunk:
            return None, b""
        head += chunk
        if len(head) > 65536:
            break
    idx = head.index(b"\r\n\r\n") + 4
    return head[:idx], head[idx:]


async def _pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


def _rewrite_host_header(head: bytes) -> bytes:
    lines = head.split(b"\r\n")
    out = []
    for line in lines:
        if line.lower().startswith(b"host:"):
            out.append(b"Host: localhost:%d" % TARGET_PORT)
        else:
            out.append(line)
    return b"\r\n".join(out)


async def handle(client_reader, client_writer):
    target_writer = None
    try:
        head, rest = await _read_head(client_reader)
        if head is None:
            return
        headers = head.split(b"\r\n")[1:]
        is_upgrade = any(h.lower().startswith(b"upgrade:") for h in headers)
        new_head = _rewrite_host_header(head)

        target_reader, target_writer = await asyncio.open_connection(TARGET_HOST, TARGET_PORT)
        target_writer.write(new_head + rest)
        await target_writer.drain()

        if is_upgrade:
            # No response rewriting needed past the 101 handshake — just
            # splice the raw framed WS traffic both ways.
            await asyncio.gather(
                _pipe(client_reader, target_writer),
                _pipe(target_reader, client_writer),
            )
            return

        resp_head, resp_rest = await _read_head(target_reader)
        if resp_head is None:
            return
        resp_lines = resp_head.split(b"\r\n")
        content_length = None
        for line in resp_lines[1:]:
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":", 1)[1].strip())

        body = resp_rest
        if content_length is not None:
            while len(body) < content_length:
                chunk = await target_reader.read(content_length - len(body))
                if not chunk:
                    break
                body += chunk

        new_body = body.replace(b"localhost:%d" % TARGET_PORT, EXTERNAL_HOST_PORT) \
                       .replace(b"127.0.0.1:%d" % TARGET_PORT, EXTERNAL_HOST_PORT)

        # resp_lines ends with two empty strings (the blank line that
        # terminates the header block) — rebuild with those trimmed off,
        # append our own Content-Length/Connection, then restore the
        # terminator. Forcing Connection: close (and NOT piping anything
        # further on this socket below) means a client that immediately
        # follows up with a WS upgrade is forced onto a fresh TCP
        # connection — one that this handler parses and rewrites from
        # scratch, instead of silently relaying an un-rewritten Host header
        # through a kept-alive pipe.
        body_lines = resp_lines[:-2] if resp_lines[-2:] == [b"", b""] else resp_lines
        out_lines = []
        for line in body_lines:
            if line.lower().startswith(b"content-length:") or line.lower().startswith(b"connection:"):
                continue
            out_lines.append(line)
        out_lines.append(b"Content-Length: %d" % len(new_body))
        out_lines.append(b"Connection: close")
        out_lines.extend([b"", b""])
        client_writer.write(b"\r\n".join(out_lines) + new_body)
        await client_writer.drain()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        try:
            client_writer.close()
        except Exception:
            pass
        if target_writer is not None:
            try:
                target_writer.close()
            except Exception:
                pass


async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", LISTEN_PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
