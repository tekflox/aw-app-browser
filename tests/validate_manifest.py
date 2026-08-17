#!/usr/bin/env python3
"""Validates an app's aw-app.json — schema, referenced files, and the things
that only blow up on a REAL install.

**The canonical copy lives in aw-marketplace** (`scripts/validate_manifest.py`
+ `schemas/aw-app.schema.json`), and `app-release.yml` runs THAT against every
app it releases. This copy is for running the same checks locally before you
push:

    python3 tests/validate_manifest.py aw-app.json

TEMPLATE: keep this file. A new app inherits it from here, and it is the
difference between finding a bad manifest now and finding it when a release
fails. CI no longer depends on its presence — but you do.

It used to live only inside each app repo, alongside a copy of the schema. That
produced 28 copies in 10 different versions, drifting apart with nobody
noticing — and the release workflow only ran the check `if [ -f
tests/validate_manifest.py ]`, so an app that simply lacked the file published
with no validation at all. That is how aw-app-kb's release failed on a
permission its schema copy had never heard of while aw-app-architecture, which
declares the same one, released green without being checked.

    python3 validate_manifest.py <manifest.json> [--schema <schema.json>]

If this local copy drifts from the canonical one it can only be *more*
permissive by accident — CI validates against aw-marketplace's either way, so a
stale copy here can no longer let anything through.

Beyond the JSON schema, this catches three classes of bug that a schema
cannot, and that every one of them has shipped at least once:

1. **Dangling references** — a window spec / frontend bundle / installer
   script named in the manifest but not in the repo. Fails at install, or
   worse, renders an empty panel.
2. **Widgets the renderer does not implement** — the declarative vocabulary is
   a fixed list in ``aw-workspace-ui/src/components/AppWindow.jsx``. A spec
   using anything else is silently inert: the window draws, the widget just
   never appears. ``aw-app-proxy`` has shipped a data-bound ``table`` widget
   for months that has never rendered.
3. **Settings panels that can't render** — ``contributes.settings_panels``
   only works with a **declarative** window (AppConfigBody reads
   ``body.spec_data``). Pointing one at a ``component`` window yields an empty
   Settings pane with no error anywhere.
"""
import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent

# Referenced files resolve relative to the MANIFEST's directory, so this runs
# against any app's checkout from anywhere.
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
_schema_flag = None
if "--schema" in sys.argv:
    _schema_flag = Path(sys.argv[sys.argv.index("--schema") + 1]).resolve()

MANIFEST_PATH = Path(_args[0]).resolve() if _args else Path("aw-app.json").resolve()
APP_ROOT = MANIFEST_PATH.parent
def _resolve_schema() -> Path:
    """Where the schema comes from, most explicit first.

    A new app does NOT need its own `schemas/` copy — that duplication is what
    produced 28 files in 10 drifted versions. If this repo happens to still
    carry one it is honoured (older apps do), otherwise the canonical copy in a
    sibling aw-marketplace checkout is used, which is also what CI validates
    against.
    """
    if _schema_flag:
        return _schema_flag
    local = ROOT / "schemas" / "aw-app.schema.json"
    if local.is_file():
        return local
    sibling = ROOT.parent / "aw-marketplace" / "schemas" / "aw-app.schema.json"
    if sibling.is_file():
        return sibling
    raise SystemExit(
        "no schema found. This repo has no schemas/aw-app.schema.json and "
        "aw-marketplace is not checked out next to it — pass one explicitly:\n"
        "    python3 tests/validate_manifest.py aw-app.json --schema <path>"
    )


SCHEMA_PATH = _resolve_schema()

# The widget types aw-workspace-ui's declarative renderer actually implements
# (src/components/AppWindow.jsx). Keep in sync with that file; anything else
# in a spec is dead weight that renders nothing.
KNOWN_WIDGETS = {
    "markdown", "list", "button", "iframe", "app_iframe",
    "collapsible", "form", "auth_status",
}

# `form` field input types the renderer gives a dedicated control. `select`
# options are plain STRINGS, not {value,label} objects — an object renders as
# "[object Object]".
KNOWN_FORM_INPUTS = {"text", "password", "select", "checkbox"}

# Anything else falls through to `<input type="text">` (AppWindow.jsx renders
# type={type === 'password' ? 'password' : 'text'}). It still WORKS — it just
# silently loses the numeric keyboard, min/max and spinner, and the value
# arrives as a string. Worth saying out loud, not worth failing a build over.
DEGRADES_TO_TEXT = {"number", "email", "url", "tel", "date"}

errors: list[str] = []
warnings: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


manifest = json.loads(MANIFEST_PATH.read_text())
schema = json.loads(SCHEMA_PATH.read_text())
jsonschema.validate(instance=manifest, schema=schema)

contributes = manifest.get("contributes", {}) or {}

# ── 1. referenced files must exist ──────────────────────────────────────────

for cli in contributes.get("system_clis", []) or []:
    if not (APP_ROOT / cli["installer"]).is_file():
        fail(f"installer script missing: {cli['installer']}")

