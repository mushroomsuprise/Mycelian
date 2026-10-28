# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/chatbot.py (part 2): the ``Chatbot_API`` class.

Every Twitch call is faked: token helpers, the twitchAPI client, OAuth, the state manager and the
aiohttp session. Threads only record their target (see ``_chatbot_fakes``).
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import aiohttp  # noqa: E402
import pytest  # noqa: E402

from _chatbot_fakes import FakeMainTwitch, cb  # noqa: E402,F401  (cb: fixture)
from modules import chatbot  # noqa: E402
from modules import twitch as twitch_mod  # noqa: E402
from modules.twitch_token_auth import RefreshResult, ValidateResult  # noqa: E402
from twitchAPI.type import InvalidRefreshTokenException  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class FakeState:
    def __init__(self):
        self._initialized = True
        self.data = SimpleNamespace(auth_token="stored-tok", refresh_token="stored-ref", user_id="bot9",
                                    token_expiry="2030-01-01T00:00:00")
        self.saved = []
        self.save_ok = True
        self.get_error = None
        self.set_error = None

    def get_chatbot_data(self):
        if self.get_error:
            raise self.get_error
        return self.data

    def set_chatbot_data(self, data):
        if self.set_error:
            raise self.set_error
        self.saved.append(dict(data))

    def save_changes(self):
        return self.save_ok


class FakeClient:
    def __init__(self):
        self.users_called = 0

    def get_users(self):
        self.users_called += 1
        return "users-generator"


@pytest.fixture
def st(cb, monkeypatch):
    """Configurable fakes for every Twitch / token helper the class calls."""
    s = SimpleNamespace(
        creds={"client_id": "cid", "client_secret": "sec"},
        expiry=datetime(2030, 1, 1),
        legacy=False,
        expired=False,
        currently_valid=True,
        refresh=RefreshResult(True, "refreshed", "new-tok", "new-ref", datetime(2031, 1, 1)),
        validate=[],
        validate_default=ValidateResult(True, "valid"),
        has_user_auth=True,
        apply=[],  # exceptions to raise on successive apply_user_authentication calls (None = ok)
        user=SimpleNamespace(id="bot1", display_name="Bot", profile_image_url="http://x/p.png"),
        users_error=None,
        oauth=("o-tok", "o-ref"),
        config_err=False,
        definitive=False,
        calls=[],
        state=FakeState(),
        cb=cb,
    )
    monkeypatch.setattr(chatbot, "get_chatbot_credentials", lambda: dict(s.creds))
    monkeypatch.setattr(chatbot, "attach_refresh_callback", lambda t, fn: s.calls.append(("attach", fn)))

    async def compute(token):
        s.calls.append(("compute", token))
        return s.expiry

    monkeypatch.setattr(chatbot, "compute_token_expiry", compute)
    monkeypatch.setattr(chatbot, "legacy_expiry_needs_migration", lambda e: s.legacy)
    monkeypatch.setattr(chatbot, "is_access_token_expired",
                        lambda tok, exp: s.expired() if callable(s.expired) else s.expired)

    async def currently_valid(tok, cid):
        s.calls.append(("currently_valid", tok))
        return s.currently_valid

    monkeypatch.setattr(chatbot, "is_token_currently_valid", currently_valid)

    async def create(cid, secret):
        s.calls.append(("create", cid))
        return FakeClient()

    monkeypatch.setattr(chatbot, "create_twitch_client", create)

    async def refresh(twitch, **kw):
        s.calls.append(("refresh", kw["refresh_token"]))
        if isinstance(s.refresh, BaseException):
            raise s.refresh
        return s.refresh

    monkeypatch.setattr(chatbot, "refresh_user_token", refresh)
    monkeypatch.setattr(chatbot, "is_credential_config_error", lambda e: s.config_err)
    monkeypatch.setattr(chatbot, "is_definitive_refresh_failure", lambda e: s.definitive)

    async def validate(tok, cid):
        s.calls.append(("validate", tok))
        return s.validate.pop(0) if s.validate else s.validate_default

    monkeypatch.setattr(chatbot, "validate_access_token", validate)
    monkeypatch.setattr(chatbot, "twitch_has_user_auth", lambda t: s.has_user_auth)

    async def apply(twitch, tok, ref, scopes):
        s.calls.append(("apply", tok))
        if s.apply:
            err = s.apply.pop(0)
            if err is not None:
                raise err

    monkeypatch.setattr(chatbot, "apply_user_authentication", apply)

    async def oauth(authenticator):
        s.calls.append(("oauth",))
        if isinstance(s.oauth, BaseException):
            raise s.oauth
        return s.oauth

    monkeypatch.setattr(chatbot, "run_user_authentication", oauth)
    monkeypatch.setattr(chatbot, "UserAuthenticator", lambda t, scopes, force_verify: SimpleNamespace(scopes=scopes))

    async def first(gen):
        if s.users_error:
            raise s.users_error
        return s.user

    monkeypatch.setattr(chatbot, "first", first)
    monkeypatch.setattr(chatbot, "stop_active_oauth", lambda: s.calls.append(("stop_oauth",)))
    monkeypatch.setattr(chatbot, "dataobjects", SimpleNamespace(state_manager=s.state))
    return s


