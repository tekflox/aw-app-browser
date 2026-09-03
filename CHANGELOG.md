# Change History

This file records user-facing Browser changes. Keep historical implementation
notes here instead of expanding the marketplace description.

## Unreleased

- **The browser now presents an honest Linux/Chromium fingerprint instead of
  pretending to be a MacBook Pro M4 Max.** The old spoof claimed macOS 26.4.1
  and an Apple Metal GPU on a Linux container running software GL, and
  `deviceandbrowserinfo.com/are_you_a_bot` classified us as a bot because of
  it — naming `hasInconsistentWebGLShaderLang` and `hasInconsistentWorkerValues`,
  both direct products of that spoof. A macOS claim contradicted by a Linux
  reality is a stronger bot signal than an honest Linux fingerprint, so the UA,
  `navigator.platform`, locale, Client Hints, WebGL vendor/renderer, the fake
  macOS speech voice and the Mac-only font remaps are all gone. What remains is
  only what is population-blending rather than contradictory: `deviceMemory`
  clamped to 8 (this build reports 32, above the ceiling Chrome has clamped to
  since 2017), a 1920x1080 screen instead of the Xvfb geometry, and
  `navigator.webdriver === false`.
- **The browser's timezone now matches the proxy's exit IP.** It reported UTC
  while egress geolocated to America/New_York — a comparison CreepJS and iphey
  both make directly. Resolved from the live exit IP at container start and set
  as `TZ` on the Chrome process, so the page, its workers and `Date` all agree
  with no patched function anywhere. Pin it with `AW_BROWSER_TIMEZONE`.
- **The fingerprint daemon is supervised.** It used to be started with a bare
  `&` and watched by nothing: if it died while Chrome stayed up, every page
  silently lost its patches with no alarm anywhere. It now runs under a restart
  loop outside the Chrome loop, and prints the sha256 of its own file at
  startup so `podman logs` can prove which build is actually live.
- The UA is no longer set in two places. It used to be forced both as a Chrome
  `--user-agent` flag and through `Emulation.setUserAgentOverride`, which had to
  be kept in step or the UA and the Client Hints would disagree — a detection
  signal in itself. Chromium's own Linux UA is correct, so both are gone.
- Added a repeatable anti-detection battery at `tests/antidetect/`
  (`python3 tests/antidetect/run_battery.py`): tiered detector sites with
  PASS / FAIL / DETECTOR-UNAVAILABLE verdicts, screenshots and raw evidence
  under `.tmp/antidetect/<timestamp>/`, pure-JS coherence assertions, a
  daemon-coverage check for targets other CDP clients create, and a detached
  measurement of the CDP `Runtime.enable` side-channel.
- Raised the container's CPU/memory limits from 0.5 CPU / 1024 MB to 2.0
  CPU / 2048 MB. noVNC frame encoding is CPU-bound, and 0.5 CPU was throttling
  hard under any on-screen movement (scroll, window drag, mouse), making the
  Browser screen visibly laggier than Kali Linux — which runs the same kind
  of in-browser desktop at 2 CPU / 4096 MB and feels smooth. This only
  targets the CPU cap; it does not change the noVNC/KasmVNC protocol itself.
- Doubled the container's CPU/memory limits again, from 2.0 CPU / 2048 MB to
  4.0 CPU / 4096 MB. The first bump confirmed noVNC lag was CPU-bound and
  fixed it; the user asked for a second doubling on top of that after
  testing the 2.0 CPU build in production.
- The Chromium profile now survives container recreation. It is bind-mounted
  from the workspace's durable app-data tree
  (`.aw-workspace/data/browser/chrome-profile`) instead of living in the
  container's writable layer, so logged-in sessions, cookies, localStorage and
  history are no longer lost on an app update or a workspace redeploy. Only the
  profile dir is persisted — `~/.cache/chromium` stays ephemeral so it can't
  grow unbounded on the host disk.
- Renamed the app display name to "Browser".
- Shortened the marketplace description.
- Enabled automatic image builds from release bump commits.
- Added a root `mcp.json` so the MCP Gateway app's scan
  (`scan_app_mcp_servers()`) picks up a `playwright` stdio server for this
  app without needing the separate `mcp-tools` app installed — it attaches
  to this container's own CDP endpoint at `http://aw-app-browser:9223`.
