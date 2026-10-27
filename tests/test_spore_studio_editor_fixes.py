#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Spore Studio fixes: timing math, legacy write-back, container routes and editor behaviour."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import web_engine  # noqa: E402
from modules.spore_studio import containers  # noqa: E402
from modules.spore_studio import save_pipeline as sp  # noqa: E402
from modules.spore_studio.template_reverse_parser import _resolve_decl  # noqa: E402
from modules.spore_studio.timing import compute_min_duration_seconds, effective_duration_seconds  # noqa: E402

STUDIO = ROOT / "assets" / "default_assets" / "spore_studio"


def _el(bindings, **anims):
    el = {"id": "a", "type": "text", "props": {}, "bindings": bindings}
    if anims:
        el["animations"] = dict({"anim_in": "fade", "anim_out": "fade"}, **anims)
    return {"elements": [el]}


# --------------------------------------------------------------------------- timing


def test_exit_animation_length_comes_from_the_element():
    model = _el([{"action": "show_for", "args": {"seconds": 5}}], anim_out_ms=1500)
    assert compute_min_duration_seconds(model) == 6.5


def test_chain_steps_start_from_the_previous_step_start():
    chained = {"action": "show_for", "args": {"seconds": 5, "anim_in": "none", "anim_out": "none"},
               "chain": [{"delay_ms": 5000, "action": "show_for",
                          "args": {"seconds": 5, "anim_in": "none", "anim_out": "none"}}]}
    assert compute_min_duration_seconds(_el([chained])) == 10.0
    # A short delay overlaps the steps instead of adding them up.
    chained["chain"][0]["delay_ms"] = 1000
    assert compute_min_duration_seconds(_el([chained])) == 6.0


def test_bad_numbers_dont_crash_the_timing():
    model = _el([{"action": "show_for", "args": {"seconds": "nan"},
                  "chain": [{"delay_ms": "inf", "action": "flash_class", "args": {"duration_ms": "x"}}]}])
    assert compute_min_duration_seconds(model) > 0


def test_saved_duration_keeps_the_users_choice():
    js = (STUDIO / "editor.js").read_text(encoding="utf-8")
    assert "state.model.duration_seconds = Math.max(0.1, v);" in js
    src = (ROOT / "modules" / "spore_studio" / "save_pipeline.py").read_text(encoding="utf-8")
    assert 'model["duration_seconds"] = effective_duration_seconds(model)' not in src
    # The overlay still runs at least as long as the layout needs.
    long = _el([{"action": "show_for", "args": {"seconds": 9, "anim_in": "none", "anim_out": "none"}}])
    long["duration_seconds"] = 3
    assert effective_duration_seconds(long) == 9.0


# --------------------------------------------------------------------------- legacy write-back


@pytest.mark.parametrize("raw, bound", [
    ("{{ Left }}px", "Left"),
    ("{{ C }}", "C"),
    ("{{ Left|default(5) }}px", "Left"),
    ("linear-gradient(90deg, transparent 0%, {{ C }} 50%)", None),
    ("{{ Left }}px {{ Left }}px", None),
    ("{{ Half }}px", None),
    ("{{ Half }}", None),
])
def test_only_exact_values_are_written_back(raw, bound):
    _value, var_id = _resolve_decl(raw, {"Left": 20, "C": "#fff", "Half": 12.5})
    assert var_id == bound


# --------------------------------------------------------------------------- containers on stream


def test_missing_template_leaves_an_empty_spot_on_stream():
    raw = {"name": "L", "route": "layout", "width": 800, "height": 600,
           "slots": [{"id": "s", "template": "gone", "x": 0, "y": 0, "w": 100, "h": 100}]}
    with mock.patch.object(containers, "template_html_exists", return_value=False):
        page = containers.render_shell(raw)
    assert "Missing template" not in page


def test_explicit_routes_hand_off_when_their_file_is_gone(tmp_path):
    engine = web_engine.WebEngine(port=5998)
    assert callable(getattr(engine, "_template_fallback", None))
    src = (ROOT / "modules" / "web_engine.py").read_text(encoding="utf-8")
    assert src.count('fallback = getattr(') >= 2


def test_compile_errors_are_reported_not_500(monkeypatch):
    def boom(model, existing_html=None):
        raise ValueError("could not convert string to float: 'abc'")

    monkeypatch.setattr(sp.template_codegen, "compile_model", boom)
    with pytest.raises(sp.SporeStudioError, match="couldn't build the overlay"):
        sp._compile({"template_name": "x"}, None)


@pytest.mark.parametrize("value, expected", [("640", 640), ("abc", 800), (None, 800), ("-5", 800), ("12.7", 12)])
def test_create_size_input(value, expected):
    assert web_engine._int_or(value, 800) == expected


# --------------------------------------------------------------------------- editor scripts


def test_editor_behaviour_fixes_are_in_place():
    js = (STUDIO / "editor.js").read_text(encoding="utf-8")
    # Drag listeners only live while the mouse is down.
    assert 'document.addEventListener("mousemove", onDragMove);' in js
    assert 'document.removeEventListener("mousemove", onDragMove);' in js
    # A click that doesn't move doesn't mark the template unsaved.
    assert "A click that didn't move anything is just a selection." in js
    # Undo decides by pending edits, not JSON equality.
    assert "var pending = historyTimer !== null;" in js
    # Slow responses for an older template are ignored.
    assert "if (loadSeq !== state.loadSeq) { return; }" in js
    assert "state.model.template_name !== forTemplate" in js
    # The tour owns the keyboard; the outline follows canvas selection.
    assert 'document.body.classList.contains("ss-tour-running")' in js
    # Renames and duplicates keep references straight.
    assert "rewriteModelRefs({ target_timer_id: 1" in js
    assert "copy.counter.counter_id = cid;" in js
    assert "nextControlId(dc.elements)" in js
    # Timer "Custom" format and clock offset show their fields.
    assert "customTimerFormat[el.id] = true;" in js
    assert 'el.type === "clock" && entry.key === "timezone"' in js
    walkthrough = (STUDIO / "walkthrough.js").read_text(encoding="utf-8")
    assert 'document.body.classList.add("ss-tour-running");' in walkthrough


def test_container_canvas_keeps_iframes_in_place():
    js = (STUDIO / "container_editor.js").read_text(encoding="utf-8")
    assert 'canvas.textContent = "";' not in js
    assert "function createSlotFrame(slotId, src)" in js
    # Throttled measuring and per-axis offsets.
    assert "if (measureTimer) { return; }" in js
    assert 'frame.style.top = (slot.axis === "vertical" ? -origin.y : 0) + "px";' in js
    assert "state.splitPadding[slot.id] = true;" in js
    shell = (STUDIO / "container_shell.js").read_text(encoding="utf-8")
    assert "if (timer) {\r\n            return;\r\n        }" in shell or "if (timer) {\n            return;\n        }" in shell
