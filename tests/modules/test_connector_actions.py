# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""connector_actions: JSON helpers, template/websocket/alert/chat/banner/announcement/Discord,
API call, write file, execute command and key press actions (all collaborators faked)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio
import subprocess
import types
from types import SimpleNamespace

import pytest

import modules
from modules import alertutils, connector_actions as ca, connector_core as core
from modules.connector_core import ActionType

TRIGGER = {"trigger_id": "t1", "triggered_at": 1.0, "connector_name": "C", "trigger_count": 1}


def run(coro):
    return asyncio.run(coro)


def fake_module(monkeypatch, name, **attrs):
    """Install a fake ``modules.<name>`` module (sys.modules and package attribute)."""
    mod = types.ModuleType(f"modules.{name}")
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, f"modules.{name}", mod)
    monkeypatch.setattr(modules, name, mod, raising=False)
    return mod


@pytest.fixture
def slot():
    """A running-action problem slot, as ConnectorManager sets it."""
    data = {"connector_id": "cid", "connector_name": "My Conn"}
    token = core._action_problem.set(data)
    yield data
    core._action_problem.reset(token)


@pytest.fixture
def sleeps(monkeypatch):
    calls = []

    async def fake_sleep(seconds, *a, **k):
        calls.append(seconds)

    monkeypatch.setattr(ca.asyncio, "sleep", fake_sleep)
    return calls


# --------------------------------------------------------------------------- helpers


def test_float_or():
    assert ca._float_or("2.5", 1.0) == 2.5
    assert ca._float_or(None, 1.0) == 1.0
    assert ca._float_or("x", 3.0) == 3.0


def test_safe_console_str():
    assert ca._safe_console_str(None) == ""
    assert ca._safe_console_str("héllo") == "h?llo"
    assert ca._safe_console_str(12) == "12"


def test_resolve_json_field_list_and_whitespace():
    ctx = {"username": "Bob"}
    assert ca.resolve_json_field(["{username}", 1], ctx, "x") == ["Bob", 1]
    assert ca.resolve_json_field("   ", ctx, "x") == {}
    assert ca.resolve_json_field('[1, "{username}"]', ctx, "x") == [1, "Bob"]


def test_resolve_json_field_bad_json_notifies_when_in_connector(monkeypatch, slot):
    seen = []
    fake_module(
        monkeypatch,
        "notification_engine",
        notify=lambda msg, **kw: seen.append((msg, kw)),
        nav_actions_main_tab=lambda tab: [("nav", tab)],
    )
    assert ca.resolve_json_field("{bad", {}, "Event data") == {}
    msg, kw = seen[0]
    assert "My Conn" in msg and "event data" in msg
    assert kw["dedupe_key"] == "connector:cid:badjson:Event data"
    assert kw["actions"] == [("nav", "Connectors")]
    assert kw["type"] == "warning"


def test_notify_bad_json_without_connector_is_silent(monkeypatch):
    seen = []
    fake_module(monkeypatch, "notification_engine", notify=lambda *a, **k: seen.append(a),
                nav_actions_main_tab=lambda t: [])
    ca._notify_bad_json("X", "d")
    assert seen == []


def test_notify_bad_json_swallows_errors(monkeypatch, slot):
    def boom(*a, **k):
        raise RuntimeError("no ui")

    fake_module(monkeypatch, "notification_engine", notify=boom, nav_actions_main_tab=lambda t: [])
    ca._notify_bad_json("X", "d")  # does not raise


# --------------------------------------------------------------------------- template control


def _web_engine(monkeypatch, instance, skip=False):
    emitted, skipped = [], []
    mod = fake_module(
        monkeypatch,
        "web_engine",
        web_engine_instance=instance,
        is_alert_skip_template_action=lambda t, a, n: skip,
        skip_current_alert=lambda payload, source: skipped.append(("current", source)) or {"reason": "ok"},
        skip_alert_from_payload=lambda payload, source: skipped.append(("payload", payload)) or {"reason": "ok"},
    )
    return mod, emitted, skipped


def _engine(emitted):
    return SimpleNamespace(socketio=object(), safe_emit=lambda n, d: emitted.append((n, d)))


def test_template_control_defaults_and_validate():
    a = ca.create_action(ActionType.TEMPLATE_CONTROL, "a", "n")
    assert a.control_data == {} and a.action_type == ActionType.TEMPLATE_CONTROL
    assert not a.validate_parameters()
    a.template_name = "tpl"
    assert not a.validate_parameters()
    a.control_action = "spin"
    assert a.validate_parameters()


def test_template_control_emits_with_placeholders(monkeypatch):
    emitted = []
    _web_engine(monkeypatch, _engine(emitted))
    a = ca.create_action(ActionType.TEMPLATE_CONTROL, "a", "n", template_name="wheel",
                         control_action="spin", control_data={"who": "{username}"})
    assert run(a.execute(TRIGGER, {"username": "Bob", "event_type": "chat"}))
    name, data = emitted[0]
    assert name == "wheel_spin"
    assert data == {"who": "Bob", "trigger_id": "t1", "event_type": "chat", "timestamp": 1.0}
    assert a.control_data == {"who": "{username}"}  # original untouched


