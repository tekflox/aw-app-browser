# Browser

Chromium packaged as an AW workspace app.

Browser provides:

- an interactive noVNC screen served through the app window
- a CDP endpoint for Playwright and Chrome DevTools automation
- workspace cookie-proxy support for authenticated browsing
- configurable window dimensions

## Runtime

The app runs as a Tier-2 container from:

```text
ghcr.io/tekflox/aw-app-browser:latest
```

The workspace exposes the noVNC screen on the app route. CDP stays available
inside the workspace network for automation clients.

## MCP tool (Playwright)

This app is self-describing for the MCP Gateway app (`tekflox/aw-mcp-gateway`):
a root-level `mcp.json` is picked up by the gateway's app scan
(`AW_APP_SCAN_ROOTS` → `scan_app_mcp_servers()` → `effective_mcp_config()`)
with no manual per-workspace configuration needed.

```json
{
  "mcpServers": {
    "playwright": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": [
        "-y",
        "@playwright/mcp@0.0.77",
        "--cdp-endpoint",
        "http://aw-app-browser:9223"
      ]
    }
  }
}
```

This spawns `@playwright/mcp` as a stdio subprocess *inside the MCP Gateway
container* (which bundles Node/npx for exactly this) and points it at this
container's own CDP endpoint instead of launching a separate browser — so
Playwright MCP tool calls drive the same Chromium instance visible on the
noVNC screen. `aw-app-browser:9223` is this container's name on the shared
podman network (`aw-app-<manifest id>`, per aw-workspace's
`ContainerSupervisor`) on port 9223 — the CDP proxy `entrypoint-lite.sh`
publishes on `0.0.0.0` (forwarding to Chrome's own `127.0.0.1:9222` CDP
listener), matching the same convention already used by the `mcp-tools` app.

If both this app and `mcp-tools` are installed in the same workspace, both
contribute a server named `playwright` pointing at the same endpoint — the
gateway scan is last-app-wins per name (harmless, functionally identical).

## Release

Pushing to `master` runs the shared marketplace release workflow:

1. validate `aw-app.json`
2. bump `aw-app.json` version
3. tag the release
4. open a marketplace sync PR

The image build runs automatically from the release bump commit so the GHCR
`latest` tag follows the manifest release.

Manual rebuild:

```bash
gh workflow run build.yml
```

## Change History

See `CHANGELOG.md`.
