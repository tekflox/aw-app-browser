#!/usr/bin/env python3
"""Pure-JS coherence assertions — no external detector needed.

These are the checks that decide whether the fingerprint contradicts *itself*.
A detector site can only tell us it dislikes us; these tell us why. They are
deterministic and entirely ours, so unlike the Tier B/C sites they are allowed
to fail the run.

Three of the six live on the PO's in-scope gap list and were measured CLEAN by
the Architect before any patching (WebRTC private-IP leak, plugins/mimeTypes
shape, Permissions/Notification coherence) — they are asserted here anyway so
a future regression shows up as a failing assertion instead of nothing.
"""
from __future__ import annotations

# Collects the raw facts. Evaluation happens in Python so the reasoning is
# readable and the raw values land in verdicts.json for a human to argue with.
COLLECT_JS = r"""
(async () => {
  const out = {};
  const safe = async (k, fn) => { try { out[k] = await fn(); } catch (e) { out[k] = {error: String(e)}; } };

  await safe('nav', async () => ({
    userAgent: navigator.userAgent,
    platform: navigator.platform,
    webdriver: navigator.webdriver,
    deviceMemory: navigator.deviceMemory,
    hardwareConcurrency: navigator.hardwareConcurrency,
    language: navigator.language,
    languages: Array.from(navigator.languages || []),
    pdfViewerEnabled: navigator.pdfViewerEnabled,
  }));

  await safe('uaData', async () => {
    if (!navigator.userAgentData) return null;
    const he = await navigator.userAgentData.getHighEntropyValues(
      ['platform', 'platformVersion', 'architecture', 'bitness', 'model',
       'uaFullVersion', 'fullVersionList']);
    return {
      platform: navigator.userAgentData.platform,
      mobile: navigator.userAgentData.mobile,
      brands: navigator.userAgentData.brands,
      highEntropy: he,
    };
  });

  await safe('intl', async () => ({
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    locale: Intl.DateTimeFormat().resolvedOptions().locale,
    offsetMinutes: new Date().getTimezoneOffset(),
    dateString: new Date().toString(),
  }));

  await safe('screen', async () => ({
    width: screen.width, height: screen.height,
    availWidth: screen.availWidth, availHeight: screen.availHeight,
    colorDepth: screen.colorDepth, pixelDepth: screen.pixelDepth,
    devicePixelRatio: window.devicePixelRatio,
    // The window the screen is supposed to contain — without these there is
    // nothing to check screen.* against, which is how a 1920x1080 claim in a
    // 1504x846 window went unnoticed until iphey flagged it.
    outerWidth: window.outerWidth, outerHeight: window.outerHeight,
    innerWidth: window.innerWidth, innerHeight: window.innerHeight,
  }));

  await safe('webgl', async () => {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl2') || c.getContext('webgl');
    if (!gl) return null;
    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
    const exts = gl.getSupportedExtensions() || [];
    return {
      vendor: gl.getParameter(gl.VENDOR),
      renderer: gl.getParameter(gl.RENDERER),
      unmaskedVendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null,
      unmaskedRenderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
      version: gl.getParameter(gl.VERSION),
      shadingLanguageVersion: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
      maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
      maxRenderbufferSize: gl.getParameter(gl.MAX_RENDERBUFFER_SIZE),
      extensionCount: exts.length,
      extensions: exts,
    };
  });

  await safe('mediaDevices', async () => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return null;
    const d = await navigator.mediaDevices.enumerateDevices();
    return d.map(x => ({kind: x.kind, hasDeviceId: !!x.deviceId,
                        hasGroupId: !!x.groupId, labelLen: (x.label || '').length}));
  });

  await safe('plugins', async () => ({
    pluginCount: navigator.plugins.length,
    mimeTypeCount: navigator.mimeTypes.length,
    isPluginArray: Object.prototype.toString.call(navigator.plugins) === '[object PluginArray]',
    names: Array.from(navigator.plugins).map(p => p.name),
  }));

  await safe('permissions', async () => {
    const st = await navigator.permissions.query({name: 'notifications'});
    return {notificationPermission: Notification.permission, permissionsState: st.state};
  });

  await safe('webrtc', async () => {
    // Private-IP leak check. Chrome's mDNS obfuscation should mean host
    // candidates come back as *.local, never a real 10./172./192.168. address.
    const pc = new RTCPeerConnection({iceServers: [{urls: 'stun:stun.l.google.com:19302'}]});
    pc.createDataChannel('x');
    const cands = [];
    await pc.setLocalDescription(await pc.createOffer());
    await new Promise(res => {
      const done = () => { pc.close(); res(); };
      const t = setTimeout(done, 6000);
      pc.onicecandidate = e => {
        if (!e.candidate) { clearTimeout(t); done(); return; }
        cands.push(e.candidate.candidate);
      };
    });
    return cands;
  });

  await safe('chromeObj', async () => ({
    hasChrome: typeof window.chrome === 'object' && window.chrome !== null,
    hasRuntime: !!(window.chrome && window.chrome.runtime),
    hasCsi: !!(window.chrome && window.chrome.csi),
    hasLoadTimes: !!(window.chrome && window.chrome.loadTimes),
  }));

  await safe('fonts', async () => {
    const probe = ['Arial', 'Times New Roman', 'Courier New', 'Helvetica Neue',
                   'San Francisco', 'Menlo', 'Liberation Sans', 'DejaVu Sans',
                   'Segoe UI', 'Ubuntu'];
    const r = {};
    for (const f of probe) { try { r[f] = document.fonts.check('12px "' + f + '"'); } catch (e) { r[f] = null; } }
    return r;
  });

  return out;
})()
"""

