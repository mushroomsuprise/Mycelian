# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for build.py, part 2: version stamping, binary names and copying, the updater
manifest, the merge helper, the Windows/Linux/macOS installers, argument parsing and main().

Every subprocess (PyInstaller, git, ISCC, makepkg, pkgbuild, productbuild, xcode-select)
is faked, and every path is under tmp_path.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

# Windows has no POSIX execute bits: chmod there only toggles the read-only flag
POSIX_MODES = os.name != "nt"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import build as bld  # noqa: E402


# --------------------------------------------------------------------------- fixtures


class FakeRun:
    """Stands in for subprocess.run. Handlers match on the command's first words."""

    def __init__(self):
        self.calls: list = []
        self.handlers: list = []

    def on(self, prefix, returncode=0, stdout="", stderr="", effect=None, raises=None):
        self.handlers.append((tuple(prefix), returncode, stdout, stderr, effect, raises))
        return self

    def __call__(self, cmd, *args, **kwargs):
        cmd = [str(c) for c in cmd]
        self.calls.append((cmd, kwargs))
        for prefix, rc, out, err, effect, raises in self.handlers:
            if tuple(cmd[: len(prefix)]) == prefix:
                if raises is not None:
                    raise raises
                if effect is not None:
                    effect(cmd, kwargs)
                return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    def commands(self):
        return [c for c, _ in self.calls]


@pytest.fixture
def run(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(bld, "get_project_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def set_os(monkeypatch):
    def _set(name):
        monkeypatch.setattr(bld, "CURRENT_OS", name)

    return _set


@pytest.fixture
def messages(monkeypatch):
    log: list = []
    monkeypatch.setattr(bld.progress, "update", lambda m: log.append(("update", m)))
    monkeypatch.setattr(bld.progress, "error", lambda m: log.append(("error", m)))
    monkeypatch.setattr(bld.progress, "success", lambda m="ok": log.append(("success", m)))
    return log


def _texts(log, kind=None):
    return [m for k, m in log if kind is None or k == kind]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- update_version_across_files


@pytest.fixture
def version_env(root, monkeypatch):
    """Project files with old versions; git commit faked; sys.path and BUILD_NUMBER restored."""
    from modules import build_info

    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(bld, "BUILD_NUMBER", bld.BUILD_NUMBER)
    monkeypatch.setattr(bld, "VERSION", "9.8.7")
    monkeypatch.setattr(bld, "BUILD_DATE", "Jan 1st 2030")
    commits = {"value": "abc123"}
    monkeypatch.setattr(build_info, "get_git_commit", lambda project_root=None: commits["value"])
    (root / "modules").mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "mycelian"\nversion = "1.0.0"\n', encoding="utf-8")
    (root / "uv.lock").write_text(
        '[[package]]\nname = "other"\nversion = "5.0"\n\n[[package]]\nname = "mycelian"\nsource = { editable = "." }\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    (root / "modules" / "database_init.py").write_text(
        "INFO = {\n    'version': '1.0.0',\n    'build_date': 'old',\n    'build_number': 'dev',\n}\n", encoding="utf-8"
    )
    (root / "modules" / "dataobjects.py").write_text(
        'class AppSettings:\n    version: str = "1.0.0"\n    build_date: str = "old"\n    build_number: str = "dev"\n',
        encoding="utf-8",
    )
    (root / "Mycelian.iss").write_text('#define MyAppVersion "1.0.0"\n#define Other "x"\n', encoding="utf-8")
    return commits


def test_update_version_across_files_all(version_env, root):
    assert bld.update_version_across_files() is True
    assert bld.BUILD_NUMBER == "abc123"
    assert sys.path[0] == str(root)
    assert 'version = "9.8.7"' in (root / "pyproject.toml").read_text()
    lock = (root / "uv.lock").read_text()
    assert 'name = "other"\nversion = "5.0"' in lock
    assert lock.endswith('name = "mycelian"\nsource = { editable = "." }\nversion = "9.8.7"\n')
    db = (root / "modules" / "database_init.py").read_text()
    assert "'version': '9.8.7'," in db and "'build_date': 'Jan 1st 2030'," in db and "'build_number': 'abc123'," in db
    do = (root / "modules" / "dataobjects.py").read_text()
    assert 'version: str = "9.8.7"' in do and 'build_date: str = "Jan 1st 2030"' in do
    assert 'build_number: str = "abc123"' in do
    iss = (root / "Mycelian.iss").read_text()
    assert '#define MyAppVersion "9.8.7"' in iss and '#define Other "x"' in iss


def test_update_version_no_git_commit_uses_dev(version_env, root):
    version_env["value"] = None
    assert bld.update_version_across_files() is True
    assert bld.BUILD_NUMBER == "dev"
    assert "'build_number': 'dev'," in (root / "modules" / "database_init.py").read_text()


def test_update_version_uv_lock_without_version_line(version_env, root):
    lock = '[[package]]\nname = "mycelian"\n' + "x = 1\n" * 12 + 'version = "1.0.0"\n'
    (root / "uv.lock").write_text(lock, encoding="utf-8")
    assert bld.update_version_across_files() is True
    assert (root / "uv.lock").read_text() == lock  # version line too far away -> untouched


def test_update_version_file_tuple(version_env, root, monkeypatch):
    monkeypatch.setattr(bld, "VERSION_FILE", "version.txt")
    (root / "version.txt").write_text(
        "filevers=(1, 0, 0, 0),\nprodvers=(1, 0, 0, 0),\n"
        "StringStruct(u'FileVersion', u'1.0.0.0'),\nStringStruct(u'ProductVersion', u'1.0.0.0')\n",
        encoding="utf-8",
    )
    assert bld.update_version_across_files() is True
    text = (root / "version.txt").read_text()
    assert "filevers=(9,8,7,0)" in text and "prodvers=(9,8,7,0)" in text
    assert "StringStruct(u'FileVersion', u'9.8.7.0')" in text
    assert "StringStruct(u'ProductVersion', u'9.8.7.0')" in text


def test_update_version_file_configured_but_missing(version_env, root, monkeypatch):
    monkeypatch.setattr(bld, "VERSION_FILE", "version.txt")
    assert bld.update_version_across_files() is True
    assert not (root / "version.txt").exists()


def test_update_version_only_pyproject(version_env, root):
    for name in ("uv.lock", "Mycelian.iss", "modules/database_init.py", "modules/dataobjects.py"):
        (root / name).unlink()
    assert bld.update_version_across_files() is True
    assert 'version = "9.8.7"' in (root / "pyproject.toml").read_text()


def test_update_version_error_returns_false(version_env, root, capsys):
    (root / "pyproject.toml").unlink()
    (root / "pyproject.toml").mkdir()  # open() fails
    assert bld.update_version_across_files() is False
    captured = capsys.readouterr()
    assert "Error updating version across files" in captured.out
    assert "Traceback" in captured.err


def test_update_version_without_pyproject_still_updates_other_files(version_env, root):
    (root / "pyproject.toml").unlink()
    assert bld.update_version_across_files() is True
    assert "'version': '9.8.7'," in (root / "modules" / "database_init.py").read_text()


# --------------------------------------------------------------------------- binary names / payload


@pytest.mark.parametrize(
    "os_name,app,helper",
    [
        ("windows", "Mycelian.exe", "merge_template_configs.exe"),
        ("macos", "Mycelian.app", "merge_template_configs"),
        ("linux", "Mycelian", "merge_template_configs"),
    ],
)
def test_binary_names(set_os, os_name, app, helper):
    assert bld.app_binary_name(os_name) == app
    assert bld.merge_helper_name(os_name) == helper
    set_os(os_name)
    assert bld.app_binary_name() == app
    assert bld.merge_helper_name() == helper


def test_load_payload_module_real():
    module = bld._load_payload_module()
    assert module.__name__ == "mycelian_install_payload"
    assert callable(module.iter_payload_files) and callable(module.stage_install_payload)


def test_load_payload_module_spec_missing(monkeypatch):
    import importlib.util

    monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *a, **k: None)
    with pytest.raises(ImportError, match="Cannot load installer payload helper"):
        bld._load_payload_module()


# --------------------------------------------------------------------------- copying artifacts


def test_copy_build_artifact_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="Build output missing"):
        bld._copy_build_artifact(tmp_path / "nope", tmp_path / "dest")


