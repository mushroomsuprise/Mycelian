# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/web_engine.py (part 3): streamer info, the twitch-api-request
proxy, PSN debug, template stats/logs, Stream Deck template actions and config
helpers, connector/dynamic-control dispatch, dynamic control handler registration
and every ``_handle_*_control`` branch.

Collaborators (state manager, Twitch API, statistics, template configs) are faked;
templates live under tmp_path.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import dataobjects, web_engine  # noqa: E402
from modules import template_config_parser as tcp_mod  # noqa: E402
from modules.spore_studio import assets_watcher  # noqa: E402
from modules.spore_studio import containers as containers_mod  # noqa: E402
from modules.template_config_parser import TemplateConfigParser  # noqa: E402

WE = web_engine.WebEngine


# ------------------------------------------------------------------ fakes / fixtures


class FakeParser:
    """In-memory stand-in for TemplateConfigParser."""

    def __init__(self, configs=None, fail=()):
        self.configs = dict(configs or {})
        self.fail = set(fail)
        self.saved = []
        self.load_calls = []

    def get_config_files(self):
        return list(self.configs)

    def load_config(self, name, include_dynamic_controls=False, include_streamdeck_options=False,
                    create=False, copy_result=True):
        self.load_calls.append(name)
        if name in self.fail:
            raise RuntimeError(f"cannot load {name}")
        return self.configs.get(name, {})

    def get_streamdeck_configs(self):
        return {k: v for k, v in self.configs.items() if isinstance(v, dict) and "streamdeck_options" in v}

    def get_config_path(self, name):
        return f"/nonexistent/{name}.json"

    def save_config(self, name, config):
        self.saved.append((name, config))
        return True

    def is_config_hidden(self, name):
        return False


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    tdir = tmp_path / "templates"
    tdir.mkdir()
    adir = tmp_path / "assets"
    adir.mkdir()
    monkeypatch.setattr(web_engine, "get_template_path", lambda rel="": str(tdir / rel) if rel else str(tdir))
    monkeypatch.setattr(web_engine, "get_assets_path", lambda rel="": str(adir / rel) if rel else str(adir))
    return SimpleNamespace(templates=tdir, assets=adir, configs=tdir / "template_configs")


@pytest.fixture
def engine(dirs, monkeypatch):
    for name in ("ALERT_PLAYING", "ALERTS_PAUSED", "ALERTS_MUTED", "web_engine_instance", "web_engine_running",
                 "EXPECTED_ALERT_COMPLETE_SEQ"):
        monkeypatch.setattr(web_engine, name, getattr(web_engine, name))
    for meth in ("ensure_web_engine_heartbeat", "ensure_template_control_emit_worker", "_refresh_cached_listener"):
        monkeypatch.setattr(WE, meth, lambda self: None)
    for fn in ("_notify_alert_processor", "_sync_pause_button_state", "_sync_mute_button_state"):
        monkeypatch.setattr(web_engine, fn, lambda: None)
    monkeypatch.setattr(web_engine, "broadcast_alert_queue_state", lambda force=False: None)
    monkeypatch.setattr(assets_watcher, "ensure_background_poller", lambda sio: None)
    monkeypatch.setattr(containers_mod, "list_enabled_routes", lambda: [])
    monkeypatch.setattr(tcp_mod, "get_shared_parser", lambda: TemplateConfigParser(config_dir=str(dirs.configs)))
    eng = WE(template_dir=str(dirs.templates), port=5994)
    eng.template_config_parser = FakeParser()
    eng.bg_tasks = []
    monkeypatch.setattr(eng.socketio, "start_background_task", lambda fn, *a, **k: eng.bg_tasks.append(fn))
    yield eng
    eng._close_emit_wake_pipe()


@pytest.fixture
def sio(engine):
    c = engine.socketio.test_client(engine.app, flask_test_client=engine.app.test_client())
    assert c.is_connected()
    c.get_received()
    yield c
    if c.is_connected():
        c.disconnect()


def received(client, name):
    return [e["args"][0] if e["args"] else None for e in client.get_received() if e["name"] == name]


def names(client):
    return [e["name"] for e in client.get_received()]


def flush(engine):
    return engine._drain_safe_emit_queue()


def call(client, event, *args):
    return client.emit(event, *args, callback=True)


def _boom(*a, **k):
    raise RuntimeError("boom")


@pytest.fixture
def emits(engine, monkeypatch):
    """Record direct socketio.emit calls (the _handle_* helpers emit directly)."""
    out = []
    monkeypatch.setattr(engine.socketio, "emit", lambda event, data=None, **kw: out.append((event, data)))
    return out


@pytest.fixture
def safe(engine, monkeypatch):
    """Record safe_emit calls."""
    out = []
    monkeypatch.setattr(engine, "safe_emit", lambda event, data=None, to=None: out.append((event, data)) or True)
    return out


class State:
    def __init__(self, twitch=None, app=None):
        self.twitch = twitch
        self.app = app
        self.psn = None

    def get_twitch_data(self):
        return self.twitch

    def get_app_settings(self):
        return self.app

    def get_live_psn_data(self):
        return self.psn


# ------------------------------------------------------------------ get-streamer-info


