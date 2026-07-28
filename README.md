# aw-app-browser

Migrates the **AW Browser** (headless Chromium with a CDP remote-debugging
endpoint — until now a hard-coded container, `tools/browser/`) onto the
[Decoupled Apps Framework](../../docs/knowledge_base/docs/architecture/decoupled-apps-framework.md)
(`aw-app.json` manifest schema v1).

## Status: BLOCKED — Tier 2 (container) runtime does not exist yet (2026-07-28)

Frederico's original ask ran Chromium in-process (Tier 1) inside the
aw-workspace container. That worked as a smoke test (see
`reference/tier1-prototype-DO-NOT-INSTALL/`) but is fragile in real use:
Chromium refuses its own sandbox as root (`--no-sandbox` needed) and the
container's `/dev/shm` is undersized for headless Chromium's default
behavior (`--disable-dev-shm-usage` needed) — both workarounds, not fixes.

Frederico's follow-up direction (Telegram, 2026-07-28): don't run Chromium
**native** inside the aw-workspace process — run the whole AW Browser as a
**separate container** (Tier 2 / container-per-app), started with
`--shm-size=1g` from a prebuilt image that already bundles Chromium + all
its libs (`browserless/chrome`, `selenium/standalone-chromium`, or the
official Playwright image). That sidesteps both problems structurally
instead of flag-patching them.

**This repo was asked to investigate feasibility before building it. Verdict: not viable yet, and here's exactly why.**

### 1. Does Tier 2 (container-per-app) exist in the decoupled apps framework?

**No.** The ADR ([`decoupled-apps-framework.md`](../../docs/knowledge_base/docs/architecture/decoupled-apps-framework.md))
defines it as **Phase 6** of the phased implementation plan:

> 6. **Phase 6 — Tier 2 containers**: lifecycle + reverse proxy
>    `/api/apps/<slug>/*` + resource limits; migrate ONE `src/custom_apps`
>    app as validation.

Only Phases 1–5 have landed (plugin runtime, permissions, cloud registry,
commands/services/db/secrets, frontend plugin runtime — see the
`decoupled-apps-framework-f4-landed.md` / `-f5-landed.md` memory notes).
Confirmed directly against the code: `repos/aw-workspace/src/apps/` has
`base.py`, `commands.py`, `services.py`, `db_tables.py`, `secret_store.py`,
`watchdog.py`, `reconciler.py`, `catalog.py`, `journal.py`, `manifest.py`,
`paths.py`, `routes.py`, `runtime.py`, `capabilities.py`,
`registry_client.py`, `fetch.py`, `install_jobs.py` — **no
`containers.py` / `ContainerSupervisor`**. The `"containers:manage"`
permission string and `"tier": "container"` manifest field exist (declared
in the schema/ADR as the target shape), but nothing in the runtime
implements or enforces them — an app can *say* `tier: container` today, but
nothing will ever start its container.

The **separate** `src/custom_apps/` + `aw-app-builder` machinery (in the
*monolith* repo, `agentic-workspace`, not `aw-workspace`) is real
container-per-app tooling — but the ADR is explicit that it "evolves into
Tier 2" later; it isn't Tier 2 itself, and it lives in a different runtime
(the `aw-sandbox` monolith container, not the per-user `aw-workspace`
data-plane container this app is meant to install into).

### 2. Can the aw-workspace container spawn a sibling container (docker-out-of-docker / podman-out-of-podman)?

**No.** Three independent confirmations:

* `repos/aw-workspace/Dockerfile` line 2, a comment stating the design
  intent directly: *"Sem CLIs de agente, sem docker socket, sem build de
  frontend."* ("No agent CLIs, no docker socket, no frontend build.")
