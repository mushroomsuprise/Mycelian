# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/assets_watcher.py (asset snapshots + mtime poller)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

import modules
from modules.spore_studio import assets_watcher as aw


@pytest.fixture
def assets(tmp_path, monkeypatch):
    root = tmp_path / "assets"
    root.mkdir()
    monkeypatch.setattr(aw, "get_assets_path", lambda rel="": str(root))
    monkeypatch.setattr(aw, "_known", {})
    monkeypatch.setattr(aw, "_poller_started", False)
    monkeypatch.setattr(aw, "_stop_event", aw.threading.Event())
    return root


def _fake_engine(monkeypatch, inst):
    fake = SimpleNamespace(web_engine_instance=inst)
    monkeypatch.setattr(modules, "web_engine", fake, raising=False)
    return fake


# ---------------------------------------------------------------- classify / validation

@pytest.mark.parametrize(
    "name,kind",
    [("a.MP3", "audio"), ("b.webm", "video"), ("c.SVG", "image"), ("d.woff2", "font"),
     ("e.txt", "other"), ("noext", "other")],
)
def test_classify(name, kind):
    assert aw._classify(name) == kind


@pytest.mark.parametrize("bad", [None, 5, "", "../x", "a b", "a/b", "a.b"])
def test_validated_dir_rejects_bad_names(assets, bad):
    with pytest.raises(ValueError, match="Invalid template name"):
        aw._validated_template_assets_dir(bad)


def test_validated_dir_ok(assets):
    assert aw._validated_template_assets_dir("my_tpl-1") == os.path.realpath(str(assets / "my_tpl-1"))


def test_validated_dir_symlink_escape(assets, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, assets / "evil")
    with pytest.raises(ValueError, match="Invalid template path"):
        aw._validated_template_assets_dir("evil")


# ---------------------------------------------------------------- scanning

def test_scan_missing_folder(assets):
    snap = aw._scan_template("nothing")
    assert snap["files"] == [] and snap["template"] == "nothing"
    assert isinstance(snap["scanned_at"], float)


def test_scan_lists_nested_sorted(assets):
    d = assets / "tpl"
    (d / "sub").mkdir(parents=True)
    (d / "B.png").write_bytes(b"12")
    (d / "a.mp3").write_bytes(b"1")
    (d / "sub" / "f.ttf").write_bytes(b"123")
    snap = aw._scan_template("tpl")
    assert [f["rel_path"] for f in snap["files"]] == ["a.mp3", "B.png", "sub/f.ttf"]
    f = snap["files"][2]
    assert f["url"] == "/assets/tpl/sub/f.ttf" and f["kind"] == "font" and f["size"] == 3
    assert f["name"] == "f.ttf"


def test_scan_skips_unstattable(assets, monkeypatch):
    d = assets / "tpl"
    d.mkdir()
    (d / "a.png").write_bytes(b"1")
    (d / "b.png").write_bytes(b"1")
    real_stat = os.stat

    def fake_stat(p, *a, **k):
        if str(p).endswith("a.png"):
            raise OSError
        return real_stat(p, *a, **k)

    monkeypatch.setattr(aw.os, "stat", fake_stat)
    assert [f["name"] for f in aw._scan_template("tpl")["files"]] == ["b.png"]


# ---------------------------------------------------------------- destinations / upload / delete

@pytest.mark.parametrize("rel", ["", ".", "..", "a/../b", "a//b", "/", "./x"])
def test_safe_destination_rejects(assets, rel):
    with pytest.raises(ValueError, match="Invalid asset path"):
        aw._safe_asset_destination("tpl", rel)


def test_safe_destination_normalises(assets):
    dest = aw._safe_asset_destination("tpl", "\\sub\\x.png")
    assert dest == os.path.realpath(str(assets / "tpl" / "sub" / "x.png"))


def test_safe_destination_symlink_escape(assets, tmp_path):
    (assets / "tpl").mkdir()
    outside = tmp_path / "out"
    outside.mkdir()
    os.symlink(outside, assets / "tpl" / "link")
    with pytest.raises(ValueError, match="Invalid asset path"):
        aw._safe_asset_destination("tpl", "link/x.png")


def test_save_uploaded_asset_writes_and_snapshots(assets):
    snap = aw.save_uploaded_asset("tpl", "../../etc/evil.png", b"data")
    assert (assets / "tpl" / "evil.png").read_bytes() == b"data"
    assert [f["name"] for f in snap["files"]] == ["evil.png"]


