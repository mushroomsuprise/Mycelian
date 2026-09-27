#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Container model, size-link solver, and shell HTML."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.spore_studio.containers import (  # noqa: E402
    ContainerError,
    container_source_record,
    container_summary,
    create_container,
    embed_src,
    inject_embed_audio,
    normalize_container,
    render_shell,
    save_container,
    solve_layout,
    suggest_route,
    validate_route_syntax,
)
from modules.spore_studio.save_pipeline import (  # noqa: E402
    SporeStudioError,
    _validate_name,
)


def _container(slots, **kwargs):
    raw = {
        "name": "Layout",
        "route": "layout",
        "width": kwargs.pop("width", 1920),
        "height": kwargs.pop("height", 1080),
        "spacing": kwargs.pop("spacing", 0),
        "slots": slots,
    }
    raw.update(kwargs)
    return raw


def _slot(slot_id, **kwargs):
    base = {
        "id": slot_id,
        "template": kwargs.pop("template", "alerts"),
        "x": 0,
        "y": 0,
        "w": 400,
        "h": 400,
    }
    base.update(kwargs)
    return base


def _link(link_id="stack", axis="vertical", role="flexible", **kwargs):
    data = {"id": link_id, "axis": axis, "role": role}
    data.update(kwargs)
    return data


def _rect(layout, slot_id):
    for slot in layout["slots"]:
        if slot["id"] == slot_id:
            return slot
    raise AssertionError(slot_id)


class RouteValidationTests(unittest.TestCase):
    def test_suggest_route_slugifies(self) -> None:
        self.assertEqual(suggest_route("My Layout"), "my_layout")
        self.assertEqual(suggest_route("api"), "container")

    def test_rejects_reserved_and_punctuation(self) -> None:
        with self.assertRaises(ContainerError):
            validate_route_syntax("api")
        with self.assertRaises(ContainerError):
            validate_route_syntax("_hidden")
        with self.assertRaises(ContainerError):
            validate_route_syntax("has space")

    def test_template_name_rejects_container_route(self) -> None:
        with patch(
            "modules.spore_studio.containers.container_route_exists",
            return_value=True,
        ):
            with self.assertRaises(SporeStudioError):
                _validate_name("my_layout")


