# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/obs_browser_docks.py: ini parsing, dock boot page, migration, OBS watchers.

OBS config files and the Mycelian app-support folder live under tmp_path; threads,
sleeps and process scans are faked.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import obs_browser_docks as docks  # noqa: E402

PORT = 5000
ROUTES = ["activity_feed", "source_controls"]


class _Stop(BaseException):
    """Breaks out of the endless watcher loop (it only catches Exception)."""


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Path.home() on Windows
    # The module only reads sys.platform: give it its own stand-in so the real
    # sys.platform stays put (urllib imports macOS-only modules when it says darwin)
    monkeypatch.setattr(docks, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(docks, "_dock_boot_port", None)
    monkeypatch.setattr(docks, "_dock_boot_routes", [])
    monkeypatch.setattr(docks, "_obs_exit_watcher_started", False)
    monkeypatch.setattr(docks, "_cached_obs_proc", None)
    return home


def _ini(rows, extra="PreviewEnabled=true\n"):
    return "[BasicWindow]\n" + extra + "ExtraBrowserDocks=" + json.dumps(rows) + "\nAlwaysOnTop=false\n"


def _dock(title, url, uuid=""):
    return {"title": title, "url": url, "uuid": uuid}


# ------------------------------------------------------------------ ini escaping / parsing


@pytest.mark.parametrize("raw, out", [
    ("a\\\\b", "a\\b"), ("a\\nb", "a\nb"), ("a\\rb", "a\rb"), ("a\\tb", "a\\tb"), ("end\\", "end\\"), ("", ""),
    (None, ""),
])
def test_unescape(raw, out):
    assert docks.unescape_obs_ini_value(raw) == out


def test_escape_roundtrip():
    value = 'x\\y\r\n"z"'
    escaped = docks.escape_obs_ini_value(value)
    assert "\n" not in escaped and "\r" not in escaped
    assert docks.unescape_obs_ini_value(escaped) == value
    assert docks.escape_obs_ini_value(None) == ""


def test_parse_skips_bad_payloads():
    text = "\n".join([
        "Other=1",
        "ExtraBrowserDocks=",
        "ExtraBrowserDocks={not json",
        'ExtraBrowserDocks={"title": "obj"}',
        'ExtraBrowserDocks=["str", {"title": "no url"}, {"title": " T ", "url": " u ", "uuid": 5}]',
    ])
    assert docks.parse_extra_browser_docks(text) == [{"title": "T", "url": "u", "uuid": "5"}]
    assert docks.parse_extra_browser_docks(None) == []


# ------------------------------------------------------------------ app support / boot page


def test_app_support_dir_per_platform(_isolate, monkeypatch, tmp_path):
    home = _isolate
    assert docks.mycelian_app_support_dir() == home / "Library" / "Application Support" / "Mycelian"
    monkeypatch.setattr(docks.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert docks.mycelian_app_support_dir() == tmp_path / "Roaming" / "Mycelian"
    monkeypatch.delenv("APPDATA")
    assert docks.mycelian_app_support_dir() == home / "AppData" / "Roaming" / "Mycelian"
    monkeypatch.setattr(docks.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert docks.mycelian_app_support_dir() == tmp_path / "xdg" / "mycelian"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert docks.mycelian_app_support_dir() == home / ".config" / "mycelian"


def test_ensure_dock_boot_html_and_url(_isolate):
    path = docks.ensure_dock_boot_html()
    assert path == docks.dock_boot_html_path()
    assert path.read_text(encoding="utf-8") == docks._DOCK_BOOT_HTML
    url = docks.dock_boot_file_url("5000", "/activity_feed/")
    assert url == f"{path.as_uri()}?port=5000&route=activity_feed#5000/activity_feed"
    assert docks.is_dock_boot_file_url(url)


@pytest.mark.parametrize("url, ok", [
    ("file:///x/obs_dock_boot.html", True),
    ("file:///x/OBS_DOCK_BOOT.HTML?a=1", True),
    ("file:///x/other.html", False),
    ("http://x/obs_dock_boot.html", False),
    ("", False),
    (None, False),
])
def test_is_dock_boot_file_url(url, ok):
    assert docks.is_dock_boot_file_url(url) is ok


def test_is_dock_boot_file_url_parse_error(monkeypatch):
    monkeypatch.setattr(docks, "urlparse", lambda raw: (_ for _ in ()).throw(ValueError("x")))
    assert docks.is_dock_boot_file_url("file:///x") is False


# ------------------------------------------------------------------ route matching


BOOT = "file:///tmp/obs_dock_boot.html"


@pytest.mark.parametrize("url, port, routes, route", [
    ("http://127.0.0.1:5000/activity_feed", 5000, ROUTES, "activity_feed"),
    ("http://127.0.0.1:5000/activity_feed", "5000", ROUTES, "activity_feed"),
    ("http://127.0.0.1:5000/activity_feed", "bad", ROUTES, None),
    ("http://127.0.0.1:5000/activity_feed", "", ROUTES, None),
    ("", 5000, ROUTES, None),
    (f"{BOOT}?port=5000&route=activity_feed", 5000, ROUTES, "activity_feed"),
    (f"{BOOT}?port=5000&route=activity_feed", None, ROUTES, None),
    (f"{BOOT}?port=abc&route=activity_feed", 5000, ROUTES, None),
    (f"{BOOT}?port=5000&route=unknown", 5000, ROUTES, None),
    (f"{BOOT}?port=1&route=x#5000/source_controls", 5000, ROUTES, "source_controls"),
    (f"{BOOT}?port=5000&route=activity_feed#zz/", 5000, ROUTES, "activity_feed"),
    (f"{BOOT}?port=5000&route=activity_feed#nofrag", 5000, ROUTES, "activity_feed"),
    ("file:///tmp/other.html?port=5000&route=activity_feed", 5000, ROUTES, None),
    ("http://127.0.0.1:5000/activity_feed", 5000, [" ", None], None),
])
def test_mycelian_dock_route_from_url(url, port, routes, route):
    assert docks.mycelian_dock_route_from_url(url, port, routes) == route


def test_mycelian_dock_route_parse_error(monkeypatch):
    monkeypatch.setattr(docks, "urlparse", lambda raw: (_ for _ in ()).throw(ValueError("x")))
    assert docks.mycelian_dock_route_from_url("http://x", 5000, ROUTES) is None


# ------------------------------------------------------------------ boot context / watcher thread


class _FakeThread:
    started = []

    def __init__(self, target=None, name=None, daemon=None):
        self.target, self.name, self.daemon = target, name, daemon

    def start(self):
        _FakeThread.started.append(self)


@pytest.fixture
def fake_threads(monkeypatch):
    _FakeThread.started = []
    monkeypatch.setattr(docks, "threading", SimpleNamespace(Thread=_FakeThread))
    return _FakeThread.started


def test_set_dock_boot_context(fake_threads):
    docks.set_dock_boot_context("5000", [" activity_feed ", "", None])
    assert docks._dock_boot_context() == (5000, ["activity_feed", "None"])
    docks.set_dock_boot_context("nope", ROUTES)
    assert docks._dock_boot_context() == (None, ROUTES)
    docks.set_dock_boot_context(None, None)
    assert docks._dock_boot_context() == (None, [])
    # Watcher thread starts only once.
    assert [t.name for t in fake_threads] == ["MycelianOBSDockBootWatch"]
    assert fake_threads[0].daemon is True and fake_threads[0].target is docks._obs_exit_dock_boot_loop


def test_schedule_migrate_starts_thread(fake_threads):
    docks.schedule_dock_boot_migrate_if_obs_exits()
    assert fake_threads[0].name == "MycelianOBSDockBootMigrate"
    assert fake_threads[0].target is docks._dock_boot_migrate_after_obs_exit


# ------------------------------------------------------------------ replace / retarget


def test_replace_keeps_indent_and_newline():
    text = "[x]\n  ExtraBrowserDocks=[]\nA=1"
    out = docks.replace_extra_browser_docks_value(text, [{"title": "T", "url": "u\nv", "uuid": None}])
    assert out == '[x]\n  ExtraBrowserDocks=[{"title":"T","url":"u\\\\nv","uuid":""}]\nA=1'
    last = docks.replace_extra_browser_docks_value("ExtraBrowserDocks=[]", [])
    assert last == "ExtraBrowserDocks=[]"


def test_replace_without_key_returns_original():
    assert docks.replace_extra_browser_docks_value("[x]\nA=1\n", [_dock("t", "u")]) == "[x]\nA=1\n"
    assert docks.replace_extra_browser_docks_value(None, []) == ""


def test_retarget_unchanged_returns_input_copy():
    rows = [_dock("SE", "https://streamelements.com/x"), _dock("Boot", f"{BOOT}?port=5000&route=activity_feed")]
    out = docks.retarget_mycelian_docks_to_boot(rows, PORT, ROUTES)
    assert out == rows and out is not rows


# ------------------------------------------------------------------ config paths / loading


def test_iter_obs_config_paths(_isolate, monkeypatch, tmp_path):
    home = _isolate
    mac = home / "Library" / "Application Support" / "obs-studio"
    assert docks.iter_obs_config_paths() == [mac / "user.ini", mac / "global.ini"]
    monkeypatch.setattr(docks.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "R"))
    assert docks.iter_obs_config_paths() == [tmp_path / "R" / "obs-studio" / "user.ini",
                                             tmp_path / "R" / "obs-studio" / "global.ini"]
    monkeypatch.delenv("APPDATA")
    assert docks.iter_obs_config_paths() == []
    monkeypatch.setattr(docks.sys, "platform", "linux")
    paths = docks.iter_obs_config_paths()
    assert len(paths) == 6
    assert paths[0] == home / ".config" / "obs-studio" / "user.ini"
    assert any("com.obsproject.Studio" in str(p) for p in paths)
    assert any("snap" in str(p) for p in paths)


def test_iter_obs_config_paths_dedupes(monkeypatch, tmp_path):
    monkeypatch.setattr(docks, "_OBS_CONFIG_FILENAMES", ("user.ini", "user.ini"))
    assert len(docks.iter_obs_config_paths()) == 1


def test_load_from_files_dedupes(tmp_path):
    a = tmp_path / "user.ini"
    b = tmp_path / "global.ini"
    a.write_text(_ini([_dock("A", "http://x/a", "u1"), _dock("B", "http://x/b")]), encoding="utf-8-sig")
    b.write_text(_ini([_dock("A again", "http://x/a2", "u1"), _dock("B", "http://x/b"), _dock("C", "http://x/c")]))
    folder = tmp_path / "dir.ini"
    folder.mkdir()
    found = docks.load_extra_browser_docks(config_paths=[tmp_path / "missing.ini", folder, a, b])
    assert [d["title"] for d in found] == ["A", "B", "C"]


def test_load_from_files_read_error(tmp_path, monkeypatch):
    a = tmp_path / "user.ini"
    a.write_text(_ini([_dock("A", "http://x/a")]))
    monkeypatch.setattr(docks.Path, "read_text", lambda self, encoding=None: (_ for _ in ()).throw(OSError("no")))
    assert docks.load_extra_browser_docks(config_paths=[a]) == []


def test_load_default_paths(_isolate):
    folder = _isolate / "Library" / "Application Support" / "obs-studio"
    folder.mkdir(parents=True)
    (folder / "user.ini").write_text(_ini([_dock("A", "http://127.0.0.1:5000/activity_feed", "u")]))
    assert [d["title"] for d in docks.load_extra_browser_docks()] == ["A"]
    listed = docks.list_mycelian_extra_browser_docks("5000", ROUTES)
    assert listed == [{"title": "A", "url": "http://127.0.0.1:5000/activity_feed", "uuid": "u",
                       "route": "activity_feed"}]


def test_list_with_bad_port():
    ini = _ini([_dock("A", "http://127.0.0.1:5000/activity_feed")])
    assert docks.list_mycelian_extra_browser_docks("x", ROUTES, ini_text=ini) == []
    assert docks.list_mycelian_extra_browser_docks("", ROUTES, ini_text=ini) == []


# ------------------------------------------------------------------ migrate_extra_browser_docks_to_boot


@pytest.fixture
def obs_off(monkeypatch):
    monkeypatch.setattr(docks, "obs_process_is_running", lambda: False)


def test_migrate_skips_when_obs_running(monkeypatch, tmp_path):
    monkeypatch.setattr(docks, "obs_process_is_running", lambda: True)
    assert docks.migrate_extra_browser_docks_to_boot(PORT, ROUTES, config_paths=[]) == 0


@pytest.mark.parametrize("port, routes", [(None, ROUTES), ("", ROUTES), ("bad", ROUTES), (PORT, []), (PORT, [" "])])
def test_migrate_needs_port_and_routes(obs_off, port, routes):
    assert docks.migrate_extra_browser_docks_to_boot(port, routes, config_paths=[]) == 0


def test_migrate_rewrites_only_mycelian_docks(obs_off, tmp_path, _isolate):
    user = tmp_path / "user.ini"
    user.write_text(_ini([
        _dock("Feed", "http://127.0.0.1:5000/activity_feed", "a"),
        _dock("SE", "https://streamelements.com/x", "b"),
    ]), encoding="utf-8")
    other = tmp_path / "global.ini"
    other.write_text(_ini([_dock("SE", "https://streamelements.com/x", "b")]))
    empty = tmp_path / "empty.ini"
    empty.write_text("[BasicWindow]\nA=1\n")
    paths = [tmp_path / "missing.ini", empty, other, user]
    assert docks.migrate_extra_browser_docks_to_boot("5000", ROUTES, config_paths=paths) == 1
    text = user.read_text(encoding="utf-8")
    assert "PreviewEnabled=true" in text and "AlwaysOnTop=false" in text
    rows = docks.parse_extra_browser_docks(text)
    assert docks.is_dock_boot_file_url(rows[0]["url"]) and "route=activity_feed" in rows[0]["url"]
    assert rows[1]["url"] == "https://streamelements.com/x"
    assert docks.dock_boot_html_path().is_file()
    # Already migrated: nothing to do the second time.
    assert docks.migrate_extra_browser_docks_to_boot(PORT, ROUTES, config_paths=paths) == 0


def test_migrate_skips_unreadable_and_unchanged_text(obs_off, tmp_path, monkeypatch):
    user = tmp_path / "user.ini"
    user.write_text(_ini([_dock("Feed", "http://127.0.0.1:5000/activity_feed")]))
    # Rewrite yields identical text (defensive branch).
    monkeypatch.setattr(docks, "replace_extra_browser_docks_value", lambda original, updated: original)
    assert docks.migrate_extra_browser_docks_to_boot(PORT, ROUTES, config_paths=[user]) == 0


def test_migrate_read_error(obs_off, tmp_path, monkeypatch):
    user = tmp_path / "user.ini"
    user.write_text("x")
    monkeypatch.setattr(docks.Path, "read_text", lambda self, encoding=None: (_ for _ in ()).throw(OSError("no")))
    assert docks.migrate_extra_browser_docks_to_boot(PORT, ROUTES, config_paths=[user]) == 0


def test_migrate_write_error(obs_off, tmp_path, monkeypatch, _isolate):
    user = tmp_path / "user.ini"
    original = _ini([_dock("Feed", "http://127.0.0.1:5000/activity_feed")])
    user.write_text(original)
    docks.ensure_dock_boot_html()
    real_write = docks.Path.write_text

    def write(self, data, encoding=None):
        if self == user:
            raise OSError("read-only")
        return real_write(self, data, encoding=encoding)

    monkeypatch.setattr(docks.Path, "write_text", write)
    assert docks.migrate_extra_browser_docks_to_boot(PORT, ROUTES, config_paths=[user]) == 0
    assert user.read_text() == original


def test_migrate_default_paths(obs_off, _isolate):
    folder = _isolate / "Library" / "Application Support" / "obs-studio"
    folder.mkdir(parents=True)
    (folder / "global.ini").write_text(_ini([_dock("Feed", "http://127.0.0.1:5000/activity_feed")]))
    assert docks.migrate_extra_browser_docks_to_boot(PORT, ROUTES) == 1


# ------------------------------------------------------------------ migrate-now helpers


def test_try_migrate_now(monkeypatch):
    calls = []
    monkeypatch.setattr(docks, "migrate_extra_browser_docks_to_boot", lambda p, r: calls.append((p, r)) or 2)
    assert docks._try_migrate_dock_boot_now() == 0  # no context yet
    monkeypatch.setattr(docks, "_dock_boot_port", 5000)
    assert docks._try_migrate_dock_boot_now() == 0  # no routes
    monkeypatch.setattr(docks, "_dock_boot_routes", ["a"])
    assert docks._try_migrate_dock_boot_now() == 2
    assert calls == [(5000, ["a"])]
    monkeypatch.setattr(docks, "migrate_extra_browser_docks_to_boot", lambda p, r: None)
    assert docks._try_migrate_dock_boot_now() == 0

    def boom(p, r):
        raise RuntimeError("x")

    monkeypatch.setattr(docks, "migrate_extra_browser_docks_to_boot", boom)
    assert docks._try_migrate_dock_boot_now() == 0


def test_migrate_after_exit_returns_if_obs_back(monkeypatch):
    sleeps = []
    monkeypatch.setattr(docks.time, "sleep", sleeps.append)
    monkeypatch.setattr(docks, "obs_process_is_running", lambda: True)
    monkeypatch.setattr(docks, "_try_migrate_dock_boot_now", lambda: pytest.fail("not while OBS runs"))
    docks._dock_boot_migrate_after_obs_exit()
    assert sleeps == [2.0]


def test_migrate_after_exit_retries_until_flush(monkeypatch):
    sleeps = []
    results = iter([0, 0, 1])
    monkeypatch.setattr(docks.time, "sleep", sleeps.append)
    monkeypatch.setattr(docks, "obs_process_is_running", lambda: False)
    monkeypatch.setattr(docks, "_try_migrate_dock_boot_now", lambda: next(results))
    docks._dock_boot_migrate_after_obs_exit()
    assert sleeps == [2.0, 2.0, 2.0]

    attempts = []
    monkeypatch.setattr(docks, "_try_migrate_dock_boot_now", lambda: attempts.append(1) or 1)
    docks._dock_boot_migrate_after_obs_exit()
    assert attempts == [1]


# ------------------------------------------------------------------ exit watcher loop


def _run_loop(monkeypatch, running_seq, *, max_sleeps, migrate_result=1, first_raises=False):
    seq = iter(running_seq)
    sleeps = []
    migrations = []

    def running():
        val = next(seq)
        if isinstance(val, Exception):
            raise val
        return val

    def sleep(sec):
        sleeps.append(sec)
        if len(sleeps) >= max_sleeps:
            raise _Stop()

    monkeypatch.setattr(docks, "obs_process_is_running", running)
    monkeypatch.setattr(docks.time, "sleep", sleep)
    monkeypatch.setattr(docks, "_try_migrate_dock_boot_now", lambda: migrations.append(1) or migrate_result)
    with pytest.raises(_Stop):
        docks._obs_exit_dock_boot_loop()
    return sleeps, migrations


def test_loop_migrates_after_obs_quits(monkeypatch):
    # initial=True; tick -> False (quit); confirm False; after migrate -> False; next tick stops.
    sleeps, migrations = _run_loop(monkeypatch, [True, False, False, False], max_sleeps=3)
    assert migrations == [1]
    assert sleeps == [1.5, docks._OBS_EXIT_FLUSH_SEC, 1.5]


def test_loop_no_change_logged_quietly(monkeypatch):
    sleeps, migrations = _run_loop(monkeypatch, [True, False, False, False], max_sleeps=3, migrate_result=0)
    assert migrations == [1]


def test_loop_obs_restarted_during_flush(monkeypatch):
    sleeps, migrations = _run_loop(monkeypatch, [True, False, True, True], max_sleeps=3)
    assert migrations == []


def test_loop_initial_error_and_iteration_error(monkeypatch):
    # Initial check raises (treated as not running); first tick raises -> 2s backoff.
    sleeps, migrations = _run_loop(monkeypatch, [RuntimeError("a"), RuntimeError("b"), False], max_sleeps=3)
    assert sleeps == [1.5, 2.0, 1.5]
    assert migrations == []


# ------------------------------------------------------------------ OBS process detection


@pytest.mark.parametrize("name, ok", [
    ("OBS", True), ("obs64.exe", True), ("obs-studio", True), ("OBS Studio", True), ("obs_studio", False),
    ("OBS Helper (Renderer)", False), ("obs-browser-page", False), ("", False), ("chrome", False),
])
def test_is_obs_main_process_name(name, ok):
    assert docks._is_obs_main_process_name(name) is ok


class _Proc:
    def __init__(self, name, running=True, raises=False):
        self.info = {"name": name}
        self._name, self._running, self._raises = name, running, raises

    def is_running(self):
        if self._raises:
            raise RuntimeError("gone")
        return self._running

    def name(self):
        return self._name


def test_obs_process_is_running_scans_and_caches(monkeypatch):
    import psutil

    obs = _Proc("obs")
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter([_Proc("python"), _Proc(None), obs]))
    assert docks.obs_process_is_running() is True
    assert docks._cached_obs_proc is obs
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: pytest.fail("cached"))
    assert docks.obs_process_is_running() is True


