# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Service and infrastructure notices: the right one fires once, with the right source."""

from __future__ import annotations

import asyncio

import pytest

from modules import notification_engine as ne


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def _fake_notify(message="", **kwargs):
        calls.append({"message": message, **kwargs})
        return "id"

    monkeypatch.setattr(ne, "notify", _fake_notify)
    return calls


@pytest.fixture
def hooks(monkeypatch):
    from modules import game_hooks
    from modules.game_hooks import base

    state = {"os": "windows", "enabled": set()}
    monkeypatch.setattr(base, "runtime_os_key", lambda: state["os"])
    monkeypatch.setattr(game_hooks, "is_hook_enabled", lambda hid: hid in state["enabled"])
    monkeypatch.setattr(ne, "_game_hook_suggested", set())
    return state


def test_game_hook_suggestion_only_for_supported_games(captured, hooks):
    ne.maybe_suggest_game_hook_for_category("Final Fantasy VII Rebirth")
    ne.maybe_suggest_game_hook_for_category("Final Fantasy VII Remake Intergrade")
    assert captured == []

    ne.maybe_suggest_game_hook_for_category("Final Fantasy VII")
    ne.maybe_suggest_game_hook_for_category("Final Fantasy VII")
    assert len(captured) == 1
    notice = captured[0]
    assert notice["source"] == "game_hooks"
    assert [a.get("kind") for a in notice["actions"]] == ["navigate"]
    assert notice["actions"][0]["settings_subtab"] == "Game Hooks"


def test_game_hook_suggestion_skips_unsupported_os_and_enabled_hooks(captured, hooks):
    hooks["os"] = "darwin"
    ne.maybe_suggest_game_hook_for_category("Final Fantasy VII")
    assert captured == []  # the FF7 hook is Windows-only

    hooks["enabled"].add("factorio")
    ne.maybe_suggest_game_hook_for_category("Factorio")
    assert captured == []  # already on

    hooks["enabled"].clear()
    ne.maybe_suggest_game_hook_for_category("Factorio")
    assert len(captured) == 1 and "Factorio" in captured[0]["title"]


def test_obs_wrong_password_is_reported_once_per_password(captured):
    from modules import obs_service as obs

    assert obs._obs_auth_problem(Exception("authentication enabled but no password provided")) == "missing"
    assert obs._obs_auth_problem(
        Exception("failed to identify client with the server, please check connection settings")
    ) == "wrong"
    assert obs._obs_auth_problem(ConnectionRefusedError()) is None

    svc = obs.ObsServiceImpl()
    kw = {"host": "localhost", "port": 4455, "password": "nope"}
    err = Exception("failed to identify client with the server")
    svc._notify_auth_failed("wrong", kw, err)
    svc._notify_auth_failed("wrong", kw, err)
    assert len(captured) == 1
    assert captured[0]["source"] == "obs" and captured[0]["actions"][0]["settings_subtab"] == "OBS"
    assert "password" in captured[0]["title"].lower()

    svc._notify_auth_failed("wrong", {**kw, "password": "still wrong"}, err)
    assert len(captured) == 2


def test_psn_expired_notice_once_per_code_until_reconnected(captured, monkeypatch):
    from modules import psn_service

    monkeypatch.setattr(psn_service, "_npsso_expired_notified_for", None)
    psn_service.notify_psn_npsso_expired("code-a")
    psn_service.notify_psn_npsso_expired("code-a")
    assert len(captured) == 1
    assert captured[0]["actions"][0]["settings_subtab"] == "PSN" and captured[0]["source"] == "psn"

    psn_service.clear_psn_npsso_expired_notice()
    psn_service.notify_psn_npsso_expired("code-a")
    assert len(captured) == 2


def test_update_check_failure_is_recorded(monkeypatch):
    import aiohttp

    from modules import updater

    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise aiohttp.ClientConnectionError("no route to host")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(updater, "_github_release_cache", None)
    monkeypatch.setattr(updater.aiohttp, "ClientSession", _Boom)
    assert asyncio.run(updater.fetch_latest_update_info_from_github(force_refresh=True)) is None
    message, details = updater.last_update_check_error()
    assert "GitHub" in message and "no route to host" in details
    monkeypatch.setattr(updater, "_github_release_cache", None)


def test_twitch_connect_notice_names_the_reason(captured):
    from modules import twitch

    twitch.notify_twitch_connect_failed("eventsub", "TwitchAPIException: 403")
    notice = captured[-1]
    assert notice["title"] == "Twitch refused event subscriptions"
    assert notice["source"] == "twitch" and notice["details"] == "TwitchAPIException: 403"


def test_hotkey_notices_deduped_and_mac_permission(captured, monkeypatch):
    from modules import hotkey_listener as hl

    monkeypatch.setattr(hl, "_notified", set())
    hl._notify_invalid_hotkey("ctrl+nope")
    hl._notify_invalid_hotkey("ctrl+nope")
    assert len(captured) == 1 and captured[0]["source"] == "connectors"

    monkeypatch.setattr(hl, "_mac_accessibility_trusted", lambda: False)
    hl._check_mac_permission()
    hl._check_mac_permission()
    assert len(captured) == 2 and "Accessibility" in captured[1]["hint"]