@pytest.fixture
def api(st):
    a = chatbot.Chatbot_API()
    a.client_id, a.client_secret = "cid", "sec"
    a.auth_token, a.refresh_token = "tok", "ref"
    return a


def names(st):
    return [c[0] for c in st.calls]


# --------------------------------------------------------------------------- basics


def test_init_defaults(st):
    a = chatbot.Chatbot_API()
    assert a.client_id == "" and a.twitch is None and a.is_connected is False and a.using_fallback is False
    assert a.health_check_interval == 60 and a.connection_timeout == 300
    assert a.last_send_problem is None
    assert chatbot.AuthScope.USER_WRITE_CHAT in a.authscope


def test_sync_client_credentials(st, api):
    st.creds = {"client_id": "new", "client_secret": ""}
    api._sync_client_credentials()
    assert api.client_id == "new" and api.client_secret == "sec"
    st.creds = {"client_secret": "s2"}
    api._sync_client_credentials()
    assert api.client_id == "new" and api.client_secret == "s2"


def test_wire_refresh_callback(st, api):
    api._wire_twitch_refresh_callback()
    assert st.calls == []
    api.twitch = FakeClient()
    api._wire_twitch_refresh_callback()
    assert st.calls == [("attach", api._on_library_tokens_refreshed)]


def test_library_refresh_persists_tokens(st, api):
    run(api._on_library_tokens_refreshed("a2", "r2"))
    assert (api.auth_token, api.refresh_token, api.token_expiry) == ("a2", "r2", st.expiry)
    assert st.state.saved[-1]["auth_token"] == "a2"


def test_migrate_legacy_expiry(st, api):
    api.auth_token = ""
    run(api._migrate_legacy_token_expiry_if_needed())
    assert st.calls == []
    api.auth_token = "tok"
    run(api._migrate_legacy_token_expiry_if_needed())
    assert st.calls == []
    st.legacy = True
    run(api._migrate_legacy_token_expiry_if_needed())
    assert api.token_expiry == st.expiry and st.state.saved


def test_clear_tokens_after_refresh_failure(st, api, cb):
    api._clear_tokens_after_refresh_failure()
    assert (api.auth_token, api.refresh_token, api.token_expiry) == ("", "", None)
    assert st.state.saved[-1]["auth_token"] == ""
    assert cb.shown.titles[-1] == "The chatbot account isn't signed in"


def test_clear_tokens_save_error_is_logged(st, api, monkeypatch, caplog):
    def boom():
        raise RuntimeError("db")

    monkeypatch.setattr(api, "save_auth_data", boom)
    api._clear_tokens_after_refresh_failure()
    assert "Failed to save cleared chatbot token state" in caplog.text


# --------------------------------------------------------------------------- load / save auth


def test_load_auth_data_without_credentials(st):
    st.creds = {"client_id": "x"}
    assert chatbot.Chatbot_API().load_auth_data() is False


def test_load_auth_data_reads_tokens(st):
    a = chatbot.Chatbot_API()
    assert a.load_auth_data() is True
    assert (a.client_id, a.auth_token, a.refresh_token, a.user_id) == ("cid", "stored-tok", "stored-ref", "bot9")
    assert a.token_expiry == datetime(2030, 1, 1)


def test_load_auth_data_bad_expiry_and_empty_fields(st, caplog):
    st.state.data = SimpleNamespace(auth_token="", refresh_token="", user_id="", token_expiry="garbage")
    a = chatbot.Chatbot_API()
    assert a.load_auth_data() is True
    assert a.auth_token == "" and a.token_expiry is None
    assert "Error parsing chatbot token expiry" in caplog.text
    st.state.data = None
    assert chatbot.Chatbot_API().load_auth_data() is True


def test_load_auth_data_state_error_continues(st, caplog):
    st.state.get_error = RuntimeError("db down")
    assert chatbot.Chatbot_API().load_auth_data() is True
    assert "Error loading chatbot tokens from state manager" in caplog.text


def test_load_auth_data_waits_for_state_manager(st, monkeypatch, caplog):
    st.state._initialized = False
    sleeps = []
    monkeypatch.setattr(chatbot.time, "sleep", lambda s: sleeps.append(s))
    assert chatbot.Chatbot_API().load_auth_data() is False
    assert len(sleeps) >= 99 and "State manager not initialized" in caplog.text


def test_load_auth_data_state_becomes_ready(st, monkeypatch):
    st.state._initialized = False

    def sleep(s):
        st.state._initialized = True

    monkeypatch.setattr(chatbot.time, "sleep", sleep)
    assert chatbot.Chatbot_API().load_auth_data() is True


def test_load_auth_data_unexpected_error(st, monkeypatch):
    def boom():
        raise RuntimeError("creds broken")

    monkeypatch.setattr(chatbot, "get_chatbot_credentials", boom)
    assert chatbot.Chatbot_API().load_auth_data() is False


def test_save_auth_data(st, api):
    api.user_id = "bot1"
    api.token_expiry = datetime(2030, 5, 6)
    assert api.save_auth_data() is True
    assert st.state.saved[-1] == {"auth_token": "tok", "refresh_token": "ref", "user_id": "bot1",
                                  "token_expiry": "2030-05-06T00:00:00"}
    assert api.is_connected is True and chatbot.chatbot_connected is True
    api.auth_token = ""
    api.token_expiry = None
    api.save_auth_data()
    assert st.state.saved[-1]["token_expiry"] == "" and api.is_connected is False


