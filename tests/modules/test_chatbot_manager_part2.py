# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/chatbot_manager.py (part 2): chat/event processing, timers, test runs, quotes, greetings."""

from __future__ import annotations

import sys
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _chatbot_manager_fakes import _cmd, _evt, env  # noqa: E402,F401  (env: fixture)
from modules import chatbot_core as core  # noqa: E402
from modules import chatbot_manager as cm  # noqa: E402
from modules.chatbot_core import ChatEvent, EventType, Greeting, Quote  # noqa: E402


@pytest.fixture
def m(env, monkeypatch):
    monkeypatch.setattr(core, "_broadcaster_user_id", lambda: "")
    return env.make()


@pytest.fixture
def lookups(monkeypatch):
    from modules import chatbot_lookups

    calls = []
    result = {"value": {"lookup_ok": True, "temp": "20C"}}

    def run(lookup, ctx, kind):
        calls.append((dict(lookup), kind, ctx.get("username")))
        if isinstance(result["value"], Exception):
            raise result["value"]
        return result["value"]

    monkeypatch.setattr(chatbot_lookups, "run_lookup_blocking", run)
    return SimpleNamespace(calls=calls, result=result)


@pytest.fixture
def chat(monkeypatch):
    from modules import chatbot

    sent = []
    state = {"connected": True, "deliver": True}

    def deliver(response, targets, discord_channels=None, output_mode="chat"):
        sent.append((response, list(targets), list(discord_channels or []), output_mode))
        if isinstance(state["deliver"], Exception):
            raise state["deliver"]
        return state["deliver"]

    monkeypatch.setattr(chatbot, "deliver_chatbot_output", deliver)
    monkeypatch.setattr(chatbot, "is_chatbot_connected", lambda: state["connected"])
    return SimpleNamespace(sent=sent, state=state)


MOD = {"username": "Mod", "badges": "moderator/1"}


# --------------------------------------------------------------------------- process_chat_message


def test_non_commands_return_none(m):
    assert m.process_chat_message({"message": "hello"}) is None
    assert m.process_chat_message({"message": "!"}) is None
    assert m.process_chat_message({"message": None}) is None
    assert m.process_chat_message({"message": "!unknown"}) is None


def test_matching_command_renders_reply_and_records_use(m, env):
    m.commands["c"] = _cmd("c", "hello", aliases=["hi"], response_text="Hi {username}: {command_message}",
                          reply_targets=["twitch", "youtube"], discord_channels=["d"], output_mode="banner")
    data = {"message": "!HI there you", "username": "Bob", "userid": 42}
    out = m.process_chat_message(data)
    assert out == ("Hi Bob: there you", "hi", ["twitch", "youtube"], ["d"], "banner")
    assert data["command_args"] == ["there", "you"] and data["command_words"] == ["there", "you"]
    assert data["user_id"] == "42"
    assert m.commands["c"].usage_count == 1 and m._save_dirty
    assert env.stats.calls[-1][0] == "command"


def test_disabled_and_condition_failing_commands_do_not_match(m):
    m.commands["c"] = _cmd("c", "x", enabled=False)
    assert m.process_chat_message({"message": "!x"}) is None
    m.commands["c"] = _cmd("c", "x", conditions=[{"field": "username", "operator": "equal", "value": "bob"}])
    assert m.process_chat_message({"message": "!x", "username": "amy"}) is None


def test_blocked_command_is_silent(m):
    m.commands["c"] = _cmd("c", "x", permission="moderator")
    assert m.process_chat_message({"message": "!x", "username": "u"}) == ("", "x", [], [], "chat")


def test_empty_reply_is_silent(m):
    m.commands["c"] = _cmd("c", "x", response_text="   ")
    assert m.process_chat_message({"message": "!x"}) == ("", "x", [], [], "chat")


def test_reset_command_resets_target(m):
    m.commands["t"] = _cmd("t", "deaths", command_type="counter", counter_value=7)
    m.commands["r"] = _cmd("r", "resetdeaths", command_type="reset", reset_command="!deaths",
                           response_text="{target_command} was {target_count}")
    out = m.process_chat_message({"message": "!resetdeaths", "username": "u"})
    assert out[0] == "deaths was 7"
    assert m.commands["t"].counter_value == 0


def test_reset_command_unknown_target_is_silent(m):
    m.commands["r"] = _cmd("r", "reset", command_type="reset", reset_command="nothing")
    assert m.process_chat_message({"message": "!reset"}) == ("", "reset", [], [], "chat")


def test_lookup_command(m, lookups):
    m.commands["w"] = _cmd("w", "weather", lookup={"id": "weather"}, response_text="It is {temp}",
                           lookup_fallback="No weather")
    assert m.process_chat_message({"message": "!weather", "username": "Al"})[0] == "It is 20C"
    assert lookups.calls == [({"id": "weather"}, "command", "Al")]
    lookups.result["value"] = {"lookup_ok": False}
    assert m.process_chat_message({"message": "!weather"})[0] == "No weather"
    m.commands["w"].lookup_fallback = ""
    assert m.process_chat_message({"message": "!weather"}) == ("", "weather", [], [], "chat")


def test_process_chat_message_errors_return_none(m, caplog):
    assert m.process_chat_message(None) is None
    assert "Error processing chat message" in caplog.text


