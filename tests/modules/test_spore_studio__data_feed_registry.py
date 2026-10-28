# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/data_feed_registry.py (data feeds, targets, outputs, migrations)."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import path_utils
from modules.spore_studio import data_feed_registry as dfr
from modules.spore_studio import template_outputs


# ---------------------------------------------------------------- tables / helpers

def test_opt_and_target_builders():
    assert dfr._opt("k", "K") == {"key": "k", "label": "K", "type": "text", "default": "", "tooltip": ""}
    row = dfr._opt("k", "K", "select", default=1, options=("a", "b"), tooltip="t", min_=0, max_=9)
    assert row["options"] == ["a", "b"] and row["min"] == 0 and row["max"] == 9
    assert dfr._target("t", "T") == {"id": "t", "label": "T", "description": "", "accepts": "item",
                                     "options": []}


def test_targets_for_merges_generic_without_duplicates():
    ids = dfr.target_ids_for("list")
    assert ids[0] == "list_add"
    assert {"show", "hide", "toggle", "show_for", "flash_class", "set_style"} <= set(ids)
    assert len(ids) == len(set(ids))
    assert dfr.target_ids_for("unknown") == [t["id"] for t in dfr.GENERIC_TARGETS]
    t = dfr.targets_for("list")
    t[0]["label"] = "mutated"
    assert dfr.targets_for("list")[0]["label"] != "mutated"


def test_default_targets_are_valid_for_each_block():
    for block, target in dfr.DEFAULT_TARGET.items():
        assert target in dfr.target_ids_for(block), block


def test_out_and_p_builders():
    out = dfr._out("o", "ev", "L", "D", [dfr._p("a", "A")], match_key="", default_enabled=False,
                   only_when="counter")
    assert out == {"id": "o", "event": "ev", "label": "L", "description": "D",
                   "payload": [{"key": "a", "label": "A", "type": "string"}],
                   "match_key": "", "default_enabled": False, "only_when": "counter"}


# ---------------------------------------------------------------- outputs

def test_output_applies_only_when_counter():
    spec = {"only_when": "counter"}
    assert dfr._output_applies(spec, {"type": "counter"}) is True
    assert dfr._output_applies(spec, {"type": "text", "text_mode": "counter"}) is True
    assert dfr._output_applies(spec, {"type": "text"}) is False
    assert dfr._output_applies({}, {"type": "text"}) is True


def test_outputs_for_block_copies():
    rows = dfr.outputs_for_block("wheel")
    assert rows[0]["event"] == "wheel_result"
    rows[0]["event"] = "x"
    assert dfr.BLOCK_OUTPUTS["wheel"][0]["event"] == "wheel_result"
    assert dfr.outputs_for_block("text") == []


def test_output_settings_and_enabled():
    el = {"output_settings": {"result": {"enabled": False, "label": "Mine"}, "bad": "x"}}
    assert dfr.output_settings(el, "result") == {"enabled": False, "label": "Mine"}
    assert dfr.output_settings(el, "bad") == {}
    assert dfr.output_settings({"output_settings": "x"}, "result") == {}
    assert dfr.output_enabled(el, {"id": "result"}) is False
    assert dfr.output_enabled({}, {"id": "row_added", "default_enabled": False}) is False
    assert dfr.output_enabled({}, {"id": "x"}) is True


def test_build_widget_outputs():
    elements = [
        "junk",
        {"id": "", "type": "wheel"},
        {"id": "w1", "type": "Wheel", "output_settings": {"result": {"label": " Spin! "}}},
        {"id": "l1", "type": "list", "output_settings": {"row_added": {"enabled": True}}},
        {"id": "c1", "type": "counter"},
        {"id": "t1", "type": "text"},
    ]
    out = dfr.build_widget_outputs(elements, "tpl")
    assert set(out) == {"w1_result", "l1_row_added", "c1_changed", "c1_goal"}
    w = out["w1_result"]
    assert w["label"] == "Spin!" and w["match"] == {"elementId": "w1"} and w["generated"] is True
    assert w["element_type"] == "wheel"
    assert out["c1_changed"]["label"] == "c1 counter changed"
    assert dfr.build_widget_outputs(None, "t") == {}


