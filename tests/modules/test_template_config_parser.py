# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/template_config_parser.py (template config JSON parsing, caching, helpers).

Every parser here works in tmp_path; the shared parser and web engine are replaced with fakes.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import database_manager  # noqa: E402
from modules import template_config_parser as tcp  # noqa: E402
from modules import web_engine  # noqa: E402
from modules.template_config_parser import TemplateConfigParser  # noqa: E402


class FakeEngine:
    def __init__(self, broadcast=True):
        self.invalidated = 0
        self.broadcasts = []
        if not broadcast:
            self.broadcast_template_config_updated = None
            del self.broadcast_template_config_updated

    def invalidate_all_template_configs_cache(self):
        self.invalidated += 1

    def broadcast_template_config_updated(self, name):
        self.broadcasts.append(name)


@pytest.fixture
def engine(monkeypatch):
    eng = FakeEngine()
    monkeypatch.setattr(web_engine, "web_engine_instance", eng, raising=False)
    return eng


@pytest.fixture
def parser(tmp_path, engine):
    return TemplateConfigParser(config_dir=str(tmp_path / "cfg"))


def _write(parser, name, data):
    path = os.path.join(parser.config_dir, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))
    return path


# ---------------------------------------------------------------- construction / names

def test_init_creates_custom_dir(tmp_path):
    p = TemplateConfigParser(config_dir=str(tmp_path / "a" / "b"))
    assert os.path.isdir(p.config_dir)
    assert p.last_error == ""


def test_init_default_dir_uses_data_path(tmp_path, monkeypatch):
    monkeypatch.setattr(tcp, "get_data_path", lambda rel: str(tmp_path / rel))
    p = TemplateConfigParser()
    assert p.config_dir == str(tmp_path / "templates/template_configs")
    assert os.path.isdir(p.config_dir)


@pytest.mark.parametrize("bad", ["", "   ", None, "a/b", "a\\b", "..", "x..y", "../evil"])
def test_sanitize_rejects_bad_names(bad):
    with pytest.raises(ValueError):
        TemplateConfigParser._sanitize_config_name(bad)


def test_sanitize_strips():
    assert TemplateConfigParser._sanitize_config_name("  chat ") == "chat"


def test_get_config_path(parser):
    assert parser.get_config_path("chat") == os.path.join(parser.config_dir, "chat.json")


def test_config_mtime(parser, monkeypatch):
    assert parser._config_mtime("../x") == 0.0
    assert parser._config_mtime("missing") == 0.0
    _write(parser, "c", {})
    assert parser._config_mtime("c") > 0
    monkeypatch.setattr(tcp.os.path, "getmtime", lambda p: (_ for _ in ()).throw(OSError()))
    assert parser._config_mtime("c") == 0.0


def test_config_file_exists(parser):
    assert parser.config_file_exists("../x") is False
    assert parser.config_file_exists("c") is False
    _write(parser, "c", {})
    assert parser.config_file_exists("c") is True


# ---------------------------------------------------------------- loading / caching

def test_load_missing_returns_default_without_writing(parser):
    cfg = parser.load_config("newone")
    assert cfg["template_name"] == "newone"
    assert [e["id"] for e in cfg["elements"]] == ["example_text", "example_color"]
    assert not os.path.exists(parser.get_config_path("newone"))


def test_load_missing_with_create_persists(parser, engine):
    cfg = parser.load_config("newone", create=True)
    assert os.path.exists(parser.get_config_path("newone"))
    with open(parser.get_config_path("newone")) as f:
        assert json.load(f) == cfg
    assert engine.broadcasts == ["newone"]


def test_load_non_dict_json_returns_default(parser):
    _write(parser, "lst", [1, 2])
    assert parser.load_config("lst")["template_name"] == "lst"


def test_load_invalid_name_returns_empty(parser):
    assert parser.load_config("../evil") == {}


def test_load_corrupt_json_returns_empty(parser, caplog):
    # JSONDecodeError is a ValueError, so it lands in the "invalid name" branch: {} (not a default
    # config that a later save could write over the user's file).
    _write(parser, "bad", "{nope")
    assert parser.load_config("bad") == {}
    assert "Invalid config name 'bad'" in caplog.text


