# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""hotkey_listener: notices, macOS permission check, combo normalizing, register / unregister,
the pynput and keyboard-library listener loops (fake libraries, no real keyboard hook),
key parsing, press dispatch and module functions."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio
import types
from types import SimpleNamespace

import pytest

import modules
import modules.connector_integration  # noqa: F401  (imported before notification_engine is faked)
import modules.connector_manager  # noqa: F401
from modules import hotkey_listener as hl


def fake_module(monkeypatch, name, **attrs):
    mod = types.ModuleType(f"modules.{name}")
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, f"modules.{name}", mod)
    monkeypatch.setattr(modules, name, mod, raising=False)
    return mod


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(hl, "_notified", set())
    notes = []
    fake_module(monkeypatch, "notification_engine", notify=lambda msg, **kw: notes.append((msg, kw)),
                notify_critical=lambda msg, **kw: notes.append((msg, kw)),
                nav_actions_settings=lambda tab: [], nav_actions_main_tab=lambda tab: [("nav", tab)])
    # Never touch the real macOS accessibility API
    monkeypatch.setattr(hl, "_mac_accessibility_trusted", lambda: None)
    return notes


@pytest.fixture
def notes(isolated):
    return isolated


# --------------------------------------------------------------------------- fake pynput


class FakeKeyCode:
    def __init__(self, char):
        self.char = char

    @classmethod
    def from_char(cls, char):
        if char in ("bad", "x-fail"):
            raise ValueError("no such key")
        return cls(char)

    def __eq__(self, other):
        return isinstance(other, FakeKeyCode) and other.char == self.char

    def __hash__(self):
        return hash(("kc", self.char))

    def __repr__(self):
        return f"KC({self.char!r})"


class FakeHotKey:
    def __init__(self, keys, callback):
        self.keys, self.callback = keys, callback
        self.pressed, self.released = [], []

    def press(self, key):
        if key == "boom":
            raise RuntimeError("press failed")
        self.pressed.append(key)

    def release(self, key):
        if key == "boom":
            raise RuntimeError("release failed")
        self.released.append(key)


KEY_NAMES = ["ctrl", "alt", "shift", "cmd", "tab", "enter", "space", "esc", "backspace", "delete", "up",
             "down", "left", "right", "home", "end", "page_up", "page_down", "insert", "caps_lock"] + [
    f"f{i}" for i in range(1, 16)]


def make_pynput(monkeypatch, script=None, listener_error=None, key_names=KEY_NAMES):
    kb = types.ModuleType("pynput.keyboard")
    kb.Key = SimpleNamespace(**{n: f"Key.{n}" for n in key_names})
    kb.KeyCode = FakeKeyCode
    kb.HotKey = FakeHotKey
    state = SimpleNamespace(listeners=[])

    class Listener:
        def __init__(self, on_press=None, on_release=None):
            if listener_error:
                raise listener_error
            self.on_press, self.on_release = on_press, on_release
            self.stopped = False
            state.listeners.append(self)

        def canonical(self, key):
            return key

        def start(self):
            if script:
                script(self)

        def stop(self):
            self.stopped = True

    kb.Listener = Listener
    pkg = types.ModuleType("pynput")
    pkg.keyboard = kb
    monkeypatch.setitem(sys.modules, "pynput", pkg)
    monkeypatch.setitem(sys.modules, "pynput.keyboard", kb)
    return state


