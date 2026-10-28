# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for pause_alerts.py (standalone alert pause toggle).

The Socket.IO client is always faked; no connection is ever made.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pause_alerts as pa  # noqa: E402


class FakeClient:
    instances: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.connected = False
        self.calls: list = []
        self.connect_error: Exception | None = None
        self.emit_error: Exception | None = None
        self.disconnect_error: Exception | None = None
        self.connect_succeeds_then_fails = False
        FakeClient.instances.append(self)
        if FakeClient.configure:
            FakeClient.configure(self)

    configure = None

    def connect(self, url, wait_timeout=None):
        self.calls.append(("connect", url, wait_timeout))
        if self.connect_error:
            raise self.connect_error
        self.connected = True

    def emit(self, event, *args):
        self.calls.append(("emit", event))
        if self.emit_error:
            raise self.emit_error

    def disconnect(self):
        self.calls.append(("disconnect",))
        if self.disconnect_error:
            raise self.disconnect_error
        self.connected = False


@pytest.fixture
def fake_sio(monkeypatch):
    FakeClient.instances = []
    FakeClient.configure = None
    sleeps: list = []
    monkeypatch.setattr(pa.socketio, "Client", FakeClient)
    monkeypatch.setattr(pa.time, "sleep", lambda s: sleeps.append(s))
    yield sleeps
    FakeClient.configure = None


def test_toggle_pause_success(fake_sio):
    assert pa.toggle_pause("10.0.0.5", 6000, timeout=3) is True
    client = FakeClient.instances[0]
    assert client.kwargs == {
        "reconnection": False,
        "reconnection_attempts": 0,
        "logger": False,
        "engineio_logger": False,
    }
    assert client.calls == [("connect", "http://10.0.0.5:6000", 3), ("emit", "pause_alerts"), ("disconnect",)]
    assert fake_sio == [0.5]


def test_toggle_pause_defaults(fake_sio):
    assert pa.toggle_pause() is True
    assert FakeClient.instances[0].calls[0] == ("connect", "http://127.0.0.1:5000", 5)


def test_toggle_pause_connect_failure_returns_false_without_disconnect(fake_sio):
    FakeClient.configure = lambda c: setattr(c, "connect_error", ConnectionError("refused"))
    assert pa.toggle_pause() is False
    client = FakeClient.instances[0]
    assert ("disconnect",) not in client.calls
    assert fake_sio == []


def test_toggle_pause_emit_failure_still_disconnects(fake_sio):
    FakeClient.configure = lambda c: setattr(c, "emit_error", RuntimeError("bad"))
    assert pa.toggle_pause() is False
    assert FakeClient.instances[0].calls[-1] == ("disconnect",)


def test_toggle_pause_disconnect_error_is_swallowed(fake_sio):
    FakeClient.configure = lambda c: setattr(c, "disconnect_error", RuntimeError("socket gone"))
    assert pa.toggle_pause() is True


@pytest.mark.parametrize("ok,code", [(True, 0), (False, 1)])
def test_main_exit_codes(monkeypatch, ok, code):
    seen = {}

    def fake_toggle(host, port, timeout):
        seen.update(host=host, port=port, timeout=timeout)
        return ok

    monkeypatch.setattr(pa, "toggle_pause", fake_toggle)
    monkeypatch.setattr(sys, "argv", ["pause_alerts.py"])
    with pytest.raises(SystemExit) as exc:
        pa.main()
    assert exc.value.code == code
    assert seen == {"host": "127.0.0.1", "port": 5000, "timeout": 5}


def test_main_parses_arguments(monkeypatch):
    seen = {}
    monkeypatch.setattr(pa, "toggle_pause", lambda **kw: seen.update(kw) or True)
    monkeypatch.setattr(sys, "argv", ["pause_alerts.py", "--host", "box", "--port", "7001", "--timeout", "9"])
    with pytest.raises(SystemExit) as exc:
        pa.main()
    assert exc.value.code == 0
    assert seen == {"host": "box", "port": 7001, "timeout": 9}


def test_main_rejects_non_integer_port(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pause_alerts.py", "--port", "abc"])
    with pytest.raises(SystemExit) as exc:
        pa.main()
    assert exc.value.code == 2
    assert "invalid int value" in capsys.readouterr().err


def test_missing_socketio_exits_with_message(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "socketio", None)
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(ROOT / "pause_alerts.py"), run_name="pause_alerts_no_socketio")
    assert exc.value.code == 1
    assert "python-socketio is required" in capsys.readouterr().out


def test_script_entry_point_runs_main(monkeypatch):
    import socketio

    FakeClient.instances = []
    FakeClient.configure = lambda c: setattr(c, "connect_error", ConnectionError("refused"))
    monkeypatch.setattr(socketio, "Client", FakeClient)
    monkeypatch.setattr(sys, "argv", ["pause_alerts.py", "--port", "1"])
    try:
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(ROOT / "pause_alerts.py"), run_name="__main__")
    finally:
        FakeClient.configure = None
    assert exc.value.code == 1
    assert FakeClient.instances[0].calls == [("connect", "http://127.0.0.1:1", 5)]