def test_build_widget_outputs_without_match_key(monkeypatch):
    monkeypatch.setitem(dfr.BLOCK_OUTPUTS, "qr", [dfr._out("x", "qr_ev", "x", "d", [], match_key="")])
    out = dfr.build_widget_outputs([{"id": "q", "type": "qr"}], "t")
    assert "match" not in out["q_x"]


def test_runtime_output_table(monkeypatch):
    monkeypatch.setitem(dfr.BLOCK_OUTPUTS, "text",
                        [dfr._out("c", "counter_changed", "c", "d", [], only_when="counter")])
    rows = dfr.runtime_output_table([
        "junk", {"id": "", "type": "wheel"},
        {"id": "w", "type": "wheel", "output_settings": {"result": {"enabled": False}}},
        {"id": "t", "type": "text"},
        {"id": "tc", "type": "text", "text_mode": "counter"},
    ])
    assert rows == [{"element_id": "w", "event": "wheel_result", "enabled": False},
                    {"element_id": "tc", "event": "counter_changed", "enabled": True}]
    assert dfr.runtime_output_table(None) == []


# ---------------------------------------------------------------- defaults / coercion

def test_default_feed_shapes():
    f = dfr.default_feed()
    assert f["id"] == "feed1" and f["source"]["events"] == ["instant_alert", "next_alert"]
    assert f["target"]["id"] == "show"
    p = dfr.default_feed("poll", "x")
    assert p["id"] == "x" and p["source"]["events"] == ["poll_state", "poll_ended"]
    assert p["target"]["id"] == "repaint"
    p["source"]["events"].append("mutated")
    assert dfr.DEFAULT_SOURCE_EVENTS["poll"] == ["poll_state", "poll_ended"]


@pytest.mark.parametrize("value,kw,expected", [
    ("3.5", {}, 3.5), ("x", {"default": 7}, 7), (None, {}, 0), (float("nan"), {"default": 2}, 2),
    (-5, {"minimum": 0}, 0), (5, {"minimum": 0}, 5)])
def test_num(value, kw, expected):
    assert dfr._num(value, **kw) == expected


def test_int_and_str():
    assert dfr._int("9.9") == 9
    assert dfr._int(-3) == 0
    assert dfr._int("bad", 4) == 4
    assert dfr._str(None) == "" and dfr._str(5) == "5"


def test_normalize_filter_rules():
    assert dfr.normalize_filter_rules("x") == []
    rules = dfr.normalize_filter_rules([
        {"field": " user ", "op": "", "value": 5},
        {"field": "", "op": "eq"},
        {"field": "a", "op": "bogus"},
        "junk",
        {"field": "b", "op": "empty", "value": None},
    ])
    assert rules == [{"field": "user", "op": "eq", "value": "5"},
                     {"field": "b", "op": "empty", "value": ""}]


def test_normalize_filters():
    assert dfr.normalize_filters(None) == dfr.default_feed()["filters"]
    out = dfr.normalize_filters({"alert_type": " SUB ", "tier": " 2000 ", "match": "ANY",
                                 "users_allow": " a ", "users_block": " b ", "cooldown_ms": "10",
                                 "max_per_minute": -1, "dedupe_ms": 5.5, "skip_first": "x",
                                 "rules": [{"field": "f", "op": "gt", "value": 1}]})
    assert out == {"alert_type": "sub", "tier": "2000", "rules": [{"field": "f", "op": "gt", "value": "1"}],
                   "match": "any", "users_allow": "a", "users_block": "b", "cooldown_ms": 10,
                   "max_per_minute": 0, "dedupe_ms": 5, "skip_first": 0}
    assert dfr.normalize_filters({"match": "some", "alert_type": "  "})["match"] == "all"
    assert dfr.normalize_filters({"alert_type": "  "})["alert_type"] == "any"
    assert dfr.normalize_filters({"tier": "  "})["tier"] == "any"


# ---------------------------------------------------------------- normalize_feed