def test_copy_build_artifact_directory_replaces(tmp_path):
    src = tmp_path / "src.app"
    (src / "Contents").mkdir(parents=True)
    (src / "Contents" / "new").write_text("new")
    dest = tmp_path / "dest.app"
    dest.mkdir()
    (dest / "stale").write_text("old")
    bld._copy_build_artifact(src, dest)
    assert (dest / "Contents" / "new").read_text() == "new"
    assert not (dest / "stale").exists()


@pytest.mark.parametrize("os_name,executable", [("linux", True), ("windows", False)])
def test_copy_build_artifact_file(tmp_path, set_os, os_name, executable):
    set_os(os_name)
    src = tmp_path / "bin"
    src.write_bytes(b"elf")
    src.chmod(0o644)
    dest = tmp_path / "out" / "deep" / "bin"
    bld._copy_build_artifact(src, dest)
    assert dest.read_bytes() == b"elf"
    if POSIX_MODES:
        assert bool(dest.stat().st_mode & 0o111) is executable


def test_installer_output_dir(tmp_path):
    out = bld.installer_output_dir(tmp_path)
    assert out == tmp_path / "Output" and out.is_dir()
    assert bld.installer_output_dir(tmp_path) == out


def test_copy_binaries_to_project_root(tmp_path, set_os):
    set_os("linux")
    dist = tmp_path / "builds"
    dist.mkdir()
    (dist / "Mycelian").write_bytes(b"app")
    (dist / "merge_template_configs").write_bytes(b"helper")
    app, helper = bld.copy_binaries_to_project_root(tmp_path, dist)
    assert (app, helper) == (tmp_path / "Mycelian", tmp_path / "merge_template_configs")
    assert app.read_bytes() == b"app" and helper.read_bytes() == b"helper"
    assert list(dist.iterdir()) == []


def test_copy_binaries_macos_bundle(tmp_path, set_os):
    set_os("macos")
    dist = tmp_path / "builds"
    (dist / "Mycelian.app" / "Contents").mkdir(parents=True)
    (dist / "merge_template_configs").write_bytes(b"helper")
    app, _ = bld.copy_binaries_to_project_root(tmp_path, dist)
    assert (app / "Contents").is_dir()
    assert not (dist / "Mycelian.app").exists()


def test_copy_binaries_same_folder_kept(tmp_path, set_os, monkeypatch):
    set_os("linux")
    (tmp_path / "Mycelian").write_bytes(b"app")
    (tmp_path / "merge_template_configs").write_bytes(b"helper")
    monkeypatch.setattr(bld, "_copy_build_artifact", lambda src, dest: None)  # copy onto itself is a no-op
    bld.copy_binaries_to_project_root(tmp_path, tmp_path)
    assert (tmp_path / "Mycelian").exists() and (tmp_path / "merge_template_configs").exists()


def test_copy_binaries_missing_source_and_resolve_error(tmp_path, set_os, monkeypatch):
    set_os("linux")
    dist = tmp_path / "builds"
    dist.mkdir()
    (dist / "Mycelian").write_bytes(b"app")
    monkeypatch.setattr(bld, "_copy_build_artifact", lambda src, dest: None)
    real_resolve = Path.resolve

    def resolve(self, *a, **k):
        if self.name == "Mycelian":
            raise OSError("cannot resolve")
        return real_resolve(self, *a, **k)

    monkeypatch.setattr(Path, "resolve", resolve)
    bld.copy_binaries_to_project_root(tmp_path, dist)
    monkeypatch.setattr(Path, "resolve", real_resolve)
    # Unresolvable -> treated as a different file and removed; the missing helper is skipped
    assert not (dist / "Mycelian").exists()


# --------------------------------------------------------------------------- hashing / manifest entries


def test_sha256_file(tmp_path):
    path = tmp_path / "f.bin"
    data = os.urandom(3 * 1024 * 1024 + 7)  # spans several 1 MiB chunks
    path.write_bytes(data)
    assert bld._sha256_file(path) == _sha(data)


def test_release_hash_text_is_lf_normalized(tmp_path):
    path = tmp_path / "a.JSON"
    path.write_bytes(b'{\r\n"a": 1\r}\n')
    assert bld._release_hash_and_size(path) == (_sha(b'{\n"a": 1\n}\n'), 11)


def test_release_hash_text_with_nul_kept_exact(tmp_path):
    path = tmp_path / "a.txt"
    data = b"a\r\n\0b"
    path.write_bytes(data)
    assert bld._release_hash_and_size(path) == (_sha(data), len(data))