@pytest.fixture
def streamer(monkeypatch):
    from modules import twitch as twitch_mod

    state = State()
    monkeypatch.setattr(dataobjects, "state_manager", state)
    icons = {"value": {"url_1x": "i1"}}

    def icon(user_id):
        if isinstance(icons["value"], Exception):
            raise icons["value"]
        return icons["value"]

    monkeypatch.setattr(twitch_mod, "fetch_channel_points_currency_icon", icon)
    return SimpleNamespace(state=state, icons=icons)


def test_streamer_info_with_icon(engine, sio, streamer):
    streamer.state.twitch = dataobjects.TwitchData(user_id="42", current_category="Art")
    streamer.state.app = SimpleNamespace(streamer_name="Shroom")
    assert call(sio, "get-streamer-info") is True
    assert received(sio, "streamer-info") == [{"user_id": "42", "streamer_name": "Shroom", "current_category": "Art",
                                               "channel_points_icon": {"url_1x": "i1"}}]


@pytest.mark.parametrize("icon", [None, RuntimeError("gql down")])
def test_streamer_info_without_icon(engine, sio, streamer, icon):
    streamer.icons["value"] = icon
    streamer.state.twitch = dataobjects.TwitchData(user_id="42")
    assert call(sio, "get-streamer-info") is True
    assert received(sio, "streamer-info") == [{"user_id": "42", "streamer_name": "Unknown Streamer",
                                               "current_category": "No Category"}]


def test_streamer_info_defaults_and_error(engine, sio, streamer):
    streamer.state.app = SimpleNamespace(streamer_name="S")
    assert call(sio, "get-streamer-info") is True
    assert received(sio, "streamer-info") == [{"user_id": None, "streamer_name": "S",
                                               "current_category": "No Category"}]
    streamer.state.app = None
    streamer.state.twitch = dataobjects.TwitchData(user_id="")
    call(sio, "get-streamer-info")
    assert received(sio, "streamer-info")[0]["streamer_name"] == "Unknown Streamer"
    streamer.state.get_twitch_data = _boom
    assert call(sio, "get-streamer-info") is False
    assert received(sio, "streamer-info-error") == [{"error": "boom"}]


# ------------------------------------------------------------------ twitch-api-request


class Api:
    def __init__(self, error=None):
        self.auth_token, self.client_id, self.client_secret, self.refresh_token = "t", "c", "s", "r"
        self.token_expiry = None
        self.error = error
        self.calls = []

    async def generic_api_call(self, url, method, params, json_data):
        self.calls.append((url, method, params, json_data))
        if self.error:
            raise self.error
        return {"data": ["ok"]}


@pytest.fixture
def helix(engine, monkeypatch):
    coros = []
    monkeypatch.setattr(engine, "_submit_twitch_api_coro", coros.append)
    state = State(twitch=dataobjects.TwitchData(auth_token="t", client_id="c", client_secret="s", refresh_token="r"))
    monkeypatch.setattr(dataobjects, "state_manager", state)
    api = Api()
    monkeypatch.setattr(web_engine, "twitch", SimpleNamespace(twitch_api=api))

    def run():
        for coro in coros:
            asyncio.run(coro)
        coros.clear()

    return SimpleNamespace(coros=coros, state=state, api=api, run=run)


URL = "https://api.twitch.tv/helix/streams"


def _responses(engine, sio):
    flush(engine)
    return received(sio, "twitch-api-response")


@pytest.mark.parametrize("payload, error, rid", [
    ({"endpoint": URL}, "Invalid data format: endpoint and requestId required", "unknown"),
    ({"requestId": "r1"}, "Invalid data format: endpoint and requestId required", "r1"),
    ({"endpoint": "http://api.twitch.tv/x", "requestId": "r2"}, "endpoint must be https://api.twitch.tv/…", "r2"),
    ({"endpoint": URL, "requestId": "r3", "params": [1]}, "params must be an object when provided", "r3"),
    ({"endpoint": URL, "requestId": "r4", "json": "x"}, "json_data (or json) must be an object when provided", "r4"),
])
def test_twitch_api_request_validation(engine, sio, helix, payload, error, rid):
    sio.emit("twitch-api-request", payload)
    assert _responses(engine, sio) == [{"success": False, "error": error, "requestId": rid}]
    assert helix.coros == []


def test_twitch_api_request_non_dict_payload_gets_error_response(engine, sio, helix):
    sio.emit("twitch-api-request", ["not", "a", "dict"])
    assert _responses(engine, sio)[0]["success"] is False


def test_twitch_api_request_not_initialised_or_unauthenticated(engine, sio, helix, monkeypatch):
    monkeypatch.setattr(web_engine, "twitch", SimpleNamespace(twitch_api=None))
    sio.emit("twitch-api-request", {"endpoint": URL, "requestId": "a"})
    assert _responses(engine, sio) == [{"success": False, "error": "Twitch API not initialized", "requestId": "a"}]
    monkeypatch.setattr(web_engine, "twitch", SimpleNamespace(twitch_api=helix.api))
    for twitch_data in (None, dataobjects.TwitchData(client_id="c"), dataobjects.TwitchData(auth_token="t")):
        helix.state.twitch = twitch_data
        sio.emit("twitch-api-request", {"endpoint": URL, "requestId": "b"})
        assert _responses(engine, sio) == [{"success": False, "error": "Twitch authentication required",
                                            "requestId": "b"}]


