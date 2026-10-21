#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Unified Data feed: catalog, migration, codegen, outputs, and save merge."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from modules.spore_studio.data_feed_registry import (
    build_widget_outputs,
    default_feed,
    effective_feeds,
    get_data_catalog,
    migrate_legacy_feeds,
    migrate_model_feeds,
    normalize_feed,
    normalize_filters,
    output_enabled,
    runtime_output_table,
    targets_for,
)
from modules.spore_studio.save_pipeline import _carry_handwritten_outputs
from modules.spore_studio.template_codegen import compile_model
from modules.spore_studio.template_outputs import (
    is_relayable_event,
    merge_into_events,
    resolve_emitter,
    scan_html_emits,
)


def _el(eid: str, etype: str, **extra):
    row = {
        "id": eid,
        "type": etype,
        "position": {"x": 0, "y": 0},
        "size": {"w": 120, "h": 80},
        "props": extra.pop("props", {}) or {},
    }
    row.update(extra)
    return row


class FilterAndFeedNormalizationTests(unittest.TestCase):
    def test_normalize_filters_drops_unknown_ops_and_blank_fields(self):
        out = normalize_filters(
            {
                "alert_type": "RAID",
                "match": "any",
                "cooldown_ms": "250",
                "rules": [
                    {"field": "username", "op": "eq", "value": "casey"},
                    {"field": "", "op": "eq", "value": "x"},
                    {"field": "amount", "op": "nope", "value": "1"},
                    {"field": "amount", "op": "gte", "value": "100"},
                ],
            }
        )
        self.assertEqual(out["alert_type"], "raid")
        self.assertEqual(out["match"], "any")
        self.assertEqual(out["cooldown_ms"], 250)
        self.assertEqual(
            out["rules"],
            [
                {"field": "username", "op": "eq", "value": "casey"},
                {"field": "amount", "op": "gte", "value": "100"},
            ],
        )

    def test_normalize_feed_falls_back_to_default_target(self):
        feed = normalize_feed({"target": {"id": "not_a_real_target"}}, "list")
        self.assertEqual(feed["target"]["id"], "list_add")
        self.assertEqual(feed["source"]["kind"], "event")
        self.assertIn("instant_alert", feed["source"]["events"])


class LegacyMigrationTests(unittest.TestCase):
    def test_list_alerts_become_a_feed(self):
        feeds = migrate_legacy_feeds(
            _el(
                "feed",
                "list",
                props={
                    "source": "alerts",
                    "alert_type": "bit",
                    "text_field": "username",
                    "value_field": "amt_cheered",
                    "row_template": "{username} +{value}",
                },
            )
        )
        self.assertEqual(len(feeds), 1)
        self.assertEqual(feeds[0]["target"]["id"], "list_add")
        self.assertEqual(feeds[0]["filters"]["alert_type"], "bit")
        self.assertEqual(feeds[0]["mapping"]["template"], "{username} +{value}")
        self.assertEqual(feeds[0]["source"]["events"], ["instant_alert", "next_alert"])

    def test_list_manual_has_no_feed(self):
        self.assertEqual(
            migrate_legacy_feeds(_el("feed", "list", props={"source": "manual"})),
            [],
        )

    def test_wheel_giveaway_becomes_a_spin_feed(self):
        feeds = migrate_legacy_feeds(_el("spin", "wheel", props={"source": "giveaway"}))
        self.assertEqual(feeds[0]["source"]["events"], ["giveaway_winner"])
        self.assertEqual(feeds[0]["target"]["id"], "wheel_spin")
        self.assertEqual(feeds[0]["target"]["options"]["segments_field"], "pool_entries")

    def test_stat_card_becomes_a_value_feed(self):
        feeds = migrate_legacy_feeds(
            _el(
                "bits",
                "stat_card",
                stat_source={"kind": "data_source", "source": "stats", "source_id": "total_bits"},
            )
        )
        self.assertEqual(feeds[0]["source"]["kind"], "value")
        self.assertEqual(feeds[0]["source"]["value"]["source"], "stats")
        self.assertEqual(feeds[0]["target"]["id"], "set_value")

    def test_poll_and_now_playing_get_default_feeds(self):
        poll = migrate_legacy_feeds(_el("votes", "poll"))
        song = migrate_legacy_feeds(_el("np", "now_playing"))
        self.assertEqual(poll[0]["source"]["events"], ["poll_state", "poll_ended"])
        self.assertEqual(song[0]["source"]["events"], ["spotify_data_update"])

    def test_explicit_empty_data_feed_wins_over_legacy(self):
        el = _el("feed", "list", props={"source": "alerts"})
        el["data_feed"] = []
        self.assertEqual(effective_feeds(el), [])

    def test_migrate_model_feeds_writes_data_feed_once(self):
        model = {
            "template_name": "demo",
            "elements": [_el("feed", "list", props={"source": "alerts"})],
        }
        self.assertTrue(migrate_model_feeds(model))
        self.assertTrue(isinstance(model["elements"][0]["data_feed"], list))
        self.assertFalse(migrate_model_feeds(model))