def test_release_hash_binary_exact(tmp_path):
    path = tmp_path / "a.png"
    data = b"\x89PNG\r\n"
    path.write_bytes(data)
    assert bld._release_hash_and_size(path) == (_sha(data), len(data))


def test_manifest_entry_minimal(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"abc")
    assert bld._manifest_entry(path, "a\\b\\f.bin", "b\\f.bin", "replace") == {
        "path": "a/b/f.bin",
        "install": "b/f.bin",
        "sha256": _sha(b"abc"),
        "size": 3,
        "action": "replace",
    }


def test_manifest_entry_os_and_bundle(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"abc")
    entry = bld._manifest_entry(path, "p", "i", "if_missing", os_name="macos", bundle=True)
    assert entry["os"] == "macos" and entry["bundle"] is True


def test_manifest_entry_too_large(tmp_path, monkeypatch):
    path = tmp_path / "f.bin"
    path.write_bytes(b"abcd")
    monkeypatch.setattr(bld, "GITHUB_MAX_FILE_BYTES", 4)
    with pytest.raises(RuntimeError, match="GitHub rejects files over 100 MB"):
        bld._manifest_entry(path, "p", "i", "replace")


def test_binary_manifest_entries_windows(tmp_path, set_os):
    set_os("windows")
    os_dir = tmp_path / "packaged" / "windows"
    os_dir.mkdir(parents=True)
    (os_dir / "Mycelian.exe").write_bytes(b"exe")
    (os_dir / ".DS_Store").write_bytes(b"junk")
    entries = bld._binary_manifest_entries(tmp_path)
    assert entries == [
        {
            "path": "packaged/windows/Mycelian.exe",
            "install": "Mycelian.exe",
            "sha256": _sha(b"exe"),
            "size": 3,
            "action": "replace",
            "os": "windows",
        }
    ]


def test_binary_manifest_entries_macos_bundle(tmp_path, set_os):
    set_os("macos")
    app = tmp_path / "packaged" / "macos" / "Mycelian.app"
    (app / "Contents" / "MacOS" / "__pycache__").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "Mycelian").write_bytes(b"bin")
    (app / "Contents" / "Info.plist").write_bytes(b"plist")
    (app / "Contents" / "MacOS" / "__pycache__" / "x.pyc").write_bytes(b"pyc")
    entries = bld._binary_manifest_entries(tmp_path)
    assert [(e["path"], e["install"], e["bundle"], e["os"]) for e in entries] == [
        ("packaged/macos/Mycelian.app/Contents/Info.plist", "Contents/Info.plist", True, "macos"),
        ("packaged/macos/Mycelian.app/Contents/MacOS/Mycelian", "Contents/MacOS/Mycelian", True, "macos"),
    ]


def test_binary_manifest_entries_macos_single_file(tmp_path, set_os):
    set_os("macos")
    os_dir = tmp_path / "packaged" / "macos"
    os_dir.mkdir(parents=True)
    (os_dir / "Mycelian.app").write_bytes(b"one file")
    entries = bld._binary_manifest_entries(tmp_path)
    assert [(e["path"], e["install"]) for e in entries] == [("packaged/macos/Mycelian.app/Mycelian.app", "Mycelian.app")]


def test_binary_manifest_entries_missing(tmp_path, set_os):
    set_os("linux")
    with pytest.raises(FileNotFoundError, match="Packaged binary missing"):
        bld._binary_manifest_entries(tmp_path)


def test_binary_manifest_entries_empty(tmp_path, set_os):
    set_os("linux")
    os_dir = tmp_path / "packaged" / "linux"
    os_dir.mkdir(parents=True)
    (os_dir / ".DS_Store").write_bytes(b"")
    with pytest.raises(FileNotFoundError, match="directory is empty"):
        bld._binary_manifest_entries(tmp_path)


@pytest.mark.parametrize(
    "content,expected",
    [
        (None, []),
        ("{bad", []),
        ("[1]", []),
        ('{"files": "nope"}', []),
        ('{"files": [{"path": "a"}]}', [{"path": "a"}]),
    ],
)
def test_load_manifest_files(tmp_path, content, expected):
    path = tmp_path / "manifest.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert bld._load_manifest_files(path) == expected


def test_load_manifest_files_unreadable(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Path, "read_text", lambda self, **k: (_ for _ in ()).throw(OSError("io")))
    assert bld._load_manifest_files(path) == []


def test_tracked_repo_paths(tmp_path, run):
    run.on(["git", "ls-files"], stdout=b"a.py\0dir\\b.json\0\0")
    assert bld._tracked_repo_paths(tmp_path) == {"a.py", "dir/b.json"}
    cmd, kwargs = run.calls[0]
    assert cmd == ["git", "ls-files", "-z"] and kwargs["cwd"] == tmp_path and kwargs["check"] is False


def test_tracked_repo_paths_git_fails(tmp_path, run):
    run.on(["git"], returncode=128, stdout=b"")
    assert bld._tracked_repo_paths(tmp_path) is None


def test_tracked_repo_paths_git_missing(tmp_path, run):
    run.on(["git"], raises=FileNotFoundError("git"))
    assert bld._tracked_repo_paths(tmp_path) is None


# --------------------------------------------------------------------------- write_update_manifest


@pytest.fixture
def manifest_env(tmp_path, monkeypatch, set_os):
    set_os("linux")
    monkeypatch.setattr(bld, "VERSION", "2.0.0")
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "a.html").write_bytes(b"<a>\r\n")
    (tmp_path / "untracked.txt").write_bytes(b"x")
    (tmp_path / "packaged" / "linux").mkdir(parents=True)
    (tmp_path / "packaged" / "linux" / "Mycelian").write_bytes(b"elf")
    payload = types.SimpleNamespace(
        iter_payload_files=lambda root: iter([("templates/a.html", "replace"), ("untracked.txt", "if_missing")])
    )
    monkeypatch.setattr(bld, "_load_payload_module", lambda: payload)
    return tmp_path


def test_write_update_manifest(manifest_env, monkeypatch, messages):
    monkeypatch.setattr(bld, "_tracked_repo_paths", lambda root: {"templates/a.html"})
    manifest = manifest_env / "packaged" / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "files": [
                    {"path": "packaged/windows/Mycelian.exe", "os": "windows"},
                    {"path": "packaged/linux/old", "os": "linux"},
                    {"path": "templates/old.html"},
                    "garbage",
                ]
            }
        ),
        encoding="utf-8",
    )
    assert bld.write_update_manifest(manifest_env) == manifest
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["version"] == "2.0.0"
    assert [(f["path"], f.get("os")) for f in data["files"]] == [
        ("templates/a.html", None),
        ("packaged/linux/Mycelian", "linux"),
        ("packaged/windows/Mycelian.exe", "windows"),
    ]
    assert data["files"][0]["sha256"] == _sha(b"<a>\n")
    assert manifest.read_bytes().endswith(b"]\n}\n")
    assert _texts(messages) == ["Skipping 1 untracked files that are not in the repository"]


