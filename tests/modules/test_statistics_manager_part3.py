# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""StatisticsManager recorders (alerts, hype trains, connectors, chatbot, quotes, giveaways,
polls, chat, templates), loading and saving lifetime totals, per-user views and rankings,
periodic saving, data integrity and the module-level singleton helpers."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

import modules  # noqa: E402
import modules.statistics_manager as sm  # noqa: E402
import time as _time
import types as _types


def _local_clock(module, monkeypatch, **fakes):
    """Give ``module`` its own copy of ``time`` with some functions replaced.

    Patching the real time module would hand the fake clock to every thread in the process.
    """
    clock = _types.SimpleNamespace(**{k: getattr(_time, k) for k in dir(_time) if not k.startswith("__")})
    for name, fn in fakes.items():
        setattr(clock, name, fn)
    monkeypatch.setattr(module, "time", clock)


SYS = "__system__"


def _shutdown(m):
    m.is_running = False
    try:
        m._close_statistics_db()
    except Exception:
        pass
    for t in (m._event_flush_thread, m._username_backfill_thread, m._periodic_thread):
        if isinstance(t, threading.Thread) and t is not threading.current_thread():
            t.join(2)
    with m._stats_db_pool_lock:
        for c in m._stats_db_pool:
            c.close()
        m._stats_db_pool.clear()


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "data_root"
    root.mkdir()
    monkeypatch.setattr(sm, "get_data_path", lambda rel: os.path.join(str(root), rel))
    old = {"value": None}
    monkeypatch.setattr(sm, "get_data", lambda path: old["value"])
    saved = []
    set_result = {"value": True}

    def fake_set_data(path, value):
        saved.append((path, value))
        return set_result["value"]

    monkeypatch.setattr(sm, "set_data", fake_set_data)
    notes = []
    import modules.notification_engine as ne

    monkeypatch.setattr(ne, "notify", lambda message, **kw: notes.append(kw))
    managers = []

    def make():
        m = sm.StatisticsManager()
        if m._username_backfill_thread is not None:
            m._username_backfill_thread.join(2)
        managers.append(m)
        return m

    ns = SimpleNamespace(root=root, old=old, saved=saved, set_result=set_result, notes=notes, make=make, managers=managers)
    yield ns
    for m in managers:
        _shutdown(m)


@pytest.fixture
def mgr(env):
    return env.make()


@pytest.fixture
def off_mgr(env):
    """A manager whose statistics database could not be opened."""
    (env.root / "data" / "statistics.db").mkdir(parents=True)
    m = env.make()
    assert not m._stats_db_initialized
    return m


def events(m):
    m._flush_pending_events()
    conn = sqlite3.connect(m._stats_db_path)
    try:
        return conn.execute("SELECT username, event_type, amount, alert_name FROM user_events ORDER BY id").fetchall()
    finally:
        conn.close()


def lifetime_json(m, category):
    conn = sqlite3.connect(m._stats_db_path)
    try:
        row = conn.execute("SELECT data_json FROM lifetime_totals WHERE category = ?", (category,)).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        conn.close()


def set_lifetime(m, category, value):
    conn = sqlite3.connect(m._stats_db_path)
    conn.execute("INSERT OR REPLACE INTO lifetime_totals (category, data_json) VALUES (?, ?)", (category, json.dumps(value)))
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- alert recorders


def test_bit_alerts_count_totals_users_type_and_event(mgr):
    mgr.increment_bit_alerts(100, "Alice", "Cheer")
    mgr.increment_bit_alerts(50)
    a = mgr.data.alerts
    assert (a.bit_alerts_played, a.total_bits) == (2, 150)
    u = a.user_stats["Alice"]
    assert (u.bit_alerts_played, u.total_bits, u.total_alerts) == (1, 100, 1)
    assert u.first_seen == u.last_seen
    assert a.alert_type_stats["bit"].trigger_count == 2 and a.alert_type_stats["bit"].alert_name == "Cheer"
    assert events(mgr) == [("Alice", "bit", 100.0, "Cheer")]


@pytest.mark.xfail(strict=True, reason="BUG: bit/gift/watch-streak recorders index user_stats by the raw name; a case variant of a known viewer raises KeyError")
@pytest.mark.parametrize("record", ["bits", "gifts", "streak"])
def test_recorders_accept_a_known_viewer_in_different_case(mgr, record):
    calls = {
        "bits": lambda name: mgr.increment_bit_alerts(10, name),
        "gifts": lambda name: mgr.increment_gift_subs(1, name),
        "streak": lambda name: mgr.increment_watch_streak_alerts(3, name),
    }[record]
    calls("Alice")
    calls("alice")  # e.g. login name vs display name, or a viewer loaded from statistics.db
    assert mgr.data.alerts.user_stats["Alice"].total_alerts == 2
    assert len(events(mgr)) == 2


def test_track_user_alert_counts_unknown_alert_kinds_as_totals_only(mgr):
    mgr._track_user_alert("Zed", "no_such_counter")
    mgr._track_user_alert("Zed", "raids")
    u = mgr.data.alerts.user_stats["Zed"]
    assert (u.total_alerts, u.raids) == (2, 1)


def test_track_alert_type_fills_in_a_late_alert_name(mgr):
    mgr._track_alert_type("follow")
    mgr._track_alert_type("follow", "Follow Alert")
    mgr._track_alert_type("follow", "Other")
    st = mgr.data.alerts.alert_type_stats["follow"]
    assert (st.trigger_count, st.alert_name, st.alert_type) == (3, "Follow Alert", "follow")
    assert st.first_triggered <= st.last_triggered


@pytest.mark.parametrize(
    "method,counter,user_counter,event_type,alert_type",
    [
        ("increment_resubs", "resubs_played", "resubs_played", "resub", "resub"),
        ("increment_new_subs", "new_subs_played", "new_subs_played", "sub", "sub"),
        ("increment_follow_alerts", "follow_alerts_played", "follow_alerts_played", "follow", "follow"),
        ("increment_raids", "raids", "raids", "raid", "raid"),
        ("increment_donations", "donations", "donations", "donation", "donation"),
    ],
)
def test_simple_alert_recorders(mgr, method, counter, user_counter, event_type, alert_type):
    getattr(mgr, method)("Viewer", "My Alert")
    getattr(mgr, method)()
    assert getattr(mgr.data.alerts, counter) == 2
    assert getattr(mgr.data.alerts.user_stats["Viewer"], user_counter) == 1
    assert mgr.data.alerts.alert_type_stats[alert_type].trigger_count == 2
    assert events(mgr) == [("Viewer", event_type, 0.0, "My Alert")]


def test_gift_subs(mgr):
    mgr.increment_gift_subs(5, "Gifter", "Gift")
    mgr.increment_gift_subs()
    a = mgr.data.alerts
    assert (a.gift_subs_played, a.total_gift_subs) == (2, 6)
    assert (a.user_stats["Gifter"].gift_subs_played, a.user_stats["Gifter"].total_gift_subs) == (1, 5)
    assert events(mgr) == [("Gifter", "giftsub", 5.0, "Gift")]


