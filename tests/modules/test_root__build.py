# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for build.py, part 1: progress, OS detection, dependencies, Stream Deck plugin,
cleaning, hidden imports, data files, exclusions, runtime hook, spec file and the
PyInstaller step.

PyInstaller, npm and every other subprocess are faked. get_project_root() is pointed at
tmp_path so nothing is written or deleted in the real project.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import build as bld  # noqa: E402
import time as _time
import types as _types


def _link_dir(link: Path, target: Path) -> None:
    """Symlink a folder; on Windows without the symlink privilege use a junction instead."""
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        import _winapi

        _winapi.CreateJunction(str(target), str(link))


def _local_clock(module, monkeypatch, **fakes):
    """Give ``module`` its own copy of ``time`` with some functions replaced.

    Patching the real time module would hand the fake clock to every thread in the process.
    """
    clock = _types.SimpleNamespace(**{k: getattr(_time, k) for k in dir(_time) if not k.startswith("__")})
    for name, fn in fakes.items():
        setattr(clock, name, fn)
    monkeypatch.setattr(module, "time", clock)


assert Path(bld.__file__).resolve() == ROOT / "build.py"


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
    """Capture BuildProgress.update/error/success calls on the global tracker."""
    log: list = []
    monkeypatch.setattr(bld.progress, "update", lambda m: log.append(("update", m)))
    monkeypatch.setattr(bld.progress, "error", lambda m: log.append(("error", m)))
    monkeypatch.setattr(bld.progress, "success", lambda m="ok": log.append(("success", m)))
    return log


def _texts(log, kind=None):
    return [m for k, m in log if kind is None or k == kind]


# --------------------------------------------------------------------------- BuildProgress


def test_build_progress_output(monkeypatch, capsys):
    clock = iter([100.0, 101.0, 112.5])
    _local_clock(bld, monkeypatch, time=lambda: next(clock))
    p = bld.BuildProgress(total_steps=3)
    assert (p.current_step, p.total_steps, p.start_time) == (0, 3, 100.0)
    p.next_step("First")
    p.update("doing")
    p.success()
    p.success("custom")
    p.error("bad")
    p.summary()
    assert p.current_step == 1
    out = capsys.readouterr().out
    assert "\n[1/3] First\n" in out
    assert "  → doing\n" in out
    assert "  ✓ Complete\n" in out and "  custom\n" in out
    assert "  ✗ ERROR: bad\n" in out
    assert "BUILD COMPLETE - 12.5s" in out
    assert "=" * 50 in out


def test_build_progress_default_steps():
    assert bld.BuildProgress().total_steps == 5


def test_global_progress_tracker():
    assert isinstance(bld.progress, bld.BuildProgress)


# --------------------------------------------------------------------------- OS helpers


def test_get_project_root():
    assert bld.get_project_root() == ROOT


@pytest.mark.parametrize("system,expected", [("Windows", "windows"), ("Linux", "linux"), ("Darwin", "macos")])
def test_detect_os(monkeypatch, system, expected):
    monkeypatch.setattr(bld.platform, "system", lambda: system)
    assert bld.detect_os() == expected


def test_detect_os_unsupported(monkeypatch):
    monkeypatch.setattr(bld.platform, "system", lambda: "SunOS")
    with pytest.raises(ValueError, match="Unsupported operating system: sunos"):
        bld.detect_os()


def test_module_configuration():
    assert bld.CURRENT_OS == bld.detect_os()
    assert bld.OUTPUT_PATH == "builds"
    assert bld.VERSION.count(".") == 2 and all(p.isdigit() for p in bld.VERSION.split("."))
    assert bld.STREAMDECK_PLUGIN_VERSION.count(".") == 3
    assert bld.ICON_PATH == bld.get_os_specific_icon_path(bld.CURRENT_OS)


def test_icon_path_windows_found(root):
    icon = root / "assets/default_assets/icons/Mycelian.ico"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"ico")
    assert bld.get_os_specific_icon_path("windows") == "assets/default_assets/icons/Mycelian.ico"


@pytest.mark.parametrize("os_name", ["windows", "linux", "macos", "beos"])
def test_icon_path_missing_or_unset(root, os_name):
    assert bld.get_os_specific_icon_path(os_name) is None


# --------------------------------------------------------------------------- ensure_dependencies


def test_ensure_dependencies_already_installed(monkeypatch, messages):
    monkeypatch.setitem(sys.modules, "PyInstaller", types.SimpleNamespace(__version__="9.9"))
    bld.ensure_dependencies()
    assert _texts(messages) == ["PyInstaller v9.9 ready"]


def test_ensure_dependencies_without_uv_raises(monkeypatch, messages):
    monkeypatch.setitem(sys.modules, "PyInstaller", None)
    monkeypatch.setattr(bld.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="uv' was not found"):
        bld.ensure_dependencies()
    assert _texts(messages) == ["Installing PyInstaller via uv (dev group)..."]


def test_ensure_dependencies_installs_with_uv(monkeypatch, messages, run):
    monkeypatch.setitem(sys.modules, "PyInstaller", None)
    monkeypatch.setattr(bld.shutil, "which", lambda name: "/bin/uv" if name == "uv" else None)
    run.on(["/bin/uv", "sync"], effect=lambda c, k: sys.modules.__setitem__("PyInstaller", types.SimpleNamespace(__version__="7.0")))
    bld.ensure_dependencies()
    assert run.calls == [(["/bin/uv", "sync", "--group", "dev"], {"check": True})]
    assert _texts(messages)[-1] == "PyInstaller v7.0 ready"


