# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/timing.py (minimum overlay duration)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules.spore_studio import timing as t


@pytest.mark.parametrize(
    "value,default,expected",
    [(None, 1.5, 1.5), ("", 2.0, 2.0), ("3.5", 0.0, 3.5), (4, 0.0, 4.0),
     ("abc", 7.0, 7.0), ([1], 9.0, 9.0)],
)
def test_coerce_float(value, default, expected):
    assert t._coerce_float(value, default) == expected


@pytest.mark.parametrize(
    "value,default,expected",
    [(None, 3, 3), ("", 4, 4), ("2.9", 0, 2), (7, 0, 7), ("x", 5, 5),
     ({}, 6, 6), (float("inf"), 8, 8)],
)
def test_coerce_int(value, default, expected):
    assert t._coerce_int(value, default) == expected


def test_coerce_defaults():
    assert t._coerce_float(None) == 0.0
    assert t._coerce_int(None) == 0


@pytest.mark.parametrize(
    "anim,ms,expected",
    [("none", 500, 0), ("bogus", 500, 0), ("fade", 250, 250), ("slideIn", None, 300),
     ("scaleOut", -50, 0), ("slideOut", "x", 300)],
)
def test_anim_ms_for_type(anim, ms, expected):
    assert t._anim_ms_for_type(anim, ms) == expected


def test_element_animations_non_dict():
    assert t._element_animations_timeline_ms({"animations": "fade"}) == 0
    assert t._element_animations_timeline_ms({}) == 0


def test_element_animations_negative_delay_clamped():
    el = {"animations": {"anim_in": " fade ", "anim_in_ms": 100, "anim_delay_ms": -500,
                         "anim_out": None}}
    assert t._element_animations_timeline_ms(el) == 100


def test_binding_step_show_for_uses_element_anim_lengths():
    el = {"animations": {"anim_delay_ms": 200, "anim_in_ms": 400, "anim_out_ms": 600}}
    # seconds window (1000) > delay+in (600); then out 600
    assert t._binding_step_ms("show_for", {"seconds": 1}, el) == 1600


def test_binding_step_show_for_entrance_longer_than_window():
    el = {"animations": {"anim_delay_ms": 900, "anim_in_ms": 400, "anim_out_ms": 0}}
    assert t._binding_step_ms("show_for", {"seconds": 0.5, "anim_out": "none"}, el) == 1300


def test_binding_step_show_for_defaults_and_bad_values():
    # default seconds 5 + default fade out 300
    assert t._binding_step_ms("show_for", {}, None) == 5300
    # infinite seconds falls back to 5
    assert t._binding_step_ms("show_for", {"seconds": "inf", "anim_in": "", "anim_out": ""}) == 5000
    # negative seconds -> 0 window; fade in 300 (delay 0) wins, then fade out 300
    assert t._binding_step_ms("show_for", {"seconds": -3}, {"animations": "bad"}) == 600


def test_binding_step_flash_and_other():
    assert t._binding_step_ms("flash_class", {}) == 500
    assert t._binding_step_ms("flash_class", {"duration_ms": "1200"}) == 1200
    assert t._binding_step_ms("set_text", {"x": 1}) == 0
    assert t._binding_step_ms("show", None) == 0


def test_chain_timeline_non_dict():
    assert t._binding_chain_timeline_ms("x") == 0


def test_chain_timeline_overlapping_steps():
    binding = {
        "action": "flash_class",
        "args": {"duration_ms": 400},
        "chain": [
            "skip-me",
            {"delay_ms": 100, "action": "flash_class", "args": {"duration_ms": 1000}},
            {"delay_ms": 200},  # delay counts even without action
            {"delay_ms": "nan", "action": "flash_class", "args": "bad"},
            {"delay_ms": -50, "action": 5},
            {"delay_ms": 50, "action": "flash_class", "args": {"duration_ms": 100}},
        ],
    }
    # starts: 100 -> end 1100; 300 (no action); nan->0 => 300, flash default 500 -> 800;
    # -50 -> 0, invalid action; +50 = 350 -> 450.  Max = 1100
    assert t._binding_chain_timeline_ms(binding) == 1100


def test_chain_timeline_no_top_action_args_non_dict():
    binding = {"action": "flash_class", "args": ["x"], "chain": None}
    assert t._binding_chain_timeline_ms(binding) == 500
    assert t._binding_chain_timeline_ms({"action": 3}) == 0


def test_element_bindings_timeline_skips_non_dicts():
    el = {"bindings": ["x", {"action": "flash_class"}, {"action": "flash_class",
                                                         "args": {"duration_ms": 900}}]}
    assert t._element_bindings_timeline_ms(el) == 900
    assert t._element_bindings_timeline_ms({"bindings": None}) == 0


def test_media_fade_audio_video_and_other():
    assert t._element_media_fade_ms({"type": "AUDIO", "props": {"fade_in_ms": 100,
                                                                "fade_out_ms": "200"}}) == 300
    assert t._element_media_fade_ms({"type": "video", "props": {"audio_fade_in_ms": 50,
                                                                "audio_fade_out_ms": 70}}) == 120
    assert t._element_media_fade_ms({"type": "text", "props": {"fade_in_ms": 100}}) == 0
    assert t._element_media_fade_ms({"type": None, "props": None}) == 0


def test_element_timeline_picks_max_and_rejects_non_dict():
    assert t.element_timeline_ms("x") == 0
    el = {"type": "audio", "props": {"fade_in_ms": 4000},
          "animations": {"anim_in": "fade", "anim_in_ms": 100},
          "bindings": [{"action": "flash_class"}]}
    assert t.element_timeline_ms(el) == 4000


def test_compute_min_duration_rounds_up_to_tenth():
    model = {"elements": [{"type": "audio", "props": {"fade_in_ms": 1234}}, "junk"]}
    assert t.compute_min_duration_seconds(model) == 1.3


def test_compute_min_duration_floor_and_non_dict():
    assert t.compute_min_duration_seconds("x") == 5.0
    assert t.compute_min_duration_seconds({"elements": None}) == 5.0
    model = {"elements": [{"type": "audio", "props": {"fade_in_ms": 1}}]}
    assert t.compute_min_duration_seconds(model) == 0.1


@pytest.mark.parametrize(
    "raw,expected",
    [(None, 5.0), ("", 5.0), ("bad", 5.0), ([1], 5.0), ("12", 12.0), (0.5, 2.0)],
)
def test_effective_duration(raw, expected):
    model = {"duration_seconds": raw,
             "elements": [{"type": "audio", "props": {"fade_in_ms": 2000}}]}
    assert t.effective_duration_seconds(model) == expected


def test_effective_duration_non_dict():
    assert t.effective_duration_seconds(None) == 5.0
