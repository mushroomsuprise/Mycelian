# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Connector actions at runtime: greetings, JSON fields, write file, commands, alerts."""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

from modules import alertutils, connector_actions as ca
from modules.connector_core import ActionType
from modules.connector_manager import ConnectorManager


def run(coro):
    return asyncio.run(coro)


TRIGGER = {"trigger_id": "t1", "triggered_at": 1.0, "connector_name": "C", "trigger_count": 1}


# ---- greetings -----------------------------------------------------------


def test_greeting_fields_no_longer_shadow_action_enabled():
    add = ca.create_action(ActionType.ADD_GREETING, "a", "n", greeting_enabled=False)
    assert add.enabled is True and add.greeting_enabled is False
    upd = ca.create_action(ActionType.UPDATE_GREETING, "u", "n", greeting_id="g")
    assert upd.enabled is True and upd.greeting_enabled is None


def test_legacy_greeting_data_is_migrated():
    manager = ConnectorManager()
    old_add = {"action_id": "a", "action_type": "add_greeting", "name": "n", "enabled": False,
               "user_id": "1", "username": "u", "greeting_text": "hi"}
    action = manager._deserialize_action(old_add)
    assert action.enabled is True and action.greeting_enabled is False
    old_upd = {"action_id": "b", "action_type": "update_greeting", "name": "n", "enabled": None,
               "greeting_id": "g", "greeting_text": "x"}
    action = manager._deserialize_action(old_upd)
    assert action.enabled is True and action.greeting_enabled is None
    # New data with both keys is kept as is
    new = dict(old_add, enabled=False, greeting_enabled=True)
    action = manager._deserialize_action(new)
    assert action.enabled is False and action.greeting_enabled is True
    # Round trip stores greeting_enabled
    assert manager._serialize_action(action)["greeting_enabled"] is True


def test_update_greeting_passes_sentinel(monkeypatch):
    calls = {}

    class FakeManager:
        def update_greeting(self, **kw):
            calls.update(kw)
            return True

    fake = types.ModuleType("modules.chatbot_manager")
    fake.get_manager = lambda: FakeManager()
    monkeypatch.setitem(sys.modules, "modules.chatbot_manager", fake)
    import modules

    monkeypatch.setattr(modules, "chatbot_manager", fake, raising=False)
    action = ca.create_action(ActionType.UPDATE_GREETING, "u", "n", greeting_id="{user_id}",
                              greeting_text="Hi {username}")
    assert run(action.execute(TRIGGER, {"user_id": "42", "username": "Bob"}))
    assert calls == {"greeting_id": "42", "greeting_text": "Hi Bob", "enabled": None}


# ---- JSON fields ---------------------------------------------------------


def test_resolve_json_field_variants(caplog):
    ctx = {"amount": 5, "username": "Bob"}
    assert ca.resolve_json_field({"a": "{username}"}, ctx, "x") == {"a": "Bob"}
    assert ca.resolve_json_field('{"amount": {amount}}', ctx, "x") == {"amount": 5}
    assert ca.resolve_json_field("", ctx, "x") == {}
    assert ca.resolve_json_field(None, ctx, "x") == {}
    assert ca.resolve_json_field("not json {username}", ctx, "x") == {}
    assert "not valid JSON" in caplog.text
    assert ca.resolve_json_field("plain {username}", ctx, "x", allow_text=True) == "plain Bob"


def test_websocket_emit_accepts_json_text(monkeypatch):
    emitted = []
    engine = SimpleNamespace(socketio=object(), safe_emit=lambda n, d: emitted.append((n, d)))
    from modules import web_engine

    monkeypatch.setattr(web_engine, "web_engine_instance", engine)
    action = ca.create_action(ActionType.WEBSOCKET_EMIT, "w", "n", event_name="ev",
                              event_data='{"amount": {amount}, "who": "{username}"}')
    assert run(action.execute(TRIGGER, {"amount": 7, "username": "Bob"}))
    name, data = emitted[0]
    assert name == "ev" and data["amount"] == 7 and data["who"] == "Bob"
    assert data["trigger_id"] == "t1"


