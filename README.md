# Browser

Chromium packaged as a decoupled AW workspace app.

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
