# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch.py, part 3: Twitch_API credentials, token load/save/refresh,
OAuth, user-auth verification and staging (all twitchAPI / network calls faked)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any, Dict, List  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402

from modules import api_credentials_manager, dataobjects, twitch as tw  # noqa: E402
from modules.twitch_token_auth import RefreshResult, ValidateResult  # noqa: E402


class FakeState:
    def __init__(self, initialized=True, data=None):
        self._initialized = initialized
        self.data = data
        self.saved: List[Dict[str, Any]] = []
        self.save_ok = True
        self.app_updates: List[Any] = []

    def get_twitch_data(self):
        return self.data

    def set_twitch_data(self, d):
        self.saved.append(d)

    def save_changes(self):
        return self.save_ok

    def update_app_setting(self, k, v):
        self.app_updates.append((k, v))


def _twitch_data(**kw):
    base = dict(client_id="cid", client_secret="sec", auth_token="tok", refresh_token="ref", user_id="42",
                token_expiry="")
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def state(monkeypatch):
    st = FakeState(data=_twitch_data())
    monkeypatch.setattr(dataobjects, "state_manager", st)
    monkeypatch.setattr(api_credentials_manager, "get_twitch_credentials", lambda: {})
    return st


@pytest.fixture
def api(state, monkeypatch):
    a = tw.Twitch_API()
    a._sync_tokens_to_state_manager = MagicMock()
    monkeypatch.setattr(tw, "notify_twitch_connect_failed", MagicMock())
    monkeypatch.setattr(tw, "_notify_twitch_client_credentials", MagicMock())
    return a


def run(coro):
    return asyncio.run(coro)


FUTURE = datetime.now() + timedelta(hours=3)


# --------------------------------------------------------------------------- init / credentials


def test_init_defaults():
    a = tw.Twitch_API()
    assert a.client_id == "" and a.twitch is None and a.is_connected is False
    assert tw.AuthScope.CHANNEL_MANAGE_POLLS in a.authscope
    assert a.health_check_interval == 60 and a.event_staleness_timeout == 45 * 60
    assert a._eventsub_subscriptions_ready is False


def test_apply_api_credentials_from_store(api, monkeypatch):
    monkeypatch.setattr(api_credentials_manager, "get_twitch_credentials",
                        lambda: {"client_id": "C", "client_secret": ""})
    api.client_secret = "keep"
    api._apply_api_credentials_from_store()
    assert api.client_id == "C" and api.client_secret == "keep"
    monkeypatch.setattr(api_credentials_manager, "get_twitch_credentials",
                        lambda: {"client_id": "", "client_secret": "S"})
    api.sync_client_credentials()
    assert api.client_id == "C" and api.client_secret == "S"
    monkeypatch.setattr(api_credentials_manager, "get_twitch_credentials", MagicMock(side_effect=RuntimeError("x")))
    api._apply_api_credentials_from_store()  # logged


def test_sync_tokens_from_state(api, state):
    state.data = _twitch_data(token_expiry="2030-01-01T00:00:00")
    api.sync_helix_credentials_from_state()
    assert (api.client_id, api.client_secret, api.auth_token, api.refresh_token, api.user_id) == (
        "cid", "sec", "tok", "ref", "42")
    assert api.token_expiry == datetime(2030, 1, 1)
    state.data = _twitch_data(client_id="", client_secret="", auth_token="", refresh_token="", user_id="",
                              token_expiry="garbage")
    api.sync_tokens_from_state()
    assert api.auth_token == "tok" and api.token_expiry == datetime(2030, 1, 1)


def test_sync_tokens_from_state_not_initialized_or_error(api, state, monkeypatch):
    state._initialized = False
    api.sync_tokens_from_state()
    assert api.auth_token == ""
    state._initialized = True
    state.data = None
    api.sync_tokens_from_state()
    monkeypatch.setattr(state, "get_twitch_data", MagicMock(side_effect=RuntimeError("db")))
    api.sync_tokens_from_state()
    assert api.auth_token == ""


