#!/usr/bin/env python3
"""The tiered detector battery.

Tiers come straight from the PO's scope on the Kanban card and are NOT equal:

  A  gates the run. Deterministic verdicts, a FAIL here fails the battery.
  B  judgment-with-evidence. Reported loudly, never fails the run on its own.
  C  informational only. These weight IP/ASN reputation, which this card
     explicitly does not change, so a Tier C FAIL *cannot* block anything.

Every site yields one of three outcomes, never two:

  PASS                  the detector ran and liked us
  FAIL                  the detector ran and flagged us
  DETECTOR-UNAVAILABLE  the detector itself is down / unreachable

That third one exists because arh.antoinevastel.com was returning 502 during
the design pass. Collapsing it into FAIL would have us "fixing" a fingerprint
that was never measured.
"""
from __future__ import annotations

PASS, FAIL, UNAVAILABLE, UNKNOWN = (
    "PASS", "FAIL", "DETECTOR-UNAVAILABLE", "UNKNOWN")


# ── extractors ─────────────────────────────────────────────────────────────
# Each is JS evaluated in the loaded page; each returns
# {verdict, detail, evidence} and never throws.

SANNYSOFT_JS = r"""
(() => {
  const rows = Array.from(document.querySelectorAll('table tr'));
  const red = [], all = {};
  for (const tr of rows) {
    const tds = tr.querySelectorAll('td');
    if (tds.length < 2) continue;
    const name = tds[0].innerText.trim();
    const val = tds[1].innerText.trim();
    if (!name) continue;
    all[name] = val;
    const cls = (tds[1].className || '') + ' ' + (tr.className || '');
    if (/\bfailed\b/.test(cls)) red.push({name, value: val});
  }
  const n = Object.keys(all).length;
  if (!n) return {verdict: 'UNKNOWN', detail: 'no result table found', evidence: null};
  return {
    verdict: red.length ? 'FAIL' : 'PASS',
    detail: red.length ? `${red.length} red row(s): ` + red.map(r => r.name).join(', ')
                       : `${n} rows, zero red`,
    evidence: {redRows: red, rowCount: n, rows: all},
  };
})()
"""

AREYOUHEADLESS_JS = r"""
(() => {
  const t = (document.body ? document.body.innerText : '').trim();
  if (!t) return {verdict: 'UNKNOWN', detail: 'empty body', evidence: null};
  if (/you are not (chrome )?headless/i.test(t))
    return {verdict: 'PASS', detail: t.slice(0, 200), evidence: t.slice(0, 500)};
  if (/you are (chrome )?headless/i.test(t))
    return {verdict: 'FAIL', detail: t.slice(0, 200), evidence: t.slice(0, 500)};
  return {verdict: 'UNKNOWN', detail: t.slice(0, 200), evidence: t.slice(0, 500)};
})()
"""

# This one is the most useful detector in the battery: it prints its whole
# decision as JSON ("Raw detection details"), so we read `isBot` and the named
# signals rather than scraping prose. Prose scraping got this wrong once
# already — the page's nav bar contains the words "Proxy Detection", which a
# looser regex happily matched.
DEVICEANDBROWSERINFO_JS = r"""
(() => {
  const t = (document.body ? document.body.innerText : '').trim();
  if (!t) return {verdict: 'UNKNOWN', detail: 'empty body', evidence: null};
  let parsed = null;
  const m = t.match(/\{[\s\S]*?"isBot"[\s\S]*?\n\}/);
  if (m) { try { parsed = JSON.parse(m[0]); } catch (e) {} }
  if (!parsed) {
    // Fall back to the prose verdict, negative pattern first.
    if (/you are not a bot/i.test(t))
      return {verdict: 'PASS', detail: 'prose verdict: not a bot (no JSON block)',
              evidence: t.slice(0, 2000)};
    if (/you are a bot/i.test(t))
      return {verdict: 'FAIL', detail: 'prose verdict: bot (no JSON block)',
              evidence: t.slice(0, 2000)};
    return {verdict: 'UNKNOWN', detail: 'no verdict rendered yet',
            evidence: t.slice(0, 2000)};
  }
  const flagged = Object.keys(parsed.details || {}).filter(k => parsed.details[k] === true);
  return {
    verdict: parsed.isBot ? 'FAIL' : 'PASS',
    detail: (parsed.isBot ? 'isBot=true' : 'isBot=false')
            + (flagged.length ? ' — signals: ' + flagged.join(', ')
                              : ' — no signal raised'),
    evidence: parsed,
  };
})()
"""

