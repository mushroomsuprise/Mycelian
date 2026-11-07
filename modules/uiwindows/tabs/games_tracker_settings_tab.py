# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Settings tab: Games Tracker options (libraries, events, sync timing, milestones, cover cache, baseline).

The library itself is the Games Tracker main tab (games_tracker_tab.py).
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Dict, Optional

from nicegui import run, ui

from ...game_tracker import covers
from ...game_tracker.models import TrackerSettings, clean_milestones
from ...game_tracker.service import get_service
from ...notification_engine import notify
from ...ui_buttons import outline_button
from .integration_ui import IntegrationPage, esc, fields, hint, icon_html, section, switch_row, tool_row

logger = logging.getLogger(__name__)


class GamesTrackerSettingsTab:
    name = "Games Tracker"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.ui_elements: Dict[str, Any] = {}
        self._page: Optional[IntegrationPage] = None
        self._loaded: TrackerSettings = TrackerSettings()
        self._buffer: TrackerSettings = TrackerSettings()

    # ----- lifecycle -----

    @property
    def service(self):
        return get_service()

    def has_unsaved_changes(self) -> bool:
        return self.dirty

    @staticmethod
    def _settings_dict(s: TrackerSettings) -> Dict[str, Any]:
        d = s.to_dict()
        d.pop("export", None)  # kept by the main tab
        d.pop("view", None)
        return d

    def _update_dirty(self) -> None:
        self.dirty = self._settings_dict(self._buffer) != self._settings_dict(self._loaded)

    def build(self, parent_container) -> None:
        from ...help_system.help_render import inject_page_stylesheet, promote_color_importance, scope_selectors
        from .games_tracker_tab import _STYLE_ID, GAMES_TRACKER_CSS

        inject_page_stylesheet(_STYLE_ID, promote_color_importance(scope_selectors(GAMES_TRACKER_CSS, ".itg")))
        self._loaded = self.service.settings
        self._buffer = TrackerSettings.from_dict(self._loaded.to_dict())
        self.dirty = False
        b = self._buffer
        self._page = page = IntegrationPage(
            parent_container,
            "games_tracker",
            "Games Tracker",
            "Libraries, events, sync timing and milestones for the Games Tracker tab",
            icon="emoji_events",
            on_save=self.save,
            on_discard=self.discard,
            is_dirty=lambda: self.dirty,
            help_topic="games_tracker",
            help_tooltip="How the Games Tracker works",
        )
        with page.column():
            with section("sports_esports", "Tracking"):
                self.ui_elements["opt_psn"] = switch_row(
                    "Track PlayStation library", "Imports every title that has a trophy list and resolves its NPWR ID",
                    b.psn_enabled, lambda v: self._set_opt(psn_enabled=v))
                self.ui_elements["opt_steam"] = switch_row(
                    "Track Steam library", "Games you have played (playtime above zero)",
                    b.steam_enabled, lambda v: self._set_opt(steam_enabled=v))
                self.ui_elements["opt_stats"] = switch_row(
                    "Count changes in Statistics", "Unlocks, platinums, 100% games, new games and milestones after the first import",
                    b.record_stats, lambda v: self._set_opt(record_stats=v))
                self.ui_elements["opt_events"] = switch_row(
                    "Send chatbot and connector events", "Game unlock, completed, started and milestone triggers",
                    b.send_events, lambda v: self._set_opt(send_events=v))
                self.ui_elements["opt_backlog"] = switch_row(
                    "Keep games with goals in a Backlog section", "Off: they stay in the library with everything else",
                    b.backlog_section, lambda v: self._set_opt(backlog_section=v))
        with page.column():
            with section("schedule", "Sync and milestones"):
                with fields():
                    self.ui_elements["opt_sync"] = self._number("Library sync", b.sync_minutes, "minutes", 5, 120, "sync_minutes")
                    self.ui_elements["opt_ttl"] = self._number("Cover art cache", b.cover_ttl_days, "days", 1, 90, "cover_ttl_days")
                with fields():
                    self.ui_elements["opt_missing"] = self._number(
                        "Retry missing covers", b.cover_missing_hours, "hours", 1, 72, "cover_missing_hours")
                    with ui.element("div"):
                        ui.html("Progress milestones", sanitize=False).classes("itg-field-label")
                        self.ui_elements["milestones"] = ui.element("div").classes("gtr-tokens")
                hint("The first import of each account is silent. Only changes after it count toward Statistics, chatbot events and "
                     "connectors. Switching account starts a new silent import.")
        with page.wide():
            with section("build", "Maintenance"):
                with tool_row("image", "Cover art cache", "Covers are kept on disk. Clearing re-downloads them in the background."):
                    outline_button("Clear covers", self._clear_covers, icon="delete_sweep")
                with tool_row("restart_alt", "Reset baseline", "Treat current progress as the new starting point. Nothing already earned gets counted."):
                    ui.button("Reset baseline", icon="restart_alt", on_click=self._confirm_reset).props("outline no-caps dense").classes("gtr-danger")
                with tool_row("sports_esports", "Library", "Games, goals, speedrun info and export live on the Games Tracker tab."):
                    outline_button("Open Games Tracker", _open_games_tracker, icon="arrow_forward")
        self._render_milestones()

    def on_enter(self) -> None:
        if self._page is not None:
            self._page.set_active(True)

    def on_exit(self) -> None:
        if self._page is not None:
            self._page.set_active(False)

    # ----- options -----

    def _number(self, label: str, value: int, unit: str, lo: int, hi: int, key: str) -> Any:
        with ui.element("div"):
            ui.html(esc(label), sanitize=False).classes("itg-field-label")
            with ui.element("div").classes("gtr-unit"):
                num = ui.number(value=value, min=lo, max=hi, step=1, format="%d").props("outlined dense").classes("gtr-num")
                num.on_value_change(lambda e, k=key, lo=lo, hi=hi: self._set_opt(**{k: max(lo, min(hi, int(e.value or lo)))}))
                ui.label(unit)
        return num

    def _render_milestones(self) -> None:
        box = self.ui_elements.get("milestones")
        if box is None:
            return
        box.clear()
        with box:
            for m in self._buffer.milestones:
                with ui.element("span").classes("gtr-token"):
                    ui.label(f"{m}%")
                    ui.button(icon="close", on_click=lambda m=m: self._remove_milestone(m)).props(
                        f'flat round dense size=xs aria-label="Remove {m}%"').classes("itg-ibtn")
            add = ui.number(placeholder="Add %", min=1, max=99, step=1, format="%d").props("outlined dense").classes("gtr-num")
            add.on("keydown.enter", lambda _e, a=add: self._add_milestone(a.value))
            add.on("blur", lambda _e, a=add: self._add_milestone(a.value))

    def _add_milestone(self, value: Any) -> None:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return
        if n in self._buffer.milestones or not 1 <= n <= 99:
            return
        self._set_opt(milestones=clean_milestones(self._buffer.milestones + [n]))
        self._render_milestones()

    def _remove_milestone(self, value: int) -> None:
        self._set_opt(milestones=[m for m in self._buffer.milestones if m != value])
        self._render_milestones()

    def _set_opt(self, **changes: Any) -> None:
        for k, v in changes.items():
            setattr(self._buffer, k, v)
        self._update_dirty()

    def save(self) -> None:
        try:
            current = self.service.settings
            buf = TrackerSettings.from_dict({**self._buffer.to_dict(), "export": current.export, "view": current.view})
            self._loaded = self.service.save_settings(buf)
            self._buffer = TrackerSettings.from_dict(self._loaded.to_dict())
            self.dirty = False
            _refresh_library()
            notify("", type="success", source="game_tracker", title="Games Tracker settings saved")
        except Exception as e:
            logger.error("Games Tracker settings save failed: %s", e, exc_info=True)
            notify("", type="error", source="game_tracker", title="Couldn't save the Games Tracker settings",
                   hint="Check the Mycelian data folder is writable, then try again.", details=f"{type(e).__name__}: {e}")

    def discard(self) -> None:
        self._buffer = TrackerSettings.from_dict(self._loaded.to_dict())
        self.dirty = False
        b = self._buffer
        for key, value in (("opt_psn", b.psn_enabled), ("opt_steam", b.steam_enabled), ("opt_stats", b.record_stats),
                           ("opt_events", b.send_events), ("opt_backlog", b.backlog_section),
                           ("opt_sync", b.sync_minutes), ("opt_ttl", b.cover_ttl_days),
                           ("opt_missing", b.cover_missing_hours)):
            el = self.ui_elements.get(key)
            if el is not None:
                el.value = value
        self._buffer = TrackerSettings.from_dict(self._loaded.to_dict())  # the value setters above re-ran _set_opt
        self.dirty = False
        self._render_milestones()

    # ----- maintenance -----

    async def _clear_covers(self) -> None:
        removed = await run.io_bound(covers.clear_covers)
        self.service.sync_now()
        notify(f"{removed:,} cover{'s' if removed != 1 else ''} removed. They download again in the background.", type="success",
               source="game_tracker", title="Cover art cleared")
        _refresh_library()

    def _dialog_parent(self) -> Any:
        """Small dialogs live in the page (also when opened from code, outside a click handler)."""
        return self._page.root if self._page is not None else contextlib.nullcontext()

    def _confirm_reset(self) -> None:
        with self._dialog_parent(), ui.dialog() as dialog, ui.card().classes("itg-dialog is-danger"):
            ui.html(f'{icon_html("restart_alt")}Reset the baseline?', sanitize=False).classes("itg-dialog-title")
            ui.label("The next sync reads every game silently and treats that as the new starting point. Trophies and achievements "
                     "already earned won't reach Statistics, chat or connectors.").classes("itg-dialog-sub")
            with ui.element("div").classes("itg-actions-end"):
                outline_button("Cancel", dialog.close)
                ui.button("Reset baseline", on_click=lambda: (self._reset_baseline(), dialog.close())).props("unelevated no-caps color=negative")
        dialog.open()

    def _reset_baseline(self) -> None:
        self.service.reset_baseline()
        self.service.sync_now()
        notify("", type="success", source="game_tracker", title="Baseline reset", hint="The next sync sets the new starting point.")


def _refresh_library() -> None:
    """Have the Games Tracker main tab redraw (if it was built) with the new settings."""
    from ..games_tracker import get_games_tracker_tab

    tab = get_games_tracker_tab()
    if tab is not None:
        tab._needs_refresh = True


def _open_games_tracker() -> None:
    from ...help_system.contextual_help import navigate_to_main_tab

    navigate_to_main_tab("Games Tracker")
