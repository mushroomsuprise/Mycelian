# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from nicegui import ui

from ... import dataobjects
from ...dataobjects import DiscordData, state_manager
from ... import discord_service
from ...notification_engine import nav_actions_settings, notify
from ...ui_buttons import outline_button, primary_button
from ...ui_timer import layout_schedule
from .integration_ui import (
    AccountCard,
    CopyRow,
    CredentialsFold,
    IntegrationPage,
    field as input_field,
    field_label,
    fields,
    hint,
    initial_of,
    list_box,
    list_empty,
    list_item,
    section,
    steps,
    steps_html,
    switch_row,
    tokens,
)

logger = logging.getLogger(__name__)

_DEFAULT_GO_LIVE_MESSAGE = "Stream is live on {platform}! {url}"


class _InviteRow(CopyRow):
    """Invite link row whose copy button keeps the tab's own copy flow and notices."""

    def __init__(self, on_copy: Callable[[], Any], label: str, **kwargs: Any) -> None:
        self._on_copy = on_copy
        super().__init__(label, **kwargs)

    def _copy(self) -> None:
        self._on_copy()


class DiscordTab:
    name = "Discord"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.DiscordData] = None
        self.ui_elements: Dict[str, Any] = {}
        self._creds: Dict[str, str] = {"bot_token": ""}
        self._token_saved: bool = False
        self._status_timer: Optional[Any] = None
        self._connect_in_progress: bool = False
        self._go_live_chip_container: Optional[Any] = None
        self._guild_select: Optional[Any] = None
        self._channel_select: Optional[Any] = None
        self._guilds_cache: List[Dict[str, str]] = []
        self._channels_cache: List[Dict[str, str]] = []
        self._page: Optional[IntegrationPage] = None
        self._card: Optional[AccountCard] = None
        self._fold: Optional[CredentialsFold] = None
        self._invite_row: Optional[CopyRow] = None
        self._invite_url: str = ""
        self._steps: Optional[Any] = None
        self._steps_content: str = ""
        self._connect_button: Optional[Any] = None
        self._reconnect_button: Optional[Any] = None
        self._add_row: Optional[Any] = None
        self._picker_hint: Optional[Any] = None
        self._connected: Optional[bool] = None

    @staticmethod
    def _str_from_value_event(e: Any) -> str:
        """
        Read full string from input value events. Do not use e.args[0] when
        args is already a str — that would keep only the first character.
        """
        v = getattr(e, "value", None)
        if v is not None and not isinstance(v, (list, tuple)):
            return str(v)
        args = getattr(e, "args", None)
        if isinstance(args, str):
            return args
        if isinstance(args, (list, tuple)) and len(args) > 0:
            return str(args[0])
        return ""

    def on_enter(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = True
        if self._page is not None:
            self._page.set_active(True)
        layout_schedule(0.05, self._refresh_status, once=True)
        layout_schedule(0.15, self._reload_guilds, once=True)

    def on_exit(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = False
        if self._page is not None:
            self._page.set_active(False)

    def _load_from_state(self) -> None:
        raw = state_manager.get_discord_data()
        values = {}
        for field in DiscordData.__dataclass_fields__.values():
            value = getattr(raw, field.name)
            if isinstance(value, (list, dict)):
                value = copy.deepcopy(value)
            values[field.name] = value
        self.buffer = DiscordData(**values)
        if self.buffer.go_live_channels is None:
            self.buffer.go_live_channels = []
        self._saved_go_live_channels = self._go_live_signature()
        self._creds = {
            "bot_token": (getattr(self.buffer, "bot_token", "") or "").strip(),
        }
        self._token_saved = bool(self._creds["bot_token"])
        self.dirty = False

    def _go_live_signature(self) -> tuple:
        items = []
        if self.buffer is None:
            return tuple()
        for entry in self.buffer.go_live_channels or []:
            if isinstance(entry, dict):
                items.append(
                    (
                        str(entry.get("guild_id") or ""),
                        str(entry.get("channel_id") or ""),
                    )
                )
        return tuple(sorted(items))

    def _go_live_is_dirty(self) -> bool:
        return self._go_live_signature() != getattr(
            self, "_saved_go_live_channels", tuple()
        )

    def _sync_go_live_dirty(self) -> None:
        """Channel-list edits are compared to the saved list at leave time."""
        return

    def has_unsaved_changes(self) -> bool:
        """Token and switches use the dirty flag. Go-live channels are a list."""
        return bool(self.dirty) or self._go_live_is_dirty()

    def _set(self, field: str, value: Any) -> None:
        if self.buffer is None:
            return
        if getattr(self.buffer, field) != value:
            setattr(self.buffer, field, value)
            if not getattr(self, "_suppress_dirty", False):
                self.dirty = True

    def _set_cred(self, field: str, value: str) -> None:
        text = (value or "").strip()
        # Password fields often emit blank events on mount/blur; do not wipe a
        # known-good token (Discord tokens commonly contain '.', '_', '+', '=').
        if not text and (self._creds.get(field) or "").strip():
            return
        if self._creds.get(field) != text:
            self._creds[field] = text
            if not getattr(self, "_suppress_dirty", False):
                self.dirty = True

    def _persist_credentials_to_state(self) -> None:
        """Write credential buffer into DiscordData state (Spotify-style)."""
        token = (self._creds.get("bot_token") or "").strip()
        if self.buffer is not None:
            self.buffer.bot_token = token
        state_manager.update_discord_field("bot_token", token)

    def _save_settings_only(self) -> bool:
        """Persist Discord credentials + buffer fields. Returns True on success.

        Every False has already been reported to the user (by save_changes or here).
        """
        try:
            self._persist_credentials_to_state()
            if self.buffer is not None:
                for field in DiscordData.__dataclass_fields__:
                    if field == "bot_token":
                        continue
                    state_manager.update_discord_field(
                        field, getattr(self.buffer, field)
                    )
            ok = bool(state_manager.save_changes())
            if not ok:
                logger.warning("Discord save_changes returned False")
                return False
            # Readback verify (log level is WARNING in production)
            stored = (state_manager.get_discord_data().bot_token or "").strip()
            expected = (self._creds.get("bot_token") or "").strip()
            if expected and not stored:
                logger.warning(
                    "Discord token save readback empty after save_changes "
                    "(expected len=%s)",
                    len(expected),
                )
                self._notify_save_failed("The bot token didn't stick after saving.")
                return False
            self._token_saved = bool(expected)
            return True
        except Exception as e:
            logger.error("Error saving Discord settings: %s", e, exc_info=True)
            self._notify_save_failed(f"{type(e).__name__}: {e}")
            return False

    @staticmethod
    def _notify_save_failed(details: str) -> None:
        notify(
            "Your changes are still in the form.",
            type="error",
            source="discord",
            title="Couldn't save Discord settings",
            hint="Check the connection in Settings → Database, then press Save again.",
            details=details,
            actions=nav_actions_settings("Database"),
        )

    def _channel_key(self, entry: Dict[str, Any]) -> str:
        return f"{entry.get('guild_id')}:{entry.get('channel_id')}"

    def _status_view(self, status: Dict[str, Any]) -> tuple:
        """(state kind, header pill text, card kicker, card name, card state line)."""
        connected = bool(status.get("is_connected"))
        raw = str(status.get("status") or "Unknown")
        low = raw.lower()
        if connected:
            bot_name = str(status.get("bot_username") or "")
            line = "Connected"
            count = len(self._guilds_cache)
            if count:
                line += f" · in {count} server{'' if count == 1 else 's'}"
            return "ok", "Bot connected", "Signed in as", bot_name or "Discord bot", line
        if self._connect_in_progress or low in ("connecting", "reconnecting"):
            text = "Reconnecting" if low == "reconnecting" else "Connecting"
            return "busy", text, "Discord bot", "Not connected", f"{text}…"
        if low == "auth failed":
            return (
                "error",
                "Auth failed",
                "Discord bot",
                "Not connected",
                "Discord rejected the token. Reset it in the Developer Portal, then connect.",
            )
        if low == "disconnected" or low == "unknown":
            line = (
                "Press Connect to start the bot"
                if self._token_saved
                else "Paste the bot token, then connect"
            )
            return "off", "Not connected", "Discord bot", "Not connected", line
        return "error", raw, "Discord bot", "Not connected", f"{raw} · press Connect to try again"

    def _setup_steps(self, connected: bool) -> List[tuple]:
        channels = [e for e in (getattr(self.buffer, "go_live_channels", None) or []) if isinstance(e, dict)]
        return [
            ("Create a bot at <b>discord.com/developers</b> and copy its token.", self._token_saved),
            ("Paste the token and click <b>Connect</b>.", connected),
            ("Use the invite link to add the bot to your server.", bool(self._invite_url)),
            ("Pick the channels to announce in.", bool(channels)),
        ]

    def _sync_connect_buttons(self, connected: bool) -> None:
        primary = self._connect_button
        secondary = self._reconnect_button
        if primary is None or secondary is None:
            return
        primary.set_visibility(not connected)
        secondary.set_visibility(connected)
        if self._connect_in_progress:
            for button in (primary, secondary):
                button.disable()
                button.set_text("Connecting…")
        else:
            primary.enable()
            secondary.enable()
            primary.set_text("Connect")
            secondary.set_text("Reconnect")

    def _refresh_status(self) -> None:
        try:
            status = discord_service.get_discord_status()
            connected = bool(status.get("is_connected"))
            was_connected = self._connected
            self._connected = connected

            invite = status.get("invite_url") or discord_service.get_invite_url() or ""
            if invite != self._invite_url:
                self._invite_url = invite
                if self._invite_row is not None:
                    self._invite_row.set(invite)

            kind, pill, kicker, name, line = self._status_view(status)
            if self._page is not None:
                self._page.set_status([("error" if kind == "off" else kind, pill)])
            if self._card is not None:
                self._card.kicker = kicker
                self._card.set(name, kind, line, initial=initial_of(name) if connected else None)
            if self._fold is not None:
                self._fold.set_saved(self._token_saved)
            disconnect = self.ui_elements.get("disconnect_button")
            if disconnect is not None:
                disconnect.set_visibility(kind != "off")
            self._sync_connect_buttons(connected)
            if self._add_row is not None:
                self._add_row.set_visibility(connected)
            if self._picker_hint is not None:
                self._picker_hint.set_visibility(not connected)
            self._refresh_steps()
            if connected and was_connected is False and not self._connect_in_progress:
                # Connected in the background (auto-connect): fill the pickers once.
                self._reload_guilds()
        except Exception:
            logger.debug("Discord status refresh failed", exc_info=True)

    def _reload_guilds(self, report: bool = False) -> None:
        """Refresh the server list; ``report`` says so when it fails (button or connect)."""
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
        except Exception as e:
            logger.debug("Failed to reload Discord guilds", exc_info=True)
            if report:
                notify(
                    "The server and channel lists are empty for now.",
                    type="warning",
                    source="discord",
                    title="Couldn't list your Discord servers",
                    hint="Make sure the bot is invited to your server (Invite the bot), "
                    "then press Reload servers and channels.",
                    details=f"{type(e).__name__}: {e}",
                )

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
        channels = [
            e for e in (getattr(self.buffer, "go_live_channels", None) or []) if isinstance(e, dict)
        ]
        with self._go_live_chip_container:
            if not channels:
                list_empty("No channels yet. Pick a server and channel below.")
        for entry in channels:
            self._create_channel_chip(entry)
        self._refresh_steps()

    def _create_channel_chip(self, entry: Dict[str, Any]) -> None:
        if not self._go_live_chip_container:
            return
        guild_name = entry.get("guild_name") or entry.get("guild_id") or "?"
        channel_name = entry.get("channel_name") or entry.get("channel_id") or "?"
        key = self._channel_key(entry)
        with self._go_live_chip_container:
            list_item(
                "tag",
                str(channel_name),
                str(guild_name),
                on_remove=lambda k=key: self._remove_go_live_channel(k),
                remove_tip="Remove channel",
            )

    def _refresh_steps(self) -> None:
        if self._steps is None:
            return
        content = steps_html(self._setup_steps(bool(self._connected)))
        if content != self._steps_content:
            self._steps_content = content
            self._steps.set_content(content)

    def _add_go_live_channel(self) -> None:
        if self.buffer is None:
            return
        if self._guild_select is None or self._channel_select is None:
            return
        guild_id = str(self._guild_select.value or "").strip()
        channel_id = str(self._channel_select.value or "").strip()
        if not guild_id or not channel_id:
            notify("", type="warning", source="discord", title="Pick a server and channel first")
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
            notify(
                "",
                type="warning",
                source="discord",
                title=f"#{channel_name or channel_id} is already in the go-live list",
            )
            return
        existing.append(entry)
        self.buffer.go_live_channels = existing
        self._sync_go_live_dirty()
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
        self._sync_go_live_dirty()
        self._rebuild_go_live_chips()

    def _insert_token(self, token: str) -> None:
        """Append a placeholder to the end of the message (the change handler marks it dirty)."""
        element = self.ui_elements.get("go_live_message")
        if element is None:
            return
        current = "" if element.value is None else str(element.value)
        sep = "" if not current or current[-1].isspace() else " "
        element.value = f"{current}{sep}{token}"

    def _handle_connect(self) -> None:
        if self._connect_in_progress:
            return
        if not self.buffer:
            return

        token = (self._creds.get("bot_token") or "").strip()
        if not token:
            notify(
                "",
                type="warning",
                source="discord",
                title="Enter a Discord bot token first",
                hint="Find it in the Discord Developer Portal under Bot → Reset Token.",
            )
            if self._fold is not None:
                self._fold.open()
            return

        # Persist from _creds only (never re-read password UI .value) — hard-fail.
        # A failed save was already reported; don't connect with an unsaved token.
        if not self._save_settings_only():
            return

        self._connect_in_progress = True
        self._refresh_status()

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
            if result.get("ok"):
                notify("", type="success", source="discord", title="Discord bot connected")
                self._creds["bot_token"] = token
                self._load_from_state()
                if not (self._creds.get("bot_token") or "").strip():
                    self._creds["bot_token"] = token
                if "bot_token" in self.ui_elements:
                    self.ui_elements["bot_token"].value = self._creds.get(
                        "bot_token", ""
                    )
                self._reload_guilds(report=True)
            else:
                notify(
                    "",
                    type="error",
                    source="discord",
                    title="Couldn't connect the Discord bot",
                    hint="Check the token is current and the bot has been invited to your server, then try again.",
                    details=result.get("error") or "Discord didn't accept the connection.",
                )
            self._refresh_status()

        layout_schedule(0.5, poll, once=True)

    def _handle_disconnect(self) -> None:
        if getattr(self, "_disconnect_in_progress", False):
            return
        self._disconnect_in_progress = True
        if "disconnect_button" in self.ui_elements:
            try:
                self.ui_elements["disconnect_button"].disable()
            except Exception:
                pass

        result: Dict[str, Any] = {"done": False, "ok": False}

        def worker():
            try:
                discord_service.disconnect()
                result["ok"] = True
            except Exception as e:
                logger.error("Discord disconnect failed: %s", e, exc_info=True)
                result["error"] = str(e)
            finally:
                result["done"] = True

        threading.Thread(
            target=worker, daemon=True, name="DiscordDisconnect"
        ).start()

        def poll():
            if not result["done"]:
                layout_schedule(0.5, poll, once=True)
                return
            self._disconnect_in_progress = False
            if "disconnect_button" in self.ui_elements:
                try:
                    self.ui_elements["disconnect_button"].enable()
                except Exception:
                    pass
            if result.get("ok"):
                notify("", type="info", source="discord", title="Discord bot disconnected")
                self._load_from_state()
                self._reload_guilds()
            else:
                notify(
                    "It will stop when Mycelian closes.",
                    type="warning",
                    source="discord",
                    title="Couldn't disconnect the Discord bot cleanly",
                    hint="Try Disconnect again.",
                    details=result.get("error") or None,
                )
            self._refresh_status()

        layout_schedule(0.5, poll, once=True)

    def _copy_invite_url(self) -> None:
        url = (self._invite_url or "").strip()
        if not url:
            url = discord_service.get_invite_url()
        if not url:
            self._notify_no_invite()
            return
        try:
            ui.clipboard.write(url)
            notify("", type="success", source="discord", title="Invite link copied")
        except Exception:
            notify(
                url,
                type="warning",
                source="discord",
                title="Couldn't copy the invite link automatically",
                hint="Use Copy link below, or select the link and copy it.",
                actions=[{"kind": "copy", "text": url, "label": "Copy link"}],
            )

    @staticmethod
    def _notify_no_invite() -> None:
        notify(
            "",
            type="warning",
            source="discord",
            title="Connect the bot first",
            hint="The invite link appears once the bot is connected.",
        )

    def _open_invite(self) -> None:
        url = discord_service.get_invite_url()
        if not url:
            url = (self._invite_url or "").strip()
        if not url:
            self._notify_no_invite()
            return
        ui.navigate.to(url, new_tab=True)

    def build(self, parent_container) -> None:
        self._load_from_state()
        self._page = page = IntegrationPage(
            parent_container,
            "discord",
            "Discord",
            "Go-live announcements from your own bot",
            on_save=self.save,
            on_discard=self.discard,
            is_dirty=self.has_unsaved_changes,
            help_topic="guide_discord",
            help_tooltip="Discord setup help",
        )
        with page.column():
            with section("smart_toy", "Bot"):
                self._card = AccountCard("discord", "Discord bot")
                with self._card.actions:
                    self.ui_elements["disconnect_button"] = (
                        ui.button(icon="logout", on_click=self._handle_disconnect)
                        .props("flat round dense")
                        .classes("itg-ibtn")
                        .tooltip("Disconnect")
                    )
                    self._reconnect_button = (
                        outline_button("Reconnect", self._handle_connect, icon="login")
                        .props("dense")
                        .classes("px-3")
                    )
                    self._connect_button = (
                        primary_button("Connect", self._handle_connect, icon="login")
                        .props("dense")
                        .classes("px-3")
                    )
                with self._card.extra().style("padding-top:0"):

                    def invite_button() -> None:
                        ui.button(icon="open_in_new", on_click=self._open_invite).props(
                            "flat round dense size=sm"
                        ).classes("itg-ibtn").tooltip("Invite the bot to a server")

                    self._invite_row = _InviteRow(
                        self._copy_invite_url,
                        "Invite link",
                        empty="Connect to generate an invite link",
                        extra=invite_button,
                    )
                    self._invite_row.button.tooltip("Copy invite link")
                self._fold = self._card.credentials("Bot token", opened=not self._token_saved)
                with self._fold.body:
                    with fields(one=True):
                        self.ui_elements["bot_token"] = input_field(
                            "Bot token",
                            value=self._creds.get("bot_token", ""),
                            password=True,
                            placeholder="Bot token",
                            tip="Discord bot token (kept encrypted in the app database)",
                            on_change=lambda e: self._set_cred(
                                "bot_token", self._str_from_value_event(e)
                            ),
                        )
            with section("menu_book", "Setup"):
                items = self._setup_steps(False)
                self._steps_content = steps_html(items)
                self._steps = steps(items)
        with page.column():
            with section("campaign", "Go-live announcement"):
                self.ui_elements["go_live_enabled"] = switch_row(
                    "Announce when you go live",
                    "Once per stream when Twitch or YouTube goes live. Multi-stream safe, never sent twice.",
                    bool(getattr(self.buffer, "go_live_enabled", False)),
                    lambda v: self._set("go_live_enabled", bool(v)),
                )
                with ui.element("div").style("margin-top:8px"):
                    field_label("Message", "Placeholders: {platform}, {title}, {url}")
                    self.ui_elements["go_live_message"] = (
                        ui.textarea(
                            value=getattr(self.buffer, "go_live_message", "")
                            or _DEFAULT_GO_LIVE_MESSAGE,
                            on_change=lambda e: self._set(
                                "go_live_message",
                                "" if e.value is None else str(e.value),
                            ),
                        )
                        .classes("w-full")
                        .props("autogrow outlined dense")
                    )
                    tokens(["{platform}", "{title}", "{url}"], self._insert_token)
                field_label("Channels").style("margin-top:14px")
                self._go_live_chip_container = list_box()
                self._add_row = ui.element("div").classes("itg-add")
                with self._add_row:
                    self._guild_select = (
                        ui.select(
                            options={},
                            with_input=True,
                            on_change=lambda e: self._on_guild_changed(e.value),
                        )
                        .props('outlined dense options-dense placeholder="Server"')
                        .tooltip("Server")
                    )
                    self._channel_select = (
                        ui.select(options={}, with_input=True)
                        .props('outlined dense options-dense placeholder="Channel"')
                        .tooltip("Channel")
                    )
                    outline_button("Add", self._add_go_live_channel, icon="add").props(
                        "dense"
                    ).classes("px-3")
                    ui.button(
                        icon="refresh", on_click=lambda: self._reload_guilds(report=True)
                    ).props("flat round dense").classes("itg-ibtn").tooltip(
                        "Reload servers and channels"
                    )
                self._picker_hint = hint("Connect the bot to pick servers and channels.")
                self._rebuild_go_live_chips()

        self._status_timer = layout_schedule(5.0, self._refresh_status, active=True)
        self._refresh_status()
        self._reload_guilds()

    def save(self, silent: bool = False) -> None:
        if not self.buffer:
            return
        if self._save_settings_only():
            self._saved_go_live_channels = self._go_live_signature()
            self.dirty = False
            if not silent:
                notify("", type="success", source="discord", title="Discord settings saved")
            self._refresh_status()
        # A failed save was already reported by _save_settings_only / save_changes.

    def discard(self) -> None:
        self._load_from_state()
        if self.buffer is None:
            return
        if "bot_token" in self.ui_elements:
            self.ui_elements["bot_token"].value = self._creds.get("bot_token", "")
        if "go_live_enabled" in self.ui_elements:
            self.ui_elements["go_live_enabled"].value = bool(
                self.buffer.go_live_enabled
            )
        if "go_live_message" in self.ui_elements:
            self.ui_elements["go_live_message"].value = (
                self.buffer.go_live_message
                or _DEFAULT_GO_LIVE_MESSAGE
            )
        self._rebuild_go_live_chips()
        self.mark_clean()

    def mark_clean(self) -> None:
        """Treat the current go-live channel list as the saved baseline."""
        self._saved_go_live_channels = self._go_live_signature()
        self.dirty = False
        self._refresh_status()