* `repos/aw-workspace/docker-compose.yml` — the `aw-workspace` service has
  **no `volumes:` section at all**: no `/var/run/docker.sock` mount, no
  podman socket mount, not `privileged`. Only `network_mode:
  "container:aw-sandbox"` (shares the sandbox's netns) and env vars.
* `repos/aw-remote-host/bootstrap/workspace/install.sh` (the actual deploy
  path used on BYOD hosts like macbook-fred, via `podman run`) — bind-mounts
  only the workspace host dir (`-v "${HOST_DIR}:${CONTAINER_WORKDIR}"`) and
  joins a user network; no socket mount, no `--privileged`.

So the aw-workspace process has no way to launch, stop, or reverse-proxy a
sibling container **even if it wrote the code to try** — there is no
docker/podman API reachable from inside it.

### Conclusion

Both preconditions the card asked to check are **false**: Tier 2 doesn't
exist, and the workspace container can't reach a container engine even if
it did. Per the card's own instruction ("Se o Tier 2 NÃO existe E o
workspace NÃO pode spawnar container: PARE e reporte") — **stopping here**
rather than shipping a Tier-1-native app labeled as something it isn't, or
a Tier-2 manifest that silently no-ops.

**This is an architecture decision for Frederico**, roughly one of:

1. Build Tier 2 for real (ADR Phase 6) — give aw-workspace a docker/podman
   socket (mounted read/write, scoped how far?) + a `ContainerSupervisor`
   (register/start/stop/status/reverse-proxy, mirroring `services.py`'s
   shape) + decide the trust/security story for granting a workspace
   container the ability to spawn arbitrary sibling containers.
2. Accept the Tier-1-native prototype's workarounds (`--no-sandbox` +
   `--disable-dev-shm-usage`) as "good enough for now" and ship that instead
   — it *does* work (see the prototype's testing evidence below), just with
   the fragility already flagged.
3. Keep the AW Browser as the existing hard-coded `tools/browser/`
   container outside the apps framework entirely, and defer this migration
   until Tier 2 is real.

## What's in this repo right now

* `aw-app.json` — updated to `"tier": "container"`, describing the intended
  shape (`runtime.image`, `runtime.port`, `runtime.resources`,
  `runtime.run_flags_needed: ["--shm-size=1g"]`) as **documentation of
  intent**, explicitly marked `_status: "ASPIRACIONAL"` — not installable.
  Trimmed `permissions`/`contributes` to what a Tier-2 app would plausibly
  need (`routes:register`, `net:outbound`, `fs:workspace-data`); dropped
  `commands:install` / `service:manage` (Tier-1-only, no longer applicable).
* `reference/tier1-prototype-DO-NOT-INSTALL/` — the original Tier-1
  in-process prototype (kept for evidence + in case option 2 above is
  chosen), **not wired into the current `aw-app.json`** so it can't be
  accidentally installed. See its own testing evidence below.
* `tests/validate_manifest.py` — structural validation only (schema
  conformance), doesn't claim the app runs.
* Marketplace entry (`tekflox/aw-marketplace/apps.json`) updated to reflect
  the blocked status.

## Tier-1 prototype — what was proven (kept as reference)

Before the direction change, `reference/tier1-prototype-DO-NOT-INSTALL/`
was built and tested as a Tier-1 `service:manage` app (Chromium subprocess
managed via `ctx.services`):

* `browser_app/plugin.py` — `BrowserAppPlugin.activate(ctx)`: installs
  `chromium` via `ctx.commands.install_system_cli`, registers + autostarts
  it as a service via `ctx.services.register(..., autostart=True)`, exposes
  `GET /status` / `POST /start` / `POST /stop`.
* `browser_app/chrome_cmd.py` — pure command-line builder, unit-tested.
* Chrome flags mirror the proven `tools/browser/entrypoint-lite.sh`:
  `--no-sandbox` (Chromium refuses its sandbox as root — confirmed
  `repos/aw-workspace/Dockerfile` has no `USER` directive, so the process
  runs as root), `--disable-dev-shm-usage` (container `/dev/shm` too small).

**Testing done on the prototype:**

1. Manifest validation passed against the (then Tier-1) schema.
2. Unit tests: `11 passed` (`chrome_cmd` logic + `/status`/`/start`/`/stop`
   routes via `FastAPI TestClient` against a fake `ctx`).
3. **Real install + CDP smoke test**, in a container matching
   aw-workspace's actual base image (`python:3.12-slim` → confirmed
   `Debian GNU/Linux 13 (trixie)` inside): ran
   `reference/tier1-prototype-DO-NOT-INSTALL/standalone_test.sh` for real
   inside a fresh Docker container — `apt-get install chromium` completed
   cleanly, headless Chromium started with the exact built flags, and
   `curl http://127.0.0.1:9333/json/version` answered within seconds:
   ```json
   {
     "Browser": "Chrome/150.0.7871.181",
     "Protocol-Version": "1.3",
     "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/browser/2dba150f-8ad4-4148-90a5-571064b4a862"
   }
   ```
   (D-Bus warnings in the log are cosmetic — no system bus in a minimal
   container, same as the existing `aw-browser`.) The dev sandbox this task
   ran in (Ubuntu 24.04) was **not** used for this check — Ubuntu's
   `chromium` apt package is a snap wrapper ("requires the chromium snap"),
   irrelevant to the Debian-based target and a false negative if used.

This proves the Tier-1 path *works*, mechanically — the concern was never
"does it start", it was "is running Chromium native in the shared workspace
process robust enough" (per Frederico's own framing: sandbox, `/dev/shm`,
crash isolation). That's exactly what Tier 2 would fix and Tier 1 can't.

## NOT done here

* **No Tier 2 runtime built** — out of scope for this card; it's the
  architecture decision flagged above, for Frederico to make.
* **No install anywhere** — nothing here is installable as-is (Tier 1 was
  deliberately un-wired per the direction change; Tier 2 has no runtime).
* **Selenium / chromedriver WebDriver** — still phase 2 regardless of tier,
  per the original card.
