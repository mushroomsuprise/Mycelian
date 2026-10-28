#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Settings fixes: profile pictures, OBS start-up notices, Save and switch, OBS tab icon."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.service_alerts import STARTUP_POLLS, ServiceAlertTracker  # noqa: E402
from modules.uiwindows.service_brand_icons import SERVICE_BRAND_SVG  # noqa: E402


def test_obs_just_opened_gets_time_before_cant_connect():
    tracker = ServiceAlertTracker()
    tracker.observe({"internet": "Online", "obs": "Not running"})
    notices = []
    # OBS starts: running, but its WebSocket server isn't up for a while.
    for _ in range(STARTUP_POLLS - 1):
        notices += tracker.observe({"internet": "Online", "obs": "Can't connect"})
    notices += tracker.observe({"internet": "Online", "obs": "Connected"})
    assert notices == []
    # Still failing long after it opened: that is worth a notice.
    for _ in range(STARTUP_POLLS + 1):
        notices += tracker.observe({"internet": "Online", "obs": "Can't connect"})
    assert [n.title for n in notices] == ["Mycelian can't connect to OBS"]


def test_obs_tab_icon_has_a_light_backing():
    assert '<circle cx="12" cy="12" r="11.6" fill="#FFFFFF"/>' in SERVICE_BRAND_SVG["obs"]


def _account_card():
    from modules.uiwindows.tabs import integration_ui as iu

    card = object.__new__(iu.AccountCard)
    card.key, card.kicker, card._top = "twitch", "Main account", ""
    card._who = SimpleNamespace(set_content=lambda html: setattr(card, "html", html))
    return card


def test_account_card_shows_profile_pictures():
    card = _account_card()
    card.set("SteveCase", "ok", "Connected", initial="S", photo_url="https://static-cdn.jtvnw.net/a.png")
    assert '<img src="https://static-cdn.jtvnw.net/a.png"' in card.html and "has-photo" in card.html
    assert 'onerror="this.remove()"' in card.html  # the letter shows if the picture can't load
    card.set("SteveCase", "ok", "Connected", initial="S", photo_url="javascript:alert(1)")
    assert "<img" not in card.html


def test_status_dicts_carry_avatar_urls():
    for path in ("modules/twitch.py", "modules/chatbot.py"):
        src = (ROOT / path).read_text(encoding="utf-8")
        assert '"avatar_url": getattr(self.user, "profile_image_url", "") or ""' in src
    assert '"bot_avatar_url": bot_avatar' in (ROOT / "modules" / "discord_service.py").read_text(encoding="utf-8")


def test_save_and_switch_takes_saved_fields_as_the_baseline():
    from modules.uiwindows.settings import SettingsUI

    class Input:  # _is_settings_field matches on the class name
        def __init__(self, value):
            self.value = value

    class Tab:
        def __init__(self):
            self.ui_elements = {"name": Input("old")}
            self.dirty = False
            self.saved = False

        def save(self):
            self.saved = True
            self.dirty = False

    ui = object.__new__(SettingsUI)
    tab = Tab()
    ui._tabs_by_name = {"App Settings": tab}
    ui._snapshot_tab_fields(tab)
    tab.ui_elements["name"].value = "new"
    tab.dirty = True
    assert ui._tab_is_dirty("App Settings")
    ui.save_tab("App Settings")
    assert tab.saved and not ui._tab_is_dirty("App Settings")