def test_write_update_manifest_without_git(manifest_env, monkeypatch, messages):
    monkeypatch.setattr(bld, "_tracked_repo_paths", lambda root: None)
    path = bld.write_update_manifest(manifest_env)
    files = json.loads(path.read_text(encoding="utf-8"))["files"]
    assert {f["path"] for f in files} == {"templates/a.html", "untracked.txt", "packaged/linux/Mycelian"}
    assert next(f for f in files if f["path"] == "untracked.txt")["action"] == "if_missing"
    assert messages == []


# --------------------------------------------------------------------------- publish_packaged_release


@pytest.mark.parametrize("os_name,dest_name", [("windows", "Mycelian.exe"), ("linux", "Mycelian")])
def test_publish_packaged_release_file(tmp_path, set_os, monkeypatch, messages, os_name, dest_name):
    set_os(os_name)
    stale = tmp_path / "packaged" / os_name / "stale"
    stale.parent.mkdir(parents=True)
    stale.write_text("old")
    app = tmp_path / dest_name
    app.write_bytes(b"app")
    monkeypatch.setattr(bld, "write_update_manifest", lambda root: root / "packaged" / "manifest.json")
    result = bld.publish_packaged_release(tmp_path, app)
    assert result == tmp_path / "packaged" / "manifest.json"
    assert (tmp_path / "packaged" / os_name / dest_name).read_bytes() == b"app"
    assert not stale.exists()
    assert _texts(messages) == [f"Updater manifest: {result}"]


def test_publish_packaged_release_macos(tmp_path, set_os, monkeypatch, messages):
    set_os("macos")
    app = tmp_path / "Mycelian.app"
    (app / "Contents").mkdir(parents=True)
    monkeypatch.setattr(bld, "write_update_manifest", lambda root: root / "m.json")
    bld.publish_packaged_release(tmp_path, app)
    assert (tmp_path / "packaged" / "macos" / "Mycelian.app" / "Contents").is_dir()


# --------------------------------------------------------------------------- build_merge_helper


def test_build_merge_helper_success(tmp_path, run, set_os):
    set_os("linux")
    dist = tmp_path / "builds"
    dist.mkdir()
    run.on([sys.executable], effect=lambda c, k: (dist / "merge_template_configs").write_bytes(b"x"))
    assert bld.build_merge_helper(tmp_path, dist) is True
    work = tmp_path / "build" / "merge_template_configs"
    assert work.is_dir()
    cmd, kwargs = run.calls[0]
    assert cmd == [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
        "--name", "merge_template_configs", "--distpath", str(dist), "--workpath", str(work),
        "--specpath", str(work), str(tmp_path / "merge_template_configs.py"),
    ]
    assert kwargs["cwd"] == tmp_path


def test_build_merge_helper_failure_reports_tail(tmp_path, run, set_os, messages):
    set_os("linux")
    run.on([sys.executable], returncode=1, stderr="x" * 3000 + "END")
    assert bld.build_merge_helper(tmp_path, tmp_path) is False
    detail = _texts(messages)[0]
    assert len(detail) == 2000 and detail.endswith("END")


def test_build_merge_helper_missing_output_quiet(tmp_path, run, set_os, messages):
    set_os("linux")
    run.on([sys.executable], returncode=0)
    assert bld.build_merge_helper(tmp_path, tmp_path) is False
    assert messages == []


def test_build_merge_helper_uses_stdout_when_no_stderr(tmp_path, run, set_os, messages):
    set_os("windows")
    run.on([sys.executable], returncode=1, stdout=" out \n")
    assert bld.build_merge_helper(tmp_path, tmp_path) is False
    assert _texts(messages) == ["out"]


# --------------------------------------------------------------------------- Inno Setup


def test_find_iscc_on_path(monkeypatch):
    monkeypatch.setattr(bld.shutil, "which", lambda name: "/opt/ISCC" if name == "iscc" else None)
    assert bld.find_iscc() == Path("/opt/ISCC")


def test_find_iscc_program_files(monkeypatch, tmp_path):
    monkeypatch.setattr(bld.shutil, "which", lambda name: None)
    iscc = tmp_path / "pf" / "Inno Setup 6" / "ISCC.exe"
    iscc.parent.mkdir(parents=True)
    iscc.write_bytes(b"")
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "pf"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))  # duplicate is checked once
    assert bld.find_iscc() == iscc


def test_find_iscc_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(bld.shutil, "which", lambda name: None)
    # Both variables name the same missing folder: the duplicate candidate is skipped
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "none"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "none"))
    checked = []
    # Pretend nothing is installed, even when this machine has Inno Setup in Program Files
    monkeypatch.setattr(Path, "is_file", lambda self: checked.append(str(self)) and False)
    with pytest.raises(FileNotFoundError, match="Inno Setup compiler"):
        bld.find_iscc()
    assert checked.count(str(tmp_path / "none" / "Inno Setup 6" / "ISCC.exe")) == 1
    assert len(checked) == 3


def test_find_iscc_without_env(monkeypatch):
    monkeypatch.setattr(bld.shutil, "which", lambda name: None)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    with pytest.raises(FileNotFoundError):
        bld.find_iscc()


FAKE_ISCC = str(Path("/fake/ISCC.exe"))  # backslashes on Windows


@pytest.fixture
def inno_env(tmp_path, monkeypatch):
    monkeypatch.setattr(bld, "find_iscc", lambda: Path(FAKE_ISCC))
    monkeypatch.setattr(bld, "VERSION", "3.0.0")
    (tmp_path / "Mycelian.iss").write_text("; script")
    return tmp_path


def test_build_inno_setup_missing_script(tmp_path, monkeypatch):
    monkeypatch.setattr(bld, "find_iscc", lambda: Path("/fake/ISCC.exe"))
    with pytest.raises(FileNotFoundError, match="Inno Setup script not found"):
        bld.build_inno_setup(tmp_path)


