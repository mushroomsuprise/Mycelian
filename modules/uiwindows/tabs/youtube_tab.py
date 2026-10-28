# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import logging
from typing import Dict, Any, List, Optional

from nicegui import run, ui

from ...ui_buttons import outline_button, primary_button
from ...notification_engine import notify, notify_progress
from ...ui_timer import layout_schedule
from ...api_credentials_manager import api_credentials_manager

from ... import dataobjects
from ...dataobjects import state_manager, YouTubeData
from ...youtube import YOUTUBE_OAUTH_REDIRECT_URI
from .integration_ui import (
    AccountCard,
    CopyRow,
    IntegrationPage,
    esc,
    field,
    fields,
    hint,
    icon_html,
    initial_of,
    list_box,
    list_empty,
    list_item,
    section,
    set_connect_style,
    switch_row,
)

logger = logging.getLogger(__name__)

# YouTubeData fields this tab edits. Save writes only these (plus the OAuth client
# credentials); tokens, resolved channels and status come from the live state because
# the YouTube client may have updated them since the tab loaded its buffer.
_TAB_FIELDS = ("api_key", "channel_urls", "playlist_filter", "live_chat_enabled", "alerts_enabled")


def _restart_live_chat() -> None:
    """Restart YouTube live chat with the saved settings; say so if that fails."""
    try:
        from ... import youtube

        youtube.restart_youtube_live_chat_if_needed()
    except Exception as e:
        logger.error("YouTube live chat restart failed: %s", e, exc_info=True)
        notify(
            "",
            type="warning",
            source="youtube",
            title="YouTube settings saved, but live chat couldn't restart",
            hint="Press Connect to start it again.",
            details=f"{type(e).__name__}: {e}",
        )


