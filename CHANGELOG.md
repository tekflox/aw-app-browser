# Change History

This file records user-facing Browser changes. Keep historical implementation
notes here instead of expanding the marketplace description.

## Unreleased

- Renamed the app display name to "Browser".
- Shortened the marketplace description.
- Enabled automatic image builds from release bump commits.
- Added a root `mcp.json` so the MCP Gateway app's scan
  (`scan_app_mcp_servers()`) picks up a `playwright` stdio server for this
  app without needing the separate `mcp-tools` app installed — it attaches
  to this container's own CDP endpoint at `http://aw-app-browser:9223`.
