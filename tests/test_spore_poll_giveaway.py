# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Restyled Poll widget, Confetti block, text highlights, giveaway events and the Spore giveaway template."""

from __future__ import annotations

import json
import re
from pathlib import Path

from modules.spore_studio import data_feed_registry as dfr
from modules.spore_studio import event_registry, preview_mocks
from modules.spore_studio.template_codegen import compile_model

ROOT = Path(__file__).resolve().parents[1]


def _model(*elements, name="lab"):
    return {"template_name": name, "alert_system": "instant", "design": {"width": 480, "height": 640}, "elements": list(elements)}


def _el(eid, etype, props=None, **extra):
    return {"id": eid, "type": etype, "category": "Lab", "position": {"x": 0, "y": 0}, "size": {"w": 300, "h": 300},
            "props": props or {}, "bindings": [], **extra}


def _tag(html, eid):
    return re.search(rf'<div id="{eid}"[^>]*>', html).group(0)


def test_poll_compiles_new_look_settings_without_style_leaks():
    html, cfg = compile_model(_model(_el("poll1", "poll", {
        "style": "cards", "title": "Pick the boss", "bar_color_2": "#ec4899", "show_rank": True,
        "winner_effect": "pulse", "bar_height": 14, "panel_shadow": False, "border_radius": 18,
    })))
    tag = _tag(html, "poll1")
    for attr in ('data-spore-poll-style="{{ poll1_style|default("cards") }}"', "data-spore-poll-title=", "data-spore-poll-bar2=",
                 "data-spore-poll-winner-fx=", "data-spore-poll-shadow="):
        assert attr in tag
    style = re.search(r'style="([^"]*)"', tag).group(1)
    for leaked in ("style:", "title", "bar-color-2", "winner-effect", "bar-height", "panel-shadow", "show-rank"):
        assert leaked not in style
    # Exposed style props are config-driven #id rules in the head, not inline.
    assert "border-radius" not in style
    assert '#poll1 { ' in html and 'border-radius: {{ (poll1_border_radius|default(18))' in html
    assert "spore-poll-head" in html and "spore-poll-foot" in html
    fields = {e["id"]: e for e in cfg["elements"] if isinstance(e, dict) and e.get("id")}
    assert fields["poll1_style"]["type"] == "select" and fields["poll1_style"]["options"] == ["bars", "inside", "cards"]
    assert fields["poll1_winner_effect"]["options"] == ["glow", "pulse", "none"]


def test_confetti_block_compiles_and_has_a_burst_target():
    html, cfg = compile_model(_model(_el("boom", "confetti", {"particle_count": 90, "origin": "top", "colors": "#fff\n#000"})))
    tag = _tag(html, "boom")
    assert 'data-spore-type="confetti"' in tag and "data-spore-confetti-origin" in tag
    assert '<canvas class="spore-confetti-canvas">' in html
    assert ".spore-confetti" in html
    assert "particle-count" not in re.search(r'style="([^"]*)"', tag).group(1)
    assert "confetti" in dfr.BLOCK_TYPES
    assert dfr.target_ids_for("confetti")[0] == "confetti_burst"
    feed = dfr.default_feed("confetti")
    assert feed["source"]["events"] == ["giveaway_winner"] and feed["target"]["id"] == "confetti_burst"
    assert any(e.get("id") == "boom_particle_count" for e in cfg["elements"] if isinstance(e, dict))


def test_list_replace_target_and_formatters():
    assert "list_replace" in dfr.target_ids_for("list")
    replace = next(t for t in dfr.targets_for("list") if t["id"] == "list_replace")
    assert replace["options"][0]["key"] == "items_field"
    ids = {f["id"] for f in dfr.FORMATTERS}
    assert {"plural", "join", "count"} <= ids


def test_text_highlight_colours_become_css_variables():
    html, _cfg = compile_model(_model(_el("kw", "text", {"text": "Type [[!enter]]", "accent_color": "#fff", "accent_background": "#7c3aed"})))
    style = re.search(r'style="([^"]*)"', _tag(html, "kw")).group(1)
    assert "--spore-accent: #fff" in style and "--spore-accent-bg: #7c3aed" in style
    assert "accent-color" not in style
    assert ".spore-accent" in html


def test_hidden_rule_beats_inline_display():
    html, _cfg = compile_model(_model(_el("t", "text", {"text": "x", "vertical_align": "center"}, start_hidden=True)))
    assert '.spore-element[data-spore-hidden="true"] { display: none !important; }' in html


def test_giveaway_events_and_preview_mocks():
    events = {e["event"]: e for e in event_registry._EVENTS}
    for name in ("giveaway_start", "giveaway_entry", "giveaway_stop", "giveaway_winner", "giveaway_clear", "giveaway_sync"):
        assert name in events
    assert "pool_entries" in {p["key"] for p in events["giveaway_entry"]["payload"]}
    pools = {"usernames": ["A", "B", "C"]}
    for name in ("giveaway_sync", "poll_state", "poll_ended"):
        socket_event, build = preview_mocks._BUILDERS[name]
        assert socket_event == name and isinstance(build(pools), dict)
    ended = preview_mocks._BUILDERS["poll_ended"][1](pools)
    assert ended["status"] == "ended" and "template" not in ended


def test_shipped_giveaway_template_is_spore_and_wired():
    sidecar = json.loads((ROOT / "templates/_spore/giveaway.spore.json").read_text())
    by_id = {e["id"]: e for e in sidecar["elements"]}
    for eid in ("panel", "keyword", "count", "closed", "entrants", "winner_panel", "winner_names", "confetti"):
        assert eid in by_id
    for el in sidecar["elements"]:
        valid = set(dfr.target_ids_for(el["type"]))
        for feed in el.get("data_feed") or []:
            assert feed["target"]["id"] in valid, (el["id"], feed["target"]["id"])
    listened = {ev for el in sidecar["elements"] for f in el.get("data_feed") or [] for ev in f["source"]["events"]}
    assert {"giveaway_start", "giveaway_entry", "giveaway_stop", "giveaway_winner", "giveaway_clear", "giveaway_sync"} <= listened
    html = (ROOT / "templates/giveaway.html").read_text()
    assert "sporeRequestFeedState" in html and "SPORE_STUDIO:dom-begin" in html
    config = json.loads((ROOT / "templates/template_configs/giveaway.json").read_text())
    assert config.get("spore_studio") is True


def test_shipped_poll_uses_the_new_bars_look():
    sidecar = json.loads((ROOT / "templates/_spore/poll.spore.json").read_text())
    props = sidecar["elements"][0]["props"]
    assert props["style"] == "bars" and props["title"]
    html = (ROOT / "templates/poll.html").read_text()
    assert 'data-spore-poll-style="{{ poll1_style|default("bars") }}"' in html
    assert "sporePollIsMine" in html


def test_installer_payload_ships_the_spore_sidecars():
    import importlib.util

    # Loaded by path: the repo's packaging/ folder shares its name with the pip package.
    spec = importlib.util.spec_from_file_location("mycelian_payload", ROOT / "packaging" / "payload.py")
    payload = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(payload)
    paths = {str(rel) for rel, _action in payload.iter_payload_files(ROOT)}
    assert "templates/_spore/giveaway.spore.json" in paths and "templates/_spore/poll.spore.json" in paths
    assert "templates/poll.html" in paths