def test_quote_commands_route_only_when_quotes_enabled(m, monkeypatch):
    seen = []
    monkeypatch.setattr(m, "_handle_quote_command", lambda args, data: seen.append(("q", args)) or ("Q", "quote"))
    monkeypatch.setattr(m, "_handle_add_quote_command", lambda args, data: seen.append(("a", args)) or ("A", "add_quote"))
    assert m.process_chat_message({"message": "!quote 3"}) == ("Q", "quote")
    assert m.process_chat_message({"message": "!add_quote hi"}) == ("A", "add_quote")
    assert seen == [("q", ["3"]), ("a", ["hi"])]
    m.quotes_enabled = False
    m.commands["c"] = _cmd("c", "quote", response_text="custom")
    assert m.process_chat_message({"message": "!quote"})[0] == "custom"


# --------------------------------------------------------------------------- !quote


def _quote(m, n, text, author="A", qid=None):
    q = Quote(quote_id=qid or f"q{n}", quote_number=n, text=text, author=author, date_added=0)
    m.quotes[q.quote_id] = q
    return q


def test_quote_random_number_and_search(m, env, monkeypatch):
    m.quote_cooldown_seconds = 0
    _quote(m, 1, "apples are red")
    _quote(m, 2, "bananas are yellow", author="")
    monkeypatch.setattr("random.choice", lambda items: items[0])
    assert m._handle_quote_command([], {"username": "u"}) == ('#1: "apples are red" — A', "quote")
    assert m._handle_quote_command(["2"], {"username": "u"}) == ('#2: "bananas are yellow"', "quote")
    assert m._handle_quote_command(["9"], {}) == ("Quote #9 not found!", "quote")
    assert m._handle_quote_command(["banana"], {})[0] == '#2: "bananas are yellow"'
    assert m._handle_quote_command(["kiwi", "fruit"], {}) == ("No quotes found matching 'kiwi fruit'", "quote")
    redeemed = [c for c in env.stats.calls if c[0] == "quote"]
    assert redeemed == [("quote", "q1", "u"), ("quote", "q2", "u"), ("quote", "q2", "Unknown")]


def test_quote_without_quotes(m):
    m.quote_cooldown_seconds = 0
    assert m._handle_quote_command([], {}) == ("No quotes available yet! Add some with !add_quote", "quote")


def test_quote_cooldown_blocks_silently(m, monkeypatch):
    monkeypatch.setattr(cm.time, "time", lambda: 100.0)
    m.quote_cooldown_seconds = 30
    m.quote_last_used = 90.0
    assert m._handle_quote_command([], {}) == ("", "quote", [], [], "chat")
    m.quote_last_used = 50.0
    _quote(m, 1, "x")
    assert m._handle_quote_command(["1"], {})[0].startswith("#1")
    assert m.quote_last_used == 100.0


def test_quote_stats_errors_are_logged(m, monkeypatch, caplog):
    m.quote_cooldown_seconds = 0
    _quote(m, 1, "pears are green")

    def broken():
        raise RuntimeError("stats down")

    monkeypatch.setattr(cm, "get_statistics_manager", broken)
    assert m._handle_quote_command([], {})[0].startswith("#1")
    assert m._handle_quote_command(["1"], {})[0].startswith("#1")
    assert m._handle_quote_command(["pears"], {})[0].startswith("#1")
    assert caplog.text.count("Error tracking quote redemption statistics") == 3


def test_quote_handler_unexpected_error(m, monkeypatch):
    m.quote_cooldown_seconds = 0

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(m, "get_random_quote", boom)
    assert m._handle_quote_command([], {}) == ("Error processing quote command", "quote")


# --------------------------------------------------------------------------- !add_quote


def test_add_quote_permission_and_usage(m):
    assert m._handle_add_quote_command(["x"], {"username": "pleb"}) == ("", "add_quote", [], [], "chat")
    assert m._handle_add_quote_command([], MOD) == ('Usage: !add_quote "quote text" [author]', "add_quote")


@pytest.mark.parametrize("args,text,author", [
    (['"Hello', 'world"', "Bob", "Smith"], "Hello world", "Bob Smith"),
    (['"Just', 'text"'], "Just text", ""),
    (['"One"', '"Two"'], "One", ""),
    (["plain", "words"], "plain words", ""),
    (['"unterminated', "quote"], '"unterminated quote', ""),
])
def test_add_quote_parsing(m, args, text, author):
    out = m._handle_add_quote_command(args, MOD)
    assert out == ("Quote #1 added successfully!", "add_quote")
    (q,) = m.quotes.values()
    assert (q.text, q.author, q.added_by) == (text, author, "Mod")


def test_add_quote_empty_text(m):
    assert m._handle_add_quote_command(['""'], MOD) == ("Quote text cannot be empty!", "add_quote")


def test_add_quote_failure_and_error(m, monkeypatch):
    monkeypatch.setattr(m, "add_quote", lambda *a: (False, "nope", 0))
    assert m._handle_add_quote_command(["x"], MOD) == ("Error adding quote: nope", "add_quote")

    def boom(*a):
        raise RuntimeError("x")

    monkeypatch.setattr(m, "add_quote", boom)
    assert m._handle_add_quote_command(["x"], MOD) == ("Error processing add_quote command", "add_quote")