def test_point_alerts_record_points_and_tolerate_bad_amounts(mgr):
    mgr.increment_point_alerts("Pat", "Hydrate", 250)
    mgr.increment_point_alerts("Pat", "Hydrate", "lots")
    mgr.increment_point_alerts()
    assert mgr.data.alerts.point_alerts_redeemed == 3
    assert mgr.data.alerts.user_stats["Pat"].point_alerts_redeemed == 2
    assert mgr.data.alerts.alert_type_stats["point"].trigger_count == 3
    assert events(mgr) == [("Pat", "point_redeem", 250.0, "Hydrate"), ("Pat", "point_redeem", 0.0, "Hydrate")]


@pytest.mark.parametrize("amount", [None, "100", [5], 0, -3, 0.4])
def test_channel_points_ignore_invalid_or_non_positive_amounts(mgr, amount):
    mgr.increment_channel_points_redeemed(amount, "Pat")
    assert mgr.data.alerts.total_channel_points_redeemed == 0
    assert mgr.data.alerts.user_stats == {}


def test_channel_points_add_to_totals_and_viewer(mgr):
    mgr.increment_channel_points_redeemed(250.9, "Pat")
    mgr.increment_channel_points_redeemed(100)
    assert mgr.data.alerts.total_channel_points_redeemed == 350
    u = mgr.data.alerts.user_stats["Pat"]
    assert u.channel_points_redeemed == 250 and u.total_alerts == 0


@pytest.mark.parametrize("streak", ["abc", None, 0, -1])
def test_watch_streak_ignores_invalid_counts(mgr, streak):
    mgr.increment_watch_streak_alerts(streak, "Kim")
    assert mgr.data.alerts.watch_streak_alerts_played == 0
    assert mgr.data.alerts.alert_type_stats == {}


def test_watch_streak_tracks_peaks(mgr):
    mgr.increment_watch_streak_alerts("5", "Kim", "Streak")
    mgr.increment_watch_streak_alerts(3, "Kim")
    mgr.increment_watch_streak_alerts(9)
    a = mgr.data.alerts
    assert a.watch_streak_alerts_played == 3 and a.highest_watch_streak_seen == 9
    u = a.user_stats["Kim"]
    assert (u.watch_streak_alerts_played, u.highest_watch_streak) == (2, 5)
    assert events(mgr) == [("Kim", "watch_streak", 5.0, "Streak"), ("Kim", "watch_streak", 3.0, "")]


# --------------------------------------------------------------------------- hype trains


def test_hype_train_levels(mgr):
    mgr.increment_hype_train_completion(2)
    mgr.increment_hype_train_completion("2")
    mgr.increment_hype_train_completion(11)
    mgr.increment_hype_train_completion("level five")
    assert mgr.data.hype_trains.level_completions == {2: 2, 11: 1}
    assert mgr.data.hype_trains.total_completions == 3


def test_hype_train_repairs_a_corrupted_level_map(mgr):
    mgr.data.hype_trains.level_completions = [1, 2]
    mgr.increment_hype_train_completion(1)
    assert mgr.data.hype_trains.level_completions == {1: 1}


# --------------------------------------------------------------------------- connectors


def test_connectors_triggered(mgr):
    mgr.increment_connectors_triggered("Ann", "Lights")
    mgr.increment_connectors_triggered("Ann")
    mgr.increment_connectors_triggered(connector_name="Lights")
    mgr.increment_connectors_triggered()
    c = mgr.data.connectors
    assert c.connectors_triggered == 4
    agg = c.user_stats["Ann"]
    assert (agg.connectors_triggered, agg.total_triggers) == (2, 2)
    per = c.user_connector_stats["Ann"]
    assert per.connector_usage == {"Lights": 1} and per.total_triggers == 1
    assert c.connector_stats["Lights"].trigger_count == 2
    assert events(mgr) == [("Ann", "connector", 1.0, "Lights"), ("Ann", "connector", 1.0, "")]


def test_connector_triggers_with_a_count(mgr):
    mgr.increment_connector_triggers(3, "Ann", "Sound")
    mgr.increment_connector_triggers(2, "Ann")
    mgr.increment_connector_triggers(4, connector_name="Sound")
    mgr.increment_connector_triggers()
    c = mgr.data.connectors
    assert c.total_triggers == 10
    agg = c.user_stats["Ann"]
    assert (agg.connectors_triggered, agg.total_triggers) == (2, 5)
    per = c.user_connector_stats["Ann"]
    assert per.connector_usage == {"Sound": 3} and per.connectors_triggered == 3
    assert c.connector_stats["Sound"].trigger_count == 7
    assert events(mgr) == [("Ann", "connector", 3.0, "Sound")]


# --------------------------------------------------------------------------- chatbot


def test_commands_triggered(mgr):
    mgr.increment_commands_triggered("Ann", "!hi")
    mgr.increment_commands_triggered("Ann", "!hi")
    mgr.increment_commands_triggered("Ann", "!bye")
    mgr.increment_commands_triggered("Bob")
    mgr.increment_commands_triggered(command_name="!hi")
    cb = mgr.data.chatbot
    assert (cb.commands_triggered, cb.total_interactions) == (5, 5)
    assert cb.user_stats["Ann"].commands_triggered == 3 and cb.user_stats["Ann"].total_interactions == 3
    assert cb.user_stats["Bob"].commands_triggered == 1
    assert cb.user_command_stats["Ann"].command_usage == {"!hi": 2, "!bye": 1}
    assert "Bob" not in cb.user_command_stats
    assert cb.command_stats["!hi"].usage_count == 3 and cb.command_stats["!bye"].usage_count == 1
    assert [e[1:] for e in events(mgr)] == [("chatbot_command", 1.0, "!hi")] * 2 + [("chatbot_command", 1.0, "!bye"), ("chatbot_command", 1.0, "")]


def test_events_triggered(mgr):
    mgr.increment_events_triggered("Ann", "Timer")
    mgr.increment_events_triggered("Ann", "Timer")
    mgr.increment_events_triggered("Bob")
    mgr.increment_events_triggered(event_name="Raid msg")
    cb = mgr.data.chatbot
    assert (cb.events_triggered, cb.total_interactions) == (4, 4)
    assert cb.user_stats["Ann"].events_triggered == 2
    assert cb.user_event_stats["Ann"].event_usage == {"Timer": 2}
    assert cb.event_stats["Timer"].trigger_count == 2 and cb.event_stats["Raid msg"].trigger_count == 1
    assert [e[1:] for e in events(mgr)] == [("chatbot_event", 1.0, "Timer")] * 2 + [("chatbot_event", 1.0, "")]


