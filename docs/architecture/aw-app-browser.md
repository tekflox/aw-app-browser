---
repo: architecture
path: docs/architecture/aw-app-browser.md
source: generated
edited: false
checksum: sha256:023f6068e4c40989587d3d5c1ac62bfd73a206c9071d0e4cb4c298849d932fbd
---
# Browser

- **repo**: aw-app-browser
- **layer**: app-container
- **technologies**: docker
- **health** (derived): planned

Chromium for AW workspaces with an interactive noVNC screen, CDP automation endpoint, workspace cookie-proxy support, and configurable window size.

## Connections
- `other` → **aw-app-proxy** — Authentication — this container's Chrome must tunnel through aw-app-proxy's CONNECT proxy, and aw-app-proxy injects/clears its cookies via CDP so it's logged in as the user

## MCP tools
_none exposed_

## Requirements
_none documented_