def test_add_quote_permission_setting(m):
    m.add_quote_permission = "everyone"
    assert m._handle_add_quote_command(["anyone", "can"], {"username": "pleb"})[0] == "Quote #1 added successfully!"


# --------------------------------------------------------------------------- process_event


def test_process_event_matches(m, env):
    m.events = {
        "a": _evt("a", "follow", response_text="A {username}", reply_targets=["youtube"], discord_channels=["d"]),
        "b": _evt("b", "raid"),
        "t": _evt("t", "interval", interval=5),
        "off": _evt("off", "follow", enabled=False),
        "blank": _evt("blank", "follow", response_text=" "),
    }
    out = m.process_event(EventType.FOLLOW, {"username": "Z"})
    assert out == ("A Z", ["youtube"], ["d"], "chat")
    assert m.events["a"].trigger_count == 1 and m.events["blank"].trigger_count == 1
    assert m.events["off"].trigger_count == 0 and m._save_dirty
    assert m.process_event(EventType.INTERVAL, {}) is None
    assert m.process_event(EventType.BITS, {}) is None


def test_process_event_multiple_matches_return_list(m):
    m.events = {"a": _evt("a", "raid", response_text="one"), "b": _evt("b", "raid", response_text="two")}
    out = m.process_event(EventType.RAID, {})
    assert isinstance(out, list) and [r[0] for r in out] == ["one", "two"]


def test_process_event_lookup_runs_in_background(m, env, lookups, chat):
    m.events = {"l": _evt("l", "follow", lookup={"id": "follow_age"}, response_text="Age {temp}")}
    assert m.process_event(EventType.FOLLOW, {"username": "Q"}) is None
    worker = env.threads[-1]
    assert worker.target == m._lookup_and_deliver_event and worker.started and worker.name == "ChatbotEventLookup-l"
    worker.run()
    assert chat.sent == [("Age 20C", ["twitch"], [], "chat")]


def test_process_event_errors_return_none(m, caplog):
    class Bad:
        def values(self):
            raise RuntimeError("broken")

    m.events = Bad()
    assert m.process_event(EventType.FOLLOW, {}) is None
    assert "Error processing event" in caplog.text


def test_lookup_and_deliver_event_silent_and_errors(m, lookups, chat, caplog):
    e = _evt("l", "follow", lookup={"id": "x"})
    lookups.result["value"] = {"lookup_ok": False}
    m._lookup_and_deliver_event(e, {})
    assert chat.sent == []
    lookups.result["value"] = RuntimeError("api down")
    m._lookup_and_deliver_event(e, {})
    assert "lookup/delivery failed" in caplog.text


# --------------------------------------------------------------------------- scheduling


def test_schedule_event_routes_by_type(m, env, monkeypatch):
    started = []
    monkeypatch.setattr(m, "_start_interval_thread", lambda e: started.append(("interval", e.event_id)))
    monkeypatch.setattr(m, "_start_specific_time_event", lambda e: started.append(("time", e.event_id)))
    for e in (_evt("i", "interval", interval=5), _evt("t", "specific_time", specific_time="10:00"),
              _evt("bad", "specific_time", specific_time="nope"), _evt("f", "follow"),
              _evt("off", "interval", interval=5, enabled=False)):
        m.events[e.event_id] = e
        m._schedule_event(e)
    stranger = _evt("x", "interval", interval=5)
    m._schedule_event(stranger)  # not stored in the manager: no timer
    assert started == [("interval", "i"), ("time", "t")]


def test_unschedule_sets_stop_flag(m):
    stop = threading.Event()
    m._repeating_stops["e"] = stop
    m.repeating_tasks["e"] = object()
    m._unschedule_event("e")
    assert stop.is_set() and "e" not in m.repeating_tasks
    m._unschedule_event("missing")  # no error


def test_timer_still_valid(m):
    e = _evt("e", "interval", interval=5)
    m.events["e"] = e
    stop = threading.Event()
    assert m._timer_still_valid(e, stop)
    e.enabled = False
    assert not m._timer_still_valid(e, stop)
    e.enabled = True
    stop.set()
    assert not m._timer_still_valid(e, stop)
    assert not m._timer_still_valid(_evt("other"), threading.Event())


def _start_interval(m, env, event):
    m.events[event.event_id] = event
    m._start_interval_thread(event)
    thread = env.threads[-1]
    stop = m._repeating_stops[event.event_id]
    return thread, stop


def test_interval_thread_fires_after_each_interval(m, env, monkeypatch):
    e = _evt("e", "interval", interval=5)
    thread, stop = _start_interval(m, env, e)
    assert thread.started and thread.name == "Event-e" and m.repeating_tasks["e"] is thread
    waits = []

    def wait(seconds):
        waits.append(seconds)
        return len(waits) >= 3  # two timeouts, then stopped

    monkeypatch.setattr(stop, "wait", wait)
    fired = []
    monkeypatch.setattr(m, "_fire_timer_event", lambda ev, data: fired.append(data["source"]))
    thread.run()
    assert waits == [5.0, 5.0, 5.0] and fired == ["interval", "interval"]


