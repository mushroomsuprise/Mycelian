# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/giveaway_manager.py: config, pool, draws, saves and failure handling."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import chat_banner, chatbot, database_manager, notification_engine, shutdown
from modules import giveaway_manager as gmod
from modules import statistics_manager, web_engine


class FakeStats:
    def __init__(self):
        self.entries = []
        self.winners = []
        self.rounds = 0

    def record_giveaway_entry(self, name):
        self.entries.append(name)

    def record_giveaway_winner(self, name):
        self.winners.append(name)

    def record_giveaway_round_complete(self):
        self.rounds += 1


class Env:
    def __init__(self):
        self.stored = {}
        self.read_ok = True
        self.read_raises = False
        self.writes = []
        self.write_result = True
        self.write_raises = None
        self.problem = None
        self.notices = []
        self.announcements = []
        self.announce_result = True
        self.announce_raises = False
        self.banners = []
        self.banner_raises = False
        self.events = []
        self.stats = FakeStats()

    def get_data_checked(self, path):
        assert path == gmod.DATA_PATH
        if self.read_raises:
            raise RuntimeError("db gone")
        return dict(self.stored), self.read_ok

    def set_data(self, path, payload):
        if self.write_raises:
            raise self.write_raises
        self.writes.append((path, dict(payload)))
        return self.write_result

    def notify(self, message, **kw):
        self.notices.append((message, kw))

    def announce(self, text, color="primary"):
        if self.announce_raises:
            raise RuntimeError("chat down")
        self.announcements.append((text, color))
        return self.announce_result

    def banner(self, text, source=None):
        if self.banner_raises:
            raise RuntimeError("banner down")
        self.banners.append((text, source))


@pytest.fixture
def env(monkeypatch):
    e = Env()

    @contextmanager
    def quiet():
        yield

    monkeypatch.setattr(database_manager, "get_data_checked", e.get_data_checked)
    monkeypatch.setattr(database_manager, "set_data", e.set_data)
    monkeypatch.setattr(database_manager, "quiet_notifications", quiet)
    monkeypatch.setattr(database_manager, "last_write_problem", lambda: e.problem)
    monkeypatch.setattr(notification_engine, "notify", e.notify)
    monkeypatch.setattr(chatbot, "send_chatbot_announcement", e.announce)
    monkeypatch.setattr(chat_banner, "emit_chat_banner", e.banner)
    monkeypatch.setattr(statistics_manager, "get_statistics_manager", lambda: e.stats)
    fake_engine = SimpleNamespace(safe_emit=lambda ev, data: e.events.append((ev, data)))
    monkeypatch.setattr(web_engine, "web_engine_instance", fake_engine, raising=False)
    return e


def _msg(text, user="alice", uid="1", badges=""):
    return {"message": text, "username": user, "userid": uid, "badges": badges}


def _active(env, keyword="!join", **cfg):
    m = gmod.GiveawayManager()
    m.update_config({"keyword": keyword, **cfg})
    ok, err = m.start_giveaway()
    assert ok, err
    return m


# --------------------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    "template,winners,expected",
    [
        ("GG {winners}!", ["a", "b"], "GG a, b!"),
        ("{winner} won ({count})", ["x"], "x won (1)"),
        ("", ["a"], ""),
        (None, ["a"], ""),
        ("{count} winners", [], "0 winners"),
    ],
)
def test_format_winners_message(template, winners, expected):
    assert gmod.format_winners_message(template, winners) == expected


def test_giveaway_entry_defaults_entered_at():
    entry = gmod.GiveawayEntry("1", "a")
    assert entry.entered_at > 0


def test_get_giveaway_manager_singleton_registers_flush(env, monkeypatch):
    monkeypatch.setattr(gmod, "_giveaway_manager", None)
    registered = []
    monkeypatch.setattr(shutdown, "register_shutdown_flush", lambda name, fn: registered.append((name, fn)))
    first = gmod.get_giveaway_manager()
    second = gmod.get_giveaway_manager()
    assert first is second
    assert registered == [("giveaway_config", first.flush)]


