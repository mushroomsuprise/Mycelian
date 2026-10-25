# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Chatbot and connector failures reach the user once, with a reason and a fix."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict

import pytest

from modules import chatbot
from modules import connector_core as cc
from modules.connector_actions import create_action
from modules.connector_core import ActionType as A
from modules.connector_core import BaseAction, Connector
from modules.connector_core import TriggerType as T
from modules.connector_triggers import create_trigger
from modules.uiwindows import connector_action_schemas as schemas
from modules.uiwindows import connector_editor as ce


@pytest.fixture
def shown(monkeypatch):
    """Every notify() call as (message, kwargs)."""
    calls = []
    monkeypatch.setattr("modules.notification_engine.notify", lambda message, **kw: calls.append((message, kw)) or "id")
    return calls


# -- chatbot -------------------------------------------------------------------


def test_twitch_drop_reason_reads_is_sent_false():
    assert chatbot._twitch_drop_reason({"data": [{"message_id": "1", "is_sent": True}]}) is None
    assert chatbot._twitch_drop_reason({"status": 204, "data": ""}) is None
    dropped = {"data": [{"is_sent": False, "drop_reason": {"code": "msg_rejected", "message": "Held by AutoMod"}}]}
    assert chatbot._twitch_drop_reason(dropped) == "Held by AutoMod (msg_rejected)"
    assert chatbot._twitch_drop_reason({"data": [{"is_sent": False}]}) == "no reason given"


class _FakeBot:
    last_send_problem = None


def test_dropped_message_is_reported_once_with_a_hint(monkeypatch, shown):
    bot = _FakeBot()
    monkeypatch.setattr(chatbot, "chatbot_api", bot)

    def fake_send(part, reply_to=None):
        bot.last_send_problem = ("dropped", "Held by AutoMod (msg_rejected)")
        return False

    monkeypatch.setattr(chatbot, "_send_one_chatbot_message_blocking", fake_send)
    assert chatbot._send_chatbot_message_blocking("hello chat") is False
    assert len(shown) == 1
    _message, kw = shown[0]
    assert kw["source"] == "chatbot" and kw["type"] == "warning" and kw["hint"]
    assert kw["dedupe_key"].startswith("chatbot:dropped:") and "AutoMod" in kw["details"]


def test_signed_out_bot_points_to_twitch_settings(monkeypatch, shown):
    bot = _FakeBot()
    monkeypatch.setattr(chatbot, "chatbot_api", bot)

    def fake_send(part, reply_to=None):
        bot.last_send_problem = ("auth", "Chatbot account has no working sign-in")
        return False

    monkeypatch.setattr(chatbot, "_send_one_chatbot_message_blocking", fake_send)
    chatbot._send_chatbot_message_blocking("hi")
    _message, kw = shown[0]
    assert kw["dedupe_key"] == "chatbot:send_failed:twitch"
    assert kw["actions"][0]["settings_subtab"] == "Twitch" and "Connect Chatbot" in kw["hint"]


# -- connectors ----------------------------------------------------------------


@dataclass
class _FailingAction(BaseAction):
    def __post_init__(self):
        self.action_type = A.WRITE_FILE

    async def execute(self, trigger_data: Dict[str, Any], event_data: Dict[str, Any]) -> bool:
        cc.report_action_problem("the file couldn't be written", "Check the folder.", "PermissionError: denied")
        return False


@dataclass
class _SkippingAction(BaseAction):
    def __post_init__(self):
        self.action_type = A.GAME_HOOK

    async def execute(self, trigger_data: Dict[str, Any], event_data: Dict[str, Any]) -> bool:
        cc.mark_action_skipped()
        return False


def _connector(*actions):
    trigger = create_trigger(T.WEBHOOK, "t1", "t", secret_key="s")
    trigger.trigger = lambda event: True
    connector = Connector("c1", "Save clip", trigger=trigger, actions=list(actions))
    connector._game_hook_prerequisites_unmet = lambda: False
    return connector


def test_failed_action_notifies_once_per_connector_and_action(shown):
    connector = _connector(_FailingAction("a1", A.WRITE_FILE, "Write file"))
    assert asyncio.run(connector.process_event({"event_type": "webhook"})) is False
    assert len(shown) == 1
    message, kw = shown[0]
    assert "Action 1 (Write file)" in message and "couldn't be written" in message
    assert kw["source"] == "connectors" and kw["hint"] == "Check the folder."
    assert kw["dedupe_key"] == "connector:c1:1" and kw["details"] == "PermissionError: denied"
    assert kw["actions"][0]["main_tab"] == "Connectors"


def test_test_runs_collect_failures_without_popups(shown):
    connector = _connector(_FailingAction("a1", A.WRITE_FILE, "Write file"), _SkippingAction("a2", A.GAME_HOOK, "Hook"))
    asyncio.run(connector.process_event({"event_type": "webhook"}, report_failures=False))
    assert shown == []
    assert [f["index"] for f in connector.last_action_failures] == [1]  # the skipped action isn't a failure


# -- connector editor ----------------------------------------------------------


def test_unreadable_action_is_not_reset_on_save(monkeypatch):
    original = Connector(
        "c1", "Say hi", trigger=create_trigger(T.TWITCH_FOLLOW, "t1", "t"),
        actions=[create_action(A.SEND_CHAT_MESSAGE, "a1", "x", message="Thanks {username}", reply_targets=["twitch"])],
    )
    real = schemas.action_to_config

    def broken(action):
        if action.action_id == "a1":
            raise ValueError("bad stored data")
        return real(action)

    monkeypatch.setattr(schemas, "action_to_config", broken)
    state = ce.state_from_connector(original)
    assert state.actions[0]["unreadable"] is True
    state.actions[0]["delay_seconds"] = 3
    kept = ce.build_connector(state, original).actions[0]
    assert kept.message == "Thanks {username}" and kept.delay_seconds == 3

    state.actions[0]["config"]["message"] = "Changed on purpose"
    rebuilt = ce.build_connector(state, original).actions[0]
    assert rebuilt.message == "Changed on purpose"
