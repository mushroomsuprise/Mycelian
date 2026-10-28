# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.update_sync (manifest planning, downloads, staging and the apply helper).

Nothing is downloaded, installed or executed: aiohttp, subprocess, os.kill and the helper
launch are faked, and every folder (install, staging, data) lives under tmp_path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import aiohttp
import pytest

from modules import update_sync as us

SHA = "a" * 40


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def entry(path="app/file.txt", install=None, action="replace", data=b"x", os_name=None, bundle=False, **extra):
    e = {"path": path, "install": install or path, "action": action, "sha256": sha(data), "size": len(data)}
    if os_name:
        e["os"] = os_name
    if bundle:
        e["bundle"] = True
    e.update(extra)
    return e


# --------------------------------------------------------------------------- fake aiohttp

class FakeContent:
    def __init__(self, body: bytes):
        self.body = body

    async def iter_chunked(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]


class FakeResponse:
    def __init__(self, status=200, body=b"", json_error=None):
        self.status = status
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.content = FakeContent(self.body)
        self.json_error = json_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type="application/json"):
        if self.json_error:
            raise self.json_error
        return json.loads(self.body.decode())

    async def text(self):
        return self.body.decode()

    async def read(self):
        return self.body


class FakeSession:
    def __init__(self, routes, requested):
        self.routes = routes
        self.requested = requested

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url):
        self.requested.append(url)
        route = self.routes.get(url)
        if route is None:
            return FakeResponse(404, b"Not Found")
        if callable(route):
            return route()
        return route


@pytest.fixture
def http(monkeypatch):
    ns = SimpleNamespace(routes={}, requested=[], timeouts=[])

    def client_session(timeout=None):
        ns.timeouts.append(timeout)
        return FakeSession(ns.routes, ns.requested)

    monkeypatch.setattr(us.aiohttp, "ClientSession", client_session)
    return ns


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- basics

def test_exception_hierarchy():
    assert issubclass(us.MissingReleaseFile, us.UpdateSyncError)
    assert issubclass(us.UpdateCancelled, us.UpdateSyncError)