def test_api_call_headers_and_body(monkeypatch):
    seen = {}

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def request(self, **kw):
            seen.update(kw)
            return FakeResponse()

    fake_aiohttp = types.ModuleType("aiohttp")
    fake_aiohttp.ClientSession = FakeSession
    monkeypatch.setitem(sys.modules, "aiohttp", fake_aiohttp)

    action = ca.create_action(ActionType.API_CALL, "a", "n", url="http://x/{username}",
                              method="post", headers='{"X-Amount": {amount}}',
                              body='{"amount": {amount}}')
    assert run(action.execute(TRIGGER, {"amount": 3, "username": "Bob"}))
    assert seen["method"] == "POST" and seen["url"] == "http://x/Bob"
    assert seen["headers"] == {"X-Amount": "3"} and seen["json"] == {"amount": 3}

    seen.clear()
    text = ca.create_action(ActionType.API_CALL, "a", "n", url="http://x", method="POST",
                            body="name={username}")
    assert run(text.execute(TRIGGER, {"username": "Bob"}))
    assert seen["data"] == b"name=Bob" and "json" not in seen


# ---- write file ----------------------------------------------------------


def test_write_file_relative_and_bare_names(monkeypatch, tmp_path):
    import modules.path_utils as pu

    monkeypatch.setattr(pu, "get_data_path", lambda rel: str(tmp_path / rel))
    bare = ca.create_action(ActionType.WRITE_FILE, "w", "n", file_path="out_{username}.txt",
                            content="Hi {username}")
    assert run(bare.execute(TRIGGER, {"username": "Bob"}))
    assert (tmp_path / "out_Bob.txt").read_text() == "Hi Bob"
    nested = ca.create_action(ActionType.WRITE_FILE, "w", "n", file_path="sub/dir/x.txt",
                              content="1", append=True)
    assert run(nested.execute(TRIGGER, {}))
    assert run(nested.execute(TRIGGER, {}))
    assert (tmp_path / "sub/dir/x.txt").read_text() == "11"
    absolute = tmp_path / "abs.txt"
    action = ca.create_action(ActionType.WRITE_FILE, "w", "n", file_path=str(absolute), content="a")
    assert run(action.execute(TRIGGER, {}))
    assert absolute.read_text() == "a"


# ---- execute command -----------------------------------------------------


def test_execute_command_timeout_key_migration():
    legacy = ca.create_action(ActionType.EXECUTE_COMMAND, "e", "n", command="x", timeout="12")
    assert legacy.timeout_seconds == 12
    frac = ca.create_action(ActionType.EXECUTE_COMMAND, "e", "n", command="x", timeout_seconds="2.5")
    assert frac.timeout_seconds == 2.5
    bad = ca.create_action(ActionType.EXECUTE_COMMAND, "e", "n", command="x", timeout_seconds="nope")
    assert bad.timeout_seconds == 30


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell")
def test_execute_command_quotes_variables(tmp_path):
    marker = tmp_path / "pwned"
    out = tmp_path / "out.txt"
    action = ca.create_action(
        ActionType.EXECUTE_COMMAND,
        "e",
        "n",
        command=f"printf %s {{message}} > {out}",
        working_directory=str(tmp_path),
    )
    message = f"hi; touch {marker} $(touch {marker})"
    assert run(action.execute(TRIGGER, {"message": message}))
    assert out.read_text() == message
    assert not marker.exists()


# ---- trigger alert -------------------------------------------------------


class _Queue:
    def __init__(self):
        self.items = []

    def enqueue_alert(self, alert):
        self.items.append(alert)