def test_per_user_command_first_seen_is_kept_after_the_first_use(mgr, monkeypatch):
    clock = iter([100.0, 200.0, 300.0, 400.0])
    _local_clock(sm, monkeypatch, time=lambda: next(clock))
    mgr._track_user_command("Ann", "!a")
    mgr._track_user_command("Ann", "!a")
    u = mgr.data.chatbot.user_command_stats["Ann"]
    assert (u.first_seen, u.last_seen) == (100.0, 200.0)
    mgr._track_user_event("Ann", "e")
    mgr._track_user_event("Ann", "e")
    ue = mgr.data.chatbot.user_event_stats["Ann"]
    assert (ue.first_seen, ue.last_seen) == (300.0, 400.0)


# --------------------------------------------------------------------------- quotes, giveaways, polls, chat


def test_quote_redeemed(mgr):
    mgr.increment_quote_redeemed(7, "Ann")
    mgr.increment_quote_redeemed(7, "Ann")
    mgr.increment_quote_redeemed("3")
    q = mgr.data.quotes
    assert q.total_quotes_redeemed == 3 and q.individual_quote_usage == {7: 2, "3": 1}
    u = q.user_stats["Ann"]
    assert u.total_quotes_redeemed == 2 and u.individual_quote_usage == {7: 2}
    assert events(mgr) == [("Ann", "quote_redeem", 1.0, "7")] * 2


def test_giveaway_entries_and_rounds(mgr):
    mgr.record_giveaway_entry("Ann")
    mgr.record_giveaway_entry()
    mgr.record_giveaway_round_complete()
    g = mgr.data.giveaways
    assert (g.total_entry_events, g.giveaways_completed) == (2, 1)
    assert events(mgr) == [("Ann", "giveaway_entry", 1.0, ""), (SYS, "giveaway_draw_complete", 1.0, "")]


def test_giveaway_winners_merge_case_and_stored_wins(mgr):
    mgr.record_giveaway_winner("")
    mgr.record_giveaway_winner("Ann")
    mgr.record_giveaway_winner("ANN")
    mgr._spill_raw_user_map("giveaways.user_wins", {"Stored": {"wins": 4}})
    mgr.record_giveaway_winner("stored")
    assert mgr.data.giveaways.user_wins == {"Ann": 2, "Stored": 5}
    assert [e[:2] for e in events(mgr)] == [("Ann", "giveaway_win"), ("ANN", "giveaway_win"), ("stored", "giveaway_win")]


def test_poll_finished_counts_votes_and_wins_by_keyword(mgr):
    mgr.record_poll_finished(
        options=[
            {"keyword": "Pizza", "name": "Pizza!", "votes": 5},
            {"keyword": "tacos", "votes": "3"},
            {"keyword": "sushi", "votes": 0},
            {"keyword": "", "votes": 9},
            {"keyword": "bad", "votes": "many"},
            {"keyword": "Ramen", "name": "Ramen", "votes": 0},
            "not a dict",
        ],
        winners=[{"keyword": "pizza"}, {"keyword": "RAMEN"}, {"keyword": "ghost"}, {"keyword": ""}, "x"],
    )
    p = mgr.data.polls
    assert p.polls_completed == 1 and p.total_votes == 8
    assert p.options == {
        "pizza": {"name": "Pizza!", "votes": 5, "wins": 1},
        "tacos": {"name": "tacos", "votes": 3, "wins": 0},
        "ramen": {"name": "Ramen", "votes": 0, "wins": 1},
        "ghost": {"name": "ghost", "votes": 0, "wins": 1},
    }
    evs = events(mgr)
    assert sorted(e[3] for e in evs if e[1] == "poll_win") == ["Pizza!", "Ramen", "ghost"]
    assert evs[-1] == (SYS, "poll_complete", 8.0, "")


def test_poll_finished_updates_existing_options(mgr):
    mgr.data.polls.options["pizza"] = "corrupt"
    mgr.record_poll_finished([{"keyword": "pizza", "votes": 2}], [{"keyword": "pizza"}])
    mgr.record_poll_finished([{"keyword": "Pizza", "name": "Pizza pie", "votes": 1}], [])
    mgr.record_poll_finished()
    assert mgr.data.polls.options == {"pizza": {"name": "Pizza pie", "votes": 3, "wins": 1}}
    assert mgr.data.polls.polls_completed == 3 and mgr.data.polls.total_votes == 3
    mgr.data.polls.options["broken"] = "x"
    mgr.data.polls.options["lost"] = {"name": "lost", "votes": 1, "wins": 0}
    mgr.record_poll_finished([], [{"keyword": "broken"}])
    assert mgr.data.polls.options["broken"] == {"name": "broken", "votes": 0, "wins": 1}


def test_top_poll_option(mgr):
    assert mgr._top_poll_option("votes") == {"name": "", "keyword": "", "count": 0}
    mgr.data.polls.options = {"a": {"votes": 2, "wins": 5}, "b": {"name": "Bee", "votes": 7}, "c": "junk"}
    assert mgr._top_poll_option("votes") == {"name": "Bee", "keyword": "b", "count": 7}
    assert mgr._top_poll_option("wins") == {"name": "a", "keyword": "a", "count": 5}


def test_twitch_messages(mgr):
    mgr.increment_twitch_messages("Ann")
    mgr.increment_twitch_messages("ann")
    mgr.increment_twitch_messages()
    c = mgr.data.chat
    assert (c.twitch_messages_received, c.total_messages) == (3, 3)
    assert list(c.user_stats) == ["Ann"] and c.user_stats["Ann"].total_messages == 2
    assert [e[:2] for e in events(mgr)] == [("Ann", "chat_message"), ("ann", "chat_message")]


# --------------------------------------------------------------------------- templates


def test_template_stats_set_increment_and_merge(mgr):
    assert mgr.submit_template_stat("tpl", "score", 5) is True
    assert mgr.submit_template_stat("tpl", "score", 2.5, increment=True)
    assert mgr.submit_template_stat("tpl", "names", ["a"], increment=True)  # first time: set
    assert mgr.submit_template_stat("tpl", "names", ["b"], increment=True)  # lists concatenate
    assert mgr.submit_template_stat("tpl", "mood", "happy")
    assert mgr.submit_template_stat("tpl", "mood", "calm", increment=True)  # non-numeric: replaced
    assert mgr.increment_template_counter("tpl", "hits") is True
    assert mgr.increment_template_counter("tpl", "hits", 4)
    stats = mgr.get_template_stats("tpl")
    assert stats["template_name"] == "tpl"
    assert stats["custom_stats"] == {"score": 7.5, "names": ["a", "b"], "mood": "calm", "hits": 5}
    stats["custom_stats"]["score"] = 0
    assert mgr.data.templates["tpl"].custom_stats["score"] == 7.5  # a copy was returned
    assert mgr.get_template_stats("missing") == {}
    assert set(mgr.get_template_stats()) == {"tpl"}


def test_template_stat_errors_return_false(mgr):
    assert mgr.submit_template_stat(["unhashable"], "x", 1) is False


