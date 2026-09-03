#!/usr/bin/env python3
"""Anti-detection battery for the aw-app-browser container — one command.

    python3 tests/antidetect/run_battery.py

Drives the RUNNING container over raw CDP (`aw-app-browser:9223`, via
container/cdp_proxy.py) and writes everything it saw to

    $AW_WORKSPACE_CONTAINER_DIR/.tmp/antidetect/<UTC timestamp>/
        verdicts.json      every verdict, with the raw evidence behind it
        summary.txt        the same thing, readable
        <site-id>.png      one screenshot per detector

`.tmp/` and not `/tmp/`: `.tmp/` lives inside the host-mounted workspace tree,
so a screenshot written here is visible from the host and from every sibling
container. `/tmp` is process scratch and vanishes (see CLAUDE.md). The same
reason rules out the devctl/playwright MCP screenshot tools, whose PNG lands
inside *their* container — this harness decodes Page.captureScreenshot itself.

Options:
    --tier A[,B,C]   only run these tiers (default: all)
    --only id[,id]   only run these site ids
    --skip-sites     coherence + side-channel + coverage only, no detectors
    --label TEXT     tag the run (e.g. --label before / --label after)

Exit codes:
    0  GREEN        — everything that gates the run was measured and passed
    1  RED          — a Tier A detector or a gating coherence assertion FAILED
    2  INCONCLUSIVE — nothing failed, but something gating was never measured

That third state matters. "Nothing failed" and "we checked" are different
claims, and collapsing them is how a run that measured nothing reads as a
pass. Tier B is reported loudly but never fails the run (it is judgment), and
Tier C cannot block anything at all — it weights IP/ASN reputation, which this
card explicitly does not change.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import coherence          # noqa: E402
import coverage           # noqa: E402
import sidechannel        # noqa: E402
import sites              # noqa: E402
from cdp import close_target, http_json, open_page  # noqa: E402

WORKSPACE = Path(os.environ.get("AW_WORKSPACE_CONTAINER_DIR", "/opt/aw-workspace"))
OUT_ROOT = WORKSPACE / ".tmp" / "antidetect"

PASS, FAIL, UNAVAILABLE, UNKNOWN = (
    sites.PASS, sites.FAIL, sites.UNAVAILABLE, sites.UNKNOWN)

_MARK = {PASS: "PASS", FAIL: "FAIL", UNAVAILABLE: "N/A ", UNKNOWN: "????"}


def log(msg: str) -> None:
    print(msg, flush=True)


async def run_site(site: dict, out_dir: Path) -> dict:
    """Navigate one detector, screenshot it, extract its verdict."""
    log(f"  [{site['tier']}] {site['id']:24s} {site['url']}")
    rec = {"id": site["id"], "tier": site["tier"], "url": site["url"],
           "note": site.get("note")}
    session = target_id = None
    try:
        # Inside the try on purpose: opening the target can fail on a transient
        # DNS blip against aw-app-browser, and one flaky site must not take the
        # whole battery down with it.
        session, target_id = await open_page("about:blank")
        nav = await session.navigate(
            site["url"],
            load_timeout=site.get("load_timeout", 45.0),
            settle=site.get("settle", 3.0))

        # These detectors decide asynchronously and at wildly different speeds
        # — CreepJS takes the best part of a minute, sannysoft is instant, and
        # deviceandbrowserinfo varies run to run. A fixed settle either wastes
        # time or reads the page before it has decided, which showed up as
        # "no verdict rendered yet" on a site that had answered cleanly minutes
        # earlier. Poll the extractor instead and stop the moment it commits.
        extracted, deadline = None, asyncio.get_event_loop().time() + \
            site.get("max_wait", 60.0)
        while True:
            try:
                extracted = await session.evaluate(site["extract"])
            except Exception as e:
                rec["extractError"] = str(e)
            if extracted and extracted.get("verdict") in (PASS, FAIL):
                break
            if asyncio.get_event_loop().time() >= deadline:
                break
            await asyncio.sleep(3.0)

        rec["navigation"] = nav
        rec["screenshot"] = await session.screenshot(out_dir / f"{site['id']}.png")
        verdict, detail, evidence = sites.classify(nav, extracted)
        rec.update(verdict=verdict, detail=detail, evidence=evidence)
    except Exception as e:
        rec.update(verdict=UNAVAILABLE, detail=f"harness error: {e}",
                   evidence=None)
    finally:
        if session:
            await session.close()
        if target_id:
            close_target(target_id)
    log(f"       -> {_MARK.get(rec['verdict'], rec['verdict'])}  {rec['detail']}")
    return rec


async def run_coherence(out_dir: Path) -> dict:
    """Pure-JS coherence pass. Needs a real origin (fetch + WebRTC + fonts all
    behave differently on about:blank), so it runs on the probe origin."""
    log("  coherence assertions (pure JS, no detector site)")
    session, target_id = await open_page("about:blank")
    try:
        nav = await session.navigate("https://example.com/", settle=1.0)
        # Generous timeout: COLLECT_JS waits on ICE gathering (up to 6s) and
        # font/permission queries, and the default 30s expired mid-run once
        # while the proxy was flaky — which silently produced a battery with
        # zero coherence assertions.
        raw = await session.evaluate(coherence.COLLECT_JS, await_promise=True,
                                     timeout=90.0)
        try:
            geo = await session.evaluate(coherence.EXIT_GEO_JS,
                                         await_promise=True, timeout=45.0)
        except Exception as e:
            geo = None
            log(f"       exit-IP geolocation unavailable: {e}")
        await session.screenshot(out_dir / "coherence.png")
        results = coherence.assess(raw or {}, geo)
        for r in results:
            log(f"       {_MARK.get(r['verdict'], r['verdict'])}  "
                f"{r['name']}: {r['detail']}")
        return {"navigation": nav, "exitGeo": geo, "raw": raw,
                "assertions": results}
    finally:
        await session.close()
        close_target(target_id)


def write_summary(report: dict, path: Path) -> str:
    L: list[str] = []
    a = L.append
    a("=" * 78)
    a(f"aw-app-browser anti-detection battery — {report['startedAt']}")
    if report.get("label"):
        a(f"label: {report['label']}")
    a(f"browser: {report['browser'].get('Browser')}")
    a(f"UA (CDP-reported): {report['browser'].get('User-Agent')}")
    a("=" * 78)

    for tier, heading in (("A", "TIER A — gates the run"),
                          ("B", "TIER B — judgment, evidence required"),
                          ("C", "TIER C — informational, cannot block")):
        rows = [s for s in report["sites"] if s["tier"] == tier]
        if not rows:
            continue
        a("")
        a(heading)
        a("-" * 78)
        for s in rows:
            a(f"  {_MARK.get(s['verdict'], s['verdict'])}  {s['id']:24s} "
              f"{s['detail']}")
            if s.get("screenshot"):
                a(f"        screenshot: {s['screenshot']}")

    if report.get("coherence"):
        a("")
        a("COHERENCE ASSERTIONS (pure JS — gating ones fail the run)")
        a("-" * 78)
        for r in report["coherence"]["assertions"]:
            tag = "" if r.get("gating", True) else "  [non-gating]"
            a(f"  {_MARK.get(r['verdict'], r['verdict'])}  {r['name']:24s} "
              f"{r['detail']}{tag}")

    if report.get("sidechannel"):
        sc = report["sidechannel"]
        a("")
        a("CDP Runtime.enable SIDE-CHANNEL (detached measurement)")
        a("-" * 78)
        a(f"  attached, Runtime NOT enabled : {sc.get('attached_plain')}")
        a(f"  DETACHED (no session at all)  : {sc.get('detached')}")
        a(f"  attached, Runtime enabled     : {sc.get('attached_runtime_enabled')}")
        a(f"  -> {sc.get('conclusion')}")

    if report.get("coverage"):
        cv = report["coverage"]
        a("")
        a("DAEMON COVERAGE — targets other CDP clients CREATE")
        a("-" * 78)
        a(f"  {_MARK.get(cv['verdict'], cv['verdict'])}  {cv['detail']}")
        for p in cv.get("paths", []):
            a(f"        {'ok  ' if p.get('matchesReference') else 'MISS'} "
              f"{p['path']:22s} {p['used_by']}")

    v = report["verdictSummary"]
    a("")
    a("=" * 78)
    a(f"RESULT: {report['result']}")
    a(f"  Tier A: {v['A']}")
    a(f"  Tier B: {v['B']}")
    a(f"  Tier C: {v['C']}")
    a(f"  coherence: {v['coherence']}")
    if report.get("unmeasured"):
        a(f"  NOT MEASURED: {', '.join(report['unmeasured'])}")
    a(f"artefacts: {report['outputDir']}")
    a("=" * 78)
    text = "\n".join(L)
    path.write_text(text)
    return text


async def main_async(args) -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = OUT_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "label": args.label,
        "outputDir": str(out_dir),
        "cdpEndpoint": "aw-app-browser:9223",
    }
    try:
        report["browser"] = http_json("/json/version")
    except Exception as e:
        log(f"FATAL: CDP endpoint aw-app-browser:9223 unreachable: {e}")
        return 2
    log(f"CDP: {report['browser'].get('Browser')} — writing to {out_dir}")

    tiers = [t.strip().upper() for t in args.tier.split(",") if t.strip()]
    only = {s.strip() for s in args.only.split(",") if s.strip()} if args.only else None

    report["sites"] = []
    if not args.skip_sites:
        log("\nDetector sites:")
        for site in sites.SITES:
            if site["tier"] not in tiers:
                continue
            if only and site["id"] not in only:
                continue
            report["sites"].append(await run_site(site, out_dir))

    log("\nCoherence:")
    report["coherence"] = {"error": "not attempted", "assertions": []}
    for attempt in (1, 2):
        try:
            report["coherence"] = await run_coherence(out_dir)
            break
        except Exception as e:
            log(f"  coherence pass failed to run (attempt {attempt}): {e}")
            report["coherence"] = {"error": str(e), "assertions": []}

    log("\nCDP side-channel (detached measurement — takes ~10s):")
    try:
        report["sidechannel"] = await sidechannel.measure()
        log(f"  {report['sidechannel']['conclusion']}")
    except Exception as e:
        log(f"  side-channel measurement failed: {e}")
        report["sidechannel"] = {"error": str(e),
                                 "conclusion": f"INCONCLUSIVE — {e}"}

    log("\nDaemon coverage:")
    try:
        report["coverage"] = await coverage.run()
        log(f"  {report['coverage']['verdict']}: {report['coverage']['detail']}")
    except Exception as e:
        log(f"  coverage check failed: {e}")
        report["coverage"] = {"verdict": UNKNOWN, "detail": f"error: {e}",
                              "paths": []}

    # ── tally ──────────────────────────────────────────────────────────────
    def tally(items, key="verdict"):
        c = {PASS: 0, FAIL: 0, UNAVAILABLE: 0, UNKNOWN: 0}
        for i in items:
            c[i.get(key, UNKNOWN)] = c.get(i.get(key, UNKNOWN), 0) + 1
        return (f"{c[PASS]} pass / {c[FAIL]} fail / {c[UNAVAILABLE]} unavailable"
                f" / {c[UNKNOWN]} unknown")

    by_tier = {t: [s for s in report["sites"] if s["tier"] == t]
               for t in ("A", "B", "C")}
    assertions = report.get("coherence", {}).get("assertions", [])
    report["verdictSummary"] = {
        "A": tally(by_tier["A"]), "B": tally(by_tier["B"]),
        "C": tally(by_tier["C"]), "coherence": tally(assertions),
    }

    blocking = ([s for s in by_tier["A"] if s["verdict"] == FAIL]
                + [a for a in assertions
                   if a["verdict"] == FAIL and a.get("gating", True)])
    if report.get("coverage", {}).get("verdict") == FAIL:
        blocking.append(report["coverage"])
    report["blocking"] = [b.get("id") or b.get("name") or "coverage"
                          for b in blocking]

    # A run that measured nothing must never read as GREEN. This bit the
    # BEFORE baseline: the coherence pass died on a CDP timeout and a Tier A
    # detector never rendered a verdict, and the battery still printed GREEN
    # because nothing had actively FAILED. "Nothing failed" and "we checked"
    # are different claims, and a false green here is exactly the failure mode
    # this card exists to stop.
    unmeasured = [s["id"] for s in by_tier["A"] if s["verdict"] == UNKNOWN]
    if not assertions:
        unmeasured.append("coherence-pass("
                          + str(report.get("coherence", {}).get("error"))
                          + ")")
    if report.get("coverage", {}).get("verdict") not in (PASS, FAIL):
        unmeasured.append("daemon-coverage")
    report["unmeasured"] = unmeasured

    unavailable = [s["id"] for s in by_tier["A"]
                   if s["verdict"] == UNAVAILABLE]
    report["tierAUnavailable"] = unavailable

    if blocking:
        report["result"] = "RED — " + ", ".join(report["blocking"])
        exit_code = 1
    elif unmeasured:
        report["result"] = ("INCONCLUSIVE — nothing failed, but these were "
                            "never measured: " + ", ".join(unmeasured))
        exit_code = 2
    else:
        report["result"] = "GREEN" + (
            f" (with {len(unavailable)} Tier A detector(s) down: "
            f"{', '.join(unavailable)} — not proven, just not failing)"
            if unavailable else "")
        exit_code = 0

    (out_dir / "verdicts.json").write_text(json.dumps(report, indent=2,
                                                      default=str))
    log("\n" + write_summary(report, out_dir / "summary.txt"))
    return exit_code


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tier", default="A,B,C")
    p.add_argument("--only", default=None)
    p.add_argument("--skip-sites", action="store_true")
    p.add_argument("--label", default=None)
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