# --------------------------------------------------------------------------- Stream Deck helpers

SD = ("streamdeck-plugin", "mycelian", "com.mushroomsuprise.mycelian.sdPlugin")


def _sd_src(root: Path) -> Path:
    return root.joinpath(*SD)


def _sd_dest(root: Path) -> Path:
    return root / "sd_plugin" / SD[-1]


def test_deploy_streamdeck_plugin_missing_source(tmp_path):
    with pytest.raises(FileNotFoundError, match="Stream Deck build output missing"):
        bld.deploy_streamdeck_plugin_to_sd_plugin(tmp_path)


def test_deploy_streamdeck_plugin_copies_and_keeps_extras(tmp_path):
    src = _sd_src(tmp_path)
    (src / "imgs").mkdir(parents=True)
    (src / "manifest.json").write_text("new")
    (src / "imgs" / "a.png").write_bytes(b"a")
    dest = _sd_dest(tmp_path)
    dest.mkdir(parents=True)
    (dest / "manifest.json").write_text("old")
    (dest / "legacy.png").write_bytes(b"legacy")
    assert bld.deploy_streamdeck_plugin_to_sd_plugin(tmp_path) == dest
    assert (dest / "manifest.json").read_text() == "new"
    assert (dest / "imgs" / "a.png").exists()
    assert (dest / "legacy.png").exists()


def test_manifest_version_missing_manifest(tmp_path, messages):
    assert bld.update_streamdeck_plugin_manifest_version(tmp_path) is True
    assert "skipping version stamp" in _texts(messages)[0]


def _manifest(tmp_path, content) -> Path:
    path = _sd_src(tmp_path) / "manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_manifest_version_written(tmp_path, monkeypatch, messages):
    path = _manifest(tmp_path, json.dumps({"Name": "Mycélian", "Version": "0.0.1.0"}))
    monkeypatch.setattr(bld, "STREAMDECK_PLUGIN_VERSION", " 2.3.4.5 ")
    assert bld.update_streamdeck_plugin_manifest_version(tmp_path) is True
    text = path.read_text(encoding="utf-8")
    assert json.loads(text) == {"Name": "Mycélian", "Version": "2.3.4.5"}
    assert text == '{\n\t"Name": "Mycélian",\n\t"Version": "2.3.4.5"\n}\n'
    assert _texts(messages) == ["Stream Deck plugin version set to 2.3.4.5"]


def test_manifest_version_empty_version(tmp_path, monkeypatch, messages):
    path = _manifest(tmp_path, "{}")
    monkeypatch.setattr(bld, "STREAMDECK_PLUGIN_VERSION", "  ")
    assert bld.update_streamdeck_plugin_manifest_version(tmp_path) is False
    assert _texts(messages, "error") == ["STREAMDECK_PLUGIN_VERSION is empty"]
    assert path.read_text() == "{}"


@pytest.mark.parametrize("content", ["{not json", "[1, 2]"])
def test_manifest_version_bad_content(tmp_path, messages, content):
    path = _manifest(tmp_path, content)
    assert bld.update_streamdeck_plugin_manifest_version(tmp_path) is False
    assert _texts(messages, "error")[0].startswith("Could not update Stream Deck manifest version")
    assert path.read_text() == content


@pytest.mark.parametrize(
    "os_name,machine,libc,expected",
    [
        ("macos", "arm64", "", "@rollup/rollup-darwin-arm64"),
        ("macos", "x86_64", "", "@rollup/rollup-darwin-x64"),
        ("macos", "i686", "", None),
        ("windows", "AMD64", "", "@rollup/rollup-win32-x64-msvc"),
        ("windows", "arm64", "", "@rollup/rollup-win32-arm64-msvc"),
        ("windows", "x86", "", "@rollup/rollup-win32-ia32-msvc"),
        ("windows", "armv7l", "", None),
        ("linux", "x86_64", "glibc", "@rollup/rollup-linux-x64-gnu"),
        ("linux", "x86_64", "musl", "@rollup/rollup-linux-x64-musl"),
        ("linux", "aarch64", "", "@rollup/rollup-linux-arm64-gnu"),
        ("linux", "armv7l", "glibc", "@rollup/rollup-linux-arm-gnueabihf"),
        ("linux", "armv6l", "musl", "@rollup/rollup-linux-arm-musleabihf"),
        ("linux", "ppc64le", "musl", "@rollup/rollup-linux-ppc64-gnu"),
        ("linux", "s390x", "", "@rollup/rollup-linux-s390x-gnu"),
        ("linux", "loongarch64", "", "@rollup/rollup-linux-loong64-gnu"),
        ("linux", "riscv64", "musl", "@rollup/rollup-linux-riscv64-musl"),
        ("linux", "mips", "", None),
        ("freebsd", "x86_64", "", None),
    ],
)
def test_rollup_native_package_name(monkeypatch, set_os, os_name, machine, libc, expected):
    set_os(os_name)
    monkeypatch.setattr(bld.platform, "machine", lambda: machine)
    monkeypatch.setattr(bld.platform, "libc_ver", lambda: (libc, "1.0"))
    assert bld.rollup_native_package_name() == expected