def test_save_uploaded_asset_empty_name(assets):
    with pytest.raises(ValueError):
        aw.save_uploaded_asset("tpl", None, b"x")


def test_delete_asset(assets):
    aw.save_uploaded_asset("tpl", "a.png", b"x")
    snap = aw.delete_asset("tpl", "a.png")
    assert snap["files"] == [] and not (assets / "tpl" / "a.png").exists()
    # deleting something missing still returns a snapshot
    assert aw.delete_asset("tpl", "gone.png")["files"] == []
    with pytest.raises(ValueError):
        aw.delete_asset("tpl", "../x")


def test_request_snapshot_creates_folder(assets):
    snap = aw.request_snapshot("newtpl")
    assert (assets / "newtpl").is_dir() and snap["files"] == []


def test_request_snapshot_empty_name_raises(assets):
    with pytest.raises(ValueError):
        aw.request_snapshot("")


# ---------------------------------------------------------------- reveal

@pytest.mark.parametrize("platform,cmd", [("darwin", "open"), ("linux", "xdg-open")])
def test_reveal_uses_platform_opener(assets, monkeypatch, platform, cmd):
    calls = []
    monkeypatch.setattr(aw.sys, "platform", platform)
    monkeypatch.setattr(aw.subprocess, "Popen", lambda args: calls.append(args))
    folder = aw.reveal_assets_folder("tpl")
    assert calls == [[cmd, folder]] and os.path.isdir(folder)


def test_reveal_windows_startfile(assets, monkeypatch):
    calls = []
    monkeypatch.setattr(aw.sys, "platform", "win32")
    monkeypatch.setattr(aw.os, "startfile", lambda p: calls.append(p), raising=False)
    monkeypatch.setattr(aw.subprocess, "Popen", lambda args: pytest.fail("no Popen on win"))
    folder = aw.reveal_assets_folder("tpl")
    assert calls == [folder]


# ---------------------------------------------------------------- broadcast

def test_broadcast_no_instance(monkeypatch):
    _fake_engine(monkeypatch, None)
    aw._broadcast_changes("t", {})


def test_broadcast_no_socketio(monkeypatch):
    _fake_engine(monkeypatch, SimpleNamespace(socketio=None))
    aw._broadcast_changes("t", {})


def test_broadcast_emits(monkeypatch):
    emitted = []
    sio = SimpleNamespace(emit=lambda name, data: emitted.append((name, data)))
    _fake_engine(monkeypatch, SimpleNamespace(socketio=sio))
    aw._broadcast_changes("t", {"files": []})
    assert emitted == [("spore_studio_assets_changed", {"template": "t", "snapshot": {"files": []}})]


def test_broadcast_emit_error_logged(monkeypatch, caplog):
    def boom(*_a):
        raise RuntimeError("closed")

    _fake_engine(monkeypatch, SimpleNamespace(socketio=SimpleNamespace(emit=boom)))
    aw._broadcast_changes("t", {})
    assert "Failed to emit" in caplog.text


def test_broadcast_import_failure(monkeypatch):
    monkeypatch.delattr(modules, "web_engine", raising=False)
    monkeypatch.setitem(sys.modules, "modules.web_engine", None)
    aw._broadcast_changes("t", {})  # returns quietly


# ---------------------------------------------------------------- watched templates

def test_watched_templates_variants(monkeypatch):
    _fake_engine(monkeypatch, None)
    assert aw._watched_templates() == []
    _fake_engine(monkeypatch, SimpleNamespace())  # no method
    assert aw._watched_templates() == []
    _fake_engine(monkeypatch, SimpleNamespace(get_assets_watch_templates=lambda: set()))
    assert aw._watched_templates() == []
    _fake_engine(monkeypatch, SimpleNamespace(get_assets_watch_templates=lambda: ("a", "b")))
    assert aw._watched_templates() == ["a", "b"]

    def boom():
        raise RuntimeError

    _fake_engine(monkeypatch, SimpleNamespace(get_assets_watch_templates=boom))
    assert aw._watched_templates() == []


# ---------------------------------------------------------------- polling

def test_poll_once_nothing_watched(assets, monkeypatch):
    monkeypatch.setattr(aw, "_watched_templates", lambda: [])
    monkeypatch.setattr(aw, "_scan_template", lambda n: pytest.fail("should not scan"))
    aw._poll_once()


