"""Keep Chromium's fingerprint internally coherent, via CDP.

Runs as a persistent background daemon.

IDENTITY: honest Linux/Chromium. This used to claim a MacBook Pro 16" M4 Max
on macOS 26.4.1, which the container contradicted five independent ways —
SwiftShader in the WebGL extension list (30 extensions including NV_* ones
Apple Metal cannot expose), MAX_TEXTURE_SIZE 8192 against Apple's 16384, an
empty enumerateDevices(), deviceMemory 32, and a 3008x1692 @ dPR 2 screen
implying a 6016px panel no Mac ships. Only 2 of ~50 WebGL getParameter values
were ever spoofed, so the Mac claim was never coherent, and making it coherent
would have meant fabricating ~20 more GL values, a synthetic extension list,
fake media devices and Mac font metrics — every one a fresh contradiction for
a checker like CreepJS, whose `lies` counter exists to find exactly that.

A macOS claim contradicted by a Linux reality is a stronger bot signal than an
honest Linux fingerprint. So this file now spoofs as little as possible; what
is left is only what is both true-ish and population-blending:

  navigator.webdriver  → false     insurance; already false in this container
  navigator.deviceMemory → 8       this build reports 32 natively, which is
                                   above the 8 GiB ceiling Chrome's Device
                                   Memory implementation has clamped to since
                                   2017 — an outlier worth flattening
  screen 1920x1080                 instead of leaking the Xvfb geometry, which
                                   is both an odd panel size and a giveaway

Everything else is now whatever Chromium natively reports — Linux x86_64, the
real SwiftShader renderer, the real UA, the real locale, the real core count —
because on this container all of those are already true.

Timezone is deliberately NOT handled here: it is set as the TZ environment
variable on the Chrome process in entrypoint-lite.sh, resolved from the
proxy's exit IP. Emulation.setTimezoneOverride would only reach page targets
and leave workers reporting UTC, which is a contradiction of its own — the env
var makes the page, its workers and Date all agree with no patched function
anywhere.

KEY DESIGN: Page.addScriptToEvaluateOnNewDocument is session-scoped — scripts
are DELETED when the CDP connection closes. Therefore ALL commands must go
through the single long-lived AsyncCDPSession (no one-shot _cdp_cmd for init
scripts). The daemon:

  1. Connects a persistent WebSocket to the browser-level CDP target.
  2. Enables Target.setAutoAttach(waitForDebuggerOnStart=true, flatten=true)
     so every new Page/Worker target is paused before executing any code.
  3. For each attached PAGE target: injects UA override + FINGERPRINT_JS init
     script via the persistent session, then resumes execution.
  4. For each attached WORKER target: injects WORKER_PATCH_JS via
     Runtime.evaluate, then resumes execution.
  5. Loops forever, handling new targets as Chrome creates them.
"""
import asyncio
import base64
import hashlib
import json
import os
import struct
import urllib.request

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222

# Chrome's Device Memory implementation rounds physical RAM to a power of two
# and clamps it at 8 GiB, so effectively the whole Chrome population reports
# <= 8. This container's Chromium 151 reports 32 natively (measured with this
# daemon stopped), which stands out on its own.
DEVICE_MEMORY_GB = 8

# A 1080p desktop — by far the most common desktop resolution — rather than
# the Xvfb geometry, which is an unusual panel size and a direct tell. Kept
# consistent with the actual window: availHeight leaves room for a panel, and
# the real outer window (~1503x845) fits inside it as a non-maximised window
# would.
SCREEN = {"width": 1920, "height": 1080, "availWidth": 1920, "availHeight": 1053}

