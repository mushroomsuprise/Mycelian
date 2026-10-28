# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/npsso_authenticator.py: subprocess capture, result type and instruction dialog."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from modules import npsso_authenticator as na  # noqa: E402

TOKEN = "A" * 32 + "b_-9" * 8  # 64 chars, base64url-like


# --------------------------------------------------------------------------- helpers


def test_project_root_is_repo_root():
    assert na._project_root() == ROOT
    assert (na._project_root() / "modules" / "npsso_authenticator.py").exists()


@pytest.mark.parametrize("frozen,expected", [(True, True), (False, False)])
def test_is_frozen_reflects_sys_frozen(monkeypatch, frozen, expected):
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    assert na._is_frozen() is expected


def test_is_frozen_false_when_attribute_missing(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert na._is_frozen() is False


def test_dev_mode_prepends_root_to_existing_pythonpath(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setenv("PYTHONPATH", "/existing/path")
    cmd, env, root = na._npsso_capture_command_and_env()
    assert cmd[1:] == ["-m", "modules.npsso_webview_capture"]
    import os

    assert env["PYTHONPATH"] == f"{root}{os.pathsep}/existing/path"


def test_dev_mode_sets_pythonpath_when_missing(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    _cmd, env, root = na._npsso_capture_command_and_env()
    assert env["PYTHONPATH"] == str(root)


def test_env_is_a_copy_not_os_environ(monkeypatch):
    import os

    monkeypatch.setattr(sys, "frozen", False, raising=False)
    _cmd, env, _root = na._npsso_capture_command_and_env()
    env["SOMETHING_NEW_FOR_TEST"] = "1"
    assert "SOMETHING_NEW_FOR_TEST" not in os.environ


# --------------------------------------------------------------------------- subprocess


class _Run:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.exc is not None:
            raise self.exc
        return self.result


def _proc(code=0, out="", err=""):
    return SimpleNamespace(returncode=code, stdout=out, stderr=err)


def test_subprocess_success_returns_token(monkeypatch):
    run = _Run(_proc(0, f"  {TOKEN}\n"))
    monkeypatch.setattr(na.subprocess, "run", run)
    assert na._run_npsso_capture_subprocess() == (True, TOKEN, "")
    cmd, kwargs = run.calls[0]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["timeout"] == na._SUBPROCESS_TIMEOUT_SEC
    assert kwargs["cwd"] == str(na._project_root())
    assert "env" in kwargs
    assert cmd[0] == sys.executable


@pytest.mark.parametrize("bad", ["short", "A" * 63, "A" * 65, "!" * 64, "a" * 63 + "="])
def test_subprocess_invalid_token_shape(monkeypatch, bad):
    monkeypatch.setattr(na.subprocess, "run", _Run(_proc(0, bad)))
    ok, token, err = na._run_npsso_capture_subprocess()
    assert (ok, token) == (False, "")
    assert "Invalid token" in err


def test_subprocess_stderr_message_returned(monkeypatch):
    monkeypatch.setattr(na.subprocess, "run", _Run(_proc(1, "", "  Window closed early \n")))
    assert na._run_npsso_capture_subprocess() == (False, "", "Window closed early")


def test_subprocess_zero_exit_without_stdout_uses_stderr(monkeypatch):
    monkeypatch.setattr(na.subprocess, "run", _Run(_proc(0, "", "boom")))
    assert na._run_npsso_capture_subprocess() == (False, "", "boom")


def test_subprocess_failure_without_output_gives_generic_message(monkeypatch):
    monkeypatch.setattr(na.subprocess, "run", _Run(_proc(2, None, None)))
    ok, token, err = na._run_npsso_capture_subprocess()
    assert (ok, token) == (False, "")
    assert err.startswith("NPSSO capture failed")


def test_subprocess_nonzero_with_stdout_and_no_stderr_is_generic_failure(monkeypatch):
    monkeypatch.setattr(na.subprocess, "run", _Run(_proc(3, TOKEN, "")))
    ok, _token, err = na._run_npsso_capture_subprocess()
    assert ok is False
    assert "NPSSO capture failed" in err


def test_subprocess_timeout(monkeypatch):
    monkeypatch.setattr(na.subprocess, "run", _Run(exc=subprocess.TimeoutExpired(cmd="x", timeout=1)))
    ok, token, err = na._run_npsso_capture_subprocess()
    assert (ok, token) == (False, "")
    assert "Timed out" in err


def test_subprocess_oserror(monkeypatch):
    monkeypatch.setattr(na.subprocess, "run", _Run(exc=OSError("no exec")))
    ok, token, err = na._run_npsso_capture_subprocess()
    assert (ok, token) == (False, "")
    assert err == "Could not start sign-in window: no exec"


# --------------------------------------------------------------------------- dataclass


def test_npsso_result_defaults_and_values():
    r = na.NpssoResult(success=False)
    assert r.npsso_code == "" and r.error_message == ""
    r2 = na.NpssoResult(True, TOKEN, "x")
    assert r2.success and r2.npsso_code == TOKEN and r2.error_message == "x"


# --------------------------------------------------------------------------- dialog


class _Scheduler:
    """Replaces layout_schedule: records callbacks so the test can fire them by hand."""

    def __init__(self):
        self.pending = []

    def __call__(self, interval, callback, *, once=False, active=True):
        self.pending.append((interval, callback, once))
        return SimpleNamespace(cancel=lambda: None)

    def fire_all(self):
        while self.pending:
            _interval, cb, _once = self.pending.pop(0)
            cb()


def _dialog_run(monkeypatch, on_continue, on_cancel, check):
    from nicegui import ui
    from ui_sim import run_ui

    from modules import ui_timer

    sched = _Scheduler()
    monkeypatch.setattr(ui_timer, "layout_schedule", sched)

    def build():
        ui.label("host page")
        ui.button("Open", on_click=lambda: na.show_npsso_instruction_dialog(on_continue, on_cancel))

    async def _check(user):
        user.find("Open").click()
        await check(user, sched)

    run_ui(build, _check)


def test_dialog_shows_steps_and_countdown(monkeypatch):
    calls = []

    async def check(user, sched):
        await user.should_see("Connect PlayStation Network")
        await user.should_see("Sign in to PlayStation Network in that window")
        await user.should_see("Continue (10)")
        btn = next(iter(user.find("Continue (10)").elements))
        assert btn.enabled is False
        # first scheduled call is the 0.1s kick-off
        assert sched.pending[0][0] == 0.1 and sched.pending[0][2] is True
        sched.pending.pop(0)[1]()  # countdown_timer(10)
        assert btn.text == "Continue (10)"
        assert sched.pending[0][0] == 1.0
        sched.pending.pop(0)[1]()  # countdown_timer(9)
        assert btn.text == "Continue (9)"
        sched.fire_all()
        assert btn.text == "Continue"
        assert btn.enabled is True
        from nicegui import ui

        dialog = next(iter(user.find(ui.dialog).elements))
        assert dialog.value is True
        user.find(kind=ui.button, content="Continue").click()
        assert calls == ["continue"]
        assert dialog.value is False

    _dialog_run(monkeypatch, lambda: calls.append("continue"), lambda: calls.append("cancel"), check)


def test_dialog_cancel_calls_on_cancel(monkeypatch):
    calls = []

    async def check(user, sched):
        await user.should_see("Cancel")
        user.find("Cancel").click()
        assert calls == ["cancel"]

    _dialog_run(monkeypatch, lambda: calls.append("continue"), lambda: calls.append("cancel"), check)


def test_dialog_cancel_without_callback(monkeypatch):
    calls = []

    async def check(user, sched):
        from nicegui import ui

        dialog = next(iter(user.find(ui.dialog).elements))
        user.find("Cancel").click()
        assert calls == []
        assert dialog.value is False

    _dialog_run(monkeypatch, lambda: calls.append("continue"), None, check)


def test_countdown_stops_when_button_deleted(monkeypatch):
    async def check(user, sched):
        btn = next(iter(user.find("Continue (10)").elements))
        cb = sched.pending.pop(0)[1]
        btn.delete()
        cb()
        assert sched.pending == []

    _dialog_run(monkeypatch, lambda: None, None, check)


def test_frozen_mode_runs_executable_with_helper_flag_and_clean_env(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/App/Mycelian")
    for var in na._PYINSTALLER_BOOTSTRAP_VARS:
        monkeypatch.setenv(var, "x")
    cmd, env, root = na._npsso_capture_command_and_env()
    assert cmd == ["/App/Mycelian", na.NPSSO_HELPER_FLAG]
    assert not any(v in env for v in na._PYINSTALLER_BOOTSTRAP_VARS)
    assert root == na._project_root()
