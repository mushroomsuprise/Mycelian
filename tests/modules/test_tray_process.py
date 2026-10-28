# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.tray_process (tray child: pystray and Qt backends, all faked)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import tray_process as tp


# --------------------------------------------------------------------------- shared fakes

class FakeConn:
    """Pipe end: ``recv`` replays scripted messages then raises EOFError."""

    def __init__(self, incoming=(), send_error=None, recv_error=EOFError):
        self.incoming = list(incoming)
        self.sent = []
        self.send_error = send_error
        self.recv_error = recv_error

    def send(self, payload):
        if self.send_error:
            raise self.send_error
        self.sent.append(payload)

    def recv(self):
        if self.incoming:
            return self.incoming.pop(0)
        raise self.recv_error()


class SyncThread:
    """Runs the target when started, on the calling thread."""

    started = []

    def __init__(self, target=None, name=None, daemon=None, args=()):
        self.target, self.name, self.daemon, self.args = target, name, daemon, args

    def start(self):
        SyncThread.started.append(self.name)
        self.target(*self.args)


@pytest.fixture(autouse=True)
def sync_threads(monkeypatch):
    SyncThread.started = []
    monkeypatch.setattr(tp.threading, "Thread", SyncThread)
    return SyncThread


# --------------------------------------------------------------------------- small helpers

@pytest.mark.parametrize("err", [OSError, BrokenPipeError, ValueError])
def test_send_swallows_pipe_errors(err):
    conn = FakeConn(send_error=err("x"))
    tp._send(conn, {"a": 1})  # no raise


def test_send_delivers():
    conn = FakeConn()
    tp._send(conn, {"action": "ready"})
    assert conn.sent == [{"action": "ready"}]


def test_send_other_errors_propagate():
    with pytest.raises(RuntimeError):
        tp._send(FakeConn(send_error=RuntimeError("bug")), {})


def test_activation_restores_names():
    for name in ("Trigger", "DoubleClick", "MiddleClick"):
        assert tp.activation_restores(SimpleNamespace(name=name))
    assert not tp.activation_restores(SimpleNamespace(name="Context"))
    assert not tp.activation_restores(SimpleNamespace(name=3))
    assert not tp.activation_restores(object())


def test_macos_policy_skipped_off_darwin(monkeypatch):
    monkeypatch.setattr(tp.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "AppKit", None)  # would raise if imported
    tp._set_macos_accessory_policy()


def test_macos_policy_sets_accessory(monkeypatch):
    calls = []
    shared = SimpleNamespace(setActivationPolicy_=lambda v: calls.append(v))
    appkit = ModuleType("AppKit")
    appkit.NSApplication = SimpleNamespace(sharedApplication=lambda: shared)
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setattr(tp.sys, "platform", "darwin")
    tp._set_macos_accessory_policy()
    assert calls == [1]


def test_macos_policy_import_error_swallowed(monkeypatch):
    monkeypatch.setitem(sys.modules, "AppKit", None)
    monkeypatch.setattr(tp.sys, "platform", "darwin")
    tp._set_macos_accessory_policy()


def test_load_icon_image_converts_and_resizes(tmp_path):
    from PIL import Image

    p = tmp_path / "icon.png"
    Image.new("RGB", (16, 16), "red").save(p)
    img = tp._load_icon_image(str(p))
    assert img.mode == "RGBA" and img.size == (64, 64)
    p2 = tmp_path / "icon2.png"
    Image.new("RGBA", (128, 128)).save(p2)
    img2 = tp._load_icon_image(str(p2))
    assert img2.mode == "RGBA" and img2.size == (64, 64)


def test_load_icon_image_missing_raises(tmp_path):
    with pytest.raises(Exception):
        tp._load_icon_image(str(tmp_path / "missing.png"))


# --------------------------------------------------------------------------- pystray backend

class FakeMenuItem:
    def __init__(self, text, action, default=False, visible=True):
        self.text, self.action, self.default, self.visible = text, action, default, visible


class FakeMenu:
    SEPARATOR = "SEP"

    def __init__(self, *items):
        self.items = list(items)


