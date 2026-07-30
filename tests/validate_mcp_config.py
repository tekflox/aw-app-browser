#!/usr/bin/env python3
"""Validates mcp.json — the file the MCP Gateway app's
``scan_app_mcp_servers()`` reads from this app's root directory (same
mcpServers shape as the in-repo project's .mcp.json: command/args/env/type).

Run with: python3 tests/validate_mcp_config.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

config = json.loads((ROOT / "mcp.json").read_text())

assert "mcpServers" in config, "mcp.json must have a top-level 'mcpServers' object"
servers = config["mcpServers"]
assert "playwright" in servers, "expected a 'playwright' server entry"

playwright = servers["playwright"]
assert playwright.get("type", "stdio") == "stdio", "playwright server must be stdio (spawned by the gateway container)"
assert playwright.get("command"), "playwright server needs a 'command'"
args = playwright.get("args", [])
assert "--cdp-endpoint" in args, "playwright server must pass --cdp-endpoint"
endpoint_index = args.index("--cdp-endpoint") + 1
assert endpoint_index < len(args), "--cdp-endpoint needs a value"
endpoint = args[endpoint_index]
assert endpoint.startswith("http://aw-app-browser:"), (
    f"--cdp-endpoint should target this app's own container name (aw-app-<id>), got {endpoint!r}"
)

print("OK: mcp.json is structurally valid")