def test_get_giveaway_manager_tolerates_missing_shutdown(env, monkeypatch):
    monkeypatch.setattr(gmod, "_giveaway_manager", None)
    monkeypatch.setitem(sys.modules, "modules.shutdown", None)  # import raises ImportError
    manager = gmod.get_giveaway_manager()
    assert isinstance(manager, gmod.GiveawayManager)


# --------------------------------------------------------------------------- load / normalize


def test_load_defaults_when_nothing_stored(env):
    m = gmod.GiveawayManager()
    assert m.get_config() == gmod.DEFAULT_CONFIG
    assert m._failed_load_baseline is None


def test_load_merges_and_normalizes_stored(env):
    env.stored = {"keyword": "  !win ", "num_winners": "500", "blocked_usernames": "Bob, carl\nDAVE",
                  "winning_message_template": "", "exclude_mods": 1}
    cfg = gmod.GiveawayManager().get_config()
    assert cfg["keyword"] == "!win"
    assert cfg["num_winners"] == 100
    assert cfg["blocked_usernames"] == ["bob", "carl", "dave"]
    assert cfg["winning_message_template"] == gmod.DEFAULT_CONFIG["winning_message_template"]
    assert cfg["exclude_mods"] is True


def test_load_failed_read_records_baseline(env):
    env.read_ok = False
    m = gmod.GiveawayManager()
    assert m._failed_load_baseline == gmod.DEFAULT_CONFIG


def test_load_exception_falls_back_to_defaults(env):
    env.read_raises = True
    m = gmod.GiveawayManager()
    assert m.get_config() == gmod.DEFAULT_CONFIG


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("abc", 1),
        (None, 1),
        (0, 1),
        (-5, 1),
        (7, 7),
        (101, 100),
    ],
)
def test_normalize_num_winners(env, raw, expected):
    m = gmod.GiveawayManager()
    cfg = {"num_winners": raw}
    m._normalize_config_in_place(cfg)
    assert cfg["num_winners"] == expected


def test_normalize_blocked_non_list_and_blank_entries(env):
    m = gmod.GiveawayManager()
    cfg = {"blocked_usernames": 42}
    m._normalize_config_in_place(cfg)
    assert cfg["blocked_usernames"] == []
    cfg = {"blocked_usernames": [" A ", "", "  ", "b"]}
    m._normalize_config_in_place(cfg)
    assert cfg["blocked_usernames"] == ["a", "b"]
    assert cfg["keyword"] == ""
    assert cfg["announce_winners_as_banner"] is False


# --------------------------------------------------------------------------- config writes


def test_set_config_field_saves_and_ignores_unknown(env):
    m = gmod.GiveawayManager()
    m.set_config_field("bogus", 1)
    assert env.writes == []
    m.set_config_field("num_winners", "3")
    assert m.get_config()["num_winners"] == 3
    assert env.writes[-1] == (gmod.DATA_PATH, m.get_config())


def test_clearing_keyword_stops_entries(env):
    m = _active(env)
    assert m.is_giveaway_active()
    m.set_config_field("keyword", "  ")
    assert not m.is_giveaway_active()
    assert m._accepting_entries is False


def test_update_config_filters_unknown_and_clears_accepting(env):
    m = _active(env)
    m.update_config({"keyword": "", "nope": 1, "exclude_vips": True})
    cfg = m.get_config()
    assert "nope" not in cfg
    assert cfg["exclude_vips"] is True
    assert m._accepting_entries is False


def test_update_config_keeps_accepting_with_keyword(env):
    m = _active(env)
    m.update_config({"num_winners": 2})
    assert m.is_giveaway_active()


def test_save_config_none_is_noop(env):
    m = gmod.GiveawayManager()
    m._save_config(None)
    assert env.writes == []