@pytest.fixture
def plugin_dir(tmp_path):
    d = tmp_path / "plugin"
    d.mkdir()
    return d


def test_ensure_rollup_unknown_platform(monkeypatch, plugin_dir, messages):
    monkeypatch.setattr(bld, "rollup_native_package_name", lambda: None)
    assert bld.ensure_rollup_native_package(plugin_dir, "npm") is True
    assert "Could not determine Rollup native package" in _texts(messages)[0]


def test_ensure_rollup_already_installed(monkeypatch, plugin_dir, run):
    monkeypatch.setattr(bld, "rollup_native_package_name", lambda: "@rollup/rollup-darwin-arm64")
    (plugin_dir / "node_modules/@rollup/rollup-darwin-arm64").mkdir(parents=True)
    assert bld.ensure_rollup_native_package(plugin_dir, "npm") is True
    assert run.calls == []


def _rollup(plugin_dir, monkeypatch, meta):
    monkeypatch.setattr(bld, "rollup_native_package_name", lambda: "@rollup/rollup-linux-x64-gnu")
    pkg = plugin_dir / "node_modules/rollup/package.json"
    if meta is not None:
        pkg.parent.mkdir(parents=True)
        pkg.write_text(meta if isinstance(meta, str) else json.dumps(meta))
    return plugin_dir / "node_modules/@rollup/rollup-linux-x64-gnu"


def test_ensure_rollup_without_rollup(monkeypatch, plugin_dir, messages):
    _rollup(plugin_dir, monkeypatch, None)
    assert bld.ensure_rollup_native_package(plugin_dir, "npm") is False
    assert _texts(messages, "error") == ["Rollup is not installed; cannot add the native package"]


def test_ensure_rollup_unreadable_version(monkeypatch, plugin_dir, messages):
    _rollup(plugin_dir, monkeypatch, "{oops")
    assert bld.ensure_rollup_native_package(plugin_dir, "npm") is False
    assert _texts(messages, "error")[0].startswith("Could not read Rollup version")


def test_ensure_rollup_missing_version(monkeypatch, plugin_dir, messages):
    _rollup(plugin_dir, monkeypatch, {"version": "  "})
    assert bld.ensure_rollup_native_package(plugin_dir, "npm") is False
    assert _texts(messages, "error") == ["Installed Rollup package has no version"]


def test_ensure_rollup_installs(monkeypatch, plugin_dir, messages, run):
    native = _rollup(plugin_dir, monkeypatch, {"version": "4.2.0"})
    run.on(["npm", "install"], effect=lambda c, k: native.mkdir(parents=True))
    assert bld.ensure_rollup_native_package(plugin_dir, "npm") is True
    cmd, kwargs = run.calls[0]
    assert cmd == ["npm", "install", "--no-save", "--no-package-lock", "@rollup/rollup-linux-x64-gnu@4.2.0"]
    assert kwargs["cwd"] == plugin_dir
    assert "Installing Rollup native package @rollup/rollup-linux-x64-gnu@4.2.0..." in _texts(messages)


def test_ensure_rollup_install_fails(monkeypatch, plugin_dir, messages, run):
    _rollup(plugin_dir, monkeypatch, {"version": "4.2.0"})
    run.on(["npm"], returncode=1, stderr=" E404 \n")
    assert bld.ensure_rollup_native_package(plugin_dir, "npm") is False
    assert _texts(messages, "error") == ["Failed to install @rollup/rollup-linux-x64-gnu@4.2.0"]
    assert _texts(messages)[-1] == "E404"


def test_ensure_rollup_install_ok_but_folder_missing(monkeypatch, plugin_dir, messages, run):
    _rollup(plugin_dir, monkeypatch, {"version": "4.2.0"})
    run.on(["npm"], returncode=0)
    assert bld.ensure_rollup_native_package(plugin_dir, "npm") is False
    # No stdout/stderr detail -> nothing extra reported after the error
    assert _texts(messages)[-1] == "Failed to install @rollup/rollup-linux-x64-gnu@4.2.0"


# --------------------------------------------------------------------------- build_streamdeck_plugin


@pytest.fixture
def sd_project(tmp_path, monkeypatch, set_os):
    """A plugin source folder with package.json and node_modules; rollup and manifest stubbed."""
    set_os("linux")
    plugin = tmp_path / "streamdeck-plugin" / "mycelian"
    (plugin / "node_modules").mkdir(parents=True)
    (plugin / "package.json").write_text("{}")
    monkeypatch.setattr(bld, "update_streamdeck_plugin_manifest_version", lambda root: True)
    monkeypatch.setattr(bld, "ensure_rollup_native_package", lambda d, npm: True)
    return tmp_path


def test_streamdeck_no_source(tmp_path, messages):
    assert bld.build_streamdeck_plugin(tmp_path) is True
    assert _texts(messages) == ["Stream Deck plugin source not found; skipping"]


def test_streamdeck_no_package_json(tmp_path, messages):
    (tmp_path / "streamdeck-plugin" / "mycelian").mkdir(parents=True)
    assert bld.build_streamdeck_plugin(tmp_path) is True
    assert _texts(messages) == ["Stream Deck plugin package.json missing; skipping"]


