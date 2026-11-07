# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Settings → Steam: Web API key, the profile to track, and what Mycelian sees right now."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from nicegui import run, ui

from ... import dataobjects
from ... import steam_service
from ...dataobjects import state_manager
from ...notification_engine import notify
from ...steamapi import parse_profile
from ...ui_buttons import outline_button, primary_button
from ...ui_timer import layout_schedule
from .integration_ui import (
    AccountCard,
    IntegrationPage,
    esc,
    field,
    fields,
    hint,
    initial_of,
    open_url,
    section,
    set_connect_style,
    UsageRow,
)

logger = logging.getLogger(__name__)

API_KEY_URL = "https://steamcommunity.com/dev/apikey"
_KEY_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32}(?![0-9A-Fa-f])")
PRIVACY_URL = "https://steamcommunity.com/my/edit/settings"
# Achievements listed under "Current game" (unlocked newest first, then the most common locked ones).
ACHIEVEMENT_ROWS = 8

STEAM_TAB_CSS = """
.stm-ach { display: flex; flex-direction: column; gap: 6px; }
.stm-game { display: flex; gap: 12px; align-items: center; }
.stm-game img { width: 184px; max-width: 40%; aspect-ratio: 460 / 215; object-fit: cover; border-radius: 8px; background: var(--color-bg-surface); }
.stm-game-name { font-weight: 700; font-size: .95rem; color: var(--color-text-primary); }
.stm-game-sub { font-size: .78rem; color: var(--color-text-muted); }
.stm-bar { height: 6px; border-radius: 99px; background: var(--color-bg-surface); overflow: hidden; margin-top: 6px; }
.stm-bar > i { display: block; height: 100%; background: var(--brand, var(--color-primary)); border-radius: 99px; }
.stm-row { display: flex; gap: 10px; align-items: center; padding: 6px 8px; border-radius: 8px; background: var(--color-bg-surface); }
.stm-row img { width: 36px; height: 36px; border-radius: 6px; flex: 0 0 auto; }
.stm-row.is-locked img { opacity: .55; }
.stm-row-name { font-weight: 600; font-size: .82rem; color: var(--color-text-primary); }
.stm-row-desc { font-size: .72rem; color: var(--color-text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.stm-row-side { margin-left: auto; font-size: .7rem; color: var(--color-text-muted); white-space: nowrap; }
"""


def _ach_row(a: Dict[str, Any]) -> str:
    unlocked = bool(a.get("achieved"))
    icon = a.get("icon_url") if unlocked else (a.get("icon_gray_url") or a.get("icon_url"))
    img = f'<img src="{esc(icon)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">' if icon else ""
    hidden = a.get("hidden") and not unlocked
    desc = "Hidden achievement" if hidden else (a.get("description") or "")
    pct = a.get("global_percent")
    side = "Unlocked" if unlocked else (f"{pct:g}% of players" if isinstance(pct, (int, float)) else "Locked")
    return (
        f'<div class="stm-row{"" if unlocked else " is-locked"}">{img}<div style="min-width:0">'
        f'<div class="stm-row-name">{esc(a.get("name") or "")}</div>'
        f'<div class="stm-row-desc">{esc(desc)}</div></div>'
        f'<div class="stm-row-side">{esc(side)}</div></div>'
    )


def game_panel_html(live: Any) -> str:
    """Current game, its progress and a few achievements (empty states included)."""
    if live is None or not getattr(live, "steam_id", None):
        return '<div class="itg-empty">Connect to Steam to see the game you are playing.</div>'
    name = getattr(live, "current_game_name", None)
    if not getattr(live, "current_game_appid", None):
        state = getattr(live, "persona_state", "Offline")
        return f'<div class="itg-empty">Not in a game right now ({esc(state)}).</div>'
    art = getattr(live, "current_game_art_url", None)
    counts = getattr(live, "achievement_counts", None) or {}
    progress = getattr(live, "current_game_progress", None)
    if getattr(live, "achievements_private", False):
        sub = "Achievements are private. Set Game details to Public in your Steam privacy settings."
    elif not getattr(live, "has_achievements", False):
        sub = "This game has no Steam achievements (or they haven't loaded yet)."
    else:
        sub = f'{counts.get("unlocked", 0)} of {counts.get("total", 0)} achievements · {progress or 0}%'
    img = f'<img src="{esc(art)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">' if art else ""
    bar = (
        f'<div class="stm-bar"><i style="width:{max(0, min(100, int(progress)))}%"></i></div>'
        if isinstance(progress, int)
        else ""
    )
    rows = "".join(_ach_row(a) for a in (getattr(live, "current_game_achievements", None) or [])[:ACHIEVEMENT_ROWS])
    return (
        f'<div class="stm-ach"><div class="stm-game">{img}<div style="min-width:0;flex:1">'
        f'<div class="stm-game-name">{esc(name or "Unknown game")}</div>'
        f'<div class="stm-game-sub">{esc(sub)}</div>{bar}</div></div>{rows}</div>'
    )