def test_can_apply_updates(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert us.can_apply_updates() is False
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert us.can_apply_updates() is True


@pytest.mark.parametrize("platform, expected", [("win32", "windows"), ("darwin", "macos"), ("linux", "linux"),
                                                ("freebsd", "linux")])
def test_current_platform(monkeypatch, platform, expected):
    monkeypatch.setattr(us.sys, "platform", platform)
    assert us.current_platform() == expected


def test_install_layout_macos_bundle(monkeypatch, tmp_path):
    exe = tmp_path / "Mycelian.app" / "Contents" / "MacOS" / "Mycelian"
    monkeypatch.setattr(us.sys, "platform", "darwin")
    monkeypatch.setattr(us.sys, "executable", str(exe))
    data_root, bundle = us.install_layout()
    app = (tmp_path / "Mycelian.app").resolve()
    assert bundle == app and data_root == app / "Contents" / "MacOS"


def test_install_layout_macos_without_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(us.sys, "platform", "darwin")
    monkeypatch.setattr(us.sys, "executable", str(tmp_path / "bin" / "Mycelian"))
    with pytest.raises(us.UpdateSyncError, match="Mycelian.app bundle"):
        us.install_layout()


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_install_layout_other(monkeypatch, tmp_path, platform):
    monkeypatch.setattr(us.sys, "platform", platform)
    monkeypatch.setattr(us.sys, "executable", str(tmp_path / "app" / "Mycelian.exe"))
    assert us.install_layout() == ((tmp_path / "app").resolve(), None)


def test_local_manifest_path(monkeypatch, tmp_path):
    monkeypatch.setenv("MYCELIAN_DATA_DIR", str(tmp_path))
    assert us.local_manifest_path() == tmp_path / "data" / "update_manifest.json"


@pytest.mark.parametrize("value, expected", [("a/b.txt", Path("a/b.txt")), ("a\\b\\c", Path("a/b/c")),
                                             ("x", Path("x")), ("./a", Path("a")), ("a/.", Path("a"))])
def test_safe_relative_ok(value, expected):
    assert us.safe_relative(value) == expected


@pytest.mark.parametrize("value", ["", "  ", None, 5, "/etc/passwd", "\\abs", "C:/x", "c:x", "a/../b", ".."])
def test_safe_relative_rejects(value):
    with pytest.raises(us.UpdateSyncError):
        us.safe_relative(value)


def test_is_within(tmp_path):
    assert us._is_within(tmp_path / "a" / "b", tmp_path) is True
    assert us._is_within(tmp_path.parent, tmp_path) is False


def test_destination_for(tmp_path):
    data_root = tmp_path / "data"
    bundle_root = tmp_path / "Mycelian.app"
    assert us.destination_for({"install": "a/b"}, data_root, None) == (data_root / "a" / "b").resolve()
    assert us.destination_for({"install": "Contents/x", "bundle": True}, data_root, bundle_root) == \
        (bundle_root / "Contents" / "x").resolve()
    with pytest.raises(us.UpdateSyncError, match="not an app bundle"):
        us.destination_for({"install": "x", "bundle": True}, data_root, None)


def test_destination_for_symlink_escape(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (data_root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(us.UpdateSyncError, match="outside the install folder"):
        us.destination_for({"install": "link/evil"}, data_root, None)


@pytest.mark.parametrize(
    "bad, message",
    [
        ("not a dict", "not an object"),
        ({**entry(), "action": "nuke"}, "unknown action"),
        ({**entry(), "path": "../x"}, "unsafe"),
        ({**entry(), "install": "/abs"}, "absolute"),
        ({**entry(), "sha256": "XYZ"}, "hash is invalid"),
        ({**entry(), "sha256": None}, "hash is invalid"),
        ({**entry(), "size": -1}, "size is invalid"),
        ({**entry(), "size": True}, "size is invalid"),
        ({**entry(), "size": "3"}, "size is invalid"),
    ],
)
def test_validate_entry_rejects(bad, message):
    with pytest.raises(us.UpdateSyncError, match=message):
        us._validate_entry(bad)


def test_validate_entry_accepts_every_action():
    for action in ("replace", "merge", "if-missing"):
        us._validate_entry(entry(action=action))


def test_entries_for_platform():
    shared = entry("shared.txt")
    mac = entry("mac.bin", os_name="macos")
    win = entry("win.exe", os_name="windows")
    assert us.entries_for_platform({"files": [shared, mac, win]}, "macos") == [shared, mac]
    with pytest.raises(us.UpdateSyncError, match="no packaged linux build"):
        us.entries_for_platform({"files": [shared, mac]}, "linux")
    for bad in ({}, {"files": "x"}, [], None):
        with pytest.raises(us.UpdateSyncError, match="missing a file list"):
            us.entries_for_platform(bad, "macos")
    with pytest.raises(us.UpdateSyncError):
        us.entries_for_platform({"files": [{"action": "replace"}]}, "macos")


def test_load_local_manifest(tmp_path, caplog):
    p = tmp_path / "m.json"
    empty = {"version": "", "files": {}}
    assert us.load_local_manifest(p) == empty
    p.write_text("{bad", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=us.logger.name):
        assert us.load_local_manifest(p) == empty
    assert "Ignoring unreadable update manifest" in caplog.text
    p.write_text(json.dumps([1]), encoding="utf-8")
    assert us.load_local_manifest(p) == empty
    p.write_text(json.dumps({"files": []}), encoding="utf-8")
    assert us.load_local_manifest(p) == empty
    p.write_text(json.dumps({"version": 3, "files": {"a": {}}}), encoding="utf-8")
    assert us.load_local_manifest(p) == {"version": "3", "files": {"a": {}}}
    p.write_text(json.dumps({"version": None, "files": {}}), encoding="utf-8")
    assert us.load_local_manifest(p)["version"] == ""


# --------------------------------------------------------------------------- plan_changes

def test_plan_changes_all_paths():
    same = entry("same.txt", data=b"same")
    changed = entry("changed.txt", data=b"new")
    fresh_on_disk = entry("fresh.txt", data=b"fresh")
    merge = entry("cfg.json", action="merge", data=b"{}")
    missing_only = entry("seed.txt", action="if-missing", data=b"seed")
    present_only = entry("seed2.txt", action="if-missing", data=b"seed2")
    entries = [same, changed, fresh_on_disk, merge, missing_only, present_only]
    local = {"files": {
        "same.txt": {"sha256": same["sha256"], "action": "replace"},
        "changed.txt": {"sha256": "0" * 64, "action": "replace"},
        "cfg.json": {"sha256": "1" * 64, "action": "merge"},
        "gone.txt": {"sha256": "2" * 64, "action": "replace", "bundle": True},
        "gone_merge.json": {"sha256": "3" * 64, "action": "merge"},
        "junk": "not a dict",
    }}
    plan = us.plan_changes(
        entries, local,
        dest_exists=lambda e: e["install"] == "seed2.txt",
        installed_unchanged=lambda e: e["install"] in ("fresh.txt", "cfg.json"),
    )
    assert [e["install"] for e in plan["downloads"]] == ["changed.txt", "cfg.json", "seed.txt"]
    assert plan["deletes"] == [{"install": "gone.txt", "bundle": True}]
    assert set(plan["files"]) == {e["install"] for e in entries}
    assert plan["files"]["cfg.json"] == {"sha256": merge["sha256"], "action": "merge", "bundle": False}


def test_plan_changes_without_unchanged_check_and_bad_local():
    e = entry("a.txt")
    for local in ({"files": "nope"}, None, {}):
        plan = us.plan_changes([e], local, dest_exists=lambda _e: True)
        assert plan["downloads"] == [e] and plan["deletes"] == []


# --------------------------------------------------------------------------- SHA / URLs / hashing

@pytest.mark.parametrize("value, expected", [(SHA, SHA), ("  " + "A" * 40 + " ", "a" * 40), ("main", None),
                                             ("a" * 39, None), (None, None), (123, None), ("g" * 40, None)])
def test_normalize_commit_sha(value, expected):
    assert us.normalize_commit_sha(value) == expected


def test_release_file_url():
    assert us.release_file_url("o", "r", SHA, "dir\\with space/f.txt") == \
        f"https://raw.githubusercontent.com/o/r/{SHA}/dir/with%20space/f.txt"
    with pytest.raises(us.UpdateSyncError):
        us.release_file_url("o", "r", "main", "x")


def test_sha256_helpers(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"hello")
    assert us._sha256_file(p) == sha(b"hello") == us._sha256_bytes(b"hello")


def test_content_matches_variants():
    text_lf = b"a\nb\n"
    text_crlf = b"a\r\nb\r\n"
    assert us.content_matches(text_lf, sha(text_lf))
    assert us.content_matches(text_crlf, sha(text_lf))  # CRLF download vs LF manifest
    assert us.content_matches(text_lf, sha(text_crlf))  # LF blob vs CRLF manifest
    assert us.content_matches(b"a\rb\r", sha(text_lf))  # old Mac line endings fold too
    assert not us.content_matches(b"\0a\r\n", sha(b"\0a\n"))  # binary is never folded
    assert not us.content_matches(b"other", sha(text_lf))


def test_file_matches(tmp_path, monkeypatch):
    p = tmp_path / "f.txt"
    assert us.file_matches(p, sha(b"x")) is False
    p.write_bytes(b"a\r\nb\r\n")
    assert us.file_matches(p, sha(b"a\r\nb\r\n")) is True
    assert us.file_matches(p, sha(b"a\nb\n")) is True
    assert us.file_matches(p, sha(b"zzz")) is False


def test_file_matches_large_file_skips_folding(tmp_path):
    p = tmp_path / "big.bin"
    with open(p, "wb") as fh:
        fh.truncate(8 * 1024 * 1024 + 1)
    assert us.file_matches(p, "0" * 64) is False


def test_file_matches_stat_and_read_errors(tmp_path, monkeypatch):
    p = tmp_path / "f.txt"
    p.write_bytes(b"data")
    real_stat = Path.stat
    state = {"fail_stat": False}

    def stat(self, *a, **k):
        if state["fail_stat"] and self == p:
            raise OSError("stat failed")
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(Path, "stat", stat)
    state["fail_stat"] = True
    assert us.file_matches(p, "0" * 64) is False
    state["fail_stat"] = False

    def bad_read(self):
        raise OSError("read failed")

    monkeypatch.setattr(Path, "read_bytes", bad_read)
    assert us.file_matches(p, "0" * 64) is False


def test_directory_writable(tmp_path, monkeypatch):
    assert us._directory_writable(tmp_path / "missing") is False
    assert us._directory_writable(tmp_path) is True
    assert list(tmp_path.iterdir()) == []  # probe removed


def test_directory_not_writable_cleans_probe(tmp_path, monkeypatch):
    real_write = Path.write_text

    def half_write(self, *a, **k):
        real_write(self, *a, **k)
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", half_write)
    assert us._directory_writable(tmp_path) is False
    monkeypatch.undo()
    assert list(tmp_path.iterdir()) == []


def test_directory_not_writable_cleanup_error(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "write_text", lambda self, *a, **k: (_ for _ in ()).throw(OSError("ro")))
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (_ for _ in ()).throw(OSError("ro")))
    assert us._directory_writable(tmp_path) is False


def test_report_and_cancelled():
    state = {}
    us._report(state, "downloading", 1.5, None, "lbl")
    assert state == {"phase": "downloading", "progress": 1.0, "speed_bps": 0.0, "label": "lbl"}
    us._report(state, "x", -1, 10, "l")
    assert state["progress"] == 0.0 and state["speed_bps"] == 10.0
    assert us._cancelled(None) is False
    ev = threading.Event()
    assert us._cancelled(ev) is False
    ev.set()
    assert us._cancelled(ev) is True


# --------------------------------------------------------------------------- staging helpers

@pytest.mark.parametrize(
    "e, expected",
    [({"install": "Mycelian.exe"}, True), ({"install": "Mycelian"}, True), ({"install": "lib/Mycelian"}, False),
     ({"install": "Contents/MacOS/Mycelian", "bundle": True}, True),
     ({"install": "Contents\\MacOS\\Mycelian", "bundle": True}, True),
     ({"install": "Mycelian", "bundle": True}, False), ({}, False)],
)
def test_is_application_binary(e, expected):
    assert us._is_application_binary(e) is expected


def test_staged_file(tmp_path):
    assert us._staged_file(tmp_path, {"install": "a/b"}) == tmp_path / "files" / "a" / "b"
    assert us._staged_file(tmp_path, {"install": "C/x", "bundle": True}) == tmp_path / "bundle" / "C" / "x"


def test_staged_file_symlink_escape(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (staging / "files").symlink_to(outside, target_is_directory=True)
    with pytest.raises(us.UpdateSyncError, match="outside the download folder"):
        us._staged_file(staging, {"install": "x"})


@pytest.fixture
def fake_merge(monkeypatch):
    calls = []

    def merge_config(new_obj, old_obj):
        calls.append((new_obj, old_obj))
        merged = dict(new_obj)
        merged.update({k: v for k, v in old_obj.items() if k in new_obj})
        return merged

    mod = ModuleType("merge_template_configs")
    mod.merge_config = merge_config
    monkeypatch.setitem(sys.modules, "merge_template_configs", mod)
    return calls


def test_merge_staged_templates(tmp_path, fake_merge):
    staging, data_root = tmp_path / "staging", tmp_path / "install"
    merge_e = entry("t/config.json", action="merge")
    new_install = entry("t/new.json", action="merge")
    replace_e = entry("t/other.json")
    for e, content in ((merge_e, {"a": 1, "b": 2}), (new_install, {"z": 1})):
        staged = us._staged_file(staging, e)
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text(json.dumps(content), encoding="utf-8")
    dest = data_root / "t" / "config.json"
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps({"a": "user", "old": True}), encoding="utf-8")
    us.merge_staged_templates(staging, [merge_e, new_install, replace_e], data_root, None)
    merged = json.loads(us._staged_file(staging, merge_e).read_text(encoding="utf-8"))
    assert merged == {"a": "user", "b": 2}
    assert us._staged_file(staging, merge_e).read_text(encoding="utf-8").endswith("}\n")
    assert fake_merge == [({"a": 1, "b": 2}, {"a": "user", "old": True})]
    assert json.loads(us._staged_file(staging, new_install).read_text()) == {"z": 1}


def test_merge_staged_templates_missing_staged(tmp_path, fake_merge):
    with pytest.raises(us.UpdateSyncError, match="Staged template is missing"):
        us.merge_staged_templates(tmp_path, [entry("c.json", action="merge")], tmp_path / "i", None)


def test_merge_staged_templates_bad_json(tmp_path, fake_merge):
    staging, data_root = tmp_path / "s", tmp_path / "i"
    e = entry("c.json", action="merge")
    staged = us._staged_file(staging, e)
    staged.parent.mkdir(parents=True)
    staged.write_text("{}", encoding="utf-8")
    data_root.mkdir()
    (data_root / "c.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(us.UpdateSyncError, match="Could not read template config c.json"):
        us.merge_staged_templates(staging, [e], data_root, None)


def test_merge_staged_templates_real_merge_module(tmp_path):
    """The real merge_template_configs module is importable from the repo root."""
    staging, data_root = tmp_path / "s", tmp_path / "i"
    e = entry("c.json", action="merge")
    staged = us._staged_file(staging, e)
    staged.parent.mkdir(parents=True)
    staged.write_text(json.dumps({"k": 1}), encoding="utf-8")
    us.merge_staged_templates(staging, [e], data_root, None)  # no installed copy: untouched
    assert json.loads(staged.read_text()) == {"k": 1}


# --------------------------------------------------------------------------- downloads

def test_download_file_success(tmp_path, http):
    body = b"y" * (64 * 1024 + 10)
    http.routes["u"] = FakeResponse(200, body)
    got = []
    dest = tmp_path / "sub" / "f.bin"

    async def go():
        async with us.aiohttp.ClientSession() as s:
            await us._download_file(s, "u", dest, sha(body), got.append, None)

    run(go())
    assert dest.read_bytes() == body
    assert got == [64 * 1024, 10]
    assert not (tmp_path / "sub" / "f.bin.partial").exists()


@pytest.mark.parametrize(
    "response, exc, message",
    [
        (FakeResponse(404, b"nf"), us.MissingReleaseFile, "f.bin"),
        (FakeResponse(500, b"Server exploded\nsecond line"), us.UpdateSyncError,
         r"Download failed \(500\) for f.bin. Server exploded"),
        (FakeResponse(503, b"   "), us.UpdateSyncError, r"Download failed \(503\) for f.bin.$"),
        (FakeResponse(200, b"wrong"), us.UpdateSyncError, "did not match the release manifest"),
    ],
)
def test_download_file_failures(tmp_path, http, response, exc, message):
    http.routes["u"] = response
    dest = tmp_path / "f.bin"

    async def go():
        async with us.aiohttp.ClientSession() as s:
            await us._download_file(s, "u", dest, sha(b"right"), lambda n: None, None)

    with pytest.raises(exc, match=message):
        run(go())
    assert not dest.exists()
    assert not (tmp_path / "f.bin.partial").exists()


def test_download_file_cancelled(tmp_path, http):
    http.routes["u"] = FakeResponse(200, b"data")
    ev = threading.Event()
    ev.set()

    async def go():
        async with us.aiohttp.ClientSession() as s:
            await us._download_file(s, "u", tmp_path / "f", sha(b"data"), lambda n: None, ev)

    with pytest.raises(us.UpdateCancelled):
        run(go())
    assert not (tmp_path / "f.partial").exists()


def test_download_file_partial_cleanup_error(tmp_path, http, monkeypatch):
    http.routes["u"] = FakeResponse(200, b"wrong")
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (_ for _ in ()).throw(OSError("busy")))

    async def go():
        async with us.aiohttp.ClientSession() as s:
            await us._download_file(s, "u", tmp_path / "f", sha(b"right"), lambda n: None, None)

    with pytest.raises(us.UpdateSyncError):
        run(go())
    monkeypatch.undo()
    assert (tmp_path / "f.partial").exists()


def manifest_url(owner="o", repo="r"):
    return us.release_file_url(owner, repo, SHA, us.MANIFEST_REPO_PATH)


@pytest.mark.parametrize(
    "response, message",
    [
        (FakeResponse(404), r"Could not read the release file list \(404\)"),
        (FakeResponse(200, b"not json"), "not valid JSON"),
        (FakeResponse(200, b"{}", json_error=aiohttp.ContentTypeError(None, ())), "not valid JSON"),
        (FakeResponse(200, [1, 2]), "not valid JSON"),
    ],
)
def test_fetch_manifest_errors(http, response, message):
    http.routes[manifest_url()] = response

    async def go():
        async with us.aiohttp.ClientSession() as s:
            return await us._fetch_manifest(s, "o", "r", SHA)

    with pytest.raises(us.UpdateSyncError, match=message):
        run(go())


def test_fetch_manifest_ok(http):
    http.routes[manifest_url()] = FakeResponse(200, {"version": "1", "files": []})

    async def go():
        async with us.aiohttp.ClientSession() as s:
            return await us._fetch_manifest(s, "o", "r", SHA)

    assert run(go()) == {"version": "1", "files": []}


# --------------------------------------------------------------------------- apply plan

def test_plan_line():
    assert us._plan_line("COPY", "a", "b") == "COPY\ta\tb"
    for bad in ("a\tb", "a\nb", "a\rb"):
        with pytest.raises(us.UpdateSyncError, match="unusual characters"):
            us._plan_line("DELETE", bad)


def test_relaunch_target(monkeypatch, tmp_path):
    monkeypatch.setattr(us.sys, "platform", "win32")
    assert us._relaunch_target(tmp_path, None) == ("windows", tmp_path / "Mycelian.exe")
    monkeypatch.setattr(us.sys, "platform", "darwin")
    assert us._relaunch_target(tmp_path, tmp_path / "M.app") == ("macos", tmp_path / "M.app")
    with pytest.raises(us.UpdateSyncError, match="to restart"):
        us._relaunch_target(tmp_path, None)
    monkeypatch.setattr(us.sys, "platform", "linux")
    assert us._relaunch_target(tmp_path, None) == ("linux", tmp_path / "Mycelian")


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_write_apply_plan(monkeypatch, tmp_path, platform):
    monkeypatch.setattr(us.sys, "platform", platform)
    us._write_apply_plan(tmp_path, [(tmp_path / "s", tmp_path / "d")], [tmp_path / "old"], [tmp_path / "exe"],
                         "linux", tmp_path / "exe")
    raw = (tmp_path / "apply_plan.txt").read_bytes()
    if platform == "win32":
        assert raw.startswith(b"\xef\xbb\xbf")
        raw = raw[3:]
    lines = raw.decode("utf-8").splitlines()
    assert lines == [
        f"PID\t{os.getpid()}", f"STAGING\t{tmp_path}", f"LAUNCH\tlinux\t{tmp_path / 'exe'}",
        f"COPY\t{tmp_path / 's'}\t{tmp_path / 'd'}", f"DELETE\t{tmp_path / 'old'}", f"CHMOD\t{tmp_path / 'exe'}",
    ]


def test_helper_scripts_cover_every_plan_op():
    ps = us._windows_helper_script()
    sh = us._unix_helper_script()
    for op in ("PID", "STAGING", "LAUNCH", "COPY", "DELETE"):
        assert op in ps and op in sh
    assert "CHMOD" in sh  # Windows plans never carry CHMOD
    assert "helper.pid" in ps and "helper.pid" in sh
    assert sh.startswith("#!/bin/bash")
    assert "PYINSTALLER_RESET_ENVIRONMENT" in ps and "PYINSTALLER_RESET_ENVIRONMENT" in sh
    for var in us._PYINSTALLER_ENV_VARS:
        assert var in sh


def test_sanitized_env(monkeypatch):
    for name in us._PYINSTALLER_ENV_VARS:
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("KEEP_ME", "1")
    env = us._sanitized_env()
    assert not set(us._PYINSTALLER_ENV_VARS) & set(env)
    assert env["KEEP_ME"] == "1" and env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert os.environ["KEEP_ME"] == "1" and "_MEIPASS2" in os.environ  # caller's env untouched


# --------------------------------------------------------------------------- process helpers

def test_pid_alive_posix(monkeypatch):
    monkeypatch.setattr(us.sys, "platform", "linux")
    sent = []

    def fake_kill(pid, sig):
        sent.append((pid, sig))
        if pid == 2:
            raise ProcessLookupError

    monkeypatch.setattr(us.os, "kill", fake_kill)
    assert us._pid_alive(1) is True
    assert us._pid_alive(2) is False
    assert us._pid_alive(0) is False and us._pid_alive(-5) is False and us._pid_alive("3") is False
    assert sent == [(1, 0), (2, 0)]


def test_pid_alive_windows(monkeypatch):
    import ctypes

    calls = []
    kernel32 = SimpleNamespace(
        OpenProcess=lambda access, inherit, pid: calls.append(("open", access, pid)) or (77 if pid == 10 else 0),
        CloseHandle=lambda h: calls.append(("close", h)),
    )
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel32), raising=False)
    monkeypatch.setattr(us.sys, "platform", "win32")
    monkeypatch.setattr(us.os, "kill", lambda *a: pytest.fail("no os.kill on Windows"))
    assert us._pid_alive(10) is True
    assert us._pid_alive(11) is False
    assert calls == [("open", 0x1000, 10), ("close", 77), ("open", 0x1000, 11)]