def test_template_control_skip_goes_through_queue(monkeypatch):
    emitted = []
    _, _, skipped = _web_engine(monkeypatch, _engine(emitted), skip=True)
    a = ca.create_action(ActionType.TEMPLATE_CONTROL, "a", "n", template_name="alerts",
                         control_action="skip")
    assert run(a.execute(TRIGGER, {}))
    assert skipped == [("current", "connector")] and emitted == []


@pytest.mark.parametrize("instance", [None, SimpleNamespace(socketio=None)])
def test_template_control_without_server(monkeypatch, slot, instance):
    _web_engine(monkeypatch, instance)
    a = ca.create_action(ActionType.TEMPLATE_CONTROL, "a", "n", template_name="t", control_action="c")
    assert not run(a.execute(TRIGGER, {}))
    assert "overlay server" in slot["reason"]


def test_template_control_emit_error_returns_false(monkeypatch):
    def boom(n, d):
        raise RuntimeError("x")

    _web_engine(monkeypatch, SimpleNamespace(socketio=object(), safe_emit=boom))
    a = ca.create_action(ActionType.TEMPLATE_CONTROL, "a", "n", template_name="t", control_action="c")
    assert not run(a.execute(TRIGGER, {}))


# --------------------------------------------------------------------------- websocket emit


def test_websocket_emit_validate_and_defaults():
    a = ca.create_action(ActionType.WEBSOCKET_EMIT, "a", "n")
    assert a.event_data == {} and not a.validate_parameters()
    a.event_name = "x"
    assert a.validate_parameters()


def test_websocket_emit_wraps_non_dict(monkeypatch):
    emitted = []
    _web_engine(monkeypatch, _engine(emitted))
    a = ca.create_action(ActionType.WEBSOCKET_EMIT, "a", "n", event_name="ev", event_data="[1,2]")
    assert run(a.execute(TRIGGER, {}))
    assert emitted[0][1] == {"data": [1, 2], "trigger_id": "t1", "triggered_at": 1.0}


def test_websocket_emit_skip(monkeypatch):
    emitted = []
    _, _, skipped = _web_engine(monkeypatch, _engine(emitted), skip=True)
    a = ca.create_action(ActionType.WEBSOCKET_EMIT, "a", "n", event_name="skip_alert",
                         event_data={"k": 1})
    assert run(a.execute(TRIGGER, {}))
    assert skipped[0][0] == "payload" and skipped[0][1]["k"] == 1 and emitted == []


def test_websocket_emit_no_server_and_error(monkeypatch, slot):
    _web_engine(monkeypatch, None)
    a = ca.create_action(ActionType.WEBSOCKET_EMIT, "a", "n", event_name="ev")
    assert not run(a.execute(TRIGGER, {}))
    assert "overlay server" in slot["reason"]

    def boom(n, d):
        raise RuntimeError("x")

    _web_engine(monkeypatch, SimpleNamespace(socketio=object(), safe_emit=boom))
    assert not run(a.execute(TRIGGER, {}))


# --------------------------------------------------------------------------- trigger alert helpers


@pytest.mark.parametrize(
    "value,expected",
    [("bit", "bit"), (" Cheers ", "bit"), ("tips", "donation"), ("reward", "point"), ("", ""),
     (None, ""), ("nonsense", "")],
)
def test_normalize_trigger_alert_type(value, expected):
    assert ca.normalize_trigger_alert_type(value) == expected


@pytest.mark.parametrize(
    "value,expected", [("1,000", 1000.0), (" 2.5 ", 2.5), ("x", 7.0), (None, 7.0), ("nan", 7.0)]
)
def test_alert_number(value, expected):
    assert ca._alert_number(value, 7.0) == expected


def test_trigger_alert_post_init_string_data():
    a = ca.TriggerAlertAction(action_id="a", name="n", action_type=ActionType.TRIGGER_ALERT,
                              alert_type="raid", alert_data='{"amount": "3"}')
    assert a.alert_data == {"amount": "3"}
    for raw in ("", "not json", "[1]"):
        b = ca.TriggerAlertAction(action_id="a", name="n", action_type=ActionType.TRIGGER_ALERT,
                                  alert_type="weird", alert_data=raw)
        assert b.alert_data == {} and b.alert_type == "weird"
    assert not ca.create_action(ActionType.TRIGGER_ALERT, "a", "n").validate_parameters()
    assert ca.create_action(ActionType.TRIGGER_ALERT, "a", "n", alert_type="raid").validate_parameters()


class _Mgr:
    def __init__(self, alerts):
        self.alerts = alerts
        self.initialized = 0

    def initialize(self):
        self.initialized += 1

    def get_alerts_by_type(self, kind, include_ranges=False):
        assert kind == "points" and include_ranges is False
        return self.alerts


