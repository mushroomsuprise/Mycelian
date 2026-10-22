#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for Spore Studio counters, data displays, and registries."""

from __future__ import annotations

import unittest

from modules.spore_studio import (
    control_action_registry,
    data_source_registry,
    spore_data_codegen,
    template_codegen,
)
from modules.spore_studio.behavior_blocks import (
    COUNTER_IMAGE_TRANSITION_CSS,
    PROGRESS_BAR_CSS,
    compile_bindings,
)


class SporeStudioDataFeaturesTests(unittest.TestCase):
    def test_data_source_registry_has_alert_sources(self):
        reg = data_source_registry.get_data_source_registry()
        ids = {s["id"] for s in reg["sources"]}
        self.assertIn("alert.quantity", ids)
        self.assertIn("alert.gift_qty", ids)
        self.assertIn("alert.amt_cheered", ids)
        self.assertNotIn("fixed", ids)
        self.assertNotIn("random_int", ids)
        self.assertNotIn("random_float", ids)

    def test_data_source_registry_has_subscription_deltas(self):
        reg = data_source_registry.get_data_source_registry()
        ids = {s["id"] for s in reg["sources"]}
        cats = set(reg.get("categories") or [])
        self.assertIn("Subscriptions", cats)
        self.assertIn("sub.new_sub", ids)
        self.assertIn("sub.resub", ids)
        self.assertIn("sub.gift_sub", ids)
        sub_new = data_source_registry.get_data_source("sub.new_sub")
        self.assertTrue(sub_new.get("delta_only"))
        self.assertEqual(sub_new.get("category"), "Subscriptions")
        keys = reg.get("alert_payload_keys") or {}
        self.assertEqual(keys.get("alert.gift_qty"), "gift_qty")

    def test_control_action_registry_has_pause(self):
        reg = control_action_registry.get_control_action_registry()
        actions = {a["action"] for a in reg["actions"]}
        self.assertIn("pause_alerts", actions)
        self.assertIn("counter_adjust", actions)

    def test_compile_counter_and_display_js(self):
        model = {
            "template_name": "test_counter_tpl",
            "alert_system": "instant",
            "design": {"width": 400, "height": 100},
            "elements": [
                {
                    "id": "subs",
                    "type": "text",
                    "category": "Elements",
                    "text_mode": "counter",
                    "position": {"x": 0, "y": 0},
                    "size": {"w": 200, "h": 40},
                    "props": {"text": "0", "font_size": 24, "color": "#fff"},
                    "bindings": [],
                    "counter": {
                        "counter_id": "subs",
                        "initial_value": 0,
                        "format": "Subs: {value}",
                        "persist": True,
                        "database_path": "test_counter_tpl/counters",
                        "database_key": "subs",
                        "rules": [
                            {
                                "trigger": "event",
                                "event": "instant_alert",
                                "filter": {"alert_type": "sub"},
                                "operation": "increment",
                                "delta": {"kind": "fixed", "value": 1},
                            }
                        ],
                    },
                },
                {
                    "id": "name",
                    "type": "text",
                    "category": "Elements",
                    "text_mode": "data_display",
                    "position": {"x": 0, "y": 50},
                    "size": {"w": 200, "h": 40},
                    "props": {"text": "—", "font_size": 20, "color": "#fff"},
                    "bindings": [],
                    "data_display": {
                        "source": {"kind": "data_source", "source": "alert.username"},
                        "format": "@{value}",
                        "refresh_on": ["instant_alert"],
                        "default_text": "—",
                    },
                },
            ],
            "dynamic_controls": {
                "elements": [
                    {
                        "type": "button",
                        "id": "pause_btn",
                        "label": "Pause",
                        "action": "pause_alerts",
                        "button_text": "Pause",
                    }
                ]
            },
        }
        js = spore_data_codegen.compile_spore_data_features(model)
        self.assertIn("sporeCounterMeta", js)
        self.assertIn("sporeDataDisplays", js)
        self.assertIn("test_counter_tpl_pause_alerts", js)
        self.assertIn("instant_alert", js)

        html, cfg = template_codegen.compile_model(model)
        self.assertIn('data-spore-text-mode="counter"', html)
        self.assertIn('data-spore-text-mode="data_display"', html)
        self.assertIn("sporeResolveSource", html)
        self.assertIn("subs_format", html)
        self.assertIn("dynamic_controls", cfg)
        self.assertEqual(len(cfg["dynamic_controls"]["elements"]), 1)
        cfg_ids = {
            el["id"]
            for el in cfg.get("elements", [])
            if isinstance(el, dict) and "id" in el
        }
        self.assertIn("subs_format", cfg_ids)
        self.assertNotIn("subsText", cfg_ids)

    def test_counter_adjust_binding_compiles(self):
        elements = [
            {
                "id": "btn",
                "type": "text",
                "bindings": [
                    {
                        "event": "instant_alert",
                        "filter": {},
                        "action": "counter_adjust",
                        "args": {
                            "counter_id": "subs",
                            "operation": "increment",
                            "delta_kind": "fixed",
                            "delta_value": 2,
                        },
                    }
                ],
            }
        ]
        out = compile_bindings(elements)
        self.assertIn("sporeCounterAdjust", out["js"])

    def test_value_animation_and_counter_image_features(self):
        model = {
            "template_name": "feat_tpl",
            "alert_system": "instant",
            "design": {"width": 400, "height": 200},
            "elements": [
                {
                    "id": "cnt",
                    "type": "text",
                    "category": "Elements",
                    "text_mode": "counter",
                    "position": {"x": 0, "y": 0},
                    "size": {"w": 120, "h": 40},
                    "props": {
                        "text": "0",
                        "font_size": 24,
                        "vertical_align": "center",
                    },
                    "bindings": [],
                    "counter": {
                        "counter_id": "bits",
                        "initial_value": 0,
                        "format": "{value}",
                        "rules": [],
                    },
                    "value_animation": {
                        "enabled": True,
                        "type": "fade-in",
                        "duration_ms": 400,
                        "easing": "ease-out",
                        "pulse": False,
                    },
                },
                {
                    "id": "pic",
                    "type": "image",
                    "category": "Elements",
                    "src_mode": "from_counter",
                    "counter_src": {
                        "counter_id": "bits",
                        "ranges": [
                            {"min": 0, "max": 99, "src": "/assets/feat_tpl/low.png"},
                            {"min": 100, "max": 9999, "src": "/assets/feat_tpl/high.png"},
                        ],
                        "default_src": "/assets/feat_tpl/fallback.png",
                    },
                    "position": {"x": 10, "y": 50},
                    "size": {"w": 64, "h": 64},
                    "props": {"src": ""},
                    "bindings": [],
                    "counter_image_transition": {
                        "enabled": True,
                        "type": "crossfade",
                        "duration_ms": 350,
                        "easing": "ease-in-out",
                    },
                },
                {
                    "id": "box",
                    "type": "container",
                    "category": "Elements",
                    "position": {"x": 200, "y": 0},
                    "size": {"w": 100, "h": 80},
                    "props": {},
                    "bindings": [],
                },
                {
                    "id": "lbl",
                    "type": "text",
                    "category": "Elements",
                    "parent_id": "box",
                    "placement": {
                        "anchor_h": "center",
                        "anchor_v": "center",
                        "offset_x": 0,
                        "offset_y": 0,
                    },
                    "position": {"x": 0, "y": 0},
                    "size": {"w": 80, "h": 24},
                    "props": {"text": "Hi", "font_size": 16},
                    "bindings": [],
                },
            ],
        }
        js = spore_data_codegen.compile_spore_data_features(model)
        self.assertIn("value_animation", js)
        self.assertIn("__sporeCounterImages", js)
        self.assertIn("pic_range_0_src", js)
        self.assertIn("pic_counter_default_src", js)
        self.assertIn("range_transition", js)
        self.assertIn("crossfade", js)
        self.assertIn("cnt_format", js)
        self.assertNotIn("cntText", js)

        html, cfg = template_codegen.compile_model(model)
        self.assertIn("justify-content: center", html)
        self.assertIn('data-spore-src-mode="counter"', html)
        self.assertIn("sporeValueFadeIn", html)
        self.assertIn("cnt_format", html)
        self.assertIn("pic_counter_default_src", html)

        cfg_ids = {
            el["id"]
            for el in cfg.get("elements", [])
            if isinstance(el, dict) and "id" in el
        }
        self.assertIn("cnt_format", cfg_ids)
        self.assertIn("pic_counter_default_src", cfg_ids)
        self.assertIn("pic_range_0_src", cfg_ids)
        self.assertIn("pic_range_1_src", cfg_ids)
        self.assertNotIn("picSrc", cfg_ids)
        self.assertNotIn("cntText", cfg_ids)
        design_w = next(
            el for el in cfg["elements"] if el.get("id") == "DesignWidth"
        )
        self.assertEqual(design_w.get("min"), 50)

        bindings = compile_bindings(model["elements"])
        self.assertIn("spore-ci-fade-out", COUNTER_IMAGE_TRANSITION_CSS)
        self.assertIn("spore-ci-fade-out", bindings["css"])

    def test_font_registry_and_tick_up_meta(self):
        from modules.spore_studio import fonts_registry

        reg = fonts_registry.get_font_registry()
        self.assertIn("fonts", reg)
        self.assertEqual(fonts_registry.resolve_font_filename("Renogare"), "Renogare.ttf")
        model = {
            "template_name": "font_tick",
            "alert_system": "instant",
            "design": {"width": 200, "height": 80},
            "elements": [
                {
                    "id": "n",
                    "type": "text",
                    "text_mode": "counter",
                    "position": {"x": 0, "y": 0},
                    "size": {"w": 100, "h": 30},
                    "props": {"font_family": "Renogare", "font_size": 20},
                    "counter": {"counter_id": "n", "initial_value": 0, "rules": []},
                    "value_animation": {
                        "enabled": True,
                        "type": "tick_up",
                        "duration_ms": 300,
                        "easing": "ease-out",
                        "pulse": False,
                    },
                }
            ],
        }
        js = spore_data_codegen.compile_spore_data_features(model)
        self.assertIn('"type": "tick_up"', js)
        html, _ = template_codegen.compile_model(model)
        self.assertIn("@font-face", html)
        self.assertIn("Renogare.ttf", html)
        self.assertIn("spore-el-n", html)
        self.assertIn("Renogare.ttf", html)

    def test_progress_bar_fixed_max_compile(self):
        model = {
            "template_name": "prog_fixed",
            "alert_system": "instant",
            "design": {"width": 500, "height": 120},
            "elements": [
                {
                    "id": "bits",
                    "type": "text",
                    "category": "Counters",
                    "text_mode": "counter",
                    "position": {"x": 0, "y": 0},
                    "size": {"w": 200, "h": 40},
                    "props": {"color": "#fff", "font_size": 24},
                    "bindings": [],
                    "counter": {
                        "counter_id": "bit_count",
                        "initial_value": 250,
                        "format": "{value}",
                        "rules": [],
                    },
                },
                {
                    "id": "cheer_bar",
                    "type": "progress_bar",
                    "category": "Progress",
                    "position": {"x": 0, "y": 50},
                    "size": {"w": 400, "h": 28},
                    "props": {
                        "track_color": "#1e293b",
                        "fill_color": "#a855f7",
                        "border_radius": 8,
                        "near_goal_threshold": 90,
                        "near_goal_effect": "pulse",
                    },
                    "progress_source": {
                        "counter_id": "bit_count",
                        "max_kind": "fixed",
                        "max": 1000,
                    },
                    "bindings": [],
                    "source_settings_expose": {
                        "max": True,
                        "fill_color": True,
                    },
                },
            ],
        }
        js = spore_data_codegen.compile_spore_data_features(model)
        self.assertIn("__sporeProgressBars", js)
        self.assertIn('"counter_id": "bit_count"', js)
        self.assertIn('"near_goal_threshold": 90', js)
        self.assertIn('"near_goal_effect"', js)
        self.assertIn("spore-progress-near-pulse", PROGRESS_BAR_CSS)
        self.assertIn("spore-progress-near-shimmer", PROGRESS_BAR_CSS)
        self.assertIn("spore-progress-near-scroll", PROGRESS_BAR_CSS)
        self.assertIn("sporeUpdateAllProgressBars", js)

        html, cfg = template_codegen.compile_model(model)
        self.assertIn('data-spore-type="progress_bar"', html)
        self.assertIn('data-spore-counter-id="bit_count"', html)
        self.assertIn('class="spore-progress-fill"', html)
        self.assertNotIn("spore-progress-label", html)
        self.assertIn("sporeUpdateProgressBars", html)

        bindings = compile_bindings(model["elements"])
        self.assertIn("spore-progress-bar", PROGRESS_BAR_CSS)
        self.assertIn("spore-progress-bar", bindings["css"])

        cfg_ids = {
            el["id"]
            for el in cfg.get("elements", [])
            if isinstance(el, dict) and "id" in el
        }
        self.assertIn("cheer_bar_max", cfg_ids)
        self.assertIn("cheer_bar_fill_color", cfg_ids)
        self.assertNotIn("cheer_bar_label_format", cfg_ids)

    def test_counter_format_min_max_tokens(self):
        model = {
            "template_name": "fmt_tpl",
            "alert_system": "instant",
            "design": {"width": 200, "height": 60},
            "elements": [
                {
                    "id": "cnt",
                    "type": "text",
                    "text_mode": "counter",
                    "position": {"x": 0, "y": 0},
                    "size": {"w": 120, "h": 30},
                    "props": {},
                    "counter": {
                        "counter_id": "cnt",
                        "initial_value": 5,
                        "min": 0,
                        "max": 100,
                        "format": "{value}/{max}",
                        "rules": [],
                    },
                }
            ],
        }
        html, _cfg = template_codegen.compile_model(model)
        self.assertIn("5/100", html)
        js = spore_data_codegen.compile_spore_data_features(model)
        self.assertIn("sporeFormatCounterDisplay", html)
        self.assertIn('"min": 0', js)
        self.assertIn('"max": 100', js)

    def test_progress_bar_counter_goal_compile(self):
        model = {
            "template_name": "prog_goal",
            "alert_system": "instant",
            "design": {"width": 500, "height": 120},
            "elements": [
                {
                    "id": "current",
                    "type": "text",
                    "category": "Counters",
                    "text_mode": "counter",
                    "position": {"x": 0, "y": 0},
                    "size": {"w": 200, "h": 40},
                    "props": {"color": "#fff"},
                    "bindings": [],
                    "counter": {
                        "counter_id": "current_val",
                        "initial_value": 40,
                        "format": "{value}",
                        "rules": [],
                    },
                },
                {
                    "id": "goal",
                    "type": "text",
                    "category": "Counters",
                    "text_mode": "counter",
                    "position": {"x": 0, "y": 0},
                    "size": {"w": 200, "h": 40},
                    "props": {"color": "#fff"},
                    "bindings": [],
                    "counter": {
                        "counter_id": "goal_val",
                        "initial_value": 100,
                        "format": "{value}",
                        "rules": [],
                    },
                },
                {
                    "id": "goal_bar",
                    "type": "progress_bar",
                    "category": "Progress",
                    "position": {"x": 0, "y": 50},
                    "size": {"w": 400, "h": 28},
                    "props": {
                        "track_color": "#222",
                        "fill_color": "#0f0",
                    },
                    "progress_source": {
                        "counter_id": "current_val",
                        "max_kind": "counter",
                        "max": 100,
                        "max_counter_id": "goal_val",
                    },
                    "bindings": [],
                },
            ],
        }
        js = spore_data_codegen.compile_spore_data_features(model)
        self.assertIn('"max_kind": "counter"', js)
        self.assertIn('"max_counter_id": "goal_val"', js)

        html, _cfg = template_codegen.compile_model(model)
        self.assertIn('data-spore-max-counter-id="goal_val"', html)
        self.assertIn('data-spore-max-kind="counter"', html)

    def test_sub_delta_counter_rule_compile(self):
        model = {
            "template_name": "sub_delta_tpl",
            "alert_system": "instant",
            "design": {"width": 400, "height": 100},
            "elements": [
                {
                    "id": "sub_counter",
                    "type": "text",
                    "category": "Counters",
                    "text_mode": "counter",
                    "position": {"x": 0, "y": 0},
                    "size": {"w": 200, "h": 40},
                    "props": {"color": "#fff"},
                    "bindings": [],
                    "counter": {
                        "counter_id": "subs",
                        "initial_value": 0,
                        "format": "{value}",
                        "rules": [
                            {
                                "trigger": "event",
                                "event": "instant_alert",
                                "filter": {},
                                "operation": "increment",
                                "delta": {
                                    "kind": "data_source",
                                    "source": "sub.gift_sub",
                                    "tier_filter": "2",
                                    "fallback": 0,
                                },
                            }
                        ],
                    },
                }
            ],
        }
        js = spore_data_codegen.compile_spore_data_features(model)
        self.assertIn('"source": "sub.gift_sub"', js)
        self.assertIn('"tier_filter": "2"', js)
        self.assertIn("instant_alert", js)

    def test_registry_dedupes_sources_and_adds_alert_fields(self):
        reg = data_source_registry.get_data_source_registry()
        ids = [s["id"] for s in reg["sources"]]
        self.assertEqual(len(ids), len(set(ids)))
        for gone in (
            "fixed",
            "random_int",
            "random_float",
            "chatbot.gift_sub_quantity",
            "chatbot.gift_sub_tier",
            "chatbot.raid_viewer_count",
            "counter.{id}",
            "config.{id}",
            "stats.total_bits",
        ):
            self.assertNotIn(gone, ids)
        for kept in ("counter", "config", "runtime", "twitch", "stats", "sub.new_sub"):
            self.assertIn(kept, ids)
        username = data_source_registry.get_data_source("alert.username")
        self.assertTrue(username.get("display_only"))
        bits = data_source_registry.get_data_source("alert.amt_cheered")
        self.assertFalse(bits.get("display_only"))
        keys = reg["alert_payload_keys"]
        for key in (
            "recipient",
            "alert_name",
            "streak_count",
            "channel_points_awarded",
            "level",
            "total",
            "duration",
            "timestamp",
        ):
            self.assertEqual(keys["alert." + key], key)
        stat_ids = {row["id"] for row in reg["session_stat_keys"]}
        self.assertIn("total_bits", stat_ids)
        self.assertIn("follows", stat_ids)
        type_ids = {row["id"] for row in reg["alert_types"]}
        self.assertIn("giftsub", type_ids)
        self.assertIn("streak", type_ids)

    def test_counter_rule_compiles_structured_source_and_filter(self):
        model = {
            "template_name": "structured_src",
            "alert_system": "instant",
            "design": {"width": 400, "height": 80},
            "elements": [
                {
                    "id": "bits",
                    "type": "text",
                    "category": "Elements",
                    "text_mode": "counter",
                    "position": {"x": 0, "y": 0},
                    "size": {"w": 120, "h": 40},
                    "props": {"text": "0"},
                    "bindings": [],
                    "counter": {
                        "counter_id": "bits",
                        "initial_value": 0,
                        "format": "{value}",
                        "rules": [
                            {
                                "trigger": "event",
                                "event": "instant_alert",
                                "filter": {"alert_type": "bit"},
                                "operation": "increment",
                                "delta": {
                                    "kind": "data_source",
                                    "source": "alert.amt_cheered",
                                    "alert_type_filter": "bit",
                                    "fallback": 0,
                                },
                            },
                            {
                                "trigger": "event",
                                "event": "instant_alert",
                                "filter": {},
                                "operation": "increment",
                                "delta": {
                                    "kind": "data_source",
                                    "source": "counter",
                                    "source_id": "bits",
                                    "fallback": 0,
                                },
                            },
                        ],
                    },
                }
            ],
        }
        js = spore_data_codegen.compile_spore_data_features(model)
        self.assertIn('"alert_type_filter": "bit"', js)
        self.assertIn('"source": "counter"', js)
        self.assertIn('"source_id": "bits"', js)
        self.assertIn("sporeMatchesFilter", js)
        self.assertIn("alert.amt_cheered", js)
        html, _cfg = template_codegen.compile_model(model)
        self.assertIn("sporeEffectiveSourceId", html)
        self.assertIn("sporeSpecPassesAlertFilters", html)

    def test_progressive_goals_compile(self):
        self.assertEqual(
            spore_data_codegen.normalize_progressive_goals([100, 50, 200, 200, 300]),
            [100, 200, 300],
        )
        mid = spore_data_codegen.progressive_goal_state(150, [100, 200, 300], 0)
        self.assertEqual(mid["active_goal"], 200)
        self.assertEqual(mid["floor"], 100)
        self.assertEqual(mid["value"], 150)
        done = spore_data_codegen.progressive_goal_state(300, [100, 200, 300], 0)
        self.assertEqual(done["active_goal"], 300)
        self.assertEqual(done["floor"], 200)
        over = spore_data_codegen.progressive_goal_state(900, [100, 200, 300], 0)
        self.assertEqual(over["value"], 300)
        self.assertEqual(over["active_goal"], 300)

        model = {
            "template_name": "prog_goals",
            "alert_system": "instant",
            "design": {"width": 400, "height": 120},
            "elements": [
                {
                    "id": "bits",
                    "type": "counter",
                    "position": {"x": 0, "y": 0},
                    "size": {"w": 200, "h": 40},
                    "props": {},
                    "counter": {
                        "counter_id": "bits",
                        "initial_value": 150,
                        "min": 0,
                        "max": 75,
                        "progressive": True,
                        "goals": [100, 200, 300],
                        "format": "{value}/{max}",
                        "rules": [],
                    },
                },
                {
                    "id": "cheer_bar",
                    "type": "progress_bar",
                    "position": {"x": 0, "y": 50},
                    "size": {"w": 300, "h": 24},
                    "props": {},
                    "progress_source": {
                        "counter_id": "bits",
                        "max_kind": "fixed",
                        "max": 9999,
                    },
                },
            ],
        }
        html, _cfg = template_codegen.compile_model(model)
        self.assertIn("150/200", html)
        self.assertNotIn("150/75", html)
        self.assertNotIn("150/9999", html)
        js = spore_data_codegen.compile_spore_data_features(model)
        self.assertIn('"progressive": true', js)
        self.assertIn('"goals": [100, 200, 300]', js)
        self.assertIn('"max": null', js)
        self.assertIn('"follow_counter_goals": true', js)
        runtime = spore_data_codegen.load_data_runtime_js()
        self.assertIn("function sporeProgressiveBounds", runtime)
        self.assertIn("follow_counter_goals", runtime)
        goal_at = runtime.find("if (spec.follow_counter_goals)")
        fixed_at = runtime.find("return sporeCoerceNumber(spec.max, 100);", goal_at)
        self.assertGreater(goal_at, 0)
        self.assertGreater(fixed_at, goal_at)

    def test_transforms_compile(self):
        model = {
            "template_name": "tf_tpl",
            "alert_system": "instant",
            "design": {"width": 400, "height": 200},
            "canvas_transforms": [
                {
                    "id": "tf_canvas",
                    "enabled": True,
                    "property": "rotate",
                    "source": {
                        "kind": "value",
                        "value": {"kind": "fixed", "value": 10},
                        "refresh_ms": 0,
                        "refresh_on": [],
                    },
                    "in_min": 0,
                    "in_max": 10,
                    "out_min": 0,
                    "out_max": 45,
                    "origin": "center",
                },
                {
                    "id": "tf_bad",
                    "enabled": True,
                    "property": "not_a_property",
                    "source": {},
                    "in_min": 0,
                    "in_max": 1,
                    "out_min": 0,
                    "out_max": 1,
                },
            ],
            "elements": [
                {
                    "id": "cnt",
                    "type": "counter",
                    "position": {"x": 0, "y": 0},
                    "size": {"w": 120, "h": 40},
                    "props": {},
                    "counter": {
                        "counter_id": "cnt",
                        "initial_value": 0,
                        "format": "{value}",
                        "rules": [],
                    },
                    "transforms": [
                        {
                            "id": "tf_scale",
                            "enabled": True,
                            "property": "scale",
                            "source": {
                                "kind": "value",
                                "value": {
                                    "kind": "data_source",
                                    "source": "counter",
                                    "source_id": "cnt",
                                },
                            },
                            "in_min": 0,
                            "in_max": 1000,
                            "out_min": 1,
                            "out_max": 2,
                            "origin": "center",
                        }
                    ],
                }
            ],
        }
        js = spore_data_codegen.compile_spore_data_features(model)
        self.assertIn("window.__sporeTransforms", js)
        self.assertIn("sporeInitTransforms", js)
        self.assertIn('"property": "scale"', js)
        self.assertIn('"property": "rotate"', js)
        self.assertNotIn("not_a_property", js)
        self.assertIn('"source_id": "cnt"', js)
        runtime = spore_data_codegen.load_data_runtime_js()
        self.assertIn("function sporeApplyTransforms", runtime)
        self.assertIn("function sporeInitTransforms", runtime)


if __name__ == "__main__":
    unittest.main()
