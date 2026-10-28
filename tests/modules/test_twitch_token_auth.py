# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch_token_auth.py (validate / refresh / expiry helpers; no network)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import aiohttp  # noqa: E402
import pytest  # noqa: E402
from twitchAPI.type import AuthScope, AuthType, InvalidRefreshTokenException  # noqa: E402

from modules import twitch_token_auth as tta  # noqa: E402


@pytest.fixture(autouse=True)
def _scopes(monkeypatch):
    monkeypatch.setattr(tta, "_token_scopes", {})


class _Resp:
    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload or {}

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _fake_session(monkeypatch, status=200, payload=None, error=None, calls=None):
    class Session:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, url, headers=None, timeout=None):
            if calls is not None:
                calls.append((url, headers))
            if error is not None:
                raise error
            return _Resp(status, payload)

    monkeypatch.setattr(tta.aiohttp, "ClientSession", Session)


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ scopes cache


def test_remember_and_read_scopes():
    tta.remember_token_scopes("", ["a"])
    tta.remember_token_scopes("t", None)
    assert tta._token_scopes == {}
    tta.remember_token_scopes(" t ", ["a", AuthScope.CHAT_READ])
    assert tta.token_scopes("t") == frozenset({"a", str(AuthScope.CHAT_READ)})
    assert tta.token_scopes(None) is None


def test_remember_scopes_clears_when_full():
    for i in range(9):
        tta.remember_token_scopes(f"t{i}", [])
    assert len(tta._token_scopes) == 9
    tta.remember_token_scopes("new", ["x"])
    assert list(tta._token_scopes) == ["new"]


# ------------------------------------------------------------------ validate


def test_validate_missing_token():
    r = run(tta.validate_access_token("  "))
    assert r.ok is False and r.outcome == "missing_token"


def test_validate_ok_records_scopes(monkeypatch):
    calls = []
    _fake_session(monkeypatch, 200, {"client_id": "cid", "expires_in": 100, "scopes": ["chat:read"]}, calls=calls)
    r = run(tta.validate_access_token("tok", "cid"))
    assert r.ok and r.outcome == "validated" and r.expires_in == 100 and r.scopes == ["chat:read"]
    assert calls == [(tta.TWITCH_VALIDATE_URL, {"Authorization": "OAuth tok"})]
    assert tta.token_scopes("tok") == frozenset({"chat:read"})


def test_validate_zero_expiry_is_none(monkeypatch):
    _fake_session(monkeypatch, 200, {"client_id": "cid", "expires_in": 0})
    r = run(tta.validate_access_token("tok"))
    assert r.ok and r.expires_in is None and r.scopes == []


def test_validate_client_id_mismatch(monkeypatch):
    _fake_session(monkeypatch, 200, {"client_id": "other", "expires_in": 5})
    r = run(tta.validate_access_token("tok", "cid"))
    assert r.ok is False and r.outcome == "client_id_mismatch" and r.client_id == "other" and r.expires_in == 5


def test_validate_401_and_other_status(monkeypatch):
    _fake_session(monkeypatch, 401)
    assert run(tta.validate_access_token("tok")).outcome == "invalid_token"
    _fake_session(monkeypatch, 503)
    r = run(tta.validate_access_token("tok"))
    assert r.outcome == "transient_error" and r.http_status == 503


@pytest.mark.parametrize("error", [aiohttp.ClientError("x"), asyncio.TimeoutError(), OSError("x"), ValueError("v")])
def test_validate_errors_are_transient(monkeypatch, error):
    _fake_session(monkeypatch, error=error)
    r = run(tta.validate_access_token("tok"))
    assert r.ok is False and r.outcome == "transient_error"


# ------------------------------------------------------------------ expiry


def _validate_returns(monkeypatch, result):
    mock = AsyncMock(return_value=result)
    monkeypatch.setattr(tta, "validate_access_token", mock)
    return mock


def test_compute_expiry_from_validate(monkeypatch):
    _validate_returns(monkeypatch, tta.ValidateResult(ok=True, outcome="validated", expires_in=3600))
    exp = run(tta.compute_token_expiry("t"))
    assert timedelta(minutes=59) < exp - datetime.now() <= timedelta(hours=1)


def test_compute_expiry_non_expiring(monkeypatch):
    _validate_returns(monkeypatch, tta.ValidateResult(ok=True, outcome="validated", expires_in=-1))
    exp = run(tta.compute_token_expiry("t"))
    assert exp - datetime.now() > timedelta(days=59)