def test_save_auth_data_state_not_ready(st, api):
    st.state._initialized = False
    assert api.save_auth_data() is False
    assert api.is_connected and chatbot.chatbot_connected and st.state.saved == []


def test_save_auth_data_save_changes_false_notifies(st, api, cb):
    st.state.save_ok = False
    assert api.save_auth_data() is True
    assert cb.shown.titles == ["Couldn't save the chatbot sign-in"]
    assert cb.shown.calls[0][1]["details"] == "state_manager.save_changes() returned False"


def test_save_auth_data_error(st, api, cb):
    st.state.set_error = RuntimeError("write")
    assert api.save_auth_data() is False
    assert cb.shown.calls[0][1]["details"] == "RuntimeError: write"
    assert api.is_connected is True


def test_is_token_expired_delegates(st, api):
    st.expired = True
    assert api.is_token_expired() is True


# --------------------------------------------------------------------------- refresh


def test_apply_refresh_result(st, api, monkeypatch, caplog):
    assert run(api._apply_refresh_result(RefreshResult(False, "x"))) is False
    assert run(api._apply_refresh_result(RefreshResult(True, "ok", "a", "b", None))) is True
    assert (api.auth_token, api.refresh_token, api.token_expiry) == ("a", "b", st.expiry)
    monkeypatch.setattr(api, "save_auth_data", lambda: False)
    assert run(api._apply_refresh_result(RefreshResult(True, "ok", "c", "d", datetime(2040, 1, 1)))) is True
    assert api.token_expiry == datetime(2040, 1, 1)
    assert "Failed to save refreshed chatbot tokens" in caplog.text


def test_refresh_success(st, api):
    assert run(api.refresh_auth_token()) is True
    assert api.auth_token == "new-tok" and api.refresh_token == "new-ref"
    assert ("create", "cid") in st.calls and ("attach", api._on_library_tokens_refreshed) in st.calls
    assert not api._refresh_lock.locked()


def test_refresh_recent_attempt_checks_validity(st, api):
    api._last_refresh_attempt = datetime.now() - timedelta(seconds=5)
    st.currently_valid = False
    assert run(api.refresh_auth_token()) is False
    assert "refresh" not in names(st)


def test_refresh_needs_credentials_and_refresh_token(st, api):
    st.creds = {}
    api.client_secret = ""
    assert run(api.refresh_auth_token()) is False
    api.client_secret = "sec"
    api.refresh_token = ""
    api._last_refresh_attempt = None
    assert run(api.refresh_auth_token()) is False
    assert "refresh" not in names(st)


def test_refresh_reuses_existing_client(st, api):
    api.twitch = FakeClient()
    assert run(api.refresh_auth_token()) is True
    assert "create" not in names(st)


@pytest.mark.parametrize("outcome,cleared", [("invalid_refresh_token", True), ("client_id_mismatch", True),
                                             ("network", False)])
def test_refresh_failure_outcomes(st, api, outcome, cleared):
    st.refresh = RefreshResult(False, outcome)
    assert run(api.refresh_auth_token()) is False
    assert (api.auth_token == "") is cleared


@pytest.mark.parametrize("exc,config,definitive,cleared", [
    (InvalidRefreshTokenException("bad"), True, False, False),
    (InvalidRefreshTokenException("bad"), False, False, True),
    (RuntimeError("secret wrong"), True, False, False),
    (RuntimeError("invalid grant"), False, True, True),
    (RuntimeError("timeout"), False, False, False),
])
def test_refresh_exceptions(st, api, exc, config, definitive, cleared):
    st.refresh = exc
    st.config_err = config
    st.definitive = definitive
    assert run(api.refresh_auth_token()) is False
    assert (api.auth_token == "") is cleared
    assert not api._refresh_lock.locked()


def test_refresh_lock_released_elsewhere_is_tolerated(st, api, monkeypatch):
    async def refresh(twitch, **kw):
        api._refresh_lock.release()  # e.g. a reset elsewhere: the final release must not raise
        return st.refresh

    monkeypatch.setattr(chatbot, "refresh_user_token", refresh)
    assert run(api.refresh_auth_token()) is True
    assert not api._refresh_lock.locked()


@pytest.mark.parametrize("valid", [True, False])
def test_refresh_waits_for_running_refresh(st, api, valid):
    st.currently_valid = valid

    async def scenario():
        api._refresh_lock.acquire()
        task = asyncio.create_task(api.refresh_auth_token())
        await asyncio.sleep(0)
        api._refresh_lock.release()
        return await task

    assert run(scenario()) is valid
    assert not api._refresh_lock.locked()
    assert "refresh" not in names(st)


# --------------------------------------------------------------------------- OAuth / ensure auth


def test_authenticate_with_oauth(st, api):
    assert run(api.authenticate_with_oauth()) is True
    assert (api.auth_token, api.refresh_token, api.user_id) == ("o-tok", "o-ref", "bot1")
    assert api.is_connected and chatbot.chatbot_connected and api.token_expiry == st.expiry
    assert api.authenticator.scopes is api.authscope