def test_wire_refresh_callback(api, monkeypatch):
    attach = MagicMock()
    monkeypatch.setattr(tw, "attach_refresh_callback", attach)
    api._wire_twitch_refresh_callback()
    attach.assert_not_called()
    api.twitch = object()
    api._wire_twitch_refresh_callback()
    attach.assert_called_once_with(api.twitch, api._on_library_tokens_refreshed)


def test_on_library_tokens_refreshed(api, state, monkeypatch):
    monkeypatch.setattr(tw, "compute_token_expiry", AsyncMock(return_value=FUTURE))
    run(api._on_library_tokens_refreshed("A", "R"))
    assert (api.auth_token, api.refresh_token, api.token_expiry) == ("A", "R", FUTURE)
    assert state.saved[-1]["auth_token"] == "A"
    api._sync_tokens_to_state_manager.assert_called_once()


# --------------------------------------------------------------------------- load / save


def test_load_auth_data_success(api, state, monkeypatch):
    state.data = _twitch_data(token_expiry="2031-02-03T04:05:06")
    monkeypatch.setattr(api_credentials_manager, "get_twitch_credentials", lambda: {"client_id": "store"})
    assert api.load_auth_data() is True
    assert api.client_id == "store" and api.token_expiry == datetime(2031, 2, 3, 4, 5, 6)


def test_load_auth_data_bad_expiry_and_missing_tokens(api, state):
    state.data = _twitch_data(token_expiry="nope", refresh_token="")
    assert api.load_auth_data() is False
    assert api.token_expiry is None
    state.data = None
    assert api.load_auth_data() is False


def test_load_auth_data_waits_then_gives_up(api, state, monkeypatch):
    state._initialized = False
    sleeps = []
    monkeypatch.setattr(tw.time, "sleep", lambda s: sleeps.append(s))
    assert api.load_auth_data() is False
    assert len(sleeps) >= 99


def test_load_auth_data_becomes_ready_while_waiting(api, state, monkeypatch):
    state._initialized = False

    def sleep(_s):
        state._initialized = True

    monkeypatch.setattr(tw.time, "sleep", sleep)
    assert api.load_auth_data() is True


def test_load_auth_data_error(api, state, monkeypatch):
    monkeypatch.setattr(state, "get_twitch_data", MagicMock(side_effect=RuntimeError("db")))
    assert api.load_auth_data() is False


def test_save_auth_data(api, state):
    api.client_id, api.auth_token, api.token_expiry = "c", "abcdefghijklmn", FUTURE
    api.user = SimpleNamespace(display_name="Streamer")
    assert api.save_auth_data() is True
    assert state.saved[-1]["token_expiry"] == FUTURE.isoformat()
    assert state.app_updates == [("streamer_name", "Streamer")]


def test_save_auth_data_variants(api, state, monkeypatch):
    state.save_ok = False
    api.user = SimpleNamespace()  # no display_name -> warning
    assert api.save_auth_data() is True
    assert state.saved[-1]["token_expiry"] == "" and state.app_updates == []
    state._initialized = False
    assert api.save_auth_data() is False
    state._initialized = True
    monkeypatch.setattr(state, "set_twitch_data", MagicMock(side_effect=RuntimeError("db")))
    assert api.save_auth_data() is False


# --------------------------------------------------------------------------- expiry


def test_compute_and_migrate_expiry(api, state, monkeypatch):
    monkeypatch.setattr(tw, "compute_token_expiry", AsyncMock(return_value=FUTURE))
    assert run(api._compute_token_expiry("t")) == FUTURE
    run(api._migrate_legacy_token_expiry_if_needed())  # no token
    assert state.saved == []
    api.auth_token = "t"
    api.token_expiry = datetime.now() + timedelta(hours=1)
    run(api._migrate_legacy_token_expiry_if_needed())  # not legacy
    assert state.saved == []
    api.token_expiry = datetime.now() + timedelta(days=50)
    run(api._migrate_legacy_token_expiry_if_needed())
    assert api.token_expiry == FUTURE and len(state.saved) == 1


def test_is_token_expired(api):
    assert api.is_token_expired() is True
    api.auth_token, api.token_expiry = "t", FUTURE
    assert api.is_token_expired() is False


