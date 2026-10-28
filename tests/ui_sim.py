# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Build NiceGUI UI inside a test, with no browser and no server.

    from ui_sim import run_ui

    def test_tab_shows_its_rows(monkeypatch):
        monkeypatch.setattr(tabmod, "state_manager", FakeState())

        def build():
            tabmod.AppSettingsTab().build(ui.column())

        async def check(user):
            await user.should_see("Minimal badges")
            user.find("Save").click()

        run_ui(build, check)

``user`` is NiceGUI's simulated user (nicegui.testing.User): should_see / should_not_see,
find(...).click() / .type(...) / .trigger(...), and find(...).elements for the elements.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

from nicegui import Client
from nicegui.testing.user import User
from nicegui.testing.user_simulation import user_simulation


def run_ui(build: Callable[[], None], check: Optional[Callable[[User], Awaitable[None]]] = None,
           path: str = "/") -> None:
    """Serve ``build`` as the page at ``path``, open it, then run ``check(user)``."""

    async def go() -> None:
        async with user_simulation(build) as user:
            await user.open(path)
            if check is not None:
                await check(user)

    before = set(Client.instances)
    try:
        asyncio.run(go())
    finally:
        # A leftover client turns off NiceGUI's script-mode fallback, so later tests that
        # build UI outside a page would fail.
        for client_id in set(Client.instances) - before:
            Client.instances.pop(client_id, None)
