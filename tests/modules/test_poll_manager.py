# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/poll_manager.py: helpers, config/runtime parsing, saves, timers,
votes, Twitch poll sync, announcements and snapshots."""

from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import chat_banner, chatbot, database_manager, notification_engine, path_utils, shutdown
from modules import poll_manager as pm
from modules import statistics_manager, twitch_polls, web_engine
from modules.spore_studio import template_parser_back


# --------------------------------------------------------------------------- fakes


class FakeTimer:
    created = []

    def __init__(self, delay, fn, args=()):
        self.delay = delay
        self.fn = fn
        self.args = args
        self.daemon = False
        self.started = False
        self.cancelled = False
        FakeTimer.created.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.fn(*self.args)


class FakeThread:
    created = []

    def __init__(self, target=None, args=(), name=None, daemon=None):
        self.target = target
        self.args = args
        self.name = name
        self.started = False
        self.alive = False
        FakeThread.created.append(self)

    def start(self):
        self.started = True
        self.alive = True

    def is_alive(self):
        return self.alive

    def run_now(self):
        self.target(*self.args)


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
        self.events = []
        self.banners = []
        self.banner_raises = False
        self.chat = []
        self.chat_raises = False
        self.stats = []
        self.stats_raises = False
        self.flushes = []
        self.twitch_ready = "ok"
        self.twitch_create = (True, {"id": "tp1", "choices": [], "started_at": None})
        self.twitch_created = []
        self.twitch_ended = []

    def get_data_checked(self, path):
        assert path == pm.DATA_PATH
        if self.read_raises:
            raise RuntimeError("db gone")
        return self.stored, self.read_ok

    def set_data(self, path, payload):
        if self.write_raises:
            raise self.write_raises
        self.writes.append((path, payload))
        return self.write_result

    def last(self):
        return self.writes[-1][1] if self.writes else None


@pytest.fixture
def env(monkeypatch):
    e = Env()
    FakeTimer.created = []
    FakeThread.created = []

    @contextmanager
    def quiet():
        yield

    monkeypatch.setattr(database_manager, "get_data_checked", e.get_data_checked)
    monkeypatch.setattr(database_manager, "set_data", e.set_data)
    monkeypatch.setattr(database_manager, "quiet_notifications", quiet)
    monkeypatch.setattr(database_manager, "last_write_problem", lambda: e.problem)
    monkeypatch.setattr(notification_engine, "notify", lambda msg, **kw: e.notices.append((msg, kw)))
    monkeypatch.setattr(shutdown, "register_shutdown_flush", lambda name, fn: e.flushes.append((name, fn)))
    monkeypatch.setattr(web_engine, "web_engine_instance",
                        SimpleNamespace(safe_emit=lambda ev, data: e.events.append((ev, data))), raising=False)

    def banner(text, duration=None, source=None):
        if e.banner_raises:
            raise RuntimeError("no overlay")
        e.banners.append((text, duration, source))

    def chat(text, platforms):
        if e.chat_raises:
            raise RuntimeError("no chat")
        e.chat.append((text, platforms))

    monkeypatch.setattr(chat_banner, "emit_chat_banner", banner)
    monkeypatch.setattr(chatbot, "dispatch_chatbot_response", chat)

    class Stats:
        def record_poll_finished(self, options, winners):
            if e.stats_raises:
                raise RuntimeError("stats")
            e.stats.append((options, winners))

    monkeypatch.setattr(statistics_manager, "get_statistics_manager", lambda: Stats())
    monkeypatch.setattr(twitch_polls, "readiness", lambda: e.twitch_ready)
    monkeypatch.setattr(twitch_polls, "readiness_message", lambda status: f"not ready: {status}")

    def create(title, choices, duration, points_enabled=False, points_per_vote=0):
        e.twitch_created.append((title, choices, duration, points_enabled, points_per_vote))
        return e.twitch_create

    monkeypatch.setattr(twitch_polls, "create_poll", create)
    monkeypatch.setattr(twitch_polls, "end_poll", lambda poll_id: e.twitch_ended.append(poll_id))
    fake_threading = SimpleNamespace(Timer=FakeTimer, Thread=FakeThread, RLock=threading.RLock,
                                     Lock=threading.Lock, Event=threading.Event)
    monkeypatch.setattr(pm, "threading", fake_threading)
    return e


@pytest.fixture
def clock(monkeypatch):
    box = {"now": 1000.0}
    monkeypatch.setattr(pm.PollManager, "_now", lambda self: box["now"])
    return box


def _manager(env, persist=True):
    return pm.PollManager(persist=persist)


def _ready_poll(env, template="poll", names=("Yes", "No"), **cfg):
    m = _manager(env)
    for n in names:
        assert m.add_category(template, n) == (True, "")
    if cfg:
        m.update_config(template, cfg)
    return m


def _cat_ids(m, template="poll"):
    return [c["id"] for c in m.get_config(template)["categories"]]


def _vote(m, user, text, **extra):
    return m.try_register_vote({"username": user, "userid": user, "message": text, **extra})


# --------------------------------------------------------------------------- pure helpers


@pytest.mark.parametrize(
    "raw,fallback,expected",
    [
        ("#Yes Please", "", "#YesPlease"),
        ("", "My Option", "#MyOption"),
        (None, None, ""),
        ("#", "", ""),
        ("!!!", "", ""),
        ("a-b_c", "", "#a-b_c"),
    ],
)
def test_normalize_keyword(raw, fallback, expected):
    assert pm.normalize_keyword(raw, fallback) == expected


@pytest.mark.parametrize("raw,expected", [("!start poll", "!startpoll"), ("go", "!go"), ("", ""), ("!", ""), (None, "")])
def test_normalize_command(raw, expected):
    assert pm.normalize_command(raw) == expected


@pytest.mark.parametrize(
    "message,command,expected",
    [
        ("!startpoll", "!startpoll", True),
        ("!StartPoll now", "startpoll", True),
        ("!startpolls", "!startpoll", False),
        ("please !startpoll", "!startpoll", False),
        ("", "!startpoll", False),
        ("!x", "", False),
        ("!startpoll\tgo", "!startpoll", True),
    ],
)
def test_message_starts_with_command(message, command, expected):
    assert pm.message_starts_with_command(message, command) is expected


@pytest.mark.parametrize(
    "msg,expected",
    [
        ("not a dict", False),
        ({"is_moderator": True}, True),
        ({"is_broadcaster": 1}, True),
        ({"is_chat_owner": True}, True),
        ({"badges": "moderator/1,subscriber/3"}, True),
        ({"badges": "broadcaster/1"}, True),
        ({"badges": "vip/1"}, False),
        ({"badges": ["moderator/1"]}, False),
        ({}, False),
    ],
)
def test_sender_can_start_poll(msg, expected):
    assert pm.sender_can_start_poll(msg) is expected


def test_keyword_match_span_edges():
    assert pm.keyword_match_span("", "#a") is None
    assert pm.keyword_match_span("hi #a", "") is None
    assert pm.keyword_match_span("hi #a.", "#a") == (3, 5)
    assert pm.keyword_match_span("x#ab", "#a") is None


def test_earliest_category_skips_non_dicts_and_misses():
    cats = ["junk", {"id": "a", "keyword": "#a"}, {"id": "b", "keyword": "#b"}, {"id": "c", "keyword": ""}]
    assert pm.earliest_category("#b then #a", cats)["id"] == "b"
    assert pm.earliest_category("nothing", cats) is None


@pytest.mark.parametrize(
    "msg,expected",
    [
        ({"platform": "YouTube", "userid": "7"}, "youtube:7"),
        ({"source": "twitch", "user_id": "9"}, "twitch:9"),
        ({"platform": "kick", "username": " Bob "}, "twitch:bob"),
        ({}, ""),
    ],
)
def test_voter_identity(msg, expected):
    assert pm.voter_identity(msg) == expected


def test_normalize_last_winners():
    assert pm._normalize_last_winners("x") == []
    raw = ["junk", {"name": ""}, {"name": " A ", "votes": "3", "id": 1, "keyword": "#a"},
           {"name": "B", "votes": "bad"}, {"name": "C", "votes": -4}]
    assert pm._normalize_last_winners(raw) == [
        {"id": "1", "name": "A", "keyword": "#a", "votes": 3},
        {"id": "", "name": "B", "keyword": "", "votes": 0},
        {"id": "", "name": "C", "keyword": "", "votes": 0},
    ]


def test_new_category_defaults():
    cat = pm._new_category("  ")
    assert cat["name"] == "Option" and cat["keyword"] == "#Option" and cat["votes"] == 0
    assert len(cat["id"]) == 12
    assert pm._new_category("Pizza", "#za")["keyword"] == "#za"


def test_walk_and_poll_elements():
    model = {"elements": [
        {"type": "Poll", "id": "p1"},
        "junk",
        {"type": "group", "children": [{"type": "poll", "id": "p2"}, {"type": "text"}]},
    ]}
    assert [e["id"] for e in pm._poll_elements(model)] == ["p1", "p2"]
    assert pm._poll_elements(None) == []
    assert pm._walk_elements("x") == []


def test_list_poll_template_names(monkeypatch, tmp_path):
    folder = tmp_path / "_spore"
    folder.mkdir()
    for name in ["b.spore.json", "a.spore.json", "_hidden.spore.json", ".spore.json", "c.spore.json", "x.txt"]:
        (folder / name).write_text("{}")
    models = {"a": {"elements": [{"type": "poll"}]}, "b": {"elements": [{"type": "poll"}]}, "c": {"elements": []}}
    monkeypatch.setattr(path_utils, "get_template_path", lambda rel="": str(tmp_path / rel))
    monkeypatch.setattr(template_parser_back, "load_sidecar", lambda stem: models.get(stem))
    assert pm.list_poll_template_names() == ["a", "b"]


def test_list_poll_template_names_missing_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(path_utils, "get_template_path", lambda rel="": str(tmp_path / "nope"))
    assert pm.list_poll_template_names() == []


def test_list_poll_template_names_unreadable_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(path_utils, "get_template_path", lambda rel="": str(tmp_path))

    def boom(_p):
        raise OSError("denied")

    monkeypatch.setattr(pm.os, "listdir", boom)
    assert pm.list_poll_template_names() == []


def test_list_poll_template_names_import_failure(monkeypatch):
    monkeypatch.setitem(sys.modules, "modules.spore_studio.template_parser_back", None)
    assert pm.list_poll_template_names() == []


def test_format_banner_message():
    assert pm.format_banner_message("", []) == pm.DEFAULT_BANNER_MESSAGE
    assert pm.format_banner_message(" Hi ", []) == "Hi"
    one = [{"name": "A", "keyword": "#a", "votes": 3}]
    assert pm.format_banner_message("{option} {keyword} {votes} {options}", one) == "A #a 3 A"
    two = one + [{"name": "B", "keyword": "#b", "votes": 3}]
    assert pm.format_banner_message("{option}|{keyword}|{votes}", two) == "A, B|#a, #b|3"
    assert pm.format_banner_message(None, one) == "Poll winner: A (3)"


def test_poll_widget_info(monkeypatch):
    model = {"elements": [{"type": "poll", "id": "w1", "props": {"bar_color": "#000", "title": "", "show_counts": False}},
                          {"type": "poll", "id": "w2"}]}
    monkeypatch.setattr(template_parser_back, "load_sidecar", lambda name: model)
    info = pm.poll_widget_info("t")
    assert info["id"] == "w1" and info["count"] == 2
    assert info["style"]["bar_color"] == "#000"
    assert info["style"]["show_counts"] is False
    assert info["style"]["title"] == ""  # empty keeps default ""


def test_poll_widget_info_errors(monkeypatch):
    def boom(name):
        raise RuntimeError("bad sidecar")

    monkeypatch.setattr(template_parser_back, "load_sidecar", boom)
    info = pm.poll_widget_info(None)
    assert info == {"id": "", "count": 0, "style": pm._WIDGET_STYLE_DEFAULTS}


def test_notify_poll_problem(env, monkeypatch):
    pm._notify_poll_problem("T", "M", "H", "D", "k", critical=True)
    msg, kw = env.notices[-1]
    assert msg == "M" and kw["type"] == "error" and kw["dedupe_key"] == "k"
    assert kw["actions"] == notification_engine.nav_chatbot("Polls")
    pm._notify_poll_problem("T", "M", "H", "D", "k")
    assert env.notices[-1][1]["type"] == "warning"

    def boom(*_a, **_k):
        raise RuntimeError("x")

    monkeypatch.setattr(notification_engine, "notify", boom)
    pm._notify_poll_problem("T", "M", "H", "D", "k")  # swallowed


def test_register_shutdown_flush(env, monkeypatch):
    pm._register_shutdown_flush("n", print)
    assert env.flushes[-1] == ("n", print)
    monkeypatch.setitem(sys.modules, "modules.shutdown", None)
    pm._register_shutdown_flush("m", print)  # ImportError ignored
    assert len(env.flushes) == 1


def test_get_poll_manager_singleton(env, monkeypatch):
    monkeypatch.setattr(pm, "_poll_manager", None)
    first = pm.get_poll_manager()
    assert first is pm.get_poll_manager()
    assert ("poll_votes", first.flush) in env.flushes


# --------------------------------------------------------------------------- load / parse


def test_persist_false_skips_load_and_flush(env):
    m = _manager(env, persist=False)
    m.add_category("poll", "A")
    m.flush()
    assert env.writes == [] and env.flushes == []


def test_load_failure_marks_and_exception(env):
    env.read_ok = False
    m = _manager(env)
    assert m._load_failed is True
    env.read_ok = True
    env.read_raises = True
    m2 = _manager(env)
    assert m2._load_failed is True
    assert m2._templates == {}


def test_load_restores_open_poll_and_schedules_timer(env, clock):
    env.stored = {"templates": {
        "live": {"config": {"duration_seconds": 60, "categories": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]},
                 "runtime": {"status": "open", "started_at": 990, "ends_at": 1030, "votes": {"a": 2}}},
        "expired": {"config": {"duration_seconds": 10, "categories": [{"id": "a", "name": "A"}]},
                    "runtime": {"status": "open", "ends_at": 900}},
        "permanent": {"config": {"categories": [{"id": "a", "name": "A"}]},
                      "runtime": {"status": "open", "ends_at": 5000}},
        "": {"config": {}},
        "bad": "not a dict",
    }}
    m = _manager(env)
    assert set(m._templates) == {"live", "expired", "permanent"}
    assert m._templates["expired"]["runtime"]["status"] == "idle"
    assert m._templates["permanent"]["runtime"]["status"] == "open"
    assert m._templates["permanent"]["runtime"]["ends_at"] is None
    timer = m._timers["live"]
    assert timer.delay == 30 and timer.started and timer.daemon
    assert m.get_overlay_state("live")["options"][0]["votes"] == 2


def test_parse_stored_handles_garbage_and_no_schedule(env, clock):
    m = _manager(env, persist=False)
    assert m._parse_stored({"templates": []}, 0) == ({}, [])
    assert m._parse_stored("x", 0) == ({}, [])
    raw = {"templates": {"t": {"config": {"duration_seconds": 60, "categories": [{"id": "a", "name": "A"}]},
                               "runtime": {"status": "open", "ends_at": 1030}}}}
    restored, timers = m._parse_stored(raw, 1000.0, schedule=False)
    assert timers == [] and restored["t"]["runtime"]["status"] == "open"
    # ends_at exactly now -> expired
    restored, timers = m._parse_stored(raw, 1030.0)
    assert restored["t"]["runtime"]["status"] == "idle"


def test_apply_stored_without_schedule(env, clock):
    m = _manager(env, persist=False)
    raw = {"templates": {"t": {"config": {"duration_seconds": 60, "categories": [{"id": "a", "name": "A"}]},
                               "runtime": {"status": "open", "ends_at": 1030}}}}
    m._apply_stored(raw, 1000.0, schedule=False)
    assert m._timers == {}


@pytest.mark.parametrize(
    "duration,ends_at,expected",
    [(0, None, False), (10, None, True), (10, "", True), (10, "bad", True), (10, 999, True), (10, 1001, False)],
)
def test_is_expired(env, duration, ends_at, expected):
    m = _manager(env, persist=False)
    assert m._is_expired({"duration_seconds": duration}, {"ends_at": ends_at}, 1000.0) is expected


def test_normalize_config_values(env):
    m = _manager(env, persist=False)
    cfg = m._normalize_config({
        "duration_seconds": "bad", "store_data": True, "start_command": "go now", "banner_message": "  ",
        "banner_duration": "x", "last_winners": [{"name": "A", "votes": 1}],
        "categories": ["junk", {"name": ""}, {"id": "a", "name": "A", "votes": "x"},
                       {"id": "a", "name": "B", "keyword": "bee", "votes": -3}],
    })
    assert cfg["duration_seconds"] == 0
    assert "store_data" not in cfg
    assert cfg["start_command"] == "!gonow"
    assert cfg["banner_message"] == pm.DEFAULT_BANNER_MESSAGE
    assert cfg["banner_duration"] == 10
    assert cfg["last_winners"][0]["name"] == "A"
    a, b = cfg["categories"]
    assert a["id"] == "a" and a["votes"] == 0 and a["keyword"] == "#A"
    assert b["id"] != "a" and b["keyword"] == "#bee" and b["votes"] == 0
    assert m._normalize_config("nope")["categories"] == []


def test_normalize_config_clamps(env):
    m = _manager(env, persist=False)
    cfg = m._normalize_config({"duration_seconds": 10 ** 9, "banner_duration": -5, "banner_message": "hi"})
    assert cfg["duration_seconds"] == 86400 * 7
    assert cfg["banner_duration"] == 0.0
    assert m._normalize_config({"banner_duration": None})["banner_duration"] == 10


def test_normalize_config_twitch_rules(env):
    m = _manager(env, persist=False)
    long_name = "An extremely long option name here"
    cfg = m._normalize_config({
        "twitch_poll": True, "duration_seconds": 0, "twitch_title": "Q" * 80, "twitch_points_per_vote": "bad",
        "categories": [
            {"id": "a", "name": long_name},                      # keyword followed name -> follows cut
            {"id": "b", "name": long_name + "2", "keyword": "#custom"},  # custom keyword kept
        ],
    })
    assert cfg["duration_seconds"] == 180
    assert len(cfg["twitch_title"]) == twitch_polls.TITLE_MAX
    assert cfg["twitch_points_per_vote"] == 100
    a, b = cfg["categories"]
    assert a["name"] == twitch_polls.clamp_choice_name(long_name)
    assert a["keyword"] == pm.normalize_keyword("", a["name"])
    assert b["keyword"] == "#custom"


def test_normalize_runtime(env):
    m = _manager(env, persist=False)
    config = {"duration_seconds": 0, "categories": [{"id": "a"}, {"id": "b"}]}
    rt = m._normalize_runtime({
        "status": "weird", "started_at": "bad", "ends_at": 5, "ended_at": "",
        "votes": {"a": "3", "b": "x", "z": 9}, "voters": {"u1": "a", "": "a", "u2": "z"},
        "winners": ["a", "z"], "twitch_poll_id": "  ", "twitch_choices": {"c1": "a"},
    }, config)
    assert rt["status"] == "idle"
    assert rt["started_at"] is None and rt["ends_at"] is None and rt["ended_at"] is None
    assert rt["votes"] == {"a": 3, "b": 0}
    assert rt["voters"] == {"u1": "a"}
    assert rt["winners"] == ["a"]
    assert rt["twitch_poll_id"] is None and rt["twitch_choices"] == {}
    rt = m._normalize_runtime({"status": "open", "ends_at": "50", "votes": "bad", "voters": [],
                               "twitch_poll_id": "p", "twitch_choices": {"c1": "a", "c2": "zz"}},
                              {"duration_seconds": 10, "categories": [{"id": "a"}]})
    assert rt["ends_at"] == 50.0 and rt["votes"] == {"a": 0}
    assert rt["twitch_poll_id"] == "p" and rt["twitch_choices"] == {"c1": "a"}


# --------------------------------------------------------------------------- saving


def test_persist_writes_payload(env):
    m = _ready_poll(env)
    payload = env.last()
    assert set(payload["templates"]) == {"poll"}
    assert "runtime" not in payload["templates"]["poll"]
    assert [c["name"] for c in payload["templates"]["poll"]["config"]["categories"]] == ["Yes", "No"]


def test_payload_includes_open_runtime(env, clock):
    m = _ready_poll(env)
    m.start_poll("poll")
    stored = env.last()["templates"]["poll"]["runtime"]
    assert stored["status"] == "open" and stored["started_at"] == 1000.0
    assert stored["winners"] == []


def test_write_failure_notifies(env):
    env.write_result = False
    env.problem = ("sql", "locked")
    m = _manager(env)
    m.ensure_template("poll")
    msg, kw = env.notices[-1]
    assert kw["title"] == "Couldn't save polls" and kw["type"] == "error"
    assert kw["details"].endswith("(sql: locked)")
    env.problem = None
    m.update_config("poll", {"duration_seconds": 5})
    assert env.notices[-1][1]["details"] == f"Database write returned no success for {pm.DATA_PATH}"


def test_write_exception_notifies(env):
    env.write_raises = OSError("disk")
    m = _manager(env)
    m.ensure_template("poll")
    assert env.notices[-1][1]["details"] == "OSError: disk"


def test_older_payload_ignored(env):
    m = _manager(env)
    with m._lock:
        old = m._build_payload_locked()
        new = m._build_payload_locked()
    m._write_payload(*new)
    m._write_payload(*old)
    assert len(env.writes) == 1


def test_failed_load_blocks_writes_until_readable(env):
    env.read_ok = False
    env.stored = {}
    m = _manager(env)
    m.add_category("poll", "Yes")
    assert env.writes == []
    assert m._vote_dirty is True
    assert env.notices[-1][1]["dedupe_key"] == "poll:save_blocked"
    # re-read raising also counts as unreadable
    env.read_raises = True
    m.update_config("poll", {"duration_seconds": 30})
    assert env.writes == []
    env.read_raises = False
    env.read_ok = True
    env.stored = {"templates": {"other": {"config": {"categories": [{"id": "x", "name": "X"}]}}}}
    m.flush()
    payload = env.last()
    assert set(payload["templates"]) == {"poll", "other"}
    assert payload["templates"]["poll"]["config"]["duration_seconds"] == 30
    assert m._load_failed is False


def test_recover_restores_untouched_templates_and_schedules(env, clock):
    env.read_ok = False
    m = _manager(env)
    m.ensure_template("idle")  # created while unreadable, unchanged since -> stored copy wins
    env.read_ok = True
    env.stored = {"templates": {
        "idle": {"config": {"duration_seconds": 60, "categories": [{"id": "a", "name": "A"}]},
                 "runtime": {"status": "open", "ends_at": 1020}},
        "new": {"config": {"categories": []}},
    }}
    with m._lock:
        assert m._recover_locked() is True
    assert m._templates["idle"]["runtime"]["status"] == "open"
    assert m._timers["idle"].delay == 20
    assert "new" in m._templates
    assert m._failed_load_baselines == {}


def test_recover_with_non_dict_raw(env):
    env.read_ok = False
    m = _manager(env)
    env.read_ok = True
    env.stored = None
    with m._lock:
        assert m._recover_locked() is True


def test_ensure_template_blank_and_existing(env):
    m = _manager(env)
    assert m.ensure_template("  ") == pm.DEFAULT_CONFIG
    m.ensure_template("a")
    n = len(env.writes)
    cfg = m.ensure_template("a")
    assert len(env.writes) == n
    cfg["categories"].append("mutated")
    assert m.get_config("a")["categories"] == []


def test_get_config_unknown_and_last_error(env):
    m = _manager(env)
    assert m.get_config("nope") == pm.DEFAULT_CONFIG
    assert m.get_last_error("nope") == ""
    m._last_error["x"] = "boom"
    assert m.get_last_error(" x ") == "boom"


def test_update_config_ignores_blank_categories_and_unknown(env):
    m = _ready_poll(env)
    n = len(env.writes)
    m.update_config("", {"duration_seconds": 3})
    assert len(env.writes) == n
    m.update_config("poll", {"categories": [], "bogus": 1, "duration_seconds": 30})
    cfg = m.get_config("poll")
    assert len(cfg["categories"]) == 2
    assert "bogus" not in cfg and cfg["duration_seconds"] == 30
    m.update_config("poll", None)


# --------------------------------------------------------------------------- vote writer


def test_schedule_vote_persist_starts_writer_once(env):
    m = _manager(env)
    with m._lock:
        m._schedule_vote_persist_locked()
        m._schedule_vote_persist_locked()
    assert len(FakeThread.created) == 1
    assert FakeThread.created[0].name == "PollVoteWriter"
    assert m._vote_wake.is_set() and m._vote_dirty
    FakeThread.created[0].alive = False
    with m._lock:
        m._schedule_vote_persist_locked()
    assert len(FakeThread.created) == 2


def test_schedule_vote_persist_disabled(env):
    m = _manager(env, persist=False)
    with m._lock:
        m._schedule_vote_persist_locked()
    assert FakeThread.created == [] and not m._vote_dirty


def test_vote_writer_loop_writes_and_survives_errors(env, monkeypatch):
    m = _manager(env)
    sleeps = []
    monkeypatch.setattr(pm.time, "sleep", lambda s: sleeps.append(s))

    class Stop(Exception):
        pass

    class Wake:
        def __init__(self):
            self.n = 0

        def wait(self):
            self.n += 1
            if self.n > 2:
                raise Stop

        def clear(self):
            pass

        def set(self):
            pass

    m._vote_wake = Wake()
    calls = []

    def write():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("first write fails")

    m._write_pending_votes = write
    with pytest.raises(Stop):
        m._vote_writer_loop()
    assert len(calls) == 2
    assert sleeps == [pm._VOTE_WRITE_COALESCE_SECONDS] * 2


def test_write_pending_votes(env):
    m = _manager(env)
    m._write_pending_votes()  # not dirty: nothing
    assert env.writes == []
    m._vote_dirty = True
    m._write_pending_votes()
    assert len(env.writes) == 1 and not m._vote_dirty
    # failed load and still unreadable -> stays dirty
    m._vote_dirty = True
    m._load_failed = True
    env.read_ok = False
    m._write_pending_votes()
    assert len(env.writes) == 1 and m._vote_dirty


# --------------------------------------------------------------------------- timers


def test_schedule_and_cancel_timers(env):
    m = _manager(env)
    with m._lock:
        m._schedule_locked("t", 5)
        first = m._timers["t"]
        m._schedule_locked("t", 0)  # cancels, no new timer
    assert first.cancelled and "t" not in m._timers
    with m._lock:
        m._schedule_locked("a", 1)
        m._schedule_locked("b", 2)
    m.shutdown()
    assert m._timers == {}
    assert all(t.cancelled for t in FakeTimer.created[1:])
    m._cancel_timer("missing")


def test_timer_fire_stops_poll_with_generation(env, clock):
    m = _ready_poll(env, duration_seconds=30)
    m.start_poll("poll")
    timer = m._timers["poll"]
    assert timer.delay == 30.0
    _vote(m, "u1", "#Yes")
    timer.fire()
    assert m.get_overlay_state("poll")["status"] == "ended"
    assert env.twitch_ended == []


def test_stale_timer_generation_is_ignored(env, clock):
    m = _ready_poll(env, duration_seconds=30)
    m.start_poll("poll")
    old = m._timers["poll"]
    m.stop_poll("poll")
    m.start_poll("poll")
    assert m.stop_poll("poll", generation=old.args[1]) == (False, "Poll is not running.")
    assert m.get_overlay_state("poll")["status"] == "open"


# --------------------------------------------------------------------------- categories


def test_add_category_defaults_and_dedupes_keywords(env):
    m = _manager(env)
    assert m.add_category("p") == (True, "")
    assert m.add_category("p", "Option 1") == (True, "")
    assert m.add_category("p", "Option 1") == (True, "")
    kws = [c["keyword"] for c in m.get_config("p")["categories"]]
    assert kws == ["#Option1", "#Option12", "#Option13"]


def test_add_category_blocked_when_open_and_twitch_full(env, clock):
    m = _ready_poll(env)
    m.start_poll("poll")
    assert m.add_category("poll", "X") == (False, "End the poll before changing its options.")
    m2 = _manager(env)
    m2.update_config("t", {"twitch_poll": True})
    for i in range(pm.TWITCH_CHOICES_MAX):
        assert m2.add_category("t", f"O{i}")[0]
    assert m2.add_category("t", "extra") == (False, pm.TWITCH_OPTIONS_FULL_MESSAGE)


def test_update_category_paths(env, clock):
    m = _ready_poll(env)
    yes, no = _cat_ids(m)
    assert m.update_category("poll", "nope", name="x") == (False, "Option not found.")
    assert m.update_category("poll", yes, name="  ") == (False, "Option name cannot be empty.")
    assert m.update_category("poll", yes, keyword="!!!", name="!!!") == (False, "Keyword cannot be empty.")
    assert m.update_category("poll", yes, keyword="#no") == (False, "#no is already used by another option.")
    assert m.update_category("poll", yes, name="Sure", keyword="ok") == (True, "")
    cat = m.get_config("poll")["categories"][0]
    assert cat["name"] == "Sure" and cat["keyword"] == "#ok"
    assert m.update_category("poll", yes) == (True, "")  # nothing given: keeps both
    m.start_poll("poll")
    assert m.update_category("poll", yes, name="Z")[0] is False


def test_update_category_twitch_clamps_name(env):
    m = _ready_poll(env, twitch_poll=True)
    yes = _cat_ids(m)[0]
    assert m.update_category("poll", yes, name="N" * 40)[0]
    assert m.get_config("poll")["categories"][0]["name"] == "N" * twitch_polls.CHOICE_TITLE_MAX


def test_remove_category(env, clock):
    m = _ready_poll(env)
    yes, no = _cat_ids(m)
    assert m.remove_category("poll", "nope") == (False, "Option not found.")
    assert m.remove_category("poll", yes) == (True, "")
    assert _cat_ids(m) == [no]
    m.add_category("poll", "B")
    m.start_poll("poll")
    assert m.remove_category("poll", no)[0] is False


def test_move_category(env, clock):
    m = _ready_poll(env, names=("A", "B", "C"))
    a, b, c = _cat_ids(m)
    assert m.move_category("poll", "nope", 1) == (False, "Option not found.")
    n = len(env.writes)
    assert m.move_category("poll", a, -1) == (True, "")  # out of range: no-op
    assert m.move_category("poll", c, 1) == (True, "")
    assert len(env.writes) == n
    assert m.move_category("poll", a, 1) == (True, "")
    assert _cat_ids(m) == [b, a, c]
    m.start_poll("poll")
    assert m.move_category("poll", a, 1)[0] is False


def test_set_category_votes(env, clock):
    m = _ready_poll(env)
    yes, no = _cat_ids(m)
    assert m.set_category_votes("poll", "nope", 1) == (False, "Option not found.")
    assert m.set_category_votes("poll", yes, "bad") == (True, "")
    assert m.get_config("poll")["categories"][0]["votes"] == 0
    assert m.set_category_votes("poll", yes, -5) == (True, "")
    assert m.set_category_votes("poll", yes, 4) == (True, "")
    assert m.get_overlay_state("poll")["options"][0]["votes"] == 4
    assert env.events == []  # idle: no emit
    m.start_poll("poll")
    assert m.set_category_votes("poll", yes, 1) == (False, "End the poll before editing votes.")


def test_set_category_votes_after_end_repicks_winner(env, clock):
    m = _ready_poll(env)
    yes, no = _cat_ids(m)
    m.start_poll("poll")
    _vote(m, "u1", "#Yes")
    m.stop_poll("poll")
    env.events.clear()
    assert m.set_category_votes("poll", no, 5) == (True, "")
    state = m.get_overlay_state("poll")
    assert [w["name"] for w in state["winners"]] == ["No"]
    assert m.get_config("poll")["last_winners"][0]["name"] == "No"
    assert env.events[-1][0] == "poll_state"


# --------------------------------------------------------------------------- start


def test_start_problems(env):
    m = _manager(env)
    m.add_category("p", "A")
    assert m.start_poll("p") == (False, "Add at least two options before starting.")
    with m._lock:
        cats = [{"name": "A", "keyword": ""}, {"name": "", "keyword": ""}]
        assert m._start_problem_locked("p", {"categories": cats}) == "A needs a keyword."
        assert m._start_problem_locked("p", {"categories": cats[1:] * 2}) == "An option needs a keyword."
        dup = [{"name": "A", "keyword": "#a"}, {"name": "B", "keyword": "#A"}]
        assert m._start_problem_locked("p", {"categories": dup}) == "#A is used more than once."


def test_start_twitch_problems(env, clock):
    m = _ready_poll(env, twitch_poll=True)
    assert m.start_poll("poll") == (False, "Give the Twitch poll a question.")
    m.update_config("poll", {"twitch_title": "Q?"})
    other = _ready_poll(env, template="other", twitch_poll=True, twitch_title="Other?")
    # pretend "other" runs on Twitch in this manager
    m._templates["other"] = other._templates["other"]
    m._templates["other"]["runtime"] = {**pm._idle_runtime(), "status": "open", "twitch_poll_id": "x"}
    assert m.twitch_poll_running() == "other"
    assert m.twitch_poll_running(exclude="other") == ""
    assert m.start_poll("poll") == (False, pm.ONE_TWITCH_POLL_MESSAGE.format(template="other"))


def test_start_twitch_not_ready_and_create_fails(env, clock):
    m = _ready_poll(env, twitch_poll=True, twitch_title="Q?")
    env.twitch_ready = "no_scope"
    assert m.start_poll("poll") == (False, "not ready: no_scope")
    env.twitch_ready = "ok"
    env.twitch_create = (False, "Twitch said no")
    assert m.start_poll("poll") == (False, "Twitch said no")
    assert m.get_overlay_state("poll")["status"] == "idle"


def test_start_plain_poll(env, clock):
    m = _ready_poll(env)
    m.set_category_votes("poll", _cat_ids(m)[0], 3)
    assert m.start_poll("poll") == (True, "Poll started.")
    state = m.get_overlay_state("poll")
    assert state["status"] == "open" and state["permanent"] is True
    assert state["total_votes"] == 0
    assert m._timers == {}
    assert env.events[-1] == ("poll_state", state)


def test_start_twitch_poll_maps_choices_and_uses_twitch_clock(env, clock):
    m = _ready_poll(env, twitch_poll=True, twitch_title="Q?", duration_seconds=60,
                    twitch_points_enabled=True, twitch_points_per_vote=50)
    yes, no = _cat_ids(m)
    env.twitch_create = (True, {"id": "tp1", "started_at": "1970-01-01T00:16:35Z",
                                "choices": [{"id": "c1", "title": "Yes"}, {"id": "c2", "title": "No"}]})
    assert m.start_poll("poll") == (True, "Poll started on Twitch too.")
    assert env.twitch_created == [("Q?", ["Yes", "No"], 60, True, 50)]
    rt = m._templates["poll"]["runtime"]
    assert rt["twitch_poll_id"] == "tp1"
    assert rt["twitch_choices"] == {"c1": yes, "c2": no}
    assert rt["started_at"] == 995.0 and rt["ends_at"] == 1055.0
    assert m._timers["poll"].delay == 60 + pm._TWITCH_END_GRACE_SECONDS
    assert m.get_overlay_state("poll")["twitch_poll"] is True
    # chat keywords do not count for a Twitch poll
    assert _vote(m, "u", "#Yes") is False


def test_start_twitch_poll_ignores_far_off_start_time(env, clock):
    m = _ready_poll(env, twitch_poll=True, twitch_title="Q?", duration_seconds=60)
    env.twitch_create = (True, {"id": "tp1", "started_at": "2001-01-01T00:00:00Z", "choices": []})
    m.start_poll("poll")
    assert m._templates["poll"]["runtime"]["started_at"] == 1000.0


# --------------------------------------------------------------------------- stop


def test_stop_not_running(env):
    m = _ready_poll(env)
    assert m.stop_poll("poll") == (False, "Poll is not running.")
    assert m.stop_poll("missing") == (False, "Poll is not running.")


def test_stop_with_winner_announces_and_records(env, clock):
    m = _ready_poll(env, announce_winners_as_banner=True, announce_winners_in_chat=True,
                    banner_message="{option} wins with {votes}", banner_duration=7)
    m.start_poll("poll")
    _vote(m, "u1", "#Yes")
    _vote(m, "u2", "#yes")
    _vote(m, "u3", "#No")
    clock["now"] = 1010.0
    ok, msg = m.stop_poll("poll")
    assert (ok, msg) == (True, "Winner: Yes")
    events = [e for e, _ in env.events]
    assert events[-2:] == ["poll_state", "poll_ended"]
    assert env.banners == [("Yes wins with 2", 7.0, "poll")]
    assert env.chat == [("Yes wins with 2", ["twitch", "youtube"])]
    options, winners = env.stats[-1]
    assert [w["name"] for w in winners] == ["Yes"]
    cfg = m.get_config("poll")
    assert [c["votes"] for c in cfg["categories"]] == [2, 1]
    assert cfg["last_winners"][0]["votes"] == 2
    state = m.get_overlay_state("poll")
    assert state["status"] == "ended" and state["ended_at"] == 1010.0 and state["ends_at"] is None


def test_stop_no_votes_keeps_previous_winners(env, clock):
    m = _ready_poll(env)
    m._templates["poll"]["config"]["last_winners"] = [{"id": "", "name": "Old", "keyword": "", "votes": 1}]
    m.start_poll("poll")
    assert m.stop_poll("poll") == (True, "Poll ended with no votes.")
    assert m.get_config("poll")["last_winners"][0]["name"] == "Old"
    assert env.banners == [] and env.chat == []


def test_stop_tie_returns_both(env, clock):
    m = _ready_poll(env)
    m.start_poll("poll")
    _vote(m, "u1", "#Yes")
    _vote(m, "u2", "#No")
    assert m.stop_poll("poll") == (True, "Winner: Yes, No")


def test_stop_twitch_poll_ends_on_twitch(env, clock):
    m = _ready_poll(env, twitch_poll=True, twitch_title="Q?", duration_seconds=60)
    env.twitch_create = (True, {"id": "tp1", "choices": []})
    m.start_poll("poll")
    m.stop_poll("poll")
    assert env.twitch_ended == ["tp1"]


# --------------------------------------------------------------------------- votes


def test_votes_ignored_without_hash_or_open_poll(env, clock):
    m = _ready_poll(env)
    assert _vote(m, "u", "yes") is False
    assert _vote(m, "u", "#Yes") is False  # not open
    assert m.try_register_vote(None) is False


def test_vote_schedules_writer_and_emits(env, clock):
    m = _ready_poll(env)
    m.start_poll("poll")
    env.events.clear()
    assert _vote(m, "u", "#Maybe") is False
    assert _vote(m, "u", "go #No") is True
    assert env.events[-1][0] == "poll_state"
    assert FakeThread.created and FakeThread.created[-1].started
    m.flush()
    assert env.last()["templates"]["poll"]["runtime"]["voters"] == {"twitch:u": _cat_ids(m)[1]}


def test_apply_vote_rules(env):
    m = _manager(env, persist=False)
    cfg = {"one_entry_per_user": True, "allow_vote_switch": False}
    rt = {}
    assert m._apply_vote_locked(cfg, rt, "", "a") is False
    assert m._apply_vote_locked(cfg, rt, "u", "a") is True
    assert m._apply_vote_locked(cfg, rt, "u", "b") is False
    cfg["allow_vote_switch"] = True
    assert m._apply_vote_locked(cfg, rt, "u", "a") is False  # same choice
    assert m._apply_vote_locked(cfg, rt, "u", "b") is True
    assert rt["votes"] == {"a": 0, "b": 1}
    # switching away from a zeroed count does not go negative
    rt["votes"]["b"] = 0
    assert m._apply_vote_locked(cfg, rt, "u", "a") is True
    assert rt["votes"] == {"a": 1, "b": 0}
    multi = {"one_entry_per_user": False}
    rt2 = {}
    assert m._apply_vote_locked(multi, rt2, "", "a") is True
    assert m._apply_vote_locked(multi, rt2, "", "a") is True
    assert rt2["votes"] == {"a": 2}


def test_vote_counts_in_every_open_poll(env, clock):
    m = _ready_poll(env)
    for n in ("Yes", "Maybe"):
        m.add_category("two", n)
    m.start_poll("poll")
    m.start_poll("two")
    assert _vote(m, "u", "#Yes") is True
    assert m.get_overlay_state("poll")["total_votes"] == 1
    assert m.get_overlay_state("two")["total_votes"] == 1


# --------------------------------------------------------------------------- moderator start


def test_moderator_start(env, clock):
    m = _ready_poll(env, mods_can_start=True, start_command="startpoll")
    m.add_category("plain", "A")  # mods can't start this one
    m.add_category("nocmd", "A")
    m.update_config("nocmd", {"mods_can_start": True})
    msg = {"message": "!startpoll", "badges": "moderator/1"}
    assert m.try_moderator_start({"message": "!startpoll"}) is False  # not a mod
    assert m.try_moderator_start({"message": "startpoll", "is_moderator": True}) is False
    assert m.try_moderator_start(msg) is True
    assert m.get_overlay_state("poll")["status"] == "open"
    assert m.try_moderator_start(msg) is False  # already open


def test_moderator_start_failure_notifies(env, clock):
    m = _manager(env)
    m.add_category("p", "A")
    m.update_config("p", {"mods_can_start": True, "start_command": "!go"})
    assert m.try_moderator_start({"message": "!go", "is_broadcaster": True}) is False
    kw = env.notices[-1][1]
    assert kw["title"] == "Couldn't start the poll from chat"
    assert kw["dedupe_key"] == "poll:chat_start:p"


def test_moderator_start_twitch_poll_runs_in_thread(env, clock):
    m = _ready_poll(env, mods_can_start=True, start_command="!go", twitch_poll=True, twitch_title="Q?",
                    duration_seconds=60)
    assert m.try_moderator_start({"message": "!go now", "is_moderator": True}) is True
    thread = [t for t in FakeThread.created if t.name == "PollChatStart"][-1]
    assert thread.started
    assert m.get_overlay_state("poll")["status"] == "idle"
    thread.run_now()
    assert m.get_overlay_state("poll")["status"] == "open"


# --------------------------------------------------------------------------- Twitch events


def _twitch_poll(env, clock, duration=60):
    m = _ready_poll(env, twitch_poll=True, twitch_title="Q?", duration_seconds=duration)
    env.twitch_create = (True, {"id": "tp1", "choices": [{"id": "c1", "title": "Yes"}, {"id": "c2", "title": "No"}]})
    m.start_poll("poll")
    return m


def test_template_for_twitch_poll(env, clock):
    m = _twitch_poll(env, clock)
    with m._lock:
        assert m._template_for_twitch_poll_locked("tp1") == "poll"
        assert m._template_for_twitch_poll_locked("") == ""
        assert m._template_for_twitch_poll_locked("zz") == ""


def test_twitch_poll_began(env, clock):
    m = _twitch_poll(env, clock)
    assert m.twitch_poll_began("zz", 2000) is False
    assert m.twitch_poll_began("tp1", None) is False
    assert m.twitch_poll_began("tp1", 1100) is True
    assert m._templates["poll"]["runtime"]["ends_at"] == 1100.0
    assert m._timers["poll"].delay == 100 + pm._TWITCH_END_GRACE_SECONDS
    assert env.events[-1][0] == "poll_state"


def test_apply_twitch_votes(env, clock):
    m = _twitch_poll(env, clock)
    assert m.apply_twitch_votes("zz", {"c1": 1}) is False
    assert m.apply_twitch_votes("tp1", {"c1": 3, "unknown": 9}) is True
    assert m.apply_twitch_votes("tp1", {"c1": 3}) is False  # unchanged
    assert m.get_overlay_state("poll")["options"][0]["votes"] == 3


def test_twitch_poll_ended_closes_open_poll(env, clock):
    m = _twitch_poll(env, clock)
    assert m.twitch_poll_ended("zz", {}) is False
    assert m.twitch_poll_ended("tp1", {"c2": 4}, "COMPLETED") is True
    state = m.get_overlay_state("poll")
    assert state["status"] == "ended"
    assert [w["name"] for w in state["winners"]] == ["No"]
    assert env.twitch_ended == []  # ended by Twitch: no end call back


def test_twitch_poll_ended_after_local_end_keeps_final_counts(env, clock):
    m = _twitch_poll(env, clock)
    m.stop_poll("poll")
    env.events.clear()
    assert m.twitch_poll_ended("tp1", {"c1": 5, "c2": 2}) is True
    cfg = m.get_config("poll")
    assert [c["votes"] for c in cfg["categories"]] == [5, 2]
    assert cfg["last_winners"][0]["name"] == "Yes"
    assert [e for e, _ in env.events] == ["poll_state"]
    assert env.events[0][1]["winners"][0]["votes"] == 5


# --------------------------------------------------------------------------- snapshots


def test_empty_snapshot(env, clock):
    m = _manager(env)
    snap = m.get_overlay_state("ghost")
    assert snap["status"] == "idle" and snap["options"] == [] and snap["server_now"] == 1000.0


def test_snapshot_ranks_and_percent(env, clock):
    m = _ready_poll(env, names=("A", "B", "C"), duration_seconds=100)
    m.start_poll("poll")
    _vote(m, "u1", "#B")
    _vote(m, "u2", "#B")
    _vote(m, "u3", "#C")
    clock["now"] = 1040.4
    snap = m.get_overlay_state("poll")
    assert [o["name"] for o in snap["options"]] == ["B", "C", "A"]
    assert [o["percent"] for o in snap["options"]] == [66.7, 33.3, 0]
    assert snap["remaining_seconds"] == 60
    assert snap["permanent"] is False and snap["ends_at"] == 1100.0


def test_snapshot_ended_uses_category_fallback_and_missing_entry(env, clock):
    m = _ready_poll(env)
    a, b = _cat_ids(m)
    m._templates["poll"]["runtime"] = {**pm._idle_runtime(), "status": "ended", "votes": {}, "winners": [a]}
    m._templates["poll"]["config"]["categories"][0]["votes"] = 4
    with m._lock:
        snap = m._snapshot_locked("poll")
        ghost = m._snapshot_locked("ghost", 5.0)
    assert snap["options"][0]["votes"] == 4 and snap["winners"][0]["id"] == a
    assert ghost["options"] == [] and ghost["status"] == "idle"


# --------------------------------------------------------------------------- announce / stats / emit


def test_announce_paths(env, monkeypatch):
    m = _ready_poll(env, announce_winners_as_banner=True, announce_winners_in_chat=True)
    m._announce("poll", {"winners": []})
    assert env.banners == []
    monkeypatch.setattr(m, "_banner_text", lambda text, winners: "")
    m._announce("poll", {"winners": [{"name": "A"}]})
    assert env.banners == []


def test_announce_banner_failure_and_chat_failure(env):
    m = _ready_poll(env, announce_winners_as_banner=True, announce_winners_in_chat=True)
    env.banner_raises = True
    env.chat_raises = True
    m._announce("poll", {"winners": [{"name": "A", "keyword": "#a", "votes": 1}]})
    kw = env.notices[-1][1]
    assert kw["dedupe_key"] == "poll:banner_failed"
    assert "RuntimeError: no overlay" == kw["details"]


def test_announce_bad_banner_duration(env, monkeypatch):
    m = _ready_poll(env, announce_winners_as_banner=True)
    real = m.get_config
    monkeypatch.setattr(m, "get_config", lambda t: {**real(t), "banner_duration": "bad"})
    m._announce("poll", {"winners": [{"name": "A", "keyword": "#a", "votes": 1}]})
    assert env.banners[-1][1] == 10


def test_record_stats(env):
    m = _manager(env)
    m._record_stats({"options": [1], "winners": [2]})
    assert env.stats == [([1], [2])]
    env.stats_raises = True
    m._record_stats({})
    off = _manager(env, persist=False)
    off._record_stats({"options": [9]})
    assert len(env.stats) == 1


def test_emit_without_engine_and_errors(env, monkeypatch):
    m = _manager(env)
    monkeypatch.setattr(web_engine, "web_engine_instance", None, raising=False)
    m._emit("x", {})

    def boom(*_a):
        raise RuntimeError("io")

    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(safe_emit=boom), raising=False)
    m._emit("x", {})
    assert env.events == []


def test_real_now_is_wall_clock(env):
    import time

    m = _manager(env, persist=False)
    assert abs(m._now() - time.time()) < 5
