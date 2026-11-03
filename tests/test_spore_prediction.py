# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Prediction widget (Spore block type), its events and preview mocks, Twitch goal events, and the
shipped Spore "predictions" template."""

from __future__ import annotations

import json
import re
from pathlib import Path

from modules.spore_studio import behavior_blocks, data_feed_registry as dfr
from modules.spore_studio import event_registry, preview_mocks, template_outputs
from modules.spore_studio.template_codegen import compile_model

ROOT = Path(__file__).resolve().parents[1]
GOAL_KEYS = {"id", "type", "description", "current_amount", "target_amount", "percent", "is_achieved",
             "status", "started_at", "ended_at"}


def _model(*elements, name="lab"):
    return {"template_name": name, "alert_system": "instant", "design": {"width": 520, "height": 360}, "elements": list(elements)}


def _el(eid, props=None, **extra):
    return {"id": eid, "type": "prediction", "category": "Lab", "position": {"x": 0, "y": 0}, "size": {"w": 500, "h": 340},
            "props": props or {}, "bindings": [], **extra}


def test_prediction_is_a_registered_widget_block():
    assert "prediction" in dfr.BLOCK_TYPES
    assert dfr.DEFAULT_TARGET["prediction"] == "repaint" and "repaint" in dfr.target_ids_for("prediction")
    assert dfr.DEFAULT_SOURCE_EVENTS["prediction"] == ["prediction_state", "prediction_ended"]
    assert dfr.default_feed("prediction")["source"]["events"] == ["prediction_state", "prediction_ended"]
    (out,) = dfr.BLOCK_OUTPUTS["prediction"]
    assert out["event"] == "prediction_result" and {p["key"] for p in out["payload"]} >= {"winner", "status", "total_points"}
    assert "prediction_result" in template_outputs.GENERATED_WIDGET_EVENTS
    assert "prediction" in behavior_blocks._WIDGET_TYPES
    assert ".spore-prediction {" in behavior_blocks.WIDGET_CSS
    (migrated,) = dfr.migrate_legacy_feeds({"id": "p", "type": "prediction"})
    assert migrated["target"]["id"] == "repaint" and migrated["source"]["events"] == ["prediction_state", "prediction_ended"]


def test_prediction_compiles_settings_into_data_attributes_and_source_settings():
    html, cfg = compile_model(_model(_el("pred1", {
        "title": "Boss?", "show_top": True, "show_ratio": False, "color_1": "#00f", "extra_colors": "#111,#222",
        "won_text": "{winner} takes it", "autohide_seconds": 30, "winner_effect": "pulse",
    }, source_settings_expose={"title": True, "show_ratio": True, "show_top": False})))
    tag = re.search(r'<div id="pred1"[^>]*>', html).group(0)
    assert 'data-spore-type="prediction"' in tag and "spore-widget spore-prediction" in tag
    for attr in ('data-spore-pred-title="{{ pred1_title|default("Boss?") }}"',
                 'data-spore-pred-top="{{ pred1_show_top|default("true") }}"',
                 'data-spore-pred-ratio="{{ pred1_show_ratio|default("false") }}"',
                 'data-spore-pred-won-text="{{ pred1_won_text|default("{winner} takes it") }}"',
                 'data-spore-pred-autohide="{{ pred1_autohide_seconds|default(30) }}"',
                 'data-spore-pred-extra-colors="{{ pred1_extra_colors|default("#111,#222") }}"'):
        assert attr in tag, attr
    for cls in ("spore-pred-head", "spore-pred-timer", "spore-pred-outcomes", "spore-pred-foot"):
        assert f'class="{cls}"' in html
    ids = {row.get("id"): row for row in cfg["elements"]}
    assert ids["pred1_title"]["value"] == "Boss?" and ids["pred1_show_ratio"]["value"] is False
    assert ids["pred1_winner_effect"]["options"] == ["glow", "pulse", "none"]
    assert "pred1_show_top" not in ids  # exposure turned off: inlined only
    assert cfg["outputs"]["pred1_result"]["event"] == "prediction_result"
    assert ".spore-pred-bar-fill" in html


def test_prediction_and_goal_events_and_preview_mocks():
    events = {e["event"]: e for e in event_registry._EVENTS}
    for name in ("prediction_state", "prediction_ended"):
        keys = {p["key"] for p in events[name]["payload"]}
        assert {"template", "status", "outcomes", "total_points"} <= keys and events[name]["alert_system"] == "either"
    for name in ("twitch_goal_begin", "twitch_goal_progress", "twitch_goal_end", "twitch_goal_sync"):
        assert {p["key"] for p in events[name]["payload"]} == GOAL_KEYS and events[name]["alert_system"] == "either"
    pools = {"usernames": ["A", "B"]}
    active = preview_mocks._BUILDERS["prediction_state"][1](pools)
    ended = preview_mocks._BUILDERS["prediction_ended"][1](pools)
    assert active["status"] == "active" and active["remaining_seconds"] and "template" not in active
    assert ended["status"] == "resolved" and sum(o["winner"] for o in ended["outcomes"]) == 1
    assert ended["winning_outcome_id"] == next(o["id"] for o in ended["outcomes"] if o["winner"])
    assert round(sum(o["percent"] for o in active["outcomes"])) == 100
    for name in ("twitch_goal_progress", "twitch_goal_sync"):
        socket_event, build = preview_mocks._BUILDERS[name]
        goal = build(pools)
        assert socket_event == name and set(goal) == GOAL_KEYS and 0 < goal["current_amount"] < goal["target_amount"]


def test_runtime_requests_goal_state_and_knows_the_prediction_widget():
    runtime = (ROOT / "templates/_boilerplates/spore_data_runtime.js").read_text(encoding="utf-8")
    assert "twitch_goal_: 'twitch_goal_request_state'" in runtime
    assert "function sporeBindPrediction" in runtime and "socket.emit('prediction_request_state'" in runtime
    assert "[data-spore-type=\"prediction\"]" in runtime


def test_shipped_templates_carry_the_current_runtime():
    from modules.spore_studio.spore_data_codegen import inject_data_runtime_block

    for name in ("bitcounter", "giveaway", "poll", "predictions", "steam_achievements", "subcounter"):
        html = (ROOT / f"templates/{name}.html").read_text(encoding="utf-8")
        assert inject_data_runtime_block(html) == html, name


def test_shipped_predictions_template_is_spore_and_wired():
    sidecar = json.loads((ROOT / "templates/_spore/predictions.spore.json").read_text(encoding="utf-8"))
    assert sidecar["template_name"] == "predictions" and sidecar["alert_system"] == "instant"
    (el,) = sidecar["elements"]
    assert el["type"] == "prediction" and el["start_hidden"] is True
    (feed,) = el["data_feed"]
    assert feed["target"]["id"] in dfr.target_ids_for("prediction")
    assert feed["source"]["events"] == ["prediction_state", "prediction_ended"]
    html = (ROOT / "templates/predictions.html").read_text(encoding="utf-8")
    assert 'data-spore-type="prediction"' in html and "SPORE_STUDIO:dom-begin" in html
    config = json.loads((ROOT / "templates/template_configs/predictions.json").read_text(encoding="utf-8"))
    assert config.get("spore_studio") is True
    assert {"prediction1_title", "prediction1_color_1", "prediction1_autohide_seconds"} <= {e.get("id") for e in config["elements"]}
    assert {m["event"] for m in config["preview_mocks"]} >= {"prediction_state", "prediction_ended"}


def test_installer_payload_ships_the_predictions_template():
    import importlib.util

    spec = importlib.util.spec_from_file_location("mycelian_payload", ROOT / "packaging" / "payload.py")
    payload = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(payload)
    assert "predictions" in payload.TEMPLATE_STEMS and "predictions" in payload.SPORE_SIDECARS
    paths = {str(rel) for rel, _action in payload.iter_payload_files(ROOT)}
    assert {"templates/predictions.html", "templates/_spore/predictions.spore.json"} <= paths


def test_editor_and_css_know_the_prediction_widget():
    editor = (ROOT / "assets/default_assets/spore_studio/editor.js").read_text(encoding="utf-8")
    for needle in ('prediction: "Prediction"', '"poll", "prediction", "progress_bar"', "function paintPredictionPreview",
                   "/api/spore-studio/prediction-state/"):
        assert needle in editor, needle
    css = (ROOT / "assets/default_assets/spore_studio/editor.css").read_text(encoding="utf-8")
    start = behavior_blocks.WIDGET_CSS.index(".spore-prediction {")
    assert behavior_blocks.WIDGET_CSS[start:] in css  # canvas copy stays in sync
    assert "prediction: \"prediction\"" in (ROOT / "assets/default_assets/spore_studio/spore_ui.js").read_text(encoding="utf-8")