def test_reset_template_stats(mgr):
    for name in ("a", "b"):
        mgr.submit_template_stat(name, "x", 1)
        mgr.submit_template_stat(name, "y", 2)
    mgr.reset_template_stats("a", "x")
    mgr.reset_template_stats("a", "nope")
    mgr.reset_template_stats("ghost")
    assert mgr.data.templates["a"].custom_stats == {"y": 2}
    mgr.reset_template_stats("b")
    assert mgr.data.templates["b"].custom_stats == {}
    mgr.reset_template_stats()
    assert mgr.data.templates == {}


# --------------------------------------------------------------------------- live counts from other managers


def _fake_module(monkeypatch, name, module):
    monkeypatch.setitem(sys.modules, f"modules.{name}", module)
    monkeypatch.setattr(modules, name, module, raising=False)


def test_connector_count(mgr, monkeypatch):
    fake = ModuleType("connector_manager")
    fake.connector_manager = SimpleNamespace(connectors={"a": 1, "b": 2})
    _fake_module(monkeypatch, "connector_manager", fake)
    assert mgr._get_connector_count() == 2
    _fake_module(monkeypatch, "connector_manager", ModuleType("connector_manager"))
    assert mgr._get_connector_count() == 0
    broken = ModuleType("connector_manager")
    broken.connector_manager = SimpleNamespace()  # no .connectors
    _fake_module(monkeypatch, "connector_manager", broken)
    assert mgr._get_connector_count() == 0


@pytest.mark.parametrize("method,attr,getter", [
    ("_get_commands_count", "commands", "get_all_commands"),
    ("_get_events_count", "events", "get_all_events"),
    ("_get_quotes_count", "quotes", "get_all_quotes"),
])
def test_chatbot_counts(mgr, monkeypatch, method, attr, getter):
    bot = SimpleNamespace(**{attr: {"x": 1}, getter: lambda: [1, 2, 3]})
    fake = ModuleType("chatbot_manager")
    fake.get_manager = lambda: bot
    _fake_module(monkeypatch, "chatbot_manager", fake)
    assert getattr(mgr, method)() == 3
    setattr(bot, attr, None)
    assert getattr(mgr, method)() == 0
    fake.get_manager = Mock(side_effect=RuntimeError("not ready"))
    assert getattr(mgr, method)() == 0


def _stub_counts(monkeypatch, m, connectors=2, commands=3, events_=4, quotes=5):
    monkeypatch.setattr(m, "_get_connector_count", lambda: connectors)
    monkeypatch.setattr(m, "_get_commands_count", lambda: commands)
    monkeypatch.setattr(m, "_get_events_count", lambda: events_)
    monkeypatch.setattr(m, "_get_quotes_count", lambda: quotes)


def test_all_statistics_shape_and_averages(mgr, monkeypatch):
    _stub_counts(monkeypatch, mgr)
    empty = mgr.get_all_statistics()
    assert empty["giveaways"]["average_entries_per_giveaway"] == 0.0
    assert empty["polls"]["average_votes_per_poll"] == 0.0
    mgr.increment_bit_alerts(10)
    mgr.increment_hype_train_completion(3)
    mgr.record_giveaway_entry()
    mgr.record_giveaway_entry()
    mgr.record_giveaway_entry()
    mgr.record_giveaway_round_complete()
    mgr.record_giveaway_round_complete()
    mgr.record_giveaway_winner("Ann")
    mgr.record_poll_finished([{"keyword": "a", "votes": 3}], [{"keyword": "a"}])
    mgr.data.polls.options["junk"] = "x"
    mgr.increment_quote_redeemed(1)
    mgr.submit_template_stat("tpl", "n", 1)
    stats = mgr.get_all_statistics()
    assert stats["alerts"]["bit_alerts_played"] == 1 and stats["alerts"]["total_bits"] == 10
    assert stats["hype_trains"] == {"level_completions": {3: 1}, "total_completions": 1}
    assert stats["connectors"]["connectors_created"] == 2
    assert stats["chatbot"]["commands_created"] == 3 and stats["chatbot"]["events_created"] == 4
    assert stats["quotes"]["quotes_created"] == 5 and stats["quotes"]["individual_quote_usage"] == {1: 1}
    assert stats["giveaways"]["average_entries_per_giveaway"] == 1.5
    assert stats["giveaways"]["user_wins"] == {"Ann": 1}
    assert stats["polls"]["average_votes_per_poll"] == 3.0
    assert stats["polls"]["most_votes"] == {"name": "a", "keyword": "a", "count": 3}
    assert stats["polls"]["options"] == {"a": {"name": "a", "votes": 3, "wins": 1}}
    assert stats["templates"]["tpl"]["custom_stats"] == {"n": 1}
    assert stats["session"]["session_duration"] >= 0
    stats["hype_trains"]["level_completions"][9] = 1
    assert 9 not in mgr.data.hype_trains.level_completions


def test_statistics_summary(mgr, monkeypatch):
    _stub_counts(monkeypatch, mgr)
    mgr.increment_bit_alerts(1)
    mgr.increment_resubs()
    mgr.increment_watch_streak_alerts(2)
    mgr.increment_hype_train_completion(1)
    mgr.increment_connector_triggers(4)
    mgr.increment_commands_triggered()
    mgr.increment_quote_redeemed(1)
    mgr.increment_twitch_messages()
    s = mgr.get_statistics_summary()
    assert s["total_alerts"] == 3 and s["total_hype_trains"] == 1
    assert s["total_connectors"] == 2 and s["total_connector_triggers"] == 4
    assert s["total_chatbot_items"] == 7 and s["total_chatbot_interactions"] == 1
    assert s["total_quotes"] == 5 and s["total_quote_redeems"] == 1 and s["total_chat_messages"] == 1
    assert 0 <= s["session_duration_hours"] < 1


# --------------------------------------------------------------------------- reset


@pytest.mark.parametrize("category,attr,cls", [
    ("alerts", "alerts", sm.AlertStatistics),
    ("hype_trains", "hype_trains", sm.HypeTrainStatistics),
    ("connectors", "connectors", sm.ConnectorStatistics),
    ("chatbot", "chatbot", sm.ChatbotStatistics),
    ("quotes", "quotes", sm.QuoteStatistics),
    ("giveaways", "giveaways", sm.GiveawayStatistics),
    ("chat", "chat", sm.ChatStatistics),
])
def test_reset_one_category(mgr, monkeypatch, category, attr, cls):
    save = Mock()
    monkeypatch.setattr(mgr, "_save_to_database", save)
    mgr.increment_twitch_messages()
    mgr.increment_bit_alerts(5)
    before = mgr.data
    mgr.reset_statistics(category)
    assert getattr(mgr.data, attr) == cls()
    assert mgr.data is before
    if category != "chat":
        assert mgr.data.chat.total_messages == 1
    save.assert_called_once()