def test_obs_process_cache_dropped_when_dead_or_erroring(monkeypatch):
    import psutil

    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter([]))
    monkeypatch.setattr(docks, "_cached_obs_proc", _Proc("obs", running=False))
    assert docks.obs_process_is_running() is False
    assert docks._cached_obs_proc is None
    monkeypatch.setattr(docks, "_cached_obs_proc", _Proc("obs", raises=True))
    assert docks.obs_process_is_running() is False
    assert docks._cached_obs_proc is None


def test_obs_process_scan_error(monkeypatch):
    import psutil

    def boom(attrs):
        raise psutil.Error("denied")

    monkeypatch.setattr(psutil, "process_iter", boom)
    assert docks.obs_process_is_running() is False


def test_obs_process_without_psutil(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert docks.obs_process_is_running() is False


def test_obs_appears_running(monkeypatch):
    from modules import obs_service as obs_mod

    monkeypatch.setattr(obs_mod, "obs_service", SimpleNamespace(is_connected=lambda: True))
    monkeypatch.setattr(docks, "obs_process_is_running", lambda: pytest.fail("websocket already says yes"))
    assert docks.obs_appears_running() is True

    def broken():
        raise RuntimeError("x")

    monkeypatch.setattr(obs_mod, "obs_service", SimpleNamespace(is_connected=broken))
    monkeypatch.setattr(docks, "obs_process_is_running", lambda: False)
    assert docks.obs_appears_running() is False
    monkeypatch.setattr(obs_mod, "obs_service", SimpleNamespace(is_connected=lambda: False))
    monkeypatch.setattr(docks, "obs_process_is_running", lambda: True)
    assert docks.obs_appears_running() is True


# ------------------------------------------------------------------ restart notices


def test_pending_http_dock_titles():
    pending = [
        {"title": "Feed", "url": "http://x/a"},
        {"title": "feed", "url": "http://x/b"},
        {"title": "", "route": "controls", "url": "http://x/c"},
        {"title": "", "route": "", "url": "http://x/d"},
        {"title": "Boot", "url": BOOT},
    ]
    assert docks._pending_http_dock_titles(pending) == ["Feed", "controls"]


@pytest.fixture
def notify_spy(monkeypatch):
    from modules import notification_engine

    calls = []
    monkeypatch.setattr(notification_engine, "notify", lambda msg, **kw: calls.append((msg, kw)))
    monkeypatch.setattr(notification_engine, "nav_actions_settings", lambda tab: [tab])
    return calls


@pytest.mark.parametrize("titles, label", [
    (["A"], "A"), (["A", "B"], "A and B"), (["A", "B", "C"], "A, B, and C"),
])
def test_notify_restart_labels(notify_spy, titles, label):
    docks._notify_restart_obs_for_docks(titles)
    (msg, kw), = notify_spy
    assert msg == f"{label} couldn't load because OBS started before Mycelian."
    assert kw["dedupe_key"] == "obs:docks:restart-obs" and kw["actions"] == ["OBS"]
    assert "Cmd+R" in kw["hint"]


def test_notify_restart_uses_ctrl_r_off_mac(notify_spy, monkeypatch):
    monkeypatch.setattr(docks.sys, "platform", "win32")
    docks._notify_restart_obs_for_docks(["A"])
    assert "Ctrl+R" in notify_spy[0][1]["hint"]


def test_notify_restart_empty_and_failure(notify_spy, monkeypatch):
    docks._notify_restart_obs_for_docks([])
    assert notify_spy == []
    from modules import notification_engine

    monkeypatch.setattr(notification_engine, "notify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    docks._notify_restart_obs_for_docks(["A"])  # swallowed


def test_wake_ignores_rows_without_route(monkeypatch):
    monkeypatch.setattr(docks, "_notify_restart_obs_for_docks", lambda t: pytest.fail("nothing pending"))
    assert docks.wake_mycelian_browser_docks([{"title": "A", "url": "http://x"}]) == []


def test_pending_docks_missing_routes_case_and_blank():
    rows = [{"route": "Feed"}, {"route": ""}, {"route": "Other"}]
    assert docks.pending_docks_missing_routes(rows, ["feed", " ", ""]) == [{"route": "Other"}]


def test_wake_notifies_with_http_titles(monkeypatch):
    seen = []
    monkeypatch.setattr(docks, "_notify_restart_obs_for_docks", seen.append)
    pending = [{"title": "Feed", "url": "http://127.0.0.1:5000/activity_feed", "route": "activity_feed"},
               {"title": "Boot", "url": BOOT, "route": "source_controls"}]
    assert docks.wake_mycelian_browser_docks(pending) == []
    assert seen == [["Feed"]]