def test_streamdeck_manifest_failure(sd_project, monkeypatch, run):
    monkeypatch.setattr(bld, "update_streamdeck_plugin_manifest_version", lambda root: False)
    assert bld.build_streamdeck_plugin(sd_project) is False
    assert run.calls == []


@pytest.mark.parametrize(
    "exc", [FileNotFoundError("npm"), subprocess.CalledProcessError(1, ["npm", "--version"])]
)
def test_streamdeck_npm_missing(sd_project, run, messages, exc):
    run.on(["npm", "--version"], raises=exc)
    assert bld.build_streamdeck_plugin(sd_project) is False
    assert _texts(messages, "error") == ["npm is required to build the Stream Deck plugin"]


def test_streamdeck_deploy_success(sd_project, run, messages):
    run.on(["npm", "--version"]).on(["npm", "run", "build:deploy"], effect=lambda c, k: _sd_dest(sd_project).mkdir(parents=True))
    assert bld.build_streamdeck_plugin(sd_project) is True
    assert run.commands() == [["npm", "--version"], ["npm", "run", "build:deploy"]]
    assert run.calls[0][1]["check"] is True
    assert _texts(messages)[-1] == f"Staged Stream Deck plugin for installer: {_sd_dest(sd_project)}"


def test_streamdeck_windows_commands(sd_project, run, set_os):
    set_os("windows")
    run.on(["npm.cmd", "--version"]).on(["npm.cmd", "run"], effect=lambda c, k: _sd_dest(sd_project).mkdir(parents=True))
    assert bld.build_streamdeck_plugin(sd_project) is True
    assert run.commands()[-1] == ["npm.cmd", "run", "build:deploy:win"]


def test_streamdeck_deploy_success_but_no_staging(sd_project, run, messages):
    run.on(["npm"])
    assert bld.build_streamdeck_plugin(sd_project) is False
    assert _texts(messages, "error")[0].startswith("Stream Deck plugin staging folder missing")


def test_streamdeck_installs_dependencies_with_ci(sd_project, run):
    (sd_project / "streamdeck-plugin/mycelian/node_modules").rmdir()
    run.on(["npm", "ci"]).on(["npm", "--version"]).on(["npm", "run"], effect=lambda c, k: _sd_dest(sd_project).mkdir(parents=True))
    assert bld.build_streamdeck_plugin(sd_project) is True
    assert ["npm", "ci"] in run.commands()
    assert ["npm", "install"] not in run.commands()


def test_streamdeck_ci_fails_then_install_works(sd_project, run, messages):
    (sd_project / "streamdeck-plugin/mycelian/node_modules").rmdir()
    run.on(["npm", "ci"], returncode=1).on(["npm", "install"]).on(["npm", "--version"])
    run.on(["npm", "run"], effect=lambda c, k: _sd_dest(sd_project).mkdir(parents=True))
    assert bld.build_streamdeck_plugin(sd_project) is True
    assert "npm ci failed; trying npm install..." in _texts(messages)


@pytest.mark.parametrize("stderr,last", [("boom\n", "boom"), ("", "npm ci failed; trying npm install...")])
def test_streamdeck_dependency_install_fails(sd_project, run, messages, stderr, last):
    (sd_project / "streamdeck-plugin/mycelian/node_modules").rmdir()
    run.on(["npm", "ci"], returncode=1).on(["npm", "install"], returncode=1, stderr=stderr).on(["npm", "--version"])
    assert bld.build_streamdeck_plugin(sd_project) is False
    assert _texts(messages, "error") == ["Failed to install Stream Deck plugin dependencies"]
    assert _texts(messages, "update")[-1] == last


def test_streamdeck_rollup_failure(sd_project, run, monkeypatch):
    monkeypatch.setattr(bld, "ensure_rollup_native_package", lambda d, npm: False)
    run.on(["npm", "--version"])
    assert bld.build_streamdeck_plugin(sd_project) is False
    assert run.commands() == [["npm", "--version"]]


def test_streamdeck_fallback_build_and_copy(sd_project, run, messages):
    src = _sd_src(sd_project)
    src.mkdir(parents=True)
    (src / "manifest.json").write_text("{}")
    run.on(["npm", "--version"]).on(["npm", "run", "build:deploy"], returncode=1).on(["npm", "run", "build"])
    assert bld.build_streamdeck_plugin(sd_project) is True
    assert (_sd_dest(sd_project) / "manifest.json").exists()
    assert "npm run build:deploy failed; falling back to build + deploy" in _texts(messages)


@pytest.mark.parametrize("stderr", ["compile error\n", ""])
def test_streamdeck_fallback_build_fails(sd_project, run, messages, stderr):
    run.on(["npm", "--version"]).on(["npm", "run", "build:deploy"], returncode=1).on(["npm", "run", "build"], returncode=2, stderr=stderr)
    assert bld.build_streamdeck_plugin(sd_project) is False
    assert _texts(messages, "error") == ["Stream Deck plugin build failed"]
    if stderr:
        assert _texts(messages)[-1] == "compile error"


def test_streamdeck_fallback_copy_missing_output(sd_project, run, messages):
    run.on(["npm", "--version"]).on(["npm", "run", "build:deploy"], returncode=1).on(["npm", "run", "build"])
    assert bld.build_streamdeck_plugin(sd_project) is False
    assert _texts(messages, "error")[0].startswith("Stream Deck build output missing")