def test_stale_snapshot_never_written_after_newer(env):
    m = gmod.GiveawayManager()
    with m._lock:
        old = m._snapshot_config_locked()
        new = m._snapshot_config_locked()
    m._save_config(new)
    m._save_config(old)
    assert len(env.writes) == 1


def test_write_returning_false_notifies_with_reason(env):
    env.write_result = False
    env.problem = ("locked", "", "busy")
    m = gmod.GiveawayManager()
    m.set_config_field("num_winners", 2)
    msg, kw = env.notices[-1]
    assert kw["title"] == "Couldn't save giveaway settings"
    assert kw["type"] == "error"
    assert "(locked: busy)" in kw["details"]
    assert kw["dedupe_key"] == "giveaway:save_config"
    assert kw["actions"] == notification_engine.nav_chatbot("Giveaways")


def test_write_returning_false_without_problem(env):
    env.write_result = False
    m = gmod.GiveawayManager()
    m.set_config_field("num_winners", 2)
    assert env.notices[-1][1]["details"] == f"Database write returned no success for {gmod.DATA_PATH}"


def test_write_exception_notifies(env):
    env.write_raises = OSError("disk full")
    m = gmod.GiveawayManager()
    m.set_config_field("num_winners", 2)
    assert env.notices[-1][1]["details"] == "OSError: disk full"


def test_notify_failure_is_swallowed(env, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("no ui")

    monkeypatch.setattr(notification_engine, "notify", boom)
    gmod.GiveawayManager._notify_save_problem("t", "m", "d")  # must not raise


def test_save_blocked_while_unreadable_then_flush_merges(env):
    env.read_ok = False
    m = gmod.GiveawayManager()
    m.set_config_field("num_winners", 5)
    assert env.writes == []
    assert m._save_blocked is True
    notice = env.notices[-1][1]
    assert notice["type"] == "warning"
    assert notice["dedupe_key"] == "giveaway:save_blocked"
    assert notice["dedupe_cooldown_sec"] == 600

    # flush while still unreadable: still nothing written
    m.flush()
    assert env.writes == []

    # becomes readable: stored fields kept, in-memory changes win
    env.read_ok = True
    env.stored = {"keyword": "!stored", "exclude_vips": True}
    m.flush()
    assert m._save_blocked is False
    assert m._failed_load_baseline is None
    written = env.writes[-1][1]
    assert written["num_winners"] == 5
    assert written["keyword"] == "!stored"
    assert written["exclude_vips"] is True


def test_recover_reread_exception_counts_as_unreadable(env):
    env.read_ok = False
    m = gmod.GiveawayManager()
    env.read_raises = True
    assert m._recover_after_failed_load() is None


def test_recover_with_non_dict_raw_and_empty_keyword_stops_entries(env, monkeypatch):
    env.read_ok = False
    m = gmod.GiveawayManager()
    m._accepting_entries = True
    monkeypatch.setattr(database_manager, "get_data_checked", lambda path: (None, True))
    seq, payload = m._recover_after_failed_load()
    assert payload == gmod.DEFAULT_CONFIG
    assert m._accepting_entries is False


def test_recover_without_baseline_just_snapshots(env):
    m = gmod.GiveawayManager()
    seq, payload = m._recover_after_failed_load()
    assert payload == m.get_config()
    assert seq == m._save_seq


def test_flush_noop_when_not_blocked(env):
    m = gmod.GiveawayManager()
    m.flush()
    assert env.writes == []


# --------------------------------------------------------------------------- status / overlay


def test_status_overlay_and_pool_views(env):
    m = _active(env)
    m.try_register_entry(_msg("!join", "alice", "1"))
    m.try_register_entry(_msg("!JOIN ", "bob", "2"))
    assert m.get_pool_size() == 2
    assert m.get_pool_entries() == ["bob", "alice"]
    details = m.get_pool_details()
    assert [d["user_id"] for d in details] == ["2", "1"]
    assert all("entered_at" in d for d in details)
    status = m.get_status()
    assert status["accepting"] is True
    assert status["keyword"] == "!join"
    assert status["pool_size"] == 2
    assert status["started_at"] > 0
    snap = m.get_overlay_snapshot()
    assert snap == m.get_overlay_state()
    assert snap["pool_entries"] == ["bob", "alice"]


def test_last_error_roundtrip(env):
    m = gmod.GiveawayManager()
    m.set_last_error("bad")
    assert m.get_last_error() == "bad"
    m.set_last_error(None)
    assert m.get_last_error() == ""


def test_emit_event_without_engine_and_with_error(env, monkeypatch):
    m = gmod.GiveawayManager()
    monkeypatch.setattr(web_engine, "web_engine_instance", None, raising=False)
    m._emit_event("x", {})  # no engine: nothing
    assert env.events == []

    def boom(*_a):
        raise RuntimeError("socket")

    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(safe_emit=boom), raising=False)
    m._emit_event("x", {})  # error swallowed