def test_authenticate_with_oauth_failure(st, api):
    st.oauth = RuntimeError("user closed browser")
    assert run(api.authenticate_with_oauth()) is False


def test_ensure_auth_without_credentials_or_tokens(st, api, monkeypatch):
    st.creds = {}
    api.client_id = ""
    assert run(api._ensure_user_authenticated()) is False
    api.client_id = "cid"
    api.refresh_token = ""
    assert run(api._ensure_user_authenticated()) is False
    monkeypatch.setattr(api, "authenticate_with_oauth", lambda: _async(True))
    assert run(api._ensure_user_authenticated(allow_oauth=True)) is True


async def _async(value):
    return value


def test_ensure_auth_client_id_mismatch(st, api, monkeypatch):
    st.validate_default = ValidateResult(False, "client_id_mismatch", message="other app")
    assert run(api._ensure_user_authenticated()) is False
    monkeypatch.setattr(api, "authenticate_with_oauth", lambda: _async(True))
    assert run(api._ensure_user_authenticated(allow_oauth=True)) is True


def test_ensure_auth_already_good(st, api):
    assert run(api._ensure_user_authenticated()) is True
    assert "create" in names(st) and "apply" not in names(st)


def test_ensure_auth_refreshes_expired_token(st, api, monkeypatch):
    api.twitch = FakeClient()
    st.validate = [ValidateResult(False, "invalid"), ValidateResult(True, "valid")]
    monkeypatch.setattr(api, "refresh_auth_token", lambda: _async(True))
    assert run(api._ensure_user_authenticated()) is True
    assert "apply" not in names(st)


def test_ensure_auth_applies_tokens_when_client_lacks_them(st, api):
    api.twitch = FakeClient()
    st.has_user_auth = False
    assert run(api._ensure_user_authenticated()) is True
    assert "apply" in names(st) and api.user_id == "bot1" and st.state.saved


def test_ensure_auth_refresh_then_apply(st, api, monkeypatch):
    api.twitch = FakeClient()
    st.expired = True
    st.has_user_auth = False
    monkeypatch.setattr(api, "refresh_auth_token", lambda: _async(False))
    assert run(api._ensure_user_authenticated()) is True  # applies the current tokens


def test_ensure_auth_apply_fails_then_refresh_recovers(st, api, monkeypatch):
    api.twitch = FakeClient()
    st.has_user_auth = False
    st.apply = [RuntimeError("401"), None]
    monkeypatch.setattr(api, "refresh_auth_token", lambda: _async(True))
    assert run(api._ensure_user_authenticated()) is True


def test_ensure_auth_apply_fails_after_refresh(st, api, monkeypatch):
    api.twitch = FakeClient()
    st.has_user_auth = False
    st.apply = [RuntimeError("401"), RuntimeError("still 401")]
    monkeypatch.setattr(api, "refresh_auth_token", lambda: _async(True))
    assert run(api._ensure_user_authenticated()) is False
    st.apply = [RuntimeError("401"), RuntimeError("still 401")]
    monkeypatch.setattr(api, "authenticate_with_oauth", lambda: _async(True))
    assert run(api._ensure_user_authenticated(allow_oauth=True)) is True


def test_ensure_auth_apply_fails_and_refresh_fails(st, api, monkeypatch):
    api.twitch = FakeClient()
    st.has_user_auth = False
    st.apply = [RuntimeError("401")]
    monkeypatch.setattr(api, "refresh_auth_token", lambda: _async(False))
    assert run(api._ensure_user_authenticated()) is False


# --------------------------------------------------------------------------- staging


@pytest.fixture
def manager(monkeypatch):
    from modules import chatbot_manager

    m = SimpleNamespace(started=0, fail=False, _deferred_interval_events=[])

    def start():
        if m.fail:
            raise RuntimeError("manager broke")
        m.started += 1

    m.start_deferred_interval_events = start
    monkeypatch.setattr(chatbot_manager, "get_manager", lambda: m)
    return m


def test_stage_fallback_mode(st, manager):
    st.creds = {}
    a = chatbot.Chatbot_API()
    assert run(a.stage_chatbot_api()) is True
    assert a.using_fallback is True and manager.started == 1
    manager.fail = True
    assert run(chatbot.Chatbot_API().stage_chatbot_api()) is True


def test_stage_with_existing_tokens(st, manager, cb):
    a = chatbot.Chatbot_API()
    assert run(a.stage_chatbot_api()) is True
    assert a.using_fallback is False and a.is_connected and chatbot.chatbot_connected
    assert a.user_id == "bot1" and manager.started == 1
    health = cb.threads[-1]
    assert health.target == a._health_check_loop and health.daemon and health.started


def test_stage_expired_tokens_refresh(st, manager, monkeypatch):
    st.expired = True
    a = chatbot.Chatbot_API()
    refreshed = []

    async def refresh():
        refreshed.append(1)
        return True

    monkeypatch.setattr(a, "refresh_auth_token", refresh)
    monkeypatch.setattr(a, "_ensure_user_authenticated", lambda: _async(True))
    assert run(a.stage_chatbot_api()) is True and refreshed