def test_build_inno_setup_success(inno_env, run, messages):
    out = inno_env / "Output"
    run.on([FAKE_ISCC], effect=lambda c, k: (out / "Mycelian v3.0.0.exe").write_bytes(b"x"))
    assert bld.build_inno_setup(inno_env) == out / "Mycelian v3.0.0.exe"
    assert run.calls[0] == ([FAKE_ISCC, f"/O{out}", str(inno_env / "Mycelian.iss")], {"cwd": inno_env})
    assert _texts(messages) == [f"Running {FAKE_ISCC}"]


def test_build_inno_setup_falls_back_to_latest_match(inno_env, run):
    out = inno_env / "Output"

    def make(c, k):
        (out / "Mycelian v1.0.0.exe").write_bytes(b"x")
        (out / "Mycelian v2.0.0.exe").write_bytes(b"x")

    run.on([FAKE_ISCC], effect=make)
    assert bld.build_inno_setup(inno_env) == out / "Mycelian v2.0.0.exe"


def test_build_inno_setup_no_installer(inno_env, run):
    run.on([FAKE_ISCC])
    with pytest.raises(FileNotFoundError, match="no installer was found"):
        bld.build_inno_setup(inno_env)


def test_build_inno_setup_failure(inno_env, run):
    run.on([FAKE_ISCC], returncode=2)
    with pytest.raises(RuntimeError, match="exit code 2"):
        bld.build_inno_setup(inno_env)


# --------------------------------------------------------------------------- Arch Linux


@pytest.fixture
def os_release(tmp_path, monkeypatch):
    target = tmp_path / "os-release"
    real_path = bld.Path

    def fake_path(*parts):
        if parts == ("/etc/os-release",):
            return target
        return real_path(*parts)

    monkeypatch.setattr(bld, "Path", fake_path)
    return target


def test_linux_is_arch_missing_file(os_release):
    assert bld._linux_is_arch() is False


@pytest.mark.parametrize(
    "content,expected",
    [
        ('# comment\n\nNAME="Arch Linux"\nID=arch\n', True),
        ("ID='cachyos'\n", True),
        ('ID=endeavouros\nID_LIKE="arch"\n', True),
        ('ID=manjaro\nID_LIKE="manjaro arch"\n', True),
        ("ID=ubuntu\nID_LIKE=debian\nnot a pair\n", False),
        ("", False),
    ],
)
def test_linux_is_arch(os_release, content, expected):
    os_release.write_text(content, encoding="utf-8")
    assert bld._linux_is_arch() is expected


@pytest.mark.parametrize(
    "machine,expected",
    [("x86_64", "x86_64"), ("AMD64", "x86_64"), ("aarch64", "aarch64"), ("arm64", "aarch64"), ("i686", "i686"), ("riscv64", "riscv64")],
)
def test_arch_package_arch(monkeypatch, machine, expected):
    monkeypatch.setattr(bld.platform, "machine", lambda: machine)
    assert bld._arch_package_arch() == expected


def test_replace_pkgbuild_line():
    text = "pkgname=mycelian\n  pkgver=1.0\narch=('any')"
    out = bld._replace_pkgbuild_line(text, "pkgver=", "pkgver=2.0")
    assert out == "pkgname=mycelian\npkgver=2.0\narch=('any')"
    out = bld._replace_pkgbuild_line(out, "arch=", "arch=('x86_64')")
    assert out.endswith("arch=('x86_64')")  # no newline added when the last line had none


def test_replace_pkgbuild_line_missing():
    with pytest.raises(RuntimeError, match="PKGBUILD is missing a 'pkgver=' line"):
        bld._replace_pkgbuild_line("pkgname=x\n", "pkgver=", "pkgver=1")


def test_normalize_lf(tmp_path):
    path = tmp_path / "f"
    path.write_bytes(b"a\r\nb\rc\n")
    bld._normalize_lf(path)
    assert path.read_bytes() == b"a\nb\nc\n"


def test_normalize_lf_unchanged_not_rewritten(tmp_path, monkeypatch):
    path = tmp_path / "f"
    path.write_bytes(b"a\nb\n")
    monkeypatch.setattr(Path, "write_bytes", lambda self, data: pytest.fail("should not write"))
    bld._normalize_lf(path)