def test_powershell_executable(monkeypatch, tmp_path):
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    assert us._powershell_executable() == "powershell.exe"
    exe = tmp_path / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert us._powershell_executable() == str(exe)
    monkeypatch.delenv("SystemRoot")
    assert us._powershell_executable() in ("powershell.exe", os.path.join(r"C:\Windows", "System32",
                                                                          "WindowsPowerShell", "v1.0",
                                                                          "powershell.exe"))


def test_popen_hidden_posix(monkeypatch, tmp_path):
    monkeypatch.setattr(us.sys, "platform", "linux")
    calls = []
    proc = SimpleNamespace(pid=5)

    def fake_popen(args, **kw):
        calls.append((args, kw))
        return proc

    monkeypatch.setattr(us.subprocess, "Popen", fake_popen)
    log = tmp_path / "launch.log"
    assert us._popen_hidden(["bash", "apply.sh"], log) is proc
    (args, kw), = calls
    assert args == ["bash", "apply.sh"]
    assert kw["start_new_session"] is True and kw["close_fds"] is True
    assert kw["stdin"] is subprocess.DEVNULL and kw["stdout"] is kw["stderr"]
    assert kw["stdout"].closed  # log handle closed once Popen has it
    assert kw["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert "creationflags" not in kw and log.exists()


def test_popen_hidden_closes_log_on_error(monkeypatch, tmp_path):
    monkeypatch.setattr(us.sys, "platform", "linux")
    handles = []

    def boom(args, **kw):
        handles.append(kw["stdout"])
        raise OSError("no bash")

    monkeypatch.setattr(us.subprocess, "Popen", boom)
    with pytest.raises(OSError):
        us._popen_hidden(["bash"], tmp_path / "l.log")
    assert handles[0].closed


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_write_helper_script(monkeypatch, tmp_path, platform):
    monkeypatch.setattr(us.sys, "platform", platform)
    path = us._write_helper_script(tmp_path)
    if platform == "win32":
        assert path == tmp_path / "apply.ps1"
        assert path.read_bytes().startswith(b"\xef\xbb\xbf$ErrorActionPreference")
    else:
        assert path == tmp_path / "apply.sh"
        assert path.read_text().startswith("#!/bin/bash")
        assert os.stat(path).st_mode & 0o111


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(us.time, "monotonic", c.monotonic)
    monkeypatch.setattr(us.time, "sleep", c.sleep)
    return c


def test_wait_for_helper_pid_found(tmp_path, clock, monkeypatch):
    pid_path = tmp_path / "helper.pid"
    monkeypatch.setattr(us, "_pid_alive", lambda pid: pid == 4321)
    original_sleep = clock.sleep

    def sleep_then_write(s):
        original_sleep(s)
        if len(clock.sleeps) == 1:
            pid_path.write_text("garbage", encoding="ascii")
        elif len(clock.sleeps) == 2:
            pid_path.write_text(" 4321\n", encoding="ascii")

    monkeypatch.setattr(us.time, "sleep", sleep_then_write)
    assert us._wait_for_helper_pid(pid_path, 5.0) == 4321
    assert clock.sleeps == [0.2, 0.2]


def test_wait_for_helper_pid_timeout(tmp_path, clock, monkeypatch):
    pid_path = tmp_path / "helper.pid"
    pid_path.write_text("99", encoding="ascii")
    monkeypatch.setattr(us, "_pid_alive", lambda pid: False)
    assert us._wait_for_helper_pid(pid_path, 1.0) == 0
    assert len(clock.sleeps) == 5


# --------------------------------------------------------------------------- launch_apply_helper

@pytest.fixture
def launcher(monkeypatch):
    ns = SimpleNamespace(popen=[], wait=[], pid=555)
    monkeypatch.setattr(us, "_popen_hidden", lambda args, log: ns.popen.append((args, log)) or SimpleNamespace())
    monkeypatch.setattr(us, "_wait_for_helper_pid", lambda path, timeout: ns.wait.append((path, timeout)) or ns.pid)
    return ns


def test_launch_helper_posix(monkeypatch, tmp_path, launcher):
    monkeypatch.setattr(us.sys, "platform", "linux")
    assert us.launch_apply_helper(tmp_path, elevate=False) == 555
    (args, log), = launcher.popen
    assert args[0] in ("/bin/bash", "bash") and args[1] == str(tmp_path / "apply.sh")
    assert log == tmp_path / "launch.log"
    assert launcher.wait == [(tmp_path / "helper.pid", 15.0)]


def test_launch_helper_posix_without_bin_bash(monkeypatch, tmp_path, launcher):
    monkeypatch.setattr(us.sys, "platform", "linux")
    real_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda self: False if str(self) == "/bin/bash" else real_is_file(self))
    us.launch_apply_helper(tmp_path, elevate=False)
    assert launcher.popen[0][0][0] == "bash"


