# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/psnapi.py PSNClient with a fake PSNAWP (no network)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from psnawp_api.core.psnawp_exceptions import (  # noqa: E402
    PSNAWPAuthenticationError,
    PSNAWPBadRequestError,
    PSNAWPForbiddenError,
    PSNAWPNotFoundError,
    PSNAWPServerError,
)
from psnawp_api.models.trophies import PlatformType  # noqa: E402
from requests.exceptions import ConnectionError as RequestsConnectionError  # noqa: E402

from modules import psnapi  # noqa: E402

PREFIX = psnapi.PSN_GAME_DATA_PATH


# --------------------------------------------------------------------------- fakes


class FakeDB:
    def __init__(self, store=None, set_ok=True, raise_on_set=False):
        self.store = dict(store or {})
        self.set_ok = set_ok
        self.raise_on_set = raise_on_set
        self.sets = []
        self.deletes = []

    def get_config(self):
        return SimpleNamespace(database_type="sql")

    def get_data(self, path):
        return self.store.get(path)

    def get_data_checked(self, path):
        return self.store.get(path), True

    def get_all_paths(self):
        return list(self.store)

    def set_data(self, path, value):
        if self.raise_on_set:
            raise RuntimeError("set fail")
        self.sets.append((path, value))
        if self.set_ok:
            self.store[path] = value
        return self.set_ok

    def delete_data(self, path):
        self.deletes.append(path)
        return self.store.pop(path, None) is not None


class FakeUser:
    def __init__(self, api, kw):
        self.api = api
        self.kw = kw
        self.account_id = api.target_account_ids.get(kw.get("online_id"), "acc-" + str(kw.get("online_id")))

    def _maybe_raise(self, name):
        r = self.api.raises.get(name)
        if isinstance(r, list):
            r = r.pop(0) if r else None
        if r is not None:
            raise r

    def get_presence(self):
        self._maybe_raise("get_presence")
        return self.api.presence

    def trophy_titles(self, limit=None, offset=0, page_size=800):
        self._maybe_raise("trophy_titles")
        self.api.title_calls.append((offset, page_size))
        pages = self.api.title_pages
        return pages.pop(0) if pages else []

    def trophy_summary(self):
        self._maybe_raise("trophy_summary")
        return self.api.summary

    def trophy_groups_summary(self, np_communication_id, platform, include_progress):
        self._maybe_raise("trophy_groups_summary")
        self.api.group_calls.append((np_communication_id, platform, include_progress))
        return self.api.groups_summary

    def trophy_titles_for_title(self, title_ids):
        self._maybe_raise("trophy_titles_for_title")
        self.api.for_title_calls.append(title_ids)
        return self.api.titles_for_title


class FakeAPI:
    def __init__(self, npsso="tok"):
        self.npsso = npsso
        self.authenticator = "AUTH"
        self.me_value = SimpleNamespace(account_id="acc-me", online_id="me")
        self.me_exc = None
        self.user_exc = None
        self.raises = {}
        self.presence = None
        self.title_pages = []
        self.title_calls = []
        self.summary = None
        self.groups_summary = None
        self.group_calls = []
        self.titles_for_title = []
        self.for_title_calls = []
        self.user_calls = []
        self.target_account_ids = {}

    def me(self):
        if self.me_exc:
            raise self.me_exc
        return self.me_value

    def user(self, **kw):
        self.user_calls.append(kw)
        exc = self.user_exc
        if isinstance(exc, list):
            exc = exc.pop(0) if exc else None
        if exc is not None:
            raise exc
        return FakeUser(self, kw)


class APIFactory:
    """Replaces PSNAWP: returns the next prepared FakeAPI (or raises)."""

    def __init__(self, *apis):
        self.apis = list(apis)
        self.created = []

    def __call__(self, npsso):
        self.created.append(npsso)
        nxt = self.apis.pop(0) if len(self.apis) > 1 else self.apis[0]
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(psnapi, "_game_cache_by_title_id", {})
    monkeypatch.setattr(psnapi, "_game_cache_by_name", {})
    monkeypatch.setattr(psnapi, "_game_cache_by_comm_id", {})
    monkeypatch.setattr(psnapi, "_game_cache_index_loaded", False)
    monkeypatch.setattr(psnapi, "_fuzzy_name_memo", {})
    db = FakeDB()
    monkeypatch.setattr(psnapi, "database_manager", db)
    from modules import notification_engine, psn_service

    notes = SimpleNamespace(expired=[], cleared=0, forbidden=[])
    monkeypatch.setattr(psn_service, "notify_psn_npsso_expired", lambda code="": notes.expired.append(code))

    def _clear():
        notes.cleared += 1

    monkeypatch.setattr(psn_service, "clear_psn_npsso_expired_notice", _clear)
    monkeypatch.setattr(notification_engine, "notify", lambda msg, **kw: notes.forbidden.append(msg))
    return SimpleNamespace(db=db, notes=notes)


def make_client(api=None, username=None, connected=True, monkeypatch=None):
    c = psnapi.PSNClient(npsso_code="tok", psn_username=username)
    if connected:
        c.api = api or FakeAPI()
        c.authenticated = True
        c.account_id = "acc-me"
        c.user_online_id = "me"
    return c


def err(cls, msg="x"):
    return cls(msg)


# --------------------------------------------------------------------------- init / credentials


def test_init_defaults_and_npsso_copied():
    c = psnapi.PSNClient()
    assert c.npsso_code is None and c.psn_data.npsso_code is None
    assert c.authenticated is False and c.api is None
    c2 = psnapi.PSNClient("abc", "friend")
    assert c2.psn_data.npsso_code == "abc" and c2.psn_username == "friend"