@pytest.mark.parametrize("expiry, year", [("2032-05-01T00:00:00", 2032), ("garbage", None), ("", None)])
def test_twitch_api_request_syncs_tokens_and_succeeds(engine, sio, helix, expiry, year):
    helix.state.twitch = dataobjects.TwitchData(auth_token="t2", client_id="c2", client_secret="s2",
                                                refresh_token="r2", token_expiry=expiry)
    sio.emit("twitch-api-request", {"endpoint": f" {URL} ", "requestId": "ok", "method": "patch",
                                    "params": {"first": 1}, "json": {"title": "x"}})
    helix.run()
    assert (helix.api.auth_token, helix.api.client_id) == ("t2", "c2")
    assert (helix.api.token_expiry.year if helix.api.token_expiry else None) == year
    assert helix.api.calls == [(URL, "PATCH", {"first": 1}, {"title": "x"})]
    assert _responses(engine, sio) == [{"success": True, "data": {"data": ["ok"]}, "requestId": "ok"}]


def test_twitch_api_request_json_data_preferred(engine, sio, helix):
    sio.emit("twitch-api-request", {"endpoint": URL, "requestId": "j", "json_data": {"a": 1}, "json": {"b": 2}})
    helix.run()
    assert helix.api.calls[0][3] == {"a": 1}


def test_twitch_api_request_skips_anonymous_user_lookup(engine, sio, helix):
    sio.emit("twitch-api-request", {"endpoint": "https://api.twitch.tv/helix/users?login=has space",
                                    "requestId": "u"})
    assert _responses(engine, sio) == [{"success": False, "error": "User lookup skipped (anonymous or invalid login)",
                                        "requestId": "u"}]
    sio.emit("twitch-api-request", {"endpoint": "https://api.twitch.tv/helix/users", "requestId": "u2",
                                    "params": {"login": "realname"}})
    helix.run()
    assert _responses(engine, sio)[0]["success"] is True


@pytest.mark.parametrize("message", [
    "Authentication required: token expired",
    "User lookup skipped for x",
    "Something else broke",
])
def test_twitch_api_request_call_errors(engine, sio, helix, message):
    helix.api.error = RuntimeError(message)
    sio.emit("twitch-api-request", {"endpoint": URL, "requestId": "e"})
    helix.run()
    assert _responses(engine, sio) == [{"success": False, "error": message, "requestId": "e"}]


@pytest.mark.parametrize("online", [True, False, "raise"])
def test_twitch_api_request_network_errors(engine, sio, helix, monkeypatch, online):
    from modules import connection_monitor

    if online == "raise":
        monkeypatch.setattr(connection_monitor, "is_internet_available", _boom)
    else:
        monkeypatch.setattr(connection_monitor, "is_internet_available", lambda *a, **k: online)
    helix.api.error = RuntimeError("Network error during API call: reset")
    sio.emit("twitch-api-request", {"endpoint": URL, "requestId": "n"})
    helix.run()
    assert _responses(engine, sio)[0]["error"] == "Network error during API call: reset"


def test_twitch_api_request_submit_failure(engine, sio, helix, monkeypatch):
    def submit(coro):
        coro.close()
        raise RuntimeError("queue full")

    monkeypatch.setattr(engine, "_submit_twitch_api_coro", submit)
    sio.emit("twitch-api-request", {"endpoint": URL, "requestId": "q"})
    assert _responses(engine, sio) == [{"success": False, "error": "queue full", "requestId": "q"}]


# ------------------------------------------------------------------ debug_psn_data


def test_debug_psn_data(engine, sio, monkeypatch):
    from modules import psn_service

    state = State()
    monkeypatch.setattr(web_engine, "state_manager", state)
    monkeypatch.setattr(psn_service, "_public_psn_payload", lambda d: {"game": "G", "trophies": 3})
    monkeypatch.setattr(web_engine, "_dev_debug_endpoints_enabled", lambda: False)
    assert call(sio, "debug_psn_data") == {"error": "Not found"}
    monkeypatch.setattr(web_engine, "_dev_debug_endpoints_enabled", lambda: True)
    info = call(sio, "debug_psn_data")
    assert info["psn_data_available"] is False and info["psn_data_fields"] == [] and info["psn_data_content"] is None
    state.psn = object()
    info = call(sio, "debug_psn_data")
    assert info["psn_data_available"] is True and info["psn_data_fields"] == ["game", "trophies"]
    assert received(sio, "debug_psn_response")[-1]["psn_data_content"] == {"game": "G", "trophies": 3}
    state.get_live_psn_data = _boom
    err = call(sio, "debug_psn_data")
    assert err["error"] == "boom" and "client_sid" in err
    assert received(sio, "debug_psn_response")[-1]["error"] == "boom"


# ------------------------------------------------------------------ submit_template_stat / template_log