def test_interval_thread_does_not_restart_running_thread(m, env):
    e = _evt("e", "interval", interval=5)
    thread, _stop = _start_interval(m, env, e)
    thread.alive = True
    count = len(env.threads)
    m._start_interval_thread(e)
    m._start_specific_time_event(e)
    assert len(env.threads) == count


def test_interval_thread_exits_when_invalid_or_zero(m, env, monkeypatch):
    e = _evt("e", "interval", interval=5)
    thread, stop = _start_interval(m, env, e)
    monkeypatch.setattr(stop, "wait", lambda s: False)
    e.enabled = False
    fired = []
    monkeypatch.setattr(m, "_fire_timer_event", lambda ev, data: fired.append(1))
    thread.run()
    e.enabled = True
    e.interval = 0
    thread.run()
    assert fired == []


def test_interval_thread_error_notifies(m, env, monkeypatch):
    e = _evt("e", "interval", interval=5)
    thread, stop = _start_interval(m, env, e)
    monkeypatch.setattr(stop, "wait", lambda s: False)

    def boom(ev, data):
        raise RuntimeError("chat broke")

    monkeypatch.setattr(m, "_fire_timer_event", boom)
    thread.run()
    assert env.notify.titles == ["A chatbot timer stopped"]
    assert env.notify.calls[0][1]["dedupe_key"] == "chatbot:timer:e"


def test_backwards_compatible_timer_names(m, monkeypatch):
    started, stopped = [], []
    monkeypatch.setattr(m, "_start_interval_thread", lambda e: started.append(e.event_id))
    monkeypatch.setattr(m, "_unschedule_event", lambda eid: stopped.append(eid))
    m._start_repeating_event(_evt("a", "interval", interval=1))
    m._start_repeating_event(_evt("b", "follow"))
    m._start_repeating_event_with_thread(_evt("c", "interval", interval=1), 5)
    m._stop_repeating_event("a")
    assert started == ["a", "c"] and stopped == ["a"]


@pytest.mark.parametrize("specific,now,last_key,last_triggered,expected", [
    ("10:00", datetime(2024, 1, 1, 10, 0, 30), None, 0, True),
    ("10:00", datetime(2024, 1, 1, 10, 1), None, 0, False),
    ("10:00", datetime(2024, 1, 1, 10, 0), "2024-01-01 10:00", 0, False),
    ("bad", datetime(2024, 1, 1, 10, 0), None, 0, False),
    ("10:00", datetime(2024, 1, 1, 10, 0), None, datetime(2024, 1, 1, 10, 0, 5).timestamp(), False),
    ("10:00", datetime(2024, 1, 1, 10, 0), None, datetime(2023, 1, 1, 10, 0).timestamp(), True),
    ("10:00", datetime(2024, 1, 1, 10, 0), None, 1e30, True),  # unreadable timestamp
])
def test_specific_time_due(specific, now, last_key, last_triggered, expected):
    e = _evt("e", "specific_time", specific_time=specific, last_triggered=last_triggered)
    due, key = cm.ChatbotManager._specific_time_due(e, now, last_key)
    assert due is expected and key == now.strftime("%Y-%m-%d %H:%M")


def test_specific_time_thread_fires_once_per_minute(m, env, monkeypatch):
    e = _evt("t", "specific_time", specific_time="10:00")
    m.events["t"] = e
    m._start_specific_time_event(e)
    thread = env.threads[-1]
    assert thread.name == "SpecificTime-t"
    stop = m._repeating_stops["t"]
    waits = []

    def wait(seconds):
        waits.append(seconds)
        if len(waits) == 3:
            stop.set()

    monkeypatch.setattr(stop, "wait", wait)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2024, 1, 1, 10, 0, 1)

    monkeypatch.setattr(cm, "datetime", Clock)
    fired = []
    monkeypatch.setattr(m, "_fire_timer_event", lambda ev, data: fired.append(data["scheduled_time"]))
    thread.run()
    assert fired == ["10:00"] and waits == [1.0, 1.0, 1.0]


def test_specific_time_thread_stops_when_type_changes_or_errors(m, env, monkeypatch):
    e = _evt("t", "specific_time", specific_time="10:00")
    m.events["t"] = e
    m._start_specific_time_event(e)
    thread = env.threads[-1]
    e.event_type = EventType.FOLLOW
    thread.run()  # leaves at once
    e.event_type = EventType.SPECIFIC_TIME

    def boom(*a):
        raise RuntimeError("clock")

    monkeypatch.setattr(m, "_specific_time_due", boom)
    thread.run()
    assert env.notify.titles == ["A chatbot timer stopped"]


def test_fire_timer_event_skips_when_chat_offline(m, chat):
    chat.state["connected"] = False
    e = _evt("e", "interval", interval=5)
    assert m._fire_timer_event(e, {}) is False and e.trigger_count == 0
    # Banner output does not need chat.
    e.output_mode = "banner"
    e.reply_targets = []
    assert m._fire_timer_event(e, {}) is True
    assert chat.sent[-1][3] == "banner"


