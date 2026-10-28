# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/chatbot.py: notifications, echo detection, module-level send / dispatch helpers.

The ``Chatbot_API`` class is tested in ``test_chatbot_part2.py``. No real Twitch client or network:
the main Twitch account, the chatbot API and every sender are fakes, and threads only record targets.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import pytest  # noqa: E402

from _chatbot_fakes import FakeMainTwitch, cb  # noqa: E402,F401  (cb: fixture)
from modules import chatbot  # noqa: E402
from modules import twitch as twitch_mod  # noqa: E402


class Bot:
    """Minimal chatbot_api stand-in for the module-level wrappers."""

    def __init__(self, fallback=False, connected=True, user_id="bot1"):
        self.using_fallback = fallback
        self.is_connected = connected
        self.user_id = user_id
        self.last_send_problem = None
        self.sent = []
        self.announced = []
        self.send_result = True
        self.client_id = "cid"
        self.client_secret = "secret"
        self.auth_token = "tok"
        self.refresh_token = "ref"
        self.load_ok = True
        self.stage_result = True
        self.saved = 0

    async def send_chat_message(self, message, reply_to=None):
        self.sent.append((message, reply_to))
        if isinstance(self.send_result, Exception):
            raise self.send_result
        return self.send_result

    async def send_chat_announcement(self, message, color):
        self.announced.append((message, color))
        if isinstance(self.send_result, Exception):
            raise self.send_result
        return self.send_result

    def load_auth_data(self):
        return self.load_ok

    def save_auth_data(self):
        self.saved += 1

    async def stage_chatbot_api(self):
        if isinstance(self.stage_result, Exception):
            raise self.stage_result
        return self.stage_result

    def get_connection_status(self):
        return {"status": "ok"}

    def get_token_status(self):
        return {"has_auth_token": True}


# --------------------------------------------------------------------------- notifications


def test_notify_chatbot_passes_everything(cb):
    chatbot._notify_chatbot("msg", title="T", hint="H", dedupe_key="k", cooldown=5, details="d", type="error")
    message, kw = cb.shown.calls[0]
    assert message == "msg" and kw["type"] == "error" and kw["source"] == "chatbot"
    assert kw["title"] == "T" and kw["hint"] == "H" and kw["details"] == "d"
    assert kw["dedupe_key"] == "k" and kw["dedupe_cooldown_sec"] == 5
    assert kw["actions"][0]["settings_subtab"] == "Twitch"


def test_notify_chatbot_without_subtab_or_details(cb):
    chatbot._notify_chatbot("m", title="T", hint="H", dedupe_key="k", cooldown=1, subtab=None)
    _m, kw = cb.shown.calls[0]
    assert kw["actions"] is None and kw["details"] is None and kw["type"] == "warning"