def test_point_alert_by_name(monkeypatch):
    alerts = {
        "bad": "not a dict",
        "r1": {"alert_name": "Other"},
        "r2": {"display_name": " hydrate ", "twitch_reward_id": ""},
        "r3": {"reward_name": "Stretch", "twitch_reward_id": "tw3"},
    }
    mgr = _Mgr(alerts)
    monkeypatch.setattr(alertutils, "alert_state_manager", mgr)

    def from_db(data):
        return SimpleNamespace(twitch_reward_id=data.get("preset"), data=data)

    monkeypatch.setattr(alertutils, "_alert_obj_from_db", from_db)
    assert ca._point_alert_by_name("") is None
    assert ca._point_alert_by_name(None) is None
    found = ca._point_alert_by_name("HYDRATE")
    assert found.data is alerts["r2"] and found.twitch_reward_id == "r2"
    assert ca._point_alert_by_name("stretch").twitch_reward_id == "tw3"
    assert ca._point_alert_by_name("missing") is None
    mgr.alerts = None
    assert ca._point_alert_by_name("x") is None
    alerts["r4"] = {"title": "Preset"}
    mgr.alerts = {"r4": {"title": "Preset", "preset": "kept"}}
    assert ca._point_alert_by_name("preset").twitch_reward_id == "kept"


# --------------------------------------------------------------------------- trigger alert execute


class _Alert(SimpleNamespace):
    pass


@pytest.fixture
def alert_env(monkeypatch):
    queued = []
    import modules.alert_processor as ap

    monkeypatch.setattr(ap, "enqueue_alert", queued.append)
    calls = []
    results = {}

    def make(name):
        def inner(*args):
            calls.append((name, args))
            if name in results:
                return results[name]
            return _Alert(alert_name=f"{name}-cfg", enable_alert=True, twitch_reward_id="")

        return inner

    for fn in ("fetch_cheer_alert", "fetch_sub_alert", "fetch_resub_alert", "fetch_giftsub_alert",
               "fetch_streak_alert", "fetch_raid_alert", "fetch_follow_alert",
               "fetch_donation_alert", "fetch_point_alert"):
        monkeypatch.setattr(alertutils, fn, make(fn))
    monkeypatch.setattr(alertutils, "new_alert_id", lambda prefix: prefix + "ID")
    import modules.donation_settings as ds

    monkeypatch.setattr(ds, "get_settings", lambda: SimpleNamespace(alert_currency="GBP"))
    return SimpleNamespace(queued=queued, calls=calls, results=results, mp=monkeypatch)


def _alert(alert_type, alert_id="", **data):
    return ca.create_action(ActionType.TRIGGER_ALERT, "a", "n", alert_type=alert_type,
                            alert_id=alert_id, alert_data=data)


def test_trigger_alert_unsupported_type_reports(alert_env, slot):
    a = _alert("weird")
    assert not run(a.execute(TRIGGER, {}))
    assert "isn't supported" in slot["reason"] and "weird" in slot["details"]


def test_trigger_alert_no_amount_uses_count_one_and_new_id(alert_env):
    assert run(_alert("bit").execute(TRIGGER, {"username": "Ann", "message": "yo"}))
    assert alert_env.calls[-1] == ("fetch_cheer_alert", (1,))
    alert = alert_env.queued[-1]
    assert alert.amt_cheered == 1 and alert.alert_id == "connector_t1_ID"
    assert alert.username == "Ann" and alert.message == "yo" and alert.is_test is False


def test_trigger_alert_explicit_id_and_empty_message(alert_env):
    assert run(_alert("follow", alert_id="fixed", message="").execute(TRIGGER, {"message": "evt"}))
    alert = alert_env.queued[-1]
    assert alert.alert_id == "fixed" and alert.message == ""


@pytest.mark.parametrize(
    "alert_type,fn,attr",
    [("resub", "fetch_resub_alert", "resub_month"), ("giftsub", "fetch_giftsub_alert", "gift_qty"),
     ("streak", "fetch_streak_alert", "streak_count"), ("raid", "fetch_raid_alert", "raider_count")],
)
def test_trigger_alert_counts_round(alert_env, alert_type, fn, attr):
    assert run(_alert(alert_type, amount="2.6").execute(TRIGGER, {}))
    assert alert_env.calls[-1] == (fn, (3,))
    assert getattr(alert_env.queued[-1], attr) == 3
    assert run(_alert(alert_type, amount="0").execute(TRIGGER, {}))
    assert getattr(alert_env.queued[-1], attr) == 1


def test_trigger_alert_direct_construct_none_data():
    a = ca.TriggerAlertAction(action_id="a", name="n", action_type=ActionType.TRIGGER_ALERT,
                              alert_type="Cheer", alert_data=None)
    assert a.alert_data == {} and a.alert_type == "bit"


def test_resolve_json_field_none_and_text():
    assert ca.resolve_json_field(None, {}, "x") == {}
    assert ca.resolve_json_field("hi {username}", {"username": "Al"}, "x", allow_text=True) == "hi Al"


