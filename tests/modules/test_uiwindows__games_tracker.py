# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""The Games Tracker main tab wrapper: build, pause / resume."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import pytest  # noqa: E402

from modules.uiwindows import games_tracker as gt  # noqa: E402
from ui_sim import run_ui  # noqa: E402


class FakeTab:
    def __init__(self, log: List[Any]) -> None:
        self.log = log
        self.raise_on: str = ""

    def build(self, container) -> None:
        self.log.append(("build", container))

    def on_enter(self) -> None:
        if self.raise_on == "enter":
            raise RuntimeError("x")
        self.log.append("enter")

    def on_exit(self) -> None:
        if self.raise_on == "exit":
            raise RuntimeError("x")
        self.log.append("exit")


@pytest.fixture
def log(monkeypatch):
    monkeypatch.setattr(gt, "_tab", None)
    return []


def test_nothing_built_is_a_no_op(log):
    assert gt.get_games_tracker_tab() is None
    gt.pause_games_tracker()
    gt.resume_games_tracker()
    assert log == []


def test_create_builds_the_page_into_a_scroll_column(log, monkeypatch):
    from modules.uiwindows.tabs import games_tracker_tab as tabmod

    monkeypatch.setattr(tabmod, "GamesTrackerTab", lambda: FakeTab(log))

    async def check(user):
        tab = gt.get_games_tracker_tab()
        assert isinstance(tab, FakeTab)
        (entry,) = log
        assert entry[0] == "build" and "gap-4" in entry[1].classes

    run_ui(gt.create_games_tracker_tab, check)


def test_pause_and_resume(log, monkeypatch):
    monkeypatch.setattr(gt, "_tab", FakeTab(log))
    gt.pause_games_tracker()
    gt.resume_games_tracker()
    assert log == ["exit", "enter"]


@pytest.mark.parametrize("where", ["enter", "exit"])
def test_tab_errors_are_swallowed(log, monkeypatch, where):
    tab = FakeTab(log)
    tab.raise_on = where
    monkeypatch.setattr(gt, "_tab", tab)
    gt.pause_games_tracker()
    gt.resume_games_tracker()