def test_apply_refresh_result(api, state, monkeypatch):
    assert run(api._apply_refresh_result(RefreshResult(success=False, outcome="x"))) is False
    monkeypatch.setattr(tw, "compute_token_expiry", AsyncMock(return_value=FUTURE))
    state.save_ok = False
    monkeypatch.setattr(api, "save_auth_data", MagicMock(return_value=False))
    ok = run(api._apply_refresh_result(RefreshResult(success=True, outcome="validated", auth_token="A",
                                                     refresh_token="R")))
    assert ok is True and api.token_expiry == FUTURE and api.auth_token == "A"
    api._sync_tokens_to_state_manager.assert_called_once()


# --------------------------------------------------------------------------- refresh_auth_token


@pytest.fixture
def refreshable(api, monkeypatch):
    api.client_id, api.client_secret = "cid", "sec"
    api.auth_token, api.refresh_token = "old", "ref"
    api.token_expiry = datetime.now() - timedelta(minutes=1)
    monkeypatch.setattr(api, "sync_client_credentials", lambda: None)
    monkeypatch.setattr(tw, "compute_token_expiry", AsyncMock(return_value=FUTURE))
    return api


def test_refresh_skipped_when_not_expired(refreshable):
    refreshable.token_expiry = FUTURE
    assert run(refreshable.refresh_auth_token()) is True
    assert refreshable._last_refresh_attempt is None
    assert not refreshable._refresh_lock.locked()


def test_refresh_waits_for_lock(refreshable, monkeypatch):
    refreshable.token_expiry = FUTURE
    refreshable._refresh_lock.acquire()
    real_sleep = asyncio.sleep

    async def fake_sleep(_d):
        refreshable._refresh_lock.release()
        await real_sleep(0)

    monkeypatch.setattr(tw.asyncio, "sleep", fake_sleep)
    assert run(refreshable.refresh_auth_token()) is True


@pytest.mark.parametrize("cid, secret, notified", [("", "", False), ("cid", "", True)])
def test_refresh_missing_client_credentials(refreshable, cid, secret, notified):
    refreshable.client_id, refreshable.client_secret = cid, secret
    assert run(refreshable.refresh_auth_token()) is False
    assert tw._notify_twitch_client_credentials.called is notified
    assert refreshable._last_refresh_attempt is not None


def test_refresh_missing_refresh_token(refreshable):
    refreshable.refresh_token = ""
    assert run(refreshable.refresh_auth_token()) is False


