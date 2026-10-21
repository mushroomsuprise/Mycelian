#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Compile checks for Spore Studio list, wheel, now playing, QR, embed, and stat card."""

from __future__ import annotations

import unittest

from modules.spore_studio.behavior_blocks import compile_bindings
from modules.spore_studio.template_codegen import compile_model


def _el(eid: str, etype: str, **extra):
    base = {
        "id": eid,
        "type": etype,
        "category": "Widgets",
        "position": {"x": 0, "y": 0},
        "size": {"w": 120, "h": 80},
        "props": extra.get("props") or {},
        "bindings": extra.get("bindings") or [],
    }
    for key in ("wheel_segments", "stat_source"):
        if key in extra:
            base[key] = extra[key]
    return base


class SporeStudioWidgetTests(unittest.TestCase):
    def test_widgets_compile_into_html_and_config(self):
        model = {
            "template_name": "widget_lab",
            "alert_system": "instant",
            "design": {"width": 800, "height": 600},
            "elements": [
                _el(
                    "feed",
                    "list",
                    props={
                        "max_items": 5,
                        "row_template": "{username} {value}",
                        "source": "alerts",
                        "font_size": 16,
                        "color": "#fff",
                    },
                ),
                _el(
                    "spin",
                    "wheel",
                    props={"spin_ms": 3000, "source": "static", "pointer": "top"},
                    wheel_segments=[
                        {"label": "A", "color": "#a855f7"},
                        {"label": "B", "color": "#22c55e"},
                    ],
                    bindings=[
                        {
                            "event": "instant_alert",
                            "filter": {},
                            "action": "wheel_spin",
                            "args": {},
                        }
                    ],
                ),
                _el(
                    "song",
                    "now_playing",
                    props={"show_art": True, "layout": "row", "bar_color": "#1db954"},
                ),
                _el("code", "qr", props={"text": "https://example.com", "foreground": "#111111"}),
                _el("frame", "embed", props={"url": "https://example.com", "scale": 1, "refresh_seconds": 0}),
                _el(
                    "bits",
                    "stat_card",
                    props={"label": "Bits", "format": "{value}", "icon": "★"},
                    stat_source={"kind": "data_source", "source": "stats", "source_id": "total_bits"},
                ),
            ],
        }
        html, cfg = compile_model(model)
        for marker in (
            'data-spore-type="list"',
            'data-spore-type="wheel"',
            'data-spore-type="now_playing"',
            'data-spore-type="qr"',
            'data-spore-type="embed"',
            'data-spore-type="stat_card"',
            "sporeInitWidgets",
            "qrcode.js",
            "__sporeStatCards",
            "__sporeFeeds",
            "sporeWheelSpin",
        ):
            self.assertIn(marker, html, marker)
        self.assertIn("&quot;label&quot;: &quot;A&quot;", html)
        ids = {
            el["id"]
            for el in cfg.get("elements", [])
            if isinstance(el, dict) and el.get("id")
        }
        self.assertIn("feed_max_items", ids)
        self.assertIn("feed_row_template", ids)
        self.assertIn("code_text", ids)
        self.assertIn("code_foreground", ids)
        self.assertIn("frame_url", ids)
        self.assertIn("bits_label", ids)
        self.assertIn("spin_spin_ms", ids)
        bindings = compile_bindings(model["elements"])
        self.assertIn("sporeWheelSpin", bindings["js"])
        self.assertIn("spore-list", bindings["css"])

    def test_list_actions_compile(self):
        elements = [
            {
                "id": "feed",
                "type": "list",
                "bindings": [
                    {
                        "event": "instant_alert",
                        "filter": {},
                        "action": "list_add",
                        "args": {"text": "hello"},
                    },
                    {
                        "event": "refresh-alerts",
                        "filter": {},
                        "action": "list_clear",
                        "args": {},
                    },
                ],
            }
        ]
        out = compile_bindings(elements)
        self.assertIn("sporeListAdd", out["js"])
        self.assertIn("sporeListClear", out["js"])
        self.assertIn("hello", out["js"])

    def test_wheel_result_click_and_variance_stay_out_of_inline_style(self):
        import re

        model = {
            "template_name": "t_wheel",
            "design": {"width": 400, "height": 400},
            "elements": [
                _el(
                    "spin",
                    "wheel",
                    props={
                        "spin_ms": 3000,
                        "variance": True,
                        "click_sound": True,
                        "click_volume": 0.2,
                        "click_file": "click.mp3",
                        "show_result": True,
                        "result_place": "below",
                        "result_font_size": 22,
                        "result_color": "#ffee00",
                        "result_bold": False,
                        "result_format": "Winner: {label}",
                        "result_hold_s": 4,
                        "background_color": "#111111",
                    },
                    wheel_segments=[
                        {"label": "A", "color": "#a855f7"},
                        {"label": "B", "color": "#22c55e"},
                    ],
                )
            ],
        }
        html, _cfg = compile_model(model)
        for marker in (
            "data-spore-wheel-variance=",
            "data-spore-wheel-click=",
            "data-spore-wheel-click-volume=",
            "data-spore-wheel-click-file=",
            "data-spore-wheel-result=",
            "data-spore-wheel-result-place=",
            "data-spore-wheel-result-size=",
            "data-spore-wheel-result-hold=",
            "below",
            "Winner: {label}",
            "spore-wheel-pointer",
        ):
            self.assertIn(marker, html, marker)
        tag = re.search(r'<div id="spin"[^>]*>', html)
        self.assertIsNotNone(tag)
        style = re.search(r'style="([^"]*)"', tag.group(0)).group(1)
        for leaked in (
            "variance",
            "click-sound",
            "click-volume",
            "click-file",
            "show-result",
            "result-place",
            "result-font",
            "result-color",
            "result-bold",
            "result-format",
            "result-hold",
        ):
            self.assertNotIn(leaked, style, leaked)
        bindings = compile_bindings(model["elements"])
        self.assertIn("z-index: 3", bindings["css"])
        self.assertNotIn("border-top: 14px", bindings["css"])


