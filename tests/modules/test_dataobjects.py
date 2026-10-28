# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/dataobjects.py: dataclasses, helpers and StateManager.

The database is a fake installed on modules.database_manager (get_data_checked, set_data, ...);
the encryption key lives under tmp_path.
"""

from __future__ import annotations

import asyncio
import copy
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import database_manager as dbm
from modules import dataobjects as do
from modules import encryption_utils as eu

PATHS = {
    "twitch_data": "TwitchData", "app_settings": "AppSettings", "psn_settings_data": "PSNSettings",
    "spotify_data": "SpotifyData", "database_settings": "DatabaseSettings", "youtube_data": "YouTubeData",
    "chatbot_data": "ChatbotData", "obs_data": "OBSData", "discord_data": "DiscordData",
}


@pytest.fixture(autouse=True)
def tmp_key(tmp_path, monkeypatch):
    key = tmp_path / ".encryption_key"
    monkeypatch.setattr(eu, "_key_file_path", lambda: str(key))
    monkeypatch.setattr(eu, "_cached_key", None)
    monkeypatch.setattr(eu, "_cached_fernet", None)


class FakeDB:
    def __init__(self):
        self.data = {}
        self.writes = []
        self.read_fail = set()
        self.write_fail = {}  # path -> (status, reason) or None (no problem recorded)
        self.write_exc = {}
        self.startup_failed = set()
        self.config_updates = []
        self.up = True
        self.async_error = None

    def get_data_checked(self, path):
        if path in self.read_fail:
            return {}, False
        return copy.deepcopy(self.data.get(path, {})), True

    def set_data(self, path, data):
        self.writes.append((path, copy.deepcopy(data)))
        if path in self.write_exc:
            raise self.write_exc[path]
        if path in self.write_fail:
            dbm._write_tls.last_write = self.write_fail[path]
            return False
        dbm._write_tls.last_write = ("ok", "")
        self.data[path] = copy.deepcopy(data)
        return True

    async def get_multiple_data_async(self, paths):
        if self.async_error:
            raise self.async_error
        return {p: copy.deepcopy(self.data.get(p)) for p in paths}

    def update_config(self, **kw):
        self.config_updates.append(kw)


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(dbm, "get_data_checked", fake.get_data_checked)
    monkeypatch.setattr(dbm, "set_data", fake.set_data)
    monkeypatch.setattr(dbm, "get_multiple_data_async", fake.get_multiple_data_async)
    monkeypatch.setattr(dbm, "startup_read_failed", lambda p: p in fake.startup_failed)
    # database_manager has no module-level update_config (see the xfail below); give the
    # fake one so the code after that call can be exercised.
    monkeypatch.setattr(dbm, "update_config", fake.update_config, raising=False)
    mgr = types.SimpleNamespace(get_config=lambda: dbm.DatabaseConfig(database_type="firebase"))
    type(fake).manager = mgr
    monkeypatch.setattr(dbm, "database_manager", mgr)
    mgr._initialized = True
    dbm._write_tls.last_write = None
    dbm._write_tls.quiet = 0
    yield fake
    dbm._write_tls.last_write = None
    dbm._write_tls.quiet = 0


@pytest.fixture
def notes(monkeypatch):
    calls = []
    monkeypatch.setattr(do, "notify", lambda *a, **k: calls.append(("notify", a, k)))
    monkeypatch.setattr(do, "notify_critical", lambda *a, **k: calls.append(("critical", a, k)))
    return calls


@pytest.fixture
def sm(db, notes):
    m = do.StateManager()
    m.initialize()
    return m


enc = eu.encrypt_value
dec = eu.decrypt_value


# ======================================================================= helpers

@pytest.mark.parametrize("keys,expected", [
    ([], ""), (["twitch_data"], "Twitch"), (["twitch_data", "obs_data"], "OBS and Twitch"),
    (["twitch_data", "obs_data", "custom"], "OBS, Twitch and custom"),
])
def test_blob_names(keys, expected):
    assert do._blob_names(keys) == expected


def test_database_is_up_and_label(db):
    assert do._database_is_up() is True
    db.manager._initialized = False
    assert do._database_is_up() is False
    assert do._database_label() == "Firebase"


def test_database_is_up_and_label_errors(monkeypatch):
    monkeypatch.setattr(dbm, "database_manager", None)
    assert do._database_is_up() is False
    assert do._database_label() == "configured"


@pytest.mark.parametrize("value,blank", [(None, True), ("", True), ("  ", True), ("x", False), (0, False)])
def test_is_blank_secret(value, blank):
    assert do._is_blank_secret(value) is blank


def test_keep_existing_if_blank_secret():
    names = ("token",)
    assert do._keep_existing_if_blank_secret("token", "", "old", names) == "old"
    assert do._keep_existing_if_blank_secret("token", "new", "old", names) == "new"
    assert do._keep_existing_if_blank_secret("token", "", "", names) == ""
    assert do._keep_existing_if_blank_secret("other", "", "old", names) == ""


class TestWriteDiscordMerged:
    def test_failed_read_skips_write(self, db):
        db.read_fail.add("DiscordData")
        assert do._write_discord_data_merged({"bot_token": "t"}) is False
        assert db.writes == []

    def test_encrypts_new_plain_token_and_merges(self, db):
        db.data["DiscordData"] = {"bot_username": "bot", "x": 1}
        assert do._write_discord_data_merged({"bot_token": " tok ", "x": 2}) is True
        stored = db.data["DiscordData"]
        assert stored["bot_username"] == "bot" and stored["x"] == 2
        assert eu.is_encrypted(stored["bot_token"]) and dec(stored["bot_token"]) == "tok"

    def test_already_encrypted_token_kept(self, db):
        token = enc("abc")
        do._write_discord_data_merged({"bot_token": token})
        assert db.data["DiscordData"]["bot_token"] == token

    def test_blank_payload_keeps_existing_token(self, db):
        db.data["DiscordData"] = {"bot_token": "STORED"}
        do._write_discord_data_merged({"bot_token": ""})
        assert db.data["DiscordData"]["bot_token"] == "STORED"

    def test_no_tokens_anywhere(self, db):
        db.data["DiscordData"] = ["not a dict"]
        assert do._write_discord_data_merged(None) is True
        assert db.data["DiscordData"] == {"bot_token": ""}

    def test_write_failure(self, db):
        db.write_fail["DiscordData"] = ("failed", "")
        assert do._write_discord_data_merged({}) is False


# ======================================================================= dataclasses

def test_dataclass_defaults():
    assert do.TwitchData().alerts_enabled is True and do.TwitchData().log_level == "INFO"
    assert do.ChatbotData().auth_token == ""
    a, b = do.AppSettings(), do.AppSettings()
    assert a.notify_levels == {"error": True, "warning": True, "info": True, "success": True}
    a.notify_levels["error"] = False
    assert b.notify_levels["error"] is True  # independent default dicts
    assert do.PSNSettingsData().npsso_code == ""
    assert do.SpotifyData().track_name == "Nothing playing"
    assert do.DatabaseSettings().database_type == "sql"
    assert do.PSNData().trophy_counts == {}
    assert do.YouTubeChannelData().channel_id == ""
    assert do.YouTubeData().playlist_filter == [] and do.YouTubeData().live_chat_enabled
    assert do.OBSData().port == 4455
    assert do.DiscordData().go_live_channels == []
    assert do.DiscordData(go_live_channels=None).go_live_channels == []


def test_apply_database_connection_settings_with_fake_update(db):
    do._apply_database_connection_settings(do.DatabaseSettings(sql_database_path="x.db", connection_timeout=9))
    kw = db.config_updates[-1]
    assert kw["sql_database_path"] == "x.db" and kw["connection_timeout"] == 9
    assert kw["streamer_name"] == "mycelian" and "database_type" not in kw


@pytest.mark.xfail(strict=True, reason="BUG: dataobjects calls database_manager.update_config(), "
                                        "but modules.database_manager has no module-level update_config")
def test_apply_database_connection_settings_reaches_the_real_manager(monkeypatch):
    mgr = dbm.DatabaseManager()
    monkeypatch.setattr(dbm, "database_manager", mgr)
    do._apply_database_connection_settings(do.DatabaseSettings(sql_database_path="other.db"))
    assert mgr.get_config().sql_database_path == "other.db"


# ======================================================================= StateManager basics

def test_state_manager_defaults(db):
    m = do.StateManager()
    assert m._paths == PATHS
    assert set(m._state) == set(PATHS)
    assert not m._initialized and not m.has_changes()
    assert isinstance(m._live_psn_data, do.PSNData)


def test_initialize_loads_and_decrypts(db, notes):
    db.data.update({
        "TwitchData": {"client_id": enc("cid"), "auth_token": enc("tok"), "client_secret": "plain",
                       "refresh_token": enc("ref"), "junk": 1},
        "AppSettings": {"current_theme": "light", "version": "0.0.1", "build_number": "x", "junk": 2},
        "PSNSettings": {"psn_username": "me", "junk": 1},
        "SpotifyData": {"client_id": enc("sc"), "client_secret": enc("ss"), "access_token": enc("at"),
                        "refresh_token": enc("rt"), "market_country": "US"},
        "ChatbotData": {"auth_token": enc("cb"), "refresh_token": enc("cr"), "user_id": "u"},
        "DatabaseSettings": {"database_type": "firebase", "connection_timeout": 7, "junk": 1},
        "OBSData": {"password": enc("pw"), "port": "4460"},
        "DiscordData": {"bot_token": enc("bt"), "go_live_channels": None},
        "YouTubeData": {"api_key": enc("ak"), "access_token": enc("yat"), "refresh_token": "",
                        "oauth_client_id": enc("oid")},
    })
    m = do.StateManager()
    m.initialize()
    t = m.get_twitch_data()
    assert (t.client_id, t.client_secret, t.auth_token, t.refresh_token) == ("cid", "plain", "tok", "ref")
    a = m.get_app_settings()
    assert a.current_theme == "light" and a.version == do.AppSettings().version
    assert "version" not in m._state["app_settings"] and "build_date" not in m._state["app_settings"]
    assert m.get_psn_settings_data().psn_username == "me"
    s = m.get_spotify_data()
    assert (s.client_id, s.client_secret, s.access_token, s.refresh_token) == ("sc", "ss", "at", "rt")
    c = m.get_chatbot_data()
    assert (c.auth_token, c.refresh_token, c.user_id) == ("cb", "cr", "u")
    assert "user" not in m._state["chatbot_data"]
    d = m.get_database_settings()
    assert d.database_type == "firebase" and d.connection_timeout == 7
    o = m.get_obs_data()
    assert o.password == "pw" and o.port == 4460
    assert m.get_discord_data().bot_token == "bt" and m.get_discord_data().go_live_channels == []
    y = m.get_youtube_data()
    assert (y.api_key, y.access_token, y.oauth_client_id, y.refresh_token) == ("ak", "yat", "oid", "")
    assert "twitch" not in m._state["twitch_data"]
    assert db.config_updates and db.config_updates[0]["connection_timeout"] == 7
    assert notes == []
    # second initialize is a no-op
    db.data["AppSettings"] = {"current_theme": "x"}
    m.initialize()
    assert m.get_app_settings().current_theme == "light"


def test_getters_return_copies_and_lazy_initialize(db, notes):
    db.data["AppSettings"] = {"current_theme": "light"}
    for getter in ("get_twitch_data", "get_app_settings", "get_psn_settings_data", "get_spotify_data",
                   "get_database_settings", "get_youtube_data", "get_chatbot_data", "get_obs_data",
                   "get_discord_data"):
        m = do.StateManager()
        obj = getattr(m, getter)()
        assert m._initialized, getter
        internal = getattr(m, "_" + getter[4:])
        assert obj == internal and obj is not internal
    m = do.StateManager()
    a = m.get_app_settings()
    a.current_theme = "mutated"
    assert m.get_app_settings().current_theme == "light"


class TestUpdateLocalObjects:
    def _run(self, **state):
        m = do.StateManager()
        for k, v in state.items():
            m._state[k] = v
        m._update_local_objects()
        return m

    def test_defaults_when_empty(self):
        m = self._run()
        assert m._twitch_data == do.TwitchData() and m._obs_data == do.OBSData()
        assert m._state["obs_data"]["port"] == 4455
        assert m._state["discord_data"]["go_live_channels"] == []

    def test_youtube_migrations(self):
        m = self._run(youtube_data={"channel_url": "https://yt/c1", "playlist_filter": "a, b ,,c"})
        y = m._youtube_data
        assert y.channel_urls == "https://yt/c1"
        assert y.playlist_filter == ["a", "b", "c"]
        assert y.channels == {}

    def test_youtube_blank_playlist_and_existing_urls(self):
        m = self._run(youtube_data={"channel_url": "old", "channel_urls": "new", "playlist_filter": "  ",
                                    "channels": {"x": {}}})
        assert m._youtube_data.channel_urls == "new"
        assert m._youtube_data.playlist_filter == []
        assert m._youtube_data.channels == {"x": {}}

    def test_youtube_list_playlist_untouched(self):
        m = self._run(youtube_data={"playlist_filter": ["x"], "api_key": "plain"})
        assert m._youtube_data.playlist_filter == ["x"] and m._youtube_data.api_key == "plain"

    @pytest.mark.parametrize("port,expected", [("bad", 4455), (None, None), (4456, 4456), ([1], 4455)])
    def test_obs_port(self, port, expected):
        m = self._run(obs_data={"port": port, "host": "h"})
        assert m._obs_data.port == expected and m._obs_data.host == "h"

    def test_discord_channels_not_list(self):
        m = self._run(discord_data={"go_live_channels": "x", "bot_token": ""})
        assert m._discord_data.go_live_channels == []
        m = self._run(discord_data={"go_live_channels": [{"guild_id": "1"}]})
        assert m._discord_data.go_live_channels == [{"guild_id": "1"}]

    def test_discord_state_none(self):
        m = self._run(discord_data=None)
        assert m._discord_data == do.DiscordData()


# ======================================================================= async / preloaded init

class TestInitializeAsync:
    def test_loads(self, db, notes):
        db.data.update({"AppSettings": {"current_theme": "light"},
                        "DatabaseSettings": {"sql_database_path": "z.db"}})
        m = do.StateManager()
        asyncio.run(m.initialize_async())
        assert m._initialized and m.get_app_settings().current_theme == "light"
        assert db.config_updates[-1]["sql_database_path"] == "z.db"
        asyncio.run(m.initialize_async())  # no-op

    def test_apply_error_ignored(self, db, notes, monkeypatch):
        monkeypatch.setattr(do, "_apply_database_connection_settings",
                            lambda s: (_ for _ in ()).throw(RuntimeError("cfg")))
        db.data["DatabaseSettings"] = {"sql_database_path": "z.db"}
        m = do.StateManager()
        asyncio.run(m.initialize_async())
        assert m._database_settings.sql_database_path == "z.db"

    def test_error_notifies_when_db_up(self, db, notes):
        db.async_error = RuntimeError("offline")
        m = do.StateManager()
        asyncio.run(m.initialize_async())
        assert notes[-1][0] == "critical" and notes[-1][2]["dedupe_key"] == "state:load"
        assert "RuntimeError: offline" == notes[-1][2]["details"]

    def test_error_silent_when_db_down(self, db, notes):
        db.manager._initialized = False
        db.async_error = RuntimeError("offline")
        asyncio.run(do.StateManager().initialize_async())
        assert notes == []


class TestInitializeWithData:
    def test_uses_preloaded_data(self, db, notes):
        m = do.StateManager()
        m.initialize_with_data({"AppSettings": {"current_theme": "light"},
                                "DatabaseSettings": {"sql_database_path": "pre.db", "bogus": 1},
                                "OBSData": {"host": "obs"}})
        assert m._initialized
        assert m.get_obs_data().host == "obs"
        assert db.config_updates[-1]["sql_database_path"] == "pre.db"
        assert notes == []
        m.initialize_with_data({"AppSettings": {"current_theme": "dark"}})
        assert m.get_app_settings().current_theme == "light"

    @pytest.mark.xfail(strict=True, reason="BUG: a non-dict preloaded DatabaseSettings is stored in state before "
                                            "the guarded parse fails, then _update_local_objects crashes on .items()")
    def test_bad_database_settings_ignored(self, db, notes):
        m = do.StateManager()
        m.initialize_with_data({"DatabaseSettings": ["bad"]})
        assert m._database_settings == do.DatabaseSettings()

    def test_startup_failures_mark_unreadable_and_notify(self, db, notes):
        db.startup_failed = {"TwitchData", "OBSData", "NotLoaded"}
        m = do.StateManager()
        m.initialize_with_data({"TwitchData": {}, "OBSData": {}, "AppSettings": {}})
        assert set(m._unreadable_blobs) == {"twitch_data", "obs_data"}
        n = notes[-1]
        assert n[2]["title"] == "Couldn't read your OBS and Twitch settings"
        assert n[2]["details"] == "Unreadable: TwitchData, OBSData"


class TestLoadFromFirebase:
    def test_failed_reads_marked_and_notified(self, db, notes):
        db.read_fail = {"SpotifyData", "DatabaseSettings"}
        m = do.StateManager()
        m.initialize()
        assert set(m._unreadable_blobs) == {"spotify_data", "database_settings"}
        assert m._unreadable_blobs["spotify_data"]["track_name"] == "Nothing playing"
        assert notes[-1][2]["dedupe_key"] == "state:unreadable"

    def test_unreadable_not_notified_when_db_down(self, db, notes):
        db.manager._initialized = False
        db.read_fail = {"SpotifyData"}
        do.StateManager().initialize()
        assert notes == []

    def test_database_settings_apply_error_tolerated(self, db, notes, monkeypatch):
        monkeypatch.delattr(dbm, "update_config")
        db.data["DatabaseSettings"] = {"sql_database_path": "q.db"}
        db.data["AppSettings"] = {"current_theme": "light"}
        m = do.StateManager()
        m.initialize()
        assert m.get_app_settings().current_theme == "light"

    def test_unexpected_error_notifies(self, db, notes, monkeypatch):
        m = do.StateManager()
        monkeypatch.setattr(m, "_update_local_objects", lambda: (_ for _ in ()).throw(ValueError("x")))
        m.initialize()
        assert notes[-1][2]["dedupe_key"] == "state:load"

    def test_notify_unreadable_empty(self, sm, notes):
        sm._notify_unreadable([])
        assert notes == []


# ======================================================================= live PSN

def test_live_psn_data(sm):
    data = do.PSNData(current_game_name="Game")
    assert sm.set_live_psn_data(data) is True
    got = sm.get_live_psn_data()
    assert got == data and got is not data
    sm._live_psn_data = None
    assert sm.get_live_psn_data() is None


def test_set_live_psn_data_error(sm):
    class Bad:
        current_game_name = "x"

        def __deepcopy__(self, memo):
            raise RuntimeError("no copy")

    assert sm.set_live_psn_data(Bad()) is False


# ======================================================================= setters that write immediately

class TestImmediateSetters:
    def test_set_twitch_data_encrypts_and_keeps_blank_secrets(self, sm, db):
        assert sm.set_twitch_data({"client_id": "cid", "client_secret": "sec", "auth_token": "tok",
                                   "refresh_token": "ref", "user_id": "42", "bogus": 1}) is True
        path, payload = db.writes[-1]
        assert path == "TwitchData"
        assert dec(payload["client_id"]) == "cid" and dec(payload["auth_token"]) == "tok"
        assert dec(payload["refresh_token"]) == "ref" and payload["user_id"] == "42"
        assert "bogus" not in payload
        assert sm.get_twitch_data().auth_token == "tok"
        assert not any(f.startswith("twitch_data") for f in sm.get_changed_fields())
        sm.set_twitch_data({"auth_token": "", "refresh_token": None})
        assert sm.get_twitch_data().auth_token == "tok"
        assert dec(db.writes[-1][1]["refresh_token"]) == "ref"

    def test_set_twitch_data_empty_tokens_not_encrypted(self, sm, db):
        sm.set_twitch_data({"auth_token": ""})
        assert db.writes[-1][1]["auth_token"] == ""

    def test_set_chatbot_data(self, sm, db):
        assert sm.set_chatbot_data({"auth_token": "a", "refresh_token": "r", "user_id": "u", "x": 1}) is True
        payload = db.writes[-1][1]
        assert dec(payload["auth_token"]) == "a" and dec(payload["refresh_token"]) == "r"
        sm.set_chatbot_data({"auth_token": ""})
        assert sm.get_chatbot_data().auth_token == "a"

    def test_set_spotify_data(self, sm, db):
        assert sm.set_spotify_data({"client_id": "c", "client_secret": "s", "access_token": "a",
                                    "refresh_token": "r", "track_name": "Song"}) is True
        payload = db.writes[-1][1]
        assert dec(payload["access_token"]) == "a" and payload["track_name"] == "Song"
        assert sm._spotify_write_failed is False
        sm.set_spotify_data({"access_token": "", "refresh_token": ""})
        assert sm.get_spotify_data().access_token == "a"

    def test_set_spotify_data_records_write_failure(self, sm, db):
        db.write_fail["SpotifyData"] = ("failed", "")
        sm.set_spotify_data({"track_name": "x"})
        assert sm._spotify_write_failed is True

    def test_set_app_settings_skips_version(self, sm, db):
        assert sm.set_app_settings({"current_theme": "light", "version": "9", "build_date": "x", "nope": 1})
        payload = db.writes[-1][1]
        assert payload["current_theme"] == "light" and "version" not in payload
        assert sm.get_app_settings().version == do.AppSettings().version

    def test_set_psn_settings(self, sm, db):
        assert sm.set_psn_settings_data({"npsso_code": "n", "x": 1})
        assert db.writes[-1] == ("PSNSettings", {"npsso_code": "n", "psn_username": ""})

    def test_set_database_settings(self, sm, db):
        assert sm.set_database_settings({"database_type": "mongodb", "x": 1})
        assert db.writes[-1][1]["database_type"] == "mongodb"
        assert sm.get_database_settings().database_type == "mongodb"

    @pytest.mark.parametrize("method", ["set_twitch_data", "set_chatbot_data", "set_spotify_data",
                                        "set_app_settings", "set_psn_settings_data", "set_database_settings"])
    def test_bad_input_returns_false(self, sm, db, method):
        assert getattr(sm, method)(None) is False
        assert db.writes == []

    @pytest.mark.parametrize("method,path", [("set_twitch_data", "TwitchData"), ("set_app_settings", "AppSettings"),
                                             ("set_psn_settings_data", "PSNSettings"),
                                             ("set_database_settings", "DatabaseSettings"),
                                             ("set_chatbot_data", "ChatbotData"), ("set_spotify_data", "SpotifyData")])
    def test_write_exception_returns_false(self, sm, db, method, path):
        db.write_exc[path] = RuntimeError("boom")
        assert getattr(sm, method)({}) is False

    @pytest.mark.parametrize("method", ["set_twitch_data", "set_app_settings", "set_chatbot_data"])
    def test_lazy_initialize(self, db, notes, method):
        m = do.StateManager()
        getattr(m, method)({})
        assert m._initialized

    @pytest.mark.xfail(strict=True, reason="BUG: _persist_unlocked ignores set_data()'s False result, so "
                                            "set_*_data reports success and drops the pending changes")
    def test_failed_write_is_reported_and_kept_pending(self, sm, db):
        db.write_fail["TwitchData"] = ("failed", "rejected")
        ok = sm.set_twitch_data({"auth_token": "new"})
        assert ok is False or sm.field_has_changes("twitch_data.auth_token")


class TestPersistUnlocked:
    def test_unreadable_blocked(self, sm, db):
        sm._unreadable_blobs["app_settings"] = {}
        db.read_fail.add("AppSettings")
        assert sm._persist_unlocked("AppSettings", {"x": 1}, "app_settings") is False
        assert db.writes == []

    def test_unreadable_recovered_goes_through_save(self, sm, db, monkeypatch):
        sm._unreadable_blobs["app_settings"] = copy.deepcopy(sm._state["app_settings"])
        db.data["AppSettings"] = {"current_theme": "stored"}
        sm.update_app_setting("alert_volume", 0.9)
        assert sm._persist_unlocked("AppSettings", {"ignored": 1}, "app_settings") is True
        stored = db.data["AppSettings"]
        assert stored["current_theme"] == "stored" and stored["alert_volume"] == 0.9

    def test_set_data_exception(self, sm, db):
        db.write_exc["X"] = RuntimeError("x")
        assert sm._persist_unlocked("X", {}, "app_settings") is False


# ======================================================================= YouTube / OBS / Discord setters

class TestYouTubeObsDiscord:
    def test_set_youtube_keeps_blank_secrets(self, sm, db):
        assert sm.set_youtube_data({"api_key": "k", "access_token": "a", "channel_urls": "u"}) is True
        assert sm.set_youtube_data({"api_key": "", "access_token": None, "channel_urls": "v", "nope": 1})
        y = sm.get_youtube_data()
        assert (y.api_key, y.access_token, y.channel_urls) == ("k", "a", "v")
        assert sm.field_has_changes("youtube_data") and sm.has_changes()
        assert db.writes == []

    def test_set_youtube_error(self, sm):
        assert sm.set_youtube_data(None) is False

    def test_set_obs_data(self, sm):
        assert sm.set_obs_data({"host": "h", "port": "4470", "nope": 1}) is True
        assert sm.get_obs_data().port == 4470 and sm._state["obs_data"]["port"] == 4470
        assert "nope" not in sm._state["obs_data"]
        assert sm.set_obs_data({"port": "abc"}) is True
        assert sm.get_obs_data().port == 4455
        assert sm.set_obs_data({"port": 0}) is True
        assert sm.get_obs_data().port == 4455

    def test_set_obs_error(self, sm):
        assert sm.set_obs_data(None) is False

    def test_set_discord_writes_merged(self, sm, db):
        assert sm.set_discord_data({"bot_token": "tok", "bot_username": "b", "nope": 1}) is True
        stored = db.data["DiscordData"]
        assert dec(stored["bot_token"]) == "tok" and stored["bot_username"] == "b"
        assert not any(f.startswith("discord_data") for f in sm.get_changed_fields())
        assert sm.has_changes() is False
        # blank token keeps the existing one
        assert sm.set_discord_data({"bot_token": "", "go_live_enabled": True}) is True
        assert sm.get_discord_data().bot_token == "tok"
        assert db.data["DiscordData"]["go_live_enabled"] is True

    def test_set_discord_none_channels_reset(self, sm, db):
        assert sm.set_discord_data({"go_live_channels": None}) is True
        assert sm.get_discord_data().go_live_channels == []

    def test_set_discord_empty_payload(self, sm, db):
        assert sm.set_discord_data({}) is True
        assert db.data["DiscordData"]["bot_token"] == ""

    def test_set_discord_write_failure_keeps_changes(self, sm, db):
        db.write_fail["DiscordData"] = ("failed", "")
        assert sm.set_discord_data({"bot_username": "x"}) is False
        assert sm.field_has_changes("discord_data.bot_username")

    def test_set_discord_error(self, sm):
        assert sm.set_discord_data(["bad"]) is False

    def test_set_discord_unreadable_blocked(self, sm, db):
        sm._unreadable_blobs["discord_data"] = {}
        db.read_fail.add("DiscordData")
        assert sm.set_discord_data({"bot_username": "x"}) is False

    def test_set_discord_unreadable_recovered(self, sm, db):
        sm._unreadable_blobs["discord_data"] = copy.deepcopy(sm._state["discord_data"])
        db.data["DiscordData"] = {"application_id": "app"}
        assert sm.set_discord_data({"bot_username": "x"}) is True
        assert db.data["DiscordData"]["application_id"] == "app"
        assert db.data["DiscordData"]["bot_username"] == "x"

    def test_persist_discord_bot_token(self, sm, db, monkeypatch):
        assert sm.persist_discord_bot_token("  ") is False
        assert sm.persist_discord_bot_token(" tok ") is True
        assert dec(db.data["DiscordData"]["bot_token"]) == "tok"
        db.write_fail["DiscordData"] = ("failed", "")
        assert sm.persist_discord_bot_token("tok2") is False
        monkeypatch.setattr(sm, "update_discord_field", lambda f, v: False)
        assert sm.persist_discord_bot_token("tok3") is False


# ======================================================================= update_*_field

FIELD_METHODS = {
    "update_twitch_field": ("twitch_data", "_twitch_data"),
    "update_app_setting": ("app_settings", "_app_settings"),
    "update_psn_setting": ("psn_settings_data", "_psn_settings_data"),
    "update_spotify_field": ("spotify_data", "_spotify_data"),
    "update_youtube_field": ("youtube_data", "_youtube_data"),
    "update_obs_field": ("obs_data", "_obs_data"),
    "update_discord_field": ("discord_data", "_discord_data"),
    "update_chatbot_field": ("chatbot_data", "_chatbot_data"),
    "update_database_setting": ("database_settings", "_database_settings"),
}


@pytest.mark.parametrize("method", FIELD_METHODS)
def test_update_field_invalid(sm, method):
    assert getattr(sm, method)("no_such_field", 1) is False
    assert not sm.has_changes()


@pytest.mark.parametrize("method", FIELD_METHODS)
def test_update_field_lazy_init(db, notes, method):
    m = do.StateManager()
    getattr(m, method)("no_such_field", 1)
    assert m._initialized


# (method, field, input, expected stored value)
CONVERSIONS = [
    ("update_twitch_field", "alerts_enabled", 0, False),
    ("update_twitch_field", "user_id", "u1", "u1"),
    ("update_app_setting", "alert_volume", "0.25", 0.25),
    ("update_app_setting", "activity_feed_limit", "7", 7),
    ("update_app_setting", "auto_update", "", False),
    ("update_psn_setting", "psn_username", "me", "me"),
    ("update_spotify_field", "is_playing", 1, True),
    ("update_spotify_field", "progress_percentage", 3, 3.0),
    ("update_youtube_field", "live_chat_enabled", 0, False),
    ("update_obs_field", "port", "4460", 4460),
    ("update_obs_field", "enabled", 0, False),
    ("update_discord_field", "go_live_enabled", 1, True),
    ("update_chatbot_field", "user_id", "c", "c"),
    ("update_database_setting", "connection_timeout", "12", 12),
]


@pytest.mark.parametrize("method,field,value,expected", CONVERSIONS)
def test_update_field_converts_and_marks(sm, method, field, value, expected):
    state_key, attr = FIELD_METHODS[method]
    assert getattr(sm, method)(field, value) is True
    got = getattr(getattr(sm, attr), field)
    assert got == expected and type(got) is type(expected)
    assert sm.field_has_changes(f"{state_key}.{field}") and sm.has_changes()


@pytest.mark.parametrize("method,field", [
    ("update_app_setting", "activity_feed_limit"), ("update_obs_field", "port"),
    ("update_database_setting", "connection_timeout"), ("update_twitch_field", "alerts_enabled"),
])
def test_update_field_conversion_error(sm, method, field, monkeypatch):
    if field == "alerts_enabled":
        # bool() never fails; force an error from the state update instead.
        monkeypatch.setattr(sm, "_update_local_objects", lambda: (_ for _ in ()).throw(RuntimeError()))
        assert getattr(sm, method)(field, True) is False
        return
    assert getattr(sm, method)(field, "not a number") is False


@pytest.mark.parametrize("method", ["update_psn_setting", "update_spotify_field", "update_youtube_field",
                                    "update_discord_field", "update_chatbot_field"])
def test_update_field_unexpected_error(sm, method, monkeypatch):
    monkeypatch.setattr(sm, "_update_local_objects", lambda: (_ for _ in ()).throw(RuntimeError()))
    field = {"update_psn_setting": "psn_username", "update_spotify_field": "track_name",
             "update_youtube_field": "channel_urls", "update_discord_field": "bot_username",
             "update_chatbot_field": "user_id"}[method]
    assert getattr(sm, method)(field, "x") is False


@pytest.mark.parametrize("method", FIELD_METHODS)
@pytest.mark.parametrize("current,value,expected", [(False, 1, True), (3, "4", 4), (1.5, "2", 2.0)])
def test_update_field_type_matching_all_branches(sm, method, current, value, expected, monkeypatch):
    # Not every dataclass has bool/int/float fields; give one a typed value to reach each branch.
    state_key, attr = FIELD_METHODS[method]
    target = getattr(sm, attr)
    field = "custom_test_field"
    setattr(target, field, current)
    monkeypatch.setattr(sm, "_update_local_objects", lambda: None)
    assert getattr(sm, method)(field, value) is True
    got = sm._state[state_key][field]
    assert got == expected and type(got) is type(expected)


@pytest.mark.parametrize("method,setter", [
    ("set_youtube_data", {}), ("set_obs_data", {}), ("set_discord_data", {}), ("set_psn_settings_data", {}),
    ("set_spotify_data", {}), ("set_database_settings", {}),
])
def test_setters_lazy_initialize(db, notes, method, setter):
    m = do.StateManager()
    getattr(m, method)(setter)
    assert m._initialized


def test_update_float_from_int_field(sm):
    # float current value, non-float input
    assert sm.update_spotify_field("track_length_seconds", "12") is True
    assert sm.get_spotify_data().track_length_seconds == 12.0
    assert sm.update_youtube_field("channel_urls", "a|b")
    assert sm.update_obs_field("host", "h")
    assert sm.update_discord_field("bot_username", "b")
    assert sm.update_chatbot_field("token_expiry", "x")
    assert sm.update_database_setting("database_type", "mongodb")


@pytest.mark.parametrize("method,field,attr", [
    ("update_twitch_field", "auth_token", "_twitch_data"),
    ("update_spotify_field", "refresh_token", "_spotify_data"),
    ("update_youtube_field", "api_key", "_youtube_data"),
    ("update_chatbot_field", "auth_token", "_chatbot_data"),
    ("update_discord_field", "bot_token", "_discord_data"),
])
def test_update_secret_field_blank_keeps_existing(sm, method, field, attr):
    assert getattr(sm, method)(field, "  secret  ") is True
    assert getattr(getattr(sm, attr), field) == "secret"
    sm._changed_fields.clear()
    sm._changes_pending = False
    assert getattr(sm, method)(field, "") is True
    assert getattr(getattr(sm, attr), field) == "secret"
    assert not sm.has_changes()


def test_update_app_setting_version_blocked(sm):
    for f in ("version", "build_number", "build_date"):
        assert sm.update_app_setting(f, "x") is False
    assert not sm.has_changes()


def test_spotify_unchanged_field_not_dirty(sm):
    sm.update_spotify_field("track_name", "Song")
    sm._changed_fields.clear()
    sm._changes_pending = False
    assert sm.update_spotify_field("track_name", "Song") is True
    assert not sm.field_has_changes("spotify_data.track_name")
    sm._spotify_write_failed = True
    sm.update_spotify_field("track_name", "Song")
    assert sm.field_has_changes("spotify_data.track_name")


# ======================================================================= save_changes

class TestSaveChanges:
    def test_nothing_to_save(self, sm, db):
        sm._changes_pending = True
        assert sm.save_changes() is True
        assert sm._changes_pending is False and db.writes == []

    def test_lazy_initialize(self, db, notes):
        m = do.StateManager()
        assert m.save_changes() is True and m._initialized

    def test_saves_every_blob_encrypted(self, sm, db, notes):
        sm.update_twitch_field("client_id", "cid")
        sm.update_twitch_field("client_secret", "cs")
        sm.update_twitch_field("auth_token", "tok")
        sm.update_twitch_field("refresh_token", "rt")
        sm.update_app_setting("current_theme", "light")
        sm.update_psn_setting("npsso_code", "n")
        sm.update_spotify_field("client_id", "sc")
        sm.update_spotify_field("client_secret", "ss")
        sm.update_spotify_field("access_token", "sa")
        sm.update_spotify_field("refresh_token", "sr")
        sm.update_youtube_field("api_key", "yk")
        sm.update_youtube_field("oauth_client_secret", "yos")
        sm.update_obs_field("password", "pw")
        sm.update_discord_field("bot_token", "dt")
        sm.update_database_setting("connection_timeout", 11)
        assert sm.save_changes() is True
        d = db.data
        assert dec(d["TwitchData"]["client_id"]) == "cid" and dec(d["TwitchData"]["refresh_token"]) == "rt"
        assert d["AppSettings"]["current_theme"] == "light"
        assert d["PSNSettings"]["npsso_code"] == "n"
        assert dec(d["SpotifyData"]["access_token"]) == "sa" and dec(d["SpotifyData"]["client_secret"]) == "ss"
        assert dec(d["YouTubeData"]["api_key"]) == "yk" and dec(d["YouTubeData"]["oauth_client_secret"]) == "yos"
        assert d["YouTubeData"]["access_token"] == ""
        assert dec(d["OBSData"]["password"]) == "pw"
        assert dec(d["DiscordData"]["bot_token"]) == "dt"
        assert d["DatabaseSettings"]["connection_timeout"] == 11
        assert db.config_updates[-1]["connection_timeout"] == 11
        assert db.config_updates[-1]["database_type"] == "sql"
        assert not sm.has_changes() and notes == []
        assert sm._spotify_write_failed is False

    @pytest.mark.xfail(strict=True, reason="BUG: save_changes has no chatbot_data branch, so "
                                            "update_chatbot_field changes are dropped yet reported as saved")
    def test_chatbot_field_changes_are_written(self, sm, db):
        sm.update_chatbot_field("user_id", "cu")
        assert sm.save_changes() is True
        assert db.data.get("ChatbotData", {}).get("user_id") == "cu"

    def test_quiet_during_writes(self, sm, db):
        seen = []
        real = db.set_data

        def spy(path, data):
            seen.append(dbm._quiet())
            return real(path, data)

        db.set_data = spy
        dbm_set = dbm.set_data
        try:
            dbm.set_data = spy
            sm.update_app_setting("current_theme", "x")
            sm.save_changes()
        finally:
            dbm.set_data = dbm_set
        assert seen == [True]

    @pytest.mark.xfail(strict=True, reason="BUG: save_changes calls database_manager.update_config(), which "
                                            "doesn't exist, so saved DatabaseSettings never reach the live manager")
    def test_saved_database_settings_reach_real_manager(self, sm, db, monkeypatch):
        monkeypatch.delattr(dbm, "update_config")
        mgr = dbm.DatabaseManager()
        monkeypatch.setattr(dbm, "database_manager", mgr)
        sm.update_database_setting("connection_timeout", 44)
        assert sm.save_changes() is True
        assert mgr.get_config().connection_timeout == 44

    def test_database_config_apply_error_logged(self, sm, db, monkeypatch):
        monkeypatch.setattr(dbm, "update_config", lambda **kw: (_ for _ in ()).throw(RuntimeError()), raising=False)
        sm.update_database_setting("connection_timeout", 5)
        assert sm.save_changes() is True

    def test_database_config_prepare_error_logged(self, sm, db, monkeypatch):
        monkeypatch.setattr(dbm, "DatabaseConfig", lambda **kw: (_ for _ in ()).throw(TypeError("cfg")))
        sm.update_database_setting("connection_timeout", 5)
        assert sm.save_changes() is True
        assert db.config_updates == []

    def test_database_settings_write_failed_skips_apply(self, sm, db, notes):
        db.write_fail["DatabaseSettings"] = ("failed", "x")
        sm.update_database_setting("connection_timeout", 5)
        assert sm.save_changes() is False
        assert db.config_updates == []

    def test_prepare_error_notifies(self, sm, db, notes, monkeypatch):
        monkeypatch.setattr(do, "ensure_encrypted", lambda v: (_ for _ in ()).throw(ValueError("enc")))
        sm.update_obs_field("password", "pw")
        assert sm.save_changes() is False
        assert notes[-1][0] == "critical" and "ValueError: enc" in notes[-1][2]["details"]
        assert sm.field_has_changes("obs_data.password")

    def test_prepare_encryption_error_not_double_reported(self, sm, db, notes, monkeypatch):
        monkeypatch.setattr(do, "ensure_encrypted", lambda v: (_ for _ in ()).throw(eu.EncryptionError("k")))
        sm.update_obs_field("password", "pw")
        assert sm.save_changes() is False
        assert notes == []

    @pytest.mark.parametrize("status,reason,why", [
        ("failed", "rejected", "didn't accept the change"),
        ("timeout", "slow", "didn't respond in time"),
        ("down", "", "can't reach the Firebase database"),
    ])
    def test_write_failures_keep_changes_and_notify(self, sm, db, notes, status, reason, why):
        db.write_fail["AppSettings"] = (status, reason)
        sm.update_app_setting("current_theme", "x")
        sm.update_psn_setting("psn_username", "ok")
        assert sm.save_changes() is False
        assert sm.field_has_changes("app_settings.current_theme")
        assert not sm.field_has_changes("psn_settings_data.psn_username")
        n = notes[-1][2]
        assert n["title"] == "Couldn't save your app settings" and n["type"] == "error"
        assert why in notes[-1][1][0]
        assert n["details"] == f"AppSettings: {status}" + (f" - {reason}" if reason else "")

    def test_failure_when_db_not_up(self, sm, db, notes):
        db.manager._initialized = False
        db.write_fail["AppSettings"] = ("failed", "")
        sm.update_app_setting("current_theme", "x")
        sm.save_changes()
        assert "can't reach" in notes[-1][1][0]

    def test_unknown_problem_defaults_to_failed(self, sm, db, notes, monkeypatch):
        db.write_fail["AppSettings"] = None
        sm.update_app_setting("current_theme", "x")
        assert sm.save_changes() is False
        assert notes[-1][2]["details"] == "AppSettings: failed"

    def test_superseded_counts_as_saved(self, sm, db, notes):
        db.write_fail["AppSettings"] = ("superseded", "")
        sm.update_app_setting("current_theme", "x")
        assert sm.save_changes() is True and notes == []
        assert not sm.has_changes()

    def test_write_exception(self, sm, db, notes):
        db.write_exc["AppSettings"] = RuntimeError("io")
        sm.update_app_setting("current_theme", "x")
        assert sm.save_changes() is False
        assert notes[-1][2]["details"] == "AppSettings: failed - io"

    def test_discord_failed_read_reason(self, sm, db, notes):
        db.read_fail.add("DiscordData")
        sm.update_discord_field("bot_username", "b")
        assert sm.save_changes() is False
        assert "the stored copy couldn't be read first" in notes[-1][2]["details"]

    def test_discord_failed_write_problem_used(self, sm, db, notes):
        db.write_fail["DiscordData"] = ("timeout", "slow")
        sm.update_discord_field("bot_username", "b")
        assert sm.save_changes() is False
        assert notes[-1][2]["details"] == "DiscordData: timeout - slow"

    def test_spotify_failure_flag(self, sm, db, notes):
        db.write_fail["SpotifyData"] = ("failed", "")
        sm.update_spotify_field("track_name", "x")
        sm.save_changes()
        assert sm._spotify_write_failed is True

    def test_changes_made_during_write_stay_pending(self, sm, db):
        real = db.set_data

        def during(path, data):
            sm.update_psn_setting("psn_username", "late")
            return real(path, data)

        db.set_data = during
        dbm_set = dbm.set_data
        dbm.set_data = during
        try:
            sm.update_app_setting("current_theme", "x")
            assert sm.save_changes() is True
        finally:
            dbm.set_data = dbm_set
        assert sm.field_has_changes("psn_settings_data.psn_username") and sm.has_changes()

    def test_unreadable_blocked_only(self, sm, db, notes):
        sm._unreadable_blobs["app_settings"] = {}
        db.read_fail.add("AppSettings")
        sm.update_app_setting("current_theme", "x")
        assert sm.save_changes() is False
        n = notes[-1]
        assert n[2]["title"] == "Didn't save your app settings" and n[2]["type"] == "warning"
        assert "not written (couldn't be read first)" in n[2]["details"]
        assert db.writes == []

    def test_unreadable_blocked_with_other_saves(self, sm, db, notes):
        sm._unreadable_blobs["app_settings"] = {}
        db.read_fail.add("AppSettings")
        sm.update_app_setting("current_theme", "x")
        sm.update_psn_setting("psn_username", "p")
        assert sm.save_changes() is False
        assert [w[0] for w in db.writes] == ["PSNSettings"]
        assert sm.field_has_changes("app_settings.current_theme")

    def test_unreadable_recovered_merges(self, sm, db, notes):
        sm._unreadable_blobs["app_settings"] = copy.deepcopy(sm._state["app_settings"])
        db.data["AppSettings"] = {"current_theme": "stored", "alert_volume": 0.1}
        sm.update_app_setting("alert_volume", 0.7)
        assert sm.save_changes() is True
        assert db.data["AppSettings"]["current_theme"] == "stored"
        assert db.data["AppSettings"]["alert_volume"] == 0.7
        assert "app_settings" not in sm._unreadable_blobs

    def test_save_failure_popup_gap(self, sm, db, notes, monkeypatch):
        import time

        clock = {"t": 100.0}
        monkeypatch.setattr(time, "monotonic", lambda: clock["t"])
        db.write_fail["AppSettings"] = ("failed", "")
        sm.update_app_setting("current_theme", "x")
        sm.save_changes()
        assert notes[-1][2]["skip_toast"] is False
        clock["t"] += 5
        sm.save_changes()
        assert notes[-1][2]["skip_toast"] is True
        clock["t"] += do._SAVE_FAILURE_POPUP_GAP_SEC + 1
        sm.save_changes()
        assert notes[-1][2]["skip_toast"] is False


class TestRecover:
    def test_skips_readable_and_handles_concurrent_pop(self, sm, db, monkeypatch):
        assert sm._recover_unreadable_blobs({"app_settings"}) == set()
        sm._unreadable_blobs["obs_data"] = {}
        real = db.get_data_checked

        def racing(path):
            sm._unreadable_blobs.pop("obs_data", None)
            return real(path)

        monkeypatch.setattr(dbm, "get_data_checked", racing)
        assert sm._recover_unreadable_blobs({"obs_data"}) == set()

    def test_non_dict_stored_copy(self, sm, db):
        sm._unreadable_blobs["obs_data"] = copy.deepcopy(sm._state["obs_data"])
        db.data["OBSData"] = ["junk"]
        sm.update_obs_field("host", "h")
        assert sm._recover_unreadable_blobs({"obs_data"}) == set()
        assert sm.get_obs_data().host == "h"


# ======================================================================= change tracking

class TestChangeTracking:
    def test_discard_and_reload(self, sm, db):
        db.data["AppSettings"] = {"current_theme": "stored"}
        sm.update_app_setting("current_theme", "x")
        sm.discard_changes()
        assert not sm.has_changes() and sm.get_app_settings().current_theme == "stored"
        sm.update_app_setting("current_theme", "y")
        sm.reload_from_firebase()
        assert not sm.has_changes() and sm.get_app_settings().current_theme == "stored"

    def test_has_changes_repairs_inconsistency(self, sm):
        sm._changes_pending = True
        assert sm.has_changes() is False
        sm._changed_fields.add("x.y")
        assert sm.has_changes() is True

    def test_changed_fields_copy(self, sm):
        sm.update_app_setting("current_theme", "x")
        fields = sm.get_changed_fields()
        fields.clear()
        assert sm.field_has_changes("app_settings.current_theme")
        assert not sm.field_has_changes("app_settings.other")


# ======================================================================= module level

def test_module_level_initializers(db, notes, monkeypatch):
    for fn, kind in ((do.initialize_state, "sync"), (do.initialize_with_data, "data"),
                     (do.initialize_state_async, "async")):
        m = do.StateManager()
        monkeypatch.setattr(do, "state_manager", m)
        if kind == "sync":
            fn()
        elif kind == "data":
            fn({"AppSettings": {"current_theme": "light"}})
            assert m.get_app_settings().current_theme == "light"
        else:
            asyncio.run(fn())
        assert m._initialized


def test_global_state_manager_exists():
    assert isinstance(do.state_manager, do.StateManager)
