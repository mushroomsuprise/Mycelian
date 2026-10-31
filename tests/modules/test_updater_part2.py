# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.updater.UpdateManager: scheduling, checks, deferred prompts, the update dialog
and the download/restart flow.

Timers, threads, GitHub and the sync/restart are all faked; nothing is downloaded or exited.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import pytest  # noqa: E402

from modules import update_sync  # noqa: E402
from modules import updater as up  # noqa: E402

SHA = "c" * 40
INFO = {"latest_version": "2.0.0", "tag_name": "v2.0.0", "commit_sha": SHA, "release_notes": "line1\nline2",
        "release_url": "https://example.invalid/r"}


# --------------------------------------------------------------------------- fakes

class FakeTimer:
    def __init__(self, interval, fn, once=False, deactivate_error=None):
        self.interval, self.fn, self.once = interval, fn, once
        self.deactivated = 0
        self.deactivate_error = deactivate_error
        self._active = True
        self.active_error = None

    def deactivate(self):
        self.deactivated += 1
        if self.deactivate_error:
            raise self.deactivate_error

    @property
    def active(self):
        return self._active

    @active.setter
    def active(self, value):
        if self.active_error:
            raise self.active_error
        self._active = value


@pytest.fixture
def sched(monkeypatch):
    timers = []

    def app_schedule(interval, fn, *, once=False, active=True):
        t = FakeTimer(interval, fn, once)
        timers.append(t)
        return t

    monkeypatch.setattr(up, "app_schedule", app_schedule)
    return timers


class FakeWork:
    def __init__(self, title, source):
        self.title, self.source = title, source
        self.calls = []

    def done(self, title=None, message="", **kw):
        self.calls.append(("done", title, message, kw))

    def fail(self, title=None, message="", **kw):
        self.calls.append(("fail", title, message, kw))

    def close(self):
        self.calls.append(("close",))


@pytest.fixture
def notes(monkeypatch):
    ns = SimpleNamespace(notifications=[], works=[])
    monkeypatch.setattr(up, "notify", lambda message, **kw: ns.notifications.append((message, kw)))
    monkeypatch.setattr(up, "nav_actions_settings", lambda tab: [{"nav": tab}])

    def notify_progress(title, *, source="app", **kw):
        w = FakeWork(title, source)
        ns.works.append(w)
        return w

    monkeypatch.setattr(up, "notify_progress", notify_progress)
    return ns


class SyncThread:
    """Runs the target as soon as it is started."""

    def __init__(self, target=None, daemon=None, name=None, args=()):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class HeldThread:
    """Holds the target until the test runs it."""

    held = []

    def __init__(self, target=None, daemon=None, name=None, args=()):
        self.target = target
        HeldThread.held.append(self)

    def start(self):
        pass


@pytest.fixture
def sync_threads(monkeypatch):
    monkeypatch.setattr(threading, "Thread", SyncThread)


@pytest.fixture
def held_threads(monkeypatch):
    HeldThread.held = []
    monkeypatch.setattr(threading, "Thread", HeldThread)
    return HeldThread.held


@pytest.fixture
def settings(monkeypatch):
    from modules import database_manager as dbm
    from modules import dataobjects

    holder = SimpleNamespace(value=SimpleNamespace(auto_update=True, version="1.0.0",
                                                   update_check_interval_minutes=30), error=None)

    def get_app_settings():
        if holder.error:
            raise holder.error
        return holder.value

    monkeypatch.setattr(dataobjects, "state_manager", SimpleNamespace(get_app_settings=get_app_settings))
    monkeypatch.setattr(dbm.database_manager, "_initialized", True)
    holder.dbm = dbm
    return holder


@pytest.fixture
def mgr():
    return up.UpdateManager()


@pytest.fixture
def client(monkeypatch):
    holder = SimpleNamespace(client=None)

    class Ctx:
        entered = 0

        def __enter__(self):
            Ctx.entered += 1
            return self

        def __exit__(self, *exc):
            return False

    holder.make = Ctx
    monkeypatch.setattr(up, "_connected_client", lambda: holder.client)
    return holder


# --------------------------------------------------------------------------- basics

def test_defaults_and_singleton(mgr):
    assert isinstance(up.update_manager, up.UpdateManager)
    assert mgr.is_checking is False
    assert mgr.last_check_result is None and mgr.available_update is None and mgr.last_check_error == ""


def test_record_check(mgr):
    mgr._record_check("available", INFO)
    assert mgr.last_check_result == "available" and mgr.available_update is INFO
    assert mgr.last_check_at > 0
    mgr._record_check("error", INFO, error="bad")
    assert mgr.available_update is None and mgr.last_check_error == "bad"


def test_dialog_is_open(mgr, client):
    assert mgr._dialog_is_open() is False
    mgr._dialog_open = True
    assert mgr._dialog_is_open() is False  # no client: the dialog went with the page
    assert mgr._dialog_open is False
    mgr._dialog_open = True
    client.client = object()
    assert mgr._dialog_is_open() is True