class SporeStudioFilterRelevanceTests(unittest.TestCase):

    def test_alert_fields_carry_applicable_alert_types(self):
        from modules.spore_studio.data_source_registry import get_data_source_registry
        from modules.spore_studio.event_registry import get_event

        sources = {s["id"]: s for s in get_data_source_registry()["sources"]}
        self.assertEqual(sources["alert.raider_count"]["alert_types"], ["raid"])
        self.assertIn("giftsub", sources["alert.tier"]["alert_types"])
        self.assertNotIn("alert_types", sources["alert.username"])

        payload = {f["key"]: f for f in get_event("instant_alert")["payload"]}
        self.assertEqual(payload["level"]["alert_types"], ["hype_train"])
        self.assertNotIn("alert_types", payload["alert_type"])

    def test_blank_filter_values_are_not_compiled(self):
        from modules.spore_studio.behavior_blocks import _compile_filter

        self.assertEqual(_compile_filter({"tier": "", "username": None}), "true")
        compiled = _compile_filter({"alert_type": "raid", "tier": ""})
        self.assertIn("alert_type", compiled)
        self.assertNotIn("tier", compiled)


class SporeStudioWidgetFollowUpTests(unittest.TestCase):

    def test_list_emits_new_row_and_filter_attributes(self):
        model = {
            "template_name": "t_list",
            "design": {"width": 400, "height": 300},
            "elements": [
                _el(
                    "feed", "list",
                    props={
                        "alert_type": "donation",
                        "row_lifetime_s": 30,
                        "row_background": "#112233",
                        "row_padding": 6,
                        "row_radius": 4,
                    },
                )
            ],
        }
        html, _cfg = compile_model(model)
        self.assertIn("data-spore-list-alert-type=", html)
        self.assertIn("donation", html)
        self.assertIn("data-spore-list-lifetime=", html)
        self.assertIn("#112233", html)
        # List-only data props must not leak into the element's inline CSS.
        import re

        tag = re.search(r'<div id="feed"[^>]*>', html).group(0)
        style = re.search(r'style="([^"]*)"', tag).group(1)
        for leaked in ("row-padding", "row-radius", "row-background", "alert-type", "row-lifetime"):
            self.assertNotIn(leaked, style)

    def test_giveaway_events_are_registered(self):
        from modules.spore_studio.event_registry import get_event

        self.assertIn("username", [f["key"] for f in get_event("giveaway_entry")["payload"]])
        self.assertIn("winners", [f["key"] for f in get_event("giveaway_winner")["payload"]])
        self.assertIn("options", [f["key"] for f in get_event("poll_state")["payload"]])
        self.assertIn("winners", [f["key"] for f in get_event("poll_ended")["payload"]])

    def test_poll_widget_compiles(self):
        import re

        model = {
            "template_name": "poll_lab",
            "alert_system": "instant",
            "design": {"width": 480, "height": 640},
            "elements": [
                _el(
                    "poll1",
                    "poll",
                    props={
                        "layout": "vertical",
                        "display_mode": "bars",
                        "max_items": 4,
                        "rotator_enabled": True,
                        "show_timer": True,
                        "winner_mode": "highlight",
                        "font_size": 22,
                        "color": "#ffffff",
                        "bar_color": "#a855f7",
                    },
                )
            ],
        }
        html, cfg = compile_model(model)
        self.assertIn('data-spore-type="poll"', html)
        self.assertIn("spore-poll-options", html)
        self.assertIn(".spore-poll", html)
        ids = {
            el["id"]
            for el in cfg.get("elements", [])
            if isinstance(el, dict) and el.get("id")
        }
        self.assertIn("poll1_max_items", ids)
        self.assertIn("poll1_rotator_enabled", ids)
        tag = re.search(r'<div id="poll1"[^>]*>', html).group(0)
        style = re.search(r'style="([^"]*)"', tag).group(1)
        for leaked in ("display-mode", "rotator-enabled", "winner-mode", "max-items"):
            self.assertNotIn(leaked, style)


if __name__ == "__main__":
    unittest.main()
