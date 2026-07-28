# aw-app-browser

Migrates the **AW Browser** (headless Chromium with a CDP remote-debugging
endpoint — until now a hard-coded container, `tools/browser/`) onto the
[Decoupled Apps Framework](../../docs/knowledge_base/docs/architecture/decoupled-apps-framework.md)
(`aw-app.json` manifest schema v1). Unlike the other `aw-app-*` repos so far
(git, aws, essentials, node — all `commands:install` CLI installs), this app
is a **service**: it registers a managed, autostarted Chromium subprocess via
the F4 `ctx.services` contribution point (`service:manage`), not a one-shot
install.

## Status — MVP, exploratory

Frederico's ask (Telegram voice, 2026-07-28) was explicit: migrate the
feature to the apps system now, and get it into a testable state — this is
the first app-as-service in the framework, so validating the pattern matters
as much as the browser itself.

**Delivered (this repo, not yet installed anywhere):**

* `browser_app/plugin.py` — `BrowserAppPlugin.activate(ctx)`:
  1. `ctx.commands.install_system_cli("chromium", "scripts/install_chromium.sh", ...)`
     — installs the `chromium` apt package (journaled; reverted on uninstall).
  2. `ctx.services.register("chromium", start=<cmd>, autostart=True)` — a
     headless Chromium instance with `--remote-debugging-port=<config.port>`
     (default `9333`), started immediately and every reconcile pass.
  3. `ctx.routes.register(...)` — `GET /api/apps/browser/status` (running,
     pid, `cdp_url`, and a live `cdp_version` probe against
     `/json/version` when up), plus `POST /start` / `POST /stop` for manual
     control.
* `browser_app/chrome_cmd.py` — pure command-line builder (binary lookup +
  flag assembly), unit-tested with no real Chromium/ctx needed.
* `scripts/install_chromium.sh` / `scripts/uninstall.sh` — idempotent apt
  install/remove, same package name (`chromium`) as the already-proven
  hard-coded `tools/browser/Dockerfile` (`debian:bookworm-slim`).
* `aw-app.json` — id `browser`, `service:manage` + `commands:install` +
  `routes:register` + `net:outbound` + `fs:workspace-data` permissions,
  `resource_estimate` (cpu medium, ~400 MB mem, ~400 MB disk), `config_schema`
  (`port`, `headless`, `window_width`, `window_height`).
* `windows/main.json` — minimal declarative window (status check + start/stop
  buttons) under the Workspace nav section.
* Marketplace entry added to `tekflox/aw-marketplace/apps.json`.

## Chrome flags (why each one)

Mirrors the proven flags from `tools/browser/entrypoint-lite.sh`:

* `--no-sandbox` — Chromium refuses its own sandbox when running as root
  (the aw-workspace container's actual user — see "Root, not non-root"
  below), so this is required, not optional.
* `--disable-dev-shm-usage` — container `/dev/shm` is too small for
  Chromium's default shared-memory usage.
* `--headless=new` — the MVP always runs headless (no Xvfb/VNC yet — that's
  a `tools/browser/`-only feature, out of scope here).
* `--remote-debugging-address=0.0.0.0` + `--remote-allow-origins=*` — CDP
  reachable from other containers on the shared workspace network, matching
  how the existing `playwright` MCP attaches to the shared aw-browser today.

## "Root, not non-root" — the task's risk assessment revisited

The card flagged running Chromium in a non-root container as the main risk.
In practice `repos/aw-workspace/Dockerfile` has **no `USER` directive**
(`FROM python:3.12-slim`, no user switch) — the aw-workspace process, and
therefore every in-process app plugin and installer script it runs, executes
as **root**. `apt-get install` in `scripts/install_chromium.sh` needs no
`sudo` for the same reason the existing `aw-app-git`/`aw-app-essentials`
installers don't use it either. The actual risk was the opposite of "no
apt without root" — it was "Chromium's sandbox refuses to run as root",
handled by `--no-sandbox`.

## Testing done

1. **Manifest validation**: `.venv/aw/bin/python tests/validate_manifest.py`
   → `OK: aw-app.json is valid and all system_clis installers exist`.
2. **Unit tests**: `.venv/aw/bin/python -m pytest tests/` → `11 passed`
   (`chrome_cmd` command-building logic + the `/status`/`/start`/`/stop`
   routes through a real `FastAPI TestClient` against a fake `ctx`).
3. **Real install + CDP smoke test, in a container matching aw-workspace's
   actual base image** (`python:3.12-slim` → confirmed `Debian GNU/Linux 13
   (trixie)` inside, same distro as `aw-app-git`'s target): ran
   `tests/standalone_test.sh` for real inside a fresh Docker container —
   `apt-get install chromium` completed cleanly, the headless Chromium
   process started with the exact flags `chrome_cmd.py` builds, and
   `curl http://127.0.0.1:9333/json/version` answered within a couple of
   seconds with a valid CDP handshake:
   ```json
   {
     "Browser": "Chrome/150.0.7871.181",
     "Protocol-Version": "1.3",
     "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/150.0.0.0 Safari/537.36",
     "V8-Version": "15.0.245.21",
     "WebKit-Version": "537.36 (@24b04c927b23c39cf9c5227cc8dc6f64a744c8e9)",
     "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/browser/2dba150f-8ad4-4148-90a5-571064b4a862"
   }
   ```
   (D-Bus warnings in the log are cosmetic — Chromium falls back cleanly with
   no system bus in a minimal container, same as the existing aw-browser.)
   The dev sandbox this task ran in (Ubuntu 24.04) was **not** used for this
   check — its `apt-get install chromium` resolves to a **snap wrapper**
   package on Ubuntu ("requires the chromium snap to be installed"), which is
   irrelevant to the Debian-based target and would have been a false
   negative.

## NOT done here (explicitly out of scope, per the card)

* **Not installed into any real workspace** — the orchestrator installs and
  does the live verify (start the service, hit `/api/apps/browser/status`,
  point a real `playwright`/CDP client at `cdp_url`) per the card's
  instructions.
* **Selenium / chromedriver WebDriver** — phase 2, explicitly deferred by the
  card. The CDP endpoint (what the current `playwright` MCP already uses) is
  the MVP surface.
* **No integration with the existing shared `aw-browser`/`playwright` MCP** —
  this app runs its own independent Chromium on a different port (`9333` vs.
  the existing `9222`/`9223`) precisely so it doesn't collide with or break
  anything already relying on the shared browser. Pointing the `playwright`
  MCP at this app's endpoint instead is deliberate follow-up work, not done
  here.
* **No Xvfb/VNC/noVNC** — the hard-coded `tools/browser/` container also
  exposes a visual desktop (noVNC); this MVP is headless-only, per the card's
  focus on "the endpoint CDP (que é o que o aw-browser atual usa)".
* **`fs:workspace-data` declared but unused via a facade** — no `ctx.fs`
  facade exists yet in the framework (confirmed: not in `base.py`'s
  `_FACADES`, and no other `aw-app-*` repo uses one either — same gap noted
  in `aw-app-whiteboard`'s README). The Chromium profile dir lives under
  `ctx.package_dir/.chrome-profile` instead, which is writable and already
  scoped to this app.