def test_stage_expired_refresh_fails_runs_oauth(st, manager, monkeypatch):
    st.expired = True
    a = chatbot.Chatbot_API()
    monkeypatch.setattr(a, "refresh_auth_token", lambda: _async(False))
    monkeypatch.setattr(a, "_ensure_user_authenticated", lambda: _async(True))
    assert run(a.stage_chatbot_api()) is True
    assert ("oauth",) in st.calls


def test_stage_without_tokens_oauth_fails(st, manager):
    st.state.data = None
    st.oauth = RuntimeError("cancelled")
    assert run(chatbot.Chatbot_API().stage_chatbot_api()) is False


def test_stage_client_id_mismatch(st, manager, monkeypatch):
    st.validate = [ValidateResult(False, "client_id_mismatch", message="other app")]
    a = chatbot.Chatbot_API()
    monkeypatch.setattr(a, "_ensure_user_authenticated", lambda: _async(True))
    assert run(a.stage_chatbot_api()) is True and ("oauth",) in st.calls
    st.validate = [ValidateResult(False, "client_id_mismatch", message="other app")]
    st.oauth = RuntimeError("no")
    assert run(chatbot.Chatbot_API().stage_chatbot_api()) is False


def test_stage_validation_failure_recovers_with_refresh(st, manager, monkeypatch, caplog):
    import logging

    caplog.set_level(logging.INFO, logger=chatbot.logger.name)
    st.users_error = RuntimeError("401")
    a = chatbot.Chatbot_API()

    async def refresh():
        st.users_error = None
        return True

    monkeypatch.setattr(a, "refresh_auth_token", refresh)
    monkeypatch.setattr(a, "_ensure_user_authenticated", lambda: _async(True))
    assert run(a.stage_chatbot_api()) is True
    assert chatbot._chatbot_token_validation_warned is True
    assert "Recovered chatbot session after token refresh" in caplog.text


def test_stage_validation_failure_second_time_logs_debug(st, manager, monkeypatch):
    chatbot._chatbot_token_validation_warned = True
    st.users_error = RuntimeError("401")
    a = chatbot.Chatbot_API()
    monkeypatch.setattr(a, "refresh_auth_token", lambda: _async(True))  # users still fail -> oauth
    monkeypatch.setattr(a, "authenticate_with_oauth", lambda: _async(False))
    assert run(a.stage_chatbot_api()) is False


def test_stage_validation_failure_oauth_succeeds(st, manager, monkeypatch):
    st.users_error = RuntimeError("401")
    a = chatbot.Chatbot_API()
    monkeypatch.setattr(a, "refresh_auth_token", lambda: _async(False))
    monkeypatch.setattr(a, "authenticate_with_oauth", lambda: _async(True))
    monkeypatch.setattr(a, "_ensure_user_authenticated", lambda: _async(True))
    assert run(a.stage_chatbot_api()) is True


def test_stage_unverified_auth_fails(st, manager, monkeypatch):
    a = chatbot.Chatbot_API()
    monkeypatch.setattr(a, "_ensure_user_authenticated", lambda: _async(False))
    assert run(a.stage_chatbot_api()) is False


def test_stage_schedules_retry_when_manager_fails(st, manager, cb, monkeypatch):
    manager.fail = True
    a = chatbot.Chatbot_API()
    assert run(a.stage_chatbot_api()) is True
    retry = cb.threads[-1]
    assert retry.daemon is True and retry.started
    sleeps = []
    monkeypatch.setattr(chatbot.time, "sleep", lambda s: sleeps.append(s))
    retry.run()  # still failing: logged
    manager.fail = False
    retry.run()
    assert sleeps == [5, 5] and manager.started == 1


def test_stage_unexpected_error(st, monkeypatch):
    a = chatbot.Chatbot_API()

    def boom():
        raise RuntimeError("load broke")

    monkeypatch.setattr(a, "load_auth_data", boom)
    assert run(a.stage_chatbot_api()) is False


# --------------------------------------------------------------------------- health check / oauth cancel


def test_start_and_stop_health_check(st, api, cb):
    api.stop_health_check()  # nothing running
    api.start_health_check()
    thread = cb.threads[-1]
    api.start_health_check()  # not alive (fake): starts again
    assert len(cb.threads) == 2
    cb.threads[-1].alive = True
    api.start_health_check()
    assert len(cb.threads) == 2
    api.stop_health_check()
    assert thread.target == api._health_check_loop


def test_cancel_oauth(st, api):
    api.cancel_oauth()
    assert st.calls == []
    api.authenticator = object()
    api.cancel_oauth()
    assert st.calls == [("stop_oauth",)] and api.authenticator is None


class Stop(BaseException):
    pass


def _one_round(monkeypatch, api, extra=None):
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if extra:
            extra()
        raise Stop()

    monkeypatch.setattr(chatbot.time, "sleep", sleep)
    with pytest.raises(Stop):
        api._health_check_loop()
    return sleeps


@pytest.fixture
def no_loop_leak():
    yield
    asyncio.set_event_loop(None)