class CatalogAndTargetsTests(unittest.TestCase):
    def test_catalog_includes_shared_pieces(self):
        cat = get_data_catalog()
        self.assertTrue(cat["operators"])
        self.assertTrue(cat["formatters"])
        self.assertIn("list", cat["targets"])
        self.assertIn("list_add", [t["id"] for t in cat["targets"]["list"]])
        self.assertIn("show", [t["id"] for t in cat["targets"]["shape"]])
        self.assertIn("timer_set", [t["id"] for t in cat["targets"]["timer"]])
        self.assertIn("wheel", cat["block_outputs"])
        self.assertTrue(any(o["event"] == "roulette_result" for o in cat["template_outputs"]))

    def test_every_block_has_generic_targets(self):
        ids = {t["id"] for t in targets_for("clock")}
        self.assertTrue({"show", "hide", "toggle", "set_style"} <= ids)


class TemplateOutputScanTests(unittest.TestCase):
    def test_scan_html_emits_skips_plumbing(self):
        html = """
        socket.emit('roulette_result', {result: 'A'});
        socket.emit("get_data", {path: 'x'});
        socket.emit('template_log', {});
        socket.emit('wheel_result', {});
        """
        self.assertEqual(scan_html_emits(html), ["roulette_result", "wheel_result"])

    def test_injected_runtime_emits_are_ignored(self):
        html = (
            "// SPORE_STUDIO:data-runtime-begin\n"
            "socket.emit('wheel_result', {});\n"
            "// SPORE_STUDIO:data-runtime-end\n"
            "socket.emit('roulette_result', {result: 'A'});\n"
        )
        self.assertEqual(scan_html_emits(html), ["roulette_result"])

    def test_poll_is_not_credited_with_wheel_result(self):
        from modules.spore_studio.template_outputs import list_template_outputs

        rows = list_template_outputs(refresh=True)
        poll_events = {row["event"] for row in rows if row["template"] == "poll"}
        self.assertNotIn("wheel_result", poll_events)

    def test_relayable_event_denies_control_plane(self):
        self.assertTrue(is_relayable_event("roulette_result"))
        self.assertFalse(is_relayable_event("get_data"))
        self.assertFalse(is_relayable_event("twitch-api-request"))
        self.assertFalse(is_relayable_event("alert_playing"))
        self.assertFalse(is_relayable_event("not a name"))

    def test_merge_into_events_adds_template_outputs(self):
        events = merge_into_events([])
        names = {ev["event"] for ev in events}
        self.assertIn("roulette_result", names)

    def test_resolve_emitter_prefers_payload_template(self):
        self.assertEqual(
            resolve_emitter("roulette_result", {"template": "roulette"}),
            "roulette",
        )