# --------------------------------------------------------------------------- clean_build


def test_clean_build_removes_artifacts(root, messages):
    for d in ("build", "dist", "__pycache__"):
        (root / d / "sub").mkdir(parents=True)
    (root / "a.spec").write_text("")
    (root / "b.spec").write_text("")
    (root / "pyi_rth_x.py").write_text("")
    (root / "keep.py").write_text("")
    bld.clean_build()
    assert sorted(p.name for p in root.iterdir()) == ["keep.py"]
    assert _texts(messages) == ["Cleaned build", "Cleaned dist", "Cleaned __pycache__", "Removed 2 spec files, 1 hook files"]


def test_clean_build_nothing_to_do(root, messages):
    bld.clean_build()
    assert messages == []


def test_clean_build_errors_are_tolerated(root, messages, monkeypatch):
    (root / "build").mkdir()
    (root / "a.spec").write_text("")
    (root / "pyi_rth_x.py").write_text("")

    def rmtree(*a, **k):
        raise OSError("busy")

    def unlink(self, *a, **k):
        raise PermissionError("locked")

    monkeypatch.setattr(bld.shutil, "rmtree", rmtree)
    monkeypatch.setattr(Path, "unlink", unlink)
    bld.clean_build()
    monkeypatch.undo()
    assert _texts(messages) == ["Could not remove build"]
    assert (root / "a.spec").exists() and (root / "pyi_rth_x.py").exists()


# --------------------------------------------------------------------------- hidden imports


@pytest.fixture
def no_pyinstaller(monkeypatch):
    monkeypatch.setitem(sys.modules, "PyInstaller", None)


@pytest.fixture
def fake_hooks(monkeypatch):
    calls = []
    hooks = types.ModuleType("PyInstaller.utils.hooks")
    hooks.collect_submodules = lambda pkg: calls.append(pkg) or [f"{pkg}.collected", "nicegui"]
    monkeypatch.setitem(sys.modules, "PyInstaller", types.ModuleType("PyInstaller"))
    monkeypatch.setitem(sys.modules, "PyInstaller.utils", types.ModuleType("PyInstaller.utils"))
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    return calls


def test_hidden_imports_windows(no_pyinstaller):
    imports = bld.get_hidden_imports("windows")
    assert len(imports) == len(set(imports))
    for name in ("nicegui", "flask_socketio", "encodings.cp1252", "pycaw.pycaw", "pystray._win32", "modules.tray_process", "pynput.keyboard", "merge_template_configs"):
        assert name in imports
    for name in ("pulsectl", "pystray._darwin", "gi", "pystray._gtk"):
        assert name not in imports
    # dnspython fallback list when PyInstaller is not importable
    assert "dns.asyncresolver" in imports


def test_hidden_imports_linux(no_pyinstaller):
    imports = bld.get_hidden_imports("linux")
    for name in ("pulsectl", "pulsectl_asyncio", "pystray._gtk", "pystray._appindicator", "gi.repository"):
        assert name in imports
    for name in ("pycaw", "encodings.cp1252", "pystray._win32", "pystray._darwin"):
        assert name not in imports


def test_hidden_imports_macos(no_pyinstaller):
    imports = bld.get_hidden_imports("macos")
    assert "pystray._darwin" in imports
    for name in ("pycaw", "pulsectl", "pystray._win32", "gi", "encodings.cp1252"):
        assert name not in imports


def test_hidden_imports_uses_collect_submodules(fake_hooks):
    imports = bld.get_hidden_imports("macos")
    assert fake_hooks == ["nicegui", "dns"]
    assert "nicegui.collected" in imports and "dns.collected" in imports
    assert "dns.btreezone" not in imports  # fallback list not used
    assert imports.count("nicegui") == 1  # de-duplicated, first position kept
    assert imports[0] == "nicegui"


# --------------------------------------------------------------------------- data files


def test_get_data_files(root, monkeypatch, capsys):
    pkg = root / "site" / "nicegui"
    for sub in ("static", "elements", "native"):
        (pkg / sub).mkdir(parents=True)
    fake = types.ModuleType("nicegui")
    fake.__file__ = str(pkg / "__init__.py")
    monkeypatch.setitem(sys.modules, "nicegui", fake)
    icons = root / "assets/default_assets/icons"
    icons.mkdir(parents=True)
    (root / "modules/help_system/content").mkdir(parents=True)
    (root / "factorio_mod").mkdir()
    files = bld.get_data_files()
    assert files == [
        (str(pkg / "static"), "nicegui/static"),
        (str(pkg / "elements"), "nicegui/elements"),
        (str(pkg / "native"), "nicegui/native"),
        (str(icons), "assets/default_assets/icons"),
        (str(root / "modules/help_system/content"), "modules/help_system/content"),
        (str(root / "factorio_mod"), "factorio_mod"),
    ]
    out = capsys.readouterr().out
    assert "Including application icons" in out and "Including help content" in out
    assert "Including Factorio companion mod" in out


