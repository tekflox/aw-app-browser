#!/usr/bin/env python3
"""CDP `Runtime.enable` side-channel — measured with the harness DETACHED.

Why this file exists at all: the probe that measures "is a CDP client
listening to the console?" is itself a CDP client. Enable `Runtime` to run the
probe and you *create* the signal you came to measure. The Architect's design
pass hit exactly this — `errStackToStringLeak=1` with no way to tell how much
of it was the instrument.

So we measure three ways and print all three:

  attached_plain      our session is open but we never called Runtime.enable
                      (Page + Network only — what the rest of the battery does)
  detached            NO session at all: the page arms a `setTimeout`, we close
                      the WebSocket, and the probe result is stashed in
                      localStorage for a later attach to read back
  attached_runtime    we deliberately call Runtime.enable — the positive
                      control that proves the probe can fire at all

Only `detached` says anything about what a real web page sees. A leak that
shows up in `attached_runtime` and nowhere else is our own instrument, and per
the implementation brief must NOT be patched.
"""
from __future__ import annotations

import asyncio
import json

from cdp import CDPSession, close_target, open_page

# A stable, cheap origin — needs a real (non-opaque) origin so localStorage
# works, which `data:` URLs do not have.
PROBE_ORIGIN = "https://example.com/"
STORAGE_KEY = "__aw_antidetect_sidechannel"
DEFERRED_DELAY_MS = 4000

# The three console-serialisation tells. Each one fires only if some CDP client
# is subscribed to Runtime events, because that is what makes Chrome build a
# RemoteObject preview for the argument (and touch the getters doing it).
_PROBE_BODY = r"""
  const r = {stackGetter: 0, errToString: 0, objPreview: 0,
             errToStringNoConsole: 0, stackGetterNoConsole: 0};
  // No-console controls: build identical traps and never hand them to
  // console. These must stay 0 — if one fires, something other than console
  // serialisation is touching the object and the whole probe is unsound.
  try {
    const c1 = new Error('control');
    c1.toString = function () { r.errToStringNoConsole++; return ''; };
    const c2 = new Error('control');
    Object.defineProperty(c2, 'stack', {
      configurable: true, get() { r.stackGetterNoConsole++; return ''; }});
  } catch (_) {}
  try {
    const e = new Error('probe');
    Object.defineProperty(e, 'stack', {
      configurable: true, get() { r.stackGetter++; return ''; }});
    console.debug(e);
  } catch (_) {}
  try {
    const e2 = new Error('probe');
    e2.toString = function () { r.errToString++; return ''; };
    console.debug(e2);
  } catch (_) {}
  try {
    const o = {};
    Object.defineProperty(o, 'leak', {
      configurable: true, enumerable: true, get() { r.objPreview++; return 1; }});
    console.debug(o);
  } catch (_) {}
"""

IMMEDIATE_JS = "(() => {" + _PROBE_BODY + " return r; })()"

# Arms the probe to run AFTER we are gone, and snapshots the counters
# synchronously right after the console calls — so a later re-attach replaying
# buffered console messages cannot inflate the number we read back.
DEFERRED_JS = f"""
(() => {{
  try {{ localStorage.removeItem('{STORAGE_KEY}'); }} catch (_) {{}}
  setTimeout(function () {{
    {_PROBE_BODY}
    try {{
      localStorage.setItem('{STORAGE_KEY}', JSON.stringify(
        {{armedAt: {DEFERRED_DELAY_MS}, ranAt: Date.now(), counts: r}}));
    }} catch (_) {{}}
  }}, {DEFERRED_DELAY_MS});
  return true;
}})()
"""

READBACK_JS = f"""
(() => {{
  try {{
    const v = localStorage.getItem('{STORAGE_KEY}');
    return v ? JSON.parse(v) : null;
  }} catch (e) {{ return {{error: String(e)}}; }}
}})()
"""


# `errToString` is NOT a CDP tell in this browser. Measured 2026-09-03 with a
# zero-CDP-client control: the override daemon was killed, the harness
# disconnected, and a deferred setTimeout probe still recorded errToString=1
# with literally nothing attached (raw result kept alongside this run's
# artefacts). So `console.debug(err)` touches Error.prototype.toString
# unconditionally in Chrome 151 — it fires the same whether a client is
# listening or not, which means it carries zero information about automation
# and patching it would be chasing our own instrument.
#
# Redo that control like this (needs podman on the workspace host):
#   podman exec aw-app-browser sh -c 'for p in $(pgrep -f "[p]latform-override"); do kill $p; done'
#   <arm a deferred probe, disconnect, wait for it to fire, re-attach, read>
#   podman exec -d aw-app-browser sh -c 'exec python3 /opt/aw-browser/platform-override.py'
UNCONDITIONAL_COUNTERS = {"errToString"}