def test_health_check_fallback(st, api, monkeypatch, manager):
    api.using_fallback = True
    monkeypatch.setattr(twitch_mod, "twitch_api", FakeMainTwitch(connected=True))
    api.is_connected = False
    assert _one_round(monkeypatch, api) == [60]
    assert chatbot.chatbot_connected is True
    monkeypatch.setattr(twitch_mod, "twitch_api", FakeMainTwitch(connected=False))
    _one_round(monkeypatch, api)
    assert chatbot.chatbot_connected is False


def test_health_check_dedicated_connected_starts_deferred(st, api, monkeypatch, manager):
    api.is_connected = True
    api.twitch = FakeClient()
    manager._deferred_interval_events = [object()]
    _one_round(monkeypatch, api)
    assert api.last_health_check is not None and chatbot.chatbot_connected is True
    assert manager.started == 1
    manager._deferred_interval_events = []
    _one_round(monkeypatch, api)
    assert manager.started == 1
    manager.fail = True
    manager._deferred_interval_events = [1]
    _one_round(monkeypatch, api)  # error is logged, loop continues


def test_health_check_manager_without_deferred_list(st, api, monkeypatch):
    from modules import chatbot_manager

    monkeypatch.setattr(chatbot_manager, "get_manager", lambda: SimpleNamespace())
    api.is_connected = True
    api.twitch = FakeClient()
    assert _one_round(monkeypatch, api) == [60]


def test_health_check_failure_marks_disconnected(st, api, monkeypatch, manager):
    api.is_connected = False
    chatbot.chatbot_connected = True
    _one_round(monkeypatch, api)
    assert api.is_connected is False and chatbot.chatbot_connected is False
    assert manager.started == 0


@pytest.mark.parametrize("result", [True, False, RuntimeError("refresh boom")])
def test_health_check_proactive_refresh(st, api, monkeypatch, manager, no_loop_leak, result):
    st.expired = True
    calls = []

    async def refresh():
        calls.append(1)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(api, "refresh_auth_token", refresh)
    _one_round(monkeypatch, api)
    assert calls == [1]


def test_health_check_unexpected_error_waits_longer(st, api, monkeypatch):
    def boom():
        raise RuntimeError("expiry check broke")

    st.expired = boom
    api.is_connected = True
    api.twitch = FakeClient()
    assert _one_round(monkeypatch, api) == [10]
    assert api.is_connected is False and chatbot.chatbot_connected is False


# --------------------------------------------------------------------------- send_chat_message


@pytest.fixture
def helix(monkeypatch, api):
    calls = []
    state = {"response": {"data": [{"is_sent": True}]}}

    async def call(url, method="GET", params=None, json_data=None):
        calls.append((url, method, json_data))
        if isinstance(state["response"], Exception):
            raise state["response"]
        return state["response"]

    monkeypatch.setattr(api, "generic_api_call", call)
    return SimpleNamespace(calls=calls, state=state)


def test_send_dedicated_success_with_reply(st, api, helix, monkeypatch):
    monkeypatch.setattr(twitch_mod, "twitch_api", FakeMainTwitch(user_id="100"))
    monkeypatch.setattr(api, "_ensure_user_authenticated", lambda: _async(True))
    api.user_id = "bot1"
    assert run(api.send_chat_message("hello", "parent")) is True
    url, method, body = helix.calls[0]
    assert url.endswith("/chat/messages") and method == "POST"
    assert body == {"broadcaster_id": "100", "sender_id": "bot1", "message": "hello", "reply_parent_message_id": "parent"}
    assert chatbot._recent_sent_chat[-1][1] == "hello"
    assert api.last_send_problem is None


def test_send_dedicated_problems(st, api, helix, monkeypatch):
    monkeypatch.setattr(api, "_ensure_user_authenticated", lambda: _async(False))
    assert run(api.send_chat_message("x")) is False
    assert api.last_send_problem == ("auth", "Chatbot account has no working sign-in")
    monkeypatch.setattr(api, "_ensure_user_authenticated", lambda: _async(True))
    st.expired = True
    monkeypatch.setattr(api, "refresh_auth_token", lambda: _async(False))
    assert run(api.send_chat_message("x")) is False
    assert api.last_send_problem == ("auth", "Chatbot token refresh failed")
    monkeypatch.setattr(api, "refresh_auth_token", lambda: _async(True))
    assert run(api.send_chat_message("x")) is False
    assert api.last_send_problem[0] == "channel"
    monkeypatch.setattr(twitch_mod, "twitch_api", FakeMainTwitch(user_id="100"))
    helix.state["response"] = {}
    assert run(api.send_chat_message("x")) is False
    assert api.last_send_problem[0] == "refused"
    helix.state["response"] = {"data": [{"is_sent": False, "drop_reason": {"message": "AutoMod"}}]}
    assert run(api.send_chat_message("x")) is False
    assert api.last_send_problem == ("dropped", "AutoMod")
    helix.state["response"] = Exception("Chatbot authentication required")
    assert run(api.send_chat_message("x")) is False
    assert api.last_send_problem[0] == "auth"
    helix.state["response"] = Exception("Twitch API Error 403")
    assert run(api.send_chat_message("x")) is False
    assert api.last_send_problem == ("refused", "Twitch API Error 403")