def test_launch_helper_posix_elevate_refused(monkeypatch, tmp_path, launcher):
    monkeypatch.setattr(us.sys, "platform", "darwin")
    with pytest.raises(us.UpdateSyncError, match="cannot write to its install folder"):
        us.launch_apply_helper(tmp_path, elevate=True)
    assert launcher.popen == []


def test_launch_helper_windows(monkeypatch, tmp_path, launcher):
    monkeypatch.setattr(us.sys, "platform", "win32")
    monkeypatch.setattr(us, "_powershell_executable", lambda: "PS.exe")
    assert us.launch_apply_helper(tmp_path, elevate=False) == 555
    (args, _log), = launcher.popen
    assert args == ["PS.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File",
                    str(tmp_path / "apply.ps1")]
    assert launcher.wait[0][1] == 15.0


def test_launch_helper_windows_elevated(monkeypatch, tmp_path, launcher):
    monkeypatch.setattr(us.sys, "platform", "win32")
    staging = tmp_path / 'we"ird'
    staging.mkdir()
    us.launch_apply_helper(staging, elevate=True)
    (args, _log), = launcher.popen
    vbs = staging / "elevate.vbs"
    assert args == ["wscript.exe", str(vbs)]
    text = vbs.read_bytes().decode("utf-8")
    assert '"runas", 0' in text and "\r\n" in text
    assert str(staging / "apply.ps1").replace('"', '""') in text
    assert launcher.wait[0][1] == 120.0