_CONTROL_COUNTERS = {"errToStringNoConsole", "stackGetterNoConsole"}


def _leaks(counts: dict | None) -> dict:
    """Counters that actually indicate a listening CDP client."""
    if not counts:
        return {}
    return {k: v for k, v in counts.items()
            if v and k not in UNCONDITIONAL_COUNTERS
            and k not in _CONTROL_COUNTERS}


def _control_dirty(counts: dict | None) -> dict:
    if not counts:
        return {}
    return {k: v for k, v in counts.items() if v and k in _CONTROL_COUNTERS}


async def measure() -> dict:
    """Run the three-way measurement. Returns a dict safe to drop into
    verdicts.json — including a `conclusion` a human can act on."""
    result: dict = {"origin": PROBE_ORIGIN, "deferredDelayMs": DEFERRED_DELAY_MS}
    session, target_id = await open_page("about:blank")
    ws_url = session.ws_url
    try:
        nav = await session.navigate(PROBE_ORIGIN, settle=1.0)
        result["navigation"] = nav
        if nav.get("error") or (nav.get("status") or 200) >= 400:
            result["conclusion"] = (
                "DETECTOR-UNAVAILABLE — could not load the probe origin, so the "
                "side-channel was not measured")
            return result

        # ── leg 1: attached, Runtime never enabled ─────────────────────────
        result["attached_plain"] = await session.evaluate(IMMEDIATE_JS)

        # ── arm the deferred probe, then leave completely ──────────────────
        await session.evaluate(DEFERRED_JS)
    finally:
        await session.close()

    # No CDP session of ours exists for the whole of this sleep. The
    # platform-override daemon's own session is still attached — that is
    # deliberate, it ships with the product and its footprint is part of what
    # a real page would see.
    await asyncio.sleep(DEFERRED_DELAY_MS / 1000.0 + 3.0)

    session2 = CDPSession(ws_url)
    await session2.connect()
    try:
        stored = await session2.evaluate(READBACK_JS)
        result["detached"] = (stored or {}).get("counts") if isinstance(stored, dict) else None
        result["detached_raw"] = stored

        # ── leg 3: positive control — deliberately create the signal ───────
        await session2.send("Runtime.enable")
        result["attached_runtime_enabled"] = await session2.evaluate(IMMEDIATE_JS)
        await session2.send("Runtime.disable")
    finally:
        await session2.close()
        close_target(target_id)

    detached = result.get("detached")
    control = result.get("attached_runtime_enabled")
    result["informativeCountersDetached"] = _leaks(detached)
    result["informativeCountersRuntimeEnabled"] = _leaks(control)

    if result.get("detached_raw") is None:
        result["conclusion"] = (
            "INCONCLUSIVE — the deferred probe never wrote its result; the page "
            "may have navigated away. Re-run before drawing any conclusion.")
    elif _control_dirty(detached) or _control_dirty(control):
        result["conclusion"] = (
            "UNSOUND — a no-console control counter fired, so something other "
            "than console serialisation is touching the probe objects. Fix the "
            "probe before believing any number here.")
    elif _leaks(detached):
        result["conclusion"] = (
            f"REAL LEAK — with no harness attached the page still observed "
            f"console serialisation on an informative counter "
            f"({json.dumps(_leaks(detached))}). Genuine CDP tell, worth patching.")
    elif _leaks(control):
        result["conclusion"] = (
            f"NO REAL LEAK — the signal appears only when the harness itself "
            f"enables Runtime ({json.dumps(_leaks(control))}). That is the "
            f"instrument, not the browser. Do NOT patch this.")
    else:
        result["conclusion"] = (
            "NO OBSERVABLE CDP SIDE-CHANNEL — every informative counter is zero "
            "in all three legs, including with Runtime deliberately enabled. "
            "Modern Chrome does not invoke accessors when building console "
            "object previews, so these classic probes no longer fire at all. "
            "The only counter that moves (errToString) also moves with ZERO CDP "
            "clients attached, so it distinguishes nothing. Do NOT patch this.")
    return result