def test_send_fallback_routes_to_main_account(st, api, monkeypatch):
    api.using_fallback = True
    assert run(api.send_chat_message("x")) is False
    assert api.last_send_problem[0] == "channel"
    main = FakeMainTwitch(user_id="100")
    monkeypatch.setattr(twitch_mod, "twitch_api", main)
    assert run(api.send_chat_message("hi", "p")) is True
    assert main.calls[0][2] == {"broadcaster_id": "100", "sender_id": "100", "message": "hi",
                                "reply_parent_message_id": "p"}
    main.response = {"data": [{"is_sent": False, "drop_reason": "slow mode"}]}
    assert run(api.send_chat_message("hi")) is False and api.last_send_problem == ("dropped", "slow mode")
    main.response = {}
    assert run(api.send_chat_message("hi")) is False and api.last_send_problem[0] == "refused"
    main.raise_exc = RuntimeError("500")
    assert run(api.send_chat_message("hi")) is False and api.last_send_problem == ("refused", "500")


# --------------------------------------------------------------------------- announcements


def test_announcement_dedicated(st, api, helix, monkeypatch):
    assert run(api.send_chat_announcement("x")) is False  # not connected
    api.is_connected, api.twitch, api.user_id = True, FakeClient(), "bot1"
    st.expired = True
    monkeypatch.setattr(api, "refresh_auth_token", lambda: _async(False))
    assert run(api.send_chat_announcement("x")) is False
    st.expired = False
    assert run(api.send_chat_announcement("x")) is False  # no main channel
    monkeypatch.setattr(twitch_mod, "twitch_api", FakeMainTwitch(user_id="100"))
    assert run(api.send_chat_announcement("hey", "rainbow")) is True
    assert helix.calls[-1][2] == {"broadcaster_id": "100", "moderator_id": "bot1", "message": "hey", "color": "primary"}
    assert run(api.send_chat_announcement("hey", "green")) is True
    assert helix.calls[-1][2]["color"] == "green"
    helix.state["response"] = {}
    assert run(api.send_chat_announcement("hey")) is False
    helix.state["response"] = RuntimeError("x")
    assert run(api.send_chat_announcement("hey")) is False


def test_announcement_fallback(st, api, monkeypatch):
    api.using_fallback = True
    assert run(api.send_chat_announcement("x")) is False
    main = FakeMainTwitch(user_id="100")
    monkeypatch.setattr(twitch_mod, "twitch_api", main)
    assert run(api.send_chat_announcement("x", "nope")) is True
    assert main.calls[-1][2]["color"] == "primary" and main.calls[-1][0].endswith("/chat/announcements")
    assert run(api.send_chat_announcement("x", "orange")) is True
    assert main.calls[-1][2]["color"] == "orange"
    main.response = {}
    assert run(api.send_chat_announcement("x")) is False
    main.raise_exc = RuntimeError("down")
    assert run(api.send_chat_announcement("x")) is False


# --------------------------------------------------------------------------- generic_api_call


class FakeResponse:
    def __init__(self, status=200, body=None, content_type="application/json"):
        self.status = status
        self.body = body if body is not None else {"ok": True}
        self.content_type = content_type

    async def json(self):
        return self.body

    async def text(self):
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, params=None, json=None, headers=None):
        self.requests.append((method, url, params, json, dict(headers)))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def session(monkeypatch, api):
    holder = {"session": FakeSession([FakeResponse()])}

    @contextlib.asynccontextmanager
    async def ephemeral():
        yield holder["session"]

    monkeypatch.setattr(twitch_mod, "_ephemeral_client_session", ephemeral)
    monkeypatch.setattr(chatbot, "_aiohttp_session_usable_on_running_loop", lambda s: False)
    return holder


def test_generic_call_needs_credentials(st, api, monkeypatch):
    api.auth_token = ""
    monkeypatch.setattr(api, "load_auth_data", lambda: False)
    with pytest.raises(Exception, match="no valid tokens"):
        run(api.generic_api_call("https://x"))


def test_generic_call_loads_credentials(st, api, session, monkeypatch):
    api.auth_token = ""

    def load():
        api.auth_token = "loaded"
        return True

    monkeypatch.setattr(api, "load_auth_data", load)
    assert run(api.generic_api_call("https://x", "get", params={"a": 1})) == {"ok": True}
    method, url, params, body, headers = session["session"].requests[0]
    assert (method, params, body) == ("GET", {"a": 1}, None)
    assert headers == {"Client-ID": "cid", "Authorization": "Bearer loaded"}


def test_generic_call_refresh_failure(st, api, monkeypatch):
    st.expired = True
    monkeypatch.setattr(api, "refresh_auth_token", lambda: _async(False))
    with pytest.raises(Exception, match="token refresh failed"):
        run(api.generic_api_call("https://x"))


def test_generic_call_token_missing_after_refresh(st, api, monkeypatch):
    st.expired = True

    async def refresh():
        api.auth_token = ""
        return True

    monkeypatch.setattr(api, "refresh_auth_token", refresh)
    with pytest.raises(Exception, match="token missing"):
        run(api.generic_api_call("https://x"))


def test_generic_call_uses_library_session_when_usable(st, api, session, monkeypatch):
    lib = FakeSession([FakeResponse(204, "", content_type="text/plain")])
    api.twitch = SimpleNamespace(_Twitch__session=lib)
    monkeypatch.setattr(chatbot, "_aiohttp_session_usable_on_running_loop", lambda s: s is lib)
    out = run(api.generic_api_call("https://x", "POST", json_data={"m": 1}))
    assert out == {"status": 204, "data": ""}
    assert lib.requests[0][4]["Content-Type"] == "application/json"
    assert session["session"].requests == []


