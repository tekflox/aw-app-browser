"""Unit tests for chrome_cmd — no real Chromium binary, no framework ctx.

Run: .venv/aw/bin/python -m pytest tests/test_chrome_cmd.py
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from browser_app import chrome_cmd  # noqa: E402


def test_find_chromium_binary_prefers_first_match(monkeypatch):
    monkeypatch.setattr(
        chrome_cmd.shutil, "which",
        lambda name: "/usr/bin/chromium" if name == "chromium" else None,
    )
    assert chrome_cmd.find_chromium_binary() == "/usr/bin/chromium"


def test_find_chromium_binary_falls_back(monkeypatch):
    def fake_which(name):
        return "/usr/bin/google-chrome" if name == "google-chrome" else None
    monkeypatch.setattr(chrome_cmd.shutil, "which", fake_which)
    assert chrome_cmd.find_chromium_binary() == "/usr/bin/google-chrome"


def test_find_chromium_binary_raises_when_missing(monkeypatch):
    monkeypatch.setattr(chrome_cmd.shutil, "which", lambda name: None)
    with pytest.raises(chrome_cmd.ChromiumNotFoundError):
        chrome_cmd.find_chromium_binary()


def test_build_command_is_shlex_splittable_and_headless_by_default():
    cmd = chrome_cmd.build_command(
        binary="/usr/bin/chromium", port=9333, profile_dir="/tmp/profile",
    )
    tokens = shlex.split(cmd)
    assert tokens[0] == "/usr/bin/chromium"
    assert "--headless=new" in tokens
    assert "--no-sandbox" in tokens
    assert "--remote-debugging-port=9333" in tokens
    assert "--remote-debugging-address=0.0.0.0" in tokens
    assert "--user-data-dir=/tmp/profile" in tokens


def test_build_command_headless_off():
    cmd = chrome_cmd.build_command(
        binary="/usr/bin/chromium", port=9333, profile_dir="/tmp/profile", headless=False,
    )
    assert "--headless=new" not in shlex.split(cmd)


def test_build_command_custom_window_size():
    cmd = chrome_cmd.build_command(
        binary="/usr/bin/chromium", port=9333, profile_dir="/tmp/profile",
        width=800, height=600,
    )
    assert "--window-size=800,600" in shlex.split(cmd)
