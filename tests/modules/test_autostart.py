# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.autostart (per-platform login items, all redirected to tmp_path / fakes)."""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import autostart


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch, tmp_path):
    """Every path the module could write resolves under tmp_path."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    exe = tmp_path / "bin" / "Mycelian"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    return str(exe)


class FakeKey:
    def __init__(self, reg):
        self.reg = reg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_winreg(monkeypatch):
    reg = {}
    calls = []
    mod = ModuleType("winreg")
    mod.HKEY_CURRENT_USER = "HKCU"
    mod.REG_SZ = 1
    mod.open_error = None

    def OpenKey(root, path):
        calls.append(("open", root, path))
        if mod.open_error:
            raise mod.open_error
        return FakeKey(reg)

    def CreateKey(root, path):
        calls.append(("create", root, path))
        return FakeKey(reg)

    def QueryValueEx(key, name):
        if name not in reg:
            raise FileNotFoundError(name)
        return reg[name], mod.REG_SZ

    def SetValueEx(key, name, reserved, kind, value):
        calls.append(("set", name, kind, value))
        reg[name] = value

    def DeleteValue(key, name):
        calls.append(("delete", name))
        if name not in reg:
            raise FileNotFoundError(name)
        del reg[name]

    mod.OpenKey, mod.CreateKey, mod.QueryValueEx = OpenKey, CreateKey, QueryValueEx
    mod.SetValueEx, mod.DeleteValue = SetValueEx, DeleteValue
    monkeypatch.setitem(sys.modules, "winreg", mod)
    return SimpleNamespace(mod=mod, reg=reg, calls=calls)


# ----- basics -----

def test_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert autostart.is_frozen() is False
    assert autostart.is_supported() is False
    assert autostart.get_executable_path() is None


@pytest.mark.parametrize(
    "platform, supported",
    [("darwin", True), ("win32", True), ("linux", True), ("linux2", True), ("freebsd13", False), ("cygwin", False)],
)
def test_is_supported_per_platform(frozen, monkeypatch, platform, supported):
    monkeypatch.setattr(sys, "platform", platform)
    assert autostart.is_frozen() is True
    assert autostart.is_supported() is supported


def test_get_executable_path_is_absolute(frozen):
    assert autostart.get_executable_path() == frozen


# ----- macOS -----

def test_launch_agent_path_under_home(_isolate_home):
    assert autostart._launch_agent_path() == _isolate_home / "Library" / "LaunchAgents" / "com.mycelian.app.plist"


def test_macos_roundtrip(_isolate_home):
    assert autostart._macos_is_enabled() is False
    assert autostart._macos_set_enabled(True, "/Apps/M") is True
    payload = plistlib.loads(autostart._launch_agent_path().read_bytes())
    assert payload == {
        "Label": "com.mycelian.app",
        "ProgramArguments": ["/Apps/M"],
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Interactive",
    }
    assert autostart._macos_is_enabled() is True
    assert autostart._macos_set_enabled(False, "x") is True
    assert autostart._macos_is_enabled() is False
    assert autostart._macos_set_enabled(False, "x") is True  # missing_ok


# ----- Windows -----

def test_windows_enable_query_disable(fake_winreg):
    assert autostart._windows_is_enabled() is False
    assert autostart._windows_set_enabled(True, r"C:\M\Mycelian.exe") is True
    assert fake_winreg.reg["Mycelian"] == r'"C:\M\Mycelian.exe"'
    assert ("create", "HKCU", autostart._WINDOWS_RUN_KEY) in fake_winreg.calls
    assert ("set", "Mycelian", 1, r'"C:\M\Mycelian.exe"') in fake_winreg.calls
    assert autostart._windows_is_enabled() is True
    assert autostart._windows_set_enabled(False, "x") is True
    assert "Mycelian" not in fake_winreg.reg
    assert autostart._windows_set_enabled(False, "x") is True  # already gone


def test_windows_empty_value_is_disabled(fake_winreg):
    fake_winreg.reg["Mycelian"] = ""
    assert autostart._windows_is_enabled() is False


@pytest.mark.parametrize("err", [FileNotFoundError("no key"), PermissionError("denied"), OSError("x")])
def test_windows_open_errors(fake_winreg, err):
    fake_winreg.mod.open_error = err
    assert autostart._windows_is_enabled() is False


# ----- Linux -----

def test_desktop_entry_path_default_and_xdg(monkeypatch, _isolate_home, tmp_path):
    assert autostart._desktop_entry_path() == _isolate_home / ".config" / "autostart" / "mycelian.desktop"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert autostart._desktop_entry_path() == tmp_path / "xdg" / "autostart" / "mycelian.desktop"
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    assert autostart._desktop_entry_path() == _isolate_home / ".config" / "autostart" / "mycelian.desktop"


def test_linux_roundtrip(_isolate_home):
    assert autostart._linux_is_enabled() is False
    assert autostart._linux_set_enabled(True, "/opt/M") is True
    text = autostart._desktop_entry_path().read_text(encoding="utf-8")
    assert 'Exec="/opt/M"' in text and "Name=Mycelian" in text and "Terminal=false" in text
    assert autostart._linux_is_enabled() is True
    assert autostart._linux_set_enabled(False, "x") is True
    assert autostart._linux_is_enabled() is False


# ----- public API -----

def test_is_enabled_unsupported(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(autostart, "_macos_is_enabled", lambda: pytest.fail("must not probe"))
    assert autostart.is_enabled() is False


@pytest.mark.parametrize(
    "platform, probe",
    [("darwin", "_macos_is_enabled"), ("win32", "_windows_is_enabled"), ("linux", "_linux_is_enabled")],
)
def test_is_enabled_dispatches(frozen, monkeypatch, platform, probe):
    monkeypatch.setattr(sys, "platform", platform)
    for name in ("_macos_is_enabled", "_windows_is_enabled", "_linux_is_enabled"):
        monkeypatch.setattr(autostart, name, (lambda n=name: n == probe))
    assert autostart.is_enabled() is True


def test_is_enabled_probe_error_logged(frozen, monkeypatch, caplog):
    monkeypatch.setattr(sys, "platform", "darwin")

    def boom():
        raise RuntimeError("bad plist")

    monkeypatch.setattr(autostart, "_macos_is_enabled", boom)
    with caplog.at_level("WARNING", logger=autostart.logger.name):
        assert autostart.is_enabled() is False
    assert "could not read the login item: bad plist" in caplog.text


def test_is_enabled_supported_but_unknown_branch(frozen, monkeypatch):
    # is_supported True but none of the dispatch branches match.
    monkeypatch.setattr(autostart, "is_supported", lambda: True)
    monkeypatch.setattr(sys, "platform", "aix")
    assert autostart.is_enabled() is False


def test_set_enabled_unsupported_logs_only_when_enabling(monkeypatch, caplog):
    monkeypatch.delattr(sys, "frozen", raising=False)
    with caplog.at_level("INFO", logger=autostart.logger.name):
        assert autostart.set_enabled(False) is False
        assert "not supported" not in caplog.text
        assert autostart.set_enabled(True) is False
    assert "not supported for this build" in caplog.text


def test_set_enabled_without_executable(frozen, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(autostart, "get_executable_path", lambda: None)
    monkeypatch.setattr(autostart, "_macos_set_enabled", lambda *a: pytest.fail("no write"))
    assert autostart.set_enabled(True) is False


@pytest.mark.parametrize(
    "platform, setter",
    [("darwin", "_macos_set_enabled"), ("win32", "_windows_set_enabled"), ("linux", "_linux_set_enabled")],
)
@pytest.mark.parametrize("enabled", [True, False])
def test_set_enabled_dispatches(frozen, monkeypatch, caplog, platform, setter, enabled):
    monkeypatch.setattr(sys, "platform", platform)
    calls = []
    for name in ("_macos_set_enabled", "_windows_set_enabled", "_linux_set_enabled"):
        monkeypatch.setattr(autostart, name, (lambda e, x, n=name: calls.append((n, e, x)) or True))
    with caplog.at_level("INFO", logger=autostart.logger.name):
        assert autostart.set_enabled(enabled) is True
    assert calls == [(setter, enabled, frozen)]
    assert ("registered" if enabled else "removed") in caplog.text


def test_set_enabled_unknown_platform(frozen, monkeypatch):
    monkeypatch.setattr(autostart, "is_supported", lambda: True)
    monkeypatch.setattr(sys, "platform", "aix")
    assert autostart.set_enabled(True) is False


def test_set_enabled_propagates_os_error(frozen, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    def boom(e, x):
        raise PermissionError("read-only")

    monkeypatch.setattr(autostart, "_linux_set_enabled", boom)
    with pytest.raises(PermissionError):
        autostart.set_enabled(True)


def test_set_enabled_real_macos_writes_under_tmp_home(frozen, monkeypatch, _isolate_home):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert autostart.set_enabled(True) is True
    assert autostart.is_enabled() is True
    plist = _isolate_home / "Library" / "LaunchAgents" / "com.mycelian.app.plist"
    assert plistlib.loads(plist.read_bytes())["ProgramArguments"] == [frozen]
    assert autostart.set_enabled(False) is True
    assert not plist.exists()


# ----- sync_with_settings -----

@pytest.fixture
def settings(monkeypatch):
    from modules import dataobjects

    holder = SimpleNamespace(value=SimpleNamespace(run_at_startup=False), error=None)

    def get_app_settings():
        if holder.error:
            raise holder.error
        return holder.value

    monkeypatch.setattr(dataobjects, "state_manager", SimpleNamespace(get_app_settings=get_app_settings))
    return holder


@pytest.fixture
def recorder(monkeypatch):
    rec = SimpleNamespace(enabled=False, sets=[])
    monkeypatch.setattr(autostart, "is_supported", lambda: True)
    monkeypatch.setattr(autostart, "is_enabled", lambda: rec.enabled)
    monkeypatch.setattr(autostart, "set_enabled", lambda v: rec.sets.append(v) or True)
    return rec


def test_sync_unsupported_does_nothing(monkeypatch, settings):
    monkeypatch.setattr(autostart, "is_supported", lambda: False)
    monkeypatch.setattr(autostart, "set_enabled", lambda v: pytest.fail("no change"))
    autostart.sync_with_settings()


@pytest.mark.parametrize(
    "wanted, current, expected",
    [(True, False, [True]), (False, True, [False]), (True, True, [True]), (False, False, [])],
)
def test_sync_matches_setting(settings, recorder, wanted, current, expected):
    settings.value = SimpleNamespace(run_at_startup=wanted)
    recorder.enabled = current
    autostart.sync_with_settings()
    assert recorder.sets == expected


def test_sync_missing_attribute_means_off(settings, recorder):
    settings.value = SimpleNamespace()
    recorder.enabled = True
    autostart.sync_with_settings()
    assert recorder.sets == [False]


def test_sync_settings_error_returns_quietly(settings, recorder):
    settings.error = RuntimeError("db locked")
    autostart.sync_with_settings()
    assert recorder.sets == []


def test_sync_set_error_logged(settings, monkeypatch, caplog):
    settings.value = SimpleNamespace(run_at_startup=True)
    monkeypatch.setattr(autostart, "is_supported", lambda: True)
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)

    def boom(v):
        raise OSError("denied")

    monkeypatch.setattr(autostart, "set_enabled", boom)
    with caplog.at_level("WARNING", logger=autostart.logger.name):
        autostart.sync_with_settings()
    assert "could not sync the login item: denied" in caplog.text