class FakeIcon:
    instances = []

    def __init__(self, name, image, title, menu):
        self.name, self.image, self.title, self.menu = name, image, title, menu
        self.visible = False
        self.stopped = 0
        self.menu_updates = 0
        self.notifications = []
        self.fail = set()
        self.on_run = None
        FakeIcon.instances.append(self)

    def update_menu(self):
        self.menu_updates += 1
        if "update_menu" in self.fail:
            raise RuntimeError("no menu")

    def notify(self, message, title):
        if "notify" in self.fail:
            raise RuntimeError("no notify")
        self.notifications.append((message, title))

    def stop(self):
        self.stopped += 1
        if "stop" in self.fail:
            raise RuntimeError("no stop")

    def run(self, setup):
        if self.on_run:
            self.on_run(self)
        setup(self)


@pytest.fixture
def fake_pystray(monkeypatch):
    FakeIcon.instances = []
    mod = ModuleType("pystray")
    mod.Menu, mod.MenuItem, mod.Icon = FakeMenu, FakeMenuItem, FakeIcon
    monkeypatch.setitem(sys.modules, "pystray", mod)
    monkeypatch.setattr(tp, "_load_icon_image", lambda path: f"image:{path}")
    policy = []
    monkeypatch.setattr(tp, "_set_macos_accessory_policy", lambda: policy.append(True))
    return SimpleNamespace(mod=mod, policy=policy)


def items_by_text(icon):
    return {i.text: i for i in icon.menu.items if isinstance(i, FakeMenuItem)}


def test_pystray_builds_menu_and_reports_ready(fake_pystray, sync_threads):
    conn = FakeConn()
    tp._run_pystray(conn, "icon.png", True)
    (icon,) = FakeIcon.instances
    assert icon.name == "Mycelian" and icon.title == "Mycelian" and icon.image == "image:icon.png"
    assert icon.visible is True
    assert fake_pystray.policy == [True]
    assert icon.menu.items[2] == FakeMenu.SEPARATOR
    items = items_by_text(icon)
    assert items["Restore Mycelian"].default is True
    assert items["Update available"].visible(None) is False
    assert sync_threads.started == ["mycelian-tray-reader"]
    # reader drained (EOF) and stopped the icon before setup sent "ready"
    assert icon.stopped == 1
    assert conn.sent == [{"action": "ready"}]


def test_pystray_menu_callbacks(fake_pystray):
    conn = FakeConn()
    tp._run_pystray(conn, "icon.png", False)
    icon = FakeIcon.instances[0]
    items = items_by_text(icon)
    conn.sent.clear()
    items["Restore Mycelian"].action()
    items["Update available"].action(icon, None)
    items["Quit Mycelian"].action(icon)
    assert conn.sent == [{"action": "restore"}, {"action": "restore"}, {"action": "quit"}]
    assert icon.stopped == 2


def test_pystray_reader_commands(fake_pystray):
    conn = FakeConn([
        "not a dict",
        {"cmd": "set_state", "minimized": True},
        {"cmd": "set_update", "available": 1},
        {"cmd": "notify", "title": "T", "message": "M"},
        {"cmd": "notify"},
        {"cmd": "unknown"},
        {"cmd": "stop"},
        {"cmd": "set_update", "available": False},  # never read
    ])
    tp._run_pystray(conn, "icon.png", False)
    icon = FakeIcon.instances[0]
    assert icon.menu_updates == 2
    assert items_by_text(icon)["Update available"].visible(None) is True
    assert icon.notifications == [("M", "T"), ("", "Mycelian")]
    assert icon.stopped == 1
    assert len(conn.incoming) == 1


def test_pystray_reader_swallows_icon_errors(fake_pystray):
    conn = FakeConn([
        {"cmd": "set_state"},
        {"cmd": "set_update"},
        {"cmd": "notify", "title": "x", "message": "y"},
    ], recv_error=OSError)

    def arm(icon):
        icon.fail.update({"update_menu", "notify", "stop"})

    FakeIcon.instances = []
    orig_init = FakeIcon.__init__

    def init(self, *a):
        orig_init(self, *a)
        self.on_run = arm

    FakeIcon.__init__ = init
    try:
        tp._run_pystray(conn, "icon.png", False)
    finally:
        FakeIcon.__init__ = orig_init
    icon = FakeIcon.instances[0]
    assert icon.menu_updates == 2 and icon.notifications == [] and icon.stopped == 1
    assert conn.sent == [{"action": "ready"}]