# Runs in the page so it goes out through aw-app-proxy — i.e. it reports the
# browser's REAL exit IP, which is the whole point of comparing it to Intl.
EXIT_GEO_JS = r"""
(async () => {
  const tries = [
    ['ipapi.co', 'https://ipapi.co/json/', j => ({ip: j.ip, tz: j.timezone, city: j.city, asn: j.asn, org: j.org})],
    ['ip-api.com', 'http://ip-api.com/json/', j => ({ip: j.query, tz: j.timezone, city: j.city, asn: j.as, org: j.isp})],
  ];
  for (const [name, url, pick] of tries) {
    try {
      const r = await fetch(url, {cache: 'no-store'});
      if (!r.ok) continue;
      const j = await r.json();
      const v = pick(j);
      if (v.tz) { v.source = name; return v; }
    } catch (e) { /* next */ }
  }
  return null;
})()
"""

PASS, FAIL, UNAVAILABLE = "PASS", "FAIL", "DETECTOR-UNAVAILABLE"


def _ok(name, detail, evidence=None, gating=True):
    return {"name": name, "verdict": PASS, "detail": detail,
            "evidence": evidence, "gating": gating}


def _bad(name, detail, evidence=None, gating=True):
    return {"name": name, "verdict": FAIL, "detail": detail,
            "evidence": evidence, "gating": gating}


def _na(name, detail, evidence=None, gating=True):
    return {"name": name, "verdict": UNAVAILABLE, "detail": detail,
            "evidence": evidence, "gating": gating}


def _ua_family(ua: str) -> str:
    u = (ua or "").lower()
    if "macintosh" in u or "mac os x" in u:
        return "mac"
    if "windows nt" in u:
        return "windows"
    if "x11" in u or "linux" in u:
        return "linux"
    return "unknown"


def _renderer_family(renderer: str) -> str:
    r = (renderer or "").lower()
    if "apple" in r or "metal" in r:
        return "mac"
    if "swiftshader" in r or "llvmpipe" in r or "mesa" in r:
        return "software-gl"
    if "direct3d" in r or "d3d11" in r:
        return "windows"
    return "unknown"