def test_load_other_error_returns_default(parser, monkeypatch, caplog):
    monkeypatch.setattr(parser, "_get_cached_raw_config", lambda *a, **k: (_ for _ in ()).throw(OSError("io")))
    cfg = parser.load_config("broken")
    assert cfg["template_name"] == "broken"
    assert "Error loading config for broken" in caplog.text


def test_load_filters_sections(parser):
    _write(parser, "c", {"a": 1, "dynamic_controls": {"x": 1}, "streamdeck_options": {"y": 2}})
    assert parser.load_config("c") == {"a": 1}
    assert parser.load_config("c", include_dynamic_controls=True) == {"a": 1, "dynamic_controls": {"x": 1}}
    assert parser.load_config("c", include_streamdeck_options=True) == {"a": 1, "streamdeck_options": {"y": 2}}
    assert "dynamic_controls" in parser.load_config("c", True, True)


def test_filter_config_does_not_mutate_input(parser):
    src = {"a": 1, "dynamic_controls": {}, "streamdeck_options": {}}
    out = parser._filter_config(src, False, False)
    assert out == {"a": 1}
    assert "dynamic_controls" in src and "streamdeck_options" in src
    src2 = {"a": 1, "streamdeck_options": {}}
    out2 = parser._filter_config(src2, True, False)
    assert out2 == {"a": 1} and "streamdeck_options" in src2
    assert parser._filter_config(src, True, True) is src
    assert parser._filter_config([1], False, False) == [1]


def test_cache_returns_copies_by_default(parser):
    _write(parser, "c", {"n": 1, "elements": []})
    a = parser.load_config("c")
    a["n"] = 99
    assert parser.load_config("c")["n"] == 1


def test_cache_shared_when_copy_result_false(parser, monkeypatch):
    _write(parser, "c", {"n": 1})
    a = parser.load_config("c", copy_result=False)
    b = parser.load_config("c", copy_result=False)
    assert a is b
    # served from cache: no disk read on the second hit
    monkeypatch.setattr(parser, "_load_raw_config_from_disk", lambda *a, **k: pytest.fail("disk read"))
    assert parser.load_config("c")["n"] == 1


def test_invalidate_cache_one_and_all(parser):
    _write(parser, "a", {})
    _write(parser, "b", {})
    parser.get_config_files()
    parser.load_config("a")
    parser.load_config("b")
    parser.is_config_hidden("a")
    parser.invalidate_cache("a")
    assert "a" not in parser._file_cache and "b" in parser._file_cache
    assert "a" not in parser._hidden_cache
    assert parser._listing_cache is None
    parser.get_config_files()
    parser.invalidate_cache()
    assert parser._file_cache == {} and parser._hidden_cache == {} and parser._listing_cache is None


# ---------------------------------------------------------------- listing / hidden

def test_get_config_files_lists_and_skips_spore_sidecars(parser):
    _write(parser, "chat", {})
    _write(parser, "title", {})
    _write(parser, "title.spore", {})
    with open(os.path.join(parser.config_dir, "notes.txt"), "w") as f:
        f.write("x")
    assert sorted(parser.get_config_files()) == ["chat", "title"]


def test_get_config_files_cached_by_dir_mtime(parser, monkeypatch):
    _write(parser, "chat", {})
    first = parser.get_config_files()
    monkeypatch.setattr(tcp.glob, "glob", lambda *a: pytest.fail("glob called"))
    second = parser.get_config_files()
    assert first == second
    second.append("x")
    assert parser.get_config_files() == ["chat"]


def test_get_config_files_missing_dir(parser, caplog):
    os.rmdir(parser.config_dir)
    assert parser.get_config_files() == []
    assert "not found" in caplog.text


def test_get_config_files_getmtime_error(parser, monkeypatch):
    _write(parser, "chat", {})
    monkeypatch.setattr(tcp.os.path, "getmtime", lambda p: (_ for _ in ()).throw(OSError()))
    assert parser.get_config_files() == ["chat"]
    assert parser._listing_cache[0] == 0.0