def test_pystray_unreadable_icon(fake_pystray, monkeypatch):
    def bad(path):
        raise OSError("cannot identify image")

    monkeypatch.setattr(tp, "_load_icon_image", bad)
    conn = FakeConn()
    tp._run_pystray(conn, "x.png", False)
    assert conn.sent == [{"action": "unavailable", "error": "tray icon unreadable: cannot identify image"}]
    assert FakeIcon.instances == []


# --------------------------------------------------------------------------- Qt backend

class FakeSignal:
    def __init__(self, *types):
        self.handlers = []

    def connect(self, handler, *conn_type):
        self.handlers.append((handler, conn_type))

    def emit(self, *args):
        for handler, _ in self.handlers:
            handler(*args)


class FakeQObject:
    pass


class FakeQApplication:
    instances = []

    def __init__(self, argv):
        self.argv = argv
        self.name = None
        self.quit_on_last = None
        self.quits = 0
        self.exec_calls = 0
        FakeQApplication.instances.append(self)

    def setApplicationName(self, name):
        self.name = name

    def setQuitOnLastWindowClosed(self, flag):
        self.quit_on_last = flag

    def quit(self):
        self.quits += 1

    def exec(self):
        self.exec_calls += 1
        return 0


class FakeQIcon:
    null = False

    def __init__(self, path):
        self.path = path

    def isNull(self):
        return FakeQIcon.null


class FakeQAction:
    def __init__(self, text, parent):
        self.text, self.parent = text, parent
        self.visible = True
        self.triggered = FakeSignal()

    def setVisible(self, flag):
        self.visible = flag


class FakeQMenu:
    def __init__(self):
        self.entries = []

    def addAction(self, action):
        self.entries.append(action)

    def addSeparator(self):
        self.entries.append("SEP")


class FakeTrayIcon:
    available = True
    instances = []
    message_error = None

    def __init__(self, icon):
        self.icon = icon
        self.tooltip = None
        self.menu = None
        self.shown = 0
        self.hidden = 0
        self.messages = []
        self.activated = FakeSignal()
        FakeTrayIcon.instances.append(self)

    @staticmethod
    def isSystemTrayAvailable():
        return FakeTrayIcon.available

    def setToolTip(self, text):
        self.tooltip = text

    def setContextMenu(self, menu):
        assert self.shown == 0, "menu must be attached before show()"
        self.menu = menu

    def show(self):
        self.shown += 1

    def hide(self):
        self.hidden += 1

    def showMessage(self, title, message):
        if FakeTrayIcon.message_error:
            raise FakeTrayIcon.message_error
        self.messages.append((title, message))


@pytest.fixture
def fake_qt(monkeypatch, tmp_path):
    FakeQApplication.instances = []
    FakeTrayIcon.instances = []
    FakeTrayIcon.available = True
    FakeTrayIcon.message_error = None
    FakeQIcon.null = False
    pkg = ModuleType("PyQt6")
    core = ModuleType("PyQt6.QtCore")
    core.QObject = FakeQObject
    core.pyqtSignal = FakeSignal
    core.Qt = SimpleNamespace(ConnectionType=SimpleNamespace(QueuedConnection="queued"))
    gui = ModuleType("PyQt6.QtGui")
    gui.QAction, gui.QIcon = FakeQAction, FakeQIcon
    widgets = ModuleType("PyQt6.QtWidgets")
    widgets.QApplication, widgets.QMenu, widgets.QSystemTrayIcon = FakeQApplication, FakeQMenu, FakeTrayIcon
    pkg.QtCore, pkg.QtGui, pkg.QtWidgets = core, gui, widgets
    for name, mod in {"PyQt6": pkg, "PyQt6.QtCore": core, "PyQt6.QtGui": gui, "PyQt6.QtWidgets": widgets}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"png")
    return str(icon)


def test_qt_import_failure_reports_unavailable(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "PyQt6", None)
    monkeypatch.setitem(sys.modules, "PyQt6.QtCore", None)
    conn = FakeConn()
    tp._run_qt_tray(conn, str(tmp_path / "i.png"), False)
    assert conn.sent[0]["action"] == "unavailable"
    assert conn.sent[0]["error"].startswith("Qt tray unavailable:")


def test_qt_missing_icon_file(fake_qt, tmp_path):
    conn = FakeConn()
    tp._run_qt_tray(conn, str(tmp_path / "nope.png"), False)
    assert conn.sent == [{"action": "unavailable", "error": "tray icon unreadable: missing file"}]
    assert FakeQApplication.instances == []