class GeneratedOutputsTests(unittest.TestCase):
    def test_codegen_writes_outputs_for_widgets(self):
        model = {
            "template_name": "lab",
            "alert_system": "instant",
            "design": {"width": 400, "height": 400},
            "elements": [
                _el("spin", "wheel"),
                _el("votes", "poll"),
                _el("clock", "timer", props={"mode": "count_down", "start_seconds": 10}),
                {
                    "id": "cnt",
                    "type": "counter",
                    "position": {"x": 0, "y": 0},
                    "size": {"w": 80, "h": 30},
                    "props": {},
                    "counter": {"counter_id": "cnt", "initial_value": 0, "format": "{value}"},
                },
                _el(
                    "bar",
                    "progress_bar",
                    progress_source={"counter_id": "cnt", "max": 100, "max_kind": "fixed"},
                ),
                _el("feed", "list", props={"source": "alerts"}),
            ],
        }
        _html, cfg = compile_model(model)
        outputs = cfg.get("outputs") or {}
        self.assertEqual(outputs["spin_result"]["event"], "wheel_result")
        self.assertTrue(outputs["spin_result"]["generated"])
        self.assertEqual(outputs["spin_result"]["match"], {"elementId": "spin"})
        self.assertEqual(outputs["votes_result"]["event"], "poll_result")
        self.assertEqual(outputs["clock_finished"]["event"], "timer_finished")
        self.assertEqual(outputs["cnt_changed"]["event"], "counter_changed")
        self.assertEqual(outputs["bar_goal"]["event"], "goal_reached")
        self.assertNotIn("feed_row_added", outputs)
        self.assertNotIn("feed_cleared", outputs)

    def test_disabled_output_is_omitted(self):
        wheel = _el("spin", "wheel", output_settings={"result": {"enabled": False}})
        self.assertEqual(build_widget_outputs([wheel], "lab"), {})
        table = runtime_output_table([wheel])
        self.assertEqual(table[0]["enabled"], False)

    def test_list_outputs_can_be_turned_on(self):
        lst = _el(
            "feed",
            "list",
            output_settings={
                "row_added": {"enabled": True, "label": "New row"},
            },
        )
        out = build_widget_outputs([lst], "lab")
        self.assertEqual(out["feed_row_added"]["label"], "New row")
        self.assertEqual(out["feed_row_added"]["event"], "list_row_added")

    def test_compile_embeds_feeds_and_output_table(self):
        model = {
            "template_name": "lab",
            "design": {"width": 400, "height": 200},
            "elements": [
                _el(
                    "feed",
                    "list",
                    props={"source": "alerts", "row_template": "{username}"},
                )
            ],
        }
        html, _cfg = compile_model(model)
        self.assertIn("window.__sporeFeeds", html)
        self.assertIn("window.__sporeOutputs", html)
        self.assertIn("window.__sporeFeedsCompiled = true", html)
        self.assertIn("list_add", html)

    def test_default_feed_shape(self):
        feed = default_feed("poll")
        self.assertEqual(feed["source"]["events"], ["poll_state", "poll_ended"])
        self.assertTrue(output_enabled({"type": "wheel"}, {"id": "result", "default_enabled": True}))