def test_normalize_feed_defaults_from_garbage():
    f = dfr.normalize_feed("garbage", "list", 2)
    assert f["id"] == "feed3" and f["enabled"] is True
    assert f["source"]["kind"] == "event" and f["source"]["events"] == ["instant_alert", "next_alert"]
    assert f["target"]["id"] == "list_add"
    assert f["mapping"]["decimals"] == ""


def test_normalize_feed_full():
    raw = {
        "id": " my ", "enabled": 0, "label": " L ",
        "source": {"kind": "TEMPLATE_OUTPUT", "events": "single",
                   "template_output": {"template": " t ", "event": " e ", "match": {1: "x"}},
                   "value": {"source": "alert.amount"}, "refresh_ms": "250",
                   "refresh_on": [" a ", "", None, "b"]},
        "filters": {"alert_type": "raid"},
        "mapping": {"text_field": " t ", "value_field": " v ", "template": " {x} ", "split_field": " s ",
                    "prefix": "$", "suffix": "!", "decimals": "2", "fallback": "-"},
        "timing": {"delay_ms": "100"},
        "target": {"id": "wheel_spin", "options": {"a": [1]}},
        "unknown": True,
    }
    f = dfr.normalize_feed(raw, "wheel")
    assert f["id"] == "my" and f["enabled"] is False and f["label"] == "L"
    src = f["source"]
    assert src["kind"] == "template_output" and src["events"] == ["single"]
    assert src["template_output"] == {"template": "t", "event": "e", "match": {"1": "x"}}
    assert src["value"] == {"source": "alert.amount", "kind": "data_source"}
    assert src["refresh_ms"] == 250 and src["refresh_on"] == ["a", "b"]
    assert f["filters"]["alert_type"] == "raid"
    assert f["mapping"] == {"text_field": "t", "value_field": "v", "template": " {x} ", "split_field": "s",
                            "prefix": "$", "suffix": "!", "decimals": 2, "fallback": "-"}
    assert f["timing"] == {"delay_ms": 100}
    assert f["target"] == {"id": "wheel_spin", "options": {"a": [1]}}
    assert "unknown" not in f
    raw["target"]["options"]["a"].append(2)
    assert f["target"]["options"] == {"a": [1]}  # deep copied


def test_normalize_feed_events_and_targets():
    # non-event kinds without events get an empty list
    f = dfr.normalize_feed({"source": {"kind": "value"}}, "text")
    assert f["source"]["events"] == []
    # explicit events key that is not a list -> empty for event kind
    f = dfr.normalize_feed({"source": {"kind": "event", "events": 5}}, "text")
    assert f["source"]["events"] == []
    # unknown kind -> event; invalid target for block -> default
    f = dfr.normalize_feed({"source": {"kind": "weird"}, "target": {"id": "nope"}}, "text")
    assert f["source"]["kind"] == "event" and f["target"]["id"] == "set_text"
    # no block type: any target id accepted
    f = dfr.normalize_feed({"target": {"id": "anything", "options": "bad"}})
    assert f["target"] == {"id": "anything", "options": {}}
    # refresh_on non-list
    f = dfr.normalize_feed({"source": {"refresh_on": "x"}})
    assert f["source"]["refresh_on"] == []


# ---------------------------------------------------------------- legacy feed migration

def test_migrate_legacy_feeds_non_dict_and_plain():
    assert dfr.migrate_legacy_feeds("x") == []
    assert dfr.migrate_legacy_feeds({"type": "image"}) == []