def test_trigger_alert_sub_tier(alert_env):
    assert run(_alert("sub", amount="3", tier="2000").execute(TRIGGER, {}))
    assert alert_env.queued[-1].tier == 2 and alert_env.queued[-1].resub_month == 3
    assert run(_alert("resub", amount="2", tier="").execute(TRIGGER, {}))
    assert not hasattr(alert_env.queued[-1], "tier")


def test_trigger_alert_donation_default_currency(alert_env):
    assert run(_alert("donation", amount="10").execute(TRIGGER, {}))
    alert = alert_env.queued[-1]
    assert alert.currency == "GBP" and alert.donation_source == "connector"
    assert alert.original_amount == 10.0 and alert.donation_text
    assert run(_alert("donation").execute(TRIGGER, {}))
    from modules.donation_currency import donation_match_quantity

    assert alert_env.calls[-1] == ("fetch_donation_alert", (donation_match_quantity(0),))
    assert alert_env.queued[-1].donation_amount == 0.0


def test_trigger_alert_point_lookup_by_name_fallback(alert_env):
    alert_env.results["fetch_point_alert"] = None
    named = _Alert(alert_name="Hydrate", enable_alert=True, twitch_reward_id="x")
    alert_env.mp.setattr(ca, "_point_alert_by_name", lambda name: named if name == "Hydrate" else None)
    assert run(_alert("point", reward_id="rid", reward_name="Hydrate").execute(TRIGGER, {}))
    assert alert_env.queued[-1] is named and named.twitch_reward_id == "rid"
    # no reward id: straight to the name lookup, and no reward name keeps the alert's name
    assert run(_alert("point", reward_name="Hydrate").execute(TRIGGER, {}))
    named.alert_name = "Kept"
    alert_env.mp.setattr(ca, "_point_alert_by_name", lambda name: named)
    assert run(_alert("point").execute(TRIGGER, {}))
    assert named.alert_name == "Kept"


def test_trigger_alert_point_disabled(alert_env, slot):
    alert_env.results["fetch_point_alert"] = _Alert(alert_name="Off", enable_alert=False)
    assert not run(_alert("point", reward_id="r").execute(TRIGGER, {}))
    assert "turned off" in slot["reason"] and alert_env.queued == []


def test_trigger_alert_missing_config_reason_mentions_amount(alert_env, slot):
    alert_env.results["fetch_raid_alert"] = None
    assert not run(_alert("raid", amount="5").execute(TRIGGER, {}))
    assert slot["reason"] == "no matching alert is set up for 5"
    alert_env.results["fetch_follow_alert"] = None
    assert not run(_alert("follow").execute(TRIGGER, {}))
    assert slot["reason"] == "no matching alert is set up"


def test_trigger_alert_lookup_unknown_type_returns_none():
    a = _alert("bit")
    assert a._lookup("other", 1, {}) is None


def test_trigger_alert_exception_returns_false(alert_env):
    def boom(alert):
        raise RuntimeError("queue down")

    import modules.alert_processor as ap

    alert_env.mp.setattr(ap, "enqueue_alert", boom)
    assert not run(_alert("bit", amount="1").execute(TRIGGER, {}))


# --------------------------------------------------------------------------- chat / banner / announcement / discord


def test_send_chat_message(monkeypatch):
    sent = []
    result = {"ok": True}
    fake_module(monkeypatch, "chatbot",
                dispatch_chatbot_response=lambda msg, targets: sent.append((msg, targets)) or result["ok"])
    a = ca.create_action(ActionType.SEND_CHAT_MESSAGE, "a", "n", message="Hi {username}",
                         reply_targets=["twitch", "youtube"])
    assert a.reply_targets == ["twitch", "youtube"]
    assert run(a.execute(TRIGGER, {"username": "Bob"}))
    assert sent == [("Hi Bob", ["twitch", "youtube"])]
    result["ok"] = False
    assert not run(a.execute(TRIGGER, {}))


def test_send_chat_message_error_and_validate(monkeypatch):
    def boom(*a):
        raise RuntimeError("x")

    fake_module(monkeypatch, "chatbot", dispatch_chatbot_response=boom)
    a = ca.create_action(ActionType.SEND_CHAT_MESSAGE, "a", "n", message="  ")
    assert not run(a.execute(TRIGGER, {}))
    assert not a.validate_parameters()
    a.message = "x"
    assert a.validate_parameters()
    a.reply_targets = []
    assert not a.validate_parameters()


def test_send_chat_message_bad_targets_fall_back(monkeypatch):
    import modules.chatbot_core as cc

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(cc, "_normalize_reply_targets", boom)
    a = ca.create_action(ActionType.SEND_CHAT_MESSAGE, "a", "n", message="x", reply_targets=["youtube"])
    assert a.reply_targets == ["twitch"]


