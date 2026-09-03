# Change History

This file records user-facing Browser changes. Keep historical implementation
notes here instead of expanding the marketplace description.

## Unreleased

- Raised the container's CPU/memory limits from 0.5 CPU / 1024 MB to 2.0
  CPU / 2048 MB. noVNC frame encoding is CPU-bound, and 0.5 CPU was throttling
  hard under any on-screen movement (scroll, window drag, mouse), making the
  Browser screen visibly laggier than Kali Linux — which runs the same kind
  of in-browser desktop at 2 CPU / 4096 MB and feels smooth. This only
  targets the CPU cap; it does not change the noVNC/KasmVNC protocol itself.
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
