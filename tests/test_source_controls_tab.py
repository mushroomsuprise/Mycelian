# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Source Controls app tab: live-update bookkeeping and rendering rules (no UI)."""

import threading
from unittest.mock import MagicMock, patch

from modules.uiwindows import sourcecontrols as sc


def setup_function(_fn):
    sc._take_dirty()


def test_mark_dirty_collects_names_from_many_threads():
    threads = [
        threading.Thread(target=sc.mark_source_controls_dirty, args=({"template_names": [f"t{i}"]},))
        for i in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    names, everything = sc._take_dirty()
    assert names == {f"t{i}" for i in range(20)}
    assert everything is False
    assert sc._take_dirty() == (set(), False)


def test_mark_dirty_without_names_means_all():
    sc.mark_source_controls_dirty({"template_names": [], "reason": "relay_event"})
    sc.mark_source_controls_dirty(None)
    assert sc._take_dirty() == (set(), True)


def test_structure_signature_ignores_live_values():
    a = [{"type": "slider", "action": "v", "value": 1}]
    b = [{"type": "slider", "action": "v", "value": 99}]
    assert sc._structure_signature("x", a, False) == sc._structure_signature("x", b, False)
    assert sc._structure_signature("x", a, False) != sc._structure_signature("x", a, True)
    assert sc._structure_signature("x", a, False) != sc._structure_signature(
        "x", [{"type": "slider", "action": "w", "value": 1}], False
    )


def test_button_label_and_search_text():
    assert sc._button_label({"type": "spin_control", "label": "Wheel"}) == "Spin"
    assert sc._button_label({"type": "button", "action": "spin", "button_text": "Go"}) == "Go"
    assert sc._button_label({"type": "button", "action": "reset_bar"}) == "Reset Bar"
    text = sc._search_text("Combo", [{"type": "button", "action": "add_exp"}, {"type": "toggle", "label": "Bonus"}])
    assert "combo" in text and "add exp" in text and "bonus" in text


def test_alphabetical_case_insensitive_order():
    assert sorted(["b", "A", "c", "a"], key=sc._template_sort_key) == ["A", "a", "b", "c"]


def test_fetch_prefers_engine_live_values_and_hidden_flag():
    engine = MagicMock()
    engine.get_source_controls_configs.return_value = {
        "shown": {"elements": [{"type": "toggle", "value": True}]},
        "secret": {"elements": [{"type": "button", "action": "go"}], "hidden": True},
        "empty": {"elements": []},
    }
    parser = MagicMock()
    parser.is_config_hidden.return_value = False
    with patch.object(sc.web_engine, "web_engine_instance", engine), patch.object(
        sc.template_config_parser, "TemplateConfigParser", return_value=parser
    ):
        configs, hidden, errors = sc._fetch_source_controls()
    assert set(configs) == {"shown", "secret"}
    assert hidden == {"secret"}
    assert errors == []
    parser.get_config_files.assert_not_called()


def test_fetch_falls_back_to_template_files_without_engine():
    parser = MagicMock()
    parser.get_config_files.return_value = ["legacy", "broken"]

    def load(name, include_dynamic_controls=True):
        if name == "broken":
            raise ValueError("bad json")
        return {"elements": [{"type": "button", "action": "go"}, {"type": "text", "id": "t"}]}

    parser.load_config.side_effect = load
    parser.is_config_hidden.return_value = False
    with patch.object(sc.web_engine, "web_engine_instance", None), patch.object(
        sc.template_config_parser, "TemplateConfigParser", return_value=parser
    ):
        configs, hidden, errors = sc._fetch_source_controls()
    assert configs == {"legacy": {"elements": [{"type": "button", "action": "go"}]}}
    assert [name for name, _ in errors] == ["broken"]


def test_spore_counter_payloads_unchanged():
    el = {"target_counter_id": "c1"}
    assert sc._spore_counter_payload(el, "increment", 0) == {
        "target_counter_id": "c1",
        "operation": "increment",
        "delta": {"kind": "fixed", "value": 1},
    }
    assert sc._spore_counter_payload(el, "reset", 5) == {"target_counter_id": "c1", "operation": "reset"}


def test_dock_url_uses_engine_port():
    engine = MagicMock(port=5123)
    engine._local_connect_host.return_value = "127.0.0.1"
    with patch.object(sc.web_engine, "web_engine_instance", engine):
        assert sc.dock_url() == "http://127.0.0.1:5123/source_controls"