def test_display_banner(monkeypatch, slot):
    shown = []
    result = {"ok": True}
    fake_module(monkeypatch, "chat_banner",
                emit_chat_banner=lambda m, **kw: shown.append((m, kw)) or result["ok"])
    a = ca.create_action(ActionType.DISPLAY_BANNER, "a", "n", message="Yo {username}",
                         duration="4", permanent=1)
    assert a.duration == 4.0 and a.permanent is True
    assert run(a.execute(TRIGGER, {"username": "Bob"}))
    assert shown == [("Yo Bob", {"duration": 4.0, "permanent": True, "source": "connector"})]
    result["ok"] = False
    assert not run(a.execute(TRIGGER, {}))
    assert "banner" in slot["reason"]
    bad = ca.create_action(ActionType.DISPLAY_BANNER, "a", "n", duration="soon")
    assert bad.duration == 10.0 and not bad.validate_parameters()
    assert a.validate_parameters()


def test_display_banner_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    fake_module(monkeypatch, "chat_banner", emit_chat_banner=boom)
    assert not run(ca.create_action(ActionType.DISPLAY_BANNER, "a", "n", message="m").execute(TRIGGER, {}))


def test_send_announcement(monkeypatch, slot):
    sent = []
    result = {"ok": True}
    fake_module(monkeypatch, "chatbot",
                send_chatbot_announcement=lambda m, c: sent.append((m, c)) or result["ok"])
    a = ca.create_action(ActionType.SEND_ANNOUNCEMENT, "a", "n", message="Hey {username}", color=" Blue ")
    assert run(a.execute(TRIGGER, {"username": "Bob"}))
    assert sent[-1] == ("Hey Bob", "blue")
    a.color = "rainbow"
    assert run(a.execute(TRIGGER, {}))
    assert sent[-1][1] == "primary"
    a.color = None
    assert run(a.execute(TRIGGER, {}))
    assert sent[-1][1] == "primary"
    result["ok"] = False
    assert not run(a.execute(TRIGGER, {}))
    assert "announcement" in slot["reason"]


def test_send_announcement_error_and_validate(monkeypatch):
    def boom(*a):
        raise RuntimeError("x")

    fake_module(monkeypatch, "chatbot", send_chatbot_announcement=boom)
    a = ca.create_action(ActionType.SEND_ANNOUNCEMENT, "a", "n", message="m")
    assert not run(a.execute(TRIGGER, {}))
    assert a.validate_parameters()
    a.color = "rainbow"
    assert not a.validate_parameters()
    a.message = " "
    assert not a.validate_parameters()


def test_send_discord_message(monkeypatch, slot):
    sent = []
    result = {"ok": True}
    fake_module(monkeypatch, "discord_service",
                send_to_channels=lambda m, ch: sent.append((m, ch)) or result["ok"])
    channels = [{"guild_id": "g", "channel_id": "c"}]
    a = ca.create_action(ActionType.SEND_DISCORD_MESSAGE, "a", "n", message="Hi {username}",
                         channels=channels)
    assert run(a.execute(TRIGGER, {"username": "Bob"}))
    assert sent == [("Hi Bob", channels)]
    result["ok"] = False
    assert not run(a.execute(TRIGGER, {}))
    assert "Discord" in slot["reason"]


def test_send_discord_message_channels_normalized_and_validate(monkeypatch):
    assert ca.create_action(ActionType.SEND_DISCORD_MESSAGE, "a", "n", channels=None).channels == []
    assert ca.create_action(ActionType.SEND_DISCORD_MESSAGE, "a", "n", channels="c").channels == []
    a = ca.create_action(ActionType.SEND_DISCORD_MESSAGE, "a", "n", message="")
    assert not a.validate_parameters()
    a.message = "m"
    assert not a.validate_parameters()
    a.channels = [{"channel_id": "c"}]
    assert a.validate_parameters()

    def boom(*a):
        raise RuntimeError("x")

    fake_module(monkeypatch, "discord_service", send_to_channels=boom)
    assert not run(a.execute(TRIGGER, {}))


# --------------------------------------------------------------------------- API call


class _Resp:
    def __init__(self, status):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _aiohttp(monkeypatch, status=200, seen=None, raise_exc=None):
    seen = seen if seen is not None else {}

    class Session:
        async def __aenter__(self):
            if raise_exc:
                raise raise_exc
            return self

        async def __aexit__(self, *a):
            return False

        def request(self, **kw):
            seen.update(kw)
            return _Resp(status)

    mod = types.ModuleType("aiohttp")
    mod.ClientSession = Session
    monkeypatch.setitem(sys.modules, "aiohttp", mod)
    return seen


def test_api_call_post_init_and_validate():
    a = ca.create_action(ActionType.API_CALL, "a", "n", method=" post ")
    assert a.method == "POST" and a.headers == {} and a.body == {}
    assert ca.create_action(ActionType.API_CALL, "a", "n", method=None).method == "GET"
    assert ca.create_action(ActionType.API_CALL, "a", "n", method="  ").method == "GET"
    assert not a.validate_parameters()
    a.url = "http://x"
    assert a.validate_parameters()
    a.method = "TRACE"
    assert not a.validate_parameters()