# CreepJS's metric, per the PO, is the `lies` count (target 0) plus the absence
# of any automation/headless flag. The trust score itself is explicitly NOT
# pass/fail.
#
# In practice the page's own "Headless" panel is the part that actually renders
# reliably, and it is more informative than the lies counter: it reports
# `N% stealth`, which is CreepJS's own measure of detected TAMPERING. That is
# the number this card cares about — a stealth score above 0 means our patches
# are being caught, which is precisely the failure mode an over-eager spoof
# produces.
CREEPJS_JS = r"""
(() => {
  const t = (document.body ? document.body.innerText : '');
  if (!t.trim()) return {verdict: 'UNKNOWN', detail: 'empty body', evidence: null};

  const num = re => { const m = t.match(re); return m ? parseFloat(m[1]) : null; };
  const stealth      = num(/([\d.]+)%\s*stealth/i);
  const headless     = num(/([\d.]+)%\s*headless/i);
  const likeHeadless = num(/([\d.]+)%\s*like headless/i);
  const liesM = t.match(/lies\s*\((\d+)\)/i) || t.match(/(\d+)\s*lies\b/i);
  const lies = liesM ? parseInt(liesM[1], 10) : null;
  const chromium = /chromium:\s*true/i.test(t);
  const tz = (t.match(/([A-Za-z]+\/[A-Za-z_]+)\s*\((-?\d+)\)/) || [])[0] || null;

  const ev = {lies, stealthPct: stealth, headlessPct: headless,
              likeHeadlessPct: likeHeadless, chromium,
              workerTimezone: tz, text: t.slice(0, 6000)};

  if (stealth === null && lies === null)
    return {verdict: 'UNKNOWN', detail: 'neither a lies count nor a stealth score rendered yet',
            evidence: ev};

  // Fail on evidence of tampering being DETECTED, not on the trust score.
  const bad = (lies !== null && lies > 0) || (stealth !== null && stealth > 0);
  return {
    verdict: bad ? 'FAIL' : 'PASS',
    detail: `lies=${lies}, stealth=${stealth}%, headless=${headless}%, `
          + `likeHeadless=${likeHeadless}%, chromium=${chromium}`,
    evidence: ev,
  };
})()
"""

BROWSERLEAKS_WEBRTC_JS = r"""
(() => {
  const t = (document.body ? document.body.innerText : '');
  if (!t.trim()) return {verdict: 'UNKNOWN', detail: 'empty body', evidence: null};
  const ips = Array.from(new Set(t.match(/\b\d{1,3}(?:\.\d{1,3}){3}\b/g) || []));
  const priv = ips.filter(ip => /^10\./.test(ip) || /^192\.168\./.test(ip) ||
                                /^172\.(1[6-9]|2\d|3[01])\./.test(ip));
  return {
    verdict: priv.length ? 'FAIL' : 'PASS',
    detail: priv.length ? `private IP leaked: ${priv.join(', ')}`
                        : `no private IP among ${ips.length} address(es)`,
    evidence: {allIps: ips, privateIps: priv},
  };
})()
"""

BROWSERLEAKS_JS_JS = r"""
(() => {
  const t = (document.body ? document.body.innerText : '');
  if (!t.trim()) return {verdict: 'UNKNOWN', detail: 'empty body', evidence: null};
  // Contradiction check, not a scraped verdict: does what the page read back
  // agree with itself on the platform?
  const mac = /mac ?os|macintosh|macintel/i.test(t);
  const lin = /linux|x11/i.test(t);
  const win = /windows nt/i.test(t);
  const n = [mac, lin, win].filter(Boolean).length;
  return {
    verdict: n > 1 ? 'FAIL' : (n === 1 ? 'PASS' : 'UNKNOWN'),
    detail: n > 1 ? `page shows more than one OS family (mac=${mac} linux=${lin} windows=${win})`
                  : `single OS family (mac=${mac} linux=${lin} windows=${win})`,
    evidence: {mac, linux: lin, windows: win, text: t.slice(0, 4000)},
  };
})()
"""