def test_submit_template_stat(engine, sio, monkeypatch):
    calls = []
    sm = SimpleNamespace(submit_template_stat=lambda **kw: calls.append(kw) or kw["stat_name"] != "bad")
    monkeypatch.setattr(web_engine.statistics_manager, "get_statistics_manager", lambda: sm)
    sio.emit("submit_template_stat", "junk")
    sio.emit("submit_template_stat", {"template_name": "t"})
    sio.emit("submit_template_stat", {"template_name": "t", "stat_name": "wins", "stat_value": 2, "increment": True})
    sio.emit("submit_template_stat", {"template_name": "t", "stat_name": "bad", "stat_value": 1})
    out = received(sio, "template_stat_response")
    assert out[0] == {"success": False, "error": "Invalid data format: must be a dictionary"}
    assert out[1] == {"success": False, "error": "Missing required fields: stat_name, stat_value"}
    assert out[2] == {"success": True, "template_name": "t", "stat_name": "wins", "stat_value": 2, "increment": True}
    assert out[3] == {"success": False, "error": "Failed to submit template statistic", "template_name": "t",
                      "stat_name": "bad"}
    assert calls[0] == {"template_name": "t", "stat_name": "wins", "stat_value": 2, "increment": True}
    assert calls[1]["increment"] is False
    monkeypatch.setattr(web_engine.statistics_manager, "get_statistics_manager", _boom)
    sio.emit("submit_template_stat", {"template_name": "t", "stat_name": "x", "stat_value": 1})
    assert received(sio, "template_stat_response") == [{"success": False, "error": "boom"}]


def test_template_log(engine, sio, monkeypatch):
    seen = []
    monkeypatch.setattr(web_engine, "process_template_log", lambda data, key, limiter: seen.append((data, key, limiter)))
    sio.emit("template_log", {"level": "info", "msg": "hi"})
    data, key, limiter = seen[0]
    assert data == {"level": "info", "msg": "hi"} and key.startswith("sid:")
    assert limiter is engine._template_log_rate_limiter
    monkeypatch.setattr(web_engine, "process_template_log", _boom)
    sio.emit("template_log", {})  # logged, not raised


# ------------------------------------------------------------------ get_template_configs_for_streamdeck


CONFIGS = {
    "sd": {"streamdeck_options": {"actions": {"go": {"name": "Go"}}}},
    "dyn": {"dynamic_controls": {"elements": [{"type": "button", "action": "fire"}]}},
    "plain": {"elements": []},
    "broken": {},
}


def test_template_configs_for_streamdeck(engine, sio):
    engine.template_config_parser = FakeParser(CONFIGS, fail={"broken"})
    sio.emit("get_template_configs_for_streamdeck")
    (resp,) = received(sio, "template_configs_response")
    assert resp["event"] == "template_configs_response"
    assert sorted(resp["data"]) == ["dyn", "sd"] and resp["count"] == 2


def test_template_configs_for_streamdeck_error(engine, sio):
    engine.template_config_parser = SimpleNamespace(get_streamdeck_configs=_boom)
    sio.emit("get_template_configs_for_streamdeck")
    assert received(sio, "template_configs_response") == [
        {"event": "template_configs_response", "error": "boom", "data": {}, "count": 0}]


# ------------------------------------------------------------------ streamdeck_template_action (socket)


@pytest.fixture
def sd(engine, monkeypatch):
    calls = SimpleNamespace(execute=[], dynamic=[], connector=[], skips=[])
    monkeypatch.setattr(engine, "_execute_streamdeck_action", lambda *a: calls.execute.append(a))
    monkeypatch.setattr(engine, "_execute_dynamic_control_action", lambda *a: calls.dynamic.append(a))
    monkeypatch.setattr(engine, "_execute_connector_action", lambda *a: calls.connector.append(a))
    monkeypatch.setattr(web_engine, "skip_current_alert",
                        lambda aid, source="": calls.skips.append(source) or {"ok": True, "reason": "skipped"})
    engine.template_config_parser = FakeParser({
        "tpl": {"streamdeck_options": {"actions": {
            "go": {"name": "Go", "event": "tpl_fire", "default_data": {"n": 1}},
            "num": {"name": "Num", "event": 7},
            "same": {"name": "Same"},
        }}},
        "dyn": {"dynamic_controls": {"elements": ["junk", {"action": "other"}, {"action": "bump", "type": "button"}]}},
        "conn": {"connector_actions": {"ping": {"trigger": "pong"}}},
        "sdempty": {"streamdeck_options": {"actions": {}}},
    })
    return calls


def _sd_events(sio):
    return [(e["name"], e["args"][0] if e["args"] else None) for e in sio.get_received()]


def test_sd_action_invalid(engine, sio, sd):
    sio.emit("streamdeck_template_action", "junk")
    assert received(sio, "streamdeck_action_response") == [{"success": False, "error": "Invalid data format"}]
    sio.emit("streamdeck_template_action", {"templateName": "tpl", "requestId": 5})
    events = _sd_events(sio)
    assert events == [("streamdeck_action_response", {"success": False, "error": "Missing templateName or actionName"}),
                      ("response", {"success": False, "error": "Missing templateName or actionName", "requestId": 5})]
    sio.emit("streamdeck_template_action", {"actionName": "x"})
    assert names(sio) == ["streamdeck_action_response"]


def test_sd_action_skip(engine, sio, sd):
    sio.emit("streamdeck_template_action", {"templateName": "alerts", "actionName": "skip_alert", "requestId": "r"})
    events = _sd_events(sio)
    assert events[0][0] == "streamdeck_action_response" and events[0][1]["skip"] == {"ok": True, "reason": "skipped"}
    assert events[1][0] == "response" and events[1][1]["requestId"] == "r"
    assert sd.skips == ["streamdeck"]
    sio.emit("streamdeck_template_action", {"templateName": "alerts", "actionName": "skip_alert"})
    assert names(sio) == ["streamdeck_action_response"]