def test_update_npsso_code_resets_state():
    c = make_client()
    c._auth_expired = c._auth_expired_warned = True
    c._cached_target_account_id, c._cached_target_username_key = "a", "b"
    c.update_npsso_code("new")
    assert c.npsso_code == "new" and c.psn_data.npsso_code == "new"
    assert c.api is None and c.authenticated is False and c._auth_expired is False
    assert c._auth_expired_warned is False and c._cached_target_account_id is None
    assert c.psn_data.connection_status == "Disconnected"
    c.update_npsso_code("  ")
    assert c.psn_data.connection_status == "Not Connected"
    c.update_npsso_code(None)
    assert c.psn_data.connection_status == "Not Connected"


def test_update_psn_username_clears_target_cache():
    c = make_client()
    c._cached_target_account_id = "x"
    c.update_psn_username("pal")
    assert c.psn_username == "pal" and c._cached_target_account_id is None


def test_update_credentials():
    c = make_client()
    c._auth_expired = True
    c.update_credentials("n2", "pal")
    assert (c.npsso_code, c.psn_username, c.psn_data.npsso_code) == ("n2", "pal", "n2")
    assert c.api is None and not c.authenticated and not c._auth_expired
    assert c.psn_data.connection_status == "Disconnected"
    c.update_credentials("", "pal")
    assert c.psn_data.connection_status == "Not Connected"


# --------------------------------------------------------------------------- cache helpers on client


def test_store_game_data(isolate):
    c = make_client()
    assert c.store_game_data({"presence_name": "x"}) is False
    d = {"np_communication_id": "NPWR1_00", "np_title_id": "CUSA1", "presence_name": "Game"}
    assert c.store_game_data(d) is True
    assert "last_updated" in d
    assert isolate.db.sets[-1][0] == f"{PREFIX}/NPWR1_00"
    assert psnapi._game_cache_by_comm_id == {}  # index not loaded: untouched
    psnapi._game_cache_index_loaded = True
    d2 = {"np_communication_id": "NPWR2_00", "presence_name": "Two"}
    assert c.store_game_data(d2) is True
    assert psnapi._game_cache_by_comm_id["NPWR2_00"] is d2
    isolate.db.set_ok = False
    assert c.store_game_data({"np_communication_id": "NPWR3_00"}) is False
    isolate.db.raise_on_set = True
    assert c.store_game_data({"np_communication_id": "NPWR3_00"}) is False


def test_cached_game_data_wrappers(isolate):
    isolate.db.store[f"{PREFIX}/NPWR1_00"] = {"np_communication_id": "NPWR1_00", "trophy_name": "T"}
    c = make_client()
    assert c.get_cached_game_data("NPWR1_00")["trophy_name"] == "T"
    assert c.get_cached_game_data_checked("NPWR1_00")[1] is True
    assert [g["np_communication_id"] for g in c.get_all_cached_games()] == ["NPWR1_00"]
    assert c.update_game_cache_entry("NPWR1_00", {"presence_name": "P"}) is True
    written = c.update_game_cache_entry_from("NPWR1_00", {"a": 1}, {"np_communication_id": "NPWR1_00"})
    assert written["a"] == 1
    assert c.delete_game_cache_entry("NPWR1_00") is True
    assert c.delete_game_cache_entry("NPWR1_00") is False