frontend = contributes.get("frontend") or {}
bundle = frontend.get("bundle")
if bundle and not (APP_ROOT / bundle).is_file():
    # The built bundle is COMMITTED in every shipping app (aw-app-whiteboard,
    # aw-app-remote-screen, ...) because the release workflow runs tests only
    # — it never runs `npm run build`. A gitignored dist therefore means the
    # installed app points at a file that does not exist, and its whole
    # frontend silently vanishes.
    fail(f"frontend bundle missing: {bundle} — run `npm run build` in ui/ AND "
         f"commit the result (release CI does not build it; check that "
         f".gitignore does not exclude it)")

for skill in contributes.get("skills", []) or []:
    if not (APP_ROOT / skill["path"]).is_file():
        fail(f"skill file missing: {skill['path']}")

# contributes.agents may point a system prompt / group instructions at a file
# in the package. A missing one is the quietest failure of the lot: the
# workspace drops that single field with a log line and seeds the agent
# anyway, so you get a live agent with an EMPTY prompt.
for _kind, _field in (("agents", "system_prompt_file"), ("groups", "instructions_file")):
    for entry in ((contributes.get("agents") or {}).get(_kind) or []):
        ref = entry.get(_field)
        if ref and not (APP_ROOT / ref).is_file():
            fail(f"contributes.agents.{_kind}[{entry.get('slug')!r}]: {_field} "
                 f"{ref!r} does not exist — the object is still created, with "
                 f"that field empty.")

migrations = manifest.get("migrations") or {}
if migrations:
    mig_dir = APP_ROOT / (migrations.get("dir") or "migrations")
    if not mig_dir.is_dir():
        fail(f"migrations.dir declared but missing: {mig_dir.name}/")

# ── 2. window specs: exist, and use widgets that actually render ────────────

windows = contributes.get("windows", []) or []
declarative_window_ids = set()


def check_widgets(widgets, where: str) -> None:
    for widget in widgets or []:
        wtype = widget.get("type")
        if wtype not in KNOWN_WIDGETS:
            fail(f"{where}: widget type {wtype!r} is not implemented by the "
                 f"declarative renderer — it will render nothing. "
                 f"Supported: {', '.join(sorted(KNOWN_WIDGETS))}. "
                 f"For a live/editable list of rows there is NO widget: serve "
                 f"your own page and point an `iframe` at it.")
            continue
        if wtype == "collapsible":
            check_widgets(widget.get("widgets"), f"{where} > collapsible")
        if wtype == "list" and widget.get("bind"):
            fail(f"{where}: `list` renders STATIC items from the spec and has "
                 f"no `bind` — use an `iframe` onto your own route instead.")
        if wtype == "form":
            for field in widget.get("fields", []) or []:
                itype = field.get("input", "text")
                if itype in DEGRADES_TO_TEXT:
                    warn(f"{where}: form input {itype!r} renders as a plain "
                         f"text box (no numeric keyboard, no min/max) and the "
                         f"value arrives as a string.")
                elif itype not in KNOWN_FORM_INPUTS:
                    fail(f"{where}: form input {itype!r} is not implemented "
                         f"(supported: {', '.join(sorted(KNOWN_FORM_INPUTS))} "
                         f"— anything else falls back to a text box).")
                if itype == "select":
                    for opt in field.get("options", []) or []:
                        if not isinstance(opt, str):
                            fail(f"{where}: `select` options must be plain "
                                 f"strings; {opt!r} renders as [object Object].")


for win in windows:
    body = win.get("body", {}) or {}
    if body.get("type") != "declarative":
        continue
    declarative_window_ids.add(win["id"])
    spec_path = APP_ROOT / body["spec"]
    if not spec_path.is_file():
        fail(f"window spec missing: {body['spec']}")
        continue
    spec = json.loads(spec_path.read_text())
    for region in spec.get("regions", []) or []:
        check_widgets(region.get("widgets"), f"{body['spec']} region {region.get('id')!r}")

# ── 3. settings panels must point at a declarative window ──────────────────

window_ids = {w["id"] for w in windows}
for panel in contributes.get("settings_panels", []) or []:
    target = panel.get("window")
    if not target:
        continue
    if target not in window_ids:
        fail(f"settings_panel {panel['id']!r} points at unknown window {target!r}")
    elif target not in declarative_window_ids:
        fail(f"settings_panel {panel['id']!r} points at window {target!r}, which "
             f"is not `declarative`. The Settings pane reads body.spec_data and "
             f"will render EMPTY for a component/managed_app window.")

# ── report ──────────────────────────────────────────────────────────────────

for w in warnings:
    print(f"WARN: {w}", file=sys.stderr)

if errors:
    for e in errors:
        print(f"FAIL: {e}", file=sys.stderr)
    sys.exit(1)

print("OK: aw-app.json is valid, every referenced file exists, and all "
      "declarative widgets are ones the renderer implements")
