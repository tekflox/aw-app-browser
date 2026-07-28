#!/usr/bin/env python3
"""Validates aw-app.json against schemas/aw-app.schema.json. Run with the
AW venv (jsonschema is installed there): .venv/aw/bin/python tests/validate_manifest.py
"""
import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent

manifest = json.loads((ROOT / "aw-app.json").read_text())
schema = json.loads((ROOT / "schemas" / "aw-app.schema.json").read_text())

jsonschema.validate(instance=manifest, schema=schema)

# System-CLI installer scripts referenced from the manifest must exist.
for cli in manifest["contributes"].get("system_clis", []):
    installer_path = ROOT / cli["installer"]
    if not installer_path.is_file():
        print(f"FAIL: installer script missing: {installer_path}", file=sys.stderr)
        sys.exit(1)

print("OK: aw-app.json is valid and all system_clis installers exist")
