#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Spore Studio output that must still render: ids, emoji, user text, names and copies."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import jinja2
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.spore_studio import behavior_blocks  # noqa: E402
from modules.spore_studio import save_pipeline as sp  # noqa: E402
from modules.spore_studio import template_codegen as tc  # noqa: E402
from modules.web_engine import WebEngine  # noqa: E402


def _model(elements, **extra):
    model = {"template_name": "safety_demo", "alert_system": "queue", "design": {"width": 800, "height": 200},
             "duration_seconds": 5, "elements": elements, "advanced_js": "",
             "streamdeck_options": {"description": "", "actions": {}}, "dynamic_controls": {"elements": []}}
    model.update(extra)
    return model


def _text(el_id, text="Hi", expose_text=True, bindings=None):
    return {"id": el_id, "type": "text", "category": "Main", "position": {"x": 0, "y": 0}, "size": {"w": 200, "h": 40},
            "props": {"text": text}, "source_settings_expose": {"text": expose_text}, "bindings": bindings or []}


def _render(model):
    html, config = tc.compile_model(model)
    variables = WebEngine._template_variable_map(config)
    out = jinja2.Environment(autoescape=True).from_string(html).render(
        **variables, mycelian_html_stem="safety_demo", mycelian_preview_mode=False
    )
    out.encode("utf-8")  # lone surrogates fail here, like the real response would
    return out


# --------------------------------------------------------------------------- element ids


@pytest.mark.parametrize("bad", ["my-title", "1title", "Follow Text", "", "a.b"])
def test_save_refuses_ids_the_overlay_cant_use(bad):
    with pytest.raises(sp.SporeStudioError, match="Element IDs can only use"):
        sp._check_element_ids(_model([_text(bad)]))


def test_save_refuses_duplicate_ids():
    with pytest.raises(sp.SporeStudioError, match="share the ID"):
        sp._check_element_ids(_model([_text("a"), _text("a")]))


def test_good_ids_pass():
    sp._check_element_ids(_model([_text("title"), _text("_x2"), _text("Winner_Name")]))


def test_save_template_checks_ids_before_writing():
    with mock.patch.object(sp, "save_sidecar_model", create=True), \
            mock.patch.object(sp.template_codegen, "compile_model") as compile_model:
        with pytest.raises(sp.SporeStudioError, match="Element IDs"):
            sp.save_template(_model([_text("bad-id")], template_name="zz_never_saved_template"))
        compile_model.assert_not_called()


def test_editor_cleans_ids_as_you_type():
    js = (ROOT / "assets" / "default_assets" / "spore_studio" / "editor.js").read_text(encoding="utf-8")
    assert "function safeElementId(text)" in js
    assert "var newId = safeElementId(typed);" in js


# --------------------------------------------------------------------------- rendering


def test_emoji_in_unexposed_text_renders():
    out = _render(_model([_text("greeting", "Hi \U0001F600", expose_text=False)]))
    assert "Hi \U0001F600" in out


def test_binding_text_is_not_read_as_jinja():
    bindings = [{"event": "next_alert", "action": "set_text", "args": {"literal": "{{ user }} followed"}},
                {"event": "next_alert", "action": "set_text", "args": {"literal": "Top fan {#1}"}},
                {"event": "next_alert", "action": "set_text", "args": {"literal": "</script><b>x</b>"}}]
    out = _render(_model([_text("who", bindings=bindings)]))
    assert "\\u007b\\u007b user }} followed" in out
    assert "Top fan \\u007b#1}" in out
    assert "</script><b>" not in out


def test_js_values_keep_object_syntax():
    js = behavior_blocks._js_value({"text": "{x}", "n": 1})
    assert js.startswith("{") and js.endswith("}")
    assert '"\\u007bx}"' in js


# --------------------------------------------------------------------------- names and copies


@pytest.mark.parametrize("name", ["café", "名前", "x²", "default_assets", "Default_Assets"])
def test_template_names_must_be_plain_ascii_and_not_reserved(name):
    with pytest.raises(sp.SporeStudioError):
        sp._validate_name(name)


def test_legacy_names_keep_their_spaces(tmp_path):
    (tmp_path / "boo chat.html").write_text("<html></html>", encoding="utf-8")
    with mock.patch.object(sp, "get_template_path", lambda rel="": str(tmp_path / rel)):
        assert sp._validate_legacy_name("boo chat") == "boo chat"
        for bad in ("../x", "a/b", "_private", "missing one"):
            with pytest.raises(sp.SporeStudioError):
                sp._validate_legacy_name(bad)


def test_create_refuses_a_name_whose_assets_folder_is_in_use(tmp_path):
    templates = tmp_path / "templates"
    assets = tmp_path / "assets"
    (assets / "orphan").mkdir(parents=True)
    (assets / "orphan" / "keep.png").write_bytes(b"x")
    templates.mkdir()
    with mock.patch.object(sp, "get_template_path", lambda rel="": str(templates / rel)), \
            mock.patch.object(sp, "get_assets_path", lambda rel="": str(assets / rel)):
        with pytest.raises(sp.SporeStudioError, match="already exists"):
            sp.create_template("orphan")
    assert (assets / "orphan" / "keep.png").exists()


def test_copy_only_moves_whole_counter_paths():
    model = {"a": "subs/counters/total", "b": "bitsubs/counters/total", "c": "/assets/subs/img.png",
             "d": ["subs/counters"], "e": "x subs/counters"}
    out = sp._rewrite_copied_strings(model, "subs", "new")
    assert out == {"a": "new/counters/total", "b": "bitsubs/counters/total", "c": "/assets/new/img.png",
                   "d": ["new/counters"], "e": "x new/counters"}


def test_save_refuses_to_turn_a_handwritten_overlay_into_a_spore_one(tmp_path):
    (tmp_path / "hand_made.html").write_text("<html>{{ Title }}</html>", encoding="utf-8")
    (tmp_path / "spore_made.html").write_text("<!-- SPORE_STUDIO:dom-begin --><!-- SPORE_STUDIO:dom-end -->", encoding="utf-8")
    with mock.patch.object(sp, "get_template_path", lambda rel="": str(tmp_path / rel)), \
            mock.patch.object(sp.template_parser_back, "load_sidecar", lambda name: None):
        assert sp._is_handwritten_template("hand_made") is True
        assert sp._is_handwritten_template("spore_made") is False
        assert sp._is_handwritten_template("brand_new") is False
    with pytest.raises(sp.SporeStudioError, match="built-in overlay"):
        sp.save_template(_model([], template_name="activity_feed"))