def test_find_game_by_np_title_id(isolate, monkeypatch):
    isolate.db.store[f"{PREFIX}/NPWR1_00"] = {"np_communication_id": "NPWR1_00", "np_title_id": "CUSA1",
                                              "presence_name": "Game"}
    c = make_client()
    assert c.find_game_by_np_title_id("") is None
    assert c.find_game_by_np_title_id("CUSA1")["np_communication_id"] == "NPWR1_00"
    assert c.find_game_by_np_title_id("CUSA9") is None
    monkeypatch.setattr(psnapi, "_ensure_game_cache_index", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert c.find_game_by_np_title_id("CUSA1") is None


def test_find_game_by_name(isolate, monkeypatch):
    isolate.db.store[f"{PREFIX}/NPWR1_00"] = {"np_communication_id": "NPWR1_00", "presence_name": "Returnal",
                                              "trophy_name": "Returnal™ Trophies", "platform": "PS5"}
    isolate.db.store[f"{PREFIX}/NPWR2_00"] = {"np_communication_id": "NPWR2_00",
                                              "trophy_name": "Horizon Forbidden West"}
    c = make_client()
    assert c.find_game_by_name("") is None
    assert c.find_game_by_name("RETURNAL")["np_communication_id"] == "NPWR1_00"  # lower key
    assert c.find_game_by_name("Returnal™")["np_communication_id"] == "NPWR1_00"  # normalized
    assert c.find_game_by_name("Horizon Forbiden West", "PS5")["np_communication_id"] == "NPWR2_00"  # fuzzy
    assert c.find_game_by_name("Netflix") is None
    assert c.find_game_by_name("!!!") is None  # normalized key empty -> skipped
    monkeypatch.setattr(psnapi, "_ensure_game_cache_index", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert c.find_game_by_name("Returnal") is None


# --------------------------------------------------------------------------- connect


def test_connect_without_npsso():
    c = psnapi.PSNClient()
    assert c.connect() is False
    assert c.psn_data.connection_status == "Not Connected"


def test_connect_skips_when_expired(monkeypatch):
    c = psnapi.PSNClient("tok")
    c._auth_expired = True
    monkeypatch.setattr(psnapi, "PSNAWP", lambda n: pytest.fail("must not build API"))
    assert c.connect() is False


def test_connect_already_authenticated(monkeypatch):
    c = make_client()
    monkeypatch.setattr(psnapi, "PSNAWP", lambda n: pytest.fail("must not rebuild"))
    assert c.connect() is True


def test_connect_success(monkeypatch, isolate):
    api = FakeAPI()
    factory = APIFactory(api)
    monkeypatch.setattr(psnapi, "PSNAWP", factory)
    c = psnapi.PSNClient("tok")
    assert c.connect() is True
    assert factory.created == ["tok"]
    assert (c.account_id, c.user_online_id) == ("acc-me", "me")
    assert (c.psn_data.account_id, c.psn_data.online_id) == ("acc-me", "me")
    assert c.psn_data.connection_status == "Connected" and c.is_connected()
    assert isolate.notes.cleared == 1


def test_connect_success_even_if_clear_notice_fails(monkeypatch):
    from modules import psn_service

    monkeypatch.setattr(psnapi, "PSNAWP", APIFactory(FakeAPI()))
    monkeypatch.setattr(psn_service, "clear_psn_npsso_expired_notice",
                        lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert psnapi.PSNClient("tok").connect() is True


def test_connect_auth_error_marks_expired(monkeypatch, isolate):
    monkeypatch.setattr(psnapi, "PSNAWP", APIFactory(err(PSNAWPAuthenticationError)))
    c = psnapi.PSNClient("tok")
    assert c.connect() is False
    assert c.is_auth_expired() and c.psn_data.connection_status == "Token Expired"
    assert isolate.notes.expired == ["tok"]
    assert c._auth_expired_warned is True


@pytest.mark.parametrize("exc_cls", [PSNAWPNotFoundError, PSNAWPBadRequestError, RuntimeError])
def test_connect_other_errors(monkeypatch, exc_cls, isolate):
    api = FakeAPI()
    api.me_exc = exc_cls("bad")
    monkeypatch.setattr(psnapi, "PSNAWP", APIFactory(api))
    c = psnapi.PSNClient("tok")
    assert c.connect() is False
    assert c.psn_data.connection_status == "Authentication Failed" and not c.authenticated
    assert isolate.notes.forbidden == []


def test_connect_forbidden_notifies(monkeypatch, isolate):
    monkeypatch.setattr(psnapi, "PSNAWP", APIFactory(err(PSNAWPForbiddenError)))
    c = psnapi.PSNClient("tok")
    assert c.connect() is False
    assert c.psn_data.connection_status == "Authentication Failed"
    assert "your trophy data" in isolate.notes.forbidden[0]


# --------------------------------------------------------------------------- auth expiry / status


def test_on_auth_expired_warns_once_and_notification_failure_swallowed(monkeypatch, isolate):
    from modules import psn_service

    c = make_client()
    c._cached_target_account_id = "x"
    c._on_psn_auth_expired("ctx", RuntimeError("expired token"))
    c._on_psn_auth_expired("ctx", RuntimeError("expired token"))
    assert isolate.notes.expired == ["tok", "tok"]
    assert c.api is None and c._cached_target_account_id is None
    monkeypatch.setattr(psn_service, "notify_psn_npsso_expired", lambda code: (_ for _ in ()).throw(RuntimeError()))
    c.npsso_code = None
    c._on_psn_auth_expired("ctx", RuntimeError("x"))  # no raise


def test_sync_presence_connection_status():
    c = psnapi.PSNClient("tok")
    c.psn_data.connection_status = "Disconnected"
    c._sync_presence_connection_status()
    assert c.psn_data.connection_status == "Disconnected"
    c.authenticated = True
    c._sync_presence_connection_status()
    assert c.psn_data.connection_status == "Offline"
    c.psn_data.is_online = True
    c._sync_presence_connection_status()
    assert c.psn_data.connection_status == "Connected"


# --------------------------------------------------------------------------- get_presence


def test_presence_not_connected_and_connect_fails(monkeypatch):
    c = psnapi.PSNClient("tok")
    monkeypatch.setattr(c, "connect", lambda: False)
    assert c.get_presence() is None


def test_presence_no_target():
    c = make_client()
    c.user_online_id = None
    assert c.get_presence() is None


def test_presence_reconnects_then_fetches(monkeypatch):
    api = FakeAPI()
    api.presence = {"basicPresence": {"availability": "availableToPlay"}}
    monkeypatch.setattr(psnapi, "PSNAWP", APIFactory(api))
    c = psnapi.PSNClient("tok", "pal")
    assert c.get_presence() == api.presence
    assert api.user_calls == [{"online_id": "pal"}]
    assert c.psn_data.is_online is True and c.psn_data.connection_status == "Connected"


def test_presence_online_with_game():
    api = FakeAPI()
    api.presence = {"basicPresence": {
        "availability": "available",
        "gameTitleInfoList": [{"titleName": "Returnal", "conceptIconUrl": "c.png", "npTitleId": "PPSA1",
                               "format": "PS5"}],
    }}
    c = make_client(api)
    assert c.get_presence() is api.presence
    assert api.user_calls == [{"online_id": "me"}]
    assert c.psn_data.current_game_name == "Returnal"
    assert c.psn_data.current_game_art_url == "c.png"
    assert c.psn_data.presence is api.presence


@pytest.mark.parametrize("keys,expected", [
    ({"npTitleIconUrl": "a", "conceptIconUrl": "b"}, "a"),
    ({"titleIconUrl": "t"}, "t"),
    ({}, None),
])
def test_presence_icon_fallbacks(keys, expected):
    api = FakeAPI()
    api.presence = {"availability": "available", "gameTitleInfoList": [{"titleName": "G", **keys}]}
    c = make_client(api)
    c.get_presence()
    assert c.psn_data.current_game_art_url == expected


def test_presence_online_via_platform_info_without_game():
    api = FakeAPI()
    api.presence = {"basicPresence": {"availability": "unavailable",
                                      "primaryPlatformInfo": {"onlineStatus": "online"},
                                      "gameTitleInfoList": []}}
    c = make_client(api)
    c.psn_data.current_game_name = "Old"
    c.psn_data.current_game_trophies = {"x": 1}
    c.psn_data.current_game_progress = 5
    c.get_presence()
    assert c.psn_data.is_online is True
    assert c.psn_data.current_game_name is None and c.psn_data.current_game_trophies == {}
    assert c.psn_data.current_game_progress is None


def test_presence_offline_root_level():
    api = FakeAPI()
    api.presence = {"availability": "unavailable"}
    c = make_client(api)
    c.psn_data.is_online = True
    c.get_presence()
    assert c.psn_data.is_online is False and c.psn_data.connection_status == "Offline"


@pytest.mark.parametrize("presence", [[], {"basicPresence": None}, {"basicPresence": "str"}, "weird"])
def test_presence_invalid_structure_clears(presence):
    api = FakeAPI()
    api.presence = presence
    c = make_client(api)
    c.psn_data.current_game_name = "Old"
    c.psn_data.is_online = True
    c.psn_data.current_game_trophies_all = {"a": 1}
    assert c.get_presence() == presence
    assert c.psn_data.is_online is False and c.psn_data.current_game_name is None
    assert c.psn_data.current_game_trophies_all == {}


@pytest.mark.parametrize("exc_cls", [PSNAWPForbiddenError, PSNAWPNotFoundError, PSNAWPBadRequestError])
def test_presence_api_errors_keep_auth(exc_cls):
    api = FakeAPI()
    api.raises["get_presence"] = exc_cls("x")
    c = make_client(api)
    assert c.get_presence() is None
    assert c.authenticated is True


@pytest.mark.parametrize("exc", [RequestsConnectionError("x"), OSError("x")])
def test_presence_network_error_reconnects_and_retries_once(monkeypatch, exc):
    api = FakeAPI()
    api.raises["get_presence"] = [exc]
    api.presence = {"availability": "available"}
    monkeypatch.setattr(psnapi, "PSNAWP", APIFactory(api))
    c = make_client(api)
    assert c.get_presence() == api.presence
    assert c.authenticated is True


def test_presence_network_error_no_retry_when_connect_fails(monkeypatch):
    api = FakeAPI()
    api.raises["get_presence"] = OSError("x")
    c = make_client(api)
    monkeypatch.setattr(c, "connect", lambda: False)
    assert c.get_presence() is None
    assert c.authenticated is False and c.api is None


def test_presence_network_error_without_retry_flag():
    api = FakeAPI()
    api.raises["get_presence"] = OSError("x")
    c = make_client(api)
    assert c.get_presence(_allow_retry=False) is None
    assert c.api is None


def test_presence_auth_expired(isolate):
    api = FakeAPI()
    api.raises["get_presence"] = PSNAWPAuthenticationError("x")
    c = make_client(api)
    # Authentication error is a PSNAWPClientError, not caught by the explicit tuple -> generic branch
    assert c.get_presence() is None
    assert c.is_auth_expired() and isolate.notes.expired == ["tok"]


def test_presence_server_error_retries(monkeypatch):
    api = FakeAPI()
    api.raises["get_presence"] = [PSNAWPServerError("x")]
    api.presence = {"availability": "available"}
    monkeypatch.setattr(psnapi, "PSNAWP", APIFactory(api))
    c = make_client(api)
    assert c.get_presence() == api.presence


def test_presence_server_error_connect_fails(monkeypatch):
    api = FakeAPI()
    api.raises["get_presence"] = PSNAWPServerError("x")
    c = make_client(api)
    monkeypatch.setattr(c, "connect", lambda: False)
    assert c.get_presence() is None and c.api is None


def test_presence_unexpected_error():
    api = FakeAPI()
    api.raises["get_presence"] = ValueError("weird")
    c = make_client(api)
    assert c.get_presence() is None
    assert c.authenticated is False


# --------------------------------------------------------------------------- trophy target account


def test_target_account_cached():
    c = make_client()
    c._cached_target_account_id = "cached"
    c._cached_target_username_key = ""
    assert c.get_trophy_target_account_id() == "cached"


def test_target_account_self_and_cache_stored():
    c = make_client()
    assert c.get_trophy_target_account_id() == "acc-me"
    assert c._cached_target_account_id == "acc-me" and c._cached_target_username_key == ""


def test_target_account_for_username():
    api = FakeAPI()
    api.target_account_ids["pal"] = "acc-pal"
    c = make_client(api, username="pal")
    assert c.get_trophy_target_account_id() == "acc-pal"
    assert c.get_trophy_target_account_id() == "acc-pal"
    assert api.user_calls == [{"online_id": "pal"}]


def test_target_account_not_connected_connect_fails(monkeypatch):
    c = psnapi.PSNClient("tok")
    monkeypatch.setattr(c, "connect", lambda: False)
    assert c.get_trophy_target_account_id() is None


def test_target_account_none_available():
    c = make_client()
    c.account_id = None
    assert c.get_trophy_target_account_id() is None


def test_target_account_auth_expired(isolate):
    api = FakeAPI()
    api.user_exc = PSNAWPAuthenticationError("x")
    c = make_client(api, username="pal")
    assert c.get_trophy_target_account_id() is None
    assert c.is_auth_expired()


def test_target_account_connection_error_retries(monkeypatch):
    api = FakeAPI()
    api.user_exc = [OSError("net")]
    monkeypatch.setattr(psnapi, "PSNAWP", APIFactory(api))
    c = make_client(api, username="pal")
    assert c.get_trophy_target_account_id() == "acc-pal"


def test_target_account_connection_error_connect_fails(monkeypatch):
    api = FakeAPI()
    api.user_exc = OSError("net")
    c = make_client(api, username="pal")
    monkeypatch.setattr(c, "connect", lambda: False)
    assert c.get_trophy_target_account_id() is None
    assert c.api is None


def test_target_account_other_error():
    api = FakeAPI()
    api.user_exc = PSNAWPNotFoundError("no user")
    c = make_client(api, username="pal")
    assert c.get_trophy_target_account_id() is None
    assert c.authenticated is True


# --------------------------------------------------------------------------- np_title_id -> comm id


class FakeTTI:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result, exc, []

    def get_np_communication_id(self, auth, title_id, account_id):
        self.calls.append((auth, title_id, account_id))
        if self.exc:
            raise self.exc
        return self.result


def test_resolve_comm_id_guards(monkeypatch):
    c = make_client()
    assert c.resolve_np_communication_id_from_np_title_id("") is None
    c.api = None
    assert c.resolve_np_communication_id_from_np_title_id("CUSA1") is None


def test_resolve_comm_id_not_connected_connect_fails(monkeypatch):
    c = make_client()
    c.authenticated = False
    monkeypatch.setattr(c, "connect", lambda: False)
    assert c.resolve_np_communication_id_from_np_title_id("CUSA1") is None


def test_resolve_comm_id_no_account(monkeypatch):
    c = make_client()
    monkeypatch.setattr(c, "get_trophy_target_account_id", lambda: None)
    assert c.resolve_np_communication_id_from_np_title_id("CUSA1") is None


@pytest.mark.parametrize("result,exc,expected", [
    ("NPWR1_00", None, "NPWR1_00"),
    ("", None, None),
    (None, PSNAWPNotFoundError("x"), None),
    (None, PSNAWPBadRequestError("x"), None),
    (None, RuntimeError("x"), None),
])
def test_resolve_comm_id(monkeypatch, result, exc, expected):
    tti = FakeTTI(result, exc)
    monkeypatch.setattr(psnapi, "TrophyTitleIterator", tti)
    c = make_client()
    assert c.resolve_np_communication_id_from_np_title_id("CUSA1") == expected
    assert tti.calls == [("AUTH", "CUSA1", "acc-me")]


# --------------------------------------------------------------------------- get_all_games


class Page(list):
    """trophy_titles result: an iterable with optional _total_item_count."""


def _title(cid, name="G", earned=True, defined=True):
    return SimpleNamespace(
        np_communication_id=cid, title_name=name, title_platform="PS5", title_icon_url="i.png",
        earned_trophies=SimpleNamespace(bronze=1, silver=2, gold=3, platinum=0) if earned else None,
        defined_trophies=SimpleNamespace(bronze=10, silver=5, gold=2, platinum=1) if defined else None,
        progress=42,
    )


def test_all_games_no_account(monkeypatch):
    c = make_client()
    monkeypatch.setattr(c, "get_trophy_target_account_id", lambda: None)
    assert c.get_all_games() is None


def test_all_games_single_page():
    api = FakeAPI()
    api.title_pages = [Page([_title("A"), _title("B", earned=False, defined=False),
                             SimpleNamespace(np_communication_id=None)])]
    c = make_client(api, username=None)
    out = c.get_all_games()
    assert set(out) == {"A", "B"}
    assert out["A"]["earned_trophies"] == {"bronze": 1, "silver": 2, "gold": 3, "platinum": 0}
    assert out["A"]["defined_trophies"]["platinum"] == 1 and out["A"]["progress"] == 42
    assert out["B"]["earned_trophies"] == {} and out["B"]["defined_trophies"] == {}
    assert c.psn_data.all_games_data is out
    assert api.user_calls == [{"account_id": "acc-me"}]


def test_all_games_paginates():
    api = FakeAPI()
    api.target_account_ids["pal"] = "acc-pal"
    api.title_pages = [Page([_title(f"G{i}") for i in range(800)]), Page([_title("LAST")])]
    c = make_client(api, username="pal")
    out = c.get_all_games()
    assert api.title_calls == [(0, 800), (800, 800)]
    assert len(out) == 801 and "LAST" in out
    assert api.user_calls[-1] == {"account_id": "acc-pal"}


@pytest.mark.parametrize("page", [Page(), None])
def test_all_games_empty_responses(page):
    api = FakeAPI()
    api.title_pages = [page]
    c = make_client(api)
    assert c.get_all_games() == {}


def test_all_games_total_item_count_zero():
    api = FakeAPI()
    p = Page([_title("A")])
    p._total_item_count = 0
    api.title_pages = [p]
    assert make_client(api).get_all_games() == {}


def test_all_games_iteration_failure():
    class BadIter:
        def __init__(self):
            self.x = 1

        def __bool__(self):
            return True

        def __iter__(self):
            raise RuntimeError("cannot iterate")

    api = FakeAPI()
    api.title_pages = [BadIter()]
    assert make_client(api).get_all_games() == {}


def test_all_games_iteration_failure_without_dict():
    class Slotted:
        __slots__ = ()

        def __bool__(self):
            return True

        def __iter__(self):
            raise RuntimeError("cannot iterate")

    api = FakeAPI()
    api.title_pages = [Slotted()]
    assert make_client(api).get_all_games() == {}


@pytest.mark.parametrize("username", [None, "pal"])
@pytest.mark.parametrize("exc_cls", [PSNAWPForbiddenError, PSNAWPNotFoundError, PSNAWPBadRequestError, RuntimeError])
def test_all_games_errors(exc_cls, username, isolate):
    api = FakeAPI()
    api.raises["trophy_titles"] = exc_cls("x")
    c = make_client(api, username=username)
    assert c.get_all_games() is None
    if exc_cls is PSNAWPForbiddenError:
        assert len(isolate.notes.forbidden) == 1
        assert ("pal's" if username else "your") in isolate.notes.forbidden[0]
    else:
        assert isolate.notes.forbidden == []


# --------------------------------------------------------------------------- trophy summary


def test_summary_not_connected(monkeypatch):
    c = psnapi.PSNClient("tok")
    monkeypatch.setattr(c, "connect", lambda: False)
    assert c.get_overall_trophy_summary() is None


def test_summary_no_account(monkeypatch):
    c = make_client()
    monkeypatch.setattr(c, "get_trophy_target_account_id", lambda _allow_retry=True: None)
    assert c.get_overall_trophy_summary() is None


@pytest.mark.parametrize("summary,expected", [
    (SimpleNamespace(earned_trophies={"bronze": 1}), {"bronze": 1}),
    (SimpleNamespace(earned_trophies=SimpleNamespace(bronze=1, silver=2, gold=3, platinum=4)),
     {"bronze": 1, "silver": 2, "gold": 3, "platinum": 4}),
    (SimpleNamespace(earnedTrophies={"gold": 9}), {"gold": 9}),
    (SimpleNamespace(bronze=5, gold=1), {"bronze": 5, "silver": 0, "gold": 1, "platinum": 0}),
])
def test_summary_shapes(summary, expected):
    api = FakeAPI()
    api.summary = summary
    c = make_client(api, username="pal")
    assert c.get_overall_trophy_summary() is summary
    assert c.psn_data.trophy_counts == expected


def test_summary_none_returned():
    api = FakeAPI()
    api.summary = None
    c = make_client(api)
    c.psn_data.trophy_counts = {"x": 1}
    assert c.get_overall_trophy_summary() is None
    assert c.psn_data.trophy_counts == {}


@pytest.mark.parametrize("with_dict", [True, False])
def test_summary_extract_failure(with_dict):
    class Boom:
        @property
        def earned_trophies(self):
            raise RuntimeError("bad attr")

    class SlotBoom:
        __slots__ = ()

        @property
        def earned_trophies(self):
            raise RuntimeError("bad attr")

    api = FakeAPI()
    # hasattr() swallows only AttributeError, so the RuntimeError propagates into the inner except
    api.summary = Boom() if with_dict else SlotBoom()
    c = make_client(api)
    c.psn_data.trophy_counts = {"x": 1}
    assert c.get_overall_trophy_summary() is api.summary
    assert c.psn_data.trophy_counts == {}


@pytest.mark.parametrize("exc_cls", [PSNAWPForbiddenError, PSNAWPNotFoundError, PSNAWPBadRequestError])
def test_summary_api_errors(exc_cls):
    api = FakeAPI()
    api.raises["trophy_summary"] = exc_cls("x")
    c = make_client(api)
    assert c.get_overall_trophy_summary() is None and c.authenticated


def test_summary_auth_expired(isolate):
    api = FakeAPI()
    api.raises["trophy_summary"] = RuntimeError("Expired Token")
    c = make_client(api, username="pal")
    assert c.get_overall_trophy_summary() is None
    assert c.is_auth_expired()


def test_summary_connection_error_retries(monkeypatch):
    api = FakeAPI()
    api.raises["trophy_summary"] = [TimeoutError("t")]
    api.summary = SimpleNamespace(earned_trophies={"bronze": 2})
    monkeypatch.setattr(psnapi, "PSNAWP", APIFactory(api))
    c = make_client(api)
    assert c.get_overall_trophy_summary() is api.summary


def test_summary_connection_error_connect_fails(monkeypatch):
    api = FakeAPI()
    api.raises["trophy_summary"] = TimeoutError("t")
    c = make_client(api)
    c._cached_target_account_id, c._cached_target_username_key = "acc-me", ""
    monkeypatch.setattr(c, "connect", lambda: False)
    assert c.get_overall_trophy_summary() is None and c.api is None


def test_summary_unexpected_error():
    api = FakeAPI()
    api.raises["trophy_summary"] = ValueError("x")
    c = make_client(api)
    assert c.get_overall_trophy_summary() is None and c.authenticated is False


# --------------------------------------------------------------------------- trophy groups (cache shape)


def test_groups_not_connected(monkeypatch):
    c = psnapi.PSNClient("tok")
    monkeypatch.setattr(c, "connect", lambda: False)
    assert c.get_game_trophy_groups("NPWR1", "PS5") is None


def test_groups_no_account(monkeypatch):
    c = make_client()
    monkeypatch.setattr(c, "get_trophy_target_account_id", lambda: None)
    assert c.get_game_trophy_groups("NPWR1", "PS5") is None


def test_groups_empty_summary():
    api = FakeAPI()
    api.groups_summary = None
    assert make_client(api).get_game_trophy_groups("NPWR1", "PS5") is None


def test_groups_object_response():
    api = FakeAPI()
    api.groups_summary = SimpleNamespace(trophy_groups=[
        SimpleNamespace(trophy_group_id="default", trophy_group_name="Base",
                        defined_trophies=SimpleNamespace(bronze=1, silver=2, gold=3, platinum=1)),
        SimpleNamespace(trophy_group_id=None, trophy_group_name=None, defined_trophies={"gold": 4}),
        SimpleNamespace(trophy_group_id="002", trophy_group_name="DLC2", defined_trophies=None),
        SimpleNamespace(trophy_group_id="003", trophy_group_name="DLC3", defined_trophies=["weird"]),
    ])
    out = make_client(api).get_game_trophy_groups("NPWR1", "ps4")
    assert api.group_calls == [("NPWR1", PlatformType.PS4, True)]
    assert out[0] == {"trophy_group_id": "default", "group_name": "Base", "is_base_game": True,
                      "defined_trophies": {"bronze": 1, "silver": 2, "gold": 3, "platinum": 1}}
    assert out[1]["trophy_group_id"] == "" and out[1]["group_name"] == "Unknown"
    assert out[1]["defined_trophies"] == {"bronze": 0, "silver": 0, "gold": 4, "platinum": 0}
    assert out[2]["defined_trophies"] == {"bronze": 0, "silver": 0, "gold": 0, "platinum": 0}
    assert out[3]["defined_trophies"] == {"bronze": 0, "silver": 0, "gold": 0, "platinum": 0}


def test_groups_dict_response():
    api = FakeAPI()
    api.groups_summary = {"trophyGroups": [
        {"trophyGroupId": "default", "trophyGroupName": "Base", "definedTrophies": {"bronze": 3}},
        {},
    ]}
    out = make_client(api).get_game_trophy_groups("NPWR1", "PS5")
    assert out[0]["is_base_game"] is True and out[0]["defined_trophies"]["bronze"] == 3
    assert out[1] == {"trophy_group_id": "", "group_name": "Unknown", "is_base_game": False,
                      "defined_trophies": {"bronze": 0, "silver": 0, "gold": 0, "platinum": 0}}


def test_groups_unknown_shape_gives_empty_list():
    api = FakeAPI()
    api.groups_summary = {"other": 1}
    assert make_client(api).get_game_trophy_groups("NPWR1", "PS5") == []


@pytest.mark.parametrize("exc_cls", [PSNAWPForbiddenError, PSNAWPNotFoundError, PSNAWPBadRequestError, RuntimeError])
def test_groups_errors(exc_cls):
    api = FakeAPI()
    api.raises["trophy_groups_summary"] = exc_cls("x")
    assert make_client(api).get_game_trophy_groups("NPWR1", "PS5") is None


# --------------------------------------------------------------------------- title trophy progress


def test_title_progress_guards(monkeypatch):
    c = make_client()
    assert c.get_title_trophy_progress("") is None
    c2 = psnapi.PSNClient("tok")
    monkeypatch.setattr(c2, "connect", lambda: False)
    assert c2.get_title_trophy_progress("CUSA1") is None
    monkeypatch.setattr(c, "get_trophy_target_account_id", lambda: None)
    assert c.get_title_trophy_progress("CUSA1") is None


def test_title_progress_no_titles():
    api = FakeAPI()
    api.titles_for_title = []
    assert make_client(api).get_title_trophy_progress("CUSA1") is None
    assert api.for_title_calls == [["CUSA1"]]


def test_title_progress_with_progress_value():
    api = FakeAPI()
    api.titles_for_title = [SimpleNamespace(
        np_communication_id="NPWR1", title_name="G", title_icon_url="i", progress=55,
        earned_trophies={"bronze": 1}, defined_trophies={"bronze": 2}, has_trophy_groups=True,
        title_platform="PS5")]
    c = make_client(api)
    out = c.get_title_trophy_progress("CUSA1")
    assert out["progress"] == 55 and out["np_communication_id"] == "NPWR1"
    assert out["has_trophy_groups"] is True and out["platform"] == "PS5"
    assert out["earned_trophies"]["total"] == 1 and out["defined_trophies"]["total"] == 2
    assert c.psn_data.current_game_np_comm_id == "NPWR1"
    assert c.psn_data.current_game_trophies == {"defined": out["defined_trophies"], "earned": out["earned_trophies"]}
    assert c.psn_data.current_game_trophies_all == c.psn_data.current_game_trophies


def test_title_progress_computed_and_zero():
    api = FakeAPI()
    api.titles_for_title = [SimpleNamespace(np_communication_id="N", earned_trophies={"bronze": 1},
                                            defined_trophies={"bronze": 3})]
    c = make_client(api)
    assert c.get_title_trophy_progress("CUSA1")["progress"] == 33
    api.titles_for_title = [SimpleNamespace(np_communication_id="N")]
    out = c.get_title_trophy_progress("CUSA1")
    assert out["progress"] == 0 and out["has_trophy_groups"] is False and out["title_name"] is None


@pytest.mark.parametrize("exc_cls", [PSNAWPForbiddenError, PSNAWPNotFoundError, PSNAWPBadRequestError])
def test_title_progress_api_errors(exc_cls):
    api = FakeAPI()
    api.raises["trophy_titles_for_title"] = exc_cls("x")
    c = make_client(api)
    assert c.get_title_trophy_progress("CUSA1") is None and c.authenticated


def test_title_progress_auth_expired(isolate):
    api = FakeAPI()
    api.raises["trophy_titles_for_title"] = RuntimeError("expired access token")
    c = make_client(api)
    assert c.get_title_trophy_progress("CUSA1") is None and c.is_auth_expired()


def test_title_progress_connection_error():
    api = FakeAPI()
    api.raises["trophy_titles_for_title"] = OSError("net")
    c = make_client(api)
    c._cached_target_account_id = "x"
    c._cached_target_username_key = ""
    assert c.get_title_trophy_progress("CUSA1") is None
    assert c.api is None and not c.authenticated and c._cached_target_account_id is None


def test_title_progress_unexpected():
    api = FakeAPI()
    api.raises["trophy_titles_for_title"] = ValueError("x")
    c = make_client(api)
    assert c.get_title_trophy_progress("CUSA1") is None and c.authenticated


# --------------------------------------------------------------------------- game trophies (per group)


def _group(gid, d, e):
    return SimpleNamespace(trophy_group_id=gid, trophy_group_name=gid,
                           defined_trophies=SimpleNamespace(**d), earned_trophies=SimpleNamespace(**e))


def test_game_trophies_guards(monkeypatch):
    c2 = psnapi.PSNClient("tok")
    monkeypatch.setattr(c2, "connect", lambda: False)
    assert c2.get_game_trophies("N", "PS5") is None
    c = make_client()
    monkeypatch.setattr(c, "get_trophy_target_account_id", lambda: None)
    assert c.get_game_trophies("N", "PS5") is None


def test_game_trophies_empty_summary():
    api = FakeAPI()
    api.groups_summary = None
    c = make_client(api)
    c.psn_data.current_game_trophies_all = {"x": 1}
    assert c.get_game_trophies("N", "PS5") is None
    assert c.psn_data.current_game_trophies_all == {}
    assert c.psn_data.current_game_trophies["defined"]["total"] == 0


def test_game_trophies_selected_group_progress():
    api = FakeAPI()
    groups = [
        _group("default", dict(bronze=10, silver=0, gold=0, platinum=0), dict(bronze=5, silver=0, gold=0, platinum=0)),
        _group("001", dict(bronze=0, silver=4, gold=0, platinum=0), dict(bronze=0, silver=4, gold=0, platinum=0)),
    ]
    api.groups_summary = SimpleNamespace(trophy_groups=groups)
    c = make_client(api, username="pal")
    out = c.get_game_trophies("NPWR1", "PS5", "001")
    assert out["trophy_groups_summary"] is api.groups_summary
    assert [g["trophy_group_id"] for g in out["trophy_groups"]] == ["default", "001"]
    assert c.psn_data.current_game_progress == 100
    assert c.psn_data.current_game_trophies["defined"]["silver"] == 4
    assert c.psn_data.current_game_trophies_all["defined"]["total"] == 14
    assert c.psn_data.current_game_trophies_all["earned"]["total"] == 9
    assert c.psn_data.current_game_np_comm_id == "NPWR1"


def test_game_trophies_missing_group_falls_back_to_first():
    api = FakeAPI()
    api.groups_summary = SimpleNamespace(trophy_groups=[
        _group("default", dict(bronze=4, silver=0, gold=0, platinum=0), dict(bronze=1, silver=0, gold=0, platinum=0))])
    c = make_client(api)
    c.get_game_trophies("N", "PS5", "nope")
    assert c.psn_data.current_game_progress == 25


def test_game_trophies_selected_has_no_defined_uses_all():
    api = FakeAPI()
    api.groups_summary = SimpleNamespace(trophy_groups=[
        SimpleNamespace(trophy_group_id="default", defined_trophies=None, earned_trophies=None),
        _group("001", dict(bronze=2, silver=0, gold=0, platinum=0), dict(bronze=1, silver=0, gold=0, platinum=0)),
    ])
    c = make_client(api)
    c.get_game_trophies("N", "PS5")
    assert c.psn_data.current_game_progress == 50


def test_game_trophies_no_groups_progress_zero():
    api = FakeAPI()
    api.groups_summary = SimpleNamespace(trophy_groups=None)
    c = make_client(api)
    out = c.get_game_trophies("N", "PS5")
    assert out["trophy_groups"] == [] and c.psn_data.current_game_progress == 0


@pytest.mark.parametrize("exc_cls", [PSNAWPForbiddenError, PSNAWPNotFoundError, PSNAWPBadRequestError])
def test_game_trophies_api_errors(exc_cls):
    api = FakeAPI()
    api.raises["trophy_groups_summary"] = exc_cls("x")
    c = make_client(api, username="pal")
    assert c.get_game_trophies("N", "PS5") is None and c.authenticated


def test_game_trophies_auth_expired(isolate):
    api = FakeAPI()
    api.raises["trophy_groups_summary"] = PSNAWPAuthenticationError("x")
    c = make_client(api)
    assert c.get_game_trophies("N", "PS5") is None and c.is_auth_expired()


def test_game_trophies_connection_error():
    api = FakeAPI()
    api.raises["trophy_groups_summary"] = RequestsConnectionError("x")
    c = make_client(api)
    assert c.get_game_trophies("N", "PS5") is None
    assert c.api is None and not c.authenticated


def test_game_trophies_unexpected():
    api = FakeAPI()
    api.raises["trophy_groups_summary"] = ValueError("x")
    c = make_client(api)
    assert c.get_game_trophies("N", "PS5") is None and c.authenticated


def test_all_games_truthy_response_that_yields_nothing():
    class TruthyEmpty:
        def __bool__(self):
            return True

        def __iter__(self):
            return iter(())

    api = FakeAPI()
    api.title_pages = [TruthyEmpty()]
    assert make_client(api).get_all_games() == {}
    assert api.title_calls == [(0, 800)]
