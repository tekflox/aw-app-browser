"""Apply macOS platform override to Chromium via CDP.

Runs as a persistent background daemon.

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
import json
import os
import socket
import struct
import time
import urllib.request

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222

PLATFORM_OVERRIDE = {
    "userAgent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
    "platform": "MacIntel",
    "acceptLanguage": "en-US,pt-BR;q=0.9,pt;q=0.8,en;q=0.7",
    "userAgentMetadata": {
        "brands": [
            {"brand": "Google Chrome", "version": "147"},
            {"brand": "Chromium", "version": "147"},
            {"brand": "Not:A-Brand", "version": "99"},
        ],
        "fullVersion": "147.0.7727.138",
        "platform": "macOS",
        "platformVersion": "26.4.1",
        "architecture": "arm",
        "model": "",
        "mobile": False,
    },
}

# ── Main-thread JS (runs on every new document via addScriptToEvaluateOnNewDocument)
FINGERPRINT_JS = """
(function () {

  /* ── Stealth helpers ────────────────────────────────────────────────────
   *
   * _P: define on PROTOTYPE — avoids creating own-properties on navigator/
   *     screen that anti-bot libs detect via Object.getOwnPropertyDescriptor.
   *
   * _D: define directly on INSTANCE — used only where no prototype exists
   *     (e.g. window.devicePixelRatio on Window).
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

  const _D = (o, k, v) => {
    try {
      const getter = _native(() => v, `get ${k}`);
      Object.defineProperty(o, k, {
        get: getter, configurable: true, enumerable: true,
      });
    } catch(e) {}
  };

  /* ── Remove automation / webdriver flag ─────────────────────────────── */
  // navigator.webdriver is set to true by Chrome when automated via CDP.
  // Override at prototype level so own-property checks show nothing.
  try {
    _P(Navigator.prototype, 'webdriver', false);
  } catch(e) {}

  /* ── navigator (prototype-level overrides — no own-property footprint) ─ */
  _P(Navigator.prototype, 'platform',            'MacIntel');
  _P(Navigator.prototype, 'language',            'en-US');
  _P(Navigator.prototype, 'languages',           Object.freeze(['en-US', 'pt-BR', 'pt', 'en']));
  _P(Navigator.prototype, 'hardwareConcurrency', 16);
  _P(Navigator.prototype, 'deviceMemory',        32);
  // oscpu is a Firefox-only property; patching it on Chrome's Navigator
  // would create an unexpected own-property — skip it.

  /* ── navigator.userAgentData (Client Hints) ── */
  try {
    const _brands = Object.freeze([
      { brand: 'Google Chrome', version: '147' },
      { brand: 'Chromium',      version: '147' },
      { brand: 'Not:A-Brand',   version: '99'  },
    ]);
    const _uad = {
      brands:   _brands,
      mobile:   false,
      platform: 'macOS',
      getHighEntropyValues: _native(async () => ({
        brands: _brands,
        fullVersionList: Object.freeze([
          { brand: 'Google Chrome', version: '147.0.7727.138' },
          { brand: 'Chromium',      version: '147.0.7727.138' },
          { brand: 'Not:A-Brand',   version: '99.0.0.0'  },
        ]),
        mobile:          false,
        platform:        'macOS',
        platformVersion: '26.4.1',
        architecture:    'arm',
        bitness:         '64',
        model:           '',
        uaFullVersion:   '147.0.7727.138',
      }), 'getHighEntropyValues'),
      toJSON: _native(() => ({ brands: _brands, mobile: false, platform: 'macOS' }), 'toJSON'),
    };
    _P(Navigator.prototype, 'userAgentData', _uad);
  } catch(e) {}

  /* ── screen (MacBook Pro 16-inch M4 Max, Retina 3008×1692, menu bar 30px)
   *   Override on Screen.prototype to avoid own-property tampering signal. ── */
  _P(Screen.prototype, 'width',      3008);
  _P(Screen.prototype, 'height',     1692);
  _P(Screen.prototype, 'availWidth', 3008);
  _P(Screen.prototype, 'availHeight',1662);
  _P(Screen.prototype, 'colorDepth',   24);
  _P(Screen.prototype, 'pixelDepth',   24);
  /* devicePixelRatio lives on Window, not a clean prototype — use instance override */
  _D(window,  'devicePixelRatio', 2);

  /* ── WebGL (Apple M4 Max via ANGLE Metal) ── */
  const _VENDOR   = 'Google Inc. (Apple)';
  const _RENDERER = 'ANGLE (Apple, ANGLE Metal Renderer: Apple M4 Max, Unspecified Version)';
  function patchWebGL(Ctx) {
    if (!Ctx) return;
    const orig = Ctx.prototype.getParameter;
    Ctx.prototype.getParameter = _native(function(p) {
      if (p === 37445) return _VENDOR;
      if (p === 37446) return _RENDERER;
      return orig.call(this, p);
    }, 'getParameter');
  }
  try { patchWebGL(WebGLRenderingContext);  } catch(e) {}
  try { patchWebGL(WebGL2RenderingContext); } catch(e) {}

  /* ── window.chrome.runtime — present in all real Chrome builds ── */
  // FingerprintJS Pro checks: window.chrome exists but chrome.runtime is
  // undefined → automated/headless signal.  Patch to add a minimal runtime.
  try {
    if (window.chrome && !window.chrome.runtime) {
      window.chrome.runtime = {
        id:         undefined,
        connect:    _native(function connect(){}, 'connect'),
        sendMessage:_native(function sendMessage(){}, 'sendMessage'),
        onConnect:  { addListener: _native(function addListener(){}, 'addListener') },
        onMessage:  { addListener: _native(function addListener(){}, 'addListener') },
        OnInstalledReason: { CHROME_UPDATE:'chrome_update', INSTALL:'install', SHARED_MODULE_UPDATE:'shared_module_update', UPDATE:'update' },
        PlatformArch: { ARM:'arm', ARM64:'arm64', MIPS:'mips', MIPS64:'mips64', X86_32:'x86-32', X86_64:'x86-64' },
        PlatformOs:   { ANDROID:'android', CROS:'cros', LINUX:'linux', MAC:'mac', OPENBSD:'openbsd', WIN:'win' },
        RequestUpdateCheckStatus: { NO_UPDATE:'no_update', THROTTLED:'throttled', UPDATE_AVAILABLE:'update_available' },
      };
    }
  } catch(e) {}

  /* ── Speech Synthesis — add a minimal macOS-like voice to avoid 0-voices signal ── */
  try {
    const _origGetVoices = SpeechSynthesis.prototype.getVoices;
    if (_origGetVoices) {
      const _fakeVoices = [{
        default: true, lang: 'en-US', localService: true,
        name: 'Samantha', voiceURI: 'com.apple.speech.synthesis.voice.samantha',
      }];
      SpeechSynthesis.prototype.getVoices = _native(function getVoices() {
        const real = _origGetVoices.call(this);
        return real && real.length > 0 ? real : _fakeVoices;
      }, 'getVoices');
    }
  } catch(e) {}

  /* ── FontFace proxy: map obscure Mac-only fonts → Liberation equivalents ── */
  try {
    const _FF = window.FontFace;
    const _MAP = {
      'gill sans': 'Liberation Sans', 'optima': 'Liberation Sans',
      'futura': 'Liberation Sans', 'lucida grande': 'Liberation Sans',
      'calibri': 'Liberation Sans', 'candara': 'Liberation Sans',
      'segoe ui': 'Liberation Sans',
      'garamond': 'Liberation Serif', 'baskerville': 'Liberation Serif',
      'palatino': 'Liberation Serif', 'book antiqua': 'Liberation Serif',
      'cambria': 'Liberation Serif',
      'monaco': 'Liberation Mono', 'menlo': 'Liberation Mono',
    };
    function _remapSrc(src) {
      if (typeof src !== 'string') return src;
      return src.replace(/local\\(['"]?([^'"()]+)['"]?\\)/gi, function(m, name) {
        const key = name.trim().toLowerCase();
        const mapped = _MAP[key];
        return mapped ? 'local("' + mapped + '")' : m;
      });
    }
    window.FontFace = _native(function FontFace(family, source, descriptors) {
      return new _FF(family, _remapSrc(source), descriptors);
    }, 'FontFace');
    window.FontFace.prototype = _FF.prototype;
  } catch(e) {}

})();
"""

# ── Worker-scope patch — injected via Runtime.evaluate when a worker starts ──
# Runs BEFORE any worker code (waitForDebuggerOnStart ensures this).
# Uses prototype-level overrides to avoid own-property tampering signals.
WORKER_PATCH_JS = """
(function(){
  const _V='Google Inc. (Apple)';
  const _R='ANGLE (Apple, ANGLE Metal Renderer: Apple M4 Max, Unspecified Version)';
  const _b=Object.freeze([
    {brand:'Google Chrome',version:'147'},
    {brand:'Chromium',version:'147'},
    {brand:'Not:A-Brand',version:'99'}
  ]);
  const _uad={
    brands:_b, mobile:false, platform:'macOS',
    getHighEntropyValues: async () => ({
      brands: _b,
      fullVersionList: Object.freeze([
        {brand:'Google Chrome',version:'147.0.7727.138'},
        {brand:'Chromium',version:'147.0.7727.138'},
        {brand:'Not:A-Brand',version:'99.0.0.0'}
      ]),
      mobile:false, platform:'macOS', platformVersion:'26.4.1',
      architecture:'arm', bitness:'64', model:'', uaFullVersion:'147.0.7727.138'
    }),
    toJSON: () => ({brands:_b, mobile:false, platform:'macOS'})
  };
  /* Use prototype-level definition where possible */
  const _p=(proto,k,v)=>{
    try { Object.defineProperty(proto,k,{get:()=>v, configurable:true, enumerable:true}); } catch(e){}
  };
  if(typeof navigator!=='undefined' && typeof WorkerNavigator!=='undefined'){
    _p(WorkerNavigator.prototype,'hardwareConcurrency',16);
    _p(WorkerNavigator.prototype,'deviceMemory',32);
    _p(WorkerNavigator.prototype,'language','en-US');
    _p(WorkerNavigator.prototype,'languages',Object.freeze(['en-US','pt-BR','pt','en']));
    _p(WorkerNavigator.prototype,'userAgentData',_uad);
    _p(WorkerNavigator.prototype,'platform','MacIntel');
  } else if(typeof navigator!=='undefined'){
    /* fallback for environments without WorkerNavigator */
    const _d=(o,k,v)=>{
      try { Object.defineProperty(o,k,{get:()=>v, configurable:true, enumerable:true}); } catch(e){}
    };
    _d(navigator,'hardwareConcurrency',16);
    _d(navigator,'deviceMemory',32);
    _d(navigator,'language','en-US');
    _d(navigator,'languages',Object.freeze(['en-US','pt-BR','pt','en']));
    _d(navigator,'userAgentData',_uad);
    _d(navigator,'platform','MacIntel');
  }
  function pGL(C){
    if(!C||!C.prototype)return;
    const o=C.prototype.getParameter;
    C.prototype.getParameter=function(p){
      if(p===37445)return _V;
      if(p===37446)return _R;
      return o.call(this,p);
    };
  }
  try{pGL(WebGLRenderingContext);}catch(e){}
  try{pGL(WebGL2RenderingContext);}catch(e){}
})();
"""


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

async def async_main():
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
        """Apply UA override + initScript to a page via the persistent session."""
        # UA override
        await cdp.send("Emulation.setUserAgentOverride", PLATFORM_OVERRIDE,
                       session_id=session_id)
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
    print("  Pages:   UA=Chrome147/macOS, platform=MacIntel, cores=16, ram=32, screen=3008x1692", flush=True)
    print("  Workers: hardwareConcurrency=16, deviceMemory=32, macOS 26.4.1 arm_64, WebGL=Apple M4", flush=True)

    # ── 4. Run event loop forever ──────────────────────────────────────────
    await cdp.run()
    print("CDP session ended — Chrome likely closed.", flush=True)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