@pytest.mark.parametrize("outcome", ["transient_error", "invalid_token"])
def test_compute_expiry_fallback(monkeypatch, outcome):
    _validate_returns(monkeypatch, tta.ValidateResult(ok=False, outcome=outcome))
    exp = run(tta.compute_token_expiry("t"))
    assert abs((exp - datetime.now()) - tta.ACCESS_TOKEN_FALLBACK_LIFETIME) < timedelta(seconds=5)


def test_is_access_token_expired():
    future = datetime.now() + timedelta(hours=1)
    assert tta.is_access_token_expired("", future) is True
    assert tta.is_access_token_expired("t", None) is True
    assert tta.is_access_token_expired("t", future) is False
    assert tta.is_access_token_expired("t", datetime.now() + timedelta(minutes=2)) is True


def test_legacy_expiry_needs_migration():
    assert tta.legacy_expiry_needs_migration(None) is True
    assert tta.legacy_expiry_needs_migration(datetime.now() + timedelta(days=50)) is True
    assert tta.legacy_expiry_needs_migration(datetime.now() + timedelta(hours=3)) is False


def test_is_token_currently_valid(monkeypatch):
    mock = _validate_returns(monkeypatch, tta.ValidateResult(ok=True, outcome="validated"))
    assert run(tta.is_token_currently_valid("t", "cid")) is True
    mock.assert_awaited_once_with("t", "cid")


def test_twitch_has_user_auth():
    assert tta.twitch_has_user_auth(None) is False
    tw = MagicMock()
    tw.has_required_auth.return_value = True
    assert tta.twitch_has_user_auth(tw) is True
    tw.has_required_auth.assert_called_once_with(AuthType.USER, [])
    tw.has_required_auth.side_effect = RuntimeError("x")
    assert tta.twitch_has_user_auth(tw) is False


def test_create_twitch_client(monkeypatch):
    created = []

    class FakeTwitch:
        def __init__(self, cid, secret):
            self.args = (cid, secret)
            created.append(self)

        def __await__(self):
            if False:
                yield
            return self

    monkeypatch.setattr(tta, "Twitch", FakeTwitch)
    tw = run(tta.create_twitch_client("cid", "sec"))
    assert tw is created[0] and tw.args == ("cid", "sec")


def test_attach_refresh_callback_forwards_and_swallows():
    tw = SimpleNamespace()
    seen = []

    async def on_refresh(a, r):
        seen.append((a, r))
        if a == "bad":
            raise RuntimeError("x")

    tta.attach_refresh_callback(tw, on_refresh)
    run(tw.user_auth_refresh_callback("a", "r"))
    run(tw.user_auth_refresh_callback("bad", "r"))  # error logged, not raised
    assert seen == [("a", "r"), ("bad", "r")]


@pytest.mark.parametrize(
    "exc, expected",
    [
        (InvalidRefreshTokenException("x"), True),
        (RuntimeError("Invalid refresh token"), True),
        (RuntimeError("token revoked"), True),
        (RuntimeError("timeout"), False),
    ],
)
def test_is_definitive_refresh_failure(exc, expected):
    assert tta.is_definitive_refresh_failure(exc) is expected


def test_is_credential_config_error():
    assert tta.is_credential_config_error(RuntimeError("Bad client secret"))
    assert tta.is_credential_config_error(RuntimeError("invalid client_id"))
    assert not tta.is_credential_config_error(RuntimeError("other"))


# ------------------------------------------------------------------ apply_user_authentication


def _tw():
    tw = SimpleNamespace(app_id="cid", app_secret="sec")
    tw.set_user_authentication = AsyncMock()
    tw.refresh_used_token = AsyncMock()
    return tw


def test_apply_without_optional_scopes_skips_validation(monkeypatch):
    mock = _validate_returns(monkeypatch, None)
    tw = _tw()
    run(tta.apply_user_authentication(tw, "t", "r", [AuthScope.CHAT_READ]))
    mock.assert_not_awaited()
    tw.set_user_authentication.assert_awaited_once_with("t", [AuthScope.CHAT_READ], "r")