class YouTubeTab:
    name = "YouTube"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.YouTubeData] = None
        self.ui_elements: Dict[str, Any] = {}
        self._creds: Dict[str, str] = {}
        self._playlist_list: Any = None
        self._playlist_head: Any = None
        self._playlist_input: Optional[ui.input] = None
        self._channel_list: Any = None
        self._channel_head: Any = None
        self._channel_input: Optional[ui.input] = None
        self._channel_raw_box: Any = None
        self._channel_raw_link: Any = None
        self._channel_ui_sync: bool = False
        self._status_timer: Optional[Any] = None
        self._oauth_in_progress: bool = False
        self._active_timers: List[Any] = []
        self._oauth_connected: bool = False
        self._page: Optional[IntegrationPage] = None
        self._live_card: Optional[AccountCard] = None
        self._lookup_card: Optional[AccountCard] = None
        self._oauth_fold: Any = None
        self._key_fold: Any = None
        self._disconnect_button: Any = None

    @staticmethod
    def _str_from_value_event(e: Any) -> str:
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

    def _status(self) -> tuple:
        """Live-chat and channel-lookup states from the YouTube module and saved data."""
        youtube_data = state_manager.get_youtube_data()

        from ... import youtube

        status_info = youtube.get_youtube_status()

        # Channel lookups (API key): the most current status from either source
        current_connection_status = (
            youtube_data.connection_status if youtube_data else "Unknown"
        )
        status_text = current_connection_status or status_info.get("status", "Unknown")
        is_connected = status_info.get("is_connected", False)
        has_key = bool((getattr(youtube_data, "api_key", "") or "").strip()) if youtube_data else False
        latest_video = status_info.get("latest_video", "N/A") or "N/A"
        latest_channel = (youtube_data.latest_video_channel if youtube_data else "") or ""
        saved_urls = [u for u in str((youtube_data.channel_urls if youtube_data else "") or "").split("|") if u.strip()]
        count = len(saved_urls) or (len(youtube_data.channels) if youtube_data and youtube_data.channels else 0)
        lookups_name = f"{count} channel{'' if count == 1 else 's'} watched" if count else "No channels configured"
        found = latest_video not in ("N/A", "No video found")
        lookups_meta = [("Latest video", latest_video, latest_channel if found else "", "")]
        if is_connected or status_text == "Connected":
            lookups = (
                "ok",
                "Channel lookups working",
                lookups_name,
                "Working · latest video found" if found else "Working",
                lookups_meta,
            )
        elif not has_key:
            lookups = ("off", "Channel lookups: no API key", lookups_name, "Add an API key to look up channels", [])
        else:
            lookups = ("error", f"Channel lookups: {status_text}", lookups_name, str(status_text), lookups_meta)

        # Live chat / OAuth
        live_status = status_info.get("live_chat_status") or (
            youtube_data.live_chat_status if youtube_data else "Not authorized"
        )
        oauth_title = status_info.get("oauth_channel_title") or (
            (youtube_data.oauth_channel_title if youtube_data else "") or ""
        )
        oauth_connected = bool(status_info.get("oauth_connected"))
        live_meta = [("Live status", str(live_status), "", "is-ok" if live_status == "Live" else "")]
        live_name = oauth_title or ("Google account" if oauth_connected else "Not signed in")
        if live_status == "Live":
            live = ("ok", "Live chat: Live", live_name, "Live · reading chat", live_meta)
        elif live_status == "Offline":
            live = ("ok", "Live chat ready", live_name, "Offline · signed in, reads chat when you go live", live_meta)
        elif oauth_connected:
            live = ("warn", f"Live chat: {live_status}", live_name, str(live_status), live_meta)
        elif not self._oauth_creds_saved(youtube_data):
            live = ("off", "Live chat: not set up", live_name, "Add OAuth credentials, then Connect", [])
        else:
            live = ("error", f"Live chat: {live_status}", live_name, str(live_status), live_meta)
        return lookups, live, oauth_connected, has_key, oauth_title

    @staticmethod
    def _oauth_creds_saved(youtube_data: Any) -> bool:
        creds = api_credentials_manager.get_youtube_credentials()
        client_id = (creds.get("client_id") or "").strip() or (
            (getattr(youtube_data, "oauth_client_id", "") or "").strip() if youtube_data else ""
        )
        client_secret = (creds.get("client_secret") or "").strip() or (
            (getattr(youtube_data, "oauth_client_secret", "") or "").strip() if youtube_data else ""
        )
        return bool(client_id and client_secret)

    def _refresh_status(self) -> None:
        """Refresh the header pills, both connection cards and the credential tags."""
        try:
            lookups, live, oauth_connected, has_key, oauth_title = self._status()
            self._oauth_connected = oauth_connected
            if self._page is not None:
                self._page.set_status([(lookups[0], lookups[1]), (live[0], live[1])])
            if self._live_card is not None:
                self._live_card.set(live[2], live[0], live[3], initial=initial_of(oauth_title) if oauth_title else None)
                self._live_card.set_meta(live[4])
            if self._lookup_card is not None:
                self._lookup_card.set(lookups[2], lookups[0], lookups[3])
                self._lookup_card.set_meta(lookups[4])
            if self._oauth_fold is not None:
                self._oauth_fold.set_saved(self._oauth_creds_saved(state_manager.get_youtube_data()))
            if self._key_fold is not None:
                self._key_fold.set_saved(has_key)
            if self._disconnect_button is not None:
                self._disconnect_button.set_visibility(oauth_connected)
            btn = self.ui_elements.get("connect_button")
            if btn is not None and not self._oauth_in_progress:
                set_connect_style(btn, oauth_connected)

        except Exception as e:
            logger.error(f"Error refreshing YouTube status: {str(e)}", exc_info=True)
            if self._page is not None:
                self._page.set_status([("error", "Status error")])
            if self._lookup_card is not None:
                self._lookup_card.set("Channel lookups", "error", "Status error")

    async def _test_connection(self) -> None:
        """Handle the Test Connection button click for YouTube"""
        try:
            import logging

            logger = logging.getLogger(__name__)
            logger.info("User clicked Test Connection button for YouTube")

            # Prevent duplicate test attempts
            if getattr(self, "_test_in_progress", False):
                logger.warning(
                    "YouTube test already in progress, ignoring duplicate request"
                )
                return  # the progress notice is already showing

            # Mark test as in progress
            self._test_in_progress = True

            # Update button state to show it's working
            if "test_button" in self.ui_elements:
                self.ui_elements["test_button"].set_text("Testing...")
                self.ui_elements["test_button"].disable()

            def test_thread():
                from ... import youtube

                return youtube.test_connection()

            work = notify_progress("Testing the YouTube connection", source="youtube")
            try:
                success = await run.io_bound(test_thread)
                if success:
                    work.done("YouTube connection works")
                    logger.info("YouTube connection test completed successfully")
                else:
                    work.fail(
                        "Couldn't reach YouTube",
                        hint="Check the API key and channel URLs. The daily API quota may also be used up.",
                    )
                    logger.warning("YouTube connection test failed")
                self._refresh_status()
            except Exception as e:
                logger.error(
                    f"Error in YouTube test: {str(e)}", exc_info=True
                )
                work.fail(
                    "Couldn't test the YouTube connection",
                    hint="Check the API key and channel URLs, then try again.",
                    details=f"{type(e).__name__}: {e}",
                )
            finally:
                self._cleanup_test()

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(
                f"Error handling YouTube test connection: {str(e)}", exc_info=True
            )
            notify(
                "",
                type="error",
                source="youtube",
                title="Couldn't run the YouTube test",
                hint="Try again. If it keeps happening, copy the details and report it.",
                details=f"{type(e).__name__}: {e}",
            )
            self._cleanup_test()

    def _cleanup_test(self) -> None:
        """Clean up after YouTube connection test"""
        try:
            # Mark test as no longer in progress
            self._test_in_progress = False

            # Reset button state
            if "test_button" in self.ui_elements:
                self.ui_elements["test_button"].set_text("Test")
                self.ui_elements["test_button"].enable()
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Error cleaning up YouTube test: {str(e)}")

    def on_exit(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = False
        if self._page is not None:
            self._page.set_active(False)

    def _persist_credentials_to_state(self, flush: bool = True) -> bool:
        """Write UI OAuth credentials to YouTubeData and api_credentials.json.

        ``flush`` saves and restarts live chat now (save() does both itself).
        Returns False when the save failed (already reported by save_changes).
        """
        client_id = self._creds.get("client_id", "")
        client_secret = self._creds.get("client_secret", "")
        if self.buffer:
            self.buffer.oauth_client_id = client_id
            self.buffer.oauth_client_secret = client_secret
        state_manager.update_youtube_field("oauth_client_id", client_id)
        state_manager.update_youtube_field("oauth_client_secret", client_secret)
        if self.buffer is not None:
            state_manager.update_youtube_field(
                "live_chat_enabled", bool(self.buffer.live_chat_enabled)
            )
        api_credentials_manager.update_youtube_credentials(
            client_id=client_id,
            client_secret=client_secret,
        )
        if not flush:
            return True
        if not state_manager.save_changes():
            return False
        _restart_live_chat()
        return True

    def _handle_oauth_connection(self) -> None:
        """Connect / reconnect Google OAuth for live chat (Spotify-style)."""
        try:
            if self._oauth_in_progress:
                return  # the progress notice is already showing

            # Check before saving so blank credentials don't overwrite saved ones.
            client_id = (self._creds.get("client_id") or "").strip()
            client_secret = (self._creds.get("client_secret") or "").strip()
            if not client_id or not client_secret:
                notify(
                    "",
                    type="warning",
                    source="youtube",
                    title="Enter your Google OAuth Client ID and Client Secret first",
                    hint="Create them in the Google Cloud console (APIs & Services → Credentials).",
                )
                return
            if not self._persist_credentials_to_state():
                return  # save_changes() already said why

            self._oauth_in_progress = True
            if "connect_button" in self.ui_elements:
                self.ui_elements["connect_button"].set_text("Connecting...")
                self.ui_elements["connect_button"].disable()

            work = notify_progress(
                "Connecting YouTube live chat",
                source="youtube",
                message="Choose your channel's Google account in the browser page that opens.",
            )
            self._oauth_work = work

            oauth_result = {
                "status": "connecting",
                "success": None,
                "error": None,
            }

            def oauth_thread():
                try:
                    from ... import youtube

                    success = youtube.trigger_youtube_oauth_flow()
                    oauth_result["status"] = "complete"
                    oauth_result["success"] = success
                except Exception as e:
                    logger.error(
                        "Error in YouTube OAuth thread: %s", e, exc_info=True
                    )
                    oauth_result["status"] = "error"
                    oauth_result["error"] = str(e)

            import threading

            oauth_worker = threading.Thread(target=oauth_thread, daemon=True)
            oauth_worker.start()

            def check_oauth_result():
                if oauth_result["status"] == "connecting":
                    return
                if oauth_result["status"] == "complete":
                    if oauth_result["success"]:
                        work.done("YouTube live chat connected")
                        self._load_from_state()
                        if "oauth_client_id" in self.ui_elements:
                            self.ui_elements["oauth_client_id"].value = (
                                self._creds.get("client_id", "")
                            )
                        if "oauth_client_secret" in self.ui_elements:
                            self.ui_elements["oauth_client_secret"].value = (
                                self._creds.get("client_secret", "")
                            )
                    else:
                        work.fail(
                            "YouTube sign-in didn't finish",
                            hint="Press Connect again and choose the channel's Google account.",
                        )
                    self._cleanup_oauth()
                    self._refresh_status()
                elif oauth_result["status"] == "error":
                    work.fail(
                        "YouTube sign-in didn't finish",
                        hint="Check the OAuth Client ID and Secret, then press Connect again.",
                        details=str(oauth_result["error"] or ""),
                    )
                    self._cleanup_oauth()
                    self._refresh_status()

            timer = layout_schedule(0.25, check_oauth_result)
            self._active_timers.append(timer)
        except Exception as e:
            logger.error("Error starting YouTube OAuth: %s", e, exc_info=True)
            kwargs = {
                "hint": "Try again. If it keeps happening, copy the details and report it.",
                "details": f"{type(e).__name__}: {e}",
            }
            work = getattr(self, "_oauth_work", None)
            if work is not None and not work.finished:
                work.fail("Couldn't start YouTube sign-in", **kwargs)
            else:
                notify("", type="error", source="youtube", title="Couldn't start YouTube sign-in", **kwargs)
            self._cleanup_oauth()

    def _cleanup_oauth(self) -> None:
        self._oauth_in_progress = False
        if "connect_button" in self.ui_elements:
            self.ui_elements["connect_button"].enable()
            set_connect_style(self.ui_elements["connect_button"], self._oauth_connected)
        for t in self._active_timers:
            try:
                t.active = False
            except Exception:
                pass
        self._active_timers.clear()

    def _handle_oauth_disconnect(self) -> None:
        try:
            from ... import youtube

            youtube.disconnect_youtube_oauth()
            self._load_from_state()
            notify("", type="info", source="youtube", title="YouTube live chat disconnected")
            self._refresh_status()
        except Exception as e:
            logger.error("Error disconnecting YouTube OAuth: %s", e, exc_info=True)
            notify(
                "",
                type="error",
                source="youtube",
                title="Couldn't disconnect YouTube live chat",
                hint="Try again.",
                details=f"{type(e).__name__}: {e}",
            )

    def _on_playlist_input_enter(self, _event=None) -> None:
        """Handle Enter (or Add) in the playlist filter input."""
        if not self._playlist_input:
            return
        name = (self._playlist_input.value or "").strip()
        if not name:
            return
        self._playlist_input.value = ""
        self._add_playlist_filter(name)
        self._sync_playlist_dirty()

    def _add_playlist_filter(self, name: str) -> None:
        """Add a playlist name to the exclusion list."""
        if not self.buffer:
            return
        if name in self.buffer.playlist_filter:
            notify("", type="warning", source="youtube", title=f"“{name}” is already in the filter list")
            return
        self.buffer.playlist_filter.append(name)
        self._sync_playlist_dirty()
        self._rebuild_playlist_list()

    def _remove_playlist_filter(self, name: str) -> None:
        """Remove a playlist name from the exclusion list."""
        if not self.buffer:
            return
        if name in self.buffer.playlist_filter:
            self.buffer.playlist_filter.remove(name)
        self._sync_playlist_dirty()
        self._rebuild_playlist_list()

    def _channel_url_list(self) -> List[str]:
        raw = "" if self.buffer is None else (self.buffer.channel_urls or "")
        return [part.strip() for part in str(raw).split("|") if part.strip()]

    def _write_channel_urls(self, urls: List[str]) -> None:
        joined = "|".join(urls)
        self._set("channel_urls", joined)
        raw_field = self.ui_elements.get("channel_urls")
        if raw_field is not None and (raw_field.value or "") != joined:
            self._channel_ui_sync = True
            try:
                raw_field.value = joined
            finally:
                self._channel_ui_sync = False
        self._rebuild_channel_list()

    def _on_channel_raw_change(self, event) -> None:
        if self._channel_ui_sync:
            return
        self._set("channel_urls", "" if event.value is None else str(event.value))
        self._rebuild_channel_list()

    def _add_channel_url(self, url: str) -> None:
        cleaned = (url or "").strip()
        if not cleaned:
            return
        urls = self._channel_url_list()
        if cleaned in urls:
            notify("", type="warning", source="youtube", title="That channel is already listed")
            return
        urls.append(cleaned)
        self._write_channel_urls(urls)
        if self._channel_input is not None:
            self._channel_input.value = ""

    def _remove_channel_url(self, url: str) -> None:
        urls = [item for item in self._channel_url_list() if item != url]
        self._write_channel_urls(urls)

    def _on_channel_input_enter(self, _event=None) -> None:
        if self._channel_input is None:
            return
        self._add_channel_url(str(self._channel_input.value or ""))

    @staticmethod
    def _list_heading(icon: str, title: str, count: int) -> str:
        return f'{icon_html(icon)}{esc(title)}<span class="itg-sec-right">{count}</span>'

    def _rebuild_channel_list(self) -> None:
        if self._channel_list is None:
            return
        urls = self._channel_url_list()
        if self._channel_head is not None:
            self._channel_head.set_content(self._list_heading("subscriptions", "Channels to watch", len(urls)))
        self._channel_list.clear()
        with self._channel_list:
            if not urls:
                list_empty("No channels yet. Add a channel URL below.")
            for url in urls:
                list_item(
                    "smart_display",
                    url,
                    on_remove=lambda item=url: self._remove_channel_url(item),
                    remove_tip="Remove channel",
                )

    def _rebuild_playlist_list(self) -> None:
        """Redraw the ignored playlists from the buffer."""
        if self._playlist_list is None:
            return
        current_list: List[str] = self.buffer.playlist_filter if self.buffer else []
        if self._playlist_head is not None:
            self._playlist_head.set_content(self._list_heading("playlist_remove", "Ignore playlists", len(current_list)))
        self._playlist_list.clear()
        with self._playlist_list:
            if not current_list:
                list_empty("No playlists ignored.")
            for name in current_list:
                list_item(
                    "playlist_play",
                    name,
                    on_remove=lambda n=name: self._remove_playlist_filter(n),
                    remove_tip="Stop ignoring this playlist",
                )

    def _toggle_raw_channels(self) -> None:
        box = self._channel_raw_box
        if box is None:
            return
        box.set_visibility(not box.visible)
        if self._channel_raw_link is not None:
            self._channel_raw_link.set_text("Hide the list field" if box.visible else "Paste several at once")

    def build(self, parent_container) -> None:
        self._load_from_state()
        self._page = page = IntegrationPage(
            parent_container,
            "youtube",
            "YouTube",
            "Latest videos, live chat, memberships and Super Chats",
            on_save=self.save,
            on_discard=self.discard,
            is_dirty=self.has_unsaved_changes,
            help_topic="guide_youtube",
            help_tooltip="YouTube setup guide",
        )
        with page.column():
            with section("account_circle", "Connections"):
                self._live_card = AccountCard("youtube", "Live chat & alerts (Google sign-in)")
                with self._live_card.actions:
                    self._disconnect_button = (
                        ui.button(icon="logout", on_click=self._handle_oauth_disconnect)
                        .props("flat round dense")
                        .classes("itg-ibtn")
                        .tooltip("Disconnect")
                    )
                    self._disconnect_button.set_visibility(False)
                    self.ui_elements["connect_button"] = primary_button(
                        "Connect",
                        self._handle_oauth_connection,
                        icon="login",
                    ).props("dense").classes("px-3")
                creds_saved = bool(
                    (self._creds.get("client_id") or "").strip() and (self._creds.get("client_secret") or "").strip()
                )
                self._oauth_fold = self._live_card.credentials("OAuth client ID & secret", opened=not creds_saved)
                with self._oauth_fold.body:
                    with fields():
                        self.ui_elements["oauth_client_id"] = field(
                            "OAuth Client ID",
                            value=self._creds.get("client_id", ""),
                            password=True,
                            placeholder="xxxx.apps.googleusercontent.com",
                            tip=(
                                "Google OAuth Client ID (Web application). "
                                "Stored in api_credentials.json like Spotify."
                            ),
                            on_change=lambda e: self._set_cred("client_id", self._str_from_value_event(e)),
                        )
                        self.ui_elements["oauth_client_secret"] = field(
                            "OAuth Client Secret",
                            value=self._creds.get("client_secret", ""),
                            password=True,
                            placeholder="GOCSPX-...",
                            tip="Google OAuth Client Secret",
                            on_change=lambda e: self._set_cred("client_secret", self._str_from_value_event(e)),
                        )
                    CopyRow("Redirect URI", YOUTUBE_OAUTH_REDIRECT_URI, copied="Redirect URI copied")
                    hint(
                        "Create an OAuth client (Web application) and add the redirect URI above. "
                        "Connect uses these values.",
                        link_label="Google Cloud console credentials",
                        url="https://console.cloud.google.com/apis/credentials",
                    )
                self._oauth_fold.set_saved(creds_saved)

                self._lookup_card = AccountCard("youtube", "Channel lookups (API key)")
                with self._lookup_card.actions:
                    self.ui_elements["refresh_button"] = (
                        ui.button(icon="refresh", on_click=self._refresh_status)
                        .props("flat round dense")
                        .classes("itg-ibtn")
                        .tooltip("Check now")
                    )
                    self.ui_elements["test_button"] = outline_button(
                        "Test",
                        self._test_connection,
                        icon="wifi_tethering",
                    ).props("dense").classes("px-3")
                key_saved = bool((getattr(self.buffer, "api_key", "") or "").strip())
                self._key_fold = self._lookup_card.credentials("API key", opened=not key_saved)
                with self._key_fold.body:
                    with fields(one=True):
                        self.ui_elements["api_key"] = field(
                            "YouTube Data API v3 key",
                            value=getattr(self.buffer, "api_key", ""),
                            password=True,
                            placeholder="YouTube Data API v3 Key",
                            tip="YouTube Data API v3 key for channel and video lookups",
                            on_change=lambda e: self._set("api_key", "" if e.value is None else str(e.value)),
                        )
                self._key_fold.set_saved(key_saved)

            with section("tune", "Options"):
                self.ui_elements["live_chat_enabled"] = switch_row(
                    "Read live chat",
                    "Poll live chat for messages and alerts while you're live",
                    bool(getattr(self.buffer, "live_chat_enabled", True)),
                    lambda v: self._set("live_chat_enabled", v),
                    tooltip=(
                        "When on and authorized, poll live chat for messages and alerts. "
                        "Chat overlay display is controlled separately in the chat template "
                        "(EnableYouTubeChat, default off)."
                    ),
                )
                self.ui_elements["alerts_enabled"] = switch_row(
                    "Process YouTube alerts",
                    "Off: memberships and Super Chats still reach chat and Connectors, but skip the alert queue",
                    bool(getattr(self.buffer, "alerts_enabled", True)),
                    lambda v: self._set("alerts_enabled", v),
                    tooltip=(
                        "When off, memberships and Super Chats still reach chat, "
                        "Connectors, and Chatbot — but skip the alert queue, "
                        "instant alerts, and activity feed."
                    ),
                )

        with page.column():
            with ui.element("div").classes("itg-sec"):
                self._channel_head = ui.html("", sanitize=False).classes("itg-sec-head")
                self._channel_list = list_box()
                with ui.element("div").classes("itg-add"):
                    self._channel_input = (
                        ui.input(placeholder="https://youtube.com/@Channel")
                        .props("outlined dense")
                        .tooltip("YouTube channel URL to monitor; press Enter to add")
                    )
                    self._channel_input.on("keydown.enter", self._on_channel_input_enter)
                    outline_button("Add", self._on_channel_input_enter, icon="add").props("dense").classes("px-3")
                with ui.element("div").classes("itg-hint"):
                    ui.html(icon_html("content_paste"), sanitize=False)
                    with ui.element("span"):
                        ui.html("Have a list?&nbsp;", sanitize=False).classes("inline")
                        self._channel_raw_link = ui.label("Paste several at once").classes("itg-link inline")
                        self._channel_raw_link.on("click", lambda _e: self._toggle_raw_channels())
                self._channel_raw_box = ui.element("div").style("margin-top:8px")
                with self._channel_raw_box:
                    with fields(one=True):
                        self.ui_elements["channel_urls"] = field(
                            "Channel URLs, separated by |",
                            value=getattr(self.buffer, "channel_urls", ""),
                            placeholder="https://youtube.com/@Channel|https://...",
                            tip="Pipe-separated YouTube channel URLs to monitor",
                            on_change=self._on_channel_raw_change,
                        )
                self._channel_raw_box.set_visibility(False)
                self._rebuild_channel_list()

            with ui.element("div").classes("itg-sec"):
                self._playlist_head = ui.html("", sanitize=False).classes("itg-sec-head")
                self._playlist_list = list_box()
                with ui.element("div").classes("itg-add"):
                    self._playlist_input = (
                        ui.input(placeholder="Playlist name")
                        .props("outlined dense")
                        .tooltip("Playlist title to exclude from latest-video selection; press Enter to add")
                    )
                    self._playlist_input.on_value_change(lambda e: self._sync_playlist_dirty())
                    self._playlist_input.on("keydown.enter", self._on_playlist_input_enter)
                    outline_button("Add", self._on_playlist_input_enter, icon="add").props("dense").classes("px-3")
                hint("Videos in these playlists are skipped when picking the latest video.")
                self._rebuild_playlist_list()

        self._status_timer = layout_schedule(5.0, self._refresh_status, active=True)
        self._refresh_status()

    def _load_from_state(self) -> None:
        yt = state_manager.get_youtube_data()
        values = {}
        for f in YouTubeData.__dataclass_fields__.values():
            value = getattr(yt, f.name)
            if isinstance(value, (list, dict)):
                value = copy.deepcopy(value)
            values[f.name] = value
        self.buffer = dataobjects.YouTubeData(**values)
        self._saved_playlist_filter = list(self.buffer.playlist_filter or [])
        self._creds = dict(api_credentials_manager.get_youtube_credentials())
        # Prefer api_credentials; fall back to YouTubeData if credentials file empty
        if not (self._creds.get("client_id") or "").strip() and (
            self.buffer.oauth_client_id or ""
        ).strip():
            self._creds["client_id"] = self.buffer.oauth_client_id
        if not (self._creds.get("client_secret") or "").strip() and (
            self.buffer.oauth_client_secret or ""
        ).strip():
            self._creds["client_secret"] = self.buffer.oauth_client_secret
        self.dirty = False

    def _playlist_is_dirty(self) -> bool:
        """True when the playlist list or uncommitted playlist text differs from saved."""
        pending = ""
        if self._playlist_input is not None:
            pending = (self._playlist_input.value or "").strip()
        if pending:
            return True
        if not self.buffer:
            return False
        return list(self.buffer.playlist_filter or []) != list(
            getattr(self, "_saved_playlist_filter", [])
        )

    def _sync_playlist_dirty(self) -> None:
        """Playlist edits are read from the list and input at leave time."""
        return

    def has_unsaved_changes(self) -> bool:
        """API fields use the dirty flag. The playlist filter is a list."""
        return bool(self.dirty) or self._playlist_is_dirty()

    def _set(self, field: str, value) -> None:
        if getattr(self.buffer, field) != value:
            setattr(self.buffer, field, value)
            if not getattr(self, "_suppress_dirty", False):
                self.dirty = True

    def _set_cred(self, field: str, value: str) -> None:
        if self._creds.get(field) != value:
            self._creds[field] = value
            if not getattr(self, "_suppress_dirty", False):
                self.dirty = True

    def save(self) -> None:
        if not self.buffer:
            return
        self._persist_credentials_to_state(flush=False)
        current = state_manager.get_youtube_data()
        yt_dict = {
            f.name: getattr(current, f.name)
            for f in YouTubeData.__dataclass_fields__.values()
        }
        for name in _TAB_FIELDS:
            yt_dict[name] = copy.deepcopy(getattr(self.buffer, name))
        # Ensure oauth fields match persisted credentials
        yt_dict["oauth_client_id"] = self._creds.get("client_id", "")
        yt_dict["oauth_client_secret"] = self._creds.get("client_secret", "")
        state_manager.set_youtube_data(yt_dict)
        if state_manager.save_changes():
            notify("", type="success", source="youtube", title="YouTube settings saved")
            _restart_live_chat()
            self._saved_playlist_filter = list(self.buffer.playlist_filter or [])
            self.dirty = False
            self._refresh_status()
        # A failed save_changes() was already reported once (changes stay pending).

    def discard(self) -> None:
        self._suppress_dirty = True
        try:
            self._load_from_state()
            for key, element in self.ui_elements.items():
                if key == "oauth_client_id" and hasattr(element, "value"):
                    element.value = self._creds.get("client_id", "")
                elif key == "oauth_client_secret" and hasattr(element, "value"):
                    element.value = self._creds.get("client_secret", "")
                elif key == "live_chat_enabled" and hasattr(element, "value"):
                    element.value = bool(
                        getattr(self.buffer, "live_chat_enabled", True)
                    )
                elif hasattr(element, "value") and hasattr(self.buffer, key):
                    val = getattr(self.buffer, key)
                    element.value = "" if val is None else val
            if self._playlist_input is not None:
                self._playlist_input.value = ""
            if self._channel_input is not None:
                self._channel_input.value = ""
            self._rebuild_playlist_list()
            self._rebuild_channel_list()
        finally:
            self._suppress_dirty = False
        self.mark_clean()

    def mark_clean(self) -> None:
        """Treat the current buffer and playlist box as the saved baseline."""
        if self._playlist_input is not None:
            self._playlist_input.value = ""
        if self.buffer is not None:
            self._saved_playlist_filter = list(self.buffer.playlist_filter or [])
        self.dirty = False