def test_get_data_files_all_nicegui_dirs_and_nothing_else(root, monkeypatch):
    pkg = root / "nicegui"
    subs = ("static", "templates", "elements", "functions", "app", "native")
    for sub in subs:
        (pkg / sub).mkdir(parents=True)
    fake = types.ModuleType("nicegui")
    fake.__file__ = str(pkg / "__init__.py")
    monkeypatch.setitem(sys.modules, "nicegui", fake)
    (root / "modules/help_system/assets").mkdir(parents=True)
    files = bld.get_data_files()
    assert [dest for _, dest in files] == [f"nicegui/{s}" for s in subs] + ["modules/help_system/assets"]


def test_get_data_files_real_project_includes_help():
    dests = [dest for _, dest in bld.get_data_files()]
    assert "nicegui/static" in dests
    assert "modules/help_system/content" in dests


# --------------------------------------------------------------------------- excluded modules


@pytest.mark.parametrize("os_name,has_qt", [("windows", True), ("macos", True), ("linux", False)])
def test_get_excluded_modules(monkeypatch, set_os, os_name, has_qt):
    set_os(os_name)
    monkeypatch.setattr(bld, "_load_bundle_filter_module", lambda: types.SimpleNamespace(UNUSED_EXTENSION_MODULES=("X._codec",)))
    excludes = bld.get_excluded_modules()
    assert "pytest" in excludes and "tkinter" in excludes and "X._codec" in excludes
    assert "docutils" not in excludes
    assert ("PyQt6" in excludes) is has_qt
    assert ("PySide6" in excludes) is has_qt


def test_load_bundle_filter_module_real():
    module = bld._load_bundle_filter_module()
    assert module.__name__ == "mycelian_bundle_filter"
    assert callable(module.filter_unused_data)
    assert "PIL._avif" in module.UNUSED_EXTENSION_MODULES


def test_load_bundle_filter_module_spec_missing(monkeypatch):
    import importlib.util

    monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *a, **k: None)
    with pytest.raises(ImportError, match="Cannot load bundle filter"):
        bld._load_bundle_filter_module()


# --------------------------------------------------------------------------- runtime hook & spec


def test_create_runtime_hook(root):
    path = Path(bld.create_runtime_hook())
    assert path == root / "pyi_rth_dll_fix.py"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    assert names == ["ensure_dll_path", "ensure_utf8_encoding"]
    assert text.rstrip().endswith("ensure_utf8_encoding()")


def _exec_runtime_hook(root, monkeypatch, meipass, has_add_dll):
    import io

    text = Path(bld.create_runtime_hook()).read_text(encoding="utf-8")
    added = []
    fake_os = types.SimpleNamespace(environ={"PATH": "/usr/bin"}, pathsep=":")
    if has_add_dll:
        def add_dll_directory(d):
            added.append(d)
            if d == "bad":
                raise OSError("nope")

        fake_os.add_dll_directory = add_dll_directory

    class Stream:
        def __init__(self, fail):
            self.fail = fail
            self.enc = None

        def reconfigure(self, encoding):
            if self.fail:
                raise io.UnsupportedOperation("no")
            self.enc = encoding

    fake_sys = types.SimpleNamespace(stdout=Stream(False), stderr=Stream(meipass == "bad"))
    if meipass:
        fake_sys._MEIPASS = meipass
    # The hook does "import os, sys"; give it our fakes through a custom __import__.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "os":
            return fake_os
        if name == "sys":
            return fake_sys
        return real_import(name, *a, **k)

    builtins_ns = dict(vars(builtins))
    builtins_ns["__import__"] = fake_import
    exec(compile(text, "pyi_rth_dll_fix.py", "exec"), {"__builtins__": builtins_ns})
    return fake_os, fake_sys, added


def test_runtime_hook_bundle(root, monkeypatch):
    fake_os, fake_sys, added = _exec_runtime_hook(root, monkeypatch, "/bundle", True)
    assert added == ["/bundle"]
    assert fake_os.environ["PATH"] == "/bundle:/usr/bin"
    assert fake_os.environ["PYTHONIOENCODING"] == "utf-8"
    assert fake_sys.stdout.enc == "utf-8"


def test_runtime_hook_bundle_errors_tolerated(root, monkeypatch):
    fake_os, fake_sys, added = _exec_runtime_hook(root, monkeypatch, "bad", True)
    assert added == ["bad"]
    assert fake_os.environ["PATH"].startswith("bad:")
    assert fake_os.environ["PYTHONIOENCODING"] == "utf-8"


def test_runtime_hook_bundle_without_add_dll_and_path_present(root, monkeypatch):
    fake_os, _, _ = _exec_runtime_hook(root, monkeypatch, "/usr/bin", False)
    assert fake_os.environ["PATH"] == "/usr/bin"  # already on PATH -> unchanged


def test_runtime_hook_outside_bundle_does_nothing(root, monkeypatch):
    fake_os, fake_sys, added = _exec_runtime_hook(root, monkeypatch, None, True)
    assert added == [] and fake_os.environ == {"PATH": "/usr/bin"}
    assert fake_sys.stdout.enc is None


@pytest.fixture
def spec_env(root, monkeypatch):
    monkeypatch.setattr(bld, "get_data_files", lambda: [("/src/static", "nicegui/static")])
    monkeypatch.setattr(bld, "get_hidden_imports", lambda os_name: ["hidden_" + os_name])
    monkeypatch.setattr(bld, "get_excluded_modules", lambda: ["tkinter"])
    monkeypatch.setattr(bld, "ICON_PATH", None)
    monkeypatch.setattr(bld, "VERSION_FILE", None)
    return root