class SteamTab:
    name = "Steam"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.SteamSettingsData] = None
        self.ui_elements: Dict[str, Any] = {}
        self._page: Optional[IntegrationPage] = None
        self._card: Optional[AccountCard] = None
        self._fold: Any = None
        self._usage: Any = None
        self._game: Any = None
        self._game_html: str = ""
        self._status_timer: Optional[Any] = None
        self._connected: bool = False
        self._saving: bool = False
        self.connect_button: Any = None

    # ----- lifecycle -----

    def on_enter(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = True
        if self._page is not None:
            self._page.set_active(True)
        layout_schedule(0.05, self._refresh_status, once=True)

    def on_exit(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = False
        if self._page is not None:
            self._page.set_active(False)

    def build(self, parent_container) -> None:
        from ...help_system.help_render import inject_page_stylesheet

        inject_page_stylesheet("mycelian-steam-tab", STEAM_TAB_CSS)
        self._load_from_state()
        self._page = page = IntegrationPage(
            parent_container,
            "steam",
            "Steam",
            "Achievements, presence and the current game for overlays",
            on_save=self.save,
            on_discard=self.discard,
            is_dirty=self.has_unsaved_changes,
            help_topic="guide_steam",
            help_tooltip="Steam setup guide",
        )
        with page.column():
            with section("account_circle", "Account"):
                self._card = AccountCard("steam", "Steam profile")
                with self._card.actions:
                    outline_button("Get an API key", lambda: open_url(API_KEY_URL), icon="open_in_new").props(
                        "dense"
                    ).classes("px-3")
                    self.connect_button = primary_button("Connect", self._connect, icon="login").props("dense").classes(
                        "px-3"
                    )
                saved_key = bool((self.buffer.api_key or "").strip())
                self._fold = self._card.credentials("Web API key", opened=not saved_key)
                with self._fold.body:
                    with fields(one=True):
                        self.ui_elements["api_key"] = field(
                            "Web API key",
                            value=self.buffer.api_key or "",
                            password=True,
                            placeholder="32 characters from steamcommunity.com/dev/apikey",
                            tip="Your Steam Web API key. Mycelian stores it encrypted.",
                            on_change=lambda e: self._set("api_key", e.value),
                        )
                    hint(
                        "Sign in on Steam's API key page, enter any domain name (for example <b>localhost</b>), "
                        "and copy the key here.",
                        link_label="Open the API key page",
                        url=API_KEY_URL,
                    )
                self._fold.set_saved(saved_key)

        with page.column():
            with section("person_search", "Tracking"):
                with fields(one=True):
                    self.ui_elements["steam_profile"] = field(
                        "Steam profile",
                        value=self.buffer.steam_profile or "",
                        placeholder="Profile link, custom URL name or SteamID64",
                        tip="For example https://steamcommunity.com/id/yourname, yourname, or 7656119…",
                        on_change=lambda e: self._set("steam_profile", e.value),
                    )
                hint(
                    "Set <b>Game details</b> to Public in your Steam privacy settings, or Steam hides the game "
                    "you are playing and its achievements.",
                    link_label="Privacy settings",
                    url=PRIVACY_URL,
                )
                self._usage = UsageRow(["Steam"])

        with page.wide():
            with section("emoji_events", "Current game"):
                self._game = ui.html("", sanitize=False).classes("w-full")

        self._refresh_status()
        layout_schedule(0.8, self._refresh_status, once=True)
        self._status_timer = layout_schedule(3.0, self._refresh_status, active=True)

    # ----- settings buffer -----

    def _load_from_state(self) -> None:
        saved = state_manager.get_steam_settings_data()
        self.buffer = dataobjects.SteamSettingsData(api_key=saved.api_key, steam_profile=saved.steam_profile)
        self.dirty = False

    @staticmethod
    def _clean_key(text: str) -> str:
        """The 32-character key, even when the paste carries more (Steam's page shows "Key: …")."""
        match = _KEY_RE.search(text)
        return match.group(0) if match else text

    def _set(self, name: str, value: Any) -> None:
        value = "" if value is None else str(value).strip()
        if name == "api_key":
            value = self._clean_key(value)
        if getattr(self.buffer, name) != value:
            setattr(self.buffer, name, value)
            self.dirty = True
            if self._page is not None:
                self._page.refresh_dirty()

    def has_unsaved_changes(self) -> bool:
        return bool(self.dirty)

    def discard(self) -> None:
        self._load_from_state()
        for name, el in self.ui_elements.items():
            el.value = getattr(self.buffer, name) or ""
        self.dirty = False

    def mark_clean(self) -> None:
        self.dirty = False

    def _validation_error(self) -> Optional[str]:
        key = (self.buffer.api_key or "").strip()
        profile = (self.buffer.steam_profile or "").strip()
        if key and (len(key) != 32 or not all(c in "0123456789abcdefABCDEF" for c in key)):
            return "A Steam Web API key is 32 letters and numbers (0-9, A-F)."
        if profile and parse_profile(profile) is None:
            return "Use a profile link, a custom URL name or a 17-digit SteamID64."
        return None

    def save(self, *, suppress_saved_notification: bool = False) -> None:
        if self.buffer is None or self._saving:
            return
        problem = self._validation_error()
        if problem:
            notify(problem, type="warning", source="steam", title="Steam settings not saved")
            return
        needs_profile = bool((self.buffer.api_key or "").strip()) and not (self.buffer.steam_profile or "").strip()
        for name in ("api_key", "steam_profile"):
            state_manager.update_steam_setting(name, getattr(self.buffer, name))
        if not state_manager.save_changes():
            return  # save_changes() already reported it (changes stay pending)
        self.dirty = False
        if self._page is not None:
            self._page.refresh_dirty()
        self._saving = True

        async def apply() -> None:
            try:
                await run.io_bound(steam_service.handle_steam_settings_change)
            except Exception as e:
                logger.error("Steam settings change failed: %s", e, exc_info=True)
                notify(
                    "",
                    type="warning",
                    source="steam",
                    title="Steam settings saved, but Steam couldn't connect",
                    hint="Check the Web API key and the profile, then press Connect.",
                    details=f"{type(e).__name__}: {e}",
                )
            else:
                if needs_profile:
                    notify(
                        "Now enter your Steam profile under Tracking so Mycelian knows whose games to show.",
                        type="info",
                        source="steam",
                        title="Steam API key saved",
                    )
                elif not suppress_saved_notification:
                    notify("", type="success", source="steam", title="Steam settings saved")
            finally:
                self._saving = False
                self._refresh_status()

        from nicegui import background_tasks

        background_tasks.create(apply(), name="steam_save_settings_change")

    def _connect(self) -> None:
        """Save what is typed and (re)connect."""
        if not (self.buffer.api_key or "").strip():
            if self._fold is not None:
                self._fold.open()
            notify("Paste your Steam Web API key first.", type="info", source="steam", title="Steam API key needed")
            return
        self.save(suppress_saved_notification=True)

    # ----- status -----

    def _status(self) -> Tuple[str, str, str, str, str, List[tuple]]:
        """(pill kind, pill text, card name, card state kind, card state text, meta rows)."""
        status, name, color = steam_service.get_steam_status_display()
        kind = {"text-theme-success": "ok", "text-theme-warning": "warn"}.get(color, "error")
        saved = state_manager.get_steam_settings_data()
        if status == "Not Connected" and not (saved.api_key or "").strip():
            return "off", "Not connected", "Not connected", "off", "Add your Web API key to connect", []
        live = state_manager.get_live_steam_data()
        meta: List[tuple] = []
        state_text = status
        if live is not None and kind in ("ok", "warn"):
            persona = getattr(live, "persona_state", "") or status
            game = getattr(live, "current_game_name", None)
            state_text = f"{persona} · Playing {game}" if game else persona
            level = getattr(live, "steam_level", None)
            if level is not None:
                meta.append(("Steam level", str(level), "", ""))
            if game:
                counts = getattr(live, "achievement_counts", None) or {}
                sub = f'{counts.get("unlocked", 0)} of {counts.get("total", 0)} achievements' if counts.get("total") else ""
                meta.append(("Now playing", game, sub, ""))
        elif status == "Invalid API Key":
            state_text = "Steam refused the Web API key"
        elif status == "Profile Not Found":
            state_text = "No Steam profile matches what you entered"
        if status == "Profile Not Set":
            return "warn", "Profile needed", "Steam profile", "warn", "Enter your Steam profile under Tracking", []
        pill = "Connected" if kind == "ok" else status
        return kind, pill, name or "Steam profile", kind, state_text, meta

    def _refresh_status(self) -> None:
        try:
            pill_kind, pill_text, name, state_kind, state_text, meta = self._status()
            self._connected = pill_kind in ("ok", "warn") and pill_text != "Profile needed"
            if self._page is not None:
                self._page.set_status([(pill_kind, pill_text)])
            if self._usage is not None:
                self._usage.refresh()
            live = state_manager.get_live_steam_data()
            if self._card is not None:
                photo = getattr(live, "avatar_url", None) if (live is not None and self._connected) else None
                initial = initial_of(name) if self._connected else None
                self._card.set(name, state_kind, state_text, initial=initial, photo_url=photo)
                self._card.set_meta(meta)
            if self._fold is not None:
                self._fold.set_saved(bool((state_manager.get_steam_settings_data().api_key or "").strip()))
            if self._game is not None:
                html = game_panel_html(live if self._connected else None)
                if html != self._game_html:
                    self._game_html = html
                    self._game.set_content(html)
        except Exception as e:
            logger.error("Error refreshing Steam status: %s", e, exc_info=True)
            self._connected = False
            if self._page is not None:
                self._page.set_status([("error", "Error")])
            if self._card is not None:
                self._card.set("Steam profile", "error", "Error")
                self._card.set_meta([])
        if self.connect_button is not None and not self._saving:
            set_connect_style(self.connect_button, self._connected, connect="Connect", reconnect="Reconnect")