class SolverTests(unittest.TestCase):
    def test_locked_unused_space_goes_to_flexible(self) -> None:
        raw = _container(
            [
                _slot(
                    "a",
                    h=500,
                    link=_link(role="locked"),
                ),
                _slot("b", y=500, h=580, link=_link(role="flexible")),
            ],
            height=1080,
        )
        layout = solve_layout(raw, {"a": {"h": 350, "w": 400}})
        locked = _rect(layout, "a")
        flexible = _rect(layout, "b")
        self.assertEqual(locked["h"], 350)
        self.assertEqual(locked["y"], 0)
        self.assertEqual(flexible["h"], 730)
        self.assertEqual(flexible["y"], 350)
        self.assertEqual(layout["warnings"], [])

    def test_used_equal_to_designated_keeps_full_box(self) -> None:
        raw = _container(
            [
                _slot("a", h=500, link=_link(role="locked")),
                _slot("b", y=500, h=580, link=_link(role="flexible")),
            ],
            height=1080,
        )
        layout = solve_layout(raw, {"a": {"h": 500}})
        self.assertEqual(_rect(layout, "a")["h"], 500)
        self.assertEqual(_rect(layout, "b")["h"], 580)

    def test_used_clamps_to_min_and_designated(self) -> None:
        raw = _container(
            [
                _slot(
                    "a",
                    h=500,
                    link=_link(role="locked", min_px=400),
                ),
                _slot("b", y=500, h=100, link=_link(role="flexible")),
            ],
            height=1080,
        )
        low = solve_layout(raw, {"a": {"h": 350}})
        self.assertEqual(_rect(low, "a")["h"], 400)
        high = solve_layout(raw, {"a": {"h": 900}})
        self.assertEqual(_rect(high, "a")["h"], 500)

    def test_min_percent_and_tighter_max(self) -> None:
        raw = _container(
            [
                _slot(
                    "a",
                    h=500,
                    link=_link(role="locked", min_pct=10, max_px=800, max_pct=20),
                ),
                _slot("b", h=100, link=_link(role="flexible")),
            ],
            height=1000,
        )
        # min 10% of 1000 = 100, max pct 20% = 200 is tighter than max px 800.
        layout = solve_layout(raw, {"a": {"h": 40}})
        self.assertEqual(_rect(layout, "a")["h"], 100)
        capped = solve_layout(raw, {"a": {"h": 400}})
        self.assertEqual(_rect(capped, "a")["h"], 200)

    def test_spacing_and_facing_padding_come_out_of_the_span(self) -> None:
        raw = _container(
            [
                _slot(
                    "a",
                    h=500,
                    padding={"t": 0, "r": 0, "b": 10, "l": 0},
                    link=_link(role="locked"),
                ),
                _slot(
                    "b",
                    y=500,
                    h=400,
                    padding={"t": 15, "r": 0, "b": 0, "l": 0},
                    link=_link(role="flexible"),
                ),
            ],
            height=1000,
            spacing=20,
        )
        layout = solve_layout(raw, {"a": {"h": 350}})
        locked = _rect(layout, "a")
        flexible = _rect(layout, "b")
        self.assertEqual(locked["h"], 360)
        self.assertEqual(locked["content"]["h"], 350)
        self.assertEqual(flexible["y"], 380)
        self.assertEqual(flexible["h"], 620)
        self.assertEqual(flexible["content"]["h"], 605)
        self.assertEqual(locked["h"] + 20 + flexible["h"], 1000)

    def test_unlinked_slot_never_moves(self) -> None:
        raw = _container(
            [
                _slot("a", h=500, link=_link(role="locked")),
                _slot("b", y=500, h=200, link=_link(role="flexible")),
                _slot("side", x=1400, y=20, w=500, h=900, template="chat"),
            ],
            height=1080,
        )
        layout = solve_layout(raw, {"a": {"h": 200}})
        side = _rect(layout, "side")
        self.assertEqual((side["x"], side["y"], side["w"], side["h"]), (1400, 20, 500, 900))
        self.assertEqual(_rect(layout, "a")["x"], 0)
        self.assertEqual(_rect(layout, "a")["w"], 400)

    def test_horizontal_link_keeps_y_and_height(self) -> None:
        raw = _container(
            [
                _slot(
                    "a",
                    w=300,
                    h=40,
                    y=5,
                    link=_link(axis="horizontal", role="locked"),
                ),
                _slot(
                    "b",
                    x=300,
                    y=5,
                    w=200,
                    h=40,
                    link=_link(axis="horizontal", role="flexible"),
                ),
            ],
            width=800,
            height=200,
        )
        layout = solve_layout(raw, {"a": {"w": 100}})
        locked = _rect(layout, "a")
        flexible = _rect(layout, "b")
        self.assertEqual((locked["x"], locked["w"], locked["y"], locked["h"]), (0, 100, 5, 40))
        self.assertEqual((flexible["x"], flexible["w"], flexible["y"], flexible["h"]), (100, 700, 5, 40))

    def test_flexible_slots_split_by_designated_weight(self) -> None:
        raw = _container(
            [
                _slot("lock", h=200, link=_link(role="locked")),
                _slot("small", y=10, h=100, link=_link(role="flexible")),
                _slot("large", y=20, h=300, link=_link(role="flexible")),
            ],
            height=1000,
        )
        layout = solve_layout(raw, {"lock": {"h": 200}})
        self.assertEqual(_rect(layout, "small")["h"], 200)
        self.assertEqual(_rect(layout, "large")["h"], 600)

    def test_minimums_that_do_not_fit_warn_and_keep_locked_size(self) -> None:
        raw = _container(
            [
                _slot("a", h=250, link=_link(role="locked", min_px=200)),
                _slot("b", h=100, link=_link(role="flexible", min_px=200)),
            ],
            height=300,
        )
        layout = solve_layout(raw, {"a": {"h": 100}})
        self.assertEqual(_rect(layout, "a")["h"], 200)
        self.assertEqual(_rect(layout, "b")["h"], 200)
        self.assertTrue(any(item["code"] == "min_overflow" for item in layout["warnings"]))

    def test_locked_iframe_stays_at_designated_content_size(self) -> None:
        raw = _container(
            [
                _slot("a", h=500, link=_link(role="locked")),
                _slot("b", h=100, link=_link(role="flexible")),
            ]
        )
        layout = solve_layout(raw, {"a": {"h": 350}})
        locked = _rect(layout, "a")
        self.assertEqual(locked["iframe"]["h"], 500)
        self.assertEqual(locked["content"]["h"], 350)


class ShellTests(unittest.TestCase):
    def test_shell_iframes_templates_and_does_not_inline_markup(self) -> None:
        raw = _container(
            [
                _slot(
                    "a",
                    template="alerts",
                    audio={"muted": True, "volume": 80},
                )
            ]
        )
        with patch(
            "modules.spore_studio.containers.template_html_exists",
            return_value=True,
        ):
            page = render_shell(raw)
        self.assertIn("/alerts?mycelian_embed=1&amp;mycelian_volume=0&amp;mycelian_muted=1", page)
        self.assertIn("container_layout.js", page)
        self.assertIn("container_shell.js", page)
        self.assertNotIn("<div id=\"alert-root\">", page)
        self.assertNotIn("SPORE_STUDIO:dom-begin", page)

    def test_embed_src_and_audio_script(self) -> None:
        self.assertEqual(
            embed_src("chat", {"muted": False, "volume": 40}),
            "/chat?mycelian_embed=1&mycelian_volume=40&mycelian_muted=0",
        )
        html = inject_embed_audio("<head><title>x</title></head>", volume=40, muted=False)
        self.assertIn("mycelian-embed-audio", html)
        self.assertIn("volume=0.4000", html)
        again = inject_embed_audio(html, volume=0, muted=True)
        self.assertEqual(again, html)

    def test_summary_counts_templates(self) -> None:
        model = normalize_container(
            _container([_slot("a", template="alerts"), _slot("b", template="chat")])
        )
        summary = container_summary(model)
        self.assertEqual(summary["type"] if "type" in summary else "container", "container")
        self.assertEqual(summary["template_count"], 2)
        self.assertEqual(summary["templates"], ["alerts", "chat"])
        record = container_source_record(model, "http://127.0.0.1:5000")
        self.assertEqual(record["type"], "container")
        self.assertEqual(record["template_count"], 2)
        self.assertEqual(record["title"], "Layout")
        self.assertEqual(record["url"], "http://127.0.0.1:5000/layout")
        self.assertEqual(record["templates"], ["alerts", "chat"])


class DiskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        templates = os.path.join(self.tmp.name, "templates")
        os.makedirs(templates)

        def _template_path(relative_path=""):
            if relative_path:
                return os.path.join(templates, relative_path)
            return templates

        patcher = patch(
            "modules.spore_studio.containers.get_template_path",
            side_effect=_template_path,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.templates = templates

    def test_round_trip_and_template_collision(self) -> None:
        created = create_container("My Layout", "my_layout", width=1280, height=720)
        self.assertEqual(created["route"], "my_layout")
        created["slots"] = [_slot("a", template="alerts")]
        saved = save_container(created, previous_route="my_layout")
        self.assertEqual(saved["slots"][0]["template"], "alerts")
        with open(os.path.join(self.templates, "chat.html"), "w", encoding="utf-8") as fh:
            fh.write("<html><body>chat body marker</body></html>")
        with self.assertRaises(ContainerError):
            create_container("Chat", "chat")
        renamed = dict(saved)
        renamed["route"] = "renamed_layout"
        save_container(renamed, previous_route="my_layout")
        self.assertFalse(os.path.isfile(os.path.join(self.templates, "_containers", "my_layout.json")))
        self.assertTrue(
            os.path.isfile(os.path.join(self.templates, "_containers", "renamed_layout.json"))
        )

    def test_rejects_nested_container(self) -> None:
        create_container("Inner", "inner")
        outer = create_container("Outer", "outer")
        outer["slots"] = [_slot("a", template="inner")]
        with self.assertRaises(ContainerError):
            save_container(outer, previous_route="outer")


class JsSolverParityTests(unittest.TestCase):
    def test_js_solver_matches_python(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        script = ROOT / "assets" / "default_assets" / "spore_studio" / "container_layout.js"
        cases = []
        specs = [
            (
                _container(
                    [
                        _slot("a", h=500, link=_link(role="locked")),
                        _slot("b", y=500, h=580, link=_link(role="flexible")),
                    ],
                    height=1080,
                ),
                {"a": {"h": 350, "w": 400}},
            ),
            (
                _container(
                    [
                        _slot(
                            "a",
                            h=500,
                            padding={"t": 0, "r": 0, "b": 10, "l": 0},
                            link=_link(role="locked"),
                        ),
                        _slot(
                            "b",
                            y=500,
                            h=400,
                            padding={"t": 15, "r": 0, "b": 0, "l": 0},
                            link=_link(role="flexible"),
                        ),
                    ],
                    height=1000,
                    spacing=20,
                ),
                {"a": {"h": 350}},
            ),
            (
                _container(
                    [
                        _slot(
                            "a",
                            w=300,
                            h=40,
                            y=5,
                            link=_link(axis="horizontal", role="locked"),
                        ),
                        _slot(
                            "b",
                            x=300,
                            y=5,
                            w=200,
                            h=40,
                            link=_link(axis="horizontal", role="flexible"),
                        ),
                    ],
                    width=800,
                    height=200,
                ),
                {"a": {"w": 100}},
            ),
        ]
        for raw, used in specs:
            cases.append({"container": normalize_container(raw), "used": used})
        runner = (
            "const L = require(process.argv[1]);"
            "const cases = JSON.parse(process.argv[2]);"
            "const out = cases.map((c) => L.solve(c.container, c.used));"
            "process.stdout.write(JSON.stringify(out));"
        )
        completed = subprocess.run(
            [node, "-e", runner, str(script), json.dumps(cases)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        js_layouts = json.loads(completed.stdout)
        for (raw, used), js_layout in zip(specs, js_layouts):
            py_layout = solve_layout(normalize_container(raw), used)
            py_rects = [(s["id"], s["x"], s["y"], s["w"], s["h"]) for s in py_layout["slots"]]
            js_rects = [(s["id"], s["x"], s["y"], s["w"], s["h"]) for s in js_layout["slots"]]
            self.assertEqual(js_rects, py_rects)
            self.assertEqual(
                [item["code"] for item in js_layout["warnings"]],
                [item["code"] for item in py_layout["warnings"]],
            )


if __name__ == "__main__":
    unittest.main()