def test_notify_chatbot_never_raises(cb, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ui gone")

    monkeypatch.setattr("modules.notification_engine.notify", boom)
    chatbot._notify_chatbot("m", title="T", hint="H", dedupe_key="k", cooldown=1)


def test_notify_signin_failed(cb):
    chatbot.notify_chatbot_signin_failed("why")
    _m, kw = cb.shown.calls[0]
    assert kw["title"] == "The chatbot account isn't signed in"
    assert kw["dedupe_key"] == "chatbot:auth_failed" and kw["details"] == "why"
    assert "Connect Chatbot" in kw["hint"]


@pytest.mark.parametrize("response,expected", [
    (None, None),
    ("text", None),
    ({"data": []}, None),
    ({"data": ["x"]}, None),
    ({"data": [{"is_sent": True}]}, None),
    ({"data": [{"is_sent": False, "drop_reason": {"message": "Held"}}]}, "Held"),
    ({"data": [{"is_sent": False, "drop_reason": {"message": "Held", "code": "c9"}}]}, "Held (c9)"),
    ({"data": [{"is_sent": False, "drop_reason": {"code": "c1"}}]}, "c1"),
    ({"data": [{"is_sent": False, "drop_reason": {}}]}, "no reason given"),
    ({"data": [{"is_sent": False, "drop_reason": "plain text"}]}, "plain text"),
])
def test_twitch_drop_reason(response, expected):
    assert chatbot._twitch_drop_reason(response) == expected


def test_twitch_drop_reason_swallows_errors():
    class Weird(dict):
        def get(self, key, default=None):
            raise RuntimeError("bad")

    assert chatbot._twitch_drop_reason(Weird()) is None


@pytest.mark.parametrize("problem,title,key", [
    (("dropped", "Held (x)"), "Twitch held back a chatbot message", "chatbot:dropped:Held (x)"),
    (("auth", "no sign-in"), "The chatbot couldn't post in Twitch chat", "chatbot:send_failed:twitch"),
    (("channel", ""), "The chatbot couldn't post in Twitch chat", "chatbot:send_failed:twitch"),
    (("refused", "403"), "The chatbot couldn't post in Twitch chat", "chatbot:send_failed:twitch"),
    (None, "The chatbot couldn't post in Twitch chat", "chatbot:send_failed:twitch"),
])
def test_report_send_problem_kinds(cb, problem, title, key):
    chatbot._report_twitch_send_problem(problem, "hello")
    message, kw = cb.shown.calls[0]
    assert kw["title"] == title and kw["dedupe_key"] == key
    assert kw["details"].startswith("Message: hello")
    if problem and problem[1]:
        assert problem[1] in kw["details"]
    if problem and problem[0] == "auth":
        assert "isn't signed in" in message
    if problem and problem[0] == "channel":
        assert "connect your Twitch account" in kw["hint"]
    if problem and problem[0] == "dropped":
        assert kw["actions"] is None


def test_report_send_problem_truncates_long_messages(cb):
    chatbot._report_twitch_send_problem(("refused", ""), "x" * 300)
    details = cb.shown.calls[0][1]["details"]
    assert details == "Message: " + "x" * 200 + "…"


# --------------------------------------------------------------------------- echo detection


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(chatbot.time, "monotonic", lambda: now["t"])
    return now


def test_note_sent_chat_message_keeps_recent_texts(cb, clock):
    chatbot.note_sent_chat_message("  ")
    assert chatbot._recent_sent_chat == []
    chatbot.note_sent_chat_message(" hi ")
    clock["t"] += 30
    chatbot.note_sent_chat_message("later")
    assert chatbot._recent_sent_chat == [(1030.0, "later")]  # the old one expired


def test_note_sent_chat_message_caps_history(cb, clock):
    for i in range(60):
        chatbot.note_sent_chat_message(f"m{i}")
    assert len(chatbot._recent_sent_chat) == 51


def test_is_own_chat_message_dedicated_bot(cb, clock):
    assert chatbot.is_own_chat_message("", "x") is False
    chatbot.chatbot_api = SimpleNamespace(using_fallback=False, user_id="bot")
    assert chatbot.is_own_chat_message("bot", "anything") is True
    assert chatbot.is_own_chat_message("other", "") is False


def test_is_own_chat_message_broadcaster_echo(cb, clock, monkeypatch):
    monkeypatch.setattr(twitch_mod, "twitch_api", FakeMainTwitch(user_id="100"))
    chatbot.note_sent_chat_message("!hello")
    assert chatbot.is_own_chat_message("200", "!hello") is False  # not the broadcaster
    assert chatbot.is_own_chat_message("100", "!other") is False
    assert chatbot.is_own_chat_message("100", "!hello") is True
    assert chatbot.is_own_chat_message("100", "!hello") is False  # consumed once
    chatbot.note_sent_chat_message("old")
    clock["t"] += 25
    assert chatbot.is_own_chat_message("100", "old") is False


def test_is_own_chat_message_without_main_account(cb, clock, monkeypatch):
    chatbot.note_sent_chat_message("hi")
    assert chatbot.is_own_chat_message("100", "hi") is False
    monkeypatch.delattr(twitch_mod, "twitch_api")
    assert chatbot.is_own_chat_message("100", "hi") is False


# --------------------------------------------------------------------------- aiohttp session check


def test_session_usable_checks():
    assert chatbot._aiohttp_session_usable_on_running_loop(None) is False
    assert chatbot._aiohttp_session_usable_on_running_loop(SimpleNamespace(closed=True)) is False
    # No running loop.
    assert chatbot._aiohttp_session_usable_on_running_loop(SimpleNamespace(closed=False)) is False

    async def inside():
        loop = asyncio.get_running_loop()
        other = asyncio.new_event_loop()
        closed = asyncio.new_event_loop()
        closed.close()
        try:
            return (
                chatbot._aiohttp_session_usable_on_running_loop(SimpleNamespace(closed=False, _loop=loop)),
                chatbot._aiohttp_session_usable_on_running_loop(
                    SimpleNamespace(closed=False, _loop=None, connector=SimpleNamespace(_loop=loop))),
                chatbot._aiohttp_session_usable_on_running_loop(SimpleNamespace(closed=False, _loop=other)),
                chatbot._aiohttp_session_usable_on_running_loop(SimpleNamespace(closed=False, _loop=closed)),
                chatbot._aiohttp_session_usable_on_running_loop(SimpleNamespace(closed=False, _loop=None, connector=None)),
            )
        finally:
            other.close()

    assert asyncio.run(inside()) == (True, True, False, False, False)


# --------------------------------------------------------------------------- credentials / reconnect


@pytest.mark.parametrize("creds,expected", [
    ({"client_id": "a", "client_secret": "b"}, True),
    ({"client_id": " ", "client_secret": "b"}, False),
    ({"client_id": "a"}, False),
    ({}, False),
])
def test_has_dedicated_credentials(monkeypatch, creds, expected):
    monkeypatch.setattr(chatbot, "get_chatbot_credentials", lambda: creds)
    assert chatbot.chatbot_has_dedicated_credentials() is expected


@pytest.fixture
def reachable(monkeypatch):
    from modules import connection_monitor

    state = {"internet": True, "service": True}
    monkeypatch.setattr(connection_monitor, "is_internet_available", lambda: state["internet"])
    monkeypatch.setattr(connection_monitor, "is_service_reachable", lambda name: state["service"])
    monkeypatch.setattr(chatbot, "chatbot_has_dedicated_credentials", lambda: True)
    return state


def test_auto_reconnect_preconditions(cb, reachable, monkeypatch):
    monkeypatch.setattr(chatbot, "chatbot_has_dedicated_credentials", lambda: False)
    assert chatbot.attempt_auto_reconnect() is False
    monkeypatch.setattr(chatbot, "chatbot_has_dedicated_credentials", lambda: True)
    assert chatbot.attempt_auto_reconnect() is False  # no chatbot_api
    chatbot.chatbot_api = Bot(fallback=True)
    assert chatbot.attempt_auto_reconnect() is False
    chatbot.chatbot_api = Bot(connected=True)
    assert chatbot.attempt_auto_reconnect() is True
    chatbot.chatbot_api = Bot(connected=False)
    reachable["internet"] = False
    assert chatbot.attempt_auto_reconnect() is False
    reachable["internet"] = True
    reachable["service"] = False
    assert chatbot.attempt_auto_reconnect() is False
    assert cb.threads == []


def test_auto_reconnect_monitor_import_error(cb, reachable, monkeypatch):
    chatbot.chatbot_api = Bot(connected=False)
    monkeypatch.setitem(sys.modules, "modules.connection_monitor", None)
    assert chatbot.attempt_auto_reconnect() is False


def test_auto_reconnect_runs_stage_in_thread(cb, reachable):
    bot = Bot(connected=False)
    chatbot.chatbot_api = bot
    assert chatbot.attempt_auto_reconnect() is True
    assert chatbot._chatbot_reconnect_in_progress is True
    assert chatbot.attempt_auto_reconnect() is False  # one at a time
    (thread,) = cb.threads
    assert thread.name == "ChatbotAutoReconnect" and thread.started
    thread.run()
    assert chatbot._chatbot_reconnect_in_progress is False
    assert cb.shown.calls == []


@pytest.mark.parametrize("result,details", [(False, "Automatic chatbot reconnect failed"),
                                            (RuntimeError("boom"), "RuntimeError: boom")])
def test_auto_reconnect_failure_notifies(cb, reachable, result, details):
    bot = Bot(connected=False)
    bot.stage_result = result
    chatbot.chatbot_api = bot
    chatbot.attempt_auto_reconnect()
    cb.threads[0].run()
    assert cb.shown.calls[0][1]["details"] == details
    assert chatbot._chatbot_reconnect_in_progress is False


# --------------------------------------------------------------------------- initialize


def test_initialize_only_in_main_process(cb, monkeypatch):
    monkeypatch.setattr(chatbot.multiprocessing, "current_process", lambda: SimpleNamespace(name="Worker", pid=1))
    chatbot.initialize()
    assert chatbot.chatbot_api is None and cb.threads == []


def test_initialize_once(cb, monkeypatch):
    monkeypatch.setattr(chatbot.multiprocessing, "current_process", lambda: SimpleNamespace(name="MainProcess", pid=1))
    chatbot.initialize()
    api = chatbot.chatbot_api
    assert isinstance(api, chatbot.Chatbot_API) and chatbot._initialized
    (thread,) = cb.threads
    assert thread.target is chatbot._run_chatbot_init and thread.daemon is True and thread.started
    chatbot.initialize()
    assert chatbot.chatbot_api is api and len(cb.threads) == 1


@pytest.mark.parametrize("staged,fallback,timeout,notified", [
    (True, False, False, False),
    (False, False, True, True),
    (False, True, False, False),
])
def test_run_chatbot_init(cb, monkeypatch, caplog, staged, fallback, timeout, notified):
    monkeypatch.setattr(twitch_mod, "wait_for_staging_complete", lambda timeout: not timeout_flag)
    timeout_flag = timeout
    bot = Bot(fallback=fallback)
    bot.stage_result = staged
    chatbot.chatbot_api = bot
    chatbot._run_chatbot_init()
    assert bool(cb.shown.calls) is notified
    assert ("Timed out waiting for Twitch staging" in caplog.text) is timeout


# --------------------------------------------------------------------------- status helpers


def test_get_chatbot_api(cb):
    assert chatbot.get_chatbot_api() is None
    chatbot.chatbot_api = Bot()
    assert chatbot.get_chatbot_api() is chatbot.chatbot_api


def test_is_chatbot_connected(cb, monkeypatch):
    assert chatbot.is_chatbot_connected() is False
    chatbot.chatbot_api = Bot()
    assert chatbot.is_chatbot_connected() is False  # global flag not set
    chatbot.chatbot_connected = True
    assert chatbot.is_chatbot_connected() is True
    chatbot.chatbot_api = Bot(fallback=True)
    assert not chatbot.is_chatbot_connected()
    monkeypatch.setattr(twitch_mod, "twitch_api", FakeMainTwitch(connected=True))
    assert chatbot.is_chatbot_connected() is True
    chatbot.chatbot_api = SimpleNamespace(using_fallback=False)  # no is_connected attribute
    chatbot.chatbot_connected = False
    assert chatbot.is_chatbot_connected() is False


def test_connection_and_token_status_wrappers(cb):
    assert chatbot.get_chatbot_connection_status()["status"] == "Not Initialized"
    assert chatbot.get_chatbot_token_status()["status"] == "Not Initialized"
    chatbot.chatbot_api = Bot()
    assert chatbot.get_chatbot_connection_status() == {"status": "ok"}
    assert chatbot.get_chatbot_token_status() == {"has_auth_token": True}

    def boom():
        raise RuntimeError("x")

    chatbot.chatbot_api.get_connection_status = boom
    chatbot.chatbot_api.get_token_status = boom
    assert chatbot.get_chatbot_connection_status()["status"] == "Error"
    assert chatbot.get_chatbot_token_status() == {"status": "Error", "error": "x"}


# --------------------------------------------------------------------------- send worker / queue


def test_running_asyncio_loop():
    assert chatbot._running_asyncio_loop() is False

    async def inside():
        return chatbot._running_asyncio_loop()

    assert asyncio.run(inside()) is True


def test_send_worker_starts_once_and_enqueue(cb):
    chatbot.enqueue_chatbot_send("hi", "r1")
    chatbot.enqueue_chatbot_dispatch("yo", ["youtube"], "r2", [{"c": 1}])
    assert len(cb.threads) == 1 and cb.threads[0].name == "ChatbotSendWorker"
    assert chatbot._chatbot_send_queue.get_nowait() == ("send", "hi", "r1", None, None)
    assert chatbot._chatbot_send_queue.get_nowait() == ("dispatch", "yo", "r2", ["youtube"], [{"c": 1}])


def test_send_worker_loop_processes_items(cb, monkeypatch, caplog):
    seen = []

    class Stop(BaseException):
        pass

    items = [RuntimeError("queue broke"), ("send", "a", "r", None, None),
             ("dispatch", "b", None, ["twitch"], None), ("send", "boom", None, None, None)]

    class Q:
        def get(self):
            if not items:
                raise Stop()
            item = items.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    def send(message, reply_to):
        if message == "boom":
            raise RuntimeError("send failed")
        seen.append(("send", message, reply_to))

    monkeypatch.setattr(chatbot, "_chatbot_send_queue", Q())
    monkeypatch.setattr(chatbot, "_send_chatbot_message_blocking", send)
    monkeypatch.setattr(chatbot, "_dispatch_chatbot_response_blocking",
                        lambda m, t, reply_to_message_id=None, discord_channels=None: seen.append(("dispatch", m, t)))
    with pytest.raises(Stop):
        chatbot._chatbot_send_worker_loop()
    assert seen == [("send", "a", "r"), ("dispatch", "b", ["twitch"])]
    assert "Chatbot send worker error" in caplog.text


# --------------------------------------------------------------------------- send_chatbot_message


def test_send_chatbot_message_without_api(cb):
    assert chatbot.send_chatbot_message("hi") is False
    assert cb.shown.calls[0][1]["details"].endswith("Chatbot not started yet")


def test_send_chatbot_message_dedicated_not_connected(cb):
    chatbot.chatbot_api = Bot(connected=False)
    assert chatbot.send_chatbot_message("hi") is False
    assert "Chatbot account not connected" in cb.shown.calls[0][1]["details"]


def test_send_chatbot_message_fallback_without_main_account(cb):
    chatbot.chatbot_api = Bot(fallback=True)
    assert chatbot.send_chatbot_message("hi") is False
    assert "Main Twitch account not connected" in cb.shown.calls[0][1]["details"]


def test_send_chatbot_message_sends_blocking(cb, monkeypatch):
    chatbot.chatbot_api = Bot(connected=True)
    sent = []
    monkeypatch.setattr(chatbot, "_send_chatbot_message_blocking", lambda m, r: sent.append((m, r)) or True)
    assert chatbot.send_chatbot_message("hi", "parent") is True
    monkeypatch.setattr(twitch_mod, "twitch_api", FakeMainTwitch())
    chatbot.chatbot_api = Bot(fallback=True)
    assert chatbot.send_chatbot_message("fb") is True
    assert sent == [("hi", "parent"), ("fb", None)]


def test_send_chatbot_message_on_event_loop_queues(cb):
    chatbot.chatbot_api = Bot()
    chatbot.chatbot_connected = True

    async def inside():
        return chatbot.send_chatbot_message("queued")

    assert asyncio.run(inside()) is True
    assert chatbot._chatbot_send_queue.get_nowait()[1] == "queued"


def test_send_chatbot_message_unexpected_error(cb, monkeypatch):
    chatbot.chatbot_api = Bot()

    def boom(m, r):
        raise RuntimeError("bad")

    monkeypatch.setattr(chatbot, "_send_chatbot_message_blocking", boom)
    assert chatbot.send_chatbot_message("x") is False
    assert cb.shown.calls[0][1]["details"].endswith("RuntimeError: bad")


# --------------------------------------------------------------------------- blocking sends


def test_send_blocking_splits_and_replies_on_first_part(cb, monkeypatch):
    chatbot.chatbot_api = Bot()
    parts = []
    monkeypatch.setattr(chatbot, "_send_one_chatbot_message_blocking", lambda p, r: parts.append((len(p), r)) or True)
    text = " ".join(["word"] * 200)
    assert chatbot._send_chatbot_message_blocking(text, "parent") is True
    assert len(parts) == 2 and parts[0][1] == "parent" and parts[1][1] is None
    assert cb.shown.calls == []


def test_send_blocking_nothing_to_send(cb):
    assert chatbot._send_chatbot_message_blocking("   ") is False
    assert chatbot._send_chatbot_message_blocking("hi") is False  # no chatbot_api


def test_send_blocking_failure_reports_first_problem(cb, monkeypatch):
    bot = Bot()
    chatbot.chatbot_api = bot
    calls = []

    def send(part, reply):
        calls.append(part)
        if len(calls) == 1:
            raise RuntimeError("socket")
        bot.last_send_problem = None
        return False

    monkeypatch.setattr(chatbot, "_send_one_chatbot_message_blocking", send)
    assert chatbot._send_chatbot_message_blocking(" ".join(["w"] * 400)) is False
    assert len(cb.shown.calls) == 1
    assert "RuntimeError: socket" in cb.shown.calls[0][1]["details"]


def test_send_blocking_failure_without_problem(cb, monkeypatch):
    chatbot.chatbot_api = SimpleNamespace()
    monkeypatch.setattr(chatbot, "_send_one_chatbot_message_blocking", lambda p, r: False)
    assert chatbot._send_chatbot_message_blocking("hi") is False
    assert cb.shown.titles == ["The chatbot couldn't post in Twitch chat"]


def test_send_one_blocking_runs_on_its_own_loop(cb):
    assert chatbot._send_one_chatbot_message_blocking("x") is False  # no api
    bot = Bot()
    chatbot.chatbot_api = bot
    assert chatbot._send_one_chatbot_message_blocking("hello", "r") is True
    assert bot.sent == [("hello", "r")]
    bot.send_result = RuntimeError("loop error")
    assert chatbot._send_one_chatbot_message_blocking("again") is False


# --------------------------------------------------------------------------- deliver / dispatch


def test_deliver_skips_empty_and_routes(cb, monkeypatch):
    from modules import chat_banner

    banners, dispatched = [], []
    monkeypatch.setattr(chat_banner, "emit_chat_banner", lambda m, source: banners.append((m, source)) or True)
    monkeypatch.setattr(chatbot, "dispatch_chatbot_response",
                        lambda m, t, reply_to_message_id=None, discord_channels=None:
                        dispatched.append((m, t, reply_to_message_id, discord_channels)) or True)
    assert chatbot.deliver_chatbot_output("  ") is False
    assert chatbot.deliver_chatbot_output("b", output_mode="BANNER") is True
    assert chatbot.deliver_chatbot_output("c", ["youtube"], "r", [{"x": 1}]) is True
    assert banners == [("b", "chatbot")]
    assert dispatched == [("c", ["youtube"], "r", [{"x": 1}])]


def test_dispatch_process_event_result_shapes(cb, monkeypatch):
    seen = []
    monkeypatch.setattr(chatbot, "deliver_chatbot_output",
                        lambda r, t, discord_channels=None, output_mode="chat": seen.append((r, t, discord_channels, output_mode)))
    chatbot.dispatch_process_event_result(None)
    chatbot.dispatch_process_event_result("plain")
    chatbot.dispatch_process_event_result("plain", default_targets=["youtube"])
    chatbot.dispatch_process_event_result(("one",))
    chatbot.dispatch_process_event_result(("two", ["youtube"]))
    chatbot.dispatch_process_event_result(("three", ["twitch"], ["d"]))
    chatbot.dispatch_process_event_result([("four", ["twitch"], [], "banner"), "five"])
    assert seen == [
        ("plain", ["twitch"], None, "chat"),
        ("plain", ["youtube"], None, "chat"),
        ("one", ["twitch"], None, "chat"),
        ("two", ["youtube"], None, "chat"),
        ("three", ["twitch"], ["d"], "chat"),
        ("four", ["twitch"], [], "banner"),
        ("five", ["twitch"], None, "chat"),
    ]


def test_dispatch_chatbot_response_queues_on_loop(cb, monkeypatch):
    calls = []
    monkeypatch.setattr(chatbot, "_dispatch_chatbot_response_blocking",
                        lambda m, t, reply_to_message_id=None, discord_channels=None: calls.append(m) or "sync")
    assert chatbot.dispatch_chatbot_response("x") == "sync"

    async def inside():
        return chatbot.dispatch_chatbot_response("y", ["twitch"], "r", None)

    assert asyncio.run(inside()) is True
    assert calls == ["x"]
    assert chatbot._chatbot_send_queue.get_nowait() == ("dispatch", "y", "r", ["twitch"], None)


@pytest.fixture
def outputs(monkeypatch):
    from modules import discord_service, youtube

    state = {"twitch": True, "youtube": True, "discord": True}
    calls = []

    def twitch_send(message, reply_to):
        calls.append(("twitch", message, reply_to))
        if isinstance(state["twitch"], Exception):
            raise state["twitch"]
        return state["twitch"]

    def yt_send(message):
        calls.append(("youtube", message))
        if isinstance(state["youtube"], Exception):
            raise state["youtube"]
        return state["youtube"]

    def discord_send(message, channels):
        calls.append(("discord", message, channels))
        if isinstance(state["discord"], Exception):
            raise state["discord"]
        return state["discord"]

    monkeypatch.setattr(chatbot, "_send_chatbot_message_blocking", twitch_send)
    monkeypatch.setattr(youtube, "send_youtube_chat_message", yt_send)
    monkeypatch.setattr(discord_service, "send_to_channels", discord_send)
    return SimpleNamespace(state=state, calls=calls)


def test_dispatch_blocking_targets(cb, outputs):
    assert chatbot._dispatch_chatbot_response_blocking(" ") is False
    assert chatbot._dispatch_chatbot_response_blocking("a") is True
    assert outputs.calls == [("twitch", "a", None)]
    outputs.calls.clear()
    assert chatbot._dispatch_chatbot_response_blocking("b", ["twitch", "youtube"], "r", [{"ch": 1}]) is True
    assert [c[0] for c in outputs.calls] == ["twitch", "youtube", "discord"]
    outputs.calls.clear()
    assert chatbot._dispatch_chatbot_response_blocking("c", [], discord_channels=[{"ch": 1}]) is True
    assert [c[0] for c in outputs.calls] == ["discord"]


def test_dispatch_blocking_failures(cb, outputs, caplog):
    outputs.state.update(twitch=False, youtube=False, discord=False)
    assert chatbot._dispatch_chatbot_response_blocking("x", ["twitch", "youtube"], discord_channels=[{"c": 1}]) is False
    assert cb.shown.titles == ["The chatbot couldn't post to Discord"]
    assert cb.shown.calls[0][1]["details"] == "Discord send returned no success"
    assert "Chatbot Twitch send failed" in caplog.text and "YouTube send failed" in caplog.text


def test_dispatch_blocking_errors(cb, outputs, caplog):
    outputs.state.update(twitch=RuntimeError("t"), youtube=RuntimeError("y"), discord=RuntimeError("d"))
    assert chatbot._dispatch_chatbot_response_blocking("x", "all", discord_channels=[{"c": 1}]) is False
    assert cb.shown.calls[0][1]["details"] == "RuntimeError: d"
    assert cb.shown.calls[0][1]["actions"][0]["settings_subtab"] == "Discord"
    assert "Chatbot Twitch dispatch error" in caplog.text and "Chatbot YouTube dispatch error" in caplog.text


# --------------------------------------------------------------------------- OAuth reconnection


def test_oauth_reconnection_preconditions(cb):
    assert chatbot.trigger_chatbot_oauth_reconnection() is False
    bot = Bot()
    bot.load_ok = False
    chatbot.chatbot_api = bot
    assert chatbot.trigger_chatbot_oauth_reconnection() is False
    bot.load_ok = True
    bot.client_secret = ""
    assert chatbot.trigger_chatbot_oauth_reconnection() is False


def test_oauth_reconnection_clears_tokens_and_restages(cb):
    bot = Bot()
    chatbot.chatbot_api = bot
    assert chatbot.trigger_chatbot_oauth_reconnection() is True
    assert bot.auth_token is None and bot.refresh_token is None and bot.saved == 1
    bot.stage_result = RuntimeError("oauth broke")
    assert chatbot.trigger_chatbot_oauth_reconnection() is False


def test_oauth_reconnection_timeout_and_errors(cb, monkeypatch):
    chatbot.chatbot_api = Bot()

    class Future:
        def result(self, timeout):
            assert timeout == 120
            raise concurrent.futures.TimeoutError()

    class Executor:
        def __init__(self, max_workers=None):
            pass

        def submit(self, fn):
            return Future()

        def shutdown(self, wait=True):
            assert wait is False

    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", Executor)
    assert chatbot.trigger_chatbot_oauth_reconnection() is False

    def boom():
        raise RuntimeError("load broke")

    chatbot.chatbot_api.load_auth_data = boom
    assert chatbot.trigger_chatbot_oauth_reconnection() is False


# --------------------------------------------------------------------------- announcements


def test_announcement_wrapper_paths(cb, monkeypatch):
    assert chatbot.send_chatbot_announcement("x") is False
    chatbot.chatbot_api = Bot(connected=False)
    assert chatbot.send_chatbot_announcement("x") is False
    bot = Bot(connected=True)
    chatbot.chatbot_api = bot
    assert chatbot.send_chatbot_announcement("hello", "blue") is True
    assert bot.announced == [("hello", "blue")]
    chatbot.chatbot_api = Bot(fallback=True)
    assert chatbot.send_chatbot_announcement("x") is False
    monkeypatch.setattr(twitch_mod, "twitch_api", FakeMainTwitch())
    assert chatbot.send_chatbot_announcement("fb") is True
    chatbot.chatbot_api.send_result = RuntimeError("thread error")
    assert chatbot.send_chatbot_announcement("err") is False


def test_announcement_wrapper_outer_error(cb, monkeypatch):
    chatbot.chatbot_api = Bot()

    class Executor:
        def __init__(self, max_workers=None):
            raise RuntimeError("no threads")

    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", Executor)
    assert chatbot.send_chatbot_announcement("x") is False


class _Flip:
    """A flag whose truth value changes on every check (reaches the defensive ``else`` branches)."""

    def __init__(self):
        self.calls = 0

    def __bool__(self):
        self.calls += 1
        return self.calls == 1


def test_defensive_else_branches(cb):
    bot = Bot()
    bot.using_fallback = _Flip()
    chatbot.chatbot_api = bot
    assert chatbot.send_chatbot_message("x") is False
    bot.using_fallback = _Flip()
    assert chatbot.send_chatbot_announcement("x") is False


def test_dedicated_global_flag_is_enough(cb):
    chatbot.chatbot_api = Bot(connected=False)
    chatbot.chatbot_connected = True
    assert chatbot.send_chatbot_announcement("ok") is True
