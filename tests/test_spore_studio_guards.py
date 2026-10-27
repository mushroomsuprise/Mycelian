#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Spore Studio safety nets: cross-site write blocking and the Advanced JS block."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import web_engine  # noqa: E402
from modules.spore_studio import template_codegen as codegen  # noqa: E402
from modules.spore_studio import template_parser_back as parser_back  # noqa: E402


# --------------------------------------------------------------------------- cross-site writes


@pytest.mark.parametrize("origin, site, host, blocked", [
    (None, None, "127.0.0.1:5000", False),                       # Python callers, curl
    ("http://127.0.0.1:5000", "same-origin", "127.0.0.1:5000", False),
    ("http://localhost:5000", None, "localhost:5000", False),
    (None, "none", "127.0.0.1:5000", False),                     # typed into the address bar
    ("https://evil.example", "cross-site", "127.0.0.1:5000", True),
    ("http://127.0.0.1:8080", "same-site", "127.0.0.1:5000", True),  # another local port
    ("http://127.0.0.1:8080", None, "127.0.0.1:5000", True),
    ("null", None, "127.0.0.1:5000", True),                      # sandboxed page / file://
    (None, "cross-site", "127.0.0.1:5000", True),
])
def test_cross_site_detection(origin, site, host, blocked):
    assert web_engine._is_cross_site_request(origin, site, host) is blocked


def test_write_routes_refuse_other_sites():
    engine = web_engine.WebEngine(port=5999)
    client = engine.app.test_client()
    evil = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
    for path in ("/api/spore-studio/save", "/api/spore-studio/delete", "/api/spore-studio/create",
                 "/api/spore-studio/assets/demo/upload", "/api/spore-studio/containers/save",
                 "/api/template-preview-settings/resolution"):
        res = client.post(path, json={}, headers=evil)
        assert res.status_code == 403, path
    # Reads still work from anywhere (OBS, the Stream Deck plugin).
    with mock.patch("modules.spore_studio.event_registry.get_event_registry", return_value={"events": []}):
        assert client.get("/api/spore-studio/events", headers=evil).status_code == 200
    # Same-origin requests get past the guard (and fail validation here instead of writing).
    same = {"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"}
    res = client.post("/api/spore-studio/save", data="not json", content_type="application/json", headers=same)
    assert res.status_code != 403


# --------------------------------------------------------------------------- Advanced JS block


def _html_with_user_block(code: str) -> str:
    return codegen._replace_block(codegen._load_boilerplate("queue"), codegen._USER_BEGIN, codegen._USER_END, code)


def _model(**extra):
    model = {"template_name": "demo", "alert_system": "queue", "design": {"width": 400, "height": 200}, "elements": []}
    model.update(extra)
    return model


def test_clearing_advanced_js_removes_it_from_the_overlay():
    existing = _html_with_user_block("console.log('OLD_CODE');")
    html, _config = codegen.compile_model(_model(advanced_js=""), existing_html=existing)
    assert "OLD_CODE" not in html
    assert codegen.extract_user_js(html) == ""


def test_new_code_replaces_the_old_block():
    existing = _html_with_user_block("a();")
    html, _config = codegen.compile_model(_model(advanced_js="b();"), existing_html=existing)
    assert codegen.extract_user_js(html) == "b();"


def test_models_without_the_key_keep_the_html_block():
    existing = _html_with_user_block("keep();")
    html, _config = codegen.compile_model(_model(), existing_html=existing)
    assert codegen.extract_user_js(html) == "keep();"


def test_markers_inside_the_code_cant_cut_the_block_short():
    code = "firstStep();\n// SPORE_STUDIO:user-end\nleftOverStep();"
    html, _config = codegen.compile_model(_model(advanced_js=code), existing_html=_html_with_user_block(""))
    # Saving again with different code leaves nothing behind.
    html2, _config = codegen.compile_model(_model(advanced_js="replacement();"), existing_html=html)
    assert codegen.extract_user_js(html2) == "replacement();"
    assert "leftOverStep" not in html2


def test_loading_shows_the_code_the_overlay_runs(tmp_path):
    (tmp_path / "demo.html").write_text(_html_with_user_block("handEdited();"), encoding="utf-8")
    sidecar = {"advanced_js": ""}
    with mock.patch.object(parser_back, "get_template_path", lambda rel="": str(tmp_path / rel)):
        parser_back._hydrate_advanced_js_from_html("demo", sidecar)
    assert sidecar["advanced_js"] == "handEdited();"
    same = {"advanced_js": "handEdited();"}
    with mock.patch.object(parser_back, "get_template_path", lambda rel="": str(tmp_path / rel)):
        parser_back._hydrate_advanced_js_from_html("missing", same)
    assert same["advanced_js"] == "handEdited();"


# --------------------------------------------------------------------------- editor scripts


EDITOR = ROOT / "assets" / "default_assets" / "spore_studio" / "editor.js"
CONTAINERS = ROOT / "assets" / "default_assets" / "spore_studio" / "container_editor.js"


def test_editor_keeps_unsaved_work():
    js = EDITOR.read_text(encoding="utf-8")
    # A save only clears "Unsaved changes" when nothing changed while it ran.
    assert "(state.revision || 0) === sentRevision" in js
    # Ctrl+S commits the field being typed in; Ctrl+Z in a field undoes typing.
    assert "commitFocusedField();" in js and "if (undoKey && typingTarget(ev.target)) { return; }" in js
    # + New and leaving the page ask first.
    assert "confirmDiscard(state.model ? state.model.template_name" in js
    assert "window.MycelianContainerEditor" in js
    assert "window.confirm(" not in js and "!confirm(" not in js


def test_container_editor_keeps_unsaved_work():
    js = CONTAINERS.read_text(encoding="utf-8")
    assert "function discardContainerEdits()" in js
    assert "JSON.stringify(state.model) === sent" in js
    assert "isDirty: function ()" in js
    assert "!confirm(" not in js