def test_generic_call_without_library_client_branch(st, api, session, monkeypatch):
    st.expired = True

    async def refresh():
        api.client_id = ""
        return True

    monkeypatch.setattr(api, "refresh_auth_token", refresh)
    assert run(api.generic_api_call("https://x")) == {"ok": True}


def test_generic_call_error_status(st, api, session):
    session["session"] = FakeSession([FakeResponse(500, {"error": "x"})])
    with pytest.raises(Exception, match="Twitch API Error 500"):
        run(api.generic_api_call("https://x"))


def test_generic_call_401_refresh_and_retry(st, api, session, monkeypatch):
    async def refresh():
        api.auth_token = "fresh"
        return True

    monkeypatch.setattr(api, "refresh_auth_token", refresh)
    session["session"] = FakeSession([FakeResponse(401, {}), FakeResponse(200, {"data": 1})])
    assert run(api.generic_api_call("https://x")) == {"data": 1}
    assert session["session"].requests[1][4]["Authorization"] == "Bearer fresh"
    session["session"] = FakeSession([FakeResponse(401, {}), FakeResponse(202, "ok", content_type="text/plain")])
    assert run(api.generic_api_call("https://x")) == {"status": 202, "data": "ok"}
    session["session"] = FakeSession([FakeResponse(401, {}), FakeResponse(403, "nope", content_type="text/plain")])
    with pytest.raises(Exception, match="Chatbot authentication failed - Twitch API Error 403"):
        run(api.generic_api_call("https://x"))


def test_generic_call_401_refresh_fails(st, api, session, monkeypatch):
    monkeypatch.setattr(api, "refresh_auth_token", lambda: _async(False))
    session["session"] = FakeSession([FakeResponse(401, {})])
    with pytest.raises(Exception, match="token refresh unsuccessful"):
        run(api.generic_api_call("https://x"))


def test_generic_call_network_error(st, api, session):
    session["session"] = FakeSession([aiohttp.ClientConnectionError("reset")])
    with pytest.raises(Exception, match="Network error during chatbot API call: reset"):
        run(api.generic_api_call("https://x"))


# --------------------------------------------------------------------------- status


@pytest.fixture
def timing(monkeypatch):
    seen = []
    monkeypatch.setattr(twitch_mod, "build_token_timing_fields",
                        lambda token_expiry, has_auth_token: seen.append((token_expiry, has_auth_token)) or {"t": 1})
    return seen


def test_connection_status_fallback(st, api, monkeypatch, timing):
    api.using_fallback = True
    status = api.get_connection_status()
    assert status["status"] == "Disconnected (Fallback Mode)" and status["t"] == 1
    assert timing[-1] == (None, False)
    main = FakeMainTwitch()
    main.token_expiry = datetime(2030, 1, 1)
    monkeypatch.setattr(twitch_mod, "twitch_api", main)
    status = api.get_connection_status()
    assert status["status"] == "Connected (Fallback Mode)" and status["user_name"] == "Streamer"
    assert timing[-1] == (datetime(2030, 1, 1), True)
    main.user = None
    assert api.get_connection_status()["user_name"] == "Main Account"


def test_connection_status_dedicated(st, api, timing):
    api.is_connected, api.twitch = True, FakeClient()
    s = api.get_connection_status()
    assert s["status"] == "Connected (Dedicated)" and s["last_update"] == "Unknown" and s["user_name"] == "Unknown"
    api.last_health_check = datetime(2030, 1, 2, 3, 4, 5)
    api.user = st.user
    s = api.get_connection_status()
    assert s["last_update"] == "2030-01-02 03:04:05" and s["user_name"] == "Bot" and s["avatar_url"] == "http://x/p.png"
    api.is_connected = False
    s = api.get_connection_status()
    assert s["status"] == "Authenticated but Disconnected" and s["user_name"] == "Bot"
    api.user = None
    assert api.get_connection_status()["user_name"] == "Unknown"
    api.auth_token = ""
    assert api.get_connection_status()["status"] == "Configured but Not Authenticated"
    api.client_secret = ""
    assert api.get_connection_status()["status"] == "Not Configured (Using Fallback)"
    assert timing[-1] == (None, False)


def test_token_status(st, api):
    s = api.get_token_status()
    assert s == {"has_auth_token": True, "has_refresh_token": True, "token_expiry": None, "is_expired": False,
                 "expires_in_minutes": None, "last_refresh_attempt": None, "is_connected": False, "user_id": ""}
    api.token_expiry = datetime.now() + timedelta(minutes=90, seconds=30)
    api._last_refresh_attempt = datetime(2030, 1, 1)
    s = api.get_token_status()
    assert s["expires_in_minutes"] in (89, 90) and s["last_refresh_attempt"] == "2030-01-01T00:00:00"


def test_token_status_error(st, api):
    def boom():
        raise RuntimeError("bad expiry")

    st.expired = boom
    assert api.get_token_status() == {"error": "bad expiry"}