@pytest.fixture
def arch_env(tmp_path, monkeypatch, set_os):
    set_os("linux")
    monkeypatch.setattr(bld, "VERSION", "4.5.6")
    monkeypatch.setattr(bld.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(bld, "_linux_is_arch", lambda: True)
    monkeypatch.setattr(bld, "_arch_package_arch", lambda: "x86_64")
    monkeypatch.setattr(bld.shutil, "which", lambda name: "/usr/bin/makepkg" if name == "makepkg" else None)
    arch = tmp_path / "packaging" / "arch"
    arch.mkdir(parents=True)
    (arch / "PKGBUILD").write_bytes(b"pkgname=mycelian\r\npkgver=0.0.1\r\narch=('any')\r\n")
    (arch / "mycelian.install").write_bytes(b"post_install() {\r\n}\r\n")
    (arch / "mycelian.sh").write_bytes(b"#!/bin/sh\n")
    (arch / "payload" / "stale").mkdir(parents=True)
    (tmp_path / "Mycelian").write_bytes(b"app")
    (tmp_path / "merge_template_configs").write_bytes(b"helper")
    staged = []

    def stage(dest, project_root):
        staged.append((dest, project_root))
        dest.mkdir(parents=True)
        (dest / "templates").mkdir()

    monkeypatch.setattr(bld, "_load_payload_module", lambda: types.SimpleNamespace(stage_install_payload=stage))
    return types.SimpleNamespace(root=tmp_path, arch=arch, staged=staged)


def test_build_arch_package_success(arch_env, run, messages):
    out = arch_env.root / "Output"

    def makepkg(cmd, kwargs):
        older = out / "mycelian-4.5.6-1-x86_64.pkg.tar.xz"
        newer = out / "mycelian-4.5.6-2-x86_64.pkg.tar.zst"
        older.write_bytes(b"")
        newer.write_bytes(b"")
        os.utime(older, (1, 1))
        os.utime(newer, (2, 2))

    run.on(["/usr/bin/makepkg"], effect=makepkg)
    result = bld.build_arch_package(arch_env.root)
    assert result == out / "mycelian-4.5.6-2-x86_64.pkg.tar.zst"
    assert (arch_env.arch / "PKGBUILD").read_bytes() == b"pkgname=mycelian\npkgver=4.5.6\narch=('x86_64')\n"
    assert (arch_env.arch / "mycelian.install").read_bytes() == b"post_install() {\n}\n"
    payload = arch_env.arch / "payload"
    assert arch_env.staged == [(payload, arch_env.root)]
    assert not (payload / "stale").exists()
    assert (payload / "Mycelian").read_bytes() == b"app"
    assert (payload / "merge_template_configs").read_bytes() == b"helper"
    cmd, kwargs = run.calls[0]
    assert cmd == ["/usr/bin/makepkg", "-f", "--noconfirm"]
    assert kwargs["cwd"] == arch_env.arch and kwargs["env"]["PKGDEST"] == str(out)
    assert "PKGDEST" not in os.environ
    assert _texts(messages) == ["Running makepkg"]


def test_build_arch_package_as_root(arch_env, monkeypatch):
    monkeypatch.setattr(bld.os, "geteuid", lambda: 0, raising=False)
    with pytest.raises(RuntimeError, match="cannot be run as root"):
        bld.build_arch_package(arch_env.root)


def test_build_arch_package_without_geteuid(arch_env, monkeypatch, run):
    monkeypatch.delattr(bld.os, "geteuid", raising=False)
    run.on(["/usr/bin/makepkg"], returncode=1)
    with pytest.raises(RuntimeError, match="makepkg failed with exit code 1"):
        bld.build_arch_package(arch_env.root)


def test_build_arch_package_not_arch(arch_env, monkeypatch):
    monkeypatch.setattr(bld, "_linux_is_arch", lambda: False)
    with pytest.raises(RuntimeError, match="does not look like Arch"):
        bld.build_arch_package(arch_env.root)


def test_build_arch_package_without_makepkg(arch_env, monkeypatch):
    monkeypatch.setattr(bld.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="makepkg was not found"):
        bld.build_arch_package(arch_env.root)


def test_build_arch_package_no_output(arch_env, run):
    run.on(["/usr/bin/makepkg"])
    with pytest.raises(FileNotFoundError, match="no package was found"):
        bld.build_arch_package(arch_env.root)


def test_build_arch_package_no_existing_payload(arch_env, run):
    import shutil

    shutil.rmtree(arch_env.arch / "payload")
    run.on(["/usr/bin/makepkg"], effect=lambda c, k: (arch_env.root / "Output" / "mycelian-4.5.6-1-any.pkg.tar.zst").write_bytes(b""))
    assert bld.build_arch_package(arch_env.root).name == "mycelian-4.5.6-1-any.pkg.tar.zst"


# --------------------------------------------------------------------------- macOS pkg


@pytest.mark.parametrize("machine,expected", [("arm64", "arm64"), ("AARCH64", "arm64"), ("x86_64", "x86_64"), ("i386", "x86_64")])
def test_macos_host_arch(monkeypatch, machine, expected):
    monkeypatch.setattr(bld.platform, "machine", lambda: machine)
    assert bld._macos_host_arch() == expected


@pytest.fixture
def mac_env(tmp_path, monkeypatch, set_os):
    set_os("macos")
    monkeypatch.setattr(bld, "VERSION", "7.7.7")
    monkeypatch.setattr(bld, "_macos_host_arch", lambda: "arm64")
    monkeypatch.setattr(bld.shutil, "which", lambda name: f"/usr/bin/{name}")
    (tmp_path / "Mycelian.app" / "Contents" / "MacOS").mkdir(parents=True)
    (tmp_path / "Mycelian.app" / "Contents" / "MacOS" / "Mycelian").write_bytes(b"bin")
    (tmp_path / "merge_template_configs").write_bytes(b"helper")
    scripts = tmp_path / "packaging" / "macos" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "postinstall").write_bytes(b"#!/bin/sh\r\nexit 0\r\n")
    stale = tmp_path / "builds" / "pkg-staging" / "stale"
    stale.parent.mkdir(parents=True)
    stale.write_text("old")
    staged = []

    def stage(dest, project_root):
        staged.append(dest)
        (dest / "templates").mkdir()

    monkeypatch.setattr(bld, "_load_payload_module", lambda: types.SimpleNamespace(stage_install_payload=stage))
    return types.SimpleNamespace(root=tmp_path, staged=staged)


def test_build_macos_pkg_success(mac_env, run, messages):
    root = mac_env.root
    staging = root / "builds" / "pkg-staging"
    seen = {}

    def pkgbuild(cmd, kwargs):
        macos = staging / "root" / "Applications" / "Mycelian.app" / "Contents" / "MacOS"
        seen["files"] = sorted(p.name for p in macos.iterdir())
        post = staging / "scripts" / "postinstall"
        seen["postinstall"] = (post.read_bytes(), post.stat().st_mode & 0o777)
        seen["stale"] = (staging / "stale").exists()

    def productbuild(cmd, kwargs):
        seen["distribution"] = (staging / "distribution.xml").read_text(encoding="utf-8")

    run.on(["/usr/bin/pkgbuild"], effect=pkgbuild).on(["/usr/bin/productbuild"], effect=productbuild)
    installer = bld.build_macos_pkg(root)
    assert installer == root / "Output" / "Mycelian-7.7.7.pkg"
    assert seen["files"] == ["Mycelian", "merge_template_configs", "templates"]
    assert seen["postinstall"][0] == b"#!/bin/sh\nexit 0\n"
    if POSIX_MODES:
        assert seen["postinstall"][1] == 0o755
    assert seen["stale"] is False
    assert 'hostArchitectures="arm64"' in seen["distribution"]
    assert 'version="7.7.7"' in seen["distribution"]
    assert seen["distribution"].startswith('<?xml version="1.0" encoding="utf-8"?>')
    assert run.commands() == [
        ["/usr/bin/pkgbuild", "--root", str(staging / "root"), "--identifier", "com.mycelian.app", "--version", "7.7.7",
         "--install-location", "/", "--scripts", str(staging / "scripts"), str(staging / "Mycelian-component.pkg")],
        ["/usr/bin/productbuild", "--distribution", str(staging / "distribution.xml"), "--package-path", str(staging), str(installer)],
    ]
    assert not staging.exists()
    assert _texts(messages) == ["Running pkgbuild", "Running productbuild"]


@pytest.mark.parametrize("missing", ["pkgbuild", "productbuild"])
def test_build_macos_pkg_tools_missing(mac_env, monkeypatch, missing):
    monkeypatch.setattr(bld.shutil, "which", lambda name: None if name == missing else f"/usr/bin/{name}")
    with pytest.raises(RuntimeError, match="Xcode command line tools"):
        bld.build_macos_pkg(mac_env.root)