def test_reset_everything_and_unknown_category(mgr, monkeypatch):
    save = Mock()
    monkeypatch.setattr(mgr, "_save_to_database", save)
    mgr.increment_twitch_messages()
    mgr.reset_statistics("nonsense")
    assert mgr.data.chat.total_messages == 1
    mgr.reset_statistics()
    assert mgr.data.chat.total_messages == 0
    assert save.call_count == 2


# --------------------------------------------------------------------------- save and load


def test_save_then_load_restores_every_category(env, mgr, monkeypatch):
    mgr.increment_bit_alerts(100, "Alice", "Cheer")
    mgr.increment_watch_streak_alerts(6, "Alice")
    mgr.increment_hype_train_completion(4)
    mgr.increment_connectors_triggered("Bob", "Lights")
    mgr.increment_commands_triggered("Cara", "!hi")
    mgr.increment_events_triggered("Cara", "Timer")
    mgr.increment_quote_redeemed("q1", "Dan")
    mgr.record_giveaway_entry("Eve")
    mgr.record_giveaway_round_complete()
    mgr.record_giveaway_winner("Eve")
    mgr.record_poll_finished([{"keyword": "yes", "votes": 4}], [{"keyword": "yes"}])
    mgr.increment_twitch_messages("Fay")
    mgr.submit_template_stat("tpl", "count", 3)
    mgr._save_to_database()
    assert mgr.last_save_ok is True
    chat_json = lifetime_json(mgr, "chat")
    assert "user_stats" not in chat_json and chat_json["total_messages"] == 1
    assert lifetime_json(mgr, "hype_trains") == {"level_completions": {"4": 1}, "total_completions": 1}
    _shutdown(mgr)

    again = env.make()
    d = again.data
    assert (d.alerts.bit_alerts_played, d.alerts.total_bits, d.alerts.highest_watch_streak_seen) == (1, 100, 6)
    assert d.alerts.user_stats == {}  # per-user rows stay in SQLite until needed
    assert d.alerts.alert_type_stats["bit"].alert_name == "Cheer"
    assert d.hype_trains.level_completions == {4: 1}
    assert d.connectors.connectors_triggered == 1 and d.connectors.connector_stats["Lights"].trigger_count == 1
    assert d.chatbot.command_stats["!hi"].usage_count == 1 and d.chatbot.event_stats["Timer"].trigger_count == 1
    assert d.chatbot.total_interactions == 2
    assert d.quotes.total_quotes_redeemed == 1 and d.quotes.individual_quote_usage == {"q1": 1}
    assert (d.giveaways.giveaways_completed, d.giveaways.total_entry_events, d.giveaways.user_wins) == (1, 1, {})
    assert d.polls.options == {"yes": {"name": "yes", "votes": 4, "wins": 1}} and d.polls.polls_completed == 1
    assert d.chat.total_messages == 1
    assert d.templates["tpl"].custom_stats == {"count": 3}
    assert d.session.start_time >= mgr.data.session.start_time
    # Per-user rows come back on demand.
    alice = again.get_user_statistics("alice")
    assert alice["username"] == "Alice" and alice["alerts"]["highest_watch_streak"] == 6
    assert again.get_user_statistics("eve")["giveaways"] == {"giveaway_wins": 1}


def test_load_parses_odd_hype_train_and_poll_rows(env, mgr):
    set_lifetime(mgr, "hype_trains", {"level_completions": [None, 2, 3], "total_completions": 5})
    set_lifetime(mgr, "polls", {"polls_completed": "2", "total_votes": None,
                                "options": {"": {"votes": 1}, "junk": "x", "Yes": {"votes": "3", "wins": 1}}})
    _shutdown(mgr)
    again = env.make()
    assert again.data.hype_trains.level_completions == {1: 2, 2: 3}
    assert again.data.hype_trains.total_completions == 5
    assert again.data.polls.polls_completed == 2 and again.data.polls.total_votes == 0
    assert again.data.polls.options == {"Yes": {"name": "Yes", "votes": 3, "wins": 1}}


@pytest.mark.parametrize("raw", ["abc", None])
def test_load_resets_unusable_level_maps(env, mgr, raw):
    set_lifetime(mgr, "hype_trains", {"level_completions": raw, "total_completions": 1})
    _shutdown(mgr)
    assert env.make().data.hype_trains.level_completions == {}


def test_load_without_level_map(env, mgr):
    set_lifetime(mgr, "hype_trains", {"total_completions": 2})
    _shutdown(mgr)
    again = env.make()
    assert again.data.hype_trains.level_completions == {} and again.data.hype_trains.total_completions == 2


def test_load_moves_legacy_per_user_maps_into_sqlite(env, mgr):
    set_lifetime(mgr, "chat", {"total_messages": 3, "twitch_messages_received": 3,
                               "user_stats": {"Old": {"total_messages": 3, "twitch_messages_received": 3}}})
    set_lifetime(mgr, "giveaways", {"giveaways_completed": 1, "user_wins": {"Lucky": 2}})
    set_lifetime(mgr, "polls", "not a dict")
    _shutdown(mgr)
    again = env.make()
    assert again.data.chat.user_stats == {} and again.data.chat.total_messages == 3
    assert again.get_user_statistics("old")["chat"]["total_messages"] == 3
    assert again._load_user_payload("giveaways.user_wins", "lucky") == {"username": "Lucky", "data": {"wins": 2}}


def test_load_error_keeps_defaults(env, mgr, caplog):
    set_lifetime(mgr, "alerts", {"no_such_field": 1})
    _shutdown(mgr)
    again = env.make()
    assert again.data.alerts == sm.AlertStatistics()
    assert "Error loading statistics from database" in caplog.text


def test_load_repairs_float_hype_counts_quietly(env, mgr, caplog):
    set_lifetime(mgr, "hype_trains", {"level_completions": {"1": 2.0}, "total_completions": 2})
    _shutdown(mgr)
    again = env.make()
    assert again.data.hype_trains.level_completions == {1: 2.0}
    assert "Data integrity issues found" in caplog.text


@pytest.mark.xfail(strict=True, reason="BUG: repair_data_integrity calls the missing save_statistics(); a bad hype-train count aborts loading every later category")
def test_bad_hype_train_count_does_not_lose_other_totals(env, mgr):
    set_lifetime(mgr, "hype_trains", {"level_completions": {"1": "lots"}, "total_completions": 1})
    set_lifetime(mgr, "chat", {"total_messages": 42, "twitch_messages_received": 42})
    _shutdown(mgr)
    again = env.make()
    assert again.data.chat.total_messages == 42