class CounterRuleMigrationTests(unittest.TestCase):
    def test_rules_become_feeds_and_are_cleared(self):
        from modules.spore_studio.data_feed_registry import migrate_counter_rules
        from modules.spore_studio.template_codegen import compile_model

        model = {
            "template_name": "bits_mig",
            "alert_system": "instant",
            "design": {"width": 300, "height": 80},
            "elements": [
                {
                    "id": "bits",
                    "type": "text",
                    "text_mode": "counter",
                    "position": {"x": 0, "y": 0},
                    "size": {"w": 120, "h": 40},
                    "props": {"color": "#fff"},
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
                                    "fallback": 0,
                                },
                            },
                            {
                                "trigger": "streamdeck",
                                "streamdeck_action": "add_one",
                                "operation": "increment",
                                "delta": {"kind": "fixed", "value": 1},
                            },
                        ],
                    },
                }
            ],
        }
        html, _cfg = compile_model(model)
        counter = model["elements"][0]
        self.assertEqual(counter["type"], "counter")
        self.assertEqual(counter["counter"]["rules"], [])
        feeds = counter["data_feed"]
        self.assertEqual(len(feeds), 2)
        self.assertEqual(feeds[0]["source"]["events"], ["instant_alert"])
        self.assertEqual(feeds[0]["filters"]["alert_type"], "bit")
        self.assertEqual(feeds[0]["target"]["id"], "counter_increment")
        self.assertEqual(feeds[0]["target"]["options"]["delta"]["source"], "alert.amt_cheered")
        self.assertEqual(feeds[1]["source"]["events"], ["streamdeck_template_action"])
        self.assertIn("alert.amt_cheered", html)
        self.assertIn("counter_increment", html)
        self.assertFalse(migrate_counter_rules(model))


class ElementTypeMigrationTests(unittest.TestCase):
    def test_text_counter_and_marquee_display_become_widgets(self):
        from modules.spore_studio.data_feed_registry import migrate_legacy_element_types

        model = {
            "elements": [
                {
                    "id": "bits",
                    "type": "text",
                    "text_mode": "counter",
                    "counter": {"counter_id": "bits"},
                },
                {
                    "id": "title",
                    "type": "marquee",
                    "text_mode": "data_display",
                    "props": {"speed": 12, "text": "hi"},
                    "data_display": {"format": "{value}"},
                },
                {"id": "label", "type": "text", "text_mode": "static", "props": {"text": "Hello"}},
            ]
        }
        self.assertTrue(migrate_legacy_element_types(model))
        by_id = {el["id"]: el for el in model["elements"]}
        self.assertEqual(by_id["bits"]["type"], "counter")
        self.assertNotIn("text_mode", by_id["bits"])
        self.assertEqual(by_id["title"]["type"], "data_display")
        self.assertTrue(by_id["title"]["props"]["scroll"])
        self.assertEqual(by_id["title"]["props"]["speed"], 12)
        self.assertEqual(by_id["label"]["type"], "text")
        self.assertFalse(migrate_legacy_element_types(model))


class SaveMergeOutputsTests(unittest.TestCase):
    def test_handwritten_outputs_survive_and_generated_are_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.json"
            path.write_text(
                json.dumps(
                    {
                        "outputs": {
                            "result": {
                                "event": "roulette_result",
                                "label": "Hand written",
                                "generated": False,
                            },
                            "old_wheel_result": {
                                "event": "wheel_result",
                                "generated": True,
                                "element_id": "gone",
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            new_config = {
                "outputs": {
                    "spin_result": {
                        "event": "wheel_result",
                        "generated": True,
                        "element_id": "spin",
                    }
                }
            }
            _carry_handwritten_outputs(str(path), new_config)
            self.assertIn("result", new_config["outputs"])
            self.assertEqual(new_config["outputs"]["result"]["label"], "Hand written")
            self.assertIn("spin_result", new_config["outputs"])
            self.assertNotIn("old_wheel_result", new_config["outputs"])


class PreviewMockFeedTests(unittest.TestCase):
    def test_derive_preview_mocks_includes_feed_events(self):
        from modules.spore_studio.preview_mocks import derive_preview_mocks

        model = {
            "elements": [
                _el(
                    "feed",
                    "list",
                    data_feed=[
                        {
                            "enabled": True,
                            "source": {"kind": "event", "events": ["giveaway_winner"]},
                            "target": {"id": "list_add", "options": {}},
                        }
                    ],
                )
            ]
        }
        rows = derive_preview_mocks(model)
        events = {row.get("event") for row in rows}
        self.assertIn("giveaway_winner", events)


if __name__ == "__main__":
    unittest.main()