def test_api_call_get_without_body(monkeypatch):
    seen = _aiohttp(monkeypatch)
    a = ca.create_action(ActionType.API_CALL, "a", "n", url="http://x", headers={"A": None, "B": 2})
    assert run(a.execute(TRIGGER, {}))
    assert seen["headers"] == {"A": "", "B": "2"}
    assert "json" not in seen and "data" not in seen


def test_api_call_json_and_text_bodies(monkeypatch):
    seen = _aiohttp(monkeypatch)
    a = ca.create_action(ActionType.API_CALL, "a", "n", url="http://x", method="PUT",
                         body={"who": "{username}"})
    assert run(a.execute(TRIGGER, {"username": "Bob"}))
    assert seen["json"] == {"who": "Bob"} and seen["method"] == "PUT"
    seen.clear()
    a.body = "plain {username}"
    assert run(a.execute(TRIGGER, {"username": "Bob"}))
    assert seen["data"] == b"plain Bob" and "json" not in seen


def test_api_call_non_object_headers_ignored(monkeypatch):
    seen = _aiohttp(monkeypatch)
    a = ca.create_action(ActionType.API_CALL, "a", "n", url="http://x", headers="[1]", body='""')
    assert run(a.execute(TRIGGER, {}))
    assert seen["headers"] == {} and "data" not in seen


def test_api_call_error_status_reports(monkeypatch, slot):
    _aiohttp(monkeypatch, status=500)
    a = ca.create_action(ActionType.API_CALL, "a", "n", url="http://x/{username}")
    assert not run(a.execute(TRIGGER, {"username": "Bob"}))
    assert slot["reason"] == "the server answered with error 500"
    assert "http://x/Bob" in slot["details"]


def test_api_call_exception_reports(monkeypatch, slot):
    _aiohttp(monkeypatch, raise_exc=OSError("down"))
    a = ca.create_action(ActionType.API_CALL, "a", "n", url="http://x")
    assert not run(a.execute(TRIGGER, {}))
    assert slot["reason"] == "the request couldn't be sent" and "OSError: down" in slot["details"]


# --------------------------------------------------------------------------- write file


def test_resolve_write_file_path(monkeypatch, tmp_path):
    import modules.path_utils as pu

    monkeypatch.setattr(pu, "get_data_path", lambda rel: str(tmp_path / rel))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))  # expanduser on Windows
    assert ca.resolve_write_file_path(" a/../b.txt ") == str(tmp_path / "b.txt")
    assert ca.resolve_write_file_path("~/x.txt") == str(tmp_path / "home" / "x.txt")
    assert ca.resolve_write_file_path(None) == str(tmp_path)


def test_write_file_failure_reports(monkeypatch, tmp_path, slot):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    a = ca.create_action(ActionType.WRITE_FILE, "a", "n", file_path=str(blocker / "sub" / "x.txt"))
    assert not run(a.execute(TRIGGER, {}))
    assert slot["reason"] == "the file couldn't be written"


def test_write_file_validate_and_no_directory(monkeypatch, tmp_path):
    a = ca.create_action(ActionType.WRITE_FILE, "a", "n")
    assert not a.validate_parameters()
    a.file_path = "x"
    assert a.validate_parameters()
    # A path whose dirname is empty (no makedirs call)
    monkeypatch.setattr(ca, "resolve_write_file_path", lambda p: "plain.txt")
    monkeypatch.chdir(tmp_path)
    made = []
    monkeypatch.setattr(ca.os, "makedirs", lambda *a, **k: made.append(a))
    a.content = "c"
    assert run(a.execute(TRIGGER, {}))
    assert (tmp_path / "plain.txt").read_text() == "c" and made == []


# --------------------------------------------------------------------------- execute command


def test_execute_command_post_init_non_positive():
    assert ca.create_action(ActionType.EXECUTE_COMMAND, "e", "n", timeout_seconds=0).timeout_seconds == 30
    assert ca.create_action(ActionType.EXECUTE_COMMAND, "e", "n", timeout_seconds=-1).timeout_seconds == 30
    a = ca.create_action(ActionType.EXECUTE_COMMAND, "e", "n")
    assert not a.validate_parameters()
    a.command = "x"
    assert a.validate_parameters()


def _fake_run(monkeypatch, returncode=0, stderr="", exc=None):
    seen = {}

    def fake(cmd, **kw):
        seen["cmd"] = cmd
        seen.update(kw)
        if exc is not None:
            raise exc
        return SimpleNamespace(returncode=returncode, stderr=stderr)

    monkeypatch.setattr(ca.subprocess, "run", fake)
    return seen