@pytest.mark.parametrize("elevate, platform, prefix", [
    (False, "linux", "The update helper did not start."),
    (True, "win32", "Update permission was not granted, so Mycelian was not changed."),
])
def test_launch_helper_failure_messages(monkeypatch, tmp_path, launcher, elevate, platform, prefix):
    monkeypatch.setattr(us.sys, "platform", platform)
    monkeypatch.setattr(us, "_powershell_executable", lambda: "PS.exe")
    launcher.pid = 0
    with pytest.raises(us.UpdateSyncError) as info:
        us.launch_apply_helper(tmp_path, elevate=elevate)
    assert str(info.value) == prefix
    (tmp_path / "launch.log").write_text("x" * 600 + "  tail error  ", encoding="utf-8")
    with pytest.raises(us.UpdateSyncError) as info:
        us.launch_apply_helper(tmp_path, elevate=elevate)
    assert str(info.value).startswith(prefix + " ") and str(info.value).endswith("tail error")


# --------------------------------------------------------------------------- Windows Popen path

@pytest.fixture
def windows_startupinfo(monkeypatch):
    class StartupInfo:
        def __init__(self):
            self.dwFlags = 0
            self.wShowWindow = None

    monkeypatch.setattr(us.subprocess, "STARTUPINFO", StartupInfo, raising=False)
    monkeypatch.setattr(us.subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(us.subprocess, "SW_HIDE", 0, raising=False)
    monkeypatch.setattr(us.sys, "platform", "win32")


def test_windows_creationflags():
    with_breakaway = us._windows_creationflags(breakaway=True)
    without = us._windows_creationflags(breakaway=False)
    assert with_breakaway == without | us._CREATE_BREAKAWAY_FROM_JOB
    assert without & us._CREATE_NO_WINDOW and without & us._CREATE_NEW_PROCESS_GROUP
    assert not without & us._DETACHED_PROCESS


def test_popen_hidden_windows_retry(windows_startupinfo, monkeypatch, tmp_path):
    flags = []

    def fake_popen(args, **kw):
        flags.append(kw["creationflags"])
        assert kw["startupinfo"].dwFlags == 1 and kw["startupinfo"].wShowWindow == 0
        assert "start_new_session" not in kw
        if len(flags) == 1:
            raise OSError("breakaway not allowed")
        return "proc"

    monkeypatch.setattr(us.subprocess, "Popen", fake_popen)
    assert us._popen_hidden(["ps"], tmp_path / "l.log") == "proc"
    assert flags == [us._windows_creationflags(breakaway=True), us._windows_creationflags(breakaway=False)]


def test_popen_hidden_windows_first_try(windows_startupinfo, monkeypatch, tmp_path):
    monkeypatch.setattr(us.subprocess, "Popen", lambda args, **kw: kw["creationflags"])
    assert us._popen_hidden(["ps"], tmp_path / "l.log") == us._windows_creationflags(breakaway=True)


# --------------------------------------------------------------------------- sync_release

class CountingEvent:
    """Reports cancelled from the ``trigger``-th is_set() call onwards."""

    def __init__(self, trigger):
        self.trigger = trigger
        self.calls = 0

    def is_set(self):
        self.calls += 1
        return self.calls >= self.trigger


@pytest.fixture
def install(monkeypatch, tmp_path, http, fake_merge):
    """A frozen Linux install under tmp_path with a fake GitHub serving one release."""
    root = tmp_path / "install"
    root.mkdir()
    data_dir = tmp_path / "data"
    monkeypatch.setenv("MYCELIAN_DATA_DIR", str(data_dir))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(us.sys, "platform", "linux")
    monkeypatch.setattr(us.sys, "executable", str(root / "Mycelian"))
    stagings = []

    def mkdtemp(prefix=""):
        d = tmp_path / f"staging{len(stagings)}"
        d.mkdir()
        stagings.append(d)
        return str(d)

    monkeypatch.setattr(us.tempfile, "mkdtemp", mkdtemp)
    launches = []

    def fake_launch(staging, *, elevate):
        launches.append({"staging": staging, "elevate": elevate,
                         "plan": (staging / "apply_plan.txt").read_text(encoding="utf-8")})
        return 4242

    monkeypatch.setattr(us, "launch_apply_helper", fake_launch)
    t = {"now": 0.0}

    def perf():
        t["now"] += 0.3
        return t["now"]

    monkeypatch.setattr(us.time, "perf_counter", perf)
    ns = SimpleNamespace(root=root.resolve(), data_dir=data_dir, http=http, stagings=stagings, launches=launches,
                         files={}, manifest={"version": "2.0", "files": []}, merges=fake_merge)

    def add(path, data, action="replace", os_name="linux", serve=True, install_path=None, bundle=False):
        e = entry(path, install=install_path, action=action, data=data, os_name=os_name, bundle=bundle)
        ns.manifest["files"].append(e)
        if serve:
            http.routes[us.release_file_url("o", "r", SHA, path)] = lambda d=data: FakeResponse(200, d)
        return e

    def write_local(files):
        p = us.local_manifest_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"version": "1.0", "files": files}), encoding="utf-8")

    ns.add, ns.write_local = add, write_local

    def serve_manifest():
        return FakeResponse(200, ns.manifest)

    http.routes[us.release_file_url("o", "r", SHA, us.MANIFEST_REPO_PATH)] = serve_manifest
    return ns