# ── Main-thread JS (runs on every new document via addScriptToEvaluateOnNewDocument)
FINGERPRINT_JS = """
(function () {

  /* ── Stealth helpers ────────────────────────────────────────────────────
   *
   * _P: define on PROTOTYPE — avoids creating own-properties on navigator/
   *     screen that anti-bot libs detect via Object.getOwnPropertyDescriptor.
   *
   * _native: make a JS getter .toString() return "[native code]" so
   *          Function.prototype.toString checks pass.
   */
  const _native = (fn, name) => {
    try {
      Object.defineProperty(fn, 'name', { value: name || '', configurable: true });
      const _toString = Function.prototype.toString;
      const _bound = _toString.bind(fn);
      const _fakeSrc = `function ${name || ''}() { [native code] }`;
      Object.defineProperty(fn, 'toString', {
        value: () => _fakeSrc,
        configurable: true,
        writable: true,
      });
    } catch(e) {}
    return fn;
  };

  const _P = (proto, k, v) => {
    try {
      const getter = _native(() => v, `get ${k}`);
      Object.defineProperty(proto, k, {
        get: getter, configurable: true, enumerable: true,
      });
    } catch(e) {}
  };

  /* ── Remove automation / webdriver flag ─────────────────────────────── */
  // navigator.webdriver is set to true by Chrome when automated via CDP.
  // Override at prototype level so own-property checks show nothing.
  // (Already false in this container — kept as insurance, not as a fix.)
  try {
    _P(Navigator.prototype, 'webdriver', false);
  } catch(e) {}

  /* ── navigator.deviceMemory ──────────────────────────────────────────── */
  // The one navigator value this container gets wrong on its own: it reports
  // 32, above the 8 GiB ceiling Chrome clamps to. platform / language /
  // languages / hardwareConcurrency are all already correct natively and are
  // deliberately NOT patched — every unnecessary override is another
  // accessor for a checker to notice.
  _P(Navigator.prototype, 'deviceMemory', __DEVICE_MEMORY__);

  /* ── screen ──────────────────────────────────────────────────────────── */
  // Overridden on Screen.prototype to avoid an own-property tampering signal.
  // colorDepth/pixelDepth (24) and devicePixelRatio (1) are already correct
  // natively, so they are left alone.
  const _S = __SCREEN__;
  _P(Screen.prototype, 'width',       _S.width);
  _P(Screen.prototype, 'height',      _S.height);
  _P(Screen.prototype, 'availWidth',  _S.availWidth);
  _P(Screen.prototype, 'availHeight', _S.availHeight);

  // WebGL vendor/renderer are NOT spoofed any more. They honestly report
  // SwiftShader, which is what the extension list, MAX_TEXTURE_SIZE and the
  // rest of the ~50 getParameter values already said. Software GL on Linux is
  // ordinary; software GL on a Mac is impossible, which is what the old Apple
  // Metal string claimed.

  // window.chrome.runtime is NOT added. Debian's Chromium genuinely lacks it,
  // and navigator.userAgentData honestly brands this as Chromium — adding a
  // Google-Chrome-only object on top of a Chromium brand list would be a new
  // contradiction, not a fix.

  // SpeechSynthesis voices are NOT faked. The old fallback injected a macOS
  // voice ('com.apple.speech.synthesis.voice.samantha') on a Linux box; an
  // empty voice list is ordinary on Linux without speech-dispatcher.

  // The FontFace remap of Mac-only families (Monaco, Menlo, Lucida Grande…)
  // is gone for the same reason, along with the matching fontconfig aliases.
  // The msttcorefonts set really is installed (see the Dockerfile), so Arial,
  // Times New Roman, Courier New and friends resolve honestly.

})();
"""

# ── Worker-scope patch — injected via Runtime.evaluate when a worker starts ──
# Runs BEFORE any worker code (waitForDebuggerOnStart ensures this).
#
# deviceMemory is the only thing left to fix here, and it MUST be fixed here:
# CreepJS reads navigator from a worker as well as from the main thread and
# compares them, so clamping it on the page and leaving the worker at 32 would
# manufacture the exact kind of contradiction this file is trying to remove.
# Everything else a worker exposes (platform, languages, hardwareConcurrency,
# userAgentData, WebGL) is already honest, so nothing else is touched.
WORKER_PATCH_JS = """
(function(){
  /* Same [native code] masking as the main thread — a worker's navigator gets
     inspected just as closely as the page's. */
  const _native=(fn,name)=>{
    try {
      Object.defineProperty(fn,'name',{value:name||'',configurable:true});
      const _src=`function ${name||''}() { [native code] }`;
      Object.defineProperty(fn,'toString',{value:()=>_src,configurable:true,writable:true});
    } catch(e){}
    return fn;
  };
  const _p=(proto,k,v)=>{
    try {
      Object.defineProperty(proto,k,{
        get:_native(()=>v,`get ${k}`), configurable:true, enumerable:true});
    } catch(e){}
  };
  if(typeof WorkerNavigator!=='undefined'){
    _p(WorkerNavigator.prototype,'deviceMemory',__DEVICE_MEMORY__);
  } else if(typeof navigator!=='undefined'){
    /* fallback for environments without WorkerNavigator */
    _p(navigator,'deviceMemory',__DEVICE_MEMORY__);
  }
})();
"""


def _fill(js: str) -> str:
    """Inline the Python-side constants so page and worker cannot drift."""
    return (js.replace("__DEVICE_MEMORY__", json.dumps(DEVICE_MEMORY_GB))
              .replace("__SCREEN__", json.dumps(SCREEN)))


FINGERPRINT_JS = _fill(FINGERPRINT_JS)
WORKER_PATCH_JS = _fill(WORKER_PATCH_JS)