# --------------------------------------------------------------------------- lifecycle


def test_start_requires_keyword(env):
    m = gmod.GiveawayManager()
    assert m.start_giveaway() == (False, "Set an entry keyword before starting.")
    assert env.events == []


def test_start_emits_and_keeps_started_at_with_pool(env):
    m = _active(env)
    assert env.events[-1] == ("giveaway_start", {"keyword": "!join"})
    m.try_register_entry(_msg("!join"))
    m._started_at = 123.0
    m.stop_accepting()
    assert env.events[-1] == ("giveaway_stop", {})
    assert not m.is_giveaway_active()
    m.start_giveaway()
    assert m._started_at == 123.0  # pool not empty: not reset


def test_clear_giveaway_resets_everything(env):
    m = _active(env)
    m.try_register_entry(_msg("!join"))
    m._last_draw = {"winners": ["a"]}
    m.clear_giveaway()
    assert m.get_pool_size() == 0
    assert m.get_config()["keyword"] == ""
    assert m._started_at == 0.0
    assert m.get_status()["last_draw"] == {}
    assert env.events[-1] == ("giveaway_clear", {})
    assert env.writes[-1][1]["keyword"] == ""


# --------------------------------------------------------------------------- entries


def test_entry_rejected_when_not_accepting(env):
    m = gmod.GiveawayManager()
    m.update_config({"keyword": "!join"})
    assert m.try_register_entry(_msg("!join")) is False


def test_entry_rejected_when_keyword_blank_but_accepting(env):
    m = gmod.GiveawayManager()
    m._accepting_entries = True
    assert m.try_register_entry(_msg("")) is False


def test_entry_wrong_message_and_missing_message(env):
    m = _active(env)
    assert m.try_register_entry(_msg("!nope")) is False
    assert m.try_register_entry({"username": "x"}) is False


def test_entry_records_stats_and_emits(env):
    m = _active(env)
    assert m.try_register_entry(_msg("!join", "alice", "1")) is True
    assert env.stats.entries == ["alice"]
    ev, data = env.events[-1]
    assert ev == "giveaway_entry"
    assert data == {"username": "alice", "pool_size": 1, "pool_entries": ["alice"]}


def test_entry_user_id_fallbacks(env):
    m = _active(env)
    m.try_register_entry({"message": "!join", "username": "bob", "user_id": "u9"})
    m.try_register_entry({"message": "!join"})
    ids = [e.user_id for e in m._pool]
    names = [e.display_name for e in m._pool]
    assert ids == ["u9", "Unknown"]
    assert names == ["bob", "Unknown"]


def test_entry_blocked_user(env):
    m = _active(env, blocked_usernames=["Alice"])
    assert m.try_register_entry(_msg("!join", "ALICE")) is False
    assert m._is_blocked_user(" alice ") is True


