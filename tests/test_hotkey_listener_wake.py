"""RT-14: hotkey listener sleeps until woken; register/unregister/stop still apply at once."""

from __future__ import annotations

import sys
import time
import types

from modules import hotkey_listener as hl


def _fake_pynput(monkeypatch):
    kb = types.ModuleType("pynput.keyboard")

    class HotKey:
        def __init__(self, keys, cb):
            self.keys = keys
            self.cb = cb

        def press(self, key):
            pass

        def release(self, key):
            pass

    class Listener:
        def __init__(self, on_press=None, on_release=None):
            self.stopped = False

        def start(self):
            pass

        def stop(self):
            self.stopped = True

        def canonical(self, key):
            return key

    kb.HotKey = HotKey
    kb.Listener = Listener
    pkg = types.ModuleType("pynput")
    pkg.keyboard = kb
    monkeypatch.setitem(sys.modules, "pynput", pkg)
    monkeypatch.setitem(sys.modules, "pynput.keyboard", kb)


def _wait_for(cond, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.005)
    return False


def test_register_unregister_and_stop_apply_promptly(monkeypatch):
    _fake_pynput(monkeypatch)
    monkeypatch.setattr(hl.HotkeyListener, "_parse_key_combination", lambda self, c: [c])
    listener = hl.HotkeyListener()
    listener.start()
    try:
        listener.register_hotkey("c1", "ctrl+k")
        assert _wait_for(lambda: [h.keys for h in listener._pynput_hotkeys] == [["ctrl+k"]])
        listener.register_hotkey("c2", "ctrl+j")
        assert _wait_for(lambda: len(listener._pynput_hotkeys) == 2)
        listener.unregister_hotkey("c1")
        assert _wait_for(lambda: [h.keys for h in listener._pynput_hotkeys] == [["ctrl+j"]])
    finally:
        t0 = time.monotonic()
        listener.stop()
        assert time.monotonic() - t0 < 0.5
        assert not listener.listener_thread.is_alive()