def sync(state=None, cancel=None, commit=SHA):
    state = {} if state is None else state
    return run(us.sync_release(commit_sha=commit, owner="o", repo="r", state=state, cancel_event=cancel))


def test_sync_release_requires_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    with pytest.raises(us.UpdateSyncError, match="running from source"):
        sync()


def test_sync_release_requires_commit(install):
    with pytest.raises(us.UpdateSyncError, match="release commit is missing"):
        sync(commit="main")


def test_sync_release_unwritable_posix(install, monkeypatch):
    monkeypatch.setattr(us, "_directory_writable", lambda p: False)
    with pytest.raises(us.UpdateSyncError, match="cannot write to its install folder"):
        sync()


def test_sync_release_cancelled_before_listing(install):
    ev = threading.Event()
    ev.set()
    state = {}
    with pytest.raises(us.UpdateCancelled):
        sync(state, ev)
    assert state["phase"] == "listing" and install.http.requested == []


def test_sync_release_cancelled_after_planning(install):
    install.add("Mycelian", b"bin")
    with pytest.raises(us.UpdateCancelled):
        sync(cancel=CountingEvent(2))
    assert install.stagings == []


def test_sync_release_already_current(install):
    data = b"same binary"
    install.add("Mycelian", data)
    (install.root / "Mycelian").write_bytes(data)
    state = {}
    assert sync(state) is None
    assert state["phase"] == "current" and state["progress"] == 1.0
    assert "already match" in state["label"]
    assert install.stagings == [] and install.launches == []
    assert install.http.timeouts[0].connect == 30 and install.http.timeouts[0].sock_read == 120


