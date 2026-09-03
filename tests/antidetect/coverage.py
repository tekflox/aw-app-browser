#!/usr/bin/env python3
"""Does the override daemon reach targets the OTHER CDP clients CREATE?

The daemon's `Target.setAutoAttach(waitForDebuggerOnStart=True, flatten=True)`
(container/platform-override.py:513) should pause every new target
browser-wide, so in principle it does. "In principle" is what this file
replaces: it exercises each creation path for real and compares the resulting
fingerprint against a page that is known to be patched.

Three paths, because they are genuinely different code in Chrome:

  reused-existing-page   attach to a page that was already open
  http-json-new          PUT /json/new — this is EXACTLY what
                         aw-app-devctl/devctl_app/cdp.py:95 and
                         aw-app-mini-browser/mini_browser_app/cdp.py:95 do
  target-createTarget    Target.createTarget over the browser-level session

Both other apps sit on the same `/json/new` call, so one path covers both —
they are reported separately anyway so a future divergence is visible.
"""
from __future__ import annotations

import asyncio

from cdp import (CDPSession, _rewrite_ws, close_target, http_json,
                 list_targets, new_target_via_http, open_page)

CHECK_URL = "https://example.com/"

# Fields the daemon controls. Two targets that agree on all of these are being
# patched the same way; one that disagrees was missed.
FINGERPRINT_JS = r"""
(() => {
  let glVendor = null, glRenderer = null;
  try {
    const gl = document.createElement('canvas').getContext('webgl');
    const d = gl && gl.getExtension('WEBGL_debug_renderer_info');
    if (d) { glVendor = gl.getParameter(d.UNMASKED_VENDOR_WEBGL);
             glRenderer = gl.getParameter(d.UNMASKED_RENDERER_WEBGL); }
  } catch (_) {}
  return {
    userAgent: navigator.userAgent,
    platform: navigator.platform,
    webdriver: navigator.webdriver,
    deviceMemory: navigator.deviceMemory,
    hardwareConcurrency: navigator.hardwareConcurrency,
    languages: (navigator.languages || []).join(','),
    uaDataPlatform: navigator.userAgentData ? navigator.userAgentData.platform : null,
    screenWidth: screen.width,
    screenHeight: screen.height,
    devicePixelRatio: window.devicePixelRatio,
    glVendor, glRenderer,
  };
})()
"""

# The "was anything patched at all?" tell. Without one, a dead daemon would
# make every path agree with a reference that is itself unpatched, and the
# comparison below would pass vacuously.
#
# It used to be `window.chrome.runtime`, which the daemon injected. That is
# gone with the Mac identity, so the tell is now deviceMemory: this container's
# Chromium reports 32 natively (measured with the daemon stopped) and the
# daemon clamps it to 8. If DEVICE_MEMORY_GB in platform-override.py ever
# changes, change it here too — deliberately explicit rather than inferred.
PATCHED_DEVICE_MEMORY = 8


async def _fingerprint(ws_url: str) -> dict:
    s = CDPSession(ws_url)
    await s.connect()
    try:
        await s.navigate(CHECK_URL, settle=1.0)
        return await s.evaluate(FINGERPRINT_JS)
    finally:
        await s.close()


def _ws(target: dict) -> str:
    return _rewrite_ws(target["webSocketDebuggerUrl"])


async def _create_via_browser_session() -> dict:
    """Target.createTarget over the browser-level WS."""
    browser_ws = _rewrite_ws(http_json("/json/version")["webSocketDebuggerUrl"])
    s = CDPSession(browser_ws)
    await s.connect()
    try:
        r = await s.send("Target.createTarget", {"url": "about:blank"})
        tid = r["targetId"]
    finally:
        await s.close()
    await asyncio.sleep(0.5)
    for t in list_targets():
        if t.get("id") == tid:
            return t
    raise RuntimeError(f"created target {tid} not found in /json")


async def run() -> dict:
    """Returns {reference, paths: [...], verdict, detail}."""
    out: dict = {}

    # Reference: a page target that was already open before we did anything.
    existing = [t for t in list_targets()
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    ref_target, ref_created = (existing[0], None) if existing else (None, None)
    if ref_target is None:
        s, tid = await open_page("about:blank")
        await s.close()
        ref_created = tid
        ref_target = next(t for t in list_targets() if t.get("id") == tid)

    reference = await _fingerprint(_ws(ref_target))
    out["reference"] = {"targetId": ref_target.get("id"),
                        "fingerprint": reference}

    paths = []
    created: list[str] = []
    try:
        # 1. reused existing page (same as the reference — recorded for shape)
        paths.append({
            "path": "reused-existing-page",
            "used_by": "any client that attaches to an open tab",
            "fingerprint": reference,
        })

        # 2. PUT /json/new — the devctl / mini-browser path
        t = new_target_via_http("about:blank")
        created.append(t["id"])
        fp = await _fingerprint(_ws(t))
        for label in ("aw-app-devctl/devctl_app/cdp.py:95",
                      "aw-app-mini-browser/mini_browser_app/cdp.py:95"):
            paths.append({
                "path": "http-json-new",
                "used_by": label,
                "fingerprint": fp,
            })

        # 3. Target.createTarget over the browser session
        t3 = await _create_via_browser_session()
        created.append(t3["id"])
        paths.append({
            "path": "target-createTarget",
            "used_by": "playwright MCP / any browser-level CDP client",
            "fingerprint": await _fingerprint(_ws(t3)),
        })
    finally:
        for tid in created:
            close_target(tid)
        if ref_created:
            close_target(ref_created)

    # ── verdict ────────────────────────────────────────────────────────────
    keys = [k for k in reference if k != "userAgent"] + ["userAgent"]
    failures = []
    for p in paths:
        fp = p["fingerprint"] or {}
        diff = {k: {"reference": reference.get(k), "thisPath": fp.get(k)}
                for k in keys if fp.get(k) != reference.get(k)}
        p["matchesReference"] = not diff
        if diff:
            p["diff"] = diff
            failures.append(f"{p['path']} ({p['used_by']}): "
                            f"{', '.join(sorted(diff))}")

    out["paths"] = paths
    patched_at_all = reference.get("deviceMemory") == PATCHED_DEVICE_MEMORY
    out["daemonAppliedToReference"] = patched_at_all

    if not patched_at_all:
        out["verdict"] = "FAIL"
        out["detail"] = (
            f"the reference page shows NO daemon patches at all "
            f"(deviceMemory={reference.get('deviceMemory')}, expected "
            f"{PATCHED_DEVICE_MEMORY}) — the override daemon is not running, "
            f"so coverage cannot be assessed")
    elif failures:
        out["verdict"] = "FAIL"
        out["detail"] = "targets missed by the daemon: " + "; ".join(failures)
    else:
        out["verdict"] = "PASS"
        out["detail"] = (f"all {len(paths)} creation paths produce an identical "
                         f"patched fingerprint")
    return out
