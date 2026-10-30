# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/streamdeck_plugin_utils.py (plugin folder, install, versions, status, notices).

Stream Deck folders are faked under tmp_path; nothing is installed for real.
"""

from __future__ import annotations

import errno
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from packaging.version import InvalidVersion  # noqa: E402

from modules import streamdeck_plugin_utils as sd  # noqa: E402

S = sd.PluginInstallState
BUNDLE = sd.PLUGIN_BUNDLE_NAME


@pytest.fixture
def fake_os(tmp_path, monkeypatch):
    """macOS-style home under tmp_path and bundled plugin data under tmp_path/data."""
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "data"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Path.home() on Windows
    monkeypatch.setattr(sd.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sd, "get_data_path", lambda rel="": str(data / rel))
    plugins = home / "Library" / "Application Support" / "com.elgato.StreamDeck" / "Plugins"
    return SimpleNamespace(home=home, data=data, plugins=plugins, bundled=data / "sd_plugin" / BUNDLE)


def _manifest(folder: Path, version) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    body = {} if version is None else {"Version": version}
    (folder / "manifest.json").write_text(json.dumps(body), encoding="utf-8")


# ------------------------------------------------------------------ enqueue_streamdeck_connector_event


def _patch_connector(monkeypatch, loop, add_event=None):
    from modules import connector_integration, connector_manager

    monkeypatch.setattr(connector_manager, "get_connector_loop", lambda: loop)
    manager = SimpleNamespace(add_event=add_event or (lambda ev: ("coro", ev)))
    monkeypatch.setattr(connector_integration, "get_integration", lambda: SimpleNamespace(manager=manager))


def test_enqueue_skips_without_loop(monkeypatch):
    _patch_connector(monkeypatch, None)
    assert sd.enqueue_streamdeck_connector_event({"a": 1}) is False
    _patch_connector(monkeypatch, SimpleNamespace(is_running=lambda: False))
    assert sd.enqueue_streamdeck_connector_event({"a": 1}) is False


def test_enqueue_schedules_on_running_loop(monkeypatch):
    loop = SimpleNamespace(is_running=lambda: True)
    _patch_connector(monkeypatch, loop)
    calls = []
    monkeypatch.setattr(sd.asyncio, "run_coroutine_threadsafe", lambda coro, lp: calls.append((coro, lp)))
    assert sd.enqueue_streamdeck_connector_event({"a": 1}) is True
    assert calls == [(("coro", {"a": 1}), loop)]


def test_enqueue_failure_returns_false(monkeypatch):
    _patch_connector(monkeypatch, SimpleNamespace(is_running=lambda: True))

    def boom(coro, loop):
        raise RuntimeError("closed")

    monkeypatch.setattr(sd.asyncio, "run_coroutine_threadsafe", boom)
    assert sd.enqueue_streamdeck_connector_event({}) is False


# ------------------------------------------------------------------ small types


def test_plugin_install_error_user_message():
    assert sd.PluginInstallError("tech").user_message == "tech"
    err = sd.PluginInstallError("tech", user_message="friendly")
    assert str(err) == "tech" and err.user_message == "friendly"


@pytest.mark.parametrize("state, installed", [
    (S.UNAVAILABLE, False), (S.NOT_INSTALLED, False), (S.UP_TO_DATE, True), (S.OUTDATED, True), (S.UNKNOWN, True),
])
def test_status_is_installed(state, installed):
    assert sd.PluginStatus(state=state).is_installed is installed


# ------------------------------------------------------------------ plugin folder paths


def test_resolve_dir_macos(fake_os):
    assert sd.resolve_streamdeck_plugins_dir() == fake_os.plugins
    assert not fake_os.plugins.exists()
    assert sd.resolve_streamdeck_plugins_dir(create=True) == fake_os.plugins
    assert fake_os.plugins.is_dir()


def test_resolve_dir_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(sd.platform, "system", lambda: "Windows")
    monkeypatch.setattr(sd.os.path, "expandvars", lambda s: str(tmp_path / "Roaming") if s == "%appdata%" else s)
    assert sd.resolve_streamdeck_plugins_dir() == tmp_path / "Roaming" / "Elgato" / "StreamDeck" / "Plugins"


def test_resolve_dir_linux(monkeypatch):
    monkeypatch.setattr(sd.platform, "system", lambda: "Linux")
    assert sd.resolve_streamdeck_plugins_dir(create=True) is None
    assert sd.get_streamdeck_plugins_dir() is None


def test_get_plugins_dir_only_when_present(fake_os):
    assert sd.get_streamdeck_plugins_dir() is None
    fake_os.plugins.mkdir(parents=True)
    assert sd.get_streamdeck_plugins_dir() == fake_os.plugins


def test_bundled_and_installed_dirs(fake_os):
    assert sd.get_bundled_plugin_dir() == fake_os.bundled
    assert sd.get_installed_plugin_dir() is None
    fake_os.plugins.mkdir(parents=True)
    assert sd.get_installed_plugin_dir() is None
    (fake_os.plugins / BUNDLE).write_text("not a dir")
    assert sd.get_installed_plugin_dir() is None
    (fake_os.plugins / BUNDLE).unlink()
    (fake_os.plugins / BUNDLE).mkdir()
    assert sd.get_installed_plugin_dir() == fake_os.plugins / BUNDLE


# ------------------------------------------------------------------ permission helpers


@pytest.mark.parametrize("exc, denied", [
    (PermissionError("x"), True),
    (OSError(errno.EACCES, "x"), True),
    (OSError(errno.EPERM, "x"), True),
    (OSError(errno.ENOENT, "x"), False),
    (ValueError("x"), False),
])
def test_is_permission_denied(exc, denied):
    assert sd._is_permission_denied(exc) is denied


def test_is_permission_denied_plain_oserror_with_eacces_errno():
    # OSError(EACCES, ...) is promoted to PermissionError; a bare OSError whose errno
    # is set afterwards (as some libraries raise) takes the errno branch.
    exc = OSError("raw")
    exc.errno = errno.EACCES
    assert type(exc) is OSError
    assert sd._is_permission_denied(exc) is True


def test_chmod_writable(tmp_path, monkeypatch):
    f = tmp_path / "ro.txt"
    f.write_text("x")
    os.chmod(f, 0o444)
    sd._chmod_writable(str(f))
    assert os.stat(f).st_mode & 0o200
    sd._chmod_writable(str(tmp_path / "missing"))  # OSError swallowed


def test_rmtree_onerror_retries_after_chmod(monkeypatch):
    chmods, calls = [], []
    monkeypatch.setattr(sd, "_chmod_writable", chmods.append)
    sd._rmtree_onerror(calls.append, "/p", (None, PermissionError("ro"), None))
    assert chmods == ["/p"] and calls == ["/p"]


def test_rmtree_onerror_reraises_when_retry_fails(monkeypatch):
    monkeypatch.setattr(sd, "_chmod_writable", lambda p: None)
    err = PermissionError("ro")

    def still_locked(path):
        raise OSError("locked")

    with pytest.raises(PermissionError):
        sd._rmtree_onerror(still_locked, "/p", (None, err, None))


def test_rmtree_onerror_reraises_other_errors():
    with pytest.raises(FileNotFoundError):
        sd._rmtree_onerror(lambda p: pytest.fail("no retry"), "/p", (None, FileNotFoundError("gone"), None))


def test_remove_tree(tmp_path):
    sd._remove_tree(tmp_path / "missing")
    d = tmp_path / "d"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "f").write_text("x")
    sd._remove_tree(d)
    assert not d.exists()


# ------------------------------------------------------------------ _remove_plugin_directory


def test_remove_plugin_directory_missing_is_noop(tmp_path):
    sd._remove_plugin_directory(tmp_path / "nothing")


def test_remove_plugin_directory_plain(tmp_path):
    d = tmp_path / BUNDLE
    d.mkdir()
    sd._remove_plugin_directory(d)
    assert not d.exists()


def test_remove_plugin_directory_non_permission_error_propagates(tmp_path, monkeypatch):
    d = tmp_path / BUNDLE
    d.mkdir()

    def boom(path):
        raise OSError(errno.ENOSPC, "disk")

    monkeypatch.setattr(sd, "_remove_tree", boom)
    with pytest.raises(OSError):
        sd._remove_plugin_directory(d)


def test_remove_plugin_directory_renames_aside_when_locked(tmp_path, monkeypatch):
    d = tmp_path / BUNDLE
    d.mkdir()
    removed = []

    def remove(path):
        if path == d:
            raise PermissionError("locked")
        removed.append(path)

    monkeypatch.setattr(sd, "_remove_tree", remove)
    monkeypatch.setattr(sd.time, "time", lambda: 1234)
    sd._remove_plugin_directory(d)
    backup = tmp_path / f"{BUNDLE}.mycelian-old-1234"
    assert not d.exists() and backup.is_dir()
    assert removed == [backup]


def test_remove_plugin_directory_backup_cleanup_failure_is_tolerated(tmp_path, monkeypatch):
    d = tmp_path / BUNDLE
    d.mkdir()

    def remove(path):
        raise PermissionError("locked")

    monkeypatch.setattr(sd, "_remove_tree", remove)
    sd._remove_plugin_directory(d)
    assert not d.exists()
    assert any(p.name.startswith(f"{BUNDLE}.mycelian-old-") for p in tmp_path.iterdir())


def test_remove_plugin_directory_rename_failure_raises_install_error(tmp_path, monkeypatch):
    d = tmp_path / BUNDLE
    d.mkdir()
    monkeypatch.setattr(sd, "_remove_tree", lambda p: (_ for _ in ()).throw(PermissionError("locked")))

    def no_rename(self, target):
        raise PermissionError("locked too")

    monkeypatch.setattr(sd.Path, "rename", no_rename)
    with pytest.raises(sd.PluginInstallError) as info:
        sd._remove_plugin_directory(d)
    assert info.value.user_message == sd._STREAMDECK_QUIT_HINT


def test_cleanup_stale_backups(tmp_path, monkeypatch):
    stale = tmp_path / f"{BUNDLE}.mycelian-old-1"
    stale.mkdir()
    locked = tmp_path / f"{BUNDLE}.mycelian-old-2"
    locked.mkdir()
    keep_dir = tmp_path / "other.sdPlugin"
    keep_dir.mkdir()
    keep_file = tmp_path / f"{BUNDLE}.mycelian-old-3"
    keep_file.write_text("file, not dir")
    real = sd._remove_tree

    def remove(path):
        if path == locked:
            raise OSError("busy")
        real(path)

    monkeypatch.setattr(sd, "_remove_tree", remove)
    sd._cleanup_stale_plugin_backups(tmp_path)
    assert not stale.exists() and locked.exists() and keep_dir.exists() and keep_file.exists()


# ------------------------------------------------------------------ install_bundled_plugin


def test_install_missing_bundle(fake_os):
    with pytest.raises(sd.PluginInstallError) as info:
        sd.install_bundled_plugin()
    assert "Plugin source files not found" in info.value.user_message


def test_install_unsupported_os(fake_os, monkeypatch):
    _manifest(fake_os.bundled, "1.0")
    monkeypatch.setattr(sd.platform, "system", lambda: "Linux")
    with pytest.raises(sd.PluginInstallError) as info:
        sd.install_bundled_plugin()
    assert "only supported on macOS and Windows" in info.value.user_message


def test_install_fresh_and_replace(fake_os):
    _manifest(fake_os.bundled, "2.0")
    res = sd.install_bundled_plugin()
    dest = fake_os.plugins / BUNDLE
    assert res == sd.PluginInstallResult(destination=dest, replaced_existing=False)
    assert json.loads((dest / "manifest.json").read_text())["Version"] == "2.0"
    (dest / "stale.txt").write_text("old")
    (fake_os.plugins / f"{BUNDLE}.mycelian-old-9").mkdir()
    res = sd.install_bundled_plugin()
    assert res.replaced_existing is True
    assert not (dest / "stale.txt").exists()
    assert not (fake_os.plugins / f"{BUNDLE}.mycelian-old-9").exists()


@pytest.mark.parametrize("exc, expected", [
    (PermissionError("ro"), sd._STREAMDECK_QUIT_HINT),
    (OSError(errno.ENOSPC, "disk full"), "Could not copy plugin files:"),
])
def test_install_copy_failures(fake_os, monkeypatch, exc, expected):
    _manifest(fake_os.bundled, "2.0")

    def fail(src, dst):
        raise exc

    monkeypatch.setattr(sd.shutil, "copytree", fail)
    with pytest.raises(sd.PluginInstallError) as info:
        sd.install_bundled_plugin()
    assert info.value.user_message.startswith(expected)


# ------------------------------------------------------------------ versions


@pytest.mark.parametrize("body, version", [
    ({"Version": "1.2.3"}, "1.2.3"),
    ({"Version": 2}, "2"),
    ({"Version": "  "}, None),
    ({"Version": None}, None),
    ({}, None),
])
def test_read_plugin_version(tmp_path, body, version):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(body))
    assert sd.read_plugin_version(p) == version


def test_read_plugin_version_errors(tmp_path):
    assert sd.read_plugin_version(tmp_path / "missing.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    assert sd.read_plugin_version(bad) is None


@pytest.mark.parametrize("bundled, installed, cmp", [("1.2", "1.1", -1), ("1.2", "1.2.0", 0), ("1.2", "1.10", 1)])
def test_compare_plugin_versions(bundled, installed, cmp):
    assert sd.compare_plugin_versions(bundled, installed) == cmp


def test_compare_plugin_versions_invalid():
    with pytest.raises(InvalidVersion):
        sd.compare_plugin_versions("1.0", "not-a-version")


# ------------------------------------------------------------------ get_plugin_status


def test_status_unavailable(fake_os):
    _manifest(fake_os.bundled, "1.0")
    assert sd.get_plugin_status() == sd.PluginStatus(S.UNAVAILABLE, "1.0")


def test_status_not_installed(fake_os):
    fake_os.plugins.mkdir(parents=True)
    assert sd.get_plugin_status() == sd.PluginStatus(S.NOT_INSTALLED, None)
    (fake_os.plugins / BUNDLE).mkdir()  # folder without manifest
    assert sd.get_plugin_status().state == S.NOT_INSTALLED


@pytest.mark.parametrize("bundled, installed, state", [
    ("2.0", "1.0", S.OUTDATED),
    ("2.0", "2.0", S.UP_TO_DATE),
    ("2.0", "3.0", S.UP_TO_DATE),
    ("2.0", None, S.UNKNOWN),
    (None, "1.0", S.UNKNOWN),
    ("2.0", "garbage!", S.UNKNOWN),
])
def test_status_versions(fake_os, bundled, installed, state):
    if bundled is not None:
        _manifest(fake_os.bundled, bundled)
    _manifest(fake_os.plugins / BUNDLE, installed)
    status = sd.get_plugin_status()
    assert status.state == state
    assert status.bundled_version == bundled
    assert status.installed_version == installed


# ------------------------------------------------------------------ display helpers


@pytest.mark.parametrize("status, display, text, button", [
    (sd.PluginStatus(S.UNAVAILABLE, "2"), sd.PluginStatusDisplay("Stream Deck not detected"),
     "Stream Deck not detected", "Install Plugin"),
    (sd.PluginStatus(S.NOT_INSTALLED, "2"), sd.PluginStatusDisplay("Not installed"), "Not installed", "Install Plugin"),
    (sd.PluginStatus(S.OUTDATED, "2", "1"), sd.PluginStatusDisplay("Installed", "1", "2"),
     "Installed · v1 · (update available: v2)", "Reinstall Plugin"),
    (sd.PluginStatus(S.UP_TO_DATE, "2", "2"), sd.PluginStatusDisplay("Installed", "2"), "Installed · v2",
     "Reinstall Plugin"),
    (sd.PluginStatus(S.UNKNOWN, "2", None), sd.PluginStatusDisplay("Installed", "2"), "Installed · v2",
     "Reinstall Plugin"),
    (sd.PluginStatus(S.UNKNOWN), sd.PluginStatusDisplay("Installed"), "Installed", "Reinstall Plugin"),
])
def test_status_display(status, display, text, button):
    assert sd.get_plugin_status_display(status) == display
    assert sd.format_plugin_status_text(status) == text
    assert sd.get_install_button_label(status) == button


# ------------------------------------------------------------------ maybe_notify_streamdeck_plugin_outdated


def _notify_spy(monkeypatch, history=(), entry_id="n1"):
    calls = []

    def notify(msg, **kw):
        calls.append((msg, kw))
        return entry_id

    monkeypatch.setattr(sd, "notify", notify)
    monkeypatch.setattr(sd, "get_history", lambda: list(history))
    monkeypatch.setattr(sd, "nav_actions_settings", lambda tab: [{"tab": tab}])
    return calls


def test_notify_only_when_outdated(monkeypatch):
    calls = _notify_spy(monkeypatch)
    monkeypatch.setattr(sd, "get_plugin_status", lambda: sd.PluginStatus(S.UP_TO_DATE, "2", "2"))
    assert sd.maybe_notify_streamdeck_plugin_outdated() is False
    assert calls == []


def test_notify_outdated(monkeypatch):
    calls = _notify_spy(monkeypatch)
    monkeypatch.setattr(sd, "get_plugin_status", lambda: sd.PluginStatus(S.OUTDATED, "2.0", "1.0"))
    assert sd.maybe_notify_streamdeck_plugin_outdated() is True
    (msg, kw), = calls
    assert kw["title"] == "Stream Deck plugin update available (v2.0)"
    assert kw["details"] == "installed: v1.0, bundled: v2.0"
    assert kw["dedupe_key"] == sd.OUTDATED_NOTIFY_DEDUPE_KEY
    assert kw["dedupe_cooldown_sec"] == sd.OUTDATED_NOTIFY_COOLDOWN_SEC
    assert kw["actions"] == [{"tab": "App Settings"}]
    assert kw["type"] == "warning"


def test_notify_skipped_when_already_in_history(monkeypatch):
    history = [{"dedupe_key": "other"}, {"dedupe_key": sd.OUTDATED_NOTIFY_DEDUPE_KEY, "details": "installed: v?, bundled: v?"}]
    calls = _notify_spy(monkeypatch, history=history)
    monkeypatch.setattr(sd, "get_plugin_status", lambda: sd.PluginStatus(S.OUTDATED))
    assert sd.maybe_notify_streamdeck_plugin_outdated() is False
    assert calls == []


def test_notify_deduped_returns_false(monkeypatch):
    history = [{"dedupe_key": sd.OUTDATED_NOTIFY_DEDUPE_KEY, "details": "older version text"}]
    calls = _notify_spy(monkeypatch, history=history, entry_id=None)
    monkeypatch.setattr(sd, "get_plugin_status", lambda: sd.PluginStatus(S.OUTDATED, "3", "1"))
    assert sd.maybe_notify_streamdeck_plugin_outdated() is False
    assert len(calls) == 1