def test_sync_release_full_flow(install):
    binary = b"B" * (64 * 1024 + 5)  # two chunks
    e_bin = install.add("Mycelian", binary)
    e_lib = install.add("lib/a.txt", b"new lib", os_name=None)
    e_cfg = install.add("templates/cfg.json", json.dumps({"a": 1, "b": 2}).encode(), action="merge", os_name=None)
    e_seed = install.add("seed.txt", b"seed", action="if-missing", os_name=None)
    install.add("present.txt", b"p", action="if-missing", os_name=None)
    (install.root / "present.txt").write_bytes(b"user copy")
    (install.root / "templates").mkdir()
    (install.root / "templates" / "cfg.json").write_text(json.dumps({"a": "user"}), encoding="utf-8")
    install.write_local({
        "old.txt": {"sha256": "0" * 64, "action": "replace"},
        "lib//a.txt": {"sha256": "0" * 64, "action": "replace"},  # same file as a copy: not deleted
        "user.json": {"sha256": "0" * 64, "action": "merge"},
    })
    state = {}
    assert sync(state) == 4242
    assert state["phase"] == "restarting" and state["label"] == "Restarting Mycelian..."

    (launch,) = install.launches
    staging = launch["staging"]
    assert launch["elevate"] is False
    assert (staging / "files" / "Mycelian").read_bytes() == binary
    assert json.loads((staging / "files" / "templates" / "cfg.json").read_text()) == {"a": "user", "b": 2}
    assert (staging / "files" / "seed.txt").read_bytes() == b"seed"
    assert not (staging / "files" / "present.txt").exists()
    record = json.loads((staging / "update_manifest.json").read_text())
    assert record["version"] == "2.0"
    assert set(record["files"]) == {"Mycelian", "lib/a.txt", "templates/cfg.json", "seed.txt", "present.txt"}

    lines = launch["plan"].splitlines()
    root = install.root
    assert lines[0] == f"PID\t{os.getpid()}"
    assert lines[1] == f"STAGING\t{staging}"
    assert lines[2] == f"LAUNCH\tlinux\t{root / 'Mycelian'}"
    copies = [l.split("\t")[2] for l in lines if l.startswith("COPY")]
    assert copies == [str(root / "Mycelian"), str(root / "lib" / "a.txt"), str(root / "templates" / "cfg.json"),
                      str(root / "seed.txt"), str(us.local_manifest_path())]
    assert [l for l in lines if l.startswith("DELETE")] == [f"DELETE\t{root / 'old.txt'}"]
    assert [l for l in lines if l.startswith("CHMOD")] == [f"CHMOD\t{root / 'Mycelian'}"]
    assert [e["path"] for e in (e_bin, e_lib, e_cfg, e_seed)]  # entries were all served


