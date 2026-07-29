#!/usr/bin/env python3
"""Validates aw-app.json against schemas/aw-app.schema.json. Run with the
AW venv (jsonschema is installed there): .venv/aw/bin/python tests/validate_manifest.py

This test proves the manifest is structurally valid. Runtime validation needs
an AW workspace with the container runtime available.
"""
import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent

manifest = json.loads((ROOT / "aw-app.json").read_text())
schema = json.loads((ROOT / "schemas" / "aw-app.schema.json").read_text())

jsonschema.validate(instance=manifest, schema=schema)

print("OK: aw-app.json is structurally valid")