def test_apply_validates_unknown_token_and_drops_missing_optional(monkeypatch):
    async def validate(tok):
        tta.remember_token_scopes(tok, ["chat:read"])

    monkeypatch.setattr(tta, "validate_access_token", validate)
    tw = _tw()
    run(tta.apply_user_authentication(tw, "t", "r", [AuthScope.CHAT_READ, AuthScope.CHANNEL_MANAGE_POLLS]))
    assert tw.set_user_authentication.await_args.args[1] == [AuthScope.CHAT_READ]


def test_apply_keeps_all_when_scopes_unknown(monkeypatch):
    _validate_returns(monkeypatch, None)  # validate never records scopes
    tw = _tw()
    scopes = [AuthScope.CHAT_READ, AuthScope.CHANNEL_MANAGE_POLLS]
    run(tta.apply_user_authentication(tw, "t", "r", scopes))
    assert tw.set_user_authentication.await_args.args[1] == scopes


# ------------------------------------------------------------------ refresh_user_token


def _refresh(tw, **kw):
    args = dict(client_id="cid", client_secret="sec", auth_token="old", refresh_token="rold",
                scopes=[AuthScope.CHAT_READ])
    args.update(kw)
    return run(tta.refresh_user_token(tw, **args))


def test_refresh_missing_refresh_token():
    assert _refresh(_tw(), refresh_token=" ").outcome == "missing_refresh_token"


def test_refresh_updates_app_credentials_and_succeeds(monkeypatch):
    tw = _tw()
    tw.app_id, tw.app_secret = "x", "y"

    async def do_refresh():
        tw._user_auth_token = "new"
        tw._user_auth_refresh_token = "rnew"

    tw.refresh_used_token = AsyncMock(side_effect=do_refresh)
    _validate_returns(monkeypatch, tta.ValidateResult(ok=True, outcome="validated"))
    expiry = datetime.now() + timedelta(hours=4)
    monkeypatch.setattr(tta, "compute_token_expiry", AsyncMock(return_value=expiry))
    r = _refresh(tw)
    assert (tw.app_id, tw.app_secret) == ("cid", "sec")
    assert r.success and r.outcome == "validated" and r.auth_token == "new" and r.refresh_token == "rnew"
    assert r.token_expiry == expiry
    assert tw.set_user_authentication.await_count == 2


def test_refresh_same_tokens_is_unchanged_but_valid(monkeypatch):
    tw = _tw()
    tw._user_auth_token, tw._user_auth_refresh_token = "old", "rold"
    _validate_returns(monkeypatch, tta.ValidateResult(ok=True, outcome="validated"))
    monkeypatch.setattr(tta, "compute_token_expiry", AsyncMock(return_value=None))
    assert _refresh(tw).outcome == "unchanged_but_valid"


@pytest.mark.parametrize(
    "exc, outcome",
    [
        (InvalidRefreshTokenException("x"), "invalid_refresh_token"),
        (RuntimeError("bad client secret"), "credential_error"),
        (RuntimeError("token revoked"), "invalid_refresh_token"),
        (RuntimeError("timeout"), "transient_error"),
    ],
)
def test_refresh_errors(exc, outcome):
    tw = _tw()
    tw.refresh_used_token = AsyncMock(side_effect=exc)
    r = _refresh(tw)
    assert r.success is False and r.outcome == outcome


def test_refresh_empty_tokens_but_old_still_valid(monkeypatch):
    tw = _tw()
    _validate_returns(monkeypatch, tta.ValidateResult(ok=True, outcome="validated"))
    expiry = datetime.now()
    monkeypatch.setattr(tta, "compute_token_expiry", AsyncMock(return_value=expiry))
    r = _refresh(tw)
    assert r.success and r.outcome == "unchanged_but_valid" and r.auth_token == "old" and r.refresh_token == "rold"


def test_refresh_empty_tokens_and_invalid(monkeypatch):
    tw = _tw()
    _validate_returns(monkeypatch, tta.ValidateResult(ok=False, outcome="invalid_token"))
    assert _refresh(tw).outcome == "empty_tokens"


@pytest.mark.parametrize(
    "outcome, expected",
    [("client_id_mismatch", "client_id_mismatch"), ("invalid_token", "invalid_refresh_token"),
     ("transient_error", "transient_error")],
)
def test_refresh_new_token_fails_validation(monkeypatch, outcome, expected):
    tw = _tw()
    tw._user_auth_token, tw._user_auth_refresh_token = "new", "rnew"
    _validate_returns(monkeypatch, tta.ValidateResult(ok=False, outcome=outcome, message="m"))
    r = _refresh(tw)
    assert r.success is False and r.outcome == expected
