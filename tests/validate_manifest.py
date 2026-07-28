#!/usr/bin/env python3
"""Validates aw-app.json against schemas/aw-app.schema.json. Run with the
AW venv (jsonschema is installed there): .venv/aw/bin/python tests/validate_manifest.py

NOTE (2026-07-28): this manifest declares tier="container" but is NOT
installable — no Tier 2 (container-per-app) runtime exists in the decoupled
apps framework yet. This test only proves the manifest is *structurally*
valid, not that the app can actually run. See README.md.
"""
import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent

manifest = json.loads((ROOT / "aw-app.json").read_text())
schema = json.loads((ROOT / "schemas" / "aw-app.schema.json").read_text())

jsonschema.validate(instance=manifest, schema=schema)

print("OK: aw-app.json is structurally valid (tier=container, NOT installable yet — see README)")