def test_build_macos_pkg_bad_bundle(mac_env):
    import shutil

    shutil.rmtree(mac_env.root / "Mycelian.app" / "Contents" / "MacOS")
    with pytest.raises(FileNotFoundError, match="missing Contents/MacOS"):
        bld.build_macos_pkg(mac_env.root)


def test_build_macos_pkg_pkgbuild_fails(mac_env, run):
    run.on(["/usr/bin/pkgbuild"], returncode=3)
    with pytest.raises(RuntimeError, match="pkgbuild failed with exit code 3"):
        bld.build_macos_pkg(mac_env.root)


def test_build_macos_pkg_productbuild_fails(mac_env, run):
    run.on(["/usr/bin/pkgbuild"]).on(["/usr/bin/productbuild"], returncode=4)
    with pytest.raises(RuntimeError, match="productbuild failed with exit code 4"):
        bld.build_macos_pkg(mac_env.root)


# --------------------------------------------------------------------------- build_installer / args


@pytest.mark.parametrize("os_name,func", [("windows", "build_inno_setup"), ("linux", "build_arch_package"), ("macos", "build_macos_pkg")])
def test_build_installer_dispatch(tmp_path, set_os, monkeypatch, os_name, func):
    set_os(os_name)
    for name in ("build_inno_setup", "build_arch_package", "build_macos_pkg"):
        monkeypatch.setattr(bld, name, lambda root, name=name: (name, root))
    assert bld.build_installer(tmp_path) == (func, tmp_path)


def test_build_installer_unknown_os(tmp_path, set_os):
    set_os("plan9")
    with pytest.raises(RuntimeError, match="No installer is configured for plan9"):
        bld.build_installer(tmp_path)


def test_parse_args_skip_installer():
    assert bld._parse_args([]).skip_installer is False
    assert bld._parse_args(["--skip-installer"]).skip_installer is True


def test_parse_args_rejects_unknown(capsys):
    with pytest.raises(SystemExit) as exc:
        bld._parse_args(["--bogus"])
    assert exc.value.code == 2


# --------------------------------------------------------------------------- main()


@pytest.fixture
def main_env(tmp_path, monkeypatch, set_os, messages, run):
    """Every build step faked. Records the order steps run in.

    Any build.py function with "test" in its name (the test-suite gate the owner is adding)
    is stubbed out, so these tests never start a real test run.
    """
    set_os("linux")
    monkeypatch.setattr(bld, "get_project_root", lambda: tmp_path)
    (tmp_path / "main.py").write_text("")
    order: list = []
    for name in dir(bld):
        if "test" in name.lower() and callable(getattr(bld, name)) and not name.startswith("__"):
            monkeypatch.setattr(bld, name, lambda *a, _n=name, **k: order.append(_n))
    dist = tmp_path / "builds"

    def step(name, result=None):
        def _f(*a, **k):
            order.append(name)
            return result

        monkeypatch.setattr(bld, name, _f)

    step("ensure_dependencies")
    step("clean_build")
    step("update_version_across_files", True)
    step("build_streamdeck_plugin", True)
    step("build_executable", (True, dist))
    step("build_merge_helper", True)
    step("copy_binaries_to_project_root", (tmp_path / "Mycelian", tmp_path / "merge_template_configs"))
    step("publish_packaged_release", tmp_path / "packaged" / "manifest.json")
    step("post_build_tasks")
    step("build_installer", tmp_path / "Output" / "pkg")
    monkeypatch.setattr(bld.progress, "next_step", lambda m: order.append(("step", m)))
    monkeypatch.setattr(bld.progress, "summary", lambda: order.append("summary"))
    monkeypatch.setattr(bld.progress, "total_steps", bld.progress.total_steps)
    monkeypatch.setattr(sys, "argv", ["build.py", "--skip-tests"])
    return types.SimpleNamespace(root=tmp_path, order=order, step=step, run=run, dist=dist)


def _calls(order):
    return [o for o in order if isinstance(o, str)]


def test_main_full_build(main_env, messages, capsys):
    (main_env.root / "pyi_rth_dll_fix.py").write_text("")
    bld.main()
    calls = [c for c in _calls(main_env.order) if "test" not in c]
    assert calls == [
        "ensure_dependencies", "clean_build", "update_version_across_files", "build_streamdeck_plugin",
        "build_executable", "build_merge_helper", "copy_binaries_to_project_root", "publish_packaged_release",
        "post_build_tasks", "build_installer", "summary",
    ]
    steps = [m for o in main_env.order if isinstance(o, tuple) for m in o[1:]]
    assert steps[-1] == "Building installer"
    assert "Checking dependencies" in steps and "Copying binaries into the project folder" in steps
    assert f"Installer: {main_env.root / 'Output' / 'pkg'}" in _texts(messages)
    assert "Cleaned up 1 temporary files" in _texts(messages)
    assert not (main_env.root / "pyi_rth_dll_fix.py").exists()
    out = capsys.readouterr().out
    assert f"Mycelian Build Script v{bld.VERSION} - LINUX" in out
    assert main_env.run.calls == []


def test_main_skip_installer(main_env, monkeypatch, messages):
    monkeypatch.setattr(sys, "argv", ["build.py", "--skip-tests", "--skip-installer"])
    bld.main()
    assert "build_installer" not in main_env.order
    assert "Installer skipped (--skip-installer)" in _texts(messages)
    assert "summary" in main_env.order
    assert "Building installer" not in [m for o in main_env.order if isinstance(o, tuple) for m in o[1:]]


def test_main_without_main_py(main_env, messages):
    (main_env.root / "main.py").unlink()
    with pytest.raises(SystemExit) as exc:
        bld.main()
    assert exc.value.code == 1
    assert _texts(messages, "error") == [f"main.py not found in {main_env.root}"]
    assert "ensure_dependencies" not in main_env.order


def test_main_macos_xcode_present(main_env, set_os):
    set_os("macos")
    main_env.run.on(["xcode-select", "-p"], stdout="/Library/Developer/CommandLineTools")
    bld.main()
    assert main_env.run.commands() == [["xcode-select", "-p"]]
    assert "summary" in main_env.order


def test_main_macos_xcode_missing_cancel(main_env, set_os, monkeypatch, messages):
    set_os("macos")
    main_env.run.on(["xcode-select"], returncode=2)
    monkeypatch.setattr("builtins.input", lambda prompt: " N ")
    with pytest.raises(SystemExit) as exc:
        bld.main()
    assert exc.value.code == 1
    assert "Xcode command line tools not found!" in _texts(messages, "error")
    assert _texts(messages)[-1] == "Build cancelled."
    assert "ensure_dependencies" not in main_env.order