def test_fire_timer_event_posts_and_handles_results(m, chat, lookups):
    e = _evt("e", "interval", interval=5, response_text="Drink {username}")
    assert m._fire_timer_event(e, {"username": "System"}) is True
    assert chat.sent[-1][0] == "Drink System" and e.trigger_count == 1
    chat.state["deliver"] = False
    assert m._fire_timer_event(e, {}) is False
    chat.state["deliver"] = RuntimeError("socket")
    assert m._fire_timer_event(e, {}) is False
    blank = _evt("b", "interval", interval=5, response_text="")
    assert m._fire_timer_event(blank, {}) is False


def test_fire_timer_event_with_lookup(m, chat, lookups):
    chat.state["deliver"] = True
    e = _evt("e", "interval", interval=5, lookup={"id": "uptime"}, response_text="Up {temp}")
    assert m._fire_timer_event(e, {"username": "System"}) is True
    assert chat.sent[-1][0] == "Up 20C" and lookups.calls[-1][1] == "event"


def test_start_deferred_interval_events(m, env, monkeypatch, caplog):
    scheduled = []

    def schedule(e):
        scheduled.append(e.event_id)
        if e.event_id == "err":
            raise RuntimeError("no")

    monkeypatch.setattr(m, "_schedule_event", schedule)
    alive = SimpleNamespace(is_alive=lambda: True)
    m.events = {"a": _evt("a", "interval", interval=5), "off": _evt("off", enabled=False),
                "run": _evt("run", "interval", interval=5), "err": _evt("err")}
    m.repeating_tasks = {"run": alive}
    m._deferred_interval_events = [1]
    m.start_deferred_interval_events()
    assert scheduled == ["a", "err"] and m._deferred_interval_events == []
    assert "Error starting timer for event err" in caplog.text


# --------------------------------------------------------------------------- test runs


def test_test_lookup_vars(m, lookups, monkeypatch):
    from modules import chatbot_variables

    monkeypatch.setattr(chatbot_variables, "example_lookup_variables", lambda lookup: {"example": lookup["id"]})
    plain = _cmd("p")
    assert m._test_lookup_vars(plain, {}, "command", True) == (None, None)
    read = _cmd("r", lookup={"id": "follow_age"})
    assert m._test_lookup_vars(read, {}, "command", True) == ({"lookup_ok": True, "temp": "20C"}, "live")
    assert m._test_lookup_vars(read, {}, "command", False) == ({"example": "follow_age"}, "example")
    write = _cmd("w", lookup={"id": "set_title"})
    assert m._test_lookup_vars(write, {}, "command", True) == ({"example": "set_title"}, "example")
    adv_get = _cmd("a", lookup={"id": "advanced", "method": "get"})
    assert m._test_lookup_vars(adv_get, {}, "command", True)[1] == "live"
    adv_post = _cmd("a", lookup={"id": "advanced", "method": "POST"})
    assert m._test_lookup_vars(adv_post, {}, "command", True)[1] == "example"


def test_test_command_paths(m, chat):
    assert m.test_command("missing")["reason"] == "Command not found"
    m.commands["c"] = _cmd("c", "hi", response_text="Hello {username}", usage_limit=1, usage_count=1)
    r = m.test_command("c")
    assert r["ok"] and r["triggered"] and r["response"] == "Hello TestViewer" and r["lookup"] is None
    assert m.commands["c"].usage_count == 1  # the stored command is untouched
    assert chat.sent == []
    r = m.test_command("c", {"username": "Zed"}, send=True)
    assert r["response"] == "Hello Zed" and chat.sent[-1][0] == "Hello Zed"


def test_test_command_not_triggered(m):
    m.commands["off"] = _cmd("off", enabled=False)
    assert m.test_command("off")["reason"] == "Command is disabled"
    m.commands["cond"] = _cmd("cond", conditions=[{"field": "username", "operator": "equal", "value": "nobody"}])
    assert m.test_command("cond")["reason"] == "Conditions not met"
    m.commands["mod"] = _cmd("mod", permission="moderator")
    r = m.test_command("mod", {"badges": "", "user_id": "1", "userid": "1"})
    assert r["reason"] == "Only moderator can use this command" and r["triggered"] is False


def test_test_command_reset_and_lookup(m, monkeypatch):
    m.commands["t"] = _cmd("t", "deaths", command_type="counter", counter_value=3)
    m.commands["r"] = _cmd("r", "rd", command_type="reset", reset_command="deaths",
                           response_text="{target_command}:{target_count}")
    assert m.test_command("r")["response"] == "deaths:3"
    assert m.commands["t"].counter_value == 3
    m.commands["r2"] = _cmd("r2", "rx", command_type="reset", reset_command="ghost",
                            response_text="{target_command}:{target_count}")
    assert m.test_command("r2")["response"] == "ghost:0"
    m.commands["l"] = _cmd("l", "lk", lookup={"id": "x"}, response_text="v={temp}")
    monkeypatch.setattr(m, "_test_lookup_vars", lambda item, data, kind, live: ({"lookup_ok": False}, "example"))
    r = m.test_command("l")
    assert r["response"] == "" and r["reason"] == "Lookup failed; nothing would be posted" and r["lookup"] == "example"


def test_test_command_error(m, monkeypatch):
    m.commands["c"] = _cmd("c")
    from modules import chatbot_variables

    def boom(*a, **k):
        raise RuntimeError("sample broke")

    monkeypatch.setattr(chatbot_variables, "sample_data", boom)
    r = m.test_command("c")
    assert r["ok"] is False and r["error"] == "sample broke"
    m.events["e"] = _evt("e")
    assert m.test_event("e")["error"] == "sample broke"