def test_migrate_legacy_list_variants():
    assert dfr.migrate_legacy_feeds({"type": "list", "props": {"source": "manual"}}) == []
    (f,) = dfr.migrate_legacy_feeds({"type": "list", "props": {"source": "giveaway"}})
    assert f["source"]["events"] == ["giveaway_entry", "giveaway_winner"]
    assert f["mapping"]["split_field"] == "winners" and f["mapping"]["template"] == "{username}"
    assert f["target"]["id"] == "list_add"
    assert dfr.migrate_legacy_feeds({"type": "list", "props": {"source": "payload"}}) == []
    (f,) = dfr.migrate_legacy_feeds({"type": "list", "props": {"source": "payload", "source_event": " ev ",
                                                               "row_template": "{a}", "text_field": "t"}})
    assert f["source"]["events"] == ["ev"] and f["mapping"]["template"] == "{a}"
    assert f["mapping"]["text_field"] == "t"
    (f,) = dfr.migrate_legacy_feeds({"type": "list", "props": {"alert_type": " RAID "}})
    assert f["filters"]["alert_type"] == "raid" and f["source"]["events"] == ["instant_alert", "next_alert"]
    (f,) = dfr.migrate_legacy_feeds({"type": "list", "props": "bad"})
    assert f["filters"]["alert_type"] == "any"


def test_migrate_legacy_wheel_stat_card_poll():
    assert dfr.migrate_legacy_feeds({"type": "wheel", "props": {"source": "static"}}) == []
    (f,) = dfr.migrate_legacy_feeds({"type": "wheel", "props": {"source": "giveaway"}})
    assert f["target"] == {"id": "wheel_spin",
                           "options": {"segments_field": "pool_entries", "land_field": "winners"}}
    assert dfr.migrate_legacy_feeds({"type": "stat_card"}) == []
    (f,) = dfr.migrate_legacy_feeds({"type": "stat_card", "stat_source": {"kind": "session_stat", "key": "follows"}})
    assert f["source"]["kind"] == "value" and f["source"]["refresh_ms"] == 1000
    assert f["source"]["value"] == {"kind": "session_stat", "key": "follows", "source": ""}
    assert f["target"]["id"] == "set_value"
    for t in ("poll", "now_playing"):
        (f,) = dfr.migrate_legacy_feeds({"type": t})
        assert f["target"]["id"] == "repaint"


def test_migrate_legacy_data_display_text():
    assert dfr.migrate_legacy_feeds({"type": "text", "text_mode": "data_display"}) == []
    (f,) = dfr.migrate_legacy_feeds({"type": "marquee", "text_mode": "data_display", "data_display": {
        "source": {"kind": "data_source", "source": "x"}, "poll_interval_ms": 500,
        "refresh_on": ["a", "", None]}})
    assert f["source"]["value"] == {"kind": "data_source", "source": "x"}
    assert f["source"]["refresh_ms"] == 500 and f["source"]["refresh_on"] == ["a"]
    assert f["target"]["id"] == "set_text"
    (f,) = dfr.migrate_legacy_feeds({"type": "text", "text_mode": "data_display", "data_display": {"source": "bad"}})
    assert f["source"]["value"] == {"kind": "data_source", "source": ""}


# ---------------------------------------------------------------- counter rules

def test_rule_to_feed_event_with_filters():
    rule = {"event": " next_alert ", "operation": "DECREMENT",
            "filters": {"rules": [{"field": "x", "op": "eq", "value": 1}]},
            "filter": {"alert_type": "SUB", "tier": 2000, "username": "bob", "empty": ""},
            "delta": {"kind": "payload", "field": "amount", "alert_type_filter": "bit",
                      "tier_filter": "3000"}}
    f = dfr._rule_to_feed(rule, 0)
    assert f["id"] == "rule1" and f["source"]["events"] == ["next_alert"]
    assert f["filters"]["alert_type"] == "bit" and f["filters"]["tier"] == "3000"
    assert {"field": "username", "op": "eq", "value": "bob"} in f["filters"]["rules"]
    assert {"field": "x", "op": "eq", "value": "1"} in f["filters"]["rules"]
    assert f["target"]["id"] == "counter_decrement"
    assert f["target"]["options"]["delta"]["field"] == "amount"


def test_rule_to_feed_streamdeck_and_failures():
    f = dfr._rule_to_feed({"trigger": "streamdeck", "streamdeck_action": "add", "operation": "bogus"}, 1)
    assert f["source"]["events"] == ["streamdeck_template_action"]
    assert f["filters"]["rules"] == [{"field": "actionName", "op": "eq", "value": "add"}]
    assert f["target"]["id"] == "counter_increment"
    assert f["target"]["options"]["delta"] == {"kind": "fixed", "value": 1}
    assert dfr._rule_to_feed({"trigger": "streamdeck"}, 0) is None
    assert dfr._rule_to_feed({"event": "  "}, 0) is None
    f = dfr._rule_to_feed({"event": "e", "filter": {"alert_type": "  "}, "delta": {"alert_type_filter": "any"}}, 0)
    assert f["filters"]["alert_type"] == "any"


