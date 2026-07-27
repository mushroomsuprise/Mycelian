# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from nicegui import ui

from ... import dataobjects
from ...dataobjects import DiscordData, state_manager
from ... import discord_service
from ...notification_engine import notify
from ...ui_buttons import outline_button, primary_button
from ...ui_form_controls import form_sensitive_input
from ...help_system.contextual_help import help_button
from ...ui_settings_layout import (
    THEME_CHIP_CLASSES,
    settings_form_grid,
    settings_section,
    settings_status_band,
    settings_surface,
    theme_chip_row,
)
from ...ui_timer import layout_schedule

logger = logging.getLogger(__name__)


class DiscordTab:
    name = "Discord"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.DiscordData] = None
        self.ui_elements: Dict[str, Any] = {}
        self._status_timer: Optional[Any] = None
        self._connect_in_progress: bool = False
        self._go_live_chip_container: Optional[Any] = None
        self._guild_select: Optional[Any] = None
        self._channel_select: Optional[Any] = None
        self._guilds_cache: List[Dict[str, str]] = []
        self._channels_cache: List[Dict[str, str]] = []

    def on_enter(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = True
        layout_schedule(0.05, self._refresh_status, once=True)
        layout_schedule(0.15, self._reload_guilds, once=True)

    def on_exit(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = False

    def _load_from_state(self) -> None:
        raw = state_manager.get_discord_data()
        self.buffer = DiscordData(
            **{
                f.name: getattr(raw, f.name)
                for f in DiscordData.__dataclass_fields__.values()
            }
        )
        if self.buffer.go_live_channels is None:
            self.buffer.go_live_channels = []
        self.dirty = False

    def _set(self, field: str, value: Any) -> None:
        if self.buffer is None:
            return
        if getattr(self.buffer, field) != value:
            setattr(self.buffer, field, value)
            self.dirty = True

    def _channel_key(self, entry: Dict[str, Any]) -> str:
        return f"{entry.get('guild_id')}:{entry.get('channel_id')}"

    def _refresh_status(self) -> None:
        try:
            status = discord_service.get_discord_status()
            connected = bool(status.get("is_connected"))
            label = status.get("status") or "Unknown"
            bot_name = status.get("bot_username") or ""
            if connected and bot_name:
                label = f"Connected — {bot_name}"

            if "status_label" in self.ui_elements:
                self.ui_elements["status_label"].set_text(label)
                css = (
                    "font-semibold text-theme-success"
                    if connected
                    else "font-semibold text-theme-error"
                )
                if (status.get("status") or "").lower() in (
                    "connecting",
                    "reconnecting",
                ):
                    css = "font-semibold text-theme-warning"
                self.ui_elements["status_label"].classes(replace=css)

            invite = status.get("invite_url") or discord_service.get_invite_url() or ""
            if "invite_url" in self.ui_elements:
                self.ui_elements["invite_url"].value = invite

            if "connect_button" in self.ui_elements and not self._connect_in_progress:
                if connected:
                    self.ui_elements["connect_button"].set_text("Reconnect")
                else:
                    self.ui_elements["connect_button"].set_text("Connect")
        except Exception:
            logger.debug("Discord status refresh failed", exc_info=True)

    def _reload_guilds(self) -> None:
        try:
            if not discord_service.discord_service.is_connected():
                self._guilds_cache = []
                self._channels_cache = []
                if self._guild_select is not None:
                    self._guild_select.set_options({})
                if self._channel_select is not None:
                    self._channel_select.set_options({})
                return
            self._guilds_cache = discord_service.list_guilds()
            options = {
                g["id"]: g.get("name") or g["id"] for g in self._guilds_cache
            }
            if self._guild_select is not None:
                self._guild_select.set_options(options)
                if options and not self._guild_select.value:
                    first = next(iter(options))
                    self._guild_select.value = first
                    self._on_guild_changed(first)
        except Exception:
            logger.debug("Failed to reload Discord guilds", exc_info=True)

    def _on_guild_changed(self, guild_id: Any) -> None:
        gid = str(guild_id or "").strip()
        if not gid:
            return
        try:
            self._channels_cache = discord_service.list_text_channels(gid)
            options = {
                c["id"]: f"#{c.get('name') or c['id']}" for c in self._channels_cache
            }
            if self._channel_select is not None:
                self._channel_select.set_options(options)
                self._channel_select.value = None
        except Exception:
            logger.debug("Failed to load Discord channels", exc_info=True)

    def _rebuild_go_live_chips(self) -> None:
        if not self._go_live_chip_container:
            return
        self._go_live_chip_container.clear()
        channels = list(getattr(self.buffer, "go_live_channels", None) or [])
        for entry in channels:
            self._create_channel_chip(entry)

    def _create_channel_chip(self, entry: Dict[str, Any]) -> None:
        if not self._go_live_chip_container:
            return
        guild_name = entry.get("guild_name") or entry.get("guild_id") or "?"
        channel_name = entry.get("channel_name") or entry.get("channel_id") or "?"
        label = f"{guild_name} / #{channel_name}"
        key = self._channel_key(entry)
        with self._go_live_chip_container:
            with (
                ui.element("div")
                .classes(THEME_CHIP_CLASSES)
                .style("white-space: nowrap;")
            ):
                ui.label(label).classes("text-sm").style("white-space: nowrap;")
                ui.button(
                    icon="close",
                    on_click=lambda _e, k=key: self._remove_go_live_channel(k),
                ).props("flat dense round size=xs")

    def _add_go_live_channel(self) -> None:
        if self.buffer is None:
            return
        if self._guild_select is None or self._channel_select is None:
            return
        guild_id = str(self._guild_select.value or "").strip()
        channel_id = str(self._channel_select.value or "").strip()
        if not guild_id or not channel_id:
            notify("Select a server and channel first", type="warning")
            return
        guild_name = next(
            (g.get("name") for g in self._guilds_cache if g.get("id") == guild_id),
            guild_id,
        )
        channel_name = next(
            (
                c.get("name")
                for c in self._channels_cache
                if c.get("id") == channel_id
            ),
            channel_id,
        )
        entry = {
            "guild_id": guild_id,
            "channel_id": channel_id,
            "guild_name": guild_name or guild_id,
            "channel_name": channel_name or channel_id,
        }
        existing = list(self.buffer.go_live_channels or [])
        key = self._channel_key(entry)
        if any(self._channel_key(e) == key for e in existing if isinstance(e, dict)):
            notify("Channel already added", type="warning")
            return
        existing.append(entry)
        self.buffer.go_live_channels = existing
        self.dirty = True
        self._rebuild_go_live_chips()

    def _remove_go_live_channel(self, key: str) -> None:
        if self.buffer is None:
            return
        existing = [
            e
            for e in (self.buffer.go_live_channels or [])
            if isinstance(e, dict) and self._channel_key(e) != key
        ]
        self.buffer.go_live_channels = existing
        self.dirty = True
        self._rebuild_go_live_chips()

    def _sync_token_from_ui(self) -> str:
        """Prefer a non-empty UI token; never replace a buffer token with empty UI."""
        if not self.buffer:
            return ""
        ui_token = ""
        if "bot_token" in self.ui_elements:
            ui_token = (self.ui_elements["bot_token"].value or "").strip()
        buffer_token = (getattr(self.buffer, "bot_token", "") or "").strip()
        if ui_token:
            self._set("bot_token", ui_token)
            return ui_token
        return buffer_token

    def _refresh_token_input(self) -> None:
        if self.buffer is None:
            return
        if "bot_token" in self.ui_elements:
            self.ui_elements["bot_token"].value = self.buffer.bot_token or ""

    def _handle_connect(self) -> None:
        if self._connect_in_progress:
            return
        if not self.buffer:
            return
        token = self._sync_token_from_ui()
        if not token:
            notify("Enter a Discord bot token first", type="warning")
            return

        self._connect_in_progress = True
        if "connect_button" in self.ui_elements:
            self.ui_elements["connect_button"].disable()
            self.ui_elements["connect_button"].set_text("Connecting…")

        # Persist token before connect so reconnect works after restart
        self.save(silent=True)

        result: Dict[str, Any] = {"done": False, "ok": False}

        def worker():
            try:
                result["ok"] = discord_service.connect_with_token(token)
            except Exception as e:
                logger.error("Discord connect failed: %s", e, exc_info=True)
                result["error"] = str(e)
            finally:
                result["done"] = True

        threading.Thread(target=worker, daemon=True, name="DiscordConnect").start()

        def poll():
            if not result["done"]:
                layout_schedule(0.5, poll, once=True)
                return
            self._connect_in_progress = False
            if "connect_button" in self.ui_elements:
                self.ui_elements["connect_button"].enable()
            if result.get("ok"):
                notify("Discord bot connected", type="positive")
                self._load_from_state()
                self._refresh_token_input()
                self._reload_guilds()
            else:
                err = result.get("error") or "Check the bot token and try again"
                notify(f"Discord connect failed: {err}", type="negative")
            self._refresh_status()

        layout_schedule(0.5, poll, once=True)

    def _handle_disconnect(self) -> None:
        try:
            discord_service.disconnect()
            notify("Discord bot disconnected", type="info")
            self._load_from_state()
            self._reload_guilds()
            self._refresh_status()
        except Exception as e:
            logger.error("Discord disconnect error: %s", e, exc_info=True)
            notify(f"Disconnect error: {e}", type="negative")

    def _copy_invite_url(self) -> None:
        url = ""
        if "invite_url" in self.ui_elements:
            url = (self.ui_elements["invite_url"].value or "").strip()
        if not url:
            url = discord_service.get_invite_url()
        if not url:
            notify("Connect the bot first to generate an invite URL", type="warning")
            return
        try:
            ui.clipboard.write(url)
            notify("Invite URL copied", type="positive")
        except Exception:
            notify(url, type="info", timeout=8000)

    def _open_invite(self) -> None:
        url = discord_service.get_invite_url()
        if not url and "invite_url" in self.ui_elements:
            url = (self.ui_elements["invite_url"].value or "").strip()
        if not url:
            notify("Connect the bot first to generate an invite URL", type="warning")
            return
        ui.navigate.to(url, new_tab=True)

    def build(self, parent_container) -> None:
        self._load_from_state()
        with settings_surface(parent_container):
            with settings_status_band():
                with ui.column().classes("gap-0"):
                    ui.label("Status").classes("text-xs secondary-text")
                    self.ui_elements["status_label"] = ui.label("Loading...").classes(
                        "font-semibold text-sm"
                    )

            with settings_section(
                "Connection",
                subtitle="Bot token from the Developer Portal — see Help for setup steps",
            ):
                with ui.row().classes("w-full items-center justify-between gap-2 mb-1"):
                    ui.label(
                        "Need a bot token? Open Help for the full Discord setup guide."
                    ).classes("text-xs secondary-text")
                    help_button(
                        topic_id="integrations_discord",
                        tooltip="Discord setup help",
                        size="sm",
                    )

                with settings_form_grid(columns=1):
                    self.ui_elements["bot_token"] = form_sensitive_input(
                        tooltip="Discord bot token (kept encrypted in the app database)",
                        label="Bot token",
                        value=getattr(self.buffer, "bot_token", ""),
                        placeholder="Bot token",
                        on_change=lambda e: self._set(
                            "bot_token",
                            "" if e.value is None else str(e.value).strip(),
                        ),
                    )

                with settings_form_grid(columns=1):
                    self.ui_elements["invite_url"] = form_sensitive_input(
                        tooltip="OAuth invite URL generated after a successful connect",
                        label="Invite URL",
                        value="",
                        placeholder="Connect to generate",
                    )
                    try:
                        self.ui_elements["invite_url"].props("readonly")
                    except Exception:
                        pass

                with ui.row().classes(
                    "button-row w-full justify-end gap-2 mt-2 flex-wrap"
                ):
                    outline_button(
                        "Copy invite",
                        self._copy_invite_url,
                        icon="content_copy",
                    )
                    outline_button(
                        "Invite bot",
                        self._open_invite,
                        icon="open_in_new",
                    )
                    outline_button(
                        "Disconnect",
                        self._handle_disconnect,
                        icon="logout",
                    )
                    self.ui_elements["connect_button"] = primary_button(
                        "Connect",
                        self._handle_connect,
                        icon="login",
                    )

            with settings_section(
                "Stream is live",
                subtitle=(
                    "Announce once per stream session when Twitch or YouTube goes live "
                    "(multi-stream safe — will not double-send)"
                ),
            ):
                self.ui_elements["go_live_enabled"] = ui.switch(
                    "Enable go-live Discord message",
                    value=bool(getattr(self.buffer, "go_live_enabled", False)),
                    on_change=lambda e: self._set("go_live_enabled", bool(e.value)),
                )

                self.ui_elements["go_live_message"] = ui.textarea(
                    label="Message template",
                    value=getattr(self.buffer, "go_live_message", "")
                    or "Stream is live on {platform}! {url}",
                    on_change=lambda e: self._set(
                        "go_live_message",
                        "" if e.value is None else str(e.value),
                    ),
                ).classes("w-full").props(
                    'autogrow outlined dense hint="Placeholders: {platform}, {title}, {url}"'
                )

                ui.label("Announcement channels").classes(
                    "text-sm font-semibold mt-2"
                )
                self._go_live_chip_container = theme_chip_row()
                self._rebuild_go_live_chips()

                with ui.row().classes("w-full items-end gap-2 flex-wrap mt-2"):
                    self._guild_select = ui.select(
                        options={},
                        label="Server",
                        with_input=True,
                        on_change=lambda e: self._on_guild_changed(e.value),
                    ).classes("min-w-[12rem] flex-1")
                    self._channel_select = ui.select(
                        options={},
                        label="Channel",
                        with_input=True,
                    ).classes("min-w-[12rem] flex-1")
                    outline_button("Add channel", self._add_go_live_channel, icon="add")
                    outline_button(
                        "Refresh servers",
                        self._reload_guilds,
                        icon="refresh",
                    )

            with ui.row().classes(
                "button-row w-full justify-end gap-2 mt-1 flex-wrap"
            ):
                outline_button("Discard", self.discard)
                primary_button("Save", self.save)

            self._status_timer = layout_schedule(5.0, self._refresh_status, active=True)

        self._refresh_status()
        self._reload_guilds()

    def save(self, silent: bool = False) -> None:
        if not self.buffer:
            return
        # Save from buffer only (like OBS). Password inputs often report empty
        # .value and would wipe a typed/stored token if re-read blindly.
        self._sync_token_from_ui()
        payload = {
            f.name: getattr(self.buffer, f.name)
            for f in DiscordData.__dataclass_fields__.values()
        }
        state_manager.set_discord_data(payload)
        if state_manager.save_changes():
            self.dirty = False
            if not silent:
                notify("Discord settings saved", type="positive")
            self._refresh_status()
        elif not silent:
            notify("Error saving Discord settings", type="negative")

    def discard(self) -> None:
        self._load_from_state()
        if self.buffer is None:
            return
        self._refresh_token_input()
        if "go_live_enabled" in self.ui_elements:
            self.ui_elements["go_live_enabled"].value = bool(
                self.buffer.go_live_enabled
            )
        if "go_live_message" in self.ui_elements:
            self.ui_elements["go_live_message"].value = (
                self.buffer.go_live_message
                or "Stream is live on {platform}! {url}"
            )
        self._rebuild_go_live_chips()
        self.dirty = False
        self._refresh_status()