def assess(raw: dict, geo: dict | None) -> list[dict]:
    """Turn the collected facts into assertions. Each returns PASS / FAIL /
    DETECTOR-UNAVAILABLE (the last one only when we genuinely could not
    measure, e.g. both geo APIs were unreachable)."""
    res: list[dict] = []
    nav = raw.get("nav") or {}
    uad = raw.get("uaData") or {}
    intl = raw.get("intl") or {}
    gl = raw.get("webgl") or {}

    # ── 1. deviceMemory ≤ 8 ────────────────────────────────────────────────
    # Careful with the claim here. This container's Chromium 151 reports 32
    # NATIVELY — measured with the override daemon killed, 2026-09-03 — so 32
    # is not "impossible", it is what this build does. It is still an outlier:
    # Chrome's Device Memory implementation has clamped the reported value at
    # 8 GiB since 2017, so the overwhelming majority of the Chrome population
    # reports <= 8 and anything above stands out. That, not impossibility, is
    # the reason to clamp it.
    dm = nav.get("deviceMemory")
    if dm is None:
        res.append(_bad("deviceMemory", "navigator.deviceMemory is undefined "
                                        "(Chrome always exposes it)"))
    elif dm > 8:
        res.append(_bad("deviceMemory",
                        f"deviceMemory={dm} — above the 8 GiB clamp virtually "
                        f"every real Chrome reports, so it is a population "
                        f"outlier", dm))
    elif dm not in (0.25, 0.5, 1, 2, 4, 8):
        res.append(_bad("deviceMemory",
                        f"deviceMemory={dm} is not one of the spec's discrete "
                        f"steps (0.25/0.5/1/2/4/8)", dm))
    else:
        res.append(_ok("deviceMemory", f"deviceMemory={dm}", dm))

    # ── 2. Intl timezone vs the exit IP's geolocation ──────────────────────
    tz = intl.get("timeZone")
    if not geo or not geo.get("tz"):
        res.append(_na("timezone-vs-exit-ip",
                       "no geolocation API reachable from the browser — "
                       "cannot compare", {"browserTimeZone": tz}))
    elif not tz:
        res.append(_bad("timezone-vs-exit-ip", "browser reports no timezone", geo))
    elif tz == geo["tz"]:
        res.append(_ok("timezone-vs-exit-ip",
                       f"browser {tz} == exit IP {geo['ip']} ({geo['tz']})", geo))
    else:
        res.append(_bad("timezone-vs-exit-ip",
                        f"browser reports {tz} but exit IP {geo['ip']} "
                        f"geolocates to {geo['tz']} ({geo.get('city')}, "
                        f"{geo.get('org')}) — CreepJS and iphey compare these "
                        f"directly",
                        {"browserTimeZone": tz, "geo": geo}))

    # ── 3. enumerateDevices() shape ────────────────────────────────────────
    # Non-gating on purpose: the PO put this at medium priority because an
    # empty device list is defensible on a Linux box with no audio hardware,
    # and asked for it to be patched only if a Tier A/B detector actually
    # flags it. It is asserted so the fact stays visible, not so it can fail
    # the run on its own.
    md = raw.get("mediaDevices")
    if md is None:
        res.append(_bad("enumerateDevices", "navigator.mediaDevices missing",
                        gating=False))
    elif isinstance(md, dict):
        res.append(_na("enumerateDevices", f"probe errored: {md.get('error')}",
                       gating=False))
    elif not md:
        res.append(_bad("enumerateDevices",
                        "enumerateDevices() returns [] — defensible on a Linux "
                        "container with no audio hardware, so non-gating; "
                        "patch only if a Tier A/B detector flags it",
                        md, gating=False))
    else:
        kinds = {d["kind"] for d in md}
        missing = {"audioinput", "audiooutput"} - kinds
        if missing:
            res.append(_bad("enumerateDevices",
                            f"no {'/'.join(sorted(missing))} device reported",
                            md, gating=False))
        else:
            res.append(_ok("enumerateDevices",
                           f"{len(md)} devices, kinds={sorted(kinds)}", md,
                           gating=False))

    # ── 4. WebGL extensions / MAX_TEXTURE_SIZE vs the claimed renderer ─────
    if not gl:
        res.append(_na("webgl-vs-renderer", "no WebGL context available"))
    else:
        renderer = gl.get("unmaskedRenderer") or gl.get("renderer") or ""
        fam = _renderer_family(renderer)
        n = gl.get("extensionCount") or 0
        mts = gl.get("maxTextureSize")
        nv = [e for e in (gl.get("extensions") or []) if e.startswith("NV_")]
        ev = {"renderer": renderer, "extensionCount": n, "maxTextureSize": mts,
              "nvExtensions": nv}
        if fam == "mac":
            problems = []
            if n < 40:
                problems.append(f"only {n} extensions (Apple Metal ANGLE "
                                f"exposes ~48-50)")
            if nv:
                problems.append(f"exposes NVIDIA-only extensions {nv} which "
                                f"Apple Metal cannot")
            if mts and mts < 16384:
                problems.append(f"MAX_TEXTURE_SIZE={mts} (Apple silicon "
                                f"reports 16384)")
            if problems:
                res.append(_bad("webgl-vs-renderer",
                                f"renderer claims Apple/Metal but: "
                                + "; ".join(problems), ev))
            else:
                res.append(_ok("webgl-vs-renderer",
                               "Apple/Metal claim is internally consistent", ev))
        elif fam == "software-gl":
            res.append(_ok("webgl-vs-renderer",
                           f"renderer honestly reports software GL "
                           f"({n} extensions, MAX_TEXTURE_SIZE={mts})", ev))
        else:
            res.append(_na("webgl-vs-renderer",
                           f"unrecognised renderer family for {renderer!r}", ev))

    # ── 5. UA vs userAgentData vs navigator.platform ───────────────────────
    ua_fam = _ua_family(nav.get("userAgent", ""))
    uad_plat = (uad.get("platform") or "").lower() if uad else None
    uad_fam = {"macos": "mac", "windows": "windows",
               "linux": "linux"}.get(uad_plat, "unknown") if uad_plat else None
    nav_plat = nav.get("platform", "")
    nav_fam = ("mac" if "Mac" in nav_plat else
               "windows" if "Win" in nav_plat else
               "linux" if "Linux" in nav_plat else "unknown")
    ev = {"userAgent": nav.get("userAgent"), "uaDataPlatform": uad.get("platform"),
          "navigatorPlatform": nav_plat,
          "highEntropyPlatform": (uad.get("highEntropy") or {}).get("platform")}
    fams = {f for f in (ua_fam, uad_fam, nav_fam) if f and f != "unknown"}
    if len(fams) > 1:
        res.append(_bad("ua-vs-uadata-platform",
                        f"platform families disagree: UA={ua_fam}, "
                        f"userAgentData={uad_fam}, navigator.platform={nav_fam}",
                        ev))
    elif not fams:
        res.append(_na("ua-vs-uadata-platform", "no platform family detected", ev))
    else:
        res.append(_ok("ua-vs-uadata-platform",
                       f"UA, userAgentData and navigator.platform all say "
                       f"{fams.pop()}", ev))

    # ── 6. renderer family vs claimed OS ───────────────────────────────────
    # The one the Architect measured five ways: a Mac UA over software GL.
    if gl and ua_fam != "unknown":
        fam = _renderer_family(gl.get("unmaskedRenderer") or gl.get("renderer") or "")
        if fam == "software-gl" and ua_fam == "mac":
            res.append(_bad("os-vs-gpu",
                            "UA claims macOS but the GPU is software GL "
                            "(SwiftShader/Mesa) — no Mac ships without a real GPU",
                            {"ua": ua_fam, "renderer": gl.get("unmaskedRenderer")}))
        elif fam != "unknown" and fam != "software-gl" and fam != ua_fam:
            res.append(_bad("os-vs-gpu",
                            f"UA claims {ua_fam} but renderer family is {fam}",
                            {"ua": ua_fam, "renderer": gl.get("unmaskedRenderer")}))
        else:
            res.append(_ok("os-vs-gpu",
                           f"UA family {ua_fam} is compatible with renderer "
                           f"family {fam}",
                           {"renderer": gl.get("unmaskedRenderer")}))

    # ── 6b. screen vs the window it is supposed to contain ─────────────────
    # Added after iphey flagged "inconsistent browser fingerprint (pineapple)"
    # on 2026-09-03: the daemon reported screen 1920x1080 while Chrome runs
    # --start-maximized in a 1504x846 Xvfb, so the claimed panel was 416x234
    # larger than the maximised window filling it. Nothing in this file looked
    # at the two together, so the only thing that ever noticed was a detector.
    #
    # NON-GATING, and deliberately so: a real user's window is routinely far
    # smaller than their screen, so a gap is not by itself evidence of
    # anything. What it does is put both numbers in the summary side by side,
    # where a fabricated screen is obvious. A window LARGER than its screen is
    # a different matter — that is impossible on real hardware, and it fails.
    sc = raw.get("screen") or {}
    sw, sh = sc.get("width"), sc.get("height")
    ow, oh = sc.get("outerWidth"), sc.get("outerHeight")
    if not all(isinstance(v, (int, float)) for v in (sw, sh, ow, oh)):
        res.append(_na("screen-vs-window", "screen or window metrics missing",
                       sc, gating=False))
    elif ow > sw or oh > sh:
        res.append(_bad("screen-vs-window",
                        f"window {ow}x{oh} is LARGER than the screen "
                        f"{sw}x{sh} — impossible on real hardware", sc))
    else:
        gap = (sw - ow, sh - oh)
        res.append(_ok("screen-vs-window",
                       f"screen {sw}x{sh}, window {ow}x{oh} "
                       f"(unused {gap[0]}x{gap[1]}px)", sc, gating=False))

    # ── 7-9. the three the Architect measured clean — asserted, not patched ─
    cands = raw.get("webrtc")
    if not isinstance(cands, list):
        res.append(_na("webrtc-private-ip", "WebRTC probe did not complete", cands))
    else:
        private = [c for c in cands
                   if any(f" {p}" in c for p in ("10.", "192.168.", "172.16.",
                                                 "172.17.", "172.18.", "172.19."))]
        if private:
            res.append(_bad("webrtc-private-ip",
                            f"ICE candidates leak private addresses: {private}",
                            cands))
        else:
            res.append(_ok("webrtc-private-ip",
                           f"{len(cands)} ICE candidates, no private address",
                           cands))

    pl = raw.get("plugins") or {}
    if pl.get("pluginCount", 0) >= 3 and pl.get("isPluginArray"):
        res.append(_ok("plugins-shape",
                       f"{pl['pluginCount']} plugins / {pl.get('mimeTypeCount')} "
                       f"mimeTypes, PluginArray intact", pl))
    else:
        res.append(_bad("plugins-shape",
                        f"plugin array looks wrong: {pl}", pl))

    perm = raw.get("permissions") or {}
    if perm.get("notificationPermission") == "denied" and \
            perm.get("permissionsState") == "prompt":
        res.append(_bad("permissions-coherence",
                        "Notification.permission=denied while Permissions API "
                        "says prompt — the classic headless mismatch", perm))
    elif perm.get("notificationPermission"):
        res.append(_ok("permissions-coherence",
                       f"Notification.permission="
                       f"{perm['notificationPermission']}, Permissions API="
                       f"{perm.get('permissionsState')}", perm))
    else:
        res.append(_na("permissions-coherence", "could not read permissions", perm))

    return res