class ScriptedWake:
    """Stands in for the listener's threading.Event; wait() runs the next step."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.sets = 0

    def wait(self, timeout=None):
        if self.steps:
            self.steps.pop(0)()
        return True

    def clear(self):
        pass

    def set(self):
        self.sets += 1


# --------------------------------------------------------------------------- notices / permission


def test_notify_hotkeys_once_per_key(notes):
    hl._notify_hotkeys("k", "T", "M", "H", level="error", details="D")
    hl._notify_hotkeys("k", "T", "M", "H")
    assert len(notes) == 1
    msg, kw = notes[0]
    assert msg == "M" and kw["type"] == "error" and kw["dedupe_key"] == "hotkeys:k"
    assert kw["details"] == "D" and kw["actions"] == [("nav", "Connectors")]


def test_notify_hotkeys_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    fake_module(monkeypatch, "notification_engine", notify=boom, nav_actions_main_tab=lambda t: [])
    hl._notify_hotkeys("k2", "T", "M", "H")
    assert "k2" in hl._notified


def test_mac_accessibility_trusted(monkeypatch):
    monkeypatch.undo()  # use the real function
    monkeypatch.setattr(hl, "_notified", set())
    real = hl._mac_accessibility_trusted
    monkeypatch.setattr(hl.sys, "platform", "linux")
    assert real() is None
    monkeypatch.setattr(hl.sys, "platform", "darwin")
    fake = types.ModuleType("HIServices")
    fake.AXIsProcessTrusted = lambda: 0
    monkeypatch.setitem(sys.modules, "HIServices", fake)
    assert real() is False
    fake.AXIsProcessTrusted = lambda: 1
    assert real() is True
    monkeypatch.setitem(sys.modules, "HIServices", None)
    assert real() is None


def test_check_mac_permission(monkeypatch, notes):
    monkeypatch.setattr(hl, "_mac_accessibility_trusted", lambda: True)
    hl._check_mac_permission()
    assert notes == []
    monkeypatch.setattr(hl, "_mac_accessibility_trusted", lambda: False)
    hl._check_mac_permission()
    hl._check_mac_permission()
    assert len(notes) == 1 and notes[0][1]["dedupe_key"] == "hotkeys:mac_permission"


# --------------------------------------------------------------------------- normalizing


@pytest.mark.parametrize(
    "char,mods,expected",
    [("ƒ", {"alt"}, "f"), ("ƒ", set(), "ƒ"), ("!", {"shift"}, "1"), ("!", None, "!"), ("A", (), "a"),
     ("ab", (), "ab")],
)
def test_base_key_for_char(char, mods, expected):
    assert hl.base_key_for_char(char, mods) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [(None, ""), ("+", "plus"), ("ctrl+shift", "ctrl+shift"), ("Alt_GR + del", "alt+delete"),
     ("os+PgUp", "cmd+page_up"), ("ctrl+A", "ctrl+a"), ("F12", "f12"), ("ctrl+a+b", "ctrl+b"),
     ("ctrl++a", "ctrl+a"), ("ctrl+ +", "ctrl+space")],
)
def test_normalize_hotkey_combo_more(raw, expected):
    assert hl.normalize_hotkey_combo(raw) == expected


# --------------------------------------------------------------------------- register / unregister


def test_register_and_unregister():
    lis = hl.HotkeyListener()
    lis._wake.clear()
    lis.register_hotkey("c1", "")  # ignored
    assert lis.hotkey_mappings == {}
    lis.register_hotkey("c1", "Ctrl+K")
    lis.register_hotkey("c1", "ctrl+k")  # duplicate id
    lis.register_hotkey("c2", "ctrl+k")
    lis.register_hotkey("c1", "f5")
    assert lis.get_registered_hotkeys() == {"ctrl+k": ["c1", "c2"], "f5": ["c1"]}
    assert lis._wake.is_set() and lis._hotkeys_dirty
    lis.unregister_hotkey("c9", "ctrl+k")  # not mapped
    lis.unregister_hotkey("c1", "alt+z")  # unknown combo
    lis.unregister_hotkey("c2", "CTRL+K")
    assert lis.hotkey_mappings == {"ctrl+k": ["c1"], "f5": ["c1"]}
    lis.unregister_hotkey("c1", "ctrl+k")
    assert lis.hotkey_mappings == {"f5": ["c1"]}
    lis.register_hotkey("c3", "f5")
    lis.unregister_hotkey("c1")
    assert lis.hotkey_mappings == {"f5": ["c3"]}
    lis.unregister_hotkey("c3")
    assert lis.hotkey_mappings == {}


def test_register_and_unregister_errors(monkeypatch):
    lis = hl.HotkeyListener()

    def boom(c):
        raise RuntimeError("x")

    monkeypatch.setattr(hl, "normalize_hotkey_combo", boom)
    lis.register_hotkey("c", "a")
    lis.unregister_hotkey("c", "a")
    assert lis.hotkey_mappings == {}


def test_register_reports_unavailable_once_hotkeys_exist(notes):
    lis = hl.HotkeyListener()
    lis._report_unavailable()  # nothing to report
    lis._unavailable = ("unavailable", "Title", "Msg", "Hint", None)
    lis._report_unavailable()  # no hotkeys yet
    assert notes == []
    lis.register_hotkey("c1", "f1")
    assert notes[0][1]["title"] == "Title" and notes[0][1]["type"] == "error"


# --------------------------------------------------------------------------- start / stop / run


class SyncThread:
    def __init__(self, target=None, daemon=None):
        self.target, self.daemon = target, daemon
        self.joined = None

    def start(self):
        self.target()

    def join(self, timeout=None):
        self.joined = timeout


def test_start_and_stop(monkeypatch):
    lis = hl.HotkeyListener()
    runs = []
    monkeypatch.setattr(lis, "_run_listener", lambda: runs.append(1))
    monkeypatch.setattr(hl.threading, "Thread", SyncThread)
    lis.start()
    assert runs == [1] and lis.listener_thread.daemon is True and lis.is_running
    lis.start()  # already running
    assert runs == [1]
    lis.stop()
    assert not lis.is_running and lis._wake.is_set() and lis.listener_thread.joined == 2.0
    lis.stop()  # not running
    lis.is_running = True
    lis.listener_thread = None
    lis.stop()


def test_run_listener_prefers_pynput(monkeypatch):
    make_pynput(monkeypatch)
    lis = hl.HotkeyListener()
    lis.is_running = True
    calls = []
    monkeypatch.setattr(lis, "_run_pynput_listener", lambda: calls.append("pynput"))
    lis._run_listener()
    assert calls == ["pynput"] and not lis.is_running


def test_run_listener_falls_back_to_keyboard(monkeypatch):
    monkeypatch.setitem(sys.modules, "pynput", None)
    monkeypatch.setitem(sys.modules, "keyboard", types.ModuleType("keyboard"))
    lis = hl.HotkeyListener()
    calls = []
    monkeypatch.setattr(lis, "_run_keyboard_listener", lambda: calls.append("keyboard"))
    lis._run_listener()
    assert calls == ["keyboard"]


def test_run_listener_without_libraries(monkeypatch, notes):
    monkeypatch.setitem(sys.modules, "pynput", None)
    monkeypatch.setitem(sys.modules, "keyboard", None)
    lis = hl.HotkeyListener()
    lis.hotkey_mappings = {"f1": ["c"]}
    lis._run_listener()
    assert lis._unavailable[0] == "unavailable"
    assert notes[0][1]["title"] == "Hotkeys aren't available"


def test_run_listener_crash_reported(monkeypatch, notes):
    make_pynput(monkeypatch)
    lis = hl.HotkeyListener()
    lis.hotkey_mappings = {"f1": ["c"]}

    def boom():
        raise RuntimeError("hook died")

    monkeypatch.setattr(lis, "_run_pynput_listener", boom)
    lis._run_listener()
    assert lis._unavailable[0] == "listener_crash" and "RuntimeError: hook died" in lis._unavailable[4]
    assert notes[0][1]["title"] == "Hotkeys stopped working"


# --------------------------------------------------------------------------- pynput loop


def test_pynput_listener_presses_and_rebuilds(monkeypatch, notes):
    pressed = []
    lis = hl.HotkeyListener()
    monkeypatch.setattr(lis, "_on_hotkey_pressed", lambda combo: pressed.append(combo))
    checks = []
    monkeypatch.setattr(hl, "_check_mac_permission", lambda: checks.append(1))
    lis.hotkey_mappings = {"alt+f": ["c1"], "bad": ["c2"]}
    lis.is_running = True

    def script(listener):
        lis_hotkeys = lis._pynput_hotkeys
        assert [h.keys for h in lis_hotkeys] == [["Key.alt", FakeKeyCode("f")]]
        # Option+F on macOS reports "ƒ"; it is mapped back to "f" while alt is held.
        listener.on_press("Key.alt")
        listener.on_press(FakeKeyCode("ƒ"))
        listener.on_press(["unhashable"])
        listener.on_press("boom")
        hk = lis._pynput_hotkeys[0]
        assert hk.pressed == ["Key.alt", FakeKeyCode("f"), ["unhashable"]]
        hk.callback()
        listener.on_release(FakeKeyCode("ƒ"))
        listener.on_release("Key.alt")
        listener.on_release(["unhashable"])
        listener.on_release("boom")
        assert hk.released == [FakeKeyCode("f"), "Key.alt", ["unhashable"]]
        # after release, "ƒ" stays as typed (alt no longer held)
        listener.on_press(FakeKeyCode("ƒ"))
        assert hk.pressed[-1] == FakeKeyCode("ƒ")
        # a mapping change marks hotkeys dirty; the next key press rebuilds them
        lis.register_hotkey("c3", "ctrl+x-fail")
        lis.register_hotkey("c4", "f2")
        listener.on_press("Key.ctrl")

    state = make_pynput(monkeypatch, script=script)
    lis._wake = ScriptedWake([lambda: setattr(lis, "is_running", False)])
    lis._run_pynput_listener()
    assert pressed == ["alt+f"] and state.listeners[0].stopped
    assert sorted(h.keys[0] for h in lis._pynput_hotkeys) == ["Key.alt", "Key.f2"]
    assert checks
    titles = [n[1]["title"] for n in notes]
    assert "Hotkey “bad” can't be used" in titles
    assert "Hotkey “ctrl+x-fail” can't be used" in titles


def test_pynput_initial_build_when_not_dirty(monkeypatch):
    lis = hl.HotkeyListener()
    lis.hotkey_mappings = {"f4": ["c"]}
    lis._hotkeys_dirty = False
    lis.is_running = False
    make_pynput(monkeypatch)
    lis._run_pynput_listener()
    assert [h.keys for h in lis._pynput_hotkeys] == [["Key.f4"]] and lis._hotkeys_dirty is False


def test_pynput_loop_rebuilds_when_woken_dirty(monkeypatch):
    lis = hl.HotkeyListener()
    lis.is_running = True
    make_pynput(monkeypatch)

    def change():
        lis.hotkey_mappings["f3"] = ["c"]
        lis._hotkeys_dirty = True

    lis._wake = ScriptedWake([change, lambda: setattr(lis, "is_running", False)])
    lis._run_pynput_listener()
    assert [h.keys for h in lis._pynput_hotkeys] == [["Key.f3"]]


def test_pynput_canonical_keeps_char_when_keycode_fails(monkeypatch):
    lis = hl.HotkeyListener()
    lis.hotkey_mappings = {"shift+1": ["c"]}
    lis.is_running = True

    def script(listener):
        monkeypatch.setattr(FakeKeyCode, "from_char", classmethod(lambda cls, c: (_ for _ in ()).throw(ValueError())))
        listener.on_press("Key.shift")
        listener.on_press(FakeKeyCode("!"))
        assert lis._pynput_hotkeys[0].pressed[-1] == FakeKeyCode("!")

    make_pynput(monkeypatch, script=script)
    lis._wake = ScriptedWake([lambda: setattr(lis, "is_running", False)])
    lis._run_pynput_listener()


def test_pynput_without_key_enum_and_rebuild_error(monkeypatch, notes):
    lis = hl.HotkeyListener()
    lis.hotkey_mappings = {"f1": ["c"]}
    lis.is_running = False
    state = make_pynput(monkeypatch)
    sys.modules["pynput.keyboard"].Key = None

    def boom(combo):
        raise RuntimeError("parse exploded")

    monkeypatch.setattr(lis, "_parse_key_combination", boom)
    lis._run_pynput_listener()
    assert state.listeners[0].stopped
    assert notes[0][1]["details"] == "RuntimeError: parse exploded"


def test_pynput_listener_failure_reports_crash(monkeypatch, notes):
    lis = hl.HotkeyListener()
    lis.hotkey_mappings = {"f1": ["c"]}
    make_pynput(monkeypatch, listener_error=OSError("no access"))
    lis._run_pynput_listener()
    assert lis._unavailable[0] == "listener_crash"


# --------------------------------------------------------------------------- keyboard-library loop


def make_keyboard(monkeypatch, fail_add=(), fail_remove=False):
    kb = types.ModuleType("keyboard")
    kb.added, kb.removed = [], []

    def add_hotkey(combo, cb, args=None):
        if combo in fail_add:
            raise ValueError("taken")
        kb.added.append((combo, cb, args))

    def remove_hotkey(combo):
        if fail_remove:
            raise KeyError(combo)
        kb.removed.append(combo)

    kb.add_hotkey, kb.remove_hotkey = add_hotkey, remove_hotkey
    monkeypatch.setitem(sys.modules, "keyboard", kb)
    return kb


def test_keyboard_listener_registers_and_reregisters(monkeypatch, notes):
    kb = make_keyboard(monkeypatch, fail_add=("ctrl+page up",))
    lis = hl.HotkeyListener()
    lis.hotkey_mappings = {"f1": ["c"], "ctrl+page_up": ["d"]}
    lis.is_running = True
    steps = [
        lambda: None,  # no change: nothing re-registered
        lambda: lis.hotkey_mappings.update({"f2": ["e"]}),
        lambda: setattr(lis, "is_running", False),
    ]
    lis._wake = ScriptedWake(steps)
    lis._run_keyboard_listener()
    assert [a[0] for a in kb.added] == ["f1", "f1", "f2"]
    assert kb.added[0][2] == ["f1"] and kb.added[0][1] == lis._on_hotkey_pressed
    assert kb.removed == ["f1", "f1", "f2"]
    assert notes[0][1]["hint"].startswith("Another app may already use it")


def test_keyboard_listener_remove_errors_ignored(monkeypatch):
    make_keyboard(monkeypatch, fail_remove=True)
    lis = hl.HotkeyListener()
    lis.hotkey_mappings = {"f1": ["c"]}
    lis.is_running = True
    lis._wake = ScriptedWake([lambda: lis.hotkey_mappings.update({"f2": ["d"]}),
                              lambda: setattr(lis, "is_running", False)])
    lis._run_keyboard_listener()


def test_keyboard_listener_crash(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyboard", None)
    lis = hl.HotkeyListener()
    lis.is_running = True
    lis._run_keyboard_listener()
    assert lis._unavailable[0] == "listener_crash"


# --------------------------------------------------------------------------- key parsing


def test_parse_key_combination(monkeypatch):
    make_pynput(monkeypatch)
    lis = hl.HotkeyListener()
    assert lis._parse_key_combination("Ctrl+Shift+F15") == ["Key.ctrl", "Key.shift", "Key.f15"]
    assert lis._parse_key_combination("ctrl++") == ["Key.ctrl", FakeKeyCode("+")]
    assert lis._parse_key_combination("alt+caps_lock") == ["Key.alt", "Key.caps_lock"]
    assert lis._parse_key_combination("ctrl+weird") == ["Key.ctrl", FakeKeyCode("weird")]
    assert lis._parse_key_combination("ctrl+bad") == []


def test_parse_key_combination_without_insert_and_import_error(monkeypatch):
    make_pynput(monkeypatch, key_names=[n for n in KEY_NAMES if n != "insert"])
    lis = hl.HotkeyListener()
    assert lis._parse_key_combination("insert") == [FakeKeyCode("insert")]
    monkeypatch.setitem(sys.modules, "pynput.keyboard", None)
    assert lis._parse_key_combination("f1") == []


# --------------------------------------------------------------------------- press dispatch


class Loop:
    def __init__(self, running=True, closed=False):
        self.running, self.closed = running, closed

    def is_running(self):
        return self.running

    def is_closed(self):
        return self.closed


def _dispatch_env(monkeypatch, loop):
    import modules.connector_integration as ci
    import modules.connector_manager as cm

    sent = []

    async def add_event(event):
        sent.append(event)

    monkeypatch.setattr(cm, "get_connector_loop", lambda: loop)
    monkeypatch.setattr(ci, "get_integration", lambda: SimpleNamespace(manager=SimpleNamespace(add_event=add_event)))

    def fake_run(coro, lp):
        asyncio.run(coro)

    monkeypatch.setattr(hl.asyncio, "run_coroutine_threadsafe", fake_run)
    return sent


def test_on_hotkey_pressed_paths(monkeypatch):
    sent = _dispatch_env(monkeypatch, Loop())
    lis = hl.HotkeyListener()
    lis._on_hotkey_pressed(None)  # nothing pressed
    lis._current_hotkey = "f1"
    lis._on_hotkey_pressed()  # f1 not registered
    lis.register_hotkey("c1", "f1")
    lis._on_hotkey_pressed()
    assert sent[0]["combo"] == "f1" and sent[0]["key_code"] == "f1" and sent[0]["modifiers"] == []
    for loop in (None, Loop(running=False), Loop(closed=True)):
        _dispatch_env(monkeypatch, loop)
        lis._on_hotkey_pressed("f1")
    assert len(sent) == 1


def test_on_hotkey_pressed_errors(monkeypatch):
    lis = hl.HotkeyListener()
    lis.register_hotkey("c1", "f1")
    import modules.connector_integration as ci

    def boom():
        raise RuntimeError("no integration")

    monkeypatch.setattr(ci, "get_integration", boom)
    lis._on_hotkey_pressed("f1")

    def bad_norm(c):
        raise RuntimeError("x")

    monkeypatch.setattr(hl, "normalize_hotkey_combo", bad_norm)
    lis._on_hotkey_pressed("f1")


def test_key_press_release_noops():
    lis = hl.HotkeyListener()
    assert lis._on_key_press("a") is None and lis._on_key_release("a") is None


# --------------------------------------------------------------------------- module helpers


def test_notify_invalid_hotkey(notes):
    hl._notify_invalid_hotkey("ctrl+q", "D")
    hl._notify_invalid_hotkey("ctrl+w", maybe_taken=True)
    assert notes[0][1]["hint"] == "Open the connector and record the hotkey again."
    assert "Another app" in notes[1][1]["hint"] and notes[0][1]["details"] == "D"


def test_build_hotkey_event(monkeypatch):
    monkeypatch.setattr(hl.time, "time", lambda: 3.0)
    assert hl.build_hotkey_event("Shift+Ctrl+K") == {
        "event_type": "hotkey", "combo": "ctrl+shift+k", "key_code": "k", "modifiers": ["ctrl", "shift"],
        "is_global": True, "timestamp": 3.0, "source": "hotkey"}
    empty = hl.build_hotkey_event("")
    assert empty["combo"] == "" and empty["key_code"] == "" and empty["modifiers"] == []


def test_keyboard_lib_combo(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert hl._keyboard_lib_combo("cmd+page_up") == "command+page up"
    monkeypatch.setattr(sys, "platform", "win32")
    assert hl._keyboard_lib_combo("cmd+page_down") == "windows+page down"
    assert hl._keyboard_lib_combo("caps_lock") == "caps lock"


def test_initialize_cleanup_get_listener(monkeypatch):
    calls = []
    fake = SimpleNamespace(start=lambda: calls.append("start"), stop=lambda: calls.append("stop"))
    monkeypatch.setattr(hl, "hotkey_listener", fake)
    hl.initialize()
    hl.cleanup()
    assert calls == ["start", "stop"] and hl.get_listener() is fake

    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(hl, "hotkey_listener", SimpleNamespace(start=boom, stop=boom))
    hl.initialize()
    hl.cleanup()