def test_refresh_success_creates_client(refreshable, state, monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(tw, "create_twitch_client", AsyncMock(return_value=client))
    monkeypatch.setattr(tw, "attach_refresh_callback", MagicMock())
    monkeypatch.setattr(tw, "refresh_user_token", AsyncMock(return_value=RefreshResult(
        success=True, outcome="validated", auth_token="new", refresh_token="nref", token_expiry=FUTURE)))
    assert run(refreshable.refresh_auth_token()) is True
    assert refreshable.twitch is client and refreshable.auth_token == "new" and refreshable.token_expiry == FUTURE
    kwargs = tw.refresh_user_token.await_args.kwargs
    assert kwargs["auth_token"] == "old" and kwargs["refresh_token"] == "ref"


@pytest.mark.parametrize("outcome, cleared", [("invalid_refresh_token", True), ("client_id_mismatch", True),
                                              ("transient_error", False)])
def test_refresh_failure_outcomes(refreshable, monkeypatch, outcome, cleared):
    refreshable.twitch = MagicMock()
    monkeypatch.setattr(tw, "refresh_user_token", AsyncMock(return_value=RefreshResult(success=False,
                                                                                       outcome=outcome)))
    assert run(refreshable.refresh_auth_token()) is False
    assert (refreshable.auth_token == "") is cleared


@pytest.mark.parametrize(
    "exc, cleared, notified",
    [
        (tw.InvalidRefreshTokenException("bad client secret"), False, True),
        (tw.InvalidRefreshTokenException("revoked"), True, False),
        (RuntimeError("invalid client_id"), False, True),
        (RuntimeError("token revoked"), True, False),
        (RuntimeError("timeout"), False, False),
    ],
)
def test_refresh_exceptions(refreshable, monkeypatch, exc, cleared, notified):
    refreshable.twitch = MagicMock()
    monkeypatch.setattr(tw, "refresh_user_token", AsyncMock(side_effect=exc))
    assert run(refreshable.refresh_auth_token()) is False
    assert (refreshable.auth_token == "") is cleared
    assert tw._notify_twitch_client_credentials.called is notified
    assert not refreshable._refresh_lock.locked()


def test_refresh_release_error_ignored(refreshable, monkeypatch):
    refreshable.token_expiry = FUTURE
    lock = MagicMock()
    lock.acquire.return_value = True
    lock.release.side_effect = RuntimeError("x")
    refreshable._refresh_lock = lock
    assert run(refreshable.refresh_auth_token()) is True


def test_clear_tokens_after_refresh_failure(api, monkeypatch):
    api.auth_token, api.refresh_token, api.token_expiry = "a", "r", FUTURE
    api._clear_tokens_after_refresh_failure()
    assert (api.auth_token, api.refresh_token, api.token_expiry) == ("", "", None)
    monkeypatch.setattr(api, "save_auth_data", MagicMock(side_effect=RuntimeError("x")))
    api._clear_tokens_after_refresh_failure()  # logged


# --------------------------------------------------------------------------- OAuth


@pytest.fixture
def oauth_env(api, monkeypatch):
    client = MagicMock()
    user = SimpleNamespace(id="99", display_name="Streamer")
    monkeypatch.setattr(tw, "create_twitch_client", AsyncMock(return_value=client))
    monkeypatch.setattr(tw, "attach_refresh_callback", MagicMock())
    monkeypatch.setattr(tw, "UserAuthenticator", MagicMock(return_value="AUTH"))
    monkeypatch.setattr(tw, "run_user_authentication", AsyncMock(return_value=("A", "R")))
    monkeypatch.setattr(tw, "apply_user_authentication", AsyncMock())
    monkeypatch.setattr(tw, "first", AsyncMock(return_value=user))
    monkeypatch.setattr(tw, "compute_token_expiry", AsyncMock(return_value=FUTURE))
    return SimpleNamespace(client=client, user=user)


def test_authenticate_with_oauth(api, oauth_env, state):
    assert run(api.authenticate_with_oauth()) is True
    assert api.authenticator == "AUTH" and (api.auth_token, api.refresh_token) == ("A", "R")
    assert api.user_id == "99" and api.token_expiry == FUTURE and state.saved
    tw.UserAuthenticator.assert_called_once_with(oauth_env.client, api.authscope, force_verify=False)


def test_authenticate_with_oauth_failure(api, oauth_env, monkeypatch):
    monkeypatch.setattr(tw, "run_user_authentication", AsyncMock(side_effect=RuntimeError("closed")))
    assert run(api.authenticate_with_oauth()) is False


# --------------------------------------------------------------------------- _ensure_user_authenticated


@pytest.fixture
def ensure_env(api, oauth_env, monkeypatch):
    api.client_id, api.client_secret = "cid", "sec"
    api.auth_token, api.refresh_token = "tok", "ref"
    api.token_expiry = FUTURE
    monkeypatch.setattr(api, "sync_client_credentials", lambda: None)
    monkeypatch.setattr(api, "authenticate_with_oauth", AsyncMock(return_value=True))
    monkeypatch.setattr(api, "refresh_auth_token", AsyncMock(return_value=False))
    monkeypatch.setattr(tw, "validate_access_token", AsyncMock(return_value=ValidateResult(ok=True,
                                                                                            outcome="validated")))
    monkeypatch.setattr(tw, "twitch_has_user_auth", MagicMock(return_value=True))
    return api


def test_ensure_missing_client(ensure_env):
    ensure_env.client_secret = ""
    assert run(ensure_env._ensure_user_authenticated()) is False


def test_ensure_missing_tokens(ensure_env):
    ensure_env.refresh_token = ""
    assert run(ensure_env._ensure_user_authenticated()) is False
    assert run(ensure_env._ensure_user_authenticated(allow_oauth=True)) is True
    ensure_env.authenticate_with_oauth.assert_awaited_once()


def test_ensure_client_id_mismatch(ensure_env, monkeypatch):
    monkeypatch.setattr(tw, "validate_access_token", AsyncMock(return_value=ValidateResult(
        ok=False, outcome="client_id_mismatch", message="m")))
    assert run(ensure_env._ensure_user_authenticated()) is False
    assert run(ensure_env._ensure_user_authenticated(allow_oauth=True)) is True
    tw.notify_twitch_connect_failed.assert_called_once_with("client_id_changed")


def test_ensure_already_authenticated_creates_client(ensure_env, oauth_env):
    assert run(ensure_env._ensure_user_authenticated()) is True
    assert ensure_env.twitch is oauth_env.client


def test_ensure_refreshes_when_token_invalid(ensure_env, monkeypatch):
    ensure_env.twitch = MagicMock()
    results = [ValidateResult(ok=False, outcome="invalid_token"), ValidateResult(ok=True, outcome="validated")]
    monkeypatch.setattr(tw, "validate_access_token", AsyncMock(side_effect=results))
    monkeypatch.setattr(ensure_env, "refresh_auth_token", AsyncMock(return_value=True))
    assert run(ensure_env._ensure_user_authenticated()) is True


def test_ensure_reapplies_auth_when_library_lacks_it(ensure_env, oauth_env, monkeypatch, state):
    ensure_env.twitch = MagicMock()
    monkeypatch.setattr(tw, "twitch_has_user_auth", MagicMock(return_value=False))
    assert run(ensure_env._ensure_user_authenticated()) is True
    assert ensure_env.user is oauth_env.user and ensure_env.user_id == "99" and state.saved


def test_ensure_apply_fails_then_refresh_recovers(ensure_env, monkeypatch):
    ensure_env.twitch = MagicMock()
    monkeypatch.setattr(tw, "twitch_has_user_auth", MagicMock(return_value=False))
    monkeypatch.setattr(tw, "apply_user_authentication", AsyncMock(side_effect=[RuntimeError("401"), None]))
    monkeypatch.setattr(ensure_env, "refresh_auth_token", AsyncMock(return_value=True))
    assert run(ensure_env._ensure_user_authenticated()) is True


def test_ensure_apply_fails_refresh_retry_fails_then_oauth(ensure_env, monkeypatch):
    ensure_env.twitch = MagicMock()
    monkeypatch.setattr(tw, "twitch_has_user_auth", MagicMock(return_value=False))
    monkeypatch.setattr(tw, "apply_user_authentication", AsyncMock(side_effect=RuntimeError("401")))
    monkeypatch.setattr(ensure_env, "refresh_auth_token", AsyncMock(return_value=True))
    assert run(ensure_env._ensure_user_authenticated()) is False
    assert run(ensure_env._ensure_user_authenticated(allow_oauth=True)) is True
    tw.notify_twitch_connect_failed.assert_called_with("signin_needed")


# --------------------------------------------------------------------------- stage_twitch_api


@pytest.fixture
def stage_env(api, oauth_env, monkeypatch):
    def load():
        api.client_id, api.client_secret = "cid", "sec"
        api.auth_token, api.refresh_token = "tok", "ref"
        api.token_expiry = FUTURE
        return True

    monkeypatch.setattr(api, "load_auth_data", load)
    monkeypatch.setattr(api, "sync_tokens_from_state", lambda: None)
    monkeypatch.setattr(api, "_migrate_legacy_token_expiry_if_needed", AsyncMock())
    monkeypatch.setattr(api, "authenticate_with_oauth", AsyncMock(return_value=True))
    monkeypatch.setattr(api, "refresh_auth_token", AsyncMock(return_value=True))
    monkeypatch.setattr(api, "_ensure_user_authenticated", AsyncMock(return_value=True))
    monkeypatch.setattr(tw, "validate_access_token", AsyncMock(return_value=ValidateResult(ok=True,
                                                                                            outcome="validated")))
    return api


def test_stage_happy_path(stage_env, oauth_env, state):
    assert run(stage_env.stage_twitch_api()) is True
    assert stage_env.twitch is oauth_env.client and stage_env.user_id == "99"
    stage_env.authenticate_with_oauth.assert_not_awaited()
    assert state.saved


def test_stage_missing_client(stage_env, monkeypatch):
    monkeypatch.setattr(stage_env, "load_auth_data", lambda: False)
    assert run(stage_env.stage_twitch_api()) is False


def test_stage_expired_token_refresh_fails_then_oauth(stage_env, monkeypatch):
    orig = stage_env.load_auth_data

    def load():
        orig()
        stage_env.token_expiry = datetime.now() - timedelta(minutes=1)
        return True

    monkeypatch.setattr(stage_env, "load_auth_data", load)
    monkeypatch.setattr(stage_env, "refresh_auth_token", AsyncMock(return_value=False))
    assert run(stage_env.stage_twitch_api()) is True
    stage_env.authenticate_with_oauth.assert_awaited_once()
    tw.notify_twitch_connect_failed.assert_called_with("signin_needed")


def test_stage_expired_token_refresh_ok(stage_env, monkeypatch):
    orig = stage_env.load_auth_data

    def load():
        orig()
        stage_env.token_expiry = None
        return True

    monkeypatch.setattr(stage_env, "load_auth_data", load)
    assert run(stage_env.stage_twitch_api()) is True
    stage_env.refresh_auth_token.assert_awaited()
    stage_env.authenticate_with_oauth.assert_not_awaited()


def test_stage_no_tokens_oauth_fails(stage_env, monkeypatch):
    def load():
        stage_env.client_id, stage_env.client_secret = "cid", "sec"
        return False

    monkeypatch.setattr(stage_env, "load_auth_data", load)
    monkeypatch.setattr(stage_env, "authenticate_with_oauth", AsyncMock(return_value=False))
    assert run(stage_env.stage_twitch_api()) is False


@pytest.mark.parametrize("oauth_ok", [True, False])
def test_stage_client_id_mismatch(stage_env, monkeypatch, oauth_ok):
    monkeypatch.setattr(tw, "validate_access_token", AsyncMock(return_value=ValidateResult(
        ok=False, outcome="client_id_mismatch", message="m")))
    monkeypatch.setattr(stage_env, "authenticate_with_oauth", AsyncMock(return_value=oauth_ok))
    assert run(stage_env.stage_twitch_api()) is oauth_ok
    tw.notify_twitch_connect_failed.assert_called_with("client_id_changed")


def test_stage_existing_tokens_fail_refresh_recovers(stage_env, oauth_env, monkeypatch):
    monkeypatch.setattr(tw, "first", AsyncMock(side_effect=[RuntimeError("401"), oauth_env.user]))
    assert run(stage_env.stage_twitch_api()) is True
    stage_env.refresh_auth_token.assert_awaited_once()
    stage_env.authenticate_with_oauth.assert_not_awaited()


def test_stage_existing_tokens_fail_refresh_retry_fails_oauth(stage_env, monkeypatch):
    monkeypatch.setattr(tw, "first", AsyncMock(side_effect=RuntimeError("401")))
    assert run(stage_env.stage_twitch_api()) is True
    stage_env.authenticate_with_oauth.assert_awaited_once()


def test_stage_existing_tokens_fail_refresh_fails_oauth_fails(stage_env, monkeypatch):
    monkeypatch.setattr(tw, "first", AsyncMock(side_effect=RuntimeError("401")))
    monkeypatch.setattr(stage_env, "refresh_auth_token", AsyncMock(return_value=False))
    monkeypatch.setattr(stage_env, "authenticate_with_oauth", AsyncMock(return_value=False))
    assert run(stage_env.stage_twitch_api()) is False


def test_stage_not_verified_at_end(stage_env, monkeypatch):
    monkeypatch.setattr(stage_env, "_ensure_user_authenticated", AsyncMock(return_value=False))
    assert run(stage_env.stage_twitch_api()) is False


def test_stage_unexpected_error(stage_env, monkeypatch):
    monkeypatch.setattr(stage_env, "load_auth_data", MagicMock(side_effect=RuntimeError("boom")))
    assert run(stage_env.stage_twitch_api()) is False