def test_main_macos_xcode_missing_continue(main_env, set_os, monkeypatch):
    set_os("macos")
    main_env.run.on(["xcode-select"], returncode=2)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "Y")
    bld.main()
    assert prompts == ["Continue anyway? (y/N): "]
    assert "summary" in main_env.order


def test_main_macos_xcode_select_not_installed(main_env, set_os, messages):
    set_os("macos")
    main_env.run.on(["xcode-select"], raises=FileNotFoundError("xcode-select"))
    with pytest.raises(SystemExit) as exc:
        bld.main()
    assert exc.value.code == 1
    assert _texts(messages, "error") == ["xcode-select command not found"]


@pytest.mark.parametrize(
    "name,result,error",
    [
        ("build_streamdeck_plugin", False, "Stream Deck plugin build failed"),
        ("build_executable", (False, None), "Build failed"),
        ("build_merge_helper", False, "merge_template_configs build failed"),
    ],
)
def test_main_step_failures_exit(main_env, messages, name, result, error):
    main_env.step(name, result)
    with pytest.raises(SystemExit) as exc:
        bld.main()
    assert exc.value.code == 1
    assert error in _texts(messages, "error")
    assert "summary" not in main_env.order


def test_main_keyboard_interrupt(main_env, monkeypatch, messages):
    def interrupted():
        raise KeyboardInterrupt

    monkeypatch.setattr(bld, "clean_build", interrupted)
    with pytest.raises(SystemExit) as exc:
        bld.main()
    assert exc.value.code == 1
    assert _texts(messages, "error") == ["Build interrupted by user"]


def test_main_unexpected_error(main_env, monkeypatch, messages, capsys):
    (main_env.root / "pyi_rth_a.py").write_text("")
    monkeypatch.setattr(bld, "build_installer", lambda root: (_ for _ in ()).throw(RuntimeError("makepkg exploded")))
    with pytest.raises(SystemExit) as exc:
        bld.main()
    assert exc.value.code == 1
    assert _texts(messages, "error") == ["Build failed: makepkg exploded"]
    assert "Traceback" in capsys.readouterr().err
    # finally block still cleans the runtime hooks
    assert not (main_env.root / "pyi_rth_a.py").exists()


def test_main_cleanup_tolerates_unlink_errors(main_env, monkeypatch, messages):
    (main_env.root / "pyi_rth_a.py").write_text("")
    real_unlink = Path.unlink

    def unlink(self, *a, **k):
        if self.name.startswith("pyi_rth_"):
            raise PermissionError("locked")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", unlink)
    bld.main()
    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert (main_env.root / "pyi_rth_a.py").exists()
    assert not any("Cleaned up" in m for m in _texts(messages))


def test_script_entry_point_calls_main(monkeypatch, tmp_path):
    """Run build.py's source as __main__ with main() unable to do anything but exit early."""
    code = compile((ROOT / "build.py").read_text(encoding="utf-8"), str(ROOT / "build.py"), "exec")
    ns = {"__name__": "__main__", "__file__": str(tmp_path / "build_under_test.py"), "__builtins__": __builtins__}
    monkeypatch.setattr(sys, "argv", ["build.py", "--skip-tests"])
    # __file__ points at an empty tmp folder, so main() stops at the "main.py not found" check
    with pytest.raises(SystemExit) as exc:
        exec(code, ns)
    assert exc.value.code == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX links and modes")
def test_binary_manifest_entries_macos_links_and_exec(tmp_path, set_os):
    set_os("macos")
    app = tmp_path / "packaged" / "macos" / "Mycelian.app"
    frameworks = app / "Contents" / "Frameworks"
    resources = app / "Contents" / "Resources"
    (frameworks / "PIL").mkdir(parents=True)
    resources.mkdir(parents=True)
    (app / "Contents" / "MacOS").mkdir()
    exe = app / "Contents" / "MacOS" / "Mycelian"
    exe.write_bytes(b"bin")
    exe.chmod(0o755)
    (frameworks / "PIL" / "_imaging.so").write_bytes(b"so")
    (resources / "base_library.zip").write_bytes(b"zip")
    (resources / "PIL").symlink_to("../Frameworks/PIL")  # directory link: not walked into
    (frameworks / "base_library.zip").symlink_to("../Resources/base_library.zip")  # file link

    entries = {e["install"]: e for e in bld._binary_manifest_entries(tmp_path)}
    assert set(entries) == {
        "Contents/MacOS/Mycelian",
        "Contents/Frameworks/PIL/_imaging.so",
        "Contents/Resources/base_library.zip",
        "Contents/Resources/PIL",
        "Contents/Frameworks/base_library.zip",
    }
    link = entries["Contents/Resources/PIL"]
    assert link["action"] == "symlink" and link["target"] == "../Frameworks/PIL"
    assert link["sha256"] == _sha(b"../Frameworks/PIL") and link["size"] == len(b"../Frameworks/PIL")
    assert link["bundle"] is True and link["os"] == "macos"
    assert entries["Contents/Frameworks/base_library.zip"]["target"] == "../Resources/base_library.zip"
    assert entries["Contents/MacOS/Mycelian"]["exec"] is True
    assert "exec" not in entries["Contents/Resources/base_library.zip"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX links")
def test_binary_manifest_entries_reject_absolute_links(tmp_path, set_os):
    set_os("macos")
    app = tmp_path / "packaged" / "macos" / "Mycelian.app"
    app.mkdir(parents=True)
    (app / "real").write_bytes(b"x")
    (app / "abs").symlink_to("/usr/lib")
    with pytest.raises(RuntimeError, match="absolute path"):
        bld._binary_manifest_entries(tmp_path)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX links")
def test_copy_build_artifact_keeps_bundle_links(tmp_path):
    src = tmp_path / "dist" / "Mycelian.app"
    (src / "Frameworks").mkdir(parents=True)
    (src / "Frameworks" / "lib.so").write_bytes(b"so")
    (src / "Resources").mkdir()
    (src / "Resources" / "lib.so").symlink_to("../Frameworks/lib.so")
    dest = tmp_path / "out" / "Mycelian.app"
    bld._copy_build_artifact(src, dest)
    assert (dest / "Resources" / "lib.so").is_symlink()
    assert os.readlink(dest / "Resources" / "lib.so") == "../Frameworks/lib.so"