def test_off_database_loads_and_saves_through_database_manager(env):
    env.old["value"] = {"data": {"chat": {"total_messages": 7, "twitch_messages_received": 7}}}
    (env.root / "data" / "statistics.db").mkdir(parents=True)
    m = env.make()
    assert not m._stats_db_initialized
    assert m.data.chat.total_messages == 7
    m._save_to_database()
    assert m.last_save_ok is True
    path, value = env.saved[-1]
    assert path == "statistics" and value["version"] == "1.0"
    assert value["data"]["chat"]["total_messages"] == 7 and "user_stats" in value["data"]["chat"]
    env.set_result["value"] = False
    m._save_to_database()
    assert m.last_save_ok is False


def test_save_failure_notifies(mgr, env, monkeypatch):
    monkeypatch.setattr(mgr, "_persist_user_maps", Mock(side_effect=RuntimeError("disk")))
    mgr._save_to_database()
    assert mgr.last_save_ok is False
    assert env.notes[-1]["dedupe_key"] == "stats:save_failed"


def test_save_writes_a_snapshot_of_every_field(mgr, monkeypatch):
    captured = {}
    monkeypatch.setattr(mgr, "_save_lifetime_totals_to_stats_db", lambda d: captured.update(d))
    mgr.data.hype_trains.level_completions = "corrupt"
    mgr.data.polls.options = {"ok": {"name": None, "votes": None}, "bad": "x"}
    mgr.data.alerts.user_stats["U"] = sm.UserAlertStatistics(total_bits=3)
    mgr.data.chatbot.user_command_stats["U"] = sm.UserCommandStatistics(command_usage={"!a": 1})
    mgr.data.chatbot.user_event_stats["U"] = sm.UserEventStatistics(event_usage={"e": 1})
    mgr.data.connectors.user_connector_stats["U"] = sm.UserConnectorStatistics(connector_usage={"c": 1})
    mgr.data.quotes.user_stats["U"] = sm.UserQuoteStatistics(total_quotes_redeemed=1)
    mgr._save_to_database()
    assert captured["hype_trains"]["level_completions"] == {}
    assert captured["polls"]["options"] == {"ok": {"name": "ok", "votes": 0, "wins": 0}}
    assert set(captured) == {"alerts", "hype_trains", "connectors", "chatbot", "quotes", "giveaways", "polls",
                             "chat", "templates", "session"}
    assert captured["session"]["session_duration"] >= 0
    # The per-user maps went to user_lifetime instead.
    assert "user_stats" not in captured["alerts"] and "user_command_stats" not in captured["chatbot"]
    assert mgr._load_user_payload("chatbot.user_command_stats", "u")["data"]["command_usage"] == {"!a": 1}


def test_initialize_with_data(mgr):
    mgr.initialize_with_data({})
    assert mgr.data.alerts == sm.AlertStatistics()
    mgr.initialize_with_data({
        "Statistics": {"data": {
            "alerts": {"raids": 2, "user_stats": {"Ann": {"raids": 2}},
                       "alert_type_stats": {"raid": {"alert_type": "raid", "trigger_count": 2}}},
            "hype_trains": {"level_completions": {"1": 1}, "total_completions": 1},
        }},
    })
    assert mgr.data.alerts.raids == 2 and mgr.data.alerts.user_stats == {}
    assert mgr.data.alerts.alert_type_stats["raid"].trigger_count == 2
    assert mgr.data.hype_trains.total_completions == 1
    assert mgr._load_user_payload("alerts.user_stats", "ann")["data"] == {"raids": 2}


@pytest.mark.parametrize("section", ["stream_sessions", "viewer_engagement", "performance", "settings"])
def test_initialize_with_data_logs_unknown_legacy_sections(mgr, caplog, section):
    mgr.initialize_with_data({"Statistics": {"data": {"alerts": {"donations": 4}, section: {}}}})
    assert mgr.data.alerts.donations == 4
    assert "Error loading statistics from pre-loaded data" in caplog.text


# --------------------------------------------------------------------------- per-user views


def test_user_statistics_load_every_category_from_storage(env, mgr):
    spills = {
        "alerts.user_stats": {"Alice": {"raids": 1, "total_alerts": 1, "highest_watch_streak": 4}},
        "connectors.user_stats": {"Alice": {"connectors_triggered": 2, "total_triggers": 5}},
        "chatbot.user_stats": {"Alice": {"commands_triggered": 1, "total_interactions": 1}},
        "chatbot.user_command_stats": {"Alice": {"command_usage": {"!hi": 1}}},
        "chatbot.user_event_stats": {"Alice": {"event_usage": {"e": 2}}},
        "quotes.user_stats": {"Alice": {"total_quotes_redeemed": 3, "individual_quote_usage": {"1": 3}}},
        "chat.user_stats": {"Alice": {"total_messages": 9, "twitch_messages_received": 9}},
        "giveaways.user_wins": {"Alice": {"wins": 2}},
    }
    for category, users in spills.items():
        mgr._spill_raw_user_map(category, users)
    s = mgr.get_user_statistics("  ALICE ")
    assert s["username"] == "Alice"
    assert s["alerts"]["raids"] == 1 and s["alerts"]["highest_watch_streak"] == 4
    assert s["connectors"]["total_triggers"] == 5
    assert s["chatbot"]["commands_triggered"] == 1
    assert s["quotes"]["individual_quote_usage"] == {"1": 3}
    assert s["giveaways"] == {"giveaway_wins": 2}
    assert s["chat"]["total_messages"] == 9
    usage = mgr.get_user_individual_usage("Alice")
    assert usage == {"username": "Alice", "commands": {"!hi": 1}, "events": {"e": 2}, "connectors": {}, "quotes": {"1": 3}}
    # Already-resident wins are not reloaded.
    mgr.data.giveaways.user_wins["Alice"] = 7
    assert mgr.get_user_statistics("alice")["giveaways"] == {"giveaway_wins": 7}


def test_user_statistics_use_per_connector_rows_when_no_aggregate(mgr):
    mgr.data.connectors.user_connector_stats["Bob"] = sm.UserConnectorStatistics(
        connectors_triggered=1, total_triggers=4, connector_usage={"c": 4})
    s = mgr.get_user_statistics("bob")
    assert s["username"] == "Bob" and s["connectors"]["total_triggers"] == 4
    assert mgr.get_user_individual_usage("Bob")["connectors"] == {"c": 4}


def test_user_statistics_for_unknown_or_blank_names(mgr):
    blank = mgr.get_user_statistics("")
    assert blank == {"username": "", "alerts": {}, "connectors": {}, "chatbot": {}, "quotes": {}, "giveaways": {}, "chat": {}}
    assert mgr.get_user_statistics(None)["username"] == ""
    assert mgr.get_user_statistics("ghost")["alerts"] == {}
    assert mgr.get_user_individual_usage("ghost") == {"username": "ghost", "commands": {}, "events": {}, "connectors": {}, "quotes": {}}