def test_execute_command_success_uses_working_dir(monkeypatch):
    seen = _fake_run(monkeypatch)
    a = ca.create_action(ActionType.EXECUTE_COMMAND, "e", "n", command="echo {username}",
                         working_directory="/tmp/{username}", timeout_seconds=5)
    assert run(a.execute(TRIGGER, {"username": "Bob"}))
    assert seen["cwd"] == "/tmp/Bob" and seen["shell"] is True and seen["timeout"] == 5
    assert "Bob" in seen["cmd"] and seen["capture_output"] and seen["text"]


def test_execute_command_no_working_dir(monkeypatch):
    seen = _fake_run(monkeypatch)
    a = ca.create_action(ActionType.EXECUTE_COMMAND, "e", "n", command="true")
    assert run(a.execute(TRIGGER, {}))
    assert seen["cwd"] is None


def test_execute_command_nonzero_exit(monkeypatch, slot):
    _fake_run(monkeypatch, returncode=3, stderr="bad thing")
    a = ca.create_action(ActionType.EXECUTE_COMMAND, "e", "n", command="false")
    assert not run(a.execute(TRIGGER, {}))
    assert slot["reason"] == "the command ended with exit code 3" and "bad thing" in slot["details"]
    _fake_run(monkeypatch, returncode=1, stderr=None)
    assert not run(a.execute(TRIGGER, {}))


def test_execute_command_timeout_and_error(monkeypatch, slot):
    _fake_run(monkeypatch, exc=subprocess.TimeoutExpired("x", 2))
    a = ca.create_action(ActionType.EXECUTE_COMMAND, "e", "n", command="sleep", timeout_seconds=2)
    assert not run(a.execute(TRIGGER, {}))
    assert slot["reason"] == "the command didn't finish within 2 seconds"
    _fake_run(monkeypatch, exc=OSError("nope"))
    assert not run(a.execute(TRIGGER, {}))


# --------------------------------------------------------------------------- key press (fake pynput)


class _KeyCode:
    def __init__(self, char):
        self.char = char

    @classmethod
    def from_char(cls, char):
        if char == "bad":
            raise ValueError("bad key")
        if char == "worse":
            raise TypeError("worse key")
        return cls(char)

    def __eq__(self, other):
        return isinstance(other, _KeyCode) and other.char == self.char

    def __hash__(self):
        return hash(self.char)

    def __repr__(self):
        return f"KC({self.char})"


class _Recorder:
    instances = []

    def __init__(self):
        self.events = []
        _Recorder.instances.append(self)

    def press(self, k):
        self.events.append(("press", k))

    def release(self, k):
        self.events.append(("release", k))

    def click(self, b):
        self.events.append(("click", b))


@pytest.fixture
def pynput(monkeypatch):
    _Recorder.instances = []
    key_names = ["ctrl", "alt", "shift", "cmd", "tab", "enter", "space", "esc", "backspace", "delete",
                 "up", "down", "left", "right", "home", "end", "page_up", "page_down"] + [
        f"f{i}" for i in range(1, 13)]
    Key = SimpleNamespace(**{n: f"Key.{n}" for n in key_names})
    Button = SimpleNamespace(left="Button.left", right="Button.right", middle="Button.middle")

    class KbController(_Recorder):
        pass

    class MouseController(_Recorder):
        pass

    pkg = types.ModuleType("pynput")
    kb = types.ModuleType("pynput.keyboard")
    kb.Controller, kb.Key, kb.KeyCode = KbController, Key, _KeyCode
    ms = types.ModuleType("pynput.mouse")
    ms.Controller, ms.Button = MouseController, Button
    pkg.keyboard, pkg.mouse = kb, ms
    monkeypatch.setitem(sys.modules, "pynput", pkg)
    monkeypatch.setitem(sys.modules, "pynput.keyboard", kb)
    monkeypatch.setitem(sys.modules, "pynput.mouse", ms)
    return SimpleNamespace(Key=Key, Button=Button, KbController=KbController,
                           MouseController=MouseController)


def _kp(**kw):
    return ca.create_action(ActionType.KEY_PRESS, "k", "n", **kw)


def _controllers():
    kb = next(r for r in _Recorder.instances if type(r).__name__ == "KbController")
    ms = next(r for r in _Recorder.instances if type(r).__name__ == "MouseController")
    return kb, ms


def test_key_press_missing_pynput_reports(monkeypatch, slot):
    monkeypatch.setitem(sys.modules, "pynput", None)
    assert not run(_kp(key_sequence="a").execute(TRIGGER, {}))
    assert slot["reason"] == "key presses aren't available on this system"


def test_key_press_press_combo(pynput, sleeps):
    assert run(_kp(key_sequence="ctrl+{letter}").execute(TRIGGER, {"letter": "c"}))
    kb, _ = _controllers()
    assert kb.events == [("press", "Key.ctrl"), ("press", _KeyCode("c")),
                         ("release", _KeyCode("c")), ("release", "Key.ctrl")]


def test_key_press_release_only(pynput, sleeps):
    assert run(_kp(key_sequence="shift+a", action_mode="release").execute(TRIGGER, {}))
    kb, _ = _controllers()
    assert kb.events == [("release", _KeyCode("a")), ("release", "Key.shift")]