@pytest.fixture
def alert_env(monkeypatch):
    queue = _Queue()
    import modules.alert_processor as ap

    monkeypatch.setattr(ap, "enqueue_alert", queue.enqueue_alert)
    calls = []

    def fake(name, result=True):
        def inner(*args):
            calls.append((name, args))
            if result is None:
                return None
            obj = alertutils.AlertObj()
            obj.alert_name = f"{name}-config"
            obj.enable_alert = True
            return obj

        return inner

    for fn in ("fetch_cheer_alert", "fetch_sub_alert", "fetch_resub_alert", "fetch_giftsub_alert",
               "fetch_streak_alert", "fetch_raid_alert", "fetch_follow_alert",
               "fetch_donation_alert", "fetch_point_alert"):
        monkeypatch.setattr(alertutils, fn, fake(fn))
    import modules.donation_settings as ds

    monkeypatch.setattr(ds, "get_settings", lambda: SimpleNamespace(alert_currency="USD"))
    return SimpleNamespace(queue=queue, calls=calls, fake=fake, monkeypatch=monkeypatch)


def _alert_action(alert_type, **data):
    return ca.create_action(ActionType.TRIGGER_ALERT, "a", "n", alert_type=alert_type,
                            alert_data=data)


def test_trigger_alert_plays_configured_alerts(alert_env):
    event = {"username": "Bob", "message": "hey", "amount": 250}
    assert run(_alert_action("bit", amount="{amount}", username="{username}",
                             message="{message}").execute(TRIGGER, event))
    alert = alert_env.queue.items[-1]
    assert alert_env.calls[-1] == ("fetch_cheer_alert", (250,))
    assert (alert.alert_type, alert.username, alert.message, alert.amt_cheered) == (
        "bit", "Bob", "hey", 250
    )
    assert alert.alert_name == "fetch_cheer_alert-config"

    cases = [
        ("resub", "fetch_resub_alert", "resub_month", 12),
        ("giftsub", "fetch_giftsub_alert", "gift_qty", 5),
        ("raid", "fetch_raid_alert", "raider_count", 42),
        ("streak", "fetch_streak_alert", "streak_count", 3),
        ("sub", "fetch_sub_alert", "resub_month", 1),
    ]
    for alert_type, fn, attr, amount in cases:
        assert run(_alert_action(alert_type, amount=str(amount)).execute(TRIGGER, event))
        assert alert_env.calls[-1] == (fn, (amount,))
        assert getattr(alert_env.queue.items[-1], attr) == amount
        # username/message fall back to the event
        assert alert_env.queue.items[-1].username == "Bob"

    assert run(_alert_action("follow", username="X").execute(TRIGGER, event))
    assert alert_env.calls[-1] == ("fetch_follow_alert", ())

    assert run(_alert_action("donation", amount="4.6", currency="eur").execute(TRIGGER, event))
    alert = alert_env.queue.items[-1]
    assert alert_env.calls[-1] == ("fetch_donation_alert", (5,))
    assert alert.donation_amount == 4.6 and alert.currency == "EUR"

    assert run(_alert_action("point", reward_id="rid", reward_name="Hydrate").execute(TRIGGER, event))
    assert alert_env.calls[-1] == ("fetch_point_alert", ("rid",))
    assert alert_env.queue.items[-1].alert_name == "Hydrate"


def test_trigger_alert_without_config_does_not_play(alert_env, caplog):
    alert_env.monkeypatch.setattr(alertutils, "fetch_raid_alert", alert_env.fake("raid", None))
    assert not run(_alert_action("raid", amount="3").execute(TRIGGER, {}))
    assert not alert_env.queue.items
    assert "no raid alert is configured" in caplog.text


def test_trigger_alert_legacy_shapes(alert_env):
    action = ca.create_action(ActionType.TRIGGER_ALERT, "a", "n", alert_type="bits",
                              amount="{amount}", message="m")
    assert action.alert_type == "bit"
    assert action.alert_data == {"amount": "{amount}", "message": "m"}
    assert run(action.execute(TRIGGER, {"amount": 10}))
    assert alert_env.queue.items[-1].amt_cheered == 10
    sub = ca.create_action(ActionType.TRIGGER_ALERT, "a", "n", alert_type="subscription")
    assert sub.alert_type == "sub"
    host = ca.create_action(ActionType.TRIGGER_ALERT, "a", "n", alert_type="host")
    assert host.alert_type == "raid"
    custom = ca.create_action(ActionType.TRIGGER_ALERT, "a", "n", alert_type="custom")
    assert not run(custom.execute(TRIGGER, {}))