def _spec_literals(text):
    tree = ast.parse(text)
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            try:
                values[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return values


def test_create_spec_file_macos(spec_env, set_os):
    set_os("macos")
    path = bld.create_spec_file()
    assert path == spec_env / "mycelian_improved.spec"
    text = path.read_text(encoding="utf-8")
    values = _spec_literals(text)
    assert values["CURRENT_OS"] == "macos"
    assert values["datas"] == [("/src/static", "nicegui/static")]
    assert values["hiddenimports"] == ["hidden_macos"]
    assert values["excludes"] == ["tkinter"]
    assert values["binaries"] == []
    assert values["runtime_hooks"] == [str(spec_env / "pyi_rth_dll_fix.py")]
    assert f"'CFBundleVersion': '{bld.VERSION}'" in text
    assert "icon=None," in text and "version=None," in text
    assert (spec_env / "pyi_rth_dll_fix.py").exists()


def test_create_spec_file_linux_with_icon(spec_env, set_os, monkeypatch):
    set_os("linux")
    monkeypatch.setattr(bld, "ICON_PATH", "assets/icon.ico")
    monkeypatch.setattr(bld, "VERSION_FILE", "version.txt")
    text = bld.create_spec_file().read_text(encoding="utf-8")
    assert "info_plist" not in text
    assert f"icon={str(spec_env / 'assets/icon.ico')!r}" in text
    assert "version=None" in text  # version file only applies on Windows
    ast.parse(text)


def _fake_win_sys(base: Path, executable: Path):
    return types.SimpleNamespace(
        version_info=(3, 14, 0),
        base_prefix=str(base),
        base_exec_prefix=str(base),  # duplicate root is skipped
        prefix="",  # empty root is skipped
        executable=str(executable),
    )


def test_create_spec_file_windows_collects_dlls(spec_env, set_os, monkeypatch, messages):
    set_os("windows")
    monkeypatch.setattr(bld, "VERSION_FILE", "version.txt")
    base = spec_env / "py"
    (base / "DLLs").mkdir(parents=True)
    (base / "python314.dll").write_bytes(b"x")
    (base / "python3.dll").write_bytes(b"x")
    (base / "VCRUNTIME140.dll").write_bytes(b"x")
    (base / "DLLs" / "msvcp140.dll").write_bytes(b"x")
    venv = spec_env / "venv" / "Scripts"
    venv.mkdir(parents=True)
    monkeypatch.setattr(bld, "sys", _fake_win_sys(base, venv / "python.exe"))
    monkeypatch.setattr(bld.sysconfig, "get_config_var", lambda name: str(base))
    text = bld.create_spec_file().read_text(encoding="utf-8")
    values = _spec_literals(text)
    assert values["binaries"] == [
        (str(base / "python314.dll"), "."),
        (str(base / "python3.dll"), "."),
        (str(base / "VCRUNTIME140.dll"), "."),
        (str(base / "DLLs" / "msvcp140.dll"), "."),
    ]
    assert f"version={str(spec_env / 'version.txt')!r}" in text
    updates = _texts(messages)
    assert f"Including Python DLL: {base / 'python314.dll'}" in updates
    assert f"Including runtime DLL: {base / 'DLLs' / 'msvcp140.dll'}" in updates
    assert not any("Python DLL not found" in m for m in updates)


def test_create_spec_file_windows_without_versioned_dll(spec_env, set_os, monkeypatch, messages):
    set_os("windows")
    base = spec_env / "py"
    base.mkdir()
    (base / "python3.dll").write_bytes(b"x")
    monkeypatch.setattr(bld, "sys", _fake_win_sys(base, spec_env / "missing" / "python.exe"))
    monkeypatch.setattr(bld.sysconfig, "get_config_var", lambda name: None)
    values = _spec_literals(bld.create_spec_file().read_text(encoding="utf-8"))
    assert values["binaries"] == [(str(base / "python3.dll"), ".")]
    assert "Warning: Python DLL not found - may cause runtime issues" in _texts(messages)


def test_create_spec_file_windows_resolve_errors_and_duplicates(spec_env, set_os, monkeypatch, messages):
    """Unresolvable paths fall back to their string form; a folder or DLL reached twice is used once."""
    set_os("windows")
    base = spec_env / "py"
    (base / "DLLs").mkdir(parents=True)
    (base / "python314.dll").write_bytes(b"x")
    (base / "python3.dll").write_bytes(b"x")
    alias = spec_env / "alias"
    _link_dir(alias, base)
    monkeypatch.setattr(
        bld,
        "sys",
        types.SimpleNamespace(
            version_info=(3, 14, 0),
            base_prefix=str(base),
            base_exec_prefix=str(alias),
            prefix=str(base),
            executable=str(base / "python.exe"),
        ),
    )
    monkeypatch.setattr(bld.sysconfig, "get_config_var", lambda name: None)
    real_resolve = Path.resolve

    def resolve(self, *a, **k):
        if self == alias or self.name == "python3.dll":
            raise OSError("cannot resolve")
        return real_resolve(self, *a, **k)

    monkeypatch.setattr(Path, "resolve", resolve)
    path = bld.create_spec_file()
    monkeypatch.setattr(Path, "resolve", real_resolve)
    values = _spec_literals(path.read_text(encoding="utf-8"))
    # alias/python314.dll resolves to the same file as py/python314.dll and is skipped;
    # python3.dll cannot be resolved, so each spelling counts as its own file.
    assert values["binaries"] == [
        (str(base / "python314.dll"), "."),
        (str(base / "python3.dll"), "."),
        (str(alias / "python3.dll"), "."),
    ]


# --------------------------------------------------------------------------- build_executable


@pytest.fixture
def exe_env(root, monkeypatch, run):
    spec = root / "mycelian_improved.spec"
    monkeypatch.setattr(bld, "create_spec_file", lambda: spec)
    monkeypatch.setattr(bld, "OUTPUT_PATH", "builds")
    return root


def test_build_executable_windows(exe_env, run, set_os, messages):
    set_os("windows")

    def make(cmd, kwargs):
        (exe_env / "builds" / "Mycelian.exe").write_bytes(b"\0" * 2 * 1024 * 1024)

    run.on([sys.executable, "-m", "PyInstaller"], effect=make)
    ok, dist = bld.build_executable()
    assert (ok, dist) == (True, exe_env / "builds")
    cmd, kwargs = run.calls[0]
    assert cmd == [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", str(exe_env / "mycelian_improved.spec"), "--distpath", str(exe_env / "builds")]
    assert kwargs["cwd"] == exe_env
    assert _texts(messages) == ["Build completed successfully", "Compiled Mycelian.exe (2.0 MB)"]


def test_build_executable_macos_bundle(exe_env, run, set_os, messages):
    set_os("macos")

    def make(cmd, kwargs):
        app = exe_env / "builds" / "Mycelian.app" / "Contents"
        app.mkdir(parents=True)
        (app / "a").write_bytes(b"\0" * 1024 * 1024)
        (app / "b").write_bytes(b"\0" * 1024 * 1024)

    run.on([sys.executable], effect=make)
    assert bld.build_executable()[0] is True
    assert _texts(messages)[1:] == ["Compiled Mycelian.app (2.0 MB)", "Note: Double-click in Finder to run without terminal"]


def test_build_executable_macos_single_file(exe_env, run, set_os, messages):
    set_os("macos")
    run.on([sys.executable], effect=lambda c, k: (exe_env / "builds" / "Mycelian.app").write_bytes(b"x"))
    assert bld.build_executable()[0] is True
    assert _texts(messages)[-1] == "Note: Double-click in Finder to run without terminal"


def test_build_executable_linux_missing_binary(exe_env, run, set_os, messages):
    set_os("linux")
    run.on([sys.executable])
    assert bld.build_executable() == (True, exe_env / "builds")
    assert _texts(messages) == ["Build completed successfully"]


def test_build_executable_no_output_path(exe_env, run, set_os, monkeypatch, messages):
    set_os("linux")
    monkeypatch.setattr(bld, "OUTPUT_PATH", None)
    run.on([sys.executable])
    assert bld.build_executable() == (True, exe_env / "dist")
    assert "--distpath" not in run.calls[0][0]
    assert _texts(messages) == ["Build completed successfully"]  # dist folder does not exist


def test_build_executable_absolute_output_path(exe_env, run, set_os, monkeypatch, tmp_path_factory):
    set_os("linux")
    out = tmp_path_factory.mktemp("abs") / "out"
    monkeypatch.setattr(bld, "OUTPUT_PATH", str(out))
    run.on([sys.executable])
    assert bld.build_executable() == (True, out)
    assert out.is_dir()


@pytest.mark.parametrize("stderr,expected", [("trace\n", ["Error: trace"]), ("", [])])
def test_build_executable_failure(exe_env, run, messages, stderr, expected):
    run.on([sys.executable], returncode=1, stderr=stderr)
    assert bld.build_executable() == (False, None)
    assert _texts(messages, "error") == ["Build failed"]
    assert _texts(messages, "update") == expected


# --------------------------------------------------------------------------- build info / post build


def test_create_build_info(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "PyInstaller", types.SimpleNamespace(__version__="6.6"))
    bld.create_build_info(tmp_path)
    lines = (tmp_path / "build_info.txt").read_text().splitlines()
    keys = [line.split(":", 1)[0] for line in lines]
    assert keys[:2] == ["build_date", "python_version"]
    assert "platform: " + sys.platform in lines
    assert "build_method: improved_pyinstaller" in lines
    assert "pyinstaller_version: 6.6" in lines


def test_create_build_info_without_pyinstaller(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "PyInstaller", None)
    bld.create_build_info(tmp_path)
    assert "pyinstaller_version: None" in (tmp_path / "build_info.txt").read_text()


def test_create_build_info_missing_dir(tmp_path):
    bld.create_build_info(tmp_path / "missing")
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("dist", [None, "missing"])
def test_post_build_tasks_no_output(tmp_path, messages, dist):
    bld.post_build_tasks(None if dist is None else tmp_path / dist)
    assert _texts(messages) == ["No output directory found"]


def test_post_build_tasks_reports_size(tmp_path, messages):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a").write_bytes(b"\0" * 1024 * 1024)
    (tmp_path / "b").write_bytes(b"\0" * 512 * 1024)
    bld.post_build_tasks(tmp_path)
    assert _texts(messages) == ["Total build size: 1.5 MB"]
