#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Fill colours accept gradients, and exposed style props follow their Source Settings value."""

from __future__ import annotations

import html
import re
import unittest

import jinja2

from modules.spore_studio.template_codegen import _css_kv, compile_model

GRADIENT = "linear-gradient(-45deg, #ffc107 40%, rgba(250, 250, 250, 0.5) 50%, #ffc107 60%)"
INLINE_ONLY = {"background_color": False, "color": False}


def _model(etype: str, props: dict, *, eid: str = "box", expose: dict | None = None) -> dict:
    element = {
        "id": eid,
        "type": etype,
        "category": "Layout",
        "position": {"x": 0, "y": 0},
        "size": {"w": 120, "h": 80},
        "props": props,
        "bindings": [],
    }
    if expose is not None:
        element["source_settings_expose"] = expose
    return {
        "template_name": "fill_lab",
        "alert_system": "instant",
        "design": {"width": 800, "height": 600},
        "elements": [element],
    }


def _inline_style(out_html: str, eid: str = "box") -> str:
    start = out_html.index(f'id="{eid}"')
    tag = out_html[start : out_html.index(">", start)]
    return html.unescape(tag.split('style="', 1)[1].split('"', 1)[0])


def _render(out_html: str, **values) -> str:
    """Render like the overlay server does (Flask: Jinja with autoescape)."""
    return jinja2.Environment(autoescape=True).from_string(out_html).render(**values)


def _rule(rendered: str, eid: str = "box") -> str:
    match = re.search(r"#" + eid + r" \{([^}]*)\}", rendered)
    assert match, rendered[:400]
    return match.group(1)


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
            out_html, _cfg = compile_model(
                _model(etype, {"background_color": GRADIENT, "color": "#fff"}, expose=INLINE_ONLY)
            )
            style = _inline_style(out_html)
            self.assertIn(f"background-image: {GRADIENT}", style, etype)
            self.assertNotIn("background-color: linear-gradient", style, etype)
            self.assertIn("color: #fff", style, etype)

    def test_image_sits_on_top_of_a_gradient_fill(self):
        props = {"background_color": GRADIENT, "background": "url('logo.png') center / cover no-repeat"}
        decls = _css_kv(props)
        self.assertEqual(decls[0], ("background", "url('logo.png') center / cover no-repeat"))
        # After the shorthand (which would reset it), image layer first, gradient underneath.
        self.assertEqual(decls[-1], ("background-image", f"url('logo.png'), {GRADIENT}"))


class ExposedStyleBindingTests(unittest.TestCase):
    PROPS = {"background_color": "rgba(15,23,42,0.82)", "color": "#ffffff", "font_size": 28, "opacity": 0.9}

    def test_exposed_props_leave_the_inline_style(self):
        out_html, cfg = compile_model(_model("text", dict(self.PROPS)))
        style = _inline_style(out_html)
        for css in ("background", "color:", "font-size", "opacity"):
            self.assertNotIn(css, style)
        ids = {e.get("id") for e in cfg["elements"] if isinstance(e, dict)}
        self.assertTrue({"box_background_color", "box_color", "box_font_size", "box_opacity"} <= ids)

    def test_defaults_render_the_editor_values(self):
        out_html, _cfg = compile_model(_model("text", dict(self.PROPS)))
        rule = _rule(_render(out_html))
        for css in ("background-color: rgba(15,23,42,0.82)", "color: #ffffff", "font-size: 28px", "opacity: 0.9"):
            self.assertIn(css, rule)
        self.assertNotIn("background-image", rule)

    def test_source_settings_values_apply(self):
        out_html, _cfg = compile_model(_model("text", dict(self.PROPS)))
        rule = _rule(_render(out_html, box_color="#ff0000", box_font_size=40, box_background_color="#00ff00"))
        self.assertIn("color: #ff0000", rule)
        self.assertIn("font-size: 40px", rule)
        self.assertIn("background-color: #00ff00", rule)

    def test_gradient_setting_becomes_a_background_image(self):
        out_html, _cfg = compile_model(_model("container", {"background_color": "#000"}))
        rule = _rule(_render(out_html, box_background_color=GRADIENT))
        self.assertIn(f"background-image: {GRADIENT}", rule)
        self.assertNotIn("background-color", rule)

    def test_setting_cannot_break_out_of_the_rule(self):
        out_html, _cfg = compile_model(_model("text", {"color": "#fff"}))
        rendered = _render(out_html, box_color="red; } body { display: none")
        self.assertEqual(_rule(rendered).strip(), "color: red  body  display: none;")

    def test_unexposed_and_non_identifier_ids_stay_inline(self):
        out_html, _cfg = compile_model(_model("text", {"color": "#123456"}, expose={"color": False}))
        self.assertIn("color: #123456", _inline_style(out_html))
        self.assertNotIn("#box {", out_html)
        dashed, _cfg = compile_model(_model("text", {"color": "#123456"}, eid="my-box"))
        self.assertIn("color: #123456", _inline_style(dashed, "my-box"))


if __name__ == "__main__":
    unittest.main()