def test_key_press_repeat_sleeps_between(pynput, sleeps):
    assert run(_kp(key_sequence="x", action_mode="repeat", repeat_count=3,
                   repeat_interval=0.2).execute(TRIGGER, {}))
    kb, _ = _controllers()
    assert kb.events.count(("press", _KeyCode("x"))) == 3
    assert sleeps == [0.2, 0.2]


def test_key_press_hold(pynput, sleeps):
    assert run(_kp(key_sequence="alt+tab", action_mode="hold", hold_duration=0.7).execute(TRIGGER, {}))
    kb, _ = _controllers()
    assert kb.events == [("press", "Key.alt"), ("press", "Key.tab"),
                         ("release", "Key.tab"), ("release", "Key.alt")]
    assert sleeps == [0.7]


def test_parse_key_sequence_mappings(pynput):
    a = _kp()
    keys = a._parse_key_sequence("Control + win + Arrow-Up + arrowdown + arrow_left + RETURN + f12 + bad + worse + ab")
    assert keys == ["Key.ctrl", "Key.cmd", "Key.up", "Key.down", "Key.left", "Key.enter", "Key.f12",
                    _KeyCode("ab")]
    assert a._parse_key_sequence("super+escape+page_down") == ["Key.cmd", "Key.esc", "Key.page_down"]


def test_keyboard_action_error_returns_false(pynput):
    a = _kp(key_sequence="a")

    class Broken:
        def press(self, k):
            raise RuntimeError("x")

    assert run(a._execute_keyboard_action(Broken(), {})) is False


@pytest.mark.parametrize(
    "seq,button", [("left_click", "Button.left"), ("Right", "Button.right"), ("middle_click", "Button.middle")]
)
def test_mouse_click(pynput, sleeps, seq, button):
    assert run(_kp(input_type="mouse", key_sequence=seq).execute(TRIGGER, {}))
    _, ms = _controllers()
    assert ms.events == [("click", button)]


def test_mouse_modes(pynput, sleeps):
    assert run(_kp(input_type="mouse", key_sequence="left", action_mode="repeat", repeat_count=2,
                   repeat_interval=0.3).execute(TRIGGER, {}))
    _, ms = _controllers()
    assert ms.events == [("click", "Button.left")] * 2 and sleeps == [0.3]
    _Recorder.instances = []
    sleeps.clear()
    assert run(_kp(input_type="mouse", key_sequence="left", action_mode="hold",
                   hold_duration=0.4).execute(TRIGGER, {}))
    _, ms = _controllers()
    assert ms.events == [("press", "Button.left"), ("release", "Button.left")] and sleeps == [0.4]
    _Recorder.instances = []
    assert run(_kp(input_type="mouse", key_sequence="left", action_mode="release").execute(TRIGGER, {}))
    _, ms = _controllers()
    assert ms.events == [("release", "Button.left")]
    _Recorder.instances = []
    assert run(_kp(input_type="mouse", key_sequence="left", action_mode="other").execute(TRIGGER, {}))
    _, ms = _controllers()
    assert ms.events == []


def test_mouse_unknown_button_and_error(pynput):
    assert not run(_kp(input_type="mouse", key_sequence="thumb").execute(TRIGGER, {}))

    class Broken:
        def click(self, b):
            raise RuntimeError("x")

    assert run(_kp(input_type="mouse", key_sequence="left")._execute_mouse_action(Broken())) is False


def test_macro_steps(pynput, sleeps):
    import json

    steps = [
        {"type": "key", "target": "{k}", "delay": 0},
        {"type": "key", "action": "release", "target": "b"},
        {"type": "mouse", "target": "left_click", "delay": 0},
        {"type": "mouse", "target": "right", "delay": 0},
        {"type": "mouse", "target": "wheel", "delay": 0},
        {"type": "delay", "target": "1.5", "delay": 0},
        {"type": "unknown", "target": "", "delay": 0},
    ]
    assert run(_kp(input_type="macro", macro_sequence=json.dumps(steps)).execute(TRIGGER, {"k": "a"}))
    kb, ms = _controllers()
    assert kb.events == [("press", _KeyCode("a")), ("release", _KeyCode("a")), ("release", _KeyCode("b"))]
    assert ms.events == [("click", "Button.left"), ("click", "Button.right")]
    assert sleeps == [0.1, 1.5]


def test_macro_empty_and_invalid(pynput, sleeps):
    assert run(_kp(input_type="macro", macro_sequence="").execute(TRIGGER, {}))
    assert not run(_kp(input_type="macro", macro_sequence="{nope").execute(TRIGGER, {}))


def test_key_press_execute_unexpected_error(pynput, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ctx")

    monkeypatch.setattr(ca, "build_connector_placeholder_context", boom)
    assert not run(_kp(key_sequence="a").execute(TRIGGER, {}))


def test_key_press_replace_placeholders_none():
    assert _kp()._replace_placeholders(None, {}) == ""