def test_user_activity_summary(mgr, monkeypatch):
    clock = iter(float(x) for x in range(100, 200))
    _local_clock(sm, monkeypatch, time=lambda: next(clock))
    mgr.increment_raids("Ann")
    mgr.increment_twitch_messages("Ann")
    mgr.increment_twitch_messages("Ann")
    mgr.increment_quote_redeemed(1, "Ann")
    mgr.increment_connector_triggers(2, "Ann")
    mgr.increment_commands_triggered("Ann")
    mgr.record_giveaway_winner("Ann")
    s = mgr.get_user_activity_summary("Ann")
    assert s["total_activity_score"] == 10 + 2 + 5 + 3 * 2 + 2
    assert s["categories_active"] == 6
    assert s["first_seen"] == 100.0 and s["most_recent_activity"] > 100
    empty = mgr.get_user_activity_summary("nobody")
    assert empty["categories_active"] == 0 and empty["first_seen"] == 0 and empty["total_activity_score"] == 0


# --------------------------------------------------------------------------- rankings


def test_top_users_by_statistic(mgr):
    a = mgr.data.alerts.user_stats
    a["Big"] = sm.UserAlertStatistics(total_alerts=9, total_bits=0, bit_alerts_played=4, total_gift_subs=0,
                                      gift_subs_played=2, donations=1, point_alerts_redeemed=3,
                                      watch_streak_alerts_played=2, highest_watch_streak=12)
    a["Small"] = sm.UserAlertStatistics(total_alerts=1, total_bits=500, total_gift_subs=7, donations=5,
                                        point_alerts_redeemed=1, watch_streak_alerts_played=5, highest_watch_streak=3)
    mgr.data.quotes.user_stats["Q"] = sm.UserQuoteStatistics(total_quotes_redeemed=4)
    mgr.data.chat.user_stats["C"] = sm.UserChatStatistics(total_messages=11)
    mgr.data.connectors.user_stats["K"] = sm.UserConnectorStatistics(total_triggers=6)
    mgr.data.chatbot.user_stats["B"] = sm.UserChatbotStatistics(total_interactions=8)
    mgr.data.giveaways.user_wins.update({"W1": 1, "W2": 3})
    # A stored user the ranking has to pull in.
    mgr._spill_raw_user_map("chat.user_stats", {"Stored": {"total_messages": 20}})

    def top(kind, limit=10):
        return [(r["username"], r["value"]) for r in mgr.get_top_users_by_statistic(kind, limit)]

    assert top("total_alerts") == [("Big", 9), ("Small", 1)]
    assert top("bits_donated") == [("Small", 500), ("Big", 4)]
    assert top("subs_gifted") == [("Small", 7), ("Big", 2)]
    assert top("donations") == [("Small", 5), ("Big", 1)]
    assert top("points_redeemed") == [("Big", 3), ("Small", 1)]
    assert top("watch_streak_alerts") == [("Small", 5), ("Big", 2)]
    assert top("highest_watch_streak") == [("Big", 12), ("Small", 3)]
    assert top("quotes_redeemed") == [("Q", 4)]
    assert top("chat_messages") == [("Stored", 20), ("C", 11)]
    assert top("connector_triggers") == [("K", 6)]
    assert top("chatbot_interactions") == [("B", 8)]
    assert top("giveaway_wins") == [("W2", 3), ("W1", 1)]
    assert top("giveaway_wins", 1) == [("W2", 3)]
    assert top("unknown") == []
    row = mgr.get_top_users_by_statistic("giveaway_wins")[0]
    assert row["first_seen"] == 0.0 and row["last_seen"] == 0.0


@pytest.mark.parametrize("kind,attr,cls", [
    ("alerts", "alerts", sm.UserAlertStatistics),
    ("chat", "chat", sm.UserChatStatistics),
    ("connectors", "connectors", sm.UserConnectorStatistics),
    ("quotes", "quotes", sm.UserQuoteStatistics),
    ("chatbot", "chatbot", sm.UserChatbotStatistics),
])
def test_recent_users(mgr, kind, attr, cls):
    users = getattr(mgr.data, attr).user_stats
    users["Old"] = cls(first_seen=1.0, last_seen=10.0)
    users["New"] = cls(first_seen=2.0, last_seen=50.0)
    users["Mid"] = cls(first_seen=3.0, last_seen=20.0)
    assert [u["username"] for u in mgr.get_recent_users(kind)] == ["New", "Mid", "Old"]
    assert mgr.get_recent_users(kind, limit=1) == [{"username": "New", "last_seen": 50.0, "first_seen": 2.0}]


def test_recent_users_unknown_type(mgr):
    assert mgr.get_recent_users("hype_trains") == []


def test_top_items_and_users_per_item(mgr):
    for _ in range(3):
        mgr.increment_commands_triggered("Ann", "!a")
        mgr.increment_events_triggered("Ann", "E1")
        mgr.increment_connectors_triggered("Ann", "C1")
        mgr.increment_quote_redeemed("Q1", "Ann")
        mgr.increment_follow_alerts()
    mgr.increment_commands_triggered("Bob", "!a")
    mgr.increment_commands_triggered("Bob", "!b")
    mgr.increment_events_triggered("Bob", "E1")
    mgr.increment_connectors_triggered("Bob", "C1")
    mgr.increment_quote_redeemed("Q1", "Bob")
    mgr.increment_raids()
    assert [(c["command_name"], c["usage_count"]) for c in mgr.get_top_commands()] == [("!a", 4), ("!b", 1)]
    assert len(mgr.get_top_commands(limit=1)) == 1
    assert [(e["event_name"], e["trigger_count"]) for e in mgr.get_top_events()] == [("E1", 4)]
    assert [(c["connector_name"], c["trigger_count"]) for c in mgr.get_top_connectors()] == [("C1", 4)]
    assert [(a["alert_type"], a["trigger_count"]) for a in mgr.get_top_alert_types()] == [("follow", 3), ("raid", 1)]
    assert [(u["username"], u["usage_count"]) for u in mgr.get_top_users_for_command("!a")] == [("Ann", 3), ("Bob", 1)]
    assert mgr.get_top_users_for_command("!zzz") == []
    assert [(u["username"], u["trigger_count"]) for u in mgr.get_top_users_for_event("E1", 1)] == [("Ann", 3)]
    assert [(u["username"], u["trigger_count"]) for u in mgr.get_top_users_for_connector("C1")] == [("Ann", 3), ("Bob", 1)]
    assert [(u["username"], u["usage_count"]) for u in mgr.get_top_users_for_quote("Q1")] == [("Ann", 3), ("Bob", 1)]
    first = mgr.get_top_users_for_command("!a")[0]
    assert set(first) == {"username", "usage_count", "first_used", "last_used"}


# --------------------------------------------------------------------------- integrity


def test_check_data_integrity(mgr):
    assert mgr.check_data_integrity() == []
    mgr.data.hype_trains.level_completions = {"1": 2.5, 2: 3}
    issues = mgr.check_data_integrity()
    assert len(issues) == 2 and "key 1" in issues[0] and "value 2.5" in issues[1]
    mgr.data.hype_trains.level_completions = [1]
    assert "instead of dict" in mgr.check_data_integrity()[0]