def test_migrate_counter_rules():
    assert dfr.migrate_counter_rules("x") is False
    assert dfr.migrate_counter_rules({"legacy": True, "elements": []}) is False
    model = {"elements": [
        "junk",
        {"id": "plain", "type": "text"},
        {"id": "norules", "type": "counter", "counter": {"rules": []}},
        {"id": "img", "type": "image", "counter": {"rules": [{"event": "e"}]}},
        {"id": "c", "type": "counter", "data_feed": [{"id": "rule1"}, "junk"],
         "counter": {"rules": [{"event": "a"}, "junk", {"event": ""}, {"event": "b", "operation": "reset"}]}},
        {"id": "tc", "type": "text", "text_mode": "counter", "counter": {"rules": [{"event": "x"}]}},
    ]}
    assert dfr.migrate_counter_rules(model) is True
    c = model["elements"][4]
    assert [f["id"] for f in c["data_feed"]] == ["rule1", "rule2", "rule3"]
    assert c["data_feed"][2]["target"]["id"] == "counter_reset"
    assert c["counter"]["rules"] == []
    assert model["elements"][3]["counter"]["rules"] == [{"event": "e"}]
    assert model["elements"][5]["data_feed"][0]["source"]["events"] == ["x"]
    assert dfr.migrate_counter_rules(model) is False


def test_migrate_counter_rules_id_collision():
    model = {"elements": [{"id": "c", "type": "counter", "data_feed": [{"id": "rule2"}],
                           "counter": {"rules": [{"event": "a"}]}}]}
    dfr.migrate_counter_rules(model)
    assert [f["id"] for f in model["elements"][0]["data_feed"]] == ["rule2", "rule2_2"]


def test_migrate_display_sources():
    model = {"elements": [
        "junk",
        {"type": "image"},
        {"type": "data_display", "data_display": {"source": {"source": " "}}},
        {"type": "data_display"},
        {"type": "data_display", "data_display": {"source": {"source": "x"}},
         "data_feed": [{"target": {"id": "set_text"}}]},
        {"type": "data_display", "data_display": {"source": {"kind": "data_source", "source": "alert.amount"},
                                                  "poll_interval_ms": 250}},
        {"type": "marquee", "text_mode": "data_display",
         "data_display": {"source": {"source": "y"}, "refresh_on": ["evt"]}},
    ]}
    assert dfr._migrate_display_sources(model) is True
    dd = model["elements"][5]["data_feed"][0]
    assert dd["id"] == "display1" and dd["source"]["refresh_ms"] == 250
    assert dd["source"]["refresh_on"] == ["instant_alert", "next_alert"]
    assert dd["target"]["id"] == "set_text"
    mq = model["elements"][6]["data_feed"][0]
    assert mq["source"]["refresh_on"] == ["evt"]
    assert len(model["elements"][4]["data_feed"]) == 1
    assert dfr._migrate_display_sources(model) is False


def test_migrate_counter_rules_reports_display_migration():
    model = {"elements": [{"type": "data_display", "data_display": {"source": {"source": "x"}}}]}
    assert dfr.migrate_counter_rules(model) is True


# ---------------------------------------------------------------- effective feeds / types / model

def test_effective_feeds():
    assert dfr.effective_feeds("x") == []
    el = {"type": "text", "data_feed": [{"target": {"id": "set_text"}}, "junk"]}
    feeds = dfr.effective_feeds(el)
    assert [f["id"] for f in feeds] == ["feed1", "feed2"]
    assert dfr.effective_feeds({"type": "poll"})[0]["target"]["id"] == "repaint"
    assert dfr.effective_feeds({"type": "poll", "data_feed": []}) == []