def test_test_event_paths(m, chat, monkeypatch):
    assert m.test_event("missing")["reason"] == "Event not found"
    m.events["e"] = _evt("e", "follow", enabled=False, response_text="Thanks {username}")
    r = m.test_event("e")
    assert r["triggered"] and r["response"] == "Thanks TestViewer"
    assert m.events["e"].trigger_count == 0
    m.test_event("e", {"username": "Y"}, send=True)
    assert chat.sent[-1][0] == "Thanks Y"
    m.events["f"] = _evt("f", "bits", bits_quantity=100)
    assert m.test_event("f", {"amount": 1})["reason"] == "This sample does not pass the event's filters"
    m.events["l"] = _evt("l", "follow", lookup={"id": "x"}, response_text="ok {temp}")
    monkeypatch.setattr(m, "_test_lookup_vars", lambda item, data, kind, live: ({"lookup_ok": True, "temp": 1}, "live"))
    r = m.test_event("l")
    assert r["response"] == "ok 1" and r["lookup"] == "live"
    monkeypatch.setattr(m, "_test_lookup_vars", lambda item, data, kind, live: ({"lookup_ok": False}, "live"))
    assert m.test_event("l")["reason"] == "Lookup failed; nothing would be posted"


def test_send_test_output(chat):
    item = _cmd("c", reply_targets=["youtube"], discord_channels=["d"], output_mode="banner")
    cm.ChatbotManager._send_test_output(item, "hi")
    assert chat.sent == [("hi", ["youtube"], ["d"], "banner")]


# --------------------------------------------------------------------------- quotes


def test_add_quote_numbers_and_validation(m, monkeypatch):
    monkeypatch.setattr(cm.time, "time", lambda: 50.0)
    assert m.add_quote("   ") == (False, "Quote text cannot be empty", 0)
    ok, err, n = m.add_quote("  first  ", " Ann ", "mod")
    assert (ok, err, n) == (True, "", 1)
    q = m.get_quote_by_number(1)
    assert (q.text, q.author, q.added_by, q.date_added) == ("first", "Ann", "mod", 50.0)
    m.quotes[q.quote_id].quote_number = 7
    assert m.add_quote("second")[2] == 8
    assert m.get_quote_by_number(99) is None
    assert m.get_quote_by_id(q.quote_id) is q
    assert len(m.get_all_quotes()) == 2 and m.get_all_quotes() is not m.quotes


def test_random_quote(m, monkeypatch):
    assert m.get_random_quote() is None
    q = _quote(m, 1, "only")
    assert m.get_random_quote() is q


def test_search_quotes_scoring(m):
    assert m.search_quotes("   ") == []
    a = _quote(m, 1, "cats rule", author="")
    b = _quote(m, 2, "all the cats are here", author="")
    c = _quote(m, 3, "bobcats", author="")
    assert m.search_quotes("CATS") == [a, b, c]
    assert m.search_quotes("cats", limit=1) == [a]
    assert m.search_quotes("dog") == []


def test_delete_quotes(m):
    q = _quote(m, 4, "x")
    assert m.delete_quote_by_number(9) is False
    assert m.delete_quote_by_number(4) and q.quote_id not in m.quotes
    assert m.delete_quote("missing") is False


@pytest.mark.parametrize("fmt,author,expected", [
    (cm.DEFAULT_QUOTE_REPLY_FORMAT, "Ann", '#3: "hi" — Ann'),
    (cm.DEFAULT_QUOTE_REPLY_FORMAT, "", '#3: "hi"'),
    ("{text} - {author}", " ", "hi"),
    ("{text} by {author}", "", "hi"),
    ("{author}{text}", "", "hi"),
    ("{quote_number}|{added_by}|{date_added}", "", "3|me|Jan 5, 2024"),
])
def test_format_quote_reply(m, fmt, author, expected):
    q = Quote(quote_number=3, text="hi", author=author, added_by="me", date_added=datetime(2024, 1, 5, 12).timestamp())
    assert m.format_quote_reply(q, fmt) == expected


def test_format_quote_reply_uses_setting_and_handles_missing_date(m):
    m.quote_reply_format = "{number}:{date_added}."
    assert m.format_quote_reply(Quote(quote_number=2, text="t", date_added=0)) == "2:."
    m.quote_reply_format = ""
    assert m.format_quote_reply(Quote(quote_number=2, text="t", author="a", date_added=0)) == '#2: "t" — a'


def test_renumber_and_gaps(m):
    assert m.quote_number_gaps() == []
    _quote(m, 2, "a", qid="a")
    _quote(m, 5, "b", qid="b")
    assert m.quote_number_gaps() == [1, 3, 4]
    m._save_dirty = False
    assert m.renumber_quotes() == 2
    assert sorted(q.quote_number for q in m.quotes.values()) == [1, 2] and m._save_dirty
    m._save_dirty = False
    assert m.renumber_quotes() == 0 and not m._save_dirty


