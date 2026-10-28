# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""The Spore Studio API snapshot the JS editor tests use still matches the Python registries.

tests/js/fixtures/studio_api.json feeds the editor tests (tests/js/editor*.test.mjs) the
responses of /api/spore-studio/*. When a registry changes, refresh the snapshot with:

    uv run python tests/test_js_fixture_snapshot.py

Fonts, templates and editor models depend on the local template folder, so they stay as
they are; only the registry responses below are regenerated.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SNAPSHOT = ROOT / "tests" / "js" / "fixtures" / "studio_api.json"
REGISTRY_ROUTES = {
    "events": "events",
    "data_sources": "data-sources",
    "data_catalog": "data-catalog",
    "control_actions": "control-actions",
}


def live_registries() -> dict:
    from modules.web_engine import WebEngine

    client = WebEngine(port=5996).app.test_client()
    return {key: client.get(f"/api/spore-studio/{route}").get_json() for key, route in REGISTRY_ROUTES.items()}


def test_snapshot_matches_the_python_registries():
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    live = live_registries()
    stale = [key for key in REGISTRY_ROUTES if snapshot.get(key) != live[key]]
    assert not stale, (
        f"tests/js/fixtures/studio_api.json is out of date for {', '.join(stale)}. "
        "Refresh it with: uv run python tests/test_js_fixture_snapshot.py"
    )


if __name__ == "__main__":
    import tempfile

    os.environ.setdefault("MYCELIAN_DATA_DIR", tempfile.mkdtemp(prefix="mycelian-snapshot-"))
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    data.update(live_registries())
    SNAPSHOT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"Updated {SNAPSHOT.relative_to(ROOT)}")