def test_sd_action_template_missing(engine, sio, sd):
    sio.emit("streamdeck_template_action", {"templateName": "nope", "actionName": "go", "requestId": 1})
    events = _sd_events(sio)
    assert events == [("streamdeck_action_response", {"success": False, "error": "Template not found: nope"}),
                      ("response", {"success": False, "error": "Template not found: nope", "requestId": 1})]
    sio.emit("streamdeck_template_action", {"templateName": "nope", "actionName": "go"})
    assert names(sio) == ["streamdeck_action_response"]


def test_sd_action_streamdeck_options(engine, sio, sd):
    sio.emit("streamdeck_template_action", {"templateName": "tpl", "actionName": "Go", "actionData": '{"m": 2}',
                                            "eventName": "stale", "requestId": "x"})
    events = _sd_events(sio)
    assert events[0] == ("streamdeck_template_action", {"templateName": "tpl", "actionName": "go",
                                                        "eventName": "tpl_fire", "actionData": {"n": 1, "m": 2}})
    assert events[1] == ("tpl_go", {"n": 1, "m": 2})
    assert events[2] == ("tpl_tpl_fire", {"n": 1, "m": 2})
    assert events[3][0] == "streamdeck_action_response" and events[3][1]["actionName"] == "go"
    assert events[4][0] == "response" and events[4][1]["requestId"] == "x"
    assert sd.execute == [("tpl", "go", {"name": "Go", "event": "tpl_fire", "default_data": {"n": 1}}, {"m": 2})]


def test_sd_action_non_string_event_and_default_event(engine, sio, sd):
    sio.emit("streamdeck_template_action", {"templateName": "tpl", "actionName": "num"})
    events = _sd_events(sio)
    assert events[0][1]["eventName"] == "7"
    assert ("tpl_7", {}) in events
    sio.emit("streamdeck_template_action", {"templateName": "tpl", "actionName": "same"})
    events = _sd_events(sio)
    assert events[0][1]["eventName"] == "tpl_same"
    assert [e[0] for e in events] == ["streamdeck_template_action", "tpl_same", "tpl_tpl_same",
                                      "streamdeck_action_response"]


def test_sd_action_dynamic_control_and_connector(engine, sio, sd):
    sio.emit("streamdeck_template_action", {"templateName": "dyn", "actionName": "bump", "actionData": {"v": 1}})
    assert sd.dynamic == [("dyn", {"action": "bump", "type": "button"}, {"v": 1})]
    assert names(sio)[-1] == "streamdeck_action_response"
    sio.emit("streamdeck_template_action", {"templateName": "conn", "actionName": "ping"})
    assert sd.connector == [("conn", "ping", {"trigger": "pong"}, {})]


@pytest.mark.parametrize("template, action", [("tpl", "missing"), ("dyn", "missing"), ("conn", "missing"),
                                              ("sdempty", "x")])
def test_sd_action_not_found(engine, sio, sd, template, action):
    sio.emit("streamdeck_template_action", {"templateName": template, "actionName": action, "requestId": 2})
    events = _sd_events(sio)
    assert events[0] == ("streamdeck_action_response", {"success": False, "error": f"Action not found: {action}"})
    assert events[1][1]["requestId"] == 2


def test_sd_action_not_found_with_odd_options(engine, sio, sd):
    engine.template_config_parser.configs["odd"] = {"streamdeck_options": "junk", "x": 1}
    sio.emit("streamdeck_template_action", {"templateName": "odd", "actionName": "a"})
    assert received(sio, "streamdeck_action_response") == [{"success": False, "error": "Action not found: a"}]


def test_sd_action_error(engine, sio, sd):
    engine.template_config_parser = SimpleNamespace(load_config=_boom)
    sio.emit("streamdeck_template_action", {"templateName": "tpl", "actionName": "go", "requestId": 3})
    assert _sd_events(sio) == [("streamdeck_action_response", {"success": False, "error": "boom"}),
                               ("response", {"success": False, "error": "boom", "requestId": 3})]
    sio.emit("streamdeck_template_action", {"templateName": "tpl", "actionName": "go"})
    assert names(sio) == ["streamdeck_action_response"]


# ------------------------------------------------------------------ Stream Deck config helpers


def test_load_streamdeck_template_config_cache(engine, dirs):
    parser = TemplateConfigParser(config_dir=str(dirs.configs))
    engine.template_config_parser = parser
    path = dirs.configs / "t.json"
    path.write_text('{"elements": [], "streamdeck_options": {"actions": {"a": {}}}}')
    first = engine._load_streamdeck_template_config("t")
    assert "streamdeck_options" in first
    assert engine._load_streamdeck_template_config("t") is first  # cached by mtime
    import os

    os.utime(path, (1, 1))
    assert engine._streamdeck_config_cache["t"]["mtime"] != 1
    engine._load_streamdeck_template_config("t")
    assert engine._streamdeck_config_cache["t"]["mtime"] == 1


def test_load_streamdeck_template_config_edge_cases(engine, monkeypatch):
    engine.template_config_parser = FakeParser({"x": "not a dict"})
    assert engine._load_streamdeck_template_config("x") == {}
    assert engine._streamdeck_config_cache["x"] == {"mtime": 0.0, "cfg": {}}
    engine._streamdeck_config_cache["y"] = {"mtime": 0.0, "cfg": "junk"}
    assert engine._load_streamdeck_template_config("y") == {}
    monkeypatch.setattr(web_engine.os.path, "exists", lambda p: True)
    monkeypatch.setattr(web_engine.os.path, "getmtime", lambda p: (_ for _ in ()).throw(OSError("gone")))
    engine.template_config_parser.configs["z"] = {"a": 1}
    assert engine._load_streamdeck_template_config("z") == {"a": 1}