def test_entry_excludes_mods_and_vips(env):
    m = _active(env, exclude_mods=True, exclude_vips=True)
    assert m.try_register_entry(_msg("!join", "mod", "1", "moderator/1")) is False
    assert m.try_register_entry(_msg("!join", "vip", "2", "vip/1")) is False
    assert m.try_register_entry(_msg("!join", "plain", "3", "subscriber/1")) is True


def test_entry_badges_non_string_ignored(env):
    m = _active(env, exclude_mods=True)
    msg = _msg("!join")
    msg["badges"] = {"moderator": "1"}
    assert m._badges_str(msg) == ""
    assert m.try_register_entry(msg) is True


def test_duplicate_entries(env):
    m = _active(env)
    assert m.try_register_entry(_msg("!join", "a", "1")) is True
    assert m.try_register_entry(_msg("!join", "a", "1")) is False
    m.update_config({"no_duplicate_entries": False})
    assert m.try_register_entry(_msg("!join", "a", "1")) is True
    assert m.get_pool_size() == 2


def test_entry_stats_failure_is_ignored(env, monkeypatch):
    def boom():
        raise RuntimeError("stats")

    monkeypatch.setattr(statistics_manager, "get_statistics_manager", boom)
    m = _active(env)
    assert m.try_register_entry(_msg("!join")) is True


# --------------------------------------------------------------------------- remove entry


def test_remove_entry_missing(env):
    m = _active(env)
    assert m.remove_entry("nope") is False


def test_remove_entry_without_block(env):
    m = _active(env)
    m.try_register_entry(_msg("!join", "Alice", "1"))
    writes = len(env.writes)
    assert m.remove_entry("1") is True
    assert m.get_pool_size() == 0
    assert len(env.writes) == writes
    assert env.events[-1][0] == "giveaway_sync"


def test_remove_entry_with_block_saves_once(env):
    m = _active(env)
    m.try_register_entry(_msg("!join", "Alice", "1"))
    assert m.remove_entry("1", block=True) is True
    assert m.get_config()["blocked_usernames"] == ["alice"]
    assert env.writes[-1][1]["blocked_usernames"] == ["alice"]
    # already blocked name: no new write
    m._pool.append(gmod.GiveawayEntry("1", "alice"))
    writes = len(env.writes)
    m.remove_entry("1", block=True)
    assert len(env.writes) == writes


# --------------------------------------------------------------------------- draws


def test_draw_empty_pool(env):
    m = gmod.GiveawayManager()
    assert m.draw_winners() == (False, "Pool is empty.", [])
    assert m.get_last_error() == "Pool is empty."


def test_draw_unique_winners_removes_from_pool(env, monkeypatch):
    m = _active(env, num_winners=2)
    for i, name in enumerate(["a", "b", "c"]):
        m.try_register_entry(_msg("!join", name, str(i)))
    monkeypatch.setattr(gmod.random, "choice", lambda seq: seq[0])
    ok, msg, winners = m.draw_winners()
    assert ok and msg == "Drew 2 winner(s)."
    assert winners == ["a", "b"]
    assert m.get_pool_entries() == ["c"]
    assert env.announcements[-1] == ("Congratulations a, b!", "primary")
    assert env.stats.winners == ["a", "b"] and env.stats.rounds == 1
    ev, data = env.events[-1]
    assert ev == "giveaway_winner"
    assert data == {"winners": ["a", "b"], "pool_size": 1, "pool_entries": ["c"]}
    last = m.get_status()["last_draw"]
    assert last["winners"] == ["a", "b"] and last["announced"] is True and last["banner"] is False


def test_draw_unique_skips_duplicate_tickets_of_same_user(env, monkeypatch):
    m = _active(env, num_winners=3, no_duplicate_entries=False)
    m.try_register_entry(_msg("!join", "a", "1"))
    m.try_register_entry(_msg("!join", "a", "1"))
    m.try_register_entry(_msg("!join", "b", "2"))
    monkeypatch.setattr(gmod.random, "choice", lambda seq: seq[0])
    ok, _msg_text, winners = m.draw_winners()
    assert winners == ["a", "b"]  # only 2 unique users even though 3 requested


