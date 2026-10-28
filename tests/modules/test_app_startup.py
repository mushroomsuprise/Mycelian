# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.app_startup (startup flags and multiprocessing child detection)."""

from __future__ import annotations

import multiprocessing
import multiprocessing.spawn
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import app_startup


@pytest.fixture(autouse=True)
def _reset_flag(monkeypatch):
    monkeypatch.setattr(app_startup, "_critical_startup_done", False)


def test_flags_default_and_after_mark():
    assert app_startup.critical_startup_done() is False
    assert app_startup.should_run_blocking_ui() is True
    app_startup.mark_critical_startup_done()
    assert app_startup.critical_startup_done() is True
    assert app_startup.should_run_blocking_ui() is False
    app_startup.mark_critical_startup_done()  # idempotent
    assert app_startup.critical_startup_done() is True


@pytest.fixture
def main_process(monkeypatch):
    monkeypatch.setattr(multiprocessing, "current_process", lambda: SimpleNamespace(name="MainProcess"))
    monkeypatch.setattr(multiprocessing.spawn, "is_forking", lambda argv: False)


@pytest.mark.parametrize("name", ["SpawnProcess-1", "mycelian-tray"])
def test_named_child_process_is_spawn_child(monkeypatch, name):
    monkeypatch.setattr(multiprocessing, "current_process", lambda: SimpleNamespace(name=name))
    assert app_startup.is_spawn_child(["main.py"]) is True


def test_forking_argv_is_spawn_child(monkeypatch):
    monkeypatch.setattr(multiprocessing, "current_process", lambda: SimpleNamespace(name="MainProcess"))
    seen = []
    monkeypatch.setattr(multiprocessing.spawn, "is_forking", lambda argv: seen.append(argv) or True)
    assert app_startup.is_spawn_child(["exe", "--multiprocessing-fork"]) is True
    assert seen == [["exe", "--multiprocessing-fork"]]


def test_real_is_forking_detection(monkeypatch):
    monkeypatch.setattr(multiprocessing, "current_process", lambda: SimpleNamespace(name="MainProcess"))
    assert app_startup.is_spawn_child(["exe", "--multiprocessing-fork", "pipe_handle=1"]) is True


@pytest.mark.parametrize(
    "cmd",
    [
        "from multiprocessing.resource_tracker import main;main(5)",
        "from multiprocessing.forkserver import main; main(1, 2)",
        "import sys; from multiprocessing.forkserver import main; main()",
    ],
)
def test_frozen_helper_commands_are_children(main_process, cmd):
    assert app_startup.is_spawn_child(["mycelian.exe", "-B", "-c", cmd]) is True


@pytest.mark.parametrize(
    "argv",
    [
        ["main.py"],
        [],
        ["exe", "-c"],  # -c is last, no command
        ["exe", "-c", "print('hi')"],
        ["exe", "-c", 123],
    ],
)
def test_plain_main_process_is_not_child(main_process, argv):
    assert app_startup.is_spawn_child(argv) is False


def test_defaults_to_sys_argv(main_process, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["exe", "-c", "from multiprocessing.resource_tracker import main"])
    assert app_startup.is_spawn_child() is True
    monkeypatch.setattr(sys, "argv", ["main.py"])
    assert app_startup.is_spawn_child() is False