def test_hidden_and_non_hidden(parser):
    _write(parser, "shown", {"elements": []})
    _write(parser, "secret", {"hidden": True})
    _write(parser, "lst", [1])
    assert parser.is_config_hidden("secret") is True
    assert parser.is_config_hidden("shown") is False
    assert parser.is_config_hidden("lst") is False
    assert sorted(parser.get_non_hidden_config_files()) == ["lst", "shown"]


def test_hidden_cache_hit_skips_load(parser, monkeypatch):
    _write(parser, "secret", {"hidden": True})
    assert parser.is_config_hidden("secret")
    monkeypatch.setattr(parser, "load_config", lambda *a, **k: pytest.fail("loaded"))
    assert parser.is_config_hidden("secret")


def test_hidden_error_returns_false(parser, monkeypatch, caplog):
    monkeypatch.setattr(parser, "load_config", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert parser.is_config_hidden("anything") is False
    assert "Error checking if config anything is hidden" in caplog.text


def test_hidden_non_dict_load(parser, monkeypatch):
    monkeypatch.setattr(parser, "load_config", lambda *a, **k: ["hidden"])
    assert parser.is_config_hidden("x") is False


# ---------------------------------------------------------------- save / notify

def test_save_writes_and_notifies(parser, engine):
    assert parser.save_config("c", {"a": 1})
    with open(parser.get_config_path("c")) as f:
        assert json.load(f) == {"a": 1}
    assert not os.path.exists(parser.get_config_path("c") + ".tmp")
    assert engine.invalidated == 1 and engine.broadcasts == ["c"]


def test_save_invalid_name(parser):
    assert parser.save_config("a/b", {}) is False
    assert parser.last_error == "Invalid template name: 'a/b'"


def test_save_failure_sets_last_error_and_cleans_tmp(parser, monkeypatch):
    monkeypatch.setattr(tcp.os, "replace", lambda s, d: (_ for _ in ()).throw(PermissionError("locked")))
    assert parser.save_config("c", {"a": 1}) is False
    path = parser.get_config_path("c")
    assert parser.last_error == f"{path}\nPermissionError: locked"
    assert not os.path.exists(path + ".tmp")


def test_save_failure_cleanup_error_swallowed(parser, monkeypatch):
    monkeypatch.setattr(tcp.os, "replace", lambda s, d: (_ for _ in ()).throw(OSError("x")))
    monkeypatch.setattr(tcp.os, "remove", lambda p: (_ for _ in ()).throw(OSError("y")))
    assert parser.save_config("c", {}) is False


def test_save_unserializable_fails_before_tmp_written(parser):
    assert parser.save_config("c", {"x": object()}) is False
    assert "TypeError" in parser.last_error


def test_save_with_empty_parent(tmp_path, monkeypatch, engine):
    monkeypatch.chdir(tmp_path)
    p = TemplateConfigParser(config_dir=str(tmp_path))
    p.config_dir = ""
    assert p.save_config("rel", {"z": 1})
    assert json.loads((tmp_path / "rel.json").read_text()) == {"z": 1}


def test_notify_no_instance(monkeypatch):
    monkeypatch.setattr(web_engine, "web_engine_instance", None, raising=False)
    TemplateConfigParser._notify_config_saved("x")  # no error


def test_notify_without_broadcast_method(monkeypatch):
    eng = FakeEngine(broadcast=False)
    monkeypatch.setattr(web_engine, "web_engine_instance", eng, raising=False)
    TemplateConfigParser._notify_config_saved("x")
    assert eng.invalidated == 1


def test_notify_errors_swallowed(monkeypatch):
    class Boom:
        def invalidate_all_template_configs_cache(self):
            raise RuntimeError("x")

    monkeypatch.setattr(web_engine, "web_engine_instance", Boom(), raising=False)
    TemplateConfigParser._notify_config_saved("x")


# ---------------------------------------------------------------- create / delete / all / streamdeck

def test_create_config(parser):
    assert parser.create_config("../x") is False
    assert parser.create_config("n1") is True
    assert parser.load_config("n1")["template_name"] == "n1"
    assert parser.create_config("n1") is False
    assert parser.create_config("n2", {"custom": True}) is True
    assert parser.load_config("n2") == {"custom": True}


def test_create_config_error(parser, monkeypatch):
    monkeypatch.setattr(tcp.os.path, "exists", lambda p: (_ for _ in ()).throw(OSError("x")))
    assert parser.create_config("c") is False


def test_delete_config(parser, engine):
    assert parser.delete_config("../x") is False
    assert parser.delete_config("missing") is False
    _write(parser, "c", {})
    parser.load_config("c")
    assert parser.delete_config("c") is True
    assert not os.path.exists(parser.get_config_path("c"))
    assert "c" not in parser._file_cache
    assert engine.invalidated == 1


def test_delete_config_engine_none_and_error(parser, monkeypatch):
    monkeypatch.setattr(web_engine, "web_engine_instance", None, raising=False)
    _write(parser, "c", {})
    assert parser.delete_config("c") is True

    class Boom:
        def invalidate_all_template_configs_cache(self):
            raise RuntimeError("x")

    monkeypatch.setattr(web_engine, "web_engine_instance", Boom(), raising=False)
    _write(parser, "d", {})
    assert parser.delete_config("d") is True


def test_delete_config_remove_error(parser, monkeypatch):
    _write(parser, "c", {})
    monkeypatch.setattr(tcp.os, "remove", lambda p: (_ for _ in ()).throw(PermissionError("x")))
    assert parser.delete_config("c") is False


def test_get_all_configs(parser):
    _write(parser, "a", {"n": 1})
    _write(parser, "b", {"n": 2, "dynamic_controls": {}})
    assert parser.get_all_configs() == {"a": {"n": 1}, "b": {"n": 2}}


def test_get_streamdeck_configs(parser, monkeypatch):
    _write(parser, "a", {"streamdeck_options": {"k": 1}})
    _write(parser, "b", {"n": 2})
    _write(parser, "c", {})
    assert parser.get_streamdeck_configs() == {"a": {"streamdeck_options": {"k": 1}}}
    real = parser.load_config

    def flaky(name, **kw):
        if name == "c":
            raise RuntimeError("x")
        return real(name, **kw)

    monkeypatch.setattr(parser, "load_config", flaky)
    assert list(parser.get_streamdeck_configs()) == ["a"]


# ---------------------------------------------------------------- dynamic controls

@pytest.fixture
def fake_db(monkeypatch):
    calls = []
    store = {}

    def get_data(path):
        calls.append(path)
        v = store.get(path)
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(database_manager, "get_data", get_data)
    return store, calls


def test_resolve_bad_inputs():
    assert tcp.resolve_dynamic_control_values_from_elements("x") == {}
    assert tcp.resolve_dynamic_control_values_from_elements({}) == {}
    assert tcp.resolve_dynamic_control_values_from_elements({"dynamic_controls": []}) == {}
    assert tcp.resolve_dynamic_control_values_from_elements({"dynamic_controls": {"a": 1}}) == {"a": 1}


def test_resolve_takes_values_from_main_elements(fake_db):
    cfg = {
        "elements": [{"id": "speed", "value": 9}, {"id": "novalue"}, "junk", {"value": 1}],
        "dynamic_controls": {"elements": [
            {"id": "c1", "value": 1, "persist": {"target_element_id": "speed"}},
            {"id": "c2", "value": 2, "persist": {"target_element_id": "novalue"}},
            {"id": "c3", "value": 3, "persist": {"target_element_id": "unknown"}},
            {"id": "c4", "value": 4, "persist": {}},
            {"id": "c5", "value": 5, "persist": "bad"},
            {"id": "c6", "value": 6},
            "junk",
        ]},
    }
    out = tcp.resolve_dynamic_control_values_from_elements(cfg)
    vals = [e["value"] for e in out["elements"] if isinstance(e, dict)]
    assert vals == [9, 2, 3, 4, 5, 6]
    # deep copy: source untouched
    assert cfg["dynamic_controls"]["elements"][0]["value"] == 1


def test_runtime_overlay_reads_db_once_per_path(fake_db):
    store, calls = fake_db
    store["counters/x"] = {"count": 42, "other": "o"}
    store["bad"] = ["not a dict"]
    store["err"] = RuntimeError("db down")
    out = {"elements": [
        {"value": 0, "persist": {"runtime_database_path": " counters/x ", "runtime_state_key": " count "}},
        {"value": 0, "persist": {"runtime_database_path": "counters/x", "runtime_state_key": "other"}},
        {"value": 0, "persist": {"runtime_database_path": "counters/x", "runtime_state_key": "missing"}},
        {"value": 0, "persist": {"runtime_database_path": "bad", "runtime_state_key": "k"}},
        {"value": 0, "persist": {"runtime_database_path": "err", "runtime_state_key": "k"}},
        {"value": 0, "persist": {"runtime_database_path": "err", "runtime_state_key": "k2"}},
        {"value": 0, "persist": {"runtime_database_path": "  ", "runtime_state_key": "k"}},
        {"value": 0, "persist": {"runtime_database_path": 5, "runtime_state_key": "k"}},
        {"value": 0, "persist": {"runtime_database_path": "p"}},
        {"value": 0, "persist": "x"},
        "junk",
    ]}
    tcp._overlay_source_control_runtime_state(out)
    assert [e["value"] for e in out["elements"][:6]] == [42, "o", 0, 0, 0, 0]
    assert calls == ["counters/x", "bad", "err"]


def test_runtime_overlay_no_elements_list(fake_db):
    store, calls = fake_db
    d = {"elements": "x"}
    tcp._overlay_source_control_runtime_state(d)
    assert calls == []


def test_runtime_overlay_import_failure(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if fromlist and "database_manager" in fromlist and level == 1:
            raise ImportError("nope")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    d = {"elements": [{"value": 1, "persist": {"runtime_database_path": "p", "runtime_state_key": "k"}}]}
    tcp._overlay_source_control_runtime_state(d)
    assert d["elements"][0]["value"] == 1


def test_resolve_applies_runtime_overlay(fake_db):
    store, _ = fake_db
    store["p"] = {"k": "db"}
    cfg = {"elements": [{"id": "t", "value": "el"}], "dynamic_controls": {"elements": [
        {"value": 0, "persist": {"target_element_id": "t", "runtime_database_path": "p", "runtime_state_key": "k"}},
    ]}}
    assert tcp.resolve_dynamic_control_values_from_elements(cfg)["elements"][0]["value"] == "db"


# ---------------------------------------------------------------- module helpers

@pytest.mark.parametrize("raw,key", [("Hello World!", "helloworld"), (None, ""), ("  A_b-1 ", "ab1"), (12, "12")])
def test_normalize_template_match_key(raw, key):
    assert tcp.normalize_template_match_key(raw) == key


def test_get_shared_parser_singleton(monkeypatch, tmp_path):
    monkeypatch.setattr(tcp, "_shared_parser", None)
    monkeypatch.setattr(tcp, "get_data_path", lambda rel: str(tmp_path / rel))
    a = tcp.get_shared_parser()
    assert a is tcp.get_shared_parser()
    assert a.config_dir.startswith(str(tmp_path))


def test_config_element_value():
    cfg = {"elements": ["x", {"id": "a", "value": 1}, {"id": "b"}]}
    assert tcp._config_element_value(cfg, "a") == 1
    assert tcp._config_element_value(cfg, "b") is None
    assert tcp._config_element_value(cfg, "zz") is None
    assert tcp._config_element_value(None, "a") is None
    assert tcp._config_element_value({"elements": "x"}, "a") is None


@pytest.fixture
def shared(parser, monkeypatch):
    monkeypatch.setattr(tcp, "_shared_parser", parser)
    return parser


def test_match_reward_title_by_stem_and_template_name(shared):
    _write(shared, "boo_alert", {"template_name": "Spooky Boo", "elements": [
        {"id": "Queued", "value": True}, {"id": "Duration", "value": 3000}]})
    _write(shared, "other", {"template_name": "Other"})
    assert tcp.find_template_config_for_reward_title("Boo Alert")["template_name"] == "Spooky Boo"
    assert tcp.find_template_config_for_reward_title("spooky-boo")["template_name"] == "Spooky Boo"
    assert tcp.find_template_config_for_reward_title("nothing") is None
    assert tcp.find_template_config_for_reward_title("!!!") is None
    assert tcp.find_matching_template_routes_for_reward_title("Spooky Boo") == ["boo_alert", "Spooky Boo"]
    assert tcp.find_matching_template_routes_for_reward_title("nope") == []
    assert tcp.match_point_reward_dedicated_template("boo alert") == {"queued": True, "duration_seconds": 3.0}
    assert tcp.match_point_reward_dedicated_template("nope") is None


def test_routes_dedupe_and_skip_blank_template_name(monkeypatch):
    monkeypatch.setattr(tcp, "_match_template_for_reward_title", lambda t: ("same", {"template_name": "same"}))
    assert tcp.find_matching_template_routes_for_reward_title("x") == ["same"]
    monkeypatch.setattr(tcp, "_match_template_for_reward_title", lambda t: ("", {"template_name": "  "}))
    assert tcp.find_matching_template_routes_for_reward_title("x") == []
    monkeypatch.setattr(tcp, "_match_template_for_reward_title", lambda t: ("s", "notdict"))
    assert tcp.find_matching_template_routes_for_reward_title("x") == ["s"]


def test_match_skips_errors_and_non_dicts(monkeypatch):
    class P:
        def get_config_files(self):
            return ["boom", "lst", "good"]

        def load_config(self, stem, **kw):
            if stem == "boom":
                raise RuntimeError("x")
            if stem == "lst":
                return [1]
            return {"template_name": "Good"}

    monkeypatch.setattr(tcp, "_shared_parser", P())
    assert tcp._match_template_for_reward_title("good") == ("good", {"template_name": "Good"})
    assert tcp._match_template_for_reward_title("boom") is None


@pytest.mark.parametrize(
    "elements,expected",
    [
        ([{"id": "DelayBetweenAlerts", "value": "2.5"}], 2.5),
        ([{"id": "DelayBetweenAlerts", "value": -3}], 0.0),
        ([{"id": "DelayBetweenAlerts", "value": ""}], 0.5),
        ([], 0.5),
        ([{"id": "DelayBetweenAlerts", "value": "abc"}], 0.5),
    ],
)
def test_delay_between_alerts(shared, elements, expected):
    _write(shared, "alerts", {"elements": elements})
    assert tcp.delay_between_alerts_seconds() == expected


def test_point_alert_silent(shared, monkeypatch):
    _write(shared, "alerts", {"elements": [{"id": "PointAlertSilentNoMedia", "value": 1}]})
    assert tcp.point_alert_silent_no_media_enabled() is True
    _write(shared, "alerts", {"elements": []})
    shared.invalidate_cache()
    assert tcp.point_alert_silent_no_media_enabled() is False
    monkeypatch.setattr(shared, "load_config", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    assert tcp.point_alert_silent_no_media_enabled() is False


@pytest.mark.parametrize(
    "value,expected",
    [(None, 5.0), ("", 5.0), ("x", 5.0), ([1], 5.0), (7, 7.0), ("100", 100.0), (2500, 2.5)],
)
def test_point_reward_duration(value, expected):
    els = [] if value is None else [{"id": "Duration", "value": value}]
    assert tcp.point_reward_template_duration_seconds({"elements": els}) == expected


def test_build_template_queue_metadata(monkeypatch):
    class P:
        def get_config_files(self):
            return ["a", "b", "boom", "lst"]

        def load_config(self, stem, **kw):
            if stem == "boom":
                raise RuntimeError()
            if stem == "lst":
                return [1]
            if stem == "a":
                return {"template_name": "Alpha", "elements": [
                    {"id": "Duration", "value": 4}, {"id": "Queued", "value": 1}]}
            return {"elements": []}

    monkeypatch.setattr(tcp, "_shared_parser", P())
    assert tcp.build_template_queue_metadata() == {
        "a": {"Duration": 4, "Queued": True, "template_name": "Alpha"},
        "b": {"Duration": None, "Queued": False, "template_name": "b"},
    }