# ── Async CDP session ──────────────────────────────────────────────────────────

class AsyncCDPSession:
    """Persistent CDP WebSocket session (browser-level, flat mode)."""

    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self._reader = None
        self._writer = None
        self._cmd_id = 0
        self._pending: dict = {}
        self._handlers: dict = {}

    async def connect(self):
        from urllib.parse import urlparse
        parsed = urlparse(self.ws_url)
        self._reader, self._writer = await asyncio.open_connection(
            parsed.hostname, parsed.port
        )
        ws_key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET {parsed.path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode()
        self._writer.write(handshake)
        await self._writer.drain()
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = await self._reader.read(4096)
            if not chunk:
                raise ConnectionError("CDP WS handshake failed")
            buf += chunk

    async def _send_frame(self, data: bytes):
        mask_key = os.urandom(4)
        frame = bytearray([0x81])
        n = len(data)
        if n < 126:
            frame.append(0x80 | n)
        elif n < 65536:
            frame.append(0x80 | 126)
            frame.extend(struct.pack(">H", n))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack(">Q", n))
        frame.extend(mask_key)
        frame.extend(bytes(b ^ mask_key[i % 4] for i, b in enumerate(data)))
        self._writer.write(bytes(frame))
        await self._writer.drain()

    async def _recv_frame(self) -> bytes:
        header = await self._reader.readexactly(2)
        payload_len = header[1] & 0x7F
        if payload_len == 126:
            payload_len = struct.unpack(">H", await self._reader.readexactly(2))[0]
        elif payload_len == 127:
            payload_len = struct.unpack(">Q", await self._reader.readexactly(8))[0]
        return await self._reader.readexactly(payload_len)

    async def send(self, method: str, params: dict = None, session_id: str = None):
        self._cmd_id += 1
        cmd_id = self._cmd_id
        msg = {"id": cmd_id, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        fut = asyncio.get_event_loop().create_future()
        self._pending[cmd_id] = fut
        await self._send_frame(json.dumps(msg).encode())
        try:
            return await asyncio.wait_for(fut, timeout=10.0)
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            return None

    def on(self, method: str, handler):
        self._handlers.setdefault(method, []).append(handler)

    async def run(self):
        """Receive loop — dispatches events and resolves pending command futures."""
        while True:
            try:
                raw = await self._recv_frame()
                msg = json.loads(raw.decode())
            except Exception as e:
                print(f"CDP recv error: {e}", flush=True)
                break
            if "id" in msg:
                fut = self._pending.pop(msg["id"], None)
                if fut and not fut.done():
                    fut.set_result(msg)
            elif "method" in msg:
                for handler in self._handlers.get(msg["method"], []):
                    try:
                        asyncio.ensure_future(handler(msg.get("params", {}), msg.get("sessionId")))
                    except Exception as e:
                        print(f"Handler error: {e}", flush=True)


# ── Main ───────────────────────────────────────────────────────────────────────

def _build_marker() -> str:
    """sha256 of this very file.

    The app manifest pins ghcr.io/tekflox/aw-app-browser:latest, so a green CI
    run does not prove a rebuilt image is what the container is actually
    running. Printing the hash of the live file at startup means
    `podman logs aw-app-browser` can settle that question on its own — compare
    it with `sha256sum container/platform-override.py` in the repo.
    """
    try:
        return hashlib.sha256(
            open(os.path.abspath(__file__), "rb").read()).hexdigest()[:16]
    except Exception:
        return "unknown"


async def async_main():
    print(f"platform-override.py build {_build_marker()}", flush=True)

    # ── 1. Wait for Chrome's CDP to be available ───────────────────────────
    browser_ws = None
    for _ in range(30):
        try:
            with urllib.request.urlopen(
                f"http://{CDP_HOST}:{CDP_PORT}/json/version", timeout=2
            ) as resp:
                info = json.loads(resp.read())
                browser_ws = info.get("webSocketDebuggerUrl")
                if browser_ws:
                    break
        except Exception:
            pass
        await asyncio.sleep(1)

    if not browser_ws:
        print("Platform override: CDP not available after 30s, skipping", flush=True)
        return

    print(f"CDP ready. Browser WS: {browser_ws[:55]}...", flush=True)

    # ── 2. Connect persistent session to browser target ────────────────────
    cdp = AsyncCDPSession(browser_ws)
    try:
        await cdp.connect()
    except Exception as e:
        print(f"Persistent CDP connect failed: {e}", flush=True)
        return

    attached_sessions: set = set()

    async def apply_page_patches(session_id: str):
        """Apply the initScript to a page via the persistent session.

        No Emulation.setUserAgentOverride any more. The UA used to be set here
        AND as --user-agent in entrypoint-lite.sh, two places that had to be
        kept in step or the UA and the Client Hints would disagree — which is
        itself a detection signal. Chromium's own Linux UA is correct, so both
        overrides are gone and there is nothing left to keep in step.
        """
        # InitScript — persists as long as this CDPSession stays open; runs
        # BEFORE any page JavaScript for navigations driven by THIS session.
        await cdp.send("Page.addScriptToEvaluateOnNewDocument",
                       {"source": FINGERPRINT_JS}, session_id=session_id)
        # Enable Page events so we can also patch via frameNavigated (fallback
        # for navigations triggered by OTHER CDP sessions, e.g. Playwright MCP).
        await cdp.send("Page.enable", {}, session_id=session_id)
        # Evaluate immediately into the current document too (handles the initial
        # about:blank → real-URL navigation that may have already happened).
        await cdp.send("Runtime.evaluate",
                       {"expression": FINGERPRINT_JS, "returnByValue": False},
                       session_id=session_id)

    # page_sessions: maps sessionId → True for active PAGE targets we're patching
    page_sessions: set = set()

    async def on_frame_navigated(params: dict, session_id: str):
        """Belt-and-suspenders: re-apply patches after every main-frame navigation.

        Page.addScriptToEvaluateOnNewDocument fires reliably when the CDP
        session that registered it drives the navigation.  When an external
        session (Playwright MCP, another tool) navigates the same target we
        catch it here via Page.frameNavigated (which routes to us via the flat
        CDP session's sessionId) and inject via Runtime.evaluate.  This runs
        after navigation commits but before DOMContentLoaded — fast enough to
        beat all fingerprinting libraries.
        """
        if session_id not in page_sessions:
            return  # not a page we're patching
        frame = params.get("frame", {})
        if frame.get("parentId"):
            return  # skip sub-frames — only patch the main document
        url = frame.get("url", "")
        if url in ("about:blank", "chrome://newtab/", ""):
            return  # nothing useful to patch on blank/newtab
        print(f"  ↻ frameNavigated [{session_id[:12]}…] → {url[:60]}", flush=True)
        await cdp.send("Runtime.evaluate",
                       {"expression": FINGERPRINT_JS, "returnByValue": False},
                       session_id=session_id)

    async def on_attached(params: dict, _):
        target = params.get("targetInfo", {})
        t_type = target.get("type", "")
        t_id   = target.get("targetId", "")[:16]
        sid    = params.get("sessionId", "")

        if sid in attached_sessions:
            return
        attached_sessions.add(sid)

        if t_type in ("worker", "service_worker", "shared_worker"):
            print(f"  Worker attached [{t_type} {t_id}…] — patching", flush=True)
            await cdp.send("Runtime.evaluate",
                           {"expression": WORKER_PATCH_JS, "returnByValue": False},
                           session_id=sid)
            await cdp.send("Runtime.runIfWaitingForDebugger", {}, session_id=sid)

        elif t_type == "page":
            print(f"  Page attached [{t_id}…] — patching", flush=True)
            page_sessions.add(sid)
            await apply_page_patches(sid)
            await cdp.send("Runtime.runIfWaitingForDebugger", {}, session_id=sid)

        else:
            # iframe, background_page, etc. — just resume
            await cdp.send("Runtime.runIfWaitingForDebugger", {}, session_id=sid)

    cdp.on("Target.attachedToTarget", on_attached)
    # Single global handler — session_id in message routes to the right page
    cdp.on("Page.frameNavigated", on_frame_navigated)

    # ── 3. Enable auto-attach for ALL targets (pauses them until we resume) ─
    await cdp.send("Target.setAutoAttach", {
        "autoAttach":            True,
        "waitForDebuggerOnStart": True,
        "flatten":                True,
    })
    # Discover existing targets so they fire attachedToTarget
    await cdp.send("Target.setDiscoverTargets", {"discover": True})

    print("Platform override daemon running.", flush=True)
    print(f"  identity: honest Linux/Chromium — no UA, platform, locale or "
          f"WebGL override", flush=True)
    print(f"  Pages:   deviceMemory={DEVICE_MEMORY_GB}, "
          f"screen={SCREEN['width']}x{SCREEN['height']}, webdriver=false",
          flush=True)
    print(f"  Workers: deviceMemory={DEVICE_MEMORY_GB}", flush=True)
    print(f"  TZ={os.environ.get('TZ') or 'unset (container default)'}",
          flush=True)

    # ── 4. Run event loop forever ──────────────────────────────────────────
    await cdp.run()
    print("CDP session ended — Chrome likely closed.", flush=True)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