def test_poll_once_baseline_then_change(assets, monkeypatch):
    monkeypatch.setattr(aw, "_watched_templates", lambda: ["tpl", "bad name"])
    sent = []
    monkeypatch.setattr(aw, "_broadcast_changes", lambda n, s: sent.append(n))
    (assets / "tpl").mkdir()
    aw._poll_once()  # first sight: records baseline, no broadcast
    assert sent == [] and aw._known["tpl"] == {}
    aw._poll_once()  # unchanged
    assert sent == []
    (assets / "tpl" / "x.png").write_bytes(b"1")
    aw._poll_once()
    assert sent == ["tpl"] and "x.png" in aw._known["tpl"]
    assert "bad name" not in aw._known


def test_seed_baseline(assets, monkeypatch):
    (assets / "tpl").mkdir()
    (assets / "tpl" / "a.png").write_bytes(b"1")
    monkeypatch.setattr(aw, "_watched_templates", lambda: ["tpl", "../bad"])
    aw._seed_baseline()
    assert list(aw._known) == ["tpl"] and list(aw._known["tpl"]) == ["a.png"]


class FakeSocketIO:
    def __init__(self, stop_after=1, fail_sleep=False, fail_start=False):
        self.sleeps = []
        self.started = []
        self.stop_after = stop_after
        self.fail_sleep = fail_sleep
        self.fail_start = fail_start

    def sleep(self, delay):
        self.sleeps.append(delay)
        if self.fail_sleep:
            raise RuntimeError("loop closed")
        if len(self.sleeps) >= self.stop_after:
            aw.stop_watcher()

    def start_background_task(self, fn, *args):
        if self.fail_start:
            raise RuntimeError("no loop")
        self.started.append((fn, args))


def test_loop_idle_and_active_intervals(assets, monkeypatch):
    watched = iter([[], ["tpl"], ["tpl"]])
    monkeypatch.setattr(aw, "_watched_templates", lambda: next(watched, []))
    monkeypatch.setattr(aw, "_seed_baseline", lambda: None)
    polls = []
    monkeypatch.setattr(aw, "_poll_once", lambda: polls.append(1))
    sio = FakeSocketIO(stop_after=2)
    aw._poller_started = True
    aw._background_poll_loop(sio)
    assert sio.sleeps == [aw._IDLE_POLL_INTERVAL_SECONDS, aw._POLL_INTERVAL_SECONDS]
    assert polls == [1]
    assert aw._poller_started is False


def test_loop_poll_error_logged_and_stop_after_poll(assets, monkeypatch, caplog):
    monkeypatch.setattr(aw, "_watched_templates", lambda: ["tpl"])
    monkeypatch.setattr(aw, "_seed_baseline", lambda: None)

    def bad_poll():
        aw.stop_watcher()
        raise RuntimeError("scan broke")

    monkeypatch.setattr(aw, "_poll_once", bad_poll)
    sio = FakeSocketIO()
    aw._background_poll_loop(sio)
    assert "poll failed" in caplog.text
    assert sio.sleeps == []  # stop seen right after the poll


def test_loop_sleep_error_exits(assets, monkeypatch):
    monkeypatch.setattr(aw, "_watched_templates", lambda: [])
    monkeypatch.setattr(aw, "_seed_baseline", lambda: None)
    sio = FakeSocketIO(fail_sleep=True)
    aw._poller_started = True
    aw._background_poll_loop(sio)
    assert sio.sleeps == [aw._IDLE_POLL_INTERVAL_SECONDS]
    assert aw._poller_started is False


def test_loop_already_stopped(assets, monkeypatch):
    seeded = []
    monkeypatch.setattr(aw, "_seed_baseline", lambda: seeded.append(1))
    aw.stop_watcher()
    sio = FakeSocketIO()
    aw._background_poll_loop(sio)
    assert seeded == [1] and sio.sleeps == []


def test_ensure_background_poller_idempotent(assets):
    sio = FakeSocketIO()
    aw._stop_event.set()
    aw.ensure_background_poller(sio)
    aw.ensure_background_poller(sio)
    assert len(sio.started) == 1
    fn, args = sio.started[0]
    assert fn is aw._background_poll_loop and args == (sio,)
    assert aw._poller_started is True
    assert not aw._stop_event.is_set()


def test_ensure_background_poller_schedule_failure(assets, caplog):
    sio = FakeSocketIO(fail_start=True)
    aw.ensure_background_poller(sio)
    assert aw._poller_started is False
    assert "Could not schedule" in caplog.text


def test_start_and_stop_watcher(assets):
    aw.start_watcher()  # deprecated no-op
    assert not aw._stop_event.is_set()
    aw.stop_watcher()
    assert aw._stop_event.is_set()
