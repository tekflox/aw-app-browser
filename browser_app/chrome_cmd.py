"""Builds the Chromium CDP-service command line (no framework coupling — pure
functions, easy to unit-test without a real ``ctx``/binary/process).

Mirrors the flags already proven by the hard-coded aw-browser container
(``tools/browser/entrypoint-lite.sh``): ``--no-sandbox`` (Chromium refuses to
run its own sandbox as root, which this workspace container runs as) and
``--disable-dev-shm-usage`` (the container's ``/dev/shm`` is too small for
Chromium's default behavior).
"""
from __future__ import annotations

import shlex
import shutil

DEFAULT_PORT = 9333
DEFAULT_WIDTH = 1440
DEFAULT_HEIGHT = 900

_CANDIDATE_BINARIES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")


class ChromiumNotFoundError(RuntimeError):
    pass


def find_chromium_binary() -> str:
    for name in _CANDIDATE_BINARIES:
        path = shutil.which(name)
        if path:
            return path
    raise ChromiumNotFoundError(
        f"no Chromium binary found on PATH (looked for: {', '.join(_CANDIDATE_BINARIES)})"
    )


def build_command(*, binary: str, port: int, profile_dir: str,
                   headless: bool = True, width: int = DEFAULT_WIDTH,
                   height: int = DEFAULT_HEIGHT) -> str:
    """Returns a shell-quoted command line, ready for ``ctx.services.register``
    (the F4 supervisor runs it via ``shlex.split`` — no shell, so every arg
    must already be a separate quoted token)."""
    args = [binary]
    if headless:
        args.append("--headless=new")
    args += [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=0.0.0.0",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile_dir}",
        f"--window-size={width},{height}",
        "--no-first-run",
        "--disable-extensions",
        "--disable-default-apps",
        "--disable-sync",
        "--disable-translate",
        "about:blank",
    ]
    return " ".join(shlex.quote(a) for a in args)