def test_show_available_update(mgr, client, monkeypatch):
    shown = []
    monkeypatch.setattr(mgr, "_show_update_modal", lambda info, manual=False: shown.append((info, manual)))
    assert mgr.show_available_update() is False
    mgr.available_update = INFO
    mgr._dialog_open = True
    client.client = object()
    assert mgr.show_available_update() is False
    mgr._dialog_open = False
    assert mgr.show_available_update() is True
    assert shown == [(INFO, True)]


# --------------------------------------------------------------------------- scheduling

def test_on_ui_ready_schedules_once(mgr, sched, monkeypatch):
    mgr.on_ui_ready()
    settle, periodic = sched
    assert (settle.interval, settle.once) == (up.STARTUP_SETTLE_SECONDS, True)
    assert periodic.fn == mgr.reschedule_periodic_timer and periodic.interval == up.PERIODIC_SCHEDULE_DELAY_SECONDS
    settle.fn()
    pre = sched[2]
    assert pre.interval == up.PRE_CHECK_DELAY_SECONDS and pre.once and pre.fn == mgr._run_initial_check
    mgr.on_ui_ready()
    assert len(sched) == 3


def test_on_ui_ready_keeps_existing_periodic(mgr, sched):
    mgr._periodic_schedule_timer = "existing"
    mgr.on_ui_ready()
    assert len(sched) == 1