def test_draw_non_unique_uses_sample_and_keeps_pool(env, monkeypatch):
    m = _active(env, num_winners=5, unique_winners_per_draw=False, remove_winners_from_pool=False)
    m.try_register_entry(_msg("!join", "a", "1"))
    m.try_register_entry(_msg("!join", "b", "2"))
    calls = []

    def fake_sample(seq, k):
        calls.append(k)
        return list(reversed(seq))[:k]

    monkeypatch.setattr(gmod.random, "sample", fake_sample)
    ok, _m, winners = m.draw_winners()
    assert calls == [2]
    assert winners == ["b", "a"]
    assert m.get_pool_size() == 2


def test_draw_no_winners_selected(env, monkeypatch):
    m = _active(env, unique_winners_per_draw=False)
    m.try_register_entry(_msg("!join"))
    monkeypatch.setattr(gmod.random, "sample", lambda seq, k: [])
    assert m.draw_winners() == (False, "No winners selected.", [])


def test_draw_announcement_returns_false(env):
    env.announce_result = False
    m = _active(env)
    m.try_register_entry(_msg("!join"))
    ok, msg, winners = m.draw_winners()
    assert ok is True
    assert msg == "Winners drawn, but the chat announcement didn't send."
    assert m.get_status()["last_draw"]["announced"] is False
    assert "Failed to send announcement" in m.get_last_error()
    assert env.events[-1][0] == "giveaway_winner"


def test_draw_announcement_raises(env):
    env.announce_raises = True
    m = _active(env)
    m.try_register_entry(_msg("!join"))
    ok, msg, _w = m.draw_winners()
    assert ok is True and "didn't send" in msg
    assert m.get_last_error() == "RuntimeError: chat down"
    assert m.get_status()["last_draw"]["announced"] is False


def test_draw_banner_and_banner_failure(env):
    m = _active(env, announce_winners_as_banner=True, winning_message_template="{winner} wins")
    m.try_register_entry(_msg("!join", "a", "1"))
    m.draw_winners()
    assert env.banners == [("a wins", "giveaway")]
    assert m.get_status()["last_draw"]["banner"] is True
    env.banner_raises = True
    m.try_register_entry(_msg("!join", "b", "2"))
    ok, _msg_text, _w = m.draw_winners()
    assert ok is True


def test_draw_stats_failure_ignored(env, monkeypatch):
    m = _active(env)
    m.try_register_entry(_msg("!join"))

    def boom():
        raise RuntimeError("stats")

    monkeypatch.setattr(statistics_manager, "get_statistics_manager", boom)
    assert m.draw_winners()[0] is True


# --------------------------------------------------------------------------- re-announce


def test_announce_last_draw_without_winners(env):
    m = gmod.GiveawayManager()
    assert m.announce_last_draw() == (False, "No winners to announce yet.")


def test_announce_last_draw_ok_with_banner(env):
    m = gmod.GiveawayManager()
    m.update_config({"announce_winners_as_banner": True, "winning_message_template": "Yay {winners} ({count})"})
    m._last_draw = {"winners": ["a", "b"]}
    assert m.announce_last_draw() == (True, "Announced again.")
    assert env.announcements[-1] == ("Yay a, b (2)", "primary")
    assert env.banners[-1] == ("Yay a, b (2)", "giveaway")


def test_announce_last_draw_failures(env):
    m = gmod.GiveawayManager()
    m.update_config({"announce_winners_as_banner": True})
    m._last_draw = {"winners": ["a"]}
    env.announce_result = False
    assert m.announce_last_draw()[0] is False
    env.announce_raises = True
    env.banner_raises = True
    ok, msg = m.announce_last_draw()
    assert ok is False
    assert "check the chatbot connection" in msg