# Read iphey's OWN verdict node, never the page prose.
#
# The previous version tested `/trustworthy/i` against innerText to mean PASS.
# iphey prints "How trustworthy is your identity" as the static caption under
# the MX Score tile on every single load, pass or fail, so that regex matched
# unconditionally — the check could only ever have failed on the literal string
# "not trustworthy", which iphey does not use. It reported PASS on a run whose
# own screenshot shows the red "Unreliable" headline (QA, 2026-09-03). A Tier B
# check that cannot fail is worse than no check: it reads as evidence.
#
# What iphey actually renders, both states measured live on 2026-09-03:
#   #hero-status   class "hero-status--bad",  text "Unreliable"   (flagged)
#                  class "hero-status--good", text "Trustworthy"  (clean)
#   a.code-block.<name>-tile   one per section; a flagged one carries the
#                              `code-block--error` modifier
#   .detail-entry              the individual signals, e.g. "browser Detected
#                              an inconsistent browser fingerprint (pineapple)"
#
# The MODIFIER CLASS is the primary signal, not the wording. The wording is not
# the antonym pair it looks like — the good headline is "Trustworthy", not
# "Reliable" — and an earlier revision of this check keyed on Reliable/
# Unreliable and so could never report PASS on a genuine pass. Keying on
# iphey's own --good/--bad modifier avoids guessing its vocabulary; the text is
# read as a fallback and kept as evidence.
#
# Both the headline and the tiles are read, and they have to agree to produce a
# PASS. UNKNOWN — not PASS — is returned until the verdict node exists at all,
# so a page that never finished deciding keeps the poller waiting and then ends
# the run as UNKNOWN rather than as a pass nobody measured.
IPHEY_JS = r"""
(() => {
  const t = (document.body ? document.body.innerText : '');
  if (!t.trim()) return {verdict: 'UNKNOWN', detail: 'empty body', evidence: null};

  const tiles = Array.from(document.querySelectorAll('a[class*="code-block"]'))
    .map(el => ({
      name: (el.className.match(/([a-z]+)-tile/) || [])[1] || null,
      error: /code-block--error/.test(el.className),
      cls: el.className,
      text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 120),
    }));
  const badTiles = tiles.filter(x => x.error).map(x => x.name || x.cls);

  // "status Not detected" is the CLEAN row — excluded, or a passing run would
  // file its own all-clear as a raised signal.
  const signals = Array.from(document.querySelectorAll('.detail-entry'))
    .map(el => ({
      cls: el.className,
      text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 300),
    }))
    .filter(x => /detected|inconsisten|mismatch|spoof/i.test(x.text)
                 && !/not detected/i.test(x.text));

  const el = document.getElementById('hero-status');
  const heroClass = el ? el.className : null;
  const headline = el ? (el.textContent || '').trim()
                      : ((t.match(/Looks\s+([A-Za-z]+)/i) || [])[1] || null);
  const mx = (t.match(/(\d+)\s*MX SCORE/i) || [])[1] || null;

  const ev = {headline, heroClass, mxScore: mx, tiles, signals,
              text: t.slice(0, 3000)};

  // iphey's own modifier class first; the wording only as a fallback. Note
  // "Unreliable" contains "reliable", so the negative is always tested first.
  let bad  = heroClass ? /hero-status--bad/.test(heroClass)  : false;
  let good = heroClass ? /hero-status--good/.test(heroClass) : false;
  if (!bad && !good && headline) {
    bad  = /^un(reliable|trustworthy)$/i.test(headline);
    good = /^(trustworthy|reliable)$/i.test(headline);
  }

  if (!headline && !tiles.length)
    return {verdict: 'UNKNOWN', detail: 'verdict has not rendered yet', evidence: ev};

  const sigText = signals.length ? ' — ' + signals.map(s => s.text).join('; ') : '';
  if (bad || badTiles.length)
    return {verdict: 'FAIL',
            detail: `iphey: "${headline}", MX ${mx}, flagged: `
                  + (badTiles.join(', ') || 'none named') + sigText,
            evidence: ev};
  if (good)
    return {verdict: 'PASS',
            detail: `iphey: "${headline}", MX ${mx}, no tile flagged`,
            evidence: ev};
  return {verdict: 'UNKNOWN',
          detail: `no headline verdict yet (${tiles.length} tile(s) rendered, none flagged)`,
          evidence: ev};
})()
"""

GENERIC_TEXT_JS = r"""
(() => {
  const t = (document.body ? document.body.innerText : '').trim();
  if (!t) return {verdict: 'UNKNOWN', detail: 'empty body', evidence: null};
  return {verdict: 'UNKNOWN', detail: t.slice(0, 300).replace(/\s+/g, ' '),
          evidence: t.slice(0, 4000)};
})()
"""