def test_sync_release_skips_missing_non_binary(install):
    install.add("Mycelian", b"bin")
    install.add("docs/gone.md", b"doc", os_name=None, serve=False)
    assert sync() == 4242
    record = json.loads((install.launches[0]["staging"] / "update_manifest.json").read_text())
    assert "docs/gone.md" not in record["files"] and "Mycelian" in record["files"]


def test_sync_release_missing_binary_is_fatal(install):
    install.add("Mycelian", b"bin", serve=False)
    with pytest.raises(us.UpdateSyncError, match="missing the application"):
        sync()
    assert not install.stagings[0].exists()  # staging cleaned up


def test_sync_release_everything_missing_means_current(install):
    (install.root / "Mycelian").write_bytes(b"bin")
    install.add("Mycelian", b"bin")
    install.add("docs/gone.md", b"doc", os_name=None, serve=False)
    state = {}
    assert sync(state) is None
    assert state["phase"] == "current"
    assert not install.stagings[0].exists()


def test_sync_release_delete_only(install):
    data = b"bin"
    (install.root / "Mycelian").write_bytes(data)
    install.add("Mycelian", data)
    install.write_local({"Mycelian": {"sha256": sha(data), "action": "replace"},
                         "obsolete.dll": {"sha256": "0" * 64, "action": "replace"}})
    assert sync() == 4242
    lines = install.launches[0]["plan"].splitlines()
    assert f"DELETE\t{install.root / 'obsolete.dll'}" in lines
    assert sum(l.startswith("COPY") for l in lines) == 1  # only the manifest record


@pytest.mark.parametrize("trigger", [3, 4, 6])
def test_sync_release_cancel_points(install, trigger):
    """3: before the first download, 4: mid-download (first chunk), 6: before merging."""
    install.add("Mycelian", b"B" * (64 * 1024 + 1))
    with pytest.raises(us.UpdateCancelled):
        sync(cancel=CountingEvent(trigger))
    assert not install.stagings[0].exists()
    assert install.launches == []


def test_sync_release_windows_elevated(install, monkeypatch, tmp_path):
    root = tmp_path / "winapp"
    root.mkdir()
    monkeypatch.setattr(us.sys, "platform", "win32")
    monkeypatch.setattr(us.sys, "executable", str(root / "Mycelian.exe"))
    monkeypatch.setattr(us, "_directory_writable", lambda p: False)
    install.add("Mycelian.exe", b"exe", os_name="windows")
    state = {}
    assert sync(state) == 4242
    launch = install.launches[0]
    assert launch["elevate"] is True
    assert state["label"] == "Waiting for permission to replace installed files..."
    lines = launch["plan"].splitlines()
    assert f"LAUNCH\twindows\t{root.resolve() / 'Mycelian.exe'}" in lines
    assert not any(l.startswith("CHMOD") for l in lines)


def test_sync_release_macos_bundle(install, monkeypatch, tmp_path):
    app = tmp_path / "Mycelian.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    monkeypatch.setattr(us.sys, "platform", "darwin")
    monkeypatch.setattr(us.sys, "executable", str(app / "Contents" / "MacOS" / "Mycelian"))
    install.add("packaged/macos/Mycelian", b"macbin", os_name="macos", install_path="Contents/MacOS/Mycelian",
                bundle=True)
    assert sync() == 4242
    staging = install.launches[0]["staging"]
    assert (staging / "bundle" / "Contents" / "MacOS" / "Mycelian").read_bytes() == b"macbin"
    lines = install.launches[0]["plan"].splitlines()
    app = app.resolve()
    assert f"LAUNCH\tmacos\t{app}" in lines
    assert f"CHMOD\t{app / 'Contents' / 'MacOS' / 'Mycelian'}" in lines
    assert f"COPY\t{staging / 'bundle' / 'Contents' / 'MacOS' / 'Mycelian'}\t" \
           f"{app / 'Contents' / 'MacOS' / 'Mycelian'}" in lines


def test_sync_release_manifest_without_platform_build(install):
    install.add("Mycelian.exe", b"exe", os_name="windows")
    with pytest.raises(us.UpdateSyncError, match="no packaged linux build"):
        sync()


def test_sync_release_reports_download_progress(install, monkeypatch):
    install.add("Mycelian", b"B" * (3 * 64 * 1024))
    seen = []
    real_report = us._report

    def spy(state, phase, fraction, speed, label):
        real_report(state, phase, fraction, speed, label)
        seen.append((phase, round(state["progress"], 3), state["speed_bps"] > 0, label))

    monkeypatch.setattr(us, "_report", spy)
    sync()
    downloading = [s for s in seen if s[0] == "downloading"]
    assert downloading[0] == ("downloading", 0.0, False, "Downloading Mycelian (1 of 1)")
    assert downloading[-1][1] == 1.0 and downloading[-1][2] is True
    assert [s[0] for s in seen][-2:] == ["merging", "restarting"]