def test_qt_no_system_tray(fake_qt):
    FakeTrayIcon.available = False
    conn = FakeConn()
    tp._run_qt_tray(conn, fake_qt, False)
    assert conn.sent == [{"action": "unavailable", "error": "no system tray available"}]
    app = FakeQApplication.instances[0]
    assert app.argv == ["Mycelian"] and app.name == "Mycelian" and app.quit_on_last is False


def test_qt_null_icon(fake_qt):
    FakeQIcon.null = True
    conn = FakeConn()
    tp._run_qt_tray(conn, fake_qt, False)
    assert conn.sent == [{"action": "unavailable", "error": "tray icon unreadable: empty image"}]
    assert FakeTrayIcon.instances == []


def run_qt(fake_qt, incoming=(), recv_error=EOFError):
    conn = FakeConn(incoming, recv_error=recv_error)
    tp._run_qt_tray(conn, fake_qt, False)
    return conn, FakeTrayIcon.instances[0], FakeQApplication.instances[0]


def test_qt_builds_tray_and_runs(fake_qt, sync_threads):
    conn, tray, app = run_qt(fake_qt)
    assert tray.tooltip == "Mycelian"
    restore, update, sep, quit_ = tray.menu.entries
    assert (restore.text, update.text, sep, quit_.text) == ("Restore Mycelian", "Update available", "SEP",
                                                           "Quit Mycelian")
    assert update.visible is False
    assert tray._mycelian_menu is tray.menu
    assert tray._mycelian_commands.received.handlers[0][1] == ("queued",)
    assert sync_threads.started == ["mycelian-tray-reader"]
    # The reader hit EOF immediately (parent gone) and emitted stop before show().
    assert tray.hidden == 1 and app.quits == 1
    assert tray.shown == 1 and app.exec_calls == 1
    assert conn.sent == [{"action": "ready"}]


def test_qt_menu_and_activation_callbacks(fake_qt):
    conn, tray, app = run_qt(fake_qt, recv_error=OSError)
    restore, update, _sep, quit_ = tray.menu.entries
    conn.sent.clear()
    restore.triggered.emit(True)
    update.triggered.emit()
    tray.activated.emit(SimpleNamespace(name="Trigger"))
    tray.activated.emit(SimpleNamespace(name="Context"))
    assert conn.sent == [{"action": "restore"}] * 3
    hidden, quits = tray.hidden, app.quits
    quit_.triggered.emit(False)
    assert conn.sent[-1] == {"action": "quit"}
    assert tray.hidden == hidden + 1 and app.quits == quits + 1


def test_qt_reader_and_command_handler(fake_qt):
    FakeTrayIcon.message_error = None
    conn, tray, app = run_qt(fake_qt, [
        42,
        {"cmd": "set_update", "available": True},
        {"cmd": "notify", "title": "Hi", "message": "There"},
        {"cmd": "notify"},
        {"cmd": "set_state", "minimized": True},
        {"cmd": "stop"},
        {"cmd": "set_update", "available": False},  # never read
    ])
    update = tray.menu.entries[1]
    assert update.visible is True
    assert tray.messages == [("Hi", "There"), ("Mycelian", "")]
    assert tray.hidden == 1 and app.quits == 1
    assert len(conn.incoming) == 1
    handler = tray._mycelian_commands.received.handlers[0][0]
    handler("not a dict")  # ignored
    handler({"cmd": "set_update"})
    assert update.visible is False


def test_qt_notify_errors_swallowed(fake_qt):
    FakeTrayIcon.message_error = RuntimeError("dbus")
    conn, tray, app = run_qt(fake_qt, [{"cmd": "notify", "title": "a", "message": "b"}])
    assert tray.messages == []
    assert conn.sent == [{"action": "ready"}]


@pytest.mark.parametrize("platform, backend", [("linux", "qt"), ("darwin", "pystray"), ("win32", "pystray")])
def test_run_tray_dispatches(monkeypatch, platform, backend):
    calls = []
    monkeypatch.setattr(tp.sys, "platform", platform)
    monkeypatch.setattr(tp, "_run_qt_tray", lambda *a: calls.append(("qt", a)))
    monkeypatch.setattr(tp, "_run_pystray", lambda *a: calls.append(("pystray", a)))
    tp.run_tray("conn", "i.png", True)
    assert calls == [(backend, ("conn", "i.png", True))]