SITES = [
    # `settle` = pause before the first read; `max_wait` = how long to keep
    # re-reading until the detector commits to PASS/FAIL. Polling stops the
    # moment it does, so the fast sites cost their settle and no more.
    # ── Tier A — these gate the run ────────────────────────────────────────
    {"id": "sannysoft", "tier": "A",
     "url": "https://bot.sannysoft.com/",
     "settle": 3.0, "max_wait": 30.0, "extract": SANNYSOFT_JS,
     "note": "no red rows"},
    {"id": "areyouheadless", "tier": "A",
     "url": "https://arh.antoinevastel.com/bots/areyouheadless",
     "settle": 3.0, "max_wait": 20.0, "extract": AREYOUHEADLESS_JS,
     "note": "'You are not Chrome headless' — was 502 during design, expect "
             "DETECTOR-UNAVAILABLE if still down"},
    {"id": "deviceandbrowserinfo", "tier": "A",
     "url": "https://deviceandbrowserinfo.com/are_you_a_bot",
     "settle": 5.0, "max_wait": 75.0, "load_timeout": 60.0,
     "extract": DEVICEANDBROWSERINFO_JS,
     "note": "isBot must be false; it names the signals it raised"},

    # ── Tier B — judgment, evidence required, never fails the run ──────────
    {"id": "creepjs", "tier": "B",
     "url": "https://abrahamjuliot.github.io/creepjs/",
     "settle": 10.0, "max_wait": 110.0, "load_timeout": 120.0,
     "extract": CREEPJS_JS,
     "note": "lies count → 0; trust score is NOT the metric"},
    {"id": "browserleaks-webrtc", "tier": "B",
     "url": "https://browserleaks.com/webrtc",
     "settle": 6.0, "max_wait": 40.0, "extract": BROWSERLEAKS_WEBRTC_JS,
     "note": "must not leak the container's private IP"},
    {"id": "browserleaks-javascript", "tier": "B",
     "url": "https://browserleaks.com/javascript",
     "settle": 5.0, "max_wait": 30.0, "extract": BROWSERLEAKS_JS_JS,
     "note": "no platform self-contradiction"},
    {"id": "iphey", "tier": "B",
     "url": "https://iphey.com/",
     "settle": 6.0, "max_wait": 50.0, "extract": IPHEY_JS,
     "note": "headline must read 'Reliable' and no section tile may carry "
             "code-block--error"},

    # ── Tier C — informational, CANNOT block ───────────────────────────────
    # These have no machine-readable verdict, so their extractor always
    # returns UNKNOWN and polling would just burn max_wait — keep it short and
    # rely on the screenshot.
    {"id": "pixelscan", "tier": "C",
     "url": "https://pixelscan.net/",
     "settle": 20.0, "max_wait": 20.0, "load_timeout": 90.0,
     "extract": GENERIC_TEXT_JS,
     "note": "weights IP/ASN — out of scope, record only"},
    {"id": "fingerprintjs-bot-demo", "tier": "C",
     "url": "https://fingerprint.com/products/bot-detection/",
     "settle": 12.0, "max_wait": 12.0, "extract": GENERIC_TEXT_JS,
     "note": "commercial bot demo — record only"},
]


def classify(nav: dict, extracted: dict | None) -> tuple[str, str, object]:
    """Fold the navigation result and the extractor output into one of the
    three outcomes.

    A definite extractor verdict wins over navigation noise. That ordering is
    deliberate: the detector reporting `isBot: false` is proof the page
    rendered and decided, whatever CDP thought of the main-document request —
    and Chrome does report net::ERR_ABORTED for main documents that in fact
    loaded fine (redirect chains, a navigation superseded by the page's own).
    Treating that as DETECTOR-UNAVAILABLE threw away a real, already-measured
    verdict.

    Navigation status still decides everything else, which is what keeps a 502
    from being reported as a failure of ours."""
    if extracted and extracted.get("verdict") in (PASS, FAIL):
        return (extracted["verdict"], extracted.get("detail", ""),
                extracted.get("evidence"))

    status, error = nav.get("status"), nav.get("error")
    if status is not None and status >= 400:
        return UNAVAILABLE, f"detector returned HTTP {status}", {"nav": nav}
    if error:
        return UNAVAILABLE, f"navigation failed: {error}", {"nav": nav}
    if status is None and not extracted:
        return UNAVAILABLE, "no document response and nothing extracted", {"nav": nav}
    if not extracted:
        return UNKNOWN, "extractor returned nothing", {"nav": nav}
    return (extracted.get("verdict", UNKNOWN),
            extracted.get("detail", ""),
            extracted.get("evidence"))
