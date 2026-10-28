# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/chatbot_manager.py: greeting flags, loading, migrations, saving and item CRUD.

Message processing, timers, test runs, quotes and greetings are in ``test_chatbot_manager_part2.py``.
No real threads run: the module's ``threading`` is swapped for one whose ``Thread`` only records
its target, so saves happen through ``flush_pending_saves`` and timer loops are driven by hand.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _chatbot_manager_fakes import PATHS, REAL_FLAGS, _cmd, _evt, env  # noqa: E402,F401  (env: fixture)
from modules import chatbot_manager as cm  # noqa: E402
from modules import database_manager as dbm  # noqa: E402
from modules.chatbot_core import ChatCommand, ChatEvent, EventType, Greeting, Quote  # noqa: E402

# --------------------------------------------------------------------------- module helpers


def test_json_fingerprint():
    assert cm._json_fingerprint({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'
    assert cm._json_fingerprint({"x": object()}).startswith('{"x": "<object')
    circular = []
    circular.append(circular)
    assert cm._json_fingerprint(circular) is None


def test_database_backend_token(monkeypatch):
    fake = SimpleNamespace(_database="db1", get_config=lambda: SimpleNamespace(database_type="sql", streamer_name="me"))
    monkeypatch.setattr(dbm, "database_manager", fake)
    assert cm._database_backend_token() == ("db1", "sql", "me")

    def boom():
        raise RuntimeError("no config")

    monkeypatch.setattr(dbm, "database_manager", SimpleNamespace(get_config=boom))
    token = cm._database_backend_token()
    assert len(token) == 1 and token != cm._database_backend_token()  # always "changed"


def test_command_matches_name():
    c = _cmd(word="hello", aliases=["hi", "HEY"])
    assert cm._command_matches_name(c, "HELLO")
    assert cm._command_matches_name(c, "hey")
    assert not cm._command_matches_name(c, "bye")
    c.aliases = ["!yo"]
    assert cm._command_matches_name(c, "yo")
    nameless = SimpleNamespace(command_name=None, aliases=None)
    assert not cm._command_matches_name(nameless, "x")
    assert cm._command_matches_name(nameless, "") is True  # empty name matches empty needle


def test_find_reset_target():
    a = _cmd("a", "deaths")
    b = _cmd("b", "reset", aliases=["deaths2"])
    cmds = {"a": a, "b": b}
    assert cm._find_reset_target(cmds, "!Deaths", "b") is a
    assert cm._find_reset_target(cmds, "deaths", "a") is None  # skips itself
    assert cm._find_reset_target(cmds, "", "x") is None
    assert cm._find_reset_target(cmds, None, "x") is None
    assert cm._find_reset_target(cmds, "deaths2", "a") is b


def test_silent_result():
    assert cm._silent_result("x") == ("", "x", [], [], "chat")


def test_test_result_shape_and_await():
    ok = cm.TestResult(ok=True, triggered=True, response="hi", reason="", lookup="example")
    assert ok["success"] is True and ok["message"] == "hi" and "error" not in ok
    assert ok["lookup"] == "example"
    bad = cm.TestResult(ok=False, triggered=False, response="", reason="nope")
    assert bad["message"] == "nope" and bad["error"] == "nope"

    async def run():
        return await bad

    import asyncio

    assert asyncio.run(run()) is bad


# --------------------------------------------------------------------------- GreetingFlagManager


def test_flags_new_file_starts_dirty_with_reset_time(env, monkeypatch):
    monkeypatch.setattr(cm.time, "time", lambda: 1234.0)
    f = REAL_FLAGS(str(env.flag_path))
    assert f.greeted_flags == {} and f.last_reset_time == 1234.0 and f._dirty
    assert env.threads[-1].target == f._background_save_loop and env.threads[-1].started
    assert env.threads[-1].daemon is True


def test_flags_load_new_format_keeps_only_true(env):
    env.flag_path.write_text(json.dumps({"greeted_flags": {"a": True, "b": False}, "last_reset_time": 50}))
    f = REAL_FLAGS(str(env.flag_path))
    assert f.greeted_flags == {"a": True} and f.last_reset_time == 50 and not f._dirty


def test_flags_load_new_format_without_reset_time(env):
    env.flag_path.write_text(json.dumps({"greeted_flags": {"a": 1}}))
    f = REAL_FLAGS(str(env.flag_path))
    assert f.last_reset_time == 0


def test_flags_migrate_old_timestamp_format(env, monkeypatch):
    monkeypatch.setattr(cm.time, "time", lambda: 99.0)
    env.flag_path.write_text(json.dumps({"u1": 123, "u2": 456}))
    f = REAL_FLAGS(str(env.flag_path))
    assert f.greeted_flags == {"u1": True, "u2": True}
    assert f.last_reset_time == 99.0 and f._dirty


def test_flags_load_non_dict_is_ignored(env):
    env.flag_path.write_text(json.dumps([1, 2]))
    f = REAL_FLAGS(str(env.flag_path))
    assert f.greeted_flags == {} and not f._dirty


def test_flags_load_corrupt_file(env, monkeypatch, caplog):
    monkeypatch.setattr(cm.time, "time", lambda: 7.0)
    env.flag_path.write_text("{not json")
    f = REAL_FLAGS(str(env.flag_path))
    assert f.greeted_flags == {} and f.last_reset_time == 7.0 and f._dirty
    assert "Failed to load greeting flags" in caplog.text


def test_flags_save_writes_atomically(env, monkeypatch):
    monkeypatch.setattr(cm.time, "time", lambda: 10.0)
    f = REAL_FLAGS(str(env.flag_path))
    f.mark_user_greeted("u1")
    f._save_flags()
    data = json.loads(env.flag_path.read_text())
    assert data == {"greeted_flags": {"u1": True}, "last_reset_time": 10.0}
    assert not f._dirty and f._last_save == 10.0
    assert not env.flag_path.with_name("greeting_flags.json.tmp").exists()


def test_flags_save_failure_notifies_and_stays_dirty(env, tmp_path):
    f = REAL_FLAGS(str(tmp_path / "missing_dir" / "flags.json"))
    f._dirty = False
    f._save_flags()
    assert f._dirty
    assert env.notify.titles == ["Couldn't save who has been greeted"]
    assert env.notify.calls[0][1]["dedupe_key"] == "chatbot:greeting_flags_save"


def test_flags_background_loop_saves_when_due_and_survives_errors(env, monkeypatch):
    f = REAL_FLAGS(str(env.flag_path))
    clock = {"t": 1000.0}
    monkeypatch.setattr(cm.time, "time", lambda: clock["t"])
    saves, sleeps = [], []

    class Stop(BaseException):
        pass

    def save():
        saves.append(clock["t"])
        if len(saves) == 2:
            raise RuntimeError("disk")
        f._dirty = len(saves) == 1

    def sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += 100
        if len(sleeps) >= 4:
            raise Stop()

    monkeypatch.setattr(f, "_save_flags", save)
    monkeypatch.setattr(cm.time, "sleep", sleep)
    f._dirty, f._last_save = True, 0
    with pytest.raises(Stop):
        f._background_save_loop()
    # 1: save (still dirty) -> sleep 10; 2: save raises -> sleep 30; 3: save -> clean -> sleep 10; 4: idle
    assert sleeps == [10, 30, 10, 10]
    assert len(saves) == 3 and not f._dirty


def test_flags_background_loop_skips_when_recently_saved(env, monkeypatch):
    f = REAL_FLAGS(str(env.flag_path))
    monkeypatch.setattr(cm.time, "time", lambda: 100.0)

    class Stop(BaseException):
        pass

    called = []
    monkeypatch.setattr(f, "_save_flags", lambda: called.append(1))
    monkeypatch.setattr(cm.time, "sleep", lambda s: (_ for _ in ()).throw(Stop()))
    f._dirty, f._last_save = True, 90.0
    with pytest.raises(Stop):
        f._background_save_loop()
    assert called == []


def test_flags_greet_mark_reset_and_stats(env, monkeypatch):
    f = REAL_FLAGS(str(env.flag_path))
    f._dirty = False
    assert f.should_greet_user("a")
    f.mark_user_greeted("a")
    assert not f.should_greet_user("a") and f._dirty
    monkeypatch.setattr(cm.time, "time", lambda: 5000.0)
    f.reset_all_flags()
    assert f.greeted_flags == {} and f.last_reset_time == 5000.0
    f.mark_user_greeted("b")
    stats = f.get_stats()
    assert stats["total_users"] == 1 and stats["greeted_users"] == 1 and stats["ungreeted_users"] == 0
    assert stats["last_reset_time"] == 5000.0 and stats["file_path"] == str(env.flag_path)
    assert stats["file_exists"] is False and stats["dirty"] is True


def test_flags_should_reset_and_perform(env, monkeypatch):
    f = REAL_FLAGS(str(env.flag_path))
    f.last_reset_time = 0
    assert not f.should_reset_flags(0, 10**9)
    assert not f.should_reset_flags(24, 23 * 3600)
    assert f.should_reset_flags(24, 24 * 3600)
    monkeypatch.setattr(cm.time, "time", lambda: 24 * 3600.0)
    assert f.should_reset_flags(24)
    f.mark_user_greeted("x")
    assert f.perform_reset_if_needed(24, 48 * 3600)
    assert f.greeted_flags == {}
    assert not f.perform_reset_if_needed(24, f.last_reset_time + 1)


def test_flags_force_save_only_when_dirty(env, monkeypatch):
    f = REAL_FLAGS(str(env.flag_path))
    calls = []
    monkeypatch.setattr(f, "_save_flags", lambda: calls.append(1))
    f._dirty = False
    f.force_save()
    assert calls == []
    f._dirty = True
    f.force_save()
    assert calls == [1]


def test_flags_default_path_follows_data_dir(env, monkeypatch):
    import os

    from modules.path_utils import get_data_path

    monkeypatch.setattr(REAL_FLAGS, "_load_flags", lambda self: None)  # never read the real file
    monkeypatch.setattr(cm.Path, "mkdir", lambda self, **kw: None)  # never create folders
    f = REAL_FLAGS()
    assert str(f.file_path) == get_data_path(os.path.join("data", "greeting_flags.json"))


# --------------------------------------------------------------------------- loading


def test_load_empty_database(env):
    m = env.make()
    assert m.commands == {} and m.events == {} and m.quotes == {} and m.greetings == {}
    assert m.quotes_enabled and m.quote_reply_format == cm.DEFAULT_QUOTE_REPLY_FORMAT
    assert m._unreadable_paths == {}
    assert env.critical.calls == []
    assert m.get_manager() is m


def test_load_items_and_settings(env):
    env.db.store["BotData/Commands"] = [_cmd("c1").to_dict()]
    env.db.store["BotData/Events"] = [_evt("e1").to_dict()]
    env.db.store["BotData/Quotes"] = [Quote(quote_id="q1", quote_number=1, text="t").to_dict()]
    env.db.store["BotData/Greetings"] = [Greeting(greeting_id="g1", user_id="u1", username="A").to_dict()]
    env.db.store["BotData/Settings"] = {"quotes_enabled": 0, "greetings_enabled": "", "default_greeting_text": "",
                                        "greeting_cooldown_hours": "5", "greeting_reset_interval_hours": 2.0,
                                        "quote_cooldown_seconds": "7", "add_quote_permission": "subs",
                                        "quote_reply_format": "  "}
    m = env.make()
    assert set(m.commands) == {"c1"} and set(m.events) == {"e1"} and set(m.quotes) == {"q1"}
    assert m.greetings_by_user_id["u1"].greeting_id == "g1"
    assert m.quotes_enabled is False and m.greetings_enabled is False
    assert m.default_greeting_enabled is False and m.custom_greeting_enabled is False
    assert m.default_greeting_text == "@{username} welcome to the stream!"
    assert m.greeting_cooldown_hours == 5 and m.greeting_reset_interval_hours == 2
    assert m.quote_cooldown_seconds == 7 and m.add_quote_permission == "subscriber"
    assert m.quote_reply_format == cm.DEFAULT_QUOTE_REPLY_FORMAT
    # Data already in the current format: nothing saved on load.
    assert not m._save_dirty


def test_apply_settings_keeps_valid_values(env):
    m = env.make()
    m._apply_settings({"quotes_enabled": True, "default_greeting_enabled": True, "custom_greeting_enabled": False,
                       "default_greeting_text": "Hey", "quote_reply_format": "{text}"})
    assert m.default_greeting_enabled is True and m.custom_greeting_enabled is False
    assert m.default_greeting_text == "Hey" and m.quote_reply_format == "{text}"


def test_load_skips_bad_items_and_warns(env):
    bad = {"command_id": "bad", "cooldown": 1}
    env.db.store["BotData/Commands"] = [_cmd("c1").to_dict(), "not a dict", bad]
    env.db.store["BotData/Events"] = ["junk", {"event_id": "e", "trigger_count": 1}]
    env.db.store["BotData/Quotes"] = ["junk"]
    env.db.store["BotData/Greetings"] = ["junk"]
    m = env.make()
    assert "c1" in m.commands
    titles = env.notify.titles
    assert "Some chatbot items couldn't be loaded" in titles
    call = env.notify.calls[titles.index("Some chatbot items couldn't be loaded")]
    details = call[1]["details"]
    assert "command ?" in details and "event ?" in details and "quote ?" in details and "greeting ?" in details
    assert call[0][0].startswith("4 chatbot items couldn't be read and were skipped")


def test_load_skipped_dict_items_show_their_ids(env, monkeypatch):
    env.db.store["BotData/Commands"] = [{"command_id": "x1"}]
    env.db.store["BotData/Events"] = [{"event_id": "x2"}]
    env.db.store["BotData/Quotes"] = [{"quote_id": "x3"}]
    env.db.store["BotData/Greetings"] = [{"greeting_id": "x4"}]

    def boom(cls, data):
        raise ValueError("bad")

    monkeypatch.setattr(ChatCommand, "from_dict", classmethod(boom))
    monkeypatch.setattr(ChatEvent, "from_dict", classmethod(boom))
    monkeypatch.setattr(Quote, "from_dict", classmethod(boom))
    monkeypatch.setattr(Greeting, "from_dict", classmethod(boom))
    env.make()
    details = env.notify.calls[0][1]["details"]
    for item in ("command x1: bad", "event x2: bad", "quote x3: bad", "greeting x4: bad"):
        assert item in details


def test_load_single_skipped_item_message(env, monkeypatch):
    env.db.store["BotData/Quotes"] = [{"quote_id": "q1"}]

    def boom(cls, data):
        raise ValueError("bad")

    monkeypatch.setattr(Quote, "from_dict", classmethod(boom))
    env.make()
    assert env.notify.calls[0][0][0].startswith("1 chatbot item couldn't be read and was skipped")


def test_load_failure_reports_critical(env, monkeypatch):
    def boom(self):
        raise RuntimeError("db down")

    monkeypatch.setattr(cm.ChatbotManager, "_migrate_legacy_data", boom)
    m = env.make()
    assert m.commands == {}
    assert env.critical.titles == ["Couldn't load the chatbot from the database"]


def test_load_schedules_timer_events_and_logs_errors(env, monkeypatch, caplog):
    env.db.store["BotData/Events"] = [_evt("e1", "interval", interval=60).to_dict(),
                                      _evt("e2", "follow").to_dict()]
    calls = []

    def schedule(self, event):
        calls.append(event.event_id)
        if event.event_id == "e2":
            raise RuntimeError("cannot")

    monkeypatch.setattr(cm.ChatbotManager, "_schedule_event", schedule)
    env.make()
    assert sorted(calls) == ["e1", "e2"]
    assert "Error starting timer for event e2" in caplog.text


def test_load_migrates_and_saves(env):
    raw = _cmd("c1").to_dict()
    raw["command_name"] = "!Hello"  # normalized on load -> differs from storage
    env.db.store["BotData/Commands"] = [raw]
    m = env.make()
    assert m._save_dirty  # a save was queued
    m.flush_pending_saves()
    assert env.db.store["BotData/Commands"][0]["command_name"] == "hello"


def test_load_marks_unreadable_paths_and_settings_baseline(env):
    env.db.unreadable = {"BotData/Commands", "BotData/Settings"}
    m = env.make()
    assert "BotData/Commands" in m._unreadable_paths
    assert m._unreadable_paths["BotData/Settings"] == m._settings_payload()


# --------------------------------------------------------------------------- legacy migration


def _legacy_dir(env):
    d = env.tmp / "data"
    d.mkdir(exist_ok=True)
    return d


def test_migrate_legacy_skips_when_database_has_commands(env):
    env.db.store["BotData/Commands"] = [_cmd("c1").to_dict()]
    m = env.make()
    assert m._migrate_legacy_data() == env.db.store["BotData/Commands"]


def test_migrate_legacy_json_files(env):
    d = _legacy_dir(env)
    (d / "chatbot_commands.json").write_text(json.dumps([_cmd("c9").to_dict()]))
    (d / "chatbot_events.json").write_text(json.dumps([_evt("e9").to_dict()]))
    (d / "chatbot_quotes.json").write_text(json.dumps([Quote(quote_id="q9", quote_number=1, text="x").to_dict()]))
    (d / "chatbot_settings.json").write_text(json.dumps({"quote_cooldown_seconds": 3}))
    env.db.missing = {"BotData/Commands"}  # nothing stored yet, so migration runs

    m = env.make()
    for path in ("BotData/Commands", "BotData/Events", "BotData/Quotes", "BotData/Settings"):
        assert path in env.db.writes
    assert m.quote_cooldown_seconds == 3
    assert set(m.events) == {"e9"} and set(m.quotes) == {"q9"}


def test_migrate_legacy_without_files_does_nothing(env):
    env.db.missing = {"BotData/Commands"}
    m = env.make()
    assert m._migrate_legacy_data() is None
    assert env.db.writes == []


def test_migrate_legacy_error_reports_critical(env):
    d = _legacy_dir(env)
    (d / "chatbot_commands.json").write_text("{broken")
    env.db.missing = {"BotData/Commands"}
    env.make()
    assert "Couldn't import the old chatbot files" in env.critical.titles


# --------------------------------------------------------------------------- load migrations


def test_apply_load_migrations_resets_non_persistent_counters(env):
    m = env.make()
    c = _cmd("c1", command_type="counter", persistent_counter=False, counter_value=5, schema_version=2)
    m.commands = {"c1": c}
    assert m._apply_load_migrations(None, None) is True
    assert c.counter_value == 0
    c2 = _cmd("c2", command_type="counter", persistent_counter=False, counter_value=0)
    m.commands = {"c2": c2}
    assert m._apply_load_migrations(None, None) is False


def test_apply_load_migrations_converts_repeating_commands(env):
    m = env.make()
    c = _cmd("c1", repeating_enabled=True, repeat_interval="90")
    m.commands = {"c1": c}
    assert m._apply_load_migrations(None, None) is True
    assert not c.repeating_enabled
    (event,) = m.events.values()
    assert event.event_type is EventType.INTERVAL and event.interval == 90


def test_apply_load_migrations_keeps_repeat_when_a_path_was_unreadable(env):
    m = env.make()
    c = _cmd("c1", repeating_enabled=True)
    m.commands = {"c1": c}
    m._unreadable_paths["BotData/Events"] = None
    assert m._apply_load_migrations([c.to_dict()], None) is False
    assert c.repeating_enabled and m.events == {}


def test_apply_load_migrations_detects_event_normalization(env):
    m = env.make()
    e = _evt("e1")
    m.events = {"e1": e}
    stored = e.to_dict()
    assert m._apply_load_migrations([], [stored]) is False
    stored["specific_time"] = "9:00"
    assert m._apply_load_migrations([], [stored]) is True


@pytest.mark.parametrize("interval,expected", [("abc", 60), (None, 60), (0, 60), (30.7, 30)])
def test_convert_repeating_command(env, interval, expected):
    m = env.make()
    c = _cmd("c1", word="hydrate", name="", repeating_enabled=True, repeat_interval=interval,
             reply_targets=["youtube"], discord_channels=["d1"], output_mode="banner",
             lookup={"id": "weather"}, lookup_fallback="fb", enabled=False)
    event = m._convert_repeating_command(c)
    assert event.name == "!hydrate (timer)"
    assert event.description == "Moved from the repeating setting of !hydrate"
    assert event.interval == expected
    assert event.reply_targets == ["youtube"] and event.discord_channels == ["d1"] and event.output_mode == "banner"
    assert event.lookup == {"id": "weather"} and event.lookup_fallback == "fb" and event.enabled is False
    assert m.events[event.event_id] is event and not c.repeating_enabled


def test_convert_repeating_command_uses_name(env):
    m = env.make()
    event = m._convert_repeating_command(_cmd("c1", name="Water", repeating_enabled=True))
    assert event.name == "Water (timer)" and event.lookup is None


# --------------------------------------------------------------------------- unreadable recovery / merge


def test_recover_unreadable_paths_blocks_and_merges(env):
    m = env.make()
    m._unreadable_paths = {"BotData/Quotes": None, "BotData/Events": None}
    env.db.unreadable = {"BotData/Events"}
    env.db.store["BotData/Quotes"] = [Quote(quote_id="q1", quote_number=1, text="a").to_dict()]
    blocked = m._recover_unreadable_paths()
    assert blocked == {"BotData/Events"}
    assert "q1" in m.quotes and "BotData/Quotes" not in m._unreadable_paths


def test_recover_skips_paths_cleared_meanwhile(env, monkeypatch):
    m = env.make()
    m._unreadable_paths = {"BotData/Quotes": None}
    real = dbm.get_data_checked

    def read_and_clear(path):
        m._unreadable_paths.clear()
        return real(path)

    monkeypatch.setattr(dbm, "get_data_checked", read_and_clear)
    assert m._recover_unreadable_paths() == set()


def test_merge_settings_keeps_local_changes(env):
    m = env.make()
    baseline = m._settings_payload()
    m.quote_cooldown_seconds = 99  # edited while the stored copy was unreadable
    m._merge_stored_path("BotData/Settings", {"quote_cooldown_seconds": 5, "quote_reply_format": "{text}"}, baseline)
    assert m.quote_cooldown_seconds == 99 and m.quote_reply_format == "{text}"


def test_merge_settings_without_baseline_or_data(env):
    m = env.make()
    m.quote_cooldown_seconds = 11
    m._merge_stored_path("BotData/Settings", None, None)
    assert m.quote_cooldown_seconds == 11


def test_merge_quotes_renumbers_clashes(env):
    m = env.make()
    local = Quote(quote_id="local", quote_number=1, text="mine")
    m.quotes = {"local": local}
    stored = [Quote(quote_id="s1", quote_number=1, text="a").to_dict(),
              Quote(quote_id="s2", quote_number=2, text="b").to_dict(), "junk"]
    m._merge_stored_path("BotData/Quotes", stored, None)
    assert set(m.quotes) == {"local", "s1", "s2"}
    assert local.quote_number == 3


def test_merge_keeps_memory_version_for_same_id(env):
    m = env.make()
    mine = _cmd("c1", word="mine")
    m.commands = {"c1": mine}
    m._merge_stored_path("BotData/Commands", [_cmd("c1", word="stored").to_dict(), _cmd("c2", word="other").to_dict()],
                         None)
    assert m.commands["c1"] is mine and m.commands["c2"].command_name == "other"


def test_merge_greetings_and_events(env, monkeypatch):
    m = env.make()
    scheduled = []
    monkeypatch.setattr(m, "_schedule_event", lambda e: scheduled.append(e.event_id))
    m._merge_stored_path("BotData/Greetings", [Greeting(greeting_id="g1", user_id="u1").to_dict()], None)
    assert m.greetings_by_user_id["u1"].greeting_id == "g1"
    m._merge_stored_path("BotData/Events", [_evt("on").to_dict(), _evt("off", enabled=False).to_dict()], None)
    assert scheduled == ["on"]
    m._merge_stored_path("BotData/Events", "not a list", None)
    assert m.events == {"on": m.events["on"], "off": m.events["off"]}


# --------------------------------------------------------------------------- saving


def test_save_data_starts_one_worker(env):
    m = env.make()
    before = len(env.threads)
    m._save_data()
    worker = env.threads[-1]
    assert len(env.threads) == before + 1 and worker.target == m._save_worker and worker.name == "ChatbotSave"
    worker.alive = True
    m._save_data()  # a worker is running: it will pick up the new snapshot
    assert len(env.threads) == before + 1
    assert m._save_dirty


def test_save_worker_loops_until_clean(env, monkeypatch, caplog):
    m = env.make()
    runs = []

    def save_now():
        runs.append(1)
        if len(runs) == 1:
            m._save_dirty = True  # another change arrived meanwhile
            raise RuntimeError("write failed")

    monkeypatch.setattr(m, "_save_data_now", save_now)
    m._save_dirty = True
    m._save_thread = object()
    m._save_worker()
    assert runs == [1, 1] and m._save_thread is None
    assert "Chatbot save failed" in caplog.text


def test_flush_pending_saves(env, monkeypatch):
    m = env.make()
    t = SimpleNamespace(alive=True, joined=None)
    t.is_alive = lambda: t.alive
    t.join = lambda timeout: setattr(t, "joined", timeout)
    m._save_thread = t
    saved, flags = [], []
    monkeypatch.setattr(m, "_save_data_now", lambda: saved.append(1))
    monkeypatch.setattr(m.flag_manager, "force_save", lambda: flags.append(1))
    m._save_dirty = False
    m.flush_pending_saves(timeout=2.5)
    assert t.joined == 2.5 and saved == [] and flags == [1]
    m._blocked_save_pending = True
    m.flush_pending_saves()
    assert saved == [1]


def test_save_data_now_writes_changed_paths_only(env):
    m = env.make()
    m.commands["c1"] = _cmd("c1")
    m._save_data_now()
    assert set(env.db.writes) == set(PATHS)
    env.db.writes.clear()
    m._save_data_now()
    assert env.db.writes == []  # nothing changed
    m.quotes_enabled = False
    m._save_data_now()
    assert env.db.writes == ["BotData/Settings"]


def test_save_data_now_rewrites_after_backend_switch(env, monkeypatch):
    m = env.make()
    m._save_data_now()
    env.db.writes.clear()
    monkeypatch.setattr(cm, "_database_backend_token", lambda: ("other",))
    m._save_data_now()
    assert set(env.db.writes) == set(PATHS)


def test_save_data_now_unfingerprintable_always_writes(env, monkeypatch):
    m = env.make()
    monkeypatch.setattr(cm, "_json_fingerprint", lambda data: None)
    m._save_data_now()
    m._save_data_now()
    assert env.db.writes.count("BotData/Settings") == 2


def test_save_data_now_failed_write_notifies(env):
    m = env.make()
    env.db.fail_writes = {"BotData/Quotes", "BotData/Events"}
    env.db.problem = ("error", "disk full")
    m._save_data_now()
    assert env.critical.titles == ["Couldn't save chatbot changes"]
    details = env.critical.calls[0][1]["details"]
    assert "BotData/Quotes (error: disk full)" in details
    # The failed path is retried next time.
    env.db.fail_writes = set()
    env.db.writes.clear()
    m._save_data_now()
    assert set(env.db.writes) == {"BotData/Quotes", "BotData/Events"}


def test_save_data_now_failed_write_without_reason(env):
    m = env.make()
    env.db.fail_writes = {"BotData/Settings"}
    m._save_data_now()
    assert env.critical.calls[0][1]["details"].endswith("for: BotData/Settings")


def test_save_data_now_blocked_paths_warn_and_skip(env):
    m = env.make()
    m._unreadable_paths = {"BotData/Commands": None}
    env.db.unreadable = {"BotData/Commands"}
    m._save_data_now()
    assert "BotData/Commands" not in env.db.writes
    assert m._blocked_save_pending is True
    assert env.notify.titles == ["Chatbot changes not saved yet"]


def test_save_data_now_exception_notifies(env):
    m = env.make()
    env.db.raise_on_write = RuntimeError("boom")
    m._save_data_now()
    assert env.critical.titles == ["Couldn't save chatbot changes"]
    assert env.critical.calls[0][1]["details"] == "RuntimeError: boom"


def test_settings_payload_coerces_types(env):
    m = env.make()
    m.quotes_enabled = 1
    m.greetings_enabled = 0
    m.default_greeting_enabled = "yes"
    m.custom_greeting_enabled = None
    m.default_greeting_text = ""
    m.greeting_cooldown_hours = 3.0
    m.greeting_reset_interval_hours = "4"
    m.quote_cooldown_seconds = 5.5
    p = m._settings_payload()
    assert p["quotes_enabled"] is True and p["greetings_enabled"] is False
    assert p["default_greeting_enabled"] is True and p["custom_greeting_enabled"] is False
    assert p["default_greeting_text"] == "@{username} welcome to the stream!"
    assert p["greeting_cooldown_hours"] == 3 and p["greeting_reset_interval_hours"] == 4
    assert p["quote_cooldown_seconds"] == 5


def test_build_save_payloads(env):
    m = env.make()
    m.commands["c"] = _cmd("c")
    m.events["e"] = _evt("e")
    m.quotes["q"] = Quote(quote_id="q", text="t")
    m.greetings["g"] = Greeting(greeting_id="g", user_id="u")
    payloads = dict(m._build_save_payloads())
    assert [p for p in payloads] == PATHS
    assert payloads["BotData/Commands"][0]["command_id"] == "c"
    assert payloads["BotData/Greetings"][0]["greeting_id"] == "g"


def test_corrupt_setting_does_not_lose_greetings(env):
    env.db.store["BotData/Greetings"] = [Greeting(greeting_id="g1", user_id="u1", username="A").to_dict()]
    env.db.store["BotData/Settings"] = {"greeting_cooldown_hours": "abc"}
    m = env.make()
    m.add_quote("trigger a save")
    m.flush_pending_saves()
    assert [g["greeting_id"] for g in env.db.store["BotData/Greetings"]] == ["g1"]


def test_load_stopped_early_never_saves_over_unloaded_paths(env, monkeypatch):
    env.db.store["BotData/Quotes"] = [Quote(quote_id="q1", quote_number=1, text="t").to_dict()]
    env.db.store["BotData/Greetings"] = [Greeting(greeting_id="g1", user_id="u1", username="A").to_dict()]

    def boom(self, *a):
        raise RuntimeError("stop")

    monkeypatch.setattr(cm.ChatbotManager, "_apply_load_migrations", boom)
    m = env.make()
    assert m.quotes == {} and m.greetings == {}
    assert {"BotData/Quotes", "BotData/Settings", "BotData/Greetings"} <= set(m._unreadable_paths)
    assert "BotData/Commands" not in m._unreadable_paths
    m.add_quote("new one")
    m.flush_pending_saves()
    # The stored copy was re-read and kept; the new quote went on top.
    assert [g["greeting_id"] for g in env.db.store["BotData/Greetings"]] == ["g1"]
    assert sorted(q["text"] for q in env.db.store["BotData/Quotes"]) == ["new one", "t"]


# --------------------------------------------------------------------------- commands CRUD


def test_validate_command(env):
    m = env.make()
    assert m.validate_command(ChatCommand(command_name="")) == "Give the command a name (the word after !)."
    assert m.validate_command(_cmd("x", "quote")) == "!quote and !add_quote are built in."
    assert m.validate_command(_cmd("x", "hi", aliases=["add_quote"])) == "!quote and !add_quote are built in."
    m.quotes_enabled = False
    assert m.validate_command(_cmd("x", "quote")) is None
    m.commands["a"] = _cmd("a", "hello", aliases=["hey"])
    assert m.validate_command(_cmd("b", "hey")) == "!hey is already used by another command."
    assert m.validate_command(_cmd("a", "hello")) is None  # editing itself
    assert m.validate_command(_cmd("b", "other")) is None


def test_add_command_assigns_id_and_converts_repeat(env, monkeypatch):
    m = env.make()
    scheduled = []
    monkeypatch.setattr(m, "_schedule_event", lambda e: scheduled.append(e))
    c = ChatCommand(command_name="x", repeating_enabled=True, repeat_interval=30)
    assert m.add_command(c)
    assert c.command_id and m.commands[c.command_id] is c
    assert len(scheduled) == 1 and scheduled[0].interval == 30
    assert m._save_dirty
    keep = _cmd("keep")
    m.add_command(keep)
    assert "keep" in m.commands


def test_update_command_keeps_runtime_and_conditions(env, monkeypatch):
    m = env.make()
    old = _cmd("c1", counter_value=5, usage_count=2, last_used=10, trigger_count=3, last_triggered=11,
               conditions=[{"field": "x", "operator": "equal", "value": 1}])
    m.commands["c1"] = old
    new = _cmd("zzz", word="new")
    assert m.update_command("c1", new)
    assert new.command_id == "c1" and m.commands["c1"] is new
    assert (new.counter_value, new.usage_count, new.last_used, new.trigger_count, new.last_triggered) == (5, 2, 10, 3, 11)
    assert len(new.conditions) == 1
    fresh = _cmd("c1", word="fresh")
    assert m.update_command("c1", fresh, keep_runtime=False, keep_conditions=False)
    assert fresh.counter_value == 0 and fresh.conditions == []


def test_update_command_missing_and_repeat(env, monkeypatch):
    m = env.make()
    assert m.update_command("nope", _cmd()) is False
    m.commands["c1"] = _cmd("c1")
    scheduled = []
    monkeypatch.setattr(m, "_schedule_event", lambda e: scheduled.append(e))
    rep = _cmd("c1", repeating_enabled=True)
    assert m.update_command("c1", rep)
    assert scheduled and not rep.repeating_enabled


def test_remove_get_and_list_commands(env):
    m = env.make()
    m.commands["c1"] = _cmd("c1")
    assert m.get_command("c1").command_id == "c1"
    all_cmds = m.get_all_commands()
    all_cmds.clear()
    assert "c1" in m.commands  # a copy is returned
    assert m.remove_command("c1") and m.get_command("c1") is None
    assert m.remove_command("c1") is False


# --------------------------------------------------------------------------- events CRUD


def test_validate_event(env):
    assert cm.ChatbotManager.validate_event(_evt(etype="interval", interval=0)) == "Timer events need an interval."
    assert cm.ChatbotManager.validate_event(_evt(etype="interval", interval=5)) is None
    bad = _evt(etype="specific_time", specific_time="25:00")
    assert cm.ChatbotManager.validate_event(bad).startswith("Pick a time as HH:MM")
    good = _evt(etype="specific_time")
    good.specific_time = "7:05"
    assert cm.ChatbotManager.validate_event(good) is None and good.specific_time == "07:05"
    assert cm.ChatbotManager.validate_event(_evt()) is None


def test_add_event(env, monkeypatch):
    m = env.make()
    scheduled = []
    monkeypatch.setattr(m, "_schedule_event", lambda e: scheduled.append(e.event_id))
    assert m.add_event(_evt(etype="interval", interval=0)) is False
    e = ChatEvent(name="x", event_type="follow")
    assert m.add_event(e) and e.event_id and scheduled == [e.event_id]
    keep = _evt("keep")
    assert m.add_event(keep) and "keep" in m.events


def test_update_event(env, monkeypatch):
    m = env.make()
    scheduled = []
    monkeypatch.setattr(m, "_schedule_event", lambda e: scheduled.append(e.event_id))
    assert m.update_event("e1", _evt(etype="interval", interval=0)) is False
    assert m.update_event("missing", _evt()) is False
    old = _evt("e1", trigger_count=4, last_triggered=9, conditions=[{"field": "a", "operator": "equal", "value": 1}])
    m.events["e1"] = old
    new = _evt("other")
    assert m.update_event("e1", new)
    assert new.event_id == "e1" and new.trigger_count == 4 and new.last_triggered == 9 and len(new.conditions) == 1
    assert scheduled == ["e1"]
    fresh = _evt("x")
    assert m.update_event("e1", fresh, keep_runtime=False, keep_conditions=False)
    assert fresh.trigger_count == 0 and fresh.conditions == []


def test_remove_get_and_list_events(env, monkeypatch):
    m = env.make()
    unscheduled = []
    monkeypatch.setattr(m, "_unschedule_event", lambda eid: unscheduled.append(eid))
    m.events["e1"] = _evt("e1")
    assert m.get_event("e1").event_id == "e1"
    assert "e1" in m.get_all_events() and m.get_all_events() is not m.events
    assert m.remove_event("e1") and unscheduled == ["e1"]
    assert m.remove_event("e1") is False


# --------------------------------------------------------------------------- toggles / counters / stats


def test_toggle_item(env, monkeypatch):
    m = env.make()
    scheduled = []
    monkeypatch.setattr(m, "_schedule_event", lambda e: scheduled.append(e.event_id))
    m.commands["c"] = _cmd("c")
    m.events["e"] = _evt("e")
    assert m.toggle_item("c", 0) and m.commands["c"].enabled is False
    assert m.toggle_item("e", False) and m.events["e"].enabled is False and scheduled == ["e"]
    assert m.toggle_item("zzz", True) is False


def test_reset_counter_and_usage(env):
    m = env.make()
    m.commands["c"] = _cmd("c", counter_value=5, usage_count=3)
    assert m.reset_command_counter("c") and m.commands["c"].counter_value == 0
    assert m.reset_command_counter("x") is False
    assert m.reset_usage("c") and m.commands["c"].usage_count == 0
    assert m.reset_usage("x") is False


def test_get_statistics(env):
    m = env.make()
    m.commands = {"a": _cmd("a", trigger_count=2), "b": _cmd("b", enabled=False, trigger_count=1)}
    m.events = {"i1": _evt("i1", "interval", interval=5, trigger_count=4),
                "i2": _evt("i2", "interval", interval=5, enabled=False),
                "f": _evt("f")}
    m.repeating_tasks = {"i1": object()}
    m._deferred_interval_events = [m.events["i2"]]
    s = m.get_statistics()
    assert s["total_commands"] == 2 and s["total_events"] == 3
    assert s["enabled_items"] == 3 and s["total_triggers"] == 7
    assert s["interval_events"] == {"total": 2, "enabled": 1, "running_tasks": 1, "deferred": 1}


def test_message_may_call_api(env):
    m = env.make()
    m.commands = {"w": _cmd("w", "weather", lookup={"id": "weather"}), "p": _cmd("p", "plain"),
                  "off": _cmd("off", "offline", lookup={"id": "x"}, enabled=False)}
    assert m.message_may_call_api({"message": "!Weather london"})
    assert not m.message_may_call_api({"message": "!plain"})
    assert not m.message_may_call_api({"message": "!offline"})
    assert not m.message_may_call_api({"message": "hello"})
    assert not m.message_may_call_api({"message": "!"})
    assert not m.message_may_call_api(None)


def test_message_may_call_api_during_concurrent_change(env):
    m = env.make()

    class Racy(dict):
        def values(self):
            raise RuntimeError("dictionary changed size during iteration")

    m.commands = Racy()
    assert m.message_may_call_api({"message": "!x"}) is True


# --------------------------------------------------------------------------- cleanup / singleton


def test_cleanup_stops_timers_and_saves_flags(env, monkeypatch):
    m = env.make()
    stop = threading.Event()
    m._repeating_stops["e"] = stop
    m.repeating_tasks["e"] = object()
    saved = []
    monkeypatch.setattr(m.flag_manager, "force_save", lambda: saved.append(1))
    m.cleanup()
    assert stop.is_set() and m._repeating_stops == {} and m.repeating_tasks == {} and saved == [1]


def test_register_shutdown_flush(monkeypatch):
    from modules import shutdown

    seen = []
    monkeypatch.setattr(shutdown, "register_shutdown_flush", lambda name, fn: seen.append((name, fn)))
    cm._register_shutdown_flush("x", print)
    assert seen == [("x", print)]


def test_register_shutdown_flush_without_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "modules.shutdown", None)  # import raises ImportError
    cm._register_shutdown_flush("x", print)  # no error


def test_get_manager_singleton(env, monkeypatch):
    monkeypatch.setattr(cm, "_chatbot_manager", None)
    registered = []
    monkeypatch.setattr(cm, "_register_shutdown_flush", lambda name, fn: registered.append(name))
    first = cm.get_manager()
    assert cm.get_manager() is first and isinstance(first, cm.ChatbotManager)
    assert registered == ["chatbot_data"]