def test_repair_data_integrity_without_problems(mgr, monkeypatch):
    mgr.data.hype_trains.level_completions = {1: 2, "3": "4"}
    assert mgr.repair_data_integrity() is False  # same number of entries: nothing replaced


@pytest.mark.xfail(strict=True, reason="BUG: repair_data_integrity calls self.save_statistics(), which does not exist (AttributeError)")
@pytest.mark.parametrize("bad", [[1, 2], {1: "x"}])
def test_repair_data_integrity_saves_the_repaired_data(mgr, bad):
    mgr.data.hype_trains.level_completions = bad
    assert mgr.repair_data_integrity() is True
    assert isinstance(mgr.data.hype_trains.level_completions, dict)


def test_repair_data_integrity_fixes_before_failing_to_save(mgr):
    mgr.data.hype_trains.level_completions = {1: "x", 2: 2}
    with pytest.raises(AttributeError):
        mgr.repair_data_integrity()
    assert mgr.data.hype_trains.level_completions == {2: 2}


# --------------------------------------------------------------------------- periodic saving


def test_periodic_saving_thread_lifecycle(mgr, monkeypatch):
    saves = []
    monkeypatch.setattr(mgr, "_save_to_database", lambda: saves.append(1))
    mgr.set_save_interval(0.01)
    assert mgr.save_interval == 0.01
    mgr.start_periodic_saving()
    thread = mgr._periodic_thread
    assert mgr.is_running and thread.name == "StatisticsPeriodicSave" and thread.daemon
    status = mgr.get_saving_status()
    assert status["is_running"] is True and status["save_interval"] == 0.01 and status["needs_start"] is False
    mgr.start_periodic_saving_safe()  # already running: no second thread
    assert mgr._periodic_thread is thread
    for _ in range(200):
        if saves:
            break
        time.sleep(0.005)
    mgr.stop_periodic_saving()
    thread.join(2)
    assert saves and not thread.is_alive()
    assert mgr._periodic_thread is None and mgr.is_running is False
    assert mgr.get_saving_status()["has_thread"] is False
    mgr.stop_periodic_saving()  # already stopped


def test_periodic_saving_start_failure_is_retried_later(mgr, monkeypatch):
    with monkeypatch.context() as mp:
        mp.setattr(threading, "Thread", Mock(side_effect=RuntimeError("no threads")))
        mgr.start_periodic_saving_safe()
    assert mgr.is_running is False and mgr._needs_periodic_start is True
    started = Mock()
    monkeypatch.setattr(mgr, "start_periodic_saving_safe", started)
    mgr.ensure_periodic_saving()
    started.assert_called_once()


def test_ensure_periodic_saving(mgr, monkeypatch):
    started = Mock()
    monkeypatch.setattr(mgr, "start_periodic_saving_safe", started)
    mgr.ensure_periodic_saving()
    started.assert_not_called()
    mgr.is_running = True
    mgr._periodic_thread = None
    mgr.ensure_periodic_saving()
    started.assert_called_once()
    mgr.is_running = False


def test_periodic_save_loop_saves_and_survives_errors(mgr, monkeypatch):
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            mgr.is_running = False

    outcomes = iter([RuntimeError("disk"), None, None])

    def save():
        err = next(outcomes)
        if err:
            raise err

    monkeypatch.setattr(time, "sleep", fake_sleep)
    monkeypatch.setattr(mgr, "_save_to_database", Mock(side_effect=save))
    mgr.is_running = True
    mgr._periodic_save_thread()
    assert sleeps == [300, 300, 300]
    assert mgr._save_to_database.call_count == 2  # the third sleep ended the loop first


def test_save_on_close_rewrites_users_and_closes(mgr, monkeypatch):
    mgr._spilled_user_digests[("x", "y")] = b"1"
    save = Mock()
    monkeypatch.setattr(mgr, "_save_to_database", save)
    mgr.save_on_close()
    save.assert_called_once()
    assert mgr._spilled_user_digests == {} and mgr._event_batch_stop.is_set()


def test_save_on_close_retries_once(mgr, monkeypatch):
    monkeypatch.setattr(sm.time, "sleep", Mock())
    save = Mock(side_effect=[RuntimeError("first"), None])
    monkeypatch.setattr(mgr, "_save_to_database", save)
    mgr.save_on_close()
    assert save.call_count == 2
    sm.time.sleep.assert_called_once_with(0.1)


def test_save_on_close_gives_up_after_two_failures(mgr, monkeypatch, caplog):
    monkeypatch.setattr(sm.time, "sleep", Mock())
    monkeypatch.setattr(mgr, "_save_to_database", Mock(side_effect=RuntimeError("never")))
    mgr.save_on_close()
    assert "Emergency statistics save also failed" in caplog.text
    assert mgr._event_batch_stop.is_set()


def test_force_save_returns_all_statistics(mgr, monkeypatch):
    save = Mock()
    monkeypatch.setattr(mgr, "_save_to_database", save)
    monkeypatch.setattr(mgr, "get_all_statistics", lambda: {"ok": True})
    assert mgr.force_save() == {"ok": True}
    save.assert_called_once()


def test_saving_status_without_last_save_time(mgr):
    mgr.data.session = SimpleNamespace(start_time=0.0)
    assert mgr.get_saving_status()["last_save_time"] is None


# --------------------------------------------------------------------------- module-level helpers


@pytest.fixture
def no_global(monkeypatch):
    monkeypatch.setattr(sm, "_statistics_manager", None)


def test_get_statistics_manager_is_a_singleton(env, no_global):
    first = sm.get_statistics_manager()
    env.managers.append(first)
    assert sm.get_statistics_manager() is first
    assert sm.initialize_statistics() is first
    assert sm.initialize_statistics_with_data({"Statistics": {"data": {"chat": {}}}}) is first


def test_start_statistics_saving(monkeypatch, no_global):
    fake = Mock()
    monkeypatch.setattr(sm, "_statistics_manager", fake)
    assert sm.start_statistics_saving() is fake
    fake.start_periodic_saving_safe.assert_called_once()
    fake.start_periodic_saving_safe.side_effect = RuntimeError("x")
    assert sm.start_statistics_saving() is None


def test_shutdown_statistics(monkeypatch, no_global, caplog):
    sm.shutdown_statistics()
    assert "No statistics manager to shutdown" in caplog.text
    fake = Mock()
    monkeypatch.setattr(sm, "_statistics_manager", fake)
    sm.shutdown_statistics()
    fake.stop_periodic_saving.assert_called_once()
    fake.save_on_close.assert_called_once()
    assert sm._statistics_manager is None
    broken = Mock()
    broken.save_on_close.side_effect = RuntimeError("x")
    monkeypatch.setattr(sm, "_statistics_manager", broken)
    sm.shutdown_statistics()
    assert sm._statistics_manager is broken
    assert "Error during statistics manager shutdown" in caplog.text
