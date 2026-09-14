# Browser

Browser adds a full Chromium session to an AW Workspace. It gives users and agents an interactive browser window that can also be automated when a workflow needs to inspect, test, or operate a website.

## What It Does

- Opens Chromium in a workspace window.
- Provides an interactive screen for normal browsing.
- Supports browser automation for testing and agent-driven workflows.
- Works with the Proxy app so authenticated browser sessions can reuse workspace-managed cookies.
- Keeps the browser profile — logins, cookies, history — across workspace redeploys and app updates.
- Lets the browser window size be configured for the workspace.

## Why Use It

Use this app when work inside AW needs a real browser. It is useful for web testing, logging into web tools, inspecting deployed pages, reproducing UI behavior, and letting agents operate a browser while the user can still see what is happening.

## How To Use It

Install Browser and its required Proxy app. Open Browser from the workspace and use it like a normal Chromium window. Agents can also connect to the browser when they need to navigate pages, click controls, capture screenshots, or validate web UI behavior.

## What It Delivers

The app gives the workspace a visible, reusable browser session. It bridges manual browsing and automated browser work so a user and an agent can inspect the same web environment.

## GPU vs software rendering

The manifest declares `runtime.host_power_optional: ["gpu"]` (+ the
`host:device-gpu` permission) — an *optional* host-power grant, unlike
`runtime.host_power`: a host that hasn't opted in still installs and runs
the app exactly as before, it just never receives `/dev/dri`. See
`aw-workspace`'s host-power docs for the three-leg grant model.

`container/entrypoint-lite.sh` picks its Chromium GL flags at container
start by actually probing what it received, not by trusting the grant was
honoured:

| Host granted `gpu`? | `/dev/dri/renderD*` present? | `seluser` can open it? | Path chosen |
|---|---|---|---|
| No | — | — | Swiftshader (no `/dev/dri` in the container at all) |
| Yes | No (e.g. a BMC card0-only host) | — | Swiftshader (`/dev/dri` present, no render node) |
| Yes | Yes | No (group join failed/insufficient) | Swiftshader (node present but unopenable — logged explicitly, never reported as GPU) |
| Yes | Yes | Yes | Real GL (`--use-gl=angle --use-angle=gl`) |

The three degraded rows are all byte-identical to the pre-GPU-aware
flags — provable by diffing the fallback branch against the flags this
file shipped before this feature. `podman logs aw-app-browser` always
prints one `GPU detection: …` line at startup naming which row applied and
why, so `chrome://gpu` is never the only way to tell.

The trap worth knowing if you touch this: `/dev/dri/renderD*` ships
`crw-rw---- root:render`, and `seluser` (uid 1200) is in no such group even
when the device node is passed through — an `open()` fails silently and
Chromium falls back to software with nothing logged, unless the detection
does a real open probe (not just a path-existence check) and joins
`seluser` into the node's owning group via `sg` before both the probe and
the eventual Chromium launch (a bare `usermod -aG` doesn't take effect in
an already-running shell).