def test_emit_streamdeck_template_events(engine, safe):
    engine._emit_streamdeck_template_events("tpl", "go", "tpl_fire", {"n": 1})
    assert safe == [("tpl_fire", {"n": 1}),
                    ("streamdeck_template_action", {"templateName": "tpl", "actionName": "go",
                                                    "eventName": "tpl_fire", "actionData": {"n": 1}}),
                    ("tpl_go", {"n": 1})]
    safe.clear()
    engine._emit_streamdeck_template_events("tpl", "go", "tpl_go", {})
    assert [e for e, _ in safe] == ["tpl_go", "streamdeck_template_action"]


def test_streamdeck_http_dispatch_emits(engine, safe, monkeypatch):
    skips = []
    monkeypatch.setattr(web_engine, "skip_current_alert", lambda aid, source="": skips.append(source) or {"reason": "x"})
    engine.template_config_parser = FakeParser({"tpl": {"streamdeck_options": {"actions": {
        "go": {"event": "tpl_fire", "default_data": {"a": 1}}}}}})
    assert engine._streamdeck_http_dispatch_emits("tpl", "go", "stale", '{"b": 2}') == ("go", "tpl_fire")
    assert safe[0] == ("tpl_fire", {"a": 1, "b": 2})
    safe.clear()
    assert engine._streamdeck_http_dispatch_emits("nocfg", "act", "client_ev", None,
                                                  use_client_event_name=True) == ("act", "client_ev")
    assert safe[0] == ("client_ev", {})
    safe.clear()
    assert engine._streamdeck_http_dispatch_emits("alerts", "skip_alert", "", None) == ("skip_alert", "skip_alert")
    assert skips == ["streamdeck"] and safe == []


def test_resolve_streamdeck_options_action_delegates(engine):
    spec = {"name": "Go"}
    assert engine._resolve_streamdeck_options_action({"k": spec}, "Go") == (spec, "k")


def test_execute_streamdeck_action(engine, safe, monkeypatch):
    skips = []
    monkeypatch.setattr(web_engine, "skip_current_alert", lambda aid, source="": skips.append(source))
    engine._execute_streamdeck_action("tpl", "go", {"event": "ev", "default_data": {"a": 1}}, {"b": 2})
    engine._execute_streamdeck_action("tpl", "plain", {}, None)
    assert safe == [("ev", {"a": 1, "b": 2}), ("tpl_plain", {})]
    engine._execute_streamdeck_action("alerts", "skip_alert", {}, None)
    assert skips == ["streamdeck"] and len(safe) == 2
    engine._execute_streamdeck_action("tpl", "bad", "not a dict", None)  # logged
    monkeypatch.setattr(web_engine.logger, "isEnabledFor", lambda level: True)
    engine._execute_streamdeck_action("tpl", "dbg", {}, None)
    assert safe[-1] == ("tpl_dbg", {})


def test_execute_dynamic_control_action(engine, safe, monkeypatch):
    engine._execute_dynamic_control_action("tpl", {"type": "counter_control", "action": "c_increment"}, {"x": 1})
    engine._execute_dynamic_control_action("tpl", {"type": "button"}, "junk")
    engine._execute_dynamic_control_action("tpl", {"type": "counter_control", "target_counter_id": "k", "step": "x"},
                                           {})
    assert safe[0] == ("tpl_c_increment", {"action": "increment", "x": 1})
    assert safe[1] == ("tpl_counter_update", {})
    assert safe[2][1]["delta"] == {"kind": "fixed", "value": 1}
    monkeypatch.setattr(engine, "safe_emit", _boom)
    engine._execute_dynamic_control_action("tpl", {"action": "a"}, {})  # logged


def test_execute_connector_action(engine, safe, monkeypatch):
    engine._execute_connector_action("tpl", "ping", {"trigger": "pong"}, {"v": 1})
    engine._execute_connector_action("tpl", "ping", {}, {})
    assert safe == [("connector_pong", {"action": "pong", "template": "tpl", "data": {"v": 1}}),
                    ("connector_ping", {"action": "ping", "template": "tpl", "data": {}})]
    engine._execute_connector_action("tpl", "ping", "not a dict", {})  # logged
    assert len(safe) == 2


# ------------------------------------------------------------------ toggles


def test_toggle_alerts_and_mute(engine, monkeypatch):
    updates = []
    monkeypatch.setattr(engine, "pause_status_update", lambda: updates.append(("pause", web_engine.ALERTS_PAUSED)))
    monkeypatch.setattr(engine, "mute_status_update", lambda: updates.append(("mute", web_engine.ALERTS_MUTED)))
    monkeypatch.setattr(web_engine, "ALERTS_PAUSED", False)
    monkeypatch.setattr(web_engine, "ALERTS_MUTED", False)
    engine.toggle_alerts()
    engine.toggle_alerts()
    engine.toggle_mute()
    assert updates == [("pause", True), ("pause", False), ("mute", True)]


# ------------------------------------------------------------------ dynamic control registration


def _use_shared(monkeypatch, parser):
    monkeypatch.setattr(tcp_mod, "get_shared_parser", lambda: parser)


