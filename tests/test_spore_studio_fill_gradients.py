#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Fill colours (background_color) accept gradients in exported Spore templates."""

from __future__ import annotations

import html
import unittest

from modules.spore_studio.template_codegen import _css_kv, compile_model

GRADIENT = "linear-gradient(-45deg, #ffc107 40%, rgba(250, 250, 250, 0.5) 50%, #ffc107 60%)"


def _model(etype: str, props: dict) -> dict:
    return {
        "template_name": "fill_lab",
        "alert_system": "instant",
        "design": {"width": 800, "height": 600},
        "elements": [
            {
                "id": "box",
                "type": etype,
                "category": "Layout",
                "position": {"x": 0, "y": 0},
                "size": {"w": 120, "h": 80},
                "props": props,
                "bindings": [],
            }
        ],
    }


def _element_style(etype: str, props: dict) -> str:
    out_html, _cfg = compile_model(_model(etype, props))
    start = out_html.index('id="box"')
    tag = out_html[start : out_html.index(">", start)]
    style = tag.split('style="', 1)[1].split('"', 1)[0]
    return html.unescape(style)


class FillGradientTests(unittest.TestCase):
    def test_solid_fills_are_unchanged(self):
        for value in ("#123456", "rgba(0, 0, 0, 0.5)", "transparent", "rgb(1, 2, 3)"):
            self.assertEqual(_css_kv({"background_color": value}), [("background-color", value)])

    def test_solid_fill_keeps_its_position_next_to_raw_background(self):
        props = {"background_color": "#000", "background": "url(a.png) no-repeat"}
        self.assertEqual(
            _css_kv(props),
            [("background-color", "#000"), ("background", "url(a.png) no-repeat")],
        )

    def test_gradient_fill_becomes_a_background_image_layer(self):
        self.assertEqual(_css_kv({"background_color": GRADIENT}), [("background-image", GRADIENT)])
        for etype in ("container", "text", "shape"):
            style = _element_style(etype, {"background_color": GRADIENT, "color": "#fff"})
            self.assertIn(f"background-image: {GRADIENT}", style, etype)
            self.assertNotIn("background-color: linear-gradient", style, etype)
            self.assertIn("color: #fff", style, etype)

    def test_image_sits_on_top_of_a_gradient_fill(self):
        props = {"background_color": GRADIENT, "background": "url('logo.png') center / cover no-repeat"}
        decls = _css_kv(props)
        self.assertEqual(decls[0], ("background", "url('logo.png') center / cover no-repeat"))
        # After the shorthand (which would reset it), image layer first, gradient underneath.
        self.assertEqual(decls[-1], ("background-image", f"url('logo.png'), {GRADIENT}"))

    def test_solid_compiled_style_matches_previous_output(self):
        style = _element_style("container", {"background_color": "rgba(115,0,255,0.04)"})
        self.assertIn("background-color: rgba(115,0,255,0.04)", style)
        self.assertNotIn("background-image", style)


if __name__ == "__main__":
    unittest.main()