def test_save_quote_settings(m):
    assert m.save_quote_settings(enabled=0, cooldown_seconds=-5, add_permission="vips", reply_format="  {text} ")
    assert (m.quotes_enabled, m.quote_cooldown_seconds, m.add_quote_permission, m.quote_reply_format) == \
        (False, 0, "vip", "{text}")
    m.save_quote_settings(enabled=True, cooldown_seconds=None, add_permission="x", reply_format=None)
    assert m.quote_cooldown_seconds == 0 and m.add_quote_permission == "everyone"
    assert m.quote_reply_format == cm.DEFAULT_QUOTE_REPLY_FORMAT


def test_update_quote(m):
    q = _quote(m, 1, "old", qid="a")
    _quote(m, 2, "other", qid="b")
    assert m.update_quote("zzz", text="x") is False
    assert m.update_quote("a", number=2) is False
    assert m.update_quote("a", number=0) is False
    assert m.update_quote("a", text="   ") is False
    assert m.update_quote("a", " new ", " Au ", number="3", added_by=" me ")
    assert (q.text, q.author, q.quote_number, q.added_by) == ("new", "Au", 3, "me")
    assert m.update_quote("a")  # nothing to change still saves


def test_quote_toggles_and_stats(m):
    assert m.toggle_quotes_enabled(False) and m.get_quotes_enabled() is False
    assert m.set_quote_cooldown(-1) and m.get_quote_cooldown() == 0
    assert m.set_quote_cooldown(12) and m.get_quote_cooldown() == 12
    s = m.get_quotes_statistics()
    assert s == {"total_quotes": 0, "quotes_enabled": False, "oldest_quote": None, "newest_quote": None}
    a = _quote(m, 1, "a", qid="a")
    a.date_added = 5
    b = _quote(m, 2, "b", qid="b")
    b.date_added = 9
    s = m.get_quotes_statistics()
    assert s["oldest_quote"] == 5 and s["newest_quote"] == 9 and s["total_quotes"] == 2


# --------------------------------------------------------------------------- greetings


def test_add_greeting_validation(m):
    assert m.add_greeting(" ", "a", "t") == (False, "User ID cannot be empty", "")
    assert m.add_greeting("1", " ", "t") == (False, "Username cannot be empty", "")
    assert m.add_greeting("1", "a", " ") == (False, "Greeting text cannot be empty", "")
    ok, err, gid = m.add_greeting("1", "Ann", "Hi", enabled=False)
    assert ok and err == "" and m.greetings[gid].enabled is False
    assert m.get_greeting(gid) is m.get_greeting_by_user_id("1")
    assert m.add_greeting("1", "Ann", "Again") == (False, "User Ann already has a greeting", "")
    assert len(m.get_all_greetings()) == 1


def test_update_rename_and_remove_greeting(m, monkeypatch):
    ok, _e, gid = m.add_greeting("1", "Ann", "Hi")
    assert m.update_greeting("nope") is False
    assert m.update_greeting(gid, "Yo", False)
    g = m.greetings[gid]
    assert g.greeting_text == "Yo" and g.enabled is False
    assert m.update_greeting(gid)  # nothing changed
    assert m.update_greeting_username("2", "x") is False
    assert m.update_greeting_username("1", "Annie") and g.username == "Annie"
    assert m.remove_greeting("nope") is False
    m.greetings_by_user_id.clear()  # out of sync index: still removed
    assert m.remove_greeting(gid) and m.greetings == {}
    ok, _e, gid = m.add_greeting("5", "Bo", "Hi")
    assert m.remove_greeting(gid) and "5" not in m.greetings_by_user_id


def test_process_greeting_custom_once_then_after_interval(m):
    ok, _e, gid = m.add_greeting("1", "Ann", "Welcome back {username}!")
    m.flag_manager.last_reset_time = 0
    m.greeting_reset_interval_hours = 10**6  # no global reset
    assert m.process_greeting("1", "AnnNew", current_time=1000.0) == "Welcome back AnnNew!"
    g = m.greetings[gid]
    assert g.greeted_flag and g.last_greeted == 1000.0 and g.username == "AnnNew"
    assert m.process_greeting("1", "AnnNew", current_time=2000.0) is None
    m.greeting_reset_interval_hours = 1
    m.flag_manager.last_reset_time = 10**9  # global reset not due
    assert m.process_greeting("1", "AnnNew", current_time=1000.0 + 3600) == "Welcome back AnnNew!"
    assert g.last_greeted == 4600.0


def test_process_greeting_custom_without_interval(m):
    m.add_greeting("1", "Ann", "Hi")
    m.greeting_reset_interval_hours = 0
    assert m.process_greeting("1", "Ann", 10.0) == "Hi"
    assert m.process_greeting("1", "Ann", 10.0**9) is None


def test_process_greeting_default_and_disabled(m, monkeypatch):
    monkeypatch.setattr(cm.time, "time", lambda: 100.0)
    m.flag_manager.last_reset_time = 100.0
    m.default_greeting_text = "Hey {username} ({user_login})"
    assert m.process_greeting("9", "Zed") == "Hey Zed (zed)"
    assert m.process_greeting("9", "Zed") is None
    m.default_greeting_enabled = False
    assert m.process_greeting("10", "New") is None
    # A switched-off personal greeting falls through to the default one.
    m.default_greeting_enabled = True
    m.add_greeting("11", "Off", "personal", enabled=False)
    assert m.process_greeting("11", "Off") == "Hey Off (off)"
    m.custom_greeting_enabled = False
    m.add_greeting("12", "Cus", "personal")
    assert m.process_greeting("12", "Cus") == "Hey Cus (cus)"