def test_register_dynamic_control_handlers(engine, monkeypatch, sio):
    parser = FakeParser({
        "tpl": {"dynamic_controls": {"elements": [
            {"type": "button", "action": "fire"},
            {"type": "counter_control", "action": "bump", "target_counter_id": "k"},
            {"type": "counter_control"},
            {"type": "counter_control", "action_increment": ""},  # blank action is skipped
            {"type": "timer_control", "target_timer_id": "Round Timer"},
            {"type": "timer_control"},
            {"type": "slider", "action": ""},
            {"type": "mystery", "action": "nope"},
            "junk",
        ]}},
        "nocontrols": {"elements": []},
        "broken": {},
    }, fail={"broken"})
    _use_shared(monkeypatch, parser)
    engine.register_dynamic_control_handlers()
    handlers = set(engine.get_registered_dynamic_handlers())
    from modules.spore_studio.spore_data_codegen import timer_control_event

    timers = {f"tpl_{timer_control_event('Round Timer', op)}" for op in ("start", "pause", "reset")}
    assert {"tpl_fire", "tpl_bump", "tpl_counter_increment", "tpl_counter_decrement", "tpl_counter_reset"} <= handlers
    assert timers <= handlers
    assert "tpl_nope" not in handlers
    # The registered handler forwards the event to every client.
    monkeypatch.setattr(engine, "persist_template_control_change", lambda *a: None)
    assert call(sio, "tpl_fire", {"x": 1}) is True
    assert received(sio, "tpl_fire") == [{"x": 1}]
    # Registering again does not duplicate.
    before = len(engine.get_registered_dynamic_handlers())
    engine.register_dynamic_control_handlers()
    assert len(engine.get_registered_dynamic_handlers()) == before
    engine.clear_dynamic_handlers()
    assert engine.get_registered_dynamic_handlers() == []


def test_register_dynamic_controls_early_exits(engine, monkeypatch):
    engine._registered_dynamic_handlers = set()
    monkeypatch.setattr(tcp_mod, "get_shared_parser", _boom)
    engine.register_additional_control_handlers()
    _use_shared(monkeypatch, FakeParser({}))
    engine.register_additional_control_handlers()
    assert engine._registered_dynamic_handlers == set()

    class Weird:  # truthy, but len() fails inside the scan
        def __bool__(self):
            return True

    _use_shared(monkeypatch, SimpleNamespace(get_config_files=lambda: Weird()))
    engine.register_additional_control_handlers()  # logged


def test_register_dynamic_controls_without_parser_module(engine, monkeypatch):
    import modules

    monkeypatch.delattr(modules, "template_config_parser")
    monkeypatch.setitem(sys.modules, "modules.template_config_parser", None)
    engine.register_additional_control_handlers()  # ImportError: skipped


def test_register_dynamic_controls_caps_at_fifty(engine, monkeypatch):
    configs = {f"t{i}": {"dynamic_controls": {"elements": [{"type": "button", "action": "a"}]}} for i in range(55)}
    parser = FakeParser(configs)
    _use_shared(monkeypatch, parser)
    engine.__dict__.pop("_registered_dynamic_handlers", None)
    engine.register_additional_control_handlers()
    assert len(parser.load_calls) == 50
    assert len(engine.get_registered_dynamic_handlers()) == 50


def test_register_element_handler_error(engine, monkeypatch):
    engine._registered_dynamic_handlers = set()
    monkeypatch.setattr(engine.socketio, "on", _boom)
    engine._register_element_handler("tpl", {"type": "button", "action": "a", "id": "b1"})
    assert engine._registered_dynamic_handlers == set()


def test_get_registered_dynamic_handlers_without_attr(engine):
    engine.__dict__.pop("_registered_dynamic_handlers", None)
    assert engine.get_registered_dynamic_handlers() == []
    engine.clear_dynamic_handlers()


# ------------------------------------------------------------------ dynamic handler dispatch


@pytest.mark.parametrize("element_type, method", [
    ("counter_control", "_handle_counter_control"),
    ("spin_control", "_handle_spin_control"),
    ("button", "_handle_button_control"),
    ("text_input", "_handle_text_input_control"),
    ("number_input", "_handle_number_input_control"),
    ("slider", "_handle_slider_control"),
    ("toggle", "_handle_toggle_control"),
])
def test_dynamic_handler_dispatch(engine, monkeypatch, element_type, method):
    seen, persisted = [], []
    monkeypatch.setattr(engine, "persist_template_control_change", lambda *a: persisted.append(a))
    monkeypatch.setattr(engine, method, lambda *a: seen.append(a))
    handler = engine._create_dynamic_handler("tpl", "act", element_type, {"cfg": 1})
    assert handler({"v": 1}) is True
    assert persisted == [("tpl", "act", {"v": 1})]
    expected = ("tpl", "act", {"v": 1}, {"cfg": 1}) if element_type == "button" else ("tpl", "act", {"v": 1})
    assert seen == [expected]


def test_dynamic_handler_generic_and_errors(engine, emits, monkeypatch):
    monkeypatch.setattr(engine, "persist_template_control_change", lambda *a: None)
    monkeypatch.setattr(web_engine.logger, "isEnabledFor", lambda level: True)
    assert engine._create_dynamic_handler("tpl", "go", "banner_control", {})() is True
    assert emits == [("tpl_go", {})]
    monkeypatch.setattr(engine, "persist_template_control_change", _boom)
    assert engine._create_dynamic_handler("tpl", "go", "button", {})({}) is False


