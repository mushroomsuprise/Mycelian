# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Discord bot integration for Mycelian.

Uses discord.py with a user-provided bot token. Runs the gateway client on a
dedicated background thread so NiceGUI / gevent remain responsive.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import discord

from .dataobjects import DiscordData, state_manager

logger = logging.getLogger(__name__)

# View Channel + Send Messages + Embed Links + Read Message History
_BOT_INVITE_PERMISSIONS = 1024 | 2048 | 16384 | 65536

_READY_TIMEOUT_SEC = 45.0


def _normalize_channel_targets(raw: Any) -> List[Dict[str, str]]:
    """Normalize channel target list to {guild_id, channel_id, ...} dicts."""
    out: List[Dict[str, str]] = []
    if not raw:
        return out
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        guild_id = str(item.get("guild_id") or "").strip()
        channel_id = str(item.get("channel_id") or "").strip()
        if not guild_id or not channel_id:
            continue
        entry = {
            "guild_id": guild_id,
            "channel_id": channel_id,
        }
        for key in ("guild_name", "channel_name"):
            val = item.get(key)
            if val:
                entry[key] = str(val)
        out.append(entry)
    return out


class GoLiveAnnouncer:
    """Session-scoped multi-platform go-live Discord announcement with dedupe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.twitch_live = False
        self.youtube_live = False
        self.announcement_sent = False

    def reset_session(self) -> None:
        with self._lock:
            self.twitch_live = False
            self.youtube_live = False
            self.announcement_sent = False

    def on_platform_live(
        self,
        platform: str,
        *,
        title: str = "",
        url: str = "",
    ) -> bool:
        """Handle a platform going live. Returns True if a Discord message was sent."""
        platform = (platform or "").strip().lower()
        if platform not in ("twitch", "youtube"):
            return False

        with self._lock:
            if platform == "twitch":
                self.twitch_live = True
            else:
                # YouTube went live: probe Twitch Helix if EventSub state is unknown
                if not self.twitch_live:
                    if _helix_twitch_is_live():
                        self.twitch_live = True
                self.youtube_live = True

            try:
                data = state_manager.get_discord_data()
            except Exception:
                logger.debug("go-live: could not load DiscordData", exc_info=True)
                return False

            if not getattr(data, "go_live_enabled", False):
                return False
            channels = _normalize_channel_targets(
                getattr(data, "go_live_channels", None)
            )
            if not channels:
                logger.debug("go-live: enabled but no channels configured")
                return False

            if self.announcement_sent:
                logger.info(
                    "Skipping Discord go-live announcement (%s) — already sent this session",
                    platform,
                )
                return False

            template = (
                getattr(data, "go_live_message", "") or "Stream is live on {platform}!"
            ).strip()
            message = _format_go_live_message(
                template, platform=platform, title=title, url=url
            )
            # Claim the slot before send to avoid double-send races
            self.announcement_sent = True

        ok = send_to_channels(message, channels)
        if not ok:
            with self._lock:
                # Allow a later platform event to retry if send failed entirely
                self.announcement_sent = False
            logger.warning("Discord go-live send failed for platform=%s", platform)
        else:
            logger.info("Discord go-live announcement sent for platform=%s", platform)
        return ok

    def on_platform_offline(self, platform: str) -> None:
        platform = (platform or "").strip().lower()
        with self._lock:
            if platform == "twitch":
                self.twitch_live = False
            elif platform == "youtube":
                self.youtube_live = False
            if not self.twitch_live and not self.youtube_live:
                self.announcement_sent = False
                logger.debug("Discord go-live session reset (all platforms offline)")


def _format_go_live_message(
    template: str, *, platform: str, title: str = "", url: str = ""
) -> str:
    platform_label = "Twitch" if platform == "twitch" else "YouTube"
    if not url:
        if platform == "twitch":
            url = _twitch_stream_url()
        elif platform == "youtube":
            url = _youtube_live_url()
    if not title and platform == "youtube":
        try:
            yd = state_manager.get_youtube_data()
            title = getattr(yd, "latest_video_title", "") or ""
        except Exception:
            title = ""
    try:
        return template.format(
            platform=platform_label,
            title=title or "",
            url=url or "",
        )
    except Exception:
        return (
            f"{template} ({platform_label}"
            + (f": {url}" if url else "")
            + ")"
        ).strip()


def _twitch_stream_url() -> str:
    try:
        from . import twitch

        api = twitch.get_twitch_api()
        if api and api.user and getattr(api.user, "login", None):
            return f"https://twitch.tv/{api.user.login}"
    except Exception:
        pass
    try:
        settings = state_manager.get_app_settings()
        name = (getattr(settings, "streamer_name", "") or "").strip()
        if name:
            return f"https://twitch.tv/{name}"
    except Exception:
        pass
    return ""


def _youtube_live_url() -> str:
    try:
        yd = state_manager.get_youtube_data()
        channel_id = (getattr(yd, "oauth_channel_id", "") or "").strip()
        if channel_id:
            return f"https://www.youtube.com/channel/{channel_id}/live"
        title = (getattr(yd, "oauth_channel_title", "") or "").strip()
        if title:
            return "https://www.youtube.com/"
    except Exception:
        pass
    return ""


def _helix_twitch_is_live() -> bool:
    """Best-effort Helix check whether the authenticated Twitch user is live."""
    try:
        from . import twitch

        api = twitch.get_twitch_api()
        if not api or not getattr(api, "is_connected", False) or not api.user_id:
            return False

        # Prefer the Twitch client's own async bridge when available
        async def _fetch():
            url = "https://api.twitch.tv/helix/streams"
            data = await api.generic_api_call(
                url, "GET", params={"user_id": api.user_id}
            )
            items = (data or {}).get("data") or []
            return bool(items)

        if hasattr(api, "run_coroutine") and callable(api.run_coroutine):
            return bool(api.run_coroutine(_fetch(), timeout=8))

        # Fallback: schedule on a short-lived thread with its own loop
        result: Dict[str, Any] = {"live": False}

        def _worker():
            try:
                result["live"] = bool(asyncio.run(_fetch()))
            except Exception:
                result["live"] = False

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=8.0)
        return bool(result.get("live"))
    except Exception:
        logger.debug("Helix live probe failed", exc_info=True)
        return False


class DiscordService:
    """Manages the discord.py client lifecycle on a background thread."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client: Optional[discord.Client] = None
        self._ready = threading.Event()
        self._stop_requested = False
        self._token: str = ""
        self._status: str = "Disconnected"
        self.go_live = GoLiveAnnouncer()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            connected = bool(self._client and self._ready.is_set() and not self._stop_requested)
            bot_name = ""
            bot_id = ""
            app_id = ""
            if self._client and self._client.user:
                bot_name = str(self._client.user)
                bot_id = str(self._client.user.id)
            if self._client and getattr(self._client, "application_id", None):
                app_id = str(self._client.application_id)
            return {
                "status": self._status,
                "is_connected": connected,
                "bot_username": bot_name,
                "bot_user_id": bot_id,
                "application_id": app_id,
                "invite_url": self.get_invite_url() if connected else "",
            }

    def is_connected(self) -> bool:
        return bool(self.get_status().get("is_connected"))

    def get_invite_url(self, permissions: int = _BOT_INVITE_PERMISSIONS) -> str:
        app_id = ""
        with self._lock:
            if self._client and getattr(self._client, "application_id", None):
                app_id = str(self._client.application_id)
        if not app_id:
            try:
                data = state_manager.get_discord_data()
                app_id = (getattr(data, "application_id", "") or "").strip()
            except Exception:
                app_id = ""
        if not app_id:
            return ""
        params = urlencode(
            {
                "client_id": app_id,
                "permissions": str(permissions),
                "scope": "bot",
            }
        )
        return f"https://discord.com/api/oauth2/authorize?{params}"

    def list_guilds(self) -> List[Dict[str, str]]:
        return self._run_coro(self._list_guilds_async(), default=[]) or []

    def list_text_channels(self, guild_id: str) -> List[Dict[str, str]]:
        return (
            self._run_coro(self._list_text_channels_async(guild_id), default=[]) or []
        )

    def send_to_channels(
        self, message: str, targets: List[Dict[str, Any]]
    ) -> bool:
        message = (message or "").strip()
        channels = _normalize_channel_targets(targets)
        if not message or not channels:
            return False
        if not self.is_connected():
            logger.warning("Discord send failed: bot not connected")
            return False
        result = self._run_coro(
            self._send_to_channels_async(message, channels),
            default=False,
            timeout=30.0,
        )
        return bool(result)

    def connect_with_token(self, token: str) -> bool:
        token = (token or "").strip()
        if not token:
            self._set_status("Disconnected")
            return False

        self.disconnect(join_timeout=8.0)

        with self._lock:
            self._token = token
            self._stop_requested = False
            self._ready.clear()
            self._set_status("Connecting")

        self._thread = threading.Thread(
            target=self._thread_main,
            name="MycelianDiscordBot",
            daemon=True,
        )
        self._thread.start()

        if not self._ready.wait(timeout=_READY_TIMEOUT_SEC):
            logger.error("Discord bot failed to become ready within timeout")
            self.disconnect(join_timeout=5.0)
            self._set_status("Error")
            self._persist_status_fields()
            return False

        if not self.is_connected():
            status = self.get_status().get("status") or "Error"
            logger.error("Discord bot failed to connect (status=%s)", status)
            self.disconnect(join_timeout=5.0)
            self._set_status(status if status != "Disconnected" else "Error")
            self._persist_status_fields()
            return False

        self._persist_status_fields(token=token)
        return True

    def disconnect(self, join_timeout: float = 10.0) -> None:
        with self._lock:
            self._stop_requested = True
            loop = self._loop
            client = self._client

        if loop and client and loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(client.close(), loop)
                fut.result(timeout=5.0)
            except Exception:
                logger.debug("Discord client close error", exc_info=True)
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass

        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=join_timeout)

        with self._lock:
            self._thread = None
            self._loop = None
            self._client = None
            self._ready.clear()
            self._set_status("Disconnected")
            session_token = (self._token or "").strip()

        try:
            data = state_manager.get_discord_data()
            stored = (getattr(data, "bot_token", "") or "").strip()
            # Shutdown/disconnect must not wipe a session token if state was cleared
            # by a blank password-field save race.
            if not stored and session_token:
                state_manager.update_discord_field("bot_token", session_token)
            state_manager.update_discord_field("connection_status", "Disconnected")
            state_manager.save_changes()
        except Exception:
            logger.debug("Could not persist Discord disconnect status", exc_info=True)

    def start_discord_service(self) -> None:
        """Deferred startup: reconnect if a bot token is stored."""
        try:
            if not state_manager._initialized:
                state_manager.initialize()
            data = state_manager.get_discord_data()
            token = (getattr(data, "bot_token", "") or "").strip()
            # Fallback: preload list may have omitted DiscordData; read DB directly.
            if not token:
                try:
                    from . import database_manager
                    from .encryption_utils import ensure_decrypted

                    raw = database_manager.get_data("DiscordData") or {}
                    raw_tok = str((raw or {}).get("bot_token") or "").strip()
                    if raw_tok:
                        token = ensure_decrypted(raw_tok).strip()
                        if token:
                            state_manager.update_discord_field("bot_token", token)
                            logger.info(
                                "Discord: seeded bot_token from DB fallback (len=%s)",
                                len(token),
                            )
                except Exception:
                    logger.debug(
                        "Discord: DB fallback token load failed", exc_info=True
                    )
            if not token:
                logger.warning("Discord: no bot token configured, skipping connect")
                return
            logger.info("Discord: connecting with stored bot token (len=%s)", len(token))
            ok = self.connect_with_token(token)
            if ok:
                # Seed YouTube live flag from current status for session accuracy
                try:
                    yd = state_manager.get_youtube_data()
                    if (getattr(yd, "live_chat_status", "") or "") == "Live":
                        self.go_live.youtube_live = True
                except Exception:
                    pass
            else:
                logger.warning("Discord: auto-connect failed")
        except Exception as e:
            logger.error("Discord service start failed: %s", e, exc_info=True)

    def attempt_auto_reconnect(self) -> bool:
        if self.is_connected():
            return True
        try:
            data = state_manager.get_discord_data()
            token = (getattr(data, "bot_token", "") or "").strip()
            if not token:
                return False
            return self.connect_with_token(token)
        except Exception:
            logger.debug("Discord auto-reconnect failed", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _set_status(self, status: str) -> None:
        with self._lock:
            self._status = status

    def _persist_status_fields(self, token: Optional[str] = None) -> None:
        try:
            if token is not None and str(token).strip():
                state_manager.update_discord_field("bot_token", str(token).strip())

            info = self.get_status()
            for field, value in (
                ("connection_status", info["status"]),
                ("bot_user_id", info.get("bot_user_id") or ""),
                ("bot_username", info.get("bot_username") or ""),
                ("application_id", info.get("application_id") or ""),
            ):
                state_manager.update_discord_field(field, value)

            if not state_manager.save_changes():
                logger.warning("Discord status persist: save_changes failed")
        except Exception:
            logger.warning("Failed to persist Discord status fields", exc_info=True)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._loop = loop

        intents = discord.Intents.default()
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready():
            logger.info(
                "Discord bot ready as %s (%s)",
                client.user,
                getattr(client.user, "id", "?"),
            )
            with self._lock:
                self._client = client
                self._set_status("Connected")
            self._ready.set()

        @client.event
        async def on_disconnect():
            if not self._stop_requested:
                logger.warning("Discord gateway disconnected")
                self._set_status("Reconnecting")

        @client.event
        async def on_resumed():
            self._set_status("Connected")

        with self._lock:
            self._client = client
            token = self._token

        try:
            loop.run_until_complete(client.start(token))
        except discord.LoginFailure:
            logger.error("Discord login failed: invalid bot token")
            self._set_status("Auth Failed")
            self._ready.set()  # unblock waiters
        except Exception as e:
            logger.error("Discord client crashed: %s", e, exc_info=True)
            self._set_status("Error")
            self._ready.set()
        finally:
            try:
                if not client.is_closed():
                    loop.run_until_complete(client.close())
            except Exception:
                pass
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            loop.close()
            with self._lock:
                self._loop = None
                self._client = None
                if self._status not in ("Auth Failed", "Error"):
                    self._set_status("Disconnected")
                self._ready.clear()

    def _run_coro(
        self,
        coro,
        *,
        default: Any = None,
        timeout: float = 15.0,
    ) -> Any:
        with self._lock:
            loop = self._loop
            ready = self._ready.is_set()
        if not loop or not ready or not loop.is_running():
            try:
                coro.close()
            except Exception:
                pass
            return default
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
            return fut.result(timeout=timeout)
        except Exception:
            logger.debug("Discord coro failed", exc_info=True)
            return default

    async def _list_guilds_async(self) -> List[Dict[str, str]]:
        client = self._client
        if not client:
            return []
        return [
            {"id": str(g.id), "name": g.name}
            for g in sorted(client.guilds, key=lambda g: (g.name or "").lower())
        ]

    async def _list_text_channels_async(self, guild_id: str) -> List[Dict[str, str]]:
        client = self._client
        if not client:
            return []
        try:
            gid = int(guild_id)
        except (TypeError, ValueError):
            return []
        guild = client.get_guild(gid)
        if not guild:
            return []
        channels: List[Dict[str, str]] = []
        for ch in guild.text_channels:
            channels.append(
                {
                    "id": str(ch.id),
                    "name": ch.name,
                    "guild_id": str(guild.id),
                    "guild_name": guild.name,
                }
            )
        channels.sort(key=lambda c: (c.get("name") or "").lower())
        return channels

    async def _send_to_channels_async(
        self, message: str, channels: List[Dict[str, str]]
    ) -> bool:
        client = self._client
        if not client:
            return False
        any_ok = False
        for target in channels:
            channel_id = target.get("channel_id") or ""
            try:
                cid = int(channel_id)
            except (TypeError, ValueError):
                logger.warning("Invalid Discord channel id: %s", channel_id)
                continue
            channel = client.get_channel(cid)
            if channel is None:
                try:
                    channel = await client.fetch_channel(cid)
                except Exception as e:
                    logger.warning(
                        "Could not fetch Discord channel %s: %s", channel_id, e
                    )
                    continue
            try:
                if not hasattr(channel, "send"):
                    logger.warning("Discord target %s is not sendable", channel_id)
                    continue
                await channel.send(message)
                any_ok = True
            except Exception as e:
                logger.error(
                    "Failed to send Discord message to %s: %s",
                    channel_id,
                    e,
                    exc_info=True,
                )
        return any_ok


# Module singleton + thin wrappers -------------------------------------------------

discord_service = DiscordService()


def start_discord_service() -> None:
    discord_service.start_discord_service()


def get_discord_status() -> Dict[str, Any]:
    return discord_service.get_status()


def connect_with_token(token: str) -> bool:
    return discord_service.connect_with_token(token)


def disconnect() -> None:
    discord_service.disconnect()


def list_guilds() -> List[Dict[str, str]]:
    return discord_service.list_guilds()


def list_text_channels(guild_id: str) -> List[Dict[str, str]]:
    return discord_service.list_text_channels(guild_id)


def send_to_channels(message: str, targets: List[Dict[str, Any]]) -> bool:
    return discord_service.send_to_channels(message, targets)


def get_invite_url() -> str:
    return discord_service.get_invite_url()


def discord_configured_for_monitor() -> bool:
    try:
        data = state_manager.get_discord_data()
        return bool((getattr(data, "bot_token", "") or "").strip())
    except Exception:
        return False


def attempt_auto_reconnect() -> bool:
    return discord_service.attempt_auto_reconnect()


def notify_platform_live(
    platform: str, *, title: str = "", url: str = ""
) -> bool:
    return discord_service.go_live.on_platform_live(
        platform, title=title, url=url
    )


def notify_platform_offline(platform: str) -> None:
    discord_service.go_live.on_platform_offline(platform)