def test_process_greeting_global_reset_clears_custom_flags(m):
    ok, _e, gid = m.add_greeting("1", "Ann", "Hi")
    m.greetings[gid].greeted_flag = True
    m.greetings[gid].last_greeted = 0
    m.greeting_reset_interval_hours = 1
    m.flag_manager.last_reset_time = 0
    assert m.process_greeting("1", "Ann", current_time=7200.0) == "Hi"
    assert m.flag_manager.last_reset_time > 0


def test_greeting_context_and_render(m):
    assert cm.ChatbotManager.greeting_context("1", "Bob") == {"username": "Bob", "user_id": "1", "user_login": "bob"}
    assert cm.ChatbotManager.greeting_context("1", None, "x")["user_login"] == "x"
    assert m.render_greeting("  Hi {username} {user_id} ", "7", "Al") == "Hi Al 7"
    assert m.render_greeting(None, "7", "Al") == ""


def test_reset_greeting_for_and_greeted_since(m):
    ok, _e, gid = m.add_greeting("1", "Ann", "Hi")
    g = m.greetings[gid]
    g.greeted_flag, g.last_greeted = True, 5
    m.flag_manager.reset_all_flags()
    m.flag_manager.mark_user_greeted("x")
    stats = m.greeted_since_reset()
    assert stats["count"] == 2 and stats["since"] == m.flag_manager.last_reset_time
    assert m.reset_greeting_for(gid) and not g.greeted_flag and g.last_greeted == 0
    assert m.reset_greeting_for("nope") is False
    m.flag_manager.last_reset_time = 0
    assert m.greeted_since_reset()["since"] == 0


def test_save_greeting_settings(m):
    assert m.save_greeting_settings(default_enabled=True, custom_enabled=True, default_text="  ",
                                    reset_interval_hours=5) is False
    assert m.save_greeting_settings(default_enabled=False, custom_enabled=1, default_text=" Hi ",
                                    reset_interval_hours=0)
    assert (m.default_greeting_enabled, m.custom_greeting_enabled, m.greetings_enabled) == (False, True, True)
    assert m.default_greeting_text == "Hi" and m.greeting_reset_interval_hours == 24
    m.save_greeting_settings(default_enabled=False, custom_enabled=False, default_text="x", reset_interval_hours=-3)
    assert m.greetings_enabled is False and m.greeting_reset_interval_hours == 1


def test_greeting_toggles_and_getters(m):
    assert m.toggle_greetings_enabled(False)
    assert (m.get_greetings_enabled(), m.get_default_greeting_enabled(), m.get_custom_greeting_enabled()) == \
        (False, False, False)
    assert m.toggle_default_greeting_enabled(True) and m.get_default_greeting_enabled()
    assert m.toggle_custom_greeting_enabled(True) and m.get_custom_greeting_enabled()
    assert m.update_default_greeting("Yo") and m.get_default_greeting() == "Yo"
    assert m.update_greeting_cooldown(3) and m.get_greeting_cooldown() == 3
    assert m.set_greeting_reset_interval(0) is False
    assert m.set_greeting_reset_interval(6) and m.get_greeting_reset_interval() == 6
    assert m.get_greeting_flag_stats()["file_path"].endswith("greeting_flags.json")


def test_reset_all_greeting_flags(m):
    ok, _e, gid = m.add_greeting("1", "Ann", "Hi")
    m.greetings[gid].greeted_flag = True
    m.flag_manager.mark_user_greeted("x")
    m.reset_all_greeting_flags()
    assert not m.greetings[gid].greeted_flag and m.flag_manager.greeted_flags == {}


def test_greetings_statistics(m):
    m.default_greeting_text = "Default"
    m.add_greeting("1", "A", "Default")
    m.add_greeting("2", "B", "Custom")
    s = m.get_greetings_statistics()
    assert s["total_greetings"] == 2 and s["custom_greetings"] == 1 and s["default_greetings_used"] == 1
    assert s["default_greeting_text"] == "Default" and s["greeting_cooldown_hours"] == 24


def test_end_to_end_save_round_trip(env, m):
    """Everything written by one manager is read back by the next."""
    m.add_command(_cmd("c1", "hello"))
    m.add_event(ChatEvent(event_id="e1", name="F", event_type="follow", response_text="x"))
    m.add_quote("q text", "Au", "me")
    m.add_greeting("u1", "Ann", "Hi")
    m.save_quote_settings(enabled=True, cooldown_seconds=4, add_permission="vip", reply_format="{text}")
    m.flush_pending_saves()
    again = env.make()
    assert set(again.commands) == {"c1"} and set(again.events) == {"e1"}
    assert [q.text for q in again.quotes.values()] == ["q text"]
    assert again.get_greeting_by_user_id("u1").username == "Ann"
    assert again.quote_cooldown_seconds == 4 and again.add_quote_permission == "vip"
    assert isinstance(again.get_all_greetings()["" + next(iter(again.greetings))], Greeting)
