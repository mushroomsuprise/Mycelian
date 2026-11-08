# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""The shared "Clear saved credentials" button: asks first, clears, then reloads the tab."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from nicegui import ui  # noqa: E402

from modules import credentials_reset  # noqa: E402
from modules.uiwindows.tabs.integration_ui import clear_credentials_button  # noqa: E402
from ui_sim import run_ui  # noqa: E402


def _press(button) -> None:
    for listener in list(button._event_listeners.values()):
        listener.handler(None)


def test_asks_then_clears_and_reloads(monkeypatch):
    cleared: List[str] = []
    reloaded: List[int] = []
    monkeypatch.setattr(credentials_reset, "clear_and_notify", lambda s: cleared.append(s) or True)
    buttons: List[Any] = []

    def build():
        page = SimpleNamespace(root=ui.element("div"))
        buttons.append(clear_credentials_button("steam", page, lambda: reloaded.append(1)))

    async def check(user):
        assert buttons[0].props["aria-label"] == "Clear saved Steam credentials"
        _press(buttons[0])
        await user.should_see("Clear the saved Steam credentials?")
        await user.should_see("This removes the Steam Web API key from Mycelian and disconnects Steam.")
        assert cleared == []  # nothing happens before confirming
        user.find("Clear credentials").click()
        for _ in range(100):
            if reloaded:
                break
            await asyncio.sleep(0.02)
        assert cleared == ["steam"] and reloaded == [1]

    run_ui(build, check)


def test_cancel_clears_nothing(monkeypatch):
    cleared: List[str] = []
    monkeypatch.setattr(credentials_reset, "clear_and_notify", lambda s: cleared.append(s) or True)
    buttons: List[Any] = []

    def build():
        buttons.append(clear_credentials_button("obs", SimpleNamespace(root=None)))

    async def check(user):
        _press(buttons[0])
        await user.should_see("Clear the saved OBS credentials?")
        user.find("Cancel").click()
        await asyncio.sleep(0.05)
        assert cleared == []

    run_ui(build, check)
