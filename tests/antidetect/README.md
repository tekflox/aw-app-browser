# Anti-detection battery

Answers one question, repeatably: **is this browser detectable as an automation
browser?**

```bash
python3 tests/antidetect/run_battery.py
```

That is the whole interface. It drives the *running* `aw-app-browser` container
over raw CDP and writes every verdict, the evidence behind it, and one
screenshot per detector to

```
.tmp/antidetect/<UTC timestamp>/
    verdicts.json    every verdict + raw evidence
    summary.txt      the same thing, readable
    <site-id>.png    one screenshot per detector
```

Useful flags: `--tier A`, `--only sannysoft,creepjs`, `--skip-sites`
(coherence + side-channel + coverage only, ~40s), `--label before|after`.

Exit code is 0 unless a **Tier A** detector or a **gating** coherence
assertion failed.

## What it checks

**Detector sites,** in three deliberately unequal tiers (from the PO's scope):

| Tier | Sites | Weight |
|---|---|---|
| A | bot.sannysoft.com, arh.antoinevastel.com/bots/areyouheadless, deviceandbrowserinfo.com/are_you_a_bot | gate the run |
| B | CreepJS, browserleaks webrtc + javascript, iphey | judgment; evidence required, never fails the run |
| C | pixelscan, fingerprint.com bot demo | informational; **cannot** block — they weight IP/ASN, which this work does not change |

Every site resolves to exactly one of `PASS` / `FAIL` / `DETECTOR-UNAVAILABLE`.
That third outcome is not decoration: `arh.antoinevastel.com` was returning 502
during design, and folding "the detector is down" into "we failed" would send
someone off patching a fingerprint nobody measured. Navigation status decides
it — an HTTP 4xx/5xx or a failed load is never reported as a FAIL.

**Coherence assertions** (`coherence.py`) — pure JS, no external site, and the
part that actually explains *why* a detector dislikes us: `deviceMemory`,
Intl timezone vs the exit IP's geolocation, `enumerateDevices()` shape, WebGL
extension count / `MAX_TEXTURE_SIZE` against the claimed renderer, UA vs
`userAgentData` vs `navigator.platform`, plus the three the design pass
measured clean (WebRTC private-IP leak, plugins/mimeTypes shape,
Permissions/`Notification.permission`) so a regression there shows up as a
failing assertion instead of silence.

**Daemon coverage** (`coverage.py`) — proves `platform-override.py` reaches
targets other CDP clients *create*, not just ones they reuse. It exercises the
real creation paths: `PUT /json/new` (exactly what `aw-app-devctl/devctl_app/
cdp.py:95` and `aw-app-mini-browser/mini_browser_app/cdp.py:95` call) and
`Target.createTarget` over the browser session, then compares each resulting
fingerprint field-by-field against a known-patched reference page.

**CDP side-channel** (`sidechannel.py`) — see below.

## Three things this harness deliberately does NOT do

**It does not use the devctl / mini-browser / playwright MCP screenshot
tools.** `devctl_browser__browser_screenshot` returns `/tmp/devctl/shot-*.png`,
which lives inside *devctl's* container and does not exist on the workspace
filesystem. You get a plausible path and no attachable file. This harness calls
`Page.captureScreenshot` and decodes the base64 itself.

**It does not write to `/tmp`.** `/tmp` is process scratch, invisible to other
containers and gone on restart. `.tmp/` is inside the host-mounted workspace
tree, so the evidence is readable from the host and from any sibling container
(see the repo's `CLAUDE.md`).

**It never enables the CDP `Runtime` domain during normal measurement** — only
`Page` and `Network`. Enabling `Runtime` creates the very console side-channel
the battery is trying to measure.

## The side-channel measurement, and why it says "do not patch"

The probe for "is a CDP client watching the console?" is itself a CDP client.
The design pass hit this: it saw `errStackToStringLeak=1` and could not tell
how much was its own instrument.

`sidechannel.py` measures three ways — attached with `Runtime` *not* enabled;
fully **detached** (the page arms a `setTimeout`, the harness closes its
WebSocket entirely, and the result is stashed in `localStorage` for a later
read-back); and attached with `Runtime` deliberately enabled as a positive
control.

Measured 2026-09-03 on Chromium 151, all three legs agree: `stackGetter=0`,
`objPreview=0`, `errToString=1`. Modern Chrome does not invoke accessors when
building console object previews, so the classic probes no longer fire at all.

`errToString` was then checked against a **zero-CDP-client** control — override
daemon killed, harness disconnected, deferred probe fired with literally
nothing attached — and it still came back 1. So `console.debug(err)` touches
`Error.prototype.toString` unconditionally in this Chrome; it fires identically
whether or not anything is listening, and therefore distinguishes nothing.

Independently corroborated by deviceandbrowserinfo.com, which reports
`isAutomatedWithCDP: false` and `isAutomatedWithCDPInWebWorker: false`.

**Conclusion: there is no observable CDP side-channel here, and it must not be
patched.** To redo the zero-client control (needs podman on the workspace
host):

```bash
podman exec aw-app-browser sh -c 'for p in $(pgrep -f "[p]latform-override"); do kill $p; done'
# arm a deferred probe, disconnect, wait for it to fire, re-attach, read localStorage
podman exec -d aw-app-browser sh -c 'exec python3 /opt/aw-browser/platform-override.py'
```

## Iterating on a fix

`container/platform-override.py` is `COPY`'d into the image
(`container/Dockerfile:44`), so **editing the repo file does not change the
running container.** For iteration, copy it in and restart the daemon — seconds
rather than a rebuild:

```bash
podman cp container/platform-override.py aw-app-browser:/opt/aw-browser/platform-override.py
podman exec aw-app-browser sh -c 'for p in $(pgrep -f "[p]latform-override"); do kill $p; done'
podman exec -d aw-app-browser sh -c 'exec python3 /opt/aw-browser/platform-override.py'
python3 tests/antidetect/run_battery.py --label after
```

Note this only exercises the daemon. Changes to `entrypoint-lite.sh` (the `TZ`
resolution, the Chrome flags) need a real container recreation to take effect.

The app manifest pins `ghcr.io/tekflox/aw-app-browser:latest`, so a green CI run
does **not** prove the fix is live. The daemon prints the sha256 of its own file
at startup — compare `podman logs aw-app-browser | grep 'platform-override.py build'`
with `sha256sum container/platform-override.py | cut -c1-16`.

## Requirements

Python 3 plus `websockets` (already present in the workspace, and what
`aw-app-devctl` uses). No new dependency. The container must be running and
reachable at `aw-app-browser:9223`.
