# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/streamdeck_template_dispatch.py (action data coercion, action lookup, emit planning)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import streamdeck_template_dispatch as sd  # noqa: E402


# ------------------------------------------------------------------ coerce_streamdeck_action_data


@pytest.mark.parametrize("raw, expected", [
    (None, {}),
    ({"a": 1}, {"a": 1}),
    ('{"a": 2}', {"a": 2}),
    ('  {"b": [1]}  ', {"b": [1]}),
    ("", {}),
    ("   ", {}),
    ("not json", {}),
    ("[1, 2]", {}),
    ('"text"', {}),
    (42, {}),
    ([("a", 1)], {}),
])
def test_coerce(raw, expected):
    assert sd.coerce_streamdeck_action_data(raw) == expected


def test_coerce_returns_a_copy():
    src = {"a": 1}
    out = sd.coerce_streamdeck_action_data(src)
    out["a"] = 9
    assert src == {"a": 1}


# ------------------------------------------------------------------ merged_streamdeck_options_payload


def test_merge_defaults_overridden_by_client():
    cfg = {"default_data": {"amount": 1, "label": "x"}}
    assert sd.merged_streamdeck_options_payload(cfg, '{"amount": 5}') == {"amount": 5, "label": "x"}
    assert cfg["default_data"] == {"amount": 1, "label": "x"}


@pytest.mark.parametrize("cfg", [{}, {"default_data": "junk"}, {"default_data": None}])
def test_merge_without_usable_defaults(cfg):
    assert sd.merged_streamdeck_options_payload(cfg, {"k": 1}) == {"k": 1}
    assert sd.merged_streamdeck_options_payload(cfg, None) == {}


# ------------------------------------------------------------------ resolve_streamdeck_options_action


@pytest.mark.parametrize("acts, name", [(None, "x"), ([], "x"), ({"x": {}}, ""), ({"x": {}}, None)])
def test_resolve_rejects_bad_input(acts, name):
    assert sd.resolve_streamdeck_options_action(acts, name) == (None, None)


def test_resolve_by_key():
    spec = {"name": "Reset"}
    assert sd.resolve_streamdeck_options_action({"reset": spec}, "reset") == (spec, "reset")


def test_resolve_by_key_with_non_dict_spec():
    assert sd.resolve_streamdeck_options_action({"reset": "bad"}, "reset") == (None, "reset")


def test_resolve_by_display_name_skips_non_dicts():
    spec = {"name": " Reset Counter "}
    acts = {"junk": "x", "rc": spec}
    assert sd.resolve_streamdeck_options_action(acts, "  Reset Counter") == (spec, "rc")


def test_resolve_by_event_exact_and_suffix():
    exact = {"event": "counter_bump"}
    suffix = {"event": "tpl_spin"}
    acts = {"junk": 1, "no_event": {}, "blank": {"event": "  "}, "a": exact, "b": suffix}
    assert sd.resolve_streamdeck_options_action(acts, "counter_bump") == (exact, "a")
    assert sd.resolve_streamdeck_options_action(acts, "spin") == (suffix, "b")


def test_resolve_not_found():
    assert sd.resolve_streamdeck_options_action({"a": {"event": "x"}}, "zzz") == (None, None)


# ------------------------------------------------------------------ plan_streamdeck_template_action_emit


def _plan(**kw):
    base = dict(template_name="tpl", action_name="go", event_name_req="", action_data_raw=None, template_config=None)
    base.update(kw)
    return sd.plan_streamdeck_template_action_emit(**base)


def test_plan_without_config_uses_request_event_or_action():
    assert _plan() == ("go", "go", {})
    assert _plan(event_name_req="custom", action_data_raw='{"v": 1}') == ("go", "custom", {"v": 1})


@pytest.mark.parametrize("cfg", [{}, {"streamdeck_options": "x"}, {"streamdeck_options": {"actions": []}}])
def test_plan_with_unusable_config(cfg):
    assert _plan(template_config=cfg, event_name_req="ev") == ("go", "ev", {})


def test_plan_with_configured_event_and_defaults():
    cfg = {"streamdeck_options": {"actions": {"go_key": {"name": "go", "event": "tpl_fire", "default_data": {"n": 1}}}}}
    assert _plan(template_config=cfg, event_name_req="stale", action_data_raw={"m": 2}) == (
        "go_key", "tpl_fire", {"n": 1, "m": 2})


def test_plan_falls_back_to_template_prefixed_event():
    cfg = {"streamdeck_options": {"actions": {"go": {"default_data": {}}}}}
    assert _plan(template_config=cfg, event_name_req="stale") == ("go", "tpl_go", {})


def test_plan_unknown_action_and_client_event_flag():
    cfg = {"streamdeck_options": {"actions": {"other": {"event": "x"}}}}
    assert _plan(template_config=cfg, event_name_req="client_ev", use_client_event_name=True) == ("go", "client_ev", {})
    assert _plan(template_config=cfg, event_name_req="client_ev") == ("go", "client_ev", {})
    assert _plan(template_config=cfg, use_client_event_name=True) == ("go", "go", {})