def test_migrate_legacy_element_types():
    assert dfr.migrate_legacy_element_types([]) is False
    assert dfr.migrate_legacy_element_types({"legacy": True}) is False
    model = {"elements": [
        "junk",
        {"type": "text", "text_mode": "counter", "props": "bad"},
        {"type": "marquee", "text_mode": "data_display", "props": {"speed": 3}},
        {"type": "text", "text_mode": "static"},
        {"type": "image", "text_mode": "counter"},
    ]}
    assert dfr.migrate_legacy_element_types(model) is True
    a, b = model["elements"][1], model["elements"][2]
    assert a == {"type": "counter", "props": {}}
    assert b["type"] == "data_display" and "text_mode" not in b
    assert b["props"] == {"speed": 3, "scroll": True, "direction": "left", "gap": 48}
    assert model["elements"][3]["type"] == "text"
    assert dfr.migrate_legacy_element_types(model) is False


def test_migrate_model_feeds():
    assert dfr.migrate_model_feeds(None) is False
    assert dfr.migrate_model_feeds({"legacy": True, "elements": [{}]}) is False
    model = {"elements": ["junk", {"type": "poll"}, {"type": "text", "data_feed": ["keep"]}]}
    assert dfr.migrate_model_feeds(model) is True
    assert model["elements"][1]["data_feed"][0]["target"]["id"] == "repaint"
    assert model["elements"][2]["data_feed"] == ["keep"]
    assert dfr.migrate_model_feeds(model) is False


# ---------------------------------------------------------------- resave scan

def _sidecar(folder, name, model):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.spore.json").write_text(
        model if isinstance(model, str) else json.dumps(model), encoding="utf-8")


def test_templates_needing_widget_resave(tmp_path, monkeypatch):
    monkeypatch.setattr(path_utils, "get_template_path", lambda rel="": str(tmp_path))
    spore = tmp_path / "_spore"
    _sidecar(spore, "a_text_counter", {"template_name": "A", "elements": [
        "junk", {"type": "text", "text_mode": "Counter"}, {"type": "text", "text_mode": "data_display"}]})
    _sidecar(spore, "b_counter_rules", {"elements": [{"type": "counter", "counter": {"rules": [{}]}}]})
    _sidecar(spore, "c_clean", {"elements": [{"type": "counter", "counter": "bad"}]})
    _sidecar(spore, "d_bad", "{broken")
    _sidecar(spore, "e_list", "[1]")
    _sidecar(spore, "f_display", {"elements": [{"type": "marquee", "text_mode": "data_display"}]})
    assert dfr.templates_needing_widget_resave() == [
        {"template": "A", "mode": "counter"},
        {"template": "b_counter_rules", "mode": "counter"},
        {"template": "f_display", "mode": "data_display"},
    ]


def test_templates_needing_widget_resave_glob_error(tmp_path, monkeypatch):
    import glob

    monkeypatch.setattr(path_utils, "get_template_path", lambda rel="": str(tmp_path))
    monkeypatch.setattr(glob, "glob", lambda p: (_ for _ in ()).throw(OSError("x")))
    assert dfr.templates_needing_widget_resave() == []


# ---------------------------------------------------------------- catalog

def test_get_data_catalog(monkeypatch):
    monkeypatch.setattr(template_outputs, "list_template_outputs", lambda refresh=False: [{"event": "x"}])
    monkeypatch.setattr(template_outputs, "merge_into_events", lambda events: events)
    cat = dfr.get_data_catalog()
    json.dumps(cat)
    assert cat["template_outputs"] == [{"event": "x"}]
    assert set(cat["targets"]) == set(dfr.BLOCK_TYPES)
    assert set(cat["default_feed"]) == set(dfr.BLOCK_TYPES)
    assert set(cat["block_outputs"]) == set(dfr.BLOCK_OUTPUTS)
    assert cat["operators"][0]["id"] == "eq" and cat["formatters"][0]["id"] == "number"
    assert cat["alert_field_types"]["tier"] == ["sub", "resub", "giftsub"]
    assert cat["events"] and cat["actions"] and cat["sources"]