def test_on_ui_ready_error_allows_retry(mgr, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("no app")

    monkeypatch.setattr(up, "app_schedule", boom)
    with caplog.at_level(logging.ERROR, logger=up.logger.name):
        mgr.on_ui_ready()
    assert mgr._ui_ready_scheduled is False
    assert "on_ui_ready error: no app" in caplog.text


def test_run_initial_check(mgr, monkeypatch):
    calls = []
    monkeypatch.setattr(mgr, "trigger_check_and_prompt", lambda manual=False: calls.append(manual))
    mgr._session_suppressed = True
    mgr._run_initial_check()
    assert calls == []
    mgr._session_suppressed = False
    mgr._run_initial_check()
    assert calls == [False]


@pytest.fixture
def ng_app(monkeypatch):
    import nicegui

    monkeypatch.setattr(nicegui.app, "update_check_timer", None, raising=False)
    return nicegui.app


@pytest.mark.parametrize("minutes, expected", [(30, 30), (1, 5), (500, 120), ("15", 15)])
def test_reschedule_periodic_timer(mgr, sched, settings, ng_app, minutes, expected):
    old = FakeTimer(1, None, deactivate_error=RuntimeError("gone"))
    mgr._periodic_timer = old
    settings.value.update_check_interval_minutes = minutes
    mgr.reschedule_periodic_timer()
    assert old.deactivated == 1
    (timer,) = sched
    assert timer.interval == expected * 60.0 and timer.fn == mgr._periodic_check and not timer.once
    assert mgr._periodic_timer is timer and ng_app.update_check_timer is timer


@pytest.mark.parametrize("auto, suppressed", [(False, False), (True, True)])
def test_reschedule_periodic_timer_disabled(mgr, sched, settings, ng_app, auto, suppressed):
    settings.value.auto_update = auto
    mgr._session_suppressed = suppressed
    old = FakeTimer(1, None, deactivate_error=RuntimeError("x"))
    mgr._periodic_timer = old
    mgr.reschedule_periodic_timer()
    assert old.deactivated == 1 and mgr._periodic_timer is None and sched == []
    mgr.reschedule_periodic_timer()  # nothing to stop now
    assert sched == []


def test_reschedule_periodic_timer_error(mgr, sched, settings, ng_app, caplog):
    settings.error = RuntimeError("state not ready")
    with caplog.at_level(logging.ERROR, logger=up.logger.name):
        mgr.reschedule_periodic_timer()
    assert "reschedule_periodic_timer failed: state not ready" in caplog.text


def test_periodic_check_gates(mgr, monkeypatch):
    calls = []
    monkeypatch.setattr(mgr, "trigger_check_and_prompt", lambda manual=False: calls.append(manual))
    mgr._session_suppressed = True
    mgr._periodic_check()
    mgr._session_suppressed = False
    mgr._periodic_check()  # startup check not done yet
    mgr._automatic_startup_check_done = True
    mgr._periodic_check()
    assert calls == [False]


def test_trigger_manual_check(mgr, monkeypatch):
    calls = []
    monkeypatch.setattr(mgr, "trigger_check_and_prompt", lambda manual=False: calls.append(manual))
    mgr.trigger_manual_check()
    assert calls == [True]


def test_cancel_periodic(mgr):
    mgr._cancel_periodic()
    t = FakeTimer(1, None, deactivate_error=RuntimeError("x"))
    mgr._periodic_timer = t
    mgr._cancel_periodic()
    assert t.deactivated == 1 and mgr._periodic_timer is None


# --------------------------------------------------------------------------- trigger_check_and_prompt

@pytest.fixture
def checker(monkeypatch):
    ns = SimpleNamespace(result=None, error=None, raise_exc=None, calls=[])

    async def fake_check(version, *, force_refresh=False):
        ns.calls.append((version, force_refresh))
        if ns.raise_exc:
            raise ns.raise_exc
        return ns.result

    monkeypatch.setattr(up, "check_for_updates", fake_check)
    monkeypatch.setattr(up, "last_update_check_error", lambda: ns.error)
    return ns


@pytest.fixture
def modal(mgr, monkeypatch):
    shown = []
    monkeypatch.setattr(mgr, "_show_update_modal", lambda info, manual=False: shown.append((info, manual)))
    return shown


def run_check(mgr, sched, manual):
    mgr.trigger_check_and_prompt(manual=manual)
    poll, safety = sched[0], sched[1]
    assert poll.interval == 0.2 and safety.interval == 1.0
    return poll, safety


def test_trigger_skips_when_running_or_dialog_open(mgr, sched, client):
    mgr._check_running = True
    mgr.trigger_check_and_prompt()
    mgr._check_running = False
    mgr._dialog_open = True
    client.client = object()
    mgr.trigger_check_and_prompt()
    assert sched == []


@pytest.mark.parametrize("manual, notified", [(True, 1), (False, 0)])
def test_trigger_db_not_ready(mgr, sched, settings, notes, client, monkeypatch, manual, notified):
    monkeypatch.setattr(settings.dbm.database_manager, "_initialized", False)
    mgr.trigger_check_and_prompt(manual=manual)
    assert sched == [] and len(notes.notifications) == notified
    assert mgr.is_checking is False


def test_trigger_auto_disabled(mgr, sched, settings, notes, client):
    settings.value.auto_update = False
    mgr.trigger_check_and_prompt()
    assert sched == []


@pytest.mark.parametrize("manual, notified", [(True, 1), (False, 0)])
def test_trigger_unknown_version(mgr, sched, settings, notes, client, manual, notified):
    settings.value.version = ""
    mgr.trigger_check_and_prompt(manual=manual)
    assert sched == [] and len(notes.notifications) == notified


def test_trigger_auto_finds_update(mgr, sched, settings, notes, client, checker, modal, sync_threads):
    checker.result = INFO
    poll, safety = run_check(mgr, sched, manual=False)
    assert mgr.is_checking is True
    assert checker.calls == [("1.0.0", False)]
    assert poll.fn() is False
    assert modal == [(INFO, False)]
    assert mgr.last_check_result == "available" and mgr.available_update is INFO
    assert mgr._automatic_startup_check_done is True and mgr.is_checking is False
    assert notes.works == []
    assert safety.fn() is False and poll.deactivated == 1


def test_trigger_auto_suppressed_records_but_does_not_prompt(mgr, sched, settings, notes, client, checker, modal,
                                                             sync_threads):
    checker.result = INFO
    mgr._session_suppressed = True
    poll, _ = run_check(mgr, sched, manual=False)
    poll.fn()
    assert modal == [] and mgr.last_check_result == "available"


def test_trigger_manual_finds_update(mgr, sched, settings, notes, client, checker, modal, sync_threads):
    checker.result = INFO
    mgr._session_suppressed = True  # manual checks ignore the session decline
    poll, _ = run_check(mgr, sched, manual=True)
    assert checker.calls == [("1.0.0", True)]
    (work,) = notes.works
    assert work.title == "Checking for updates" and work.source == "updates"
    poll.fn()
    assert work.calls == [("close",)]
    assert modal == [(INFO, True)]
    assert mgr._automatic_startup_check_done is False


def test_trigger_manual_up_to_date(mgr, sched, settings, notes, client, checker, modal, sync_threads):
    poll, _ = run_check(mgr, sched, manual=True)
    poll.fn()
    (call,) = notes.works[0].calls
    assert call[:3] == ("done", "You're up to date", "Mycelian 1.0.0 is the latest version.")
    assert mgr.last_check_result == "up_to_date" and modal == []


def test_trigger_auto_up_to_date(mgr, sched, settings, notes, client, checker, modal, sync_threads):
    poll, _ = run_check(mgr, sched, manual=False)
    poll.fn()
    assert mgr.last_check_result == "up_to_date" and mgr._automatic_startup_check_done is True


@pytest.mark.parametrize("manual", [True, False])
def test_trigger_reports_fetch_error(mgr, sched, settings, notes, client, checker, modal, sync_threads, manual):
    checker.error = ("GitHub is limiting requests right now.", "HTTP 403")
    poll, _ = run_check(mgr, sched, manual=manual)
    assert poll.fn() is False
    assert mgr.last_check_result == "error" and mgr.last_check_error == "GitHub is limiting requests right now."
    if manual:
        (call,) = notes.works[0].calls
        assert call[0] == "fail" and call[2] == "GitHub is limiting requests right now."
        assert call[3]["details"] == "HTTP 403"
    else:
        assert mgr._automatic_startup_check_done is True
    assert mgr.is_checking is False


def test_trigger_worker_exception(mgr, sched, settings, notes, client, checker, modal, sync_threads):
    checker.raise_exc = ValueError("bad json")
    poll, _ = run_check(mgr, sched, manual=True)
    poll.fn()
    (call,) = notes.works[0].calls
    assert call[0] == "fail" and call[2] == "Something went wrong while checking."
    assert call[3]["details"] == "ValueError: bad json"


@pytest.mark.parametrize("manual", [True, False])
def test_trigger_poll_waits_then_times_out(mgr, sched, settings, notes, client, checker, held_threads, manual):
    poll, safety = run_check(mgr, sched, manual=manual)
    assert poll.fn() is True  # worker not finished yet
    assert safety.fn() is True
    for _ in range(148):
        poll.fn()
    assert poll.fn() is False  # 150th poll
    assert mgr.last_check_result == "error" and mgr.last_check_error == "The update check timed out"
    assert mgr.is_checking is False
    if manual:
        (call,) = notes.works[0].calls
        assert call[:2] == ("done", "The update check timed out") and call[3]["type"] == "warning"
    else:
        assert mgr._automatic_startup_check_done is True


def test_trigger_safety_timer_stop_error(mgr, sched, settings, notes, client, checker, held_threads, caplog):
    poll, safety = run_check(mgr, sched, manual=False)
    held_threads[0].target()  # worker finishes
    poll.deactivate_error = RuntimeError("already gone")
    with caplog.at_level(logging.DEBUG, logger=up.logger.name):
        assert safety.fn() is False
    assert "Error stopping timer: already gone" in caplog.text


def test_trigger_poll_exception(mgr, sched, settings, notes, client, checker, sync_threads, monkeypatch):
    poll, _ = run_check(mgr, sched, manual=True)

    def boom(*a, **k):
        raise RuntimeError("record failed")

    monkeypatch.setattr(mgr, "_record_check", boom)
    assert poll.fn() is False
    (call,) = notes.works[0].calls
    assert call[0] == "fail" and call[3]["details"] == "RuntimeError: record failed"
    assert mgr.is_checking is False


def test_trigger_poll_exception_auto(mgr, sched, settings, notes, client, checker, sync_threads, monkeypatch):
    poll, _ = run_check(mgr, sched, manual=False)
    monkeypatch.setattr(mgr, "_record_check", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert poll.fn() is False and mgr.is_checking is False


def test_trigger_setup_exception_fails_work(mgr, settings, notes, client, checker, sync_threads, monkeypatch):
    monkeypatch.setattr(up, "app_schedule", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no loop")))
    mgr.trigger_check_and_prompt(manual=True)
    (call,) = notes.works[0].calls
    assert call[0] == "fail" and call[3]["details"] == "RuntimeError: no loop"
    assert mgr.is_checking is False


def test_trigger_setup_exception_without_work(mgr, settings, notes, client, monkeypatch):
    settings.error = RuntimeError("state broken")
    mgr.trigger_check_and_prompt(manual=False)
    assert notes.works == [] and mgr.is_checking is False


# --------------------------------------------------------------------------- modal / deferral

def test_show_update_modal_with_client(mgr, client, monkeypatch):
    built = []
    client.client = client.make()
    monkeypatch.setattr(mgr, "_build_update_modal", lambda info, manual=False: built.append((info, manual)))
    mgr._show_update_modal(INFO, manual=True)
    assert built == [(INFO, True)] and client.make.entered == 1


def test_show_update_modal_build_error(mgr, client, monkeypatch, caplog):
    client.client = client.make()
    monkeypatch.setattr(mgr, "_build_update_modal", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ui")))
    with caplog.at_level(logging.ERROR, logger=up.logger.name):
        mgr._show_update_modal(INFO)
    assert "_show_update_modal error: ui" in caplog.text


@pytest.fixture
def desktop(monkeypatch):
    ns = SimpleNamespace(notified=[], tray=[], tray_error=None)
    sn = ModuleType("modules.system_notify")
    sn.notify_async = lambda message, *, title="": ns.notified.append((message, title))
    tc = ModuleType("modules.tray_controller")

    def set_tray_update_available(flag):
        if ns.tray_error:
            raise ns.tray_error
        ns.tray.append(flag)

    tc.set_tray_update_available = set_tray_update_available
    monkeypatch.setitem(sys.modules, "modules.system_notify", sn)
    monkeypatch.setitem(sys.modules, "modules.tray_controller", tc)
    return ns


def test_show_update_modal_without_client_defers(mgr, client, desktop):
    mgr._show_update_modal(INFO)
    assert mgr._pending_prompt is INFO
    assert desktop.notified == [("Mycelian 2.0.0 is available. Open Mycelian to update.", "Update available")]
    assert desktop.tray == [True]
    mgr._defer_update_prompt({"latest_version": "3"})  # already pending: ignored
    assert mgr._pending_prompt is INFO and len(desktop.notified) == 1


def test_defer_update_prompt_tray_error(mgr, desktop):
    desktop.tray_error = RuntimeError("no tray")
    mgr._defer_update_prompt({})
    assert desktop.notified == [("Mycelian ? is available. Open Mycelian to update.", "Update available")]


def test_flush_pending_prompt_nothing(mgr, sched, desktop):
    mgr.flush_pending_prompt()
    assert sched == [] and desktop.tray == []


def test_flush_pending_prompt_waits_for_client(mgr, sched, desktop, client, monkeypatch):
    built = []
    monkeypatch.setattr(mgr, "_build_update_modal", lambda info, manual=False: built.append((info, manual)))
    mgr._pending_prompt = INFO
    mgr.flush_pending_prompt()
    assert desktop.tray == [False]
    (timer,) = sched
    assert timer.interval == 1.0
    timer.fn()  # no client yet
    assert built == [] and timer.deactivated == 0
    client.client = client.make()
    timer.fn()
    assert built == [(INFO, False)] and mgr._pending_prompt is None and timer.deactivated == 1


def test_flush_pending_prompt_gives_up(mgr, sched, desktop, client, caplog):
    desktop.tray_error = RuntimeError("tray gone")
    mgr._pending_prompt = INFO
    mgr.flush_pending_prompt()
    timer = sched[0]
    timer.deactivate_error = RuntimeError("x")
    with caplog.at_level(logging.INFO, logger=up.logger.name):
        for _ in range(30):
            timer.fn()
    assert timer.deactivated == 1
    assert "gave up waiting for a UI" in caplog.text
    timer.fn()  # timer slot cleared: no second deactivate
    assert timer.deactivated == 1
    assert mgr._pending_prompt is INFO


def test_flush_pending_prompt_build_error(mgr, sched, desktop, client, monkeypatch, caplog):
    client.client = client.make()
    monkeypatch.setattr(mgr, "_build_update_modal", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ui")))
    mgr._pending_prompt = INFO
    mgr.flush_pending_prompt()
    with caplog.at_level(logging.ERROR, logger=up.logger.name):
        sched[0].fn()
    assert "flush_pending_prompt error: ui" in caplog.text


# --------------------------------------------------------------------------- update window (UpdateDialog is faked)

class FakeDialog:
    """Records every view the manager asks for."""

    def __init__(self, on_dismiss=None):
        self.on_dismiss = on_dismiss
        self.calls = []
        self.gone = False
        self.client = contextlib.nullcontext()
        self.sub_label = SimpleNamespace(set_text=lambda text: self.calls.append(("sub", text)))

    @property
    def is_gone(self):
        return self.gone

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return record

    def dismiss(self):
        self.calls.append(("dismiss",))
        self.on_dismiss()

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


@pytest.fixture
def dialogs(monkeypatch):
    from modules.uiwindows import update_dialog

    made = []

    def factory(on_dismiss):
        d = FakeDialog(on_dismiss)
        made.append(d)
        return d

    monkeypatch.setattr(update_dialog, "UpdateDialog", factory)
    return made


@pytest.fixture
def flow(mgr, monkeypatch):
    calls = []
    monkeypatch.setattr(mgr, "_start_sync_flow", lambda *a, **k: calls.append((a, k)))
    return calls


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(update_sync, "can_apply_updates", lambda: True)


def test_current_version(mgr, settings):
    assert mgr._current_version() == "1.0.0"
    settings.error = RuntimeError("db")
    assert mgr._current_version() == ""


def test_build_update_modal_shows_offer(mgr, settings, dialogs, notes, monkeypatch):
    begun = []
    monkeypatch.setattr(mgr, "_begin_install", lambda d, release, **k: begun.append((d, release, k)))
    mgr._build_update_modal(INFO, manual=True)
    (d,) = dialogs
    (offer,) = d.named("show_offer")
    assert offer[1] == (INFO, "1.0.0") and d.named("open")
    assert mgr._dialog_open is True
    offer[2]["on_update"]()
    assert begun == [(d, INFO, {})]
    offer[2]["on_later"]()
    assert mgr._dialog_open is False and mgr._session_suppressed is False  # manual: not suppressed


def test_build_update_modal_later_suppresses_automatic_prompts(mgr, settings, dialogs, notes):
    t = FakeTimer(1, None)
    mgr._periodic_timer = t
    mgr._build_update_modal(INFO, manual=False)
    dialogs[0].named("show_offer")[0][2]["on_later"]()
    assert mgr._session_suppressed is True and t.deactivated == 1 and mgr._dialog_open is False


def test_build_update_modal_skips_when_open(mgr, dialogs):
    mgr._dialog_open = True
    mgr._build_update_modal(INFO)
    assert dialogs == []


def test_build_update_modal_failure_notifies(mgr, notes, monkeypatch):
    from modules.uiwindows import update_dialog

    monkeypatch.setattr(update_dialog, "UpdateDialog", lambda on_dismiss: (_ for _ in ()).throw(RuntimeError("ui")))
    mgr._build_update_modal(INFO)
    mgr._build_update_modal({})
    assert mgr._dialog_open is False
    (msg, kw), (msg2, _) = notes.notifications
    assert msg.startswith("Mycelian 2.0.0 is available") and kw["details"] == "RuntimeError: ui"
    assert msg2.startswith("Mycelian update is available")


# --------------------------------------------------------------------------- _begin_install

def test_begin_install_from_source_checkout(mgr, notes, flow, monkeypatch):
    monkeypatch.setattr(update_sync, "can_apply_updates", lambda: False)
    d = FakeDialog()
    mgr._begin_install(d, INFO)
    (msg, kw), = notes.notifications
    assert "runs from source" in msg and kw["actions"][0]["text"] == INFO["release_url"]
    assert d.calls == [] and flow == []
    mgr._begin_install(d, {"latest_version": "2"})
    assert notes.notifications[1][1]["actions"] is None


def test_begin_install_without_commit_or_tag(mgr, notes, flow, frozen):
    d = FakeDialog()
    mgr._begin_install(d, {"latest_version": "2", "commit_sha": "main", "release_url": "https://r"})
    (msg, kw), = notes.notifications
    assert msg == "This release can't be installed automatically." and kw["actions"][0]["text"] == "https://r"
    mgr._begin_install(d, {"latest_version": "2"})
    assert notes.notifications[1][1]["actions"] is None and flow == []


def test_begin_install_starts_progress_and_sync(mgr, notes, flow, frozen):
    d = FakeDialog()
    mgr._begin_install(d, {"latest_version": "1.14.0", "tag_name": "v1.14.0"}, downgrade=True)
    (progress,) = d.named("show_progress")
    assert progress[1] == ("1.14.0",) and progress[2]["downgrade"] is True
    ((args, kwargs),) = flow
    assert args[0] is d and kwargs["downgrade"] is True
    cancel_event = kwargs["cancel_event"]
    progress[2]["on_cancel"]()
    assert cancel_event.is_set() and ("sub", "Cancelling…") in d.calls
    d.sub_label = None
    progress[2]["on_cancel"]()  # a missing label is ignored


def test_begin_install_start_failure_notifies(mgr, notes, frozen, monkeypatch):
    monkeypatch.setattr(mgr, "_start_sync_flow", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("thread")))
    mgr._begin_install(FakeDialog(), INFO)
    (msg, kw), = notes.notifications
    assert msg == "The update couldn't start. Mycelian is unchanged." and kw["details"] == "RuntimeError: thread"


# --------------------------------------------------------------------------- _start_sync_flow

@pytest.fixture
def syncer(monkeypatch):
    ns = SimpleNamespace(result=None, exc=None, state=None, kwargs=None, resolved=[], resolve_to=SHA)

    async def fake_sync_release(**kwargs):
        ns.kwargs = kwargs
        ns.state = kwargs["state"]
        if ns.exc:
            raise ns.exc
        return ns.result

    async def fake_resolve(tag):
        ns.resolved.append(tag)
        return ns.resolve_to

    monkeypatch.setattr(update_sync, "sync_release", fake_sync_release)
    monkeypatch.setattr(up, "resolve_release_commit", fake_resolve)
    return ns


@pytest.fixture
def finish(monkeypatch):
    ns = SimpleNamespace(pids=[], error=None)

    def fake_finish(pid):
        ns.pids.append(pid)
        if ns.error:
            raise ns.error

    monkeypatch.setattr(up, "finish_update_and_exit", fake_finish)
    return ns


def start_flow(mgr, dialog, release=INFO, cancel_event=None, downgrade=False):
    mgr._start_sync_flow(dialog, release, cancel_event=cancel_event or threading.Event(), downgrade=downgrade)


@pytest.mark.parametrize(
    "result, exc, cancel_set, status, error",
    [
        (4242, None, False, "ready", None),
        (None, None, False, "current", None),
        (None, None, True, "cancelled", "Update cancelled."),
        (None, update_sync.UpdateCancelled("Stopped by user."), False, "cancelled", "Stopped by user."),
        (None, RuntimeError("disk full"), False, "failed", "disk full"),
        (None, RuntimeError(), False, "failed", "RuntimeError"),
    ],
)
def test_sync_flow_worker_outcomes(mgr, sched, syncer, sync_threads, client, result, exc, cancel_set, status, error):
    syncer.result, syncer.exc = result, exc
    ev = threading.Event()
    if cancel_set:
        ev.set()
    start_flow(mgr, FakeDialog(), cancel_event=ev)
    state = syncer.state
    assert state["status"] == status and state["error"] == error and state["done"]
    assert syncer.kwargs["commit_sha"] == SHA and syncer.kwargs["expected_version"] == "2.0.0"
    assert syncer.resolved == []  # the release already had its commit


def test_sync_flow_resolves_tag_for_older_release(mgr, sched, syncer, sync_threads, client):
    release = {"latest_version": "1.14.0", "tag_name": "v1.14.0"}
    start_flow(mgr, FakeDialog(), release, downgrade=True)
    assert syncer.resolved == ["v1.14.0"] and syncer.kwargs["commit_sha"] == SHA
    syncer.resolve_to, syncer.kwargs, syncer.state = None, None, None
    start_flow(mgr, FakeDialog(), release, downgrade=True)
    assert syncer.kwargs is None  # never downloaded
    assert syncer.state is None


def test_sync_flow_unresolvable_tag_fails(mgr, sched, syncer, sync_threads, client):
    syncer.resolve_to = None
    d = FakeDialog()
    start_flow(mgr, d, {"latest_version": "1.14.0", "tag_name": "v1.14.0"})
    sched[0].fn()
    (failed,) = d.named("show_failed")
    assert "couldn't find the files for Mycelian 1.14.0" in failed[1][1]


def test_sync_flow_starts_at_step_one(mgr, sched, held_threads, client):
    d = FakeDialog()
    start_flow(mgr, d)
    client.client = object()
    sched[0].fn()
    ((_, (state,), _),) = d.named("render_progress")
    assert state["step_index"] == 1 and state["label"] == "Finding Mycelian 2.0.0 on GitHub..."
    assert sched[0].deactivated == 0


def test_sync_flow_progress_skipped_without_ui(mgr, sched, held_threads, client):
    d = FakeDialog()
    start_flow(mgr, d)
    sched[0].fn()  # no client
    client.client = object()
    d.gone = True
    sched[0].fn()  # window gone
    assert d.named("render_progress") == []


def test_sync_flow_failed_shows_retry(mgr, sched, syncer, sync_threads, client, settings, monkeypatch):
    syncer.exc = RuntimeError("disk full")
    d = FakeDialog()
    start_flow(mgr, d, downgrade=True)
    sched[0].fn()
    assert sched[0].deactivated == 1
    ((_, (state, message, current), kwargs),) = d.named("show_failed")
    assert message == "disk full" and current == "1.0.0" and kwargs["details"] == "disk full"
    begun = []
    monkeypatch.setattr(mgr, "_begin_install", lambda dialog, release, **k: begun.append((dialog, release, k)))
    kwargs["on_retry"]()
    assert begun == [(d, INFO, {"downgrade": True})]


def test_sync_flow_failed_after_window_closed_notifies(mgr, sched, syncer, sync_threads, client, notes):
    syncer.exc = RuntimeError("boom")
    d = FakeDialog()
    start_flow(mgr, d)
    mgr._dialog_open = True
    d.gone = True
    sched[0].fn()
    assert mgr._dialog_open is False
    (msg, kw), = notes.notifications
    assert msg == "Something went wrong while downloading the update." and kw["details"] == "boom"


@pytest.mark.parametrize("downgrade, title", [(False, "Already up to date"), (True, "Already on 2.0.0")])
def test_sync_flow_current(mgr, sched, syncer, sync_threads, client, downgrade, title):
    d = FakeDialog()
    start_flow(mgr, d, downgrade=downgrade)
    sched[0].fn()
    ((_, args, kwargs),) = d.named("show_message")
    assert args[0] == title and kwargs["kind"] == "is-ok"


def test_sync_flow_cancelled(mgr, sched, syncer, sync_threads, client):
    syncer.exc = update_sync.UpdateCancelled("Update cancelled.")
    d = FakeDialog()
    start_flow(mgr, d)
    sched[0].fn()
    ((_, args, _),) = d.named("show_message")
    assert args == ("Update cancelled", "Nothing was changed.")


@pytest.mark.parametrize("status", ["current", "cancelled"])
def test_sync_flow_finished_after_window_closed(mgr, sched, syncer, sync_threads, client, notes, status):
    syncer.exc = update_sync.UpdateCancelled("x") if status == "cancelled" else None
    d = FakeDialog()
    start_flow(mgr, d)
    mgr._dialog_open = True
    d.gone = True
    sched[0].fn()
    assert mgr._dialog_open is False and notes.notifications == [] and d.named("show_message") == []


def test_sync_flow_ready_then_restart(mgr, sched, syncer, sync_threads, client, finish):
    syncer.result = 4242
    d = FakeDialog()
    start_flow(mgr, d)
    mgr._dialog_open = True
    sched[0].fn()
    assert d.named("render_progress") and d.named("show_restarting")
    launch = sched[-1]
    assert launch.interval == 0.6 and launch.once
    launch.fn()
    assert finish.pids == [4242] and mgr._dialog_open is False


def test_sync_flow_ready_with_window_gone(mgr, sched, syncer, sync_threads, client, finish):
    syncer.result = 4242
    d = FakeDialog()
    d.gone = True
    start_flow(mgr, d)
    sched[0].fn()
    assert d.named("show_restarting") == []
    sched[-1].fn()
    assert finish.pids == [4242]


def test_sync_flow_restart_failure_shown_in_window(mgr, sched, syncer, sync_threads, client, finish, settings):
    syncer.result = 4242
    finish.error = RuntimeError("no helper")
    d = FakeDialog()
    start_flow(mgr, d)
    sched[0].fn()
    sched[-1].fn()
    ((_, (_, message, _), _),) = d.named("show_failed")
    assert "couldn't restart to apply it. no helper" in message


def test_sync_flow_restart_failure_after_window_closed(mgr, sched, syncer, sync_threads, client, finish, notes,
                                                        monkeypatch):
    syncer.result = 4242
    finish.error = RuntimeError("no helper")
    d = FakeDialog()
    start_flow(mgr, d)
    sched[0].fn()
    d.gone = True
    mgr._dialog_open = True
    sched[-1].fn()
    assert mgr._dialog_open is False
    assert "couldn't restart to apply it" in notes.notifications[0][1]["details"]

    d2 = FakeDialog()
    d2.show_failed = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ui gone"))
    start_flow(mgr, d2)
    sched[-1].fn()
    sched[-1].fn()
    assert notes.notifications[-1][1]["details"] == "RuntimeError: no helper"


def test_sync_flow_unknown_status_stops_poll(mgr, sched, held_threads, client):
    d = FakeDialog()
    start_flow(mgr, d)
    poll = sched[0]
    state = None  # the worker never ran; reach its state through the closure
    for cell in held_threads[0].target.__closure__:
        if isinstance(cell.cell_contents, dict) and "status" in cell.cell_contents:
            state = cell.cell_contents
    state.update(done=True, status="working")
    poll.fn()
    assert poll.deactivated == 1 and d.named("show_message") == [] and d.named("show_failed") == []


def test_sync_flow_poll_errors_are_logged(mgr, sched, held_threads, client, caplog):
    d = FakeDialog()
    d.render_progress = lambda state: (_ for _ in ()).throw(RuntimeError("render"))
    start_flow(mgr, d)
    client.client = object()
    with caplog.at_level(logging.ERROR, logger=up.logger.name):
        sched[0].fn()
    assert "poll_update error: render" in caplog.text and sched[0].deactivated == 0


def test_sync_flow_stop_poll_tolerates_timer_errors(mgr, sched, syncer, sync_threads, client):
    d = FakeDialog()
    start_flow(mgr, d)
    poll = sched[0]
    poll.active_error = RuntimeError("x")
    poll.deactivate_error = RuntimeError("y")
    poll.fn()
    assert poll.deactivated == 1
    poll.fn()  # the timer slot is cleared: nothing else is shown
    assert len(d.named("show_message")) == 2


def test_sync_flow_setup_error_logged(mgr, monkeypatch, caplog):
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no threads")))
    with caplog.at_level(logging.ERROR, logger=up.logger.name):
        start_flow(mgr, FakeDialog())
    assert "_start_sync_flow error: no threads" in caplog.text


# --------------------------------------------------------------------------- older versions

@pytest.fixture
def history(monkeypatch):
    ns = SimpleNamespace(releases=[{"latest_version": v} for v in ("1.16.0", "1.15.0", "1.14.0")], error=None)

    async def fake_history():
        if ns.error:
            raise ns.error
        return ns.releases

    monkeypatch.setattr(up, "fetch_release_history", fake_history)
    return ns


def test_show_older_versions_lists_older_releases(mgr, sched, settings, dialogs, history, sync_threads, monkeypatch):
    settings.value.version = "1.16.0"
    assert mgr.show_older_versions() is True
    (d,) = dialogs
    assert d.named("show_loading") and d.named("open") and mgr._dialog_open is True
    sched[0].fn()
    assert sched[0].deactivated == 1
    ((_, (current, choices), kwargs),) = d.named("show_picker")
    assert current == "1.16.0" and [r["latest_version"] for r in choices] == ["1.15.0", "1.14.0"]
    begun = []
    monkeypatch.setattr(mgr, "_begin_install", lambda dialog, release, **k: begun.append((dialog, release, k)))
    kwargs["on_install"](choices[1])
    assert begun == [(d, choices[1], {"downgrade": True})]
    kwargs["on_cancel"]()
    assert mgr._dialog_open is False


def test_show_older_versions_waits_for_github(mgr, sched, settings, dialogs, history, held_threads):
    mgr.show_older_versions()
    sched[0].fn()
    assert sched[0].deactivated == 0 and dialogs[0].named("show_picker") == []


def test_show_older_versions_error(mgr, sched, settings, dialogs, history, sync_threads):
    history.error = RuntimeError("HTTP 403")
    mgr.show_older_versions()
    sched[0].fn()
    ((_, args, kwargs),) = dialogs[0].named("show_message")
    assert args[0] == "Couldn't load older versions" and kwargs["kind"] == "is-error"


def test_show_older_versions_cancelled_or_closed(mgr, sched, settings, dialogs, history, held_threads):
    mgr.show_older_versions()
    d = dialogs[0]
    d.named("show_loading")[0][2]["on_cancel"]()
    held_threads[0].target()
    sched[0].fn()
    assert d.named("show_picker") == [] and mgr._dialog_open is False

    mgr.show_older_versions()
    d2 = dialogs[1]
    d2.gone = True
    held_threads[1].target()
    sched[-1].fn()
    assert d2.named("show_picker") == []


def test_show_older_versions_picker_error_logged(mgr, sched, settings, dialogs, history, sync_threads, caplog):
    mgr.show_older_versions()
    dialogs[0].show_picker = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ui"))
    with caplog.at_level(logging.ERROR, logger=up.logger.name):
        sched[0].fn()
    assert "older versions window error: ui" in caplog.text


def test_show_older_versions_refused_while_open(mgr, client, dialogs):
    client.client = object()
    mgr._dialog_open = True
    assert mgr.show_older_versions() is False and dialogs == []


def test_show_older_versions_window_error(mgr, settings, monkeypatch, caplog):
    from modules.uiwindows import update_dialog

    monkeypatch.setattr(update_dialog, "UpdateDialog", lambda on_dismiss: (_ for _ in ()).throw(RuntimeError("ui")))
    with caplog.at_level(logging.ERROR, logger=up.logger.name):
        assert mgr.show_older_versions() is False
    assert mgr._dialog_open is False and "show_older_versions error: ui" in caplog.text