# ------------------------------------------------------------------ _handle_*_control branches


@pytest.mark.parametrize("action, data, expected", [
    ("counter_increment", None, [("counter_update", {"action": "increment"})]),
    ("x_increment", None, [("counter_update", {"action": "increment"})]),
    ("increment", None, [("counter_update", {"action": "increment"})]),
    ("counter_decrement", None, [("counter_update", {"action": "decrement"})]),
    ("decrement", None, [("counter_update", {"action": "decrement"})]),
    ("counter_reset", None, [("counter_update", {"action": "reset"})]),
    ("reset", None, [("counter_update", {"action": "reset"})]),
    ("counter_message", {"text": "hi"}, [("counter_message", {"message": "hi"})]),
    ("counter_message", {}, []),
    ("counter_update", {"value": 5}, [("counter_update", {"action": "set", "value": 5})]),
    ("counter_update", None, []),
    ("other", None, []),
])
def test_counter_control_on_counter_template(engine, emits, action, data, expected):
    engine._handle_counter_control("counter", action, data)
    assert emits == expected


def test_counter_control_other_templates(engine, emits):
    engine._handle_counter_control("score", "increment", None)
    engine._handle_counter_control("score", "reset", {"a": 1})
    engine._handle_counter_control("score", "bump", {"a": 1})
    assert emits == [("score_increment", {}), ("score_reset", {"a": 1})]


def test_spin_button_slider_controls(engine, emits):
    engine._handle_spin_control("wheel", "spin", None)
    engine._handle_spin_control("wheel", "reset", {"a": 1})
    engine._handle_button_control("t", "go", None, {})
    engine._handle_slider_control("t", "vol", {"value": 3})
    assert emits == [("spin-wheel", {}), ("wheel_reset", {"a": 1}), ("t_go", {}), ("t_vol", {"value": 3})]


@pytest.mark.parametrize("template, action, data, expected", [
    ("roulette", "add_option", {"text": "A"}, [("roulette_update", {"action": "add_option", "option": "A"})]),
    ("roulette", "add_option", None, []),
    ("counter", "counter_message", {"text": "m"}, [("counter_message", {"message": "m"})]),
    ("counter", "counter_message", {}, []),
    ("t", "say", None, [("t_say", {})]),
])
def test_text_input_control(engine, emits, template, action, data, expected):
    engine._handle_text_input_control(template, action, data)
    assert emits == expected


@pytest.mark.parametrize("template, action, data, expected", [
    ("counter", "counter_update", {"value": 2}, [("counter_update", {"action": "set", "value": 2})]),
    ("counter", "counter_update", None, []),
    ("ttimer", "set", {"value": 30}, [("timer_set", {"value": 30})]),
    ("ttimer", "set", {}, []),
    ("memecalc", "update", {"value": 1}, [("memecalc_update", {"action": "set", "value": 1})]),
    ("memecalc", "add_bits", {"value": 9}, [("memecalc_update", {"action": "add", "value": 9})]),
    ("memecalc", "update", None, [("memecalc_update", {})]),
    ("memecalc", "other", {"v": 1}, [("memecalc_other", {"v": 1})]),
    ("combobar", "combobar_set", None, [("combobar_set", {})]),
    ("combobar", "set", {"v": 1}, [("combobar_set", {"v": 1})]),
    ("t", "n", None, [("t_n", {})]),
])
def test_number_input_control(engine, emits, template, action, data, expected):
    engine._handle_number_input_control(template, action, data)
    assert emits == expected


@pytest.mark.parametrize("template, action, expected", [
    ("combobar", "combobar_bonus", "combobar_bonus"),
    ("combobar", "bonus", "combobar_bonus"),
    ("t", "on", "t_on"),
])
def test_toggle_control(engine, emits, template, action, expected):
    engine._handle_toggle_control(template, action, None)
    assert emits == [(expected, {})]


# ------------------------------------------------------------------ _broadcast_template_control_event


def test_broadcast_template_control_event(engine, sio):
    engine._emit_delivery_lags_ms.clear()
    engine._broadcast_template_control_event("tpl_go", {"a": 1})
    assert received(sio, "tpl_go") == [{"a": 1}]
    assert len(engine._emit_delivery_lags_ms) == 0
    import time

    engine._broadcast_template_control_event("tpl_go", {"a": 2}, enqueued_at=time.monotonic())
    assert len(engine._emit_delivery_lags_ms) == 1


def test_broadcast_template_control_event_error(engine, monkeypatch):
    monkeypatch.setattr(engine.socketio, "emit", _boom)
    before = len(engine._socket_emit_error_times)
    with pytest.raises(RuntimeError):
        engine._broadcast_template_control_event("x", {})
    assert len(engine._socket_emit_error_times) == before + 1


def test_dynamic_handler_runs_on_server_thread(engine, sio, monkeypatch):
    # Direct emits from a registered handler reach other clients.
    monkeypatch.setattr(engine, "persist_template_control_change", lambda *a: None)
    engine._registered_dynamic_handlers = set()
    engine._register_element_handler("counter", {"type": "counter_control"})
    assert call(sio, "counter_counter_increment") is True
    assert received(sio, "counter_update") == [{"action": "increment"}]
