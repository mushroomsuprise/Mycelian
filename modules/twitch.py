#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024-2026 Mycelian

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import asyncio
import logging
import multiprocessing
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Optional

import aiohttp
from twitchAPI.eventsub.websocket import EventSubWebsocket
from twitchAPI.helper import first
from twitchAPI.oauth import UserAuthenticator
from twitchAPI.object.eventsub import (
    ChannelBitsUseEvent,
    ChannelChatMessageEvent,
    ChannelChatNotificationEvent,
    ChannelCheerEvent,
    ChannelFollowEvent,
    ChannelModerateEvent,
    ChannelPointsCustomRewardRedemptionAddEvent,
    ChannelRaidEvent,
    ChannelSubscribeEvent,
    ChannelSubscriptionGiftEvent,
    ChannelSubscriptionMessageEvent,
    ChannelUpdateEvent,
    HypeTrainEndEvent,
    HypeTrainEvent,
)
from twitchAPI.twitch import Twitch
from urllib.parse import parse_qs, urlparse

from twitchAPI.type import AuthScope, InvalidRefreshTokenException

from .twitch_chat_commands import is_allowed_slash_message

from . import (
    alert_processor,
    alertutils,
    database_manager,
    dataobjects,
    statistics_manager,
    web_engine,
)
from .chatbot_core import EventType
from .chatbot_manager import get_manager as get_chatbot_manager
from .template_config_parser import match_point_reward_dedicated_template
from .text_safe import safe_console_str
from .twitch_eventsub_patch import ensure_channel_chat_notification_watch_streak_patch
from .twitch_oauth import run_user_authentication, stop_active_oauth
from .uiwindows.activity_feed import (
    add_alert_to_feed,
    format_raid_activity_message,
    format_watch_streak_message,
)

ensure_channel_chat_notification_watch_streak_patch()

logger = logging.getLogger(__name__)

# Twitch user access tokens live ~4 hours (not 60 days). Used as a conservative
# fallback when the OAuth validate endpoint can't be reached, so the proactive
# refresh in ``_health_check_loop`` still runs before the token actually dies.
ACCESS_TOKEN_FALLBACK_LIFETIME = timedelta(hours=3, minutes=30)
# Proactive refresh in ``is_token_expired`` / ``_health_check_loop`` fires this
# many minutes before the access token actually expires.
TOKEN_PROACTIVE_REFRESH_BUFFER = timedelta(minutes=5)

_ANONYMOUS_USER_SENTINELS = frozenset({"anonymous", "anonymous gifter"})


def twitch_user_lookup_allowed(username: str, *, anonymous: bool = False) -> bool:
    """Return False when a Twitch helix/users lookup should be skipped."""
    if anonymous:
        return False
    login = (username or "").strip()
    if not login:
        return False
    if " " in login:
        return False
    if login.lower() in _ANONYMOUS_USER_SENTINELS:
        return False
    return True


def parse_helix_users_login(
    url: str, params: Optional[dict] = None
) -> Optional[str]:
    """Extract login from a helix/users URL or query params."""
    if params and params.get("login") is not None:
        login = params.get("login")
        if isinstance(login, list):
            login = login[0] if login else ""
        return str(login).strip() or None
    try:
        parsed = urlparse(url)
        if "/helix/users" not in parsed.path:
            return None
        qs = parse_qs(parsed.query)
        logins = qs.get("login")
        if logins:
            return str(logins[0]).strip() or None
    except Exception:
        return None
    return None


def format_token_countdown(delta: timedelta) -> str:
    """Format a timedelta as a human-readable countdown (e.g. ``2h 14m 32s``)."""
    total = int(delta.total_seconds())
    if total <= 0:
        return "Due now"
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def build_token_timing_fields(
    *,
    token_expiry: Optional[datetime],
    has_auth_token: bool,
) -> dict:
    """Countdown / clock fields for the Twitch settings tab token timers."""
    empty = {
        "token_refresh_countdown": "—",
        "token_refresh_at": "—",
        "token_expires_countdown": "—",
        "token_expires_at": "—",
        "token_refresh_due": False,
        "token_expired": False,
    }
    if not has_auth_token:
        return empty
    if not token_expiry:
        return {
            **empty,
            "token_refresh_countdown": "Unknown",
            "token_refresh_at": "Unknown",
            "token_expires_countdown": "Unknown",
            "token_expires_at": "Unknown",
        }
    now = datetime.now()
    refresh_at = token_expiry - TOKEN_PROACTIVE_REFRESH_BUFFER
    until_refresh = refresh_at - now
    until_expiry = token_expiry - now
    return {
        "token_refresh_countdown": format_token_countdown(until_refresh),
        "token_refresh_at": refresh_at.strftime("%Y-%m-%d %H:%M:%S"),
        "token_expires_countdown": format_token_countdown(until_expiry),
        "token_expires_at": token_expiry.strftime("%Y-%m-%d %H:%M:%S"),
        "token_refresh_due": until_refresh.total_seconds() <= 0,
        "token_expired": until_expiry.total_seconds() <= 0,
    }


class TwitchSessionNotReadyError(Exception):
    """Helix call cannot run yet (missing auth or Twitch library client)."""


class TwitchPermissionError(Exception):
    """Twitch returned 403 Forbidden (e.g. endpoint requires Affiliate/Partner).

    This is an expected condition for some accounts, not an auth failure, so
    callers can treat it quietly instead of logging it as an error.
    """


# Global flag to track initialization status
_initialized = False
_init_lock = threading.Lock()
_staging_complete = threading.Event()

# Global flag to track Twitch API connection status
twitch_connected = False


@asynccontextmanager
async def _ephemeral_client_session():
    """Short-lived Helix client; enable_cleanup_closed avoids pending tasks on Py 3.13."""
    connector = aiohttp.TCPConnector(enable_cleanup_closed=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        yield session


_CHEER_BITS_CACHE_TTL = 10.0
_cheer_bits_cache: dict[str, dict[str, Any]] = {}

_ALERT_USER_MESSAGE_CACHE_TTL = 30.0
_alert_user_message_cache: dict[str, float] = {}


def _alert_user_message_cache_key(user_id: str, message_text: str) -> str:
    return f"{user_id}:{(message_text or '').strip()}"


def _prune_alert_user_message_cache() -> None:
    now = time.time()
    expired = [k for k, v in _alert_user_message_cache.items() if v <= now]
    for key in expired:
        _alert_user_message_cache.pop(key, None)


def register_alert_user_message(user_id: str, message_text: str) -> None:
    """Mark a chat message body as originating from an alert user message."""
    text = (message_text or "").strip()
    if not user_id or not text:
        return
    _prune_alert_user_message_cache()
    _alert_user_message_cache[
        _alert_user_message_cache_key(user_id, text)
    ] = time.time() + _ALERT_USER_MESSAGE_CACHE_TTL


def is_alert_user_message(user_id: str, message_text: str) -> bool:
    _prune_alert_user_message_cache()
    expiry = _alert_user_message_cache.get(
        _alert_user_message_cache_key(user_id, message_text)
    )
    return expiry is not None and expiry > time.time()


def subscription_emotes_to_json(emotes) -> Optional[list]:
    """Convert EventSub subscription message.emotes to JSON-serializable list."""
    if not emotes:
        return None
    out = []
    for emote in emotes:
        if isinstance(emote, dict):
            begin = emote.get("begin")
            end = emote.get("end")
            emote_id = emote.get("id")
        else:
            begin = getattr(emote, "begin", None)
            end = getattr(emote, "end", None)
            emote_id = getattr(emote, "id", None)
        if begin is None or end is None or emote_id is None:
            continue
        out.append({"begin": int(begin), "end": int(end), "id": str(emote_id)})
    return out if out else None


def serialize_bits_message_fragments(fragments) -> list:
    """Serialize channel.bits.use message fragments for alert overlay rendering."""
    if not fragments:
        return []
    result = []
    for fragment in fragments:
        frag_type = getattr(fragment, "type", None) or "text"
        text = getattr(fragment, "text", "") or ""
        entry: dict[str, Any] = {"type": frag_type, "text": text}

        if frag_type == "cheermote" or (
            hasattr(fragment, "cheermote") and fragment.cheermote
        ):
            entry["type"] = "cheermote"
            cm = fragment.cheermote
            entry["prefix"] = str(getattr(cm, "prefix", "") or "") if cm else ""
            entry["bits"] = int(getattr(cm, "bits", 0) or 0) if cm else 0
            entry["tier"] = int(getattr(cm, "tier", 1) or 1) if cm else 1
        elif frag_type == "emote" or (hasattr(fragment, "emote") and fragment.emote):
            entry["type"] = "emote"
            em = fragment.emote
            entry["emote_id"] = str(getattr(em, "id", "") or "") if em else ""
            if em and hasattr(em, "emote_set_id"):
                entry["emote_set_id"] = str(getattr(em, "emote_set_id", "") or "")
        result.append(entry)
    return result


def extract_emotes_positions_from_fragments(
    message_text: str, fragments
) -> Optional[list]:
    """Build subscription-style emote position list from bits message fragments."""
    if not fragments or not message_text:
        return None
    emotes = []
    char_pos = 0
    for fragment in fragments:
        text = getattr(fragment, "text", "") or ""
        length = len(text)
        frag_type = getattr(fragment, "type", None) or "text"
        if frag_type == "emote" or (hasattr(fragment, "emote") and fragment.emote):
            em = fragment.emote
            emote_id = getattr(em, "id", None) if em else None
            if emote_id is not None:
                emotes.append(
                    {
                        "begin": char_pos,
                        "end": char_pos + length - 1,
                        "id": str(emote_id),
                    }
                )
        char_pos += length
    return emotes if emotes else None


def _cheer_cache_key(user_id: str, bits: int) -> str:
    return f"{user_id}:{bits}"


def _prune_cheer_bits_cache() -> None:
    now = time.time()
    expired = [k for k, v in _cheer_bits_cache.items() if v.get("expires", 0) < now]
    for key in expired:
        _cheer_bits_cache.pop(key, None)


def _store_cheer_bits_message(
    user_id: str,
    bits: int,
    text: str,
    fragments: Optional[list],
    emotes: Optional[list],
) -> None:
    _prune_cheer_bits_cache()
    _cheer_bits_cache[_cheer_cache_key(user_id, bits)] = {
        "text": text,
        "fragments": fragments,
        "emotes": emotes,
        "expires": time.time() + _CHEER_BITS_CACHE_TTL,
    }
    register_alert_user_message(user_id, text)


def _pop_cheer_bits_message(user_id: str, bits: int) -> Optional[dict]:
    _prune_cheer_bits_cache()
    return _cheer_bits_cache.pop(_cheer_cache_key(user_id, bits), None)


def _subscription_message_emotes(message) -> Optional[list]:
    if not message:
        return None
    return subscription_emotes_to_json(getattr(message, "emotes", None))


def _apply_bits_message_to_alert(alert, message_obj) -> None:
    if not message_obj:
        return
    text = getattr(message_obj, "text", None) or ""
    fragments = getattr(message_obj, "fragments", None)
    alert.message = text
    if fragments:
        alert.fragments = serialize_bits_message_fragments(fragments)
        alert.emotes = extract_emotes_positions_from_fragments(text, fragments)
    else:
        alert.fragments = None
        alert.emotes = None


class Twitch_API:
    def __init__(self):
        logger.debug("Initializing Twitch API class")
        self.authscope = [
            AuthScope.BITS_READ,
            AuthScope.CHANNEL_READ_REDEMPTIONS,
            AuthScope.CHANNEL_READ_SUBSCRIPTIONS,
            AuthScope.CHANNEL_MANAGE_REDEMPTIONS,
            AuthScope.CHANNEL_READ_HYPE_TRAIN,
            AuthScope.CHANNEL_MODERATE,
            AuthScope.CHANNEL_MANAGE_BROADCAST,
            AuthScope.CHANNEL_MANAGE_POLLS,
            AuthScope.CHANNEL_MANAGE_PREDICTIONS,
            AuthScope.CHANNEL_MANAGE_RAIDS,
            AuthScope.CHANNEL_MANAGE_ADS,
            AuthScope.CHANNEL_SUBSCRIPTIONS,
            AuthScope.CHANNEL_MANAGE_MODERATORS,  # GET /moderation/moderators
            AuthScope.CHAT_EDIT,
            AuthScope.CHAT_READ,
            AuthScope.MODERATOR_READ_FOLLOWERS,
            AuthScope.MODERATOR_MANAGE_ANNOUNCEMENTS,
            AuthScope.MODERATOR_MANAGE_CHAT_MESSAGES,
            AuthScope.MODERATOR_MANAGE_SHOUTOUTS,
            AuthScope.MODERATOR_READ_MODERATORS,
            AuthScope.MODERATOR_READ_VIPS,
            AuthScope.MODERATOR_MANAGE_BLOCKED_TERMS,
            AuthScope.MODERATOR_MANAGE_CHAT_SETTINGS,
            AuthScope.MODERATOR_MANAGE_UNBAN_REQUESTS,
            AuthScope.MODERATOR_MANAGE_BANNED_USERS,
            AuthScope.MODERATOR_MANAGE_WARNINGS,
            AuthScope.MODERATOR_MANAGE_SHIELD_MODE,
            AuthScope.MODERATOR_MANAGE_SHIELD_MODE,
            AuthScope.MODERATOR_MANAGE_AUTOMOD,
            AuthScope.MODERATOR_MANAGE_AUTOMOD_SETTINGS,
            AuthScope.USER_EDIT,
            AuthScope.USER_BOT,
            AuthScope.USER_READ_CHAT,
            AuthScope.USER_WRITE_CHAT,
            AuthScope.USER_READ_EMOTES,
            AuthScope.USER_READ_BROADCAST,
        ]
        self.client_id = ""
        self.client_secret = ""
        self.auth_token = ""
        self.refresh_token = ""
        self.user_id = ""
        self.twitch = None
        self.authenticator = None
        self.user = None
        self.eventsub = None
        self.token_expiry = None
        self.health_check_thread = None
        self.is_connected = False
        self.last_health_check = None
        self.health_check_interval = 60  # Check connection every 60 seconds
        self.connection_timeout = (
            300  # Consider connection dead after 5 minutes without successful check
        )

        # Token refresh synchronization
        self._refresh_lock = threading.Lock()
        self._last_refresh_attempt = None
        self._connection_epoch = 0

        # EventSub liveness / revocation recovery
        self.last_event_time = None
        self._last_revocation_reconnect = None
        # Serializes reconnect() so health-check, revocation, and 401 paths
        # don't spawn overlapping init threads.
        self._reconnect_lock = threading.Lock()
        self._reconnect_in_progress = False
        # A live websocket session that stops delivering events for this long is
        # treated as a "silent death" zombie and rebuilt (subscriptions revoked
        # without a revocation message reaching us). Long enough that an ordinary
        # quiet stream rarely trips it; a rebuild costs only a few seconds.
        self.event_staleness_timeout = 45 * 60

    def _apply_api_credentials_from_store(self) -> None:
        """Merge Twitch app client id/secret from api_credentials.json (Settings source of truth)."""
        try:
            from modules import api_credentials_manager

            creds = api_credentials_manager.get_twitch_credentials()
            if creds.get("client_id"):
                self.client_id = creds["client_id"]
            if creds.get("client_secret"):
                self.client_secret = creds["client_secret"]
        except Exception as e:
            logger.debug(
                "Could not load Twitch credentials from credential store: %s", e
            )

    def sync_helix_credentials_from_state(self) -> None:
        """Merge tokens and client credentials from state/credential store into this instance.

        EventSub init populates twitch_api in its init thread; Helix proxy callers
        (e.g. overlay web_engine) must refresh from state_manager before API calls.
        """
        try:
            if (
                hasattr(dataobjects.state_manager, "_initialized")
                and dataobjects.state_manager._initialized
            ):
                twitch_data = dataobjects.state_manager.get_twitch_data()
                if twitch_data:
                    if twitch_data.client_id:
                        self.client_id = twitch_data.client_id
                    if twitch_data.client_secret:
                        self.client_secret = twitch_data.client_secret
                    if twitch_data.auth_token:
                        self.auth_token = twitch_data.auth_token
                    if twitch_data.refresh_token:
                        self.refresh_token = twitch_data.refresh_token
                    if twitch_data.user_id:
                        self.user_id = twitch_data.user_id
                    if twitch_data.token_expiry:
                        try:
                            self.token_expiry = datetime.fromisoformat(
                                twitch_data.token_expiry
                            )
                        except ValueError:
                            pass
        except Exception as e:
            logger.debug("Helix credential sync from state manager: %s", e)

        self._apply_api_credentials_from_store()

    def load_auth_data(self):
        """Load authentication data from the state manager"""
        try:
            # Wait for state manager to be initialized (with timeout)
            max_wait_time = 10  # seconds
            wait_interval = 0.1  # seconds
            waited_time = 0

            while waited_time < max_wait_time:
                if (
                    hasattr(dataobjects.state_manager, "_initialized")
                    and dataobjects.state_manager._initialized
                ):
                    break
                time.sleep(wait_interval)
                waited_time += wait_interval

            if (
                not hasattr(dataobjects.state_manager, "_initialized")
                or not dataobjects.state_manager._initialized
            ):
                logger.warning(
                    "State manager not initialized after waiting - proceeding anyway"
                )
                return False

            # Get Twitch data from state manager
            twitch_data = dataobjects.state_manager.get_twitch_data()

            if twitch_data:
                self.client_id = twitch_data.client_id
                self.client_secret = twitch_data.client_secret
                self.auth_token = twitch_data.auth_token
                self.refresh_token = twitch_data.refresh_token
                self.user_id = twitch_data.user_id

                # Get token_expiry from StateManager (now part of TwitchData)
                expiry_str = twitch_data.token_expiry

                if expiry_str:
                    try:
                        self.token_expiry = datetime.fromisoformat(expiry_str)
                    except ValueError:
                        logger.warning(f"Invalid token expiry format: {expiry_str}")
                        self.token_expiry = None

                # Check if we have valid tokens
            # Client id/secret often live only in api_credentials.json, not TwitchData
            self._apply_api_credentials_from_store()

            if self.auth_token and self.refresh_token:
                logger.debug("Successfully loaded Twitch authentication data")
                return True
            logger.info(
                "Missing auth token or refresh token in state data - will need to authenticate"
            )
            return False
        except Exception as e:
            logger.error(
                f"Error loading Twitch authentication data: {str(e)}", exc_info=True
            )
            # Continue without data
            return False

    def save_auth_data(self):
        """Save authentication data to the state manager"""
        try:
            # Check if state manager is initialized
            if (
                not hasattr(dataobjects.state_manager, "_initialized")
                or not dataobjects.state_manager._initialized
            ):
                logger.warning(
                    "State manager not initialized when saving Twitch auth data"
                )
                return False

            # Update the state manager
            twitch_data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_token": self.auth_token,
                "refresh_token": self.refresh_token,
                "user_id": self.user_id,
                "token_expiry": self.token_expiry.isoformat()
                if self.token_expiry
                else "",
            }

            # Log token details for debugging (without exposing full tokens)
            token_preview = self.auth_token[:10] + "..." if self.auth_token else "None"
            expiry_str = self.token_expiry.isoformat() if self.token_expiry else "None"
            logger.info(
                f"Saving Twitch auth data - Token: {token_preview}, Expiry: {expiry_str}"
            )

            dataobjects.state_manager.set_twitch_data(twitch_data)

            # Force save changes to ensure data is persisted immediately
            save_changes_success = dataobjects.state_manager.save_changes()
            if not save_changes_success:
                logger.warning(
                    "Failed to save changes to database when saving Twitch auth data"
                )

            # Also update the streamer_name in app_settings if we have user info
            if hasattr(self, "user") and self.user:
                try:
                    # Update streamer_name in app_settings with the display name from Twitch
                    streamer_name = self.user.display_name
                    dataobjects.state_manager.update_app_setting(
                        "streamer_name", streamer_name
                    )
                    logger.info(f"Updated streamer name to: {streamer_name}")
                except Exception as e:
                    logger.warning(f"Could not update streamer name: {str(e)}")

            logger.info("Successfully saved Twitch authentication data to database")
            return True
        except Exception as e:
            logger.error(
                f"Error saving Twitch authentication data: {str(e)}", exc_info=True
            )
            # Continue without saving
            return False

    async def _compute_token_expiry(self, token: str) -> datetime:
        """Resolve the real expiry for a Twitch user access token.

        Queries the OAuth validate endpoint for the exact ``expires_in`` (Twitch
        user tokens last ~4h, not 60 days). Falls back to a conservative ~3.5h if
        validation is unavailable, so ``is_token_expired()`` becomes True in time
        for the health-check loop to refresh proactively.
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://id.twitch.tv/oauth2/validate",
                    headers={"Authorization": f"OAuth {token}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        payload = await resp.json()
                        expires_in = int(payload.get("expires_in", 0) or 0)
                        if expires_in > 0:
                            return datetime.now() + timedelta(seconds=expires_in)
                        # expires_in == 0 means a non-expiring token; keep using it.
                        return datetime.now() + timedelta(days=60)
                    logger.warning(
                        "Twitch token validate returned %s; using fallback expiry",
                        resp.status,
                    )
        except Exception as e:
            logger.warning("Could not validate Twitch token expiry: %s", e)
        return datetime.now() + ACCESS_TOKEN_FALLBACK_LIFETIME

    def is_token_expired(self):
        """Check if the current auth token is expired"""
        if not self.auth_token:
            return True
        if not self.token_expiry:
            # Unknown expiry — use token until Helix returns 401
            return False
        # Consider token expired if less than 5 minutes remaining
        return datetime.now() + timedelta(minutes=5) >= self.token_expiry

    async def refresh_auth_token(self):
        """Refresh the authentication token using the refresh token"""

        # Check if another refresh is already in progress
        if not self._refresh_lock.acquire(blocking=False):
            logger.info("Token refresh already in progress, waiting...")
            # Wait for the other refresh to complete
            with self._refresh_lock:
                logger.info(
                    "Other token refresh completed, checking if we still need to refresh"
                )
                if not self.is_token_expired():
                    logger.info("Token was already refreshed by another process")
                    return True

        try:
            # Check if we recently attempted a refresh (within last 30 seconds)
            now = datetime.now()
            if (
                self._last_refresh_attempt
                and (now - self._last_refresh_attempt).total_seconds() < 30
            ):
                logger.info(
                    "Token refresh attempted recently, skipping duplicate refresh"
                )
                return not self.is_token_expired()

            self._last_refresh_attempt = now

            logger.info("Refreshing authentication token")

            self.sync_helix_credentials_from_state()
            if not self.client_id or not self.client_secret:
                logger.warning(
                    "Cannot refresh Twitch token: client id/secret not configured"
                )
                return False

            # Check if we have a refresh token
            if not self.refresh_token:
                logger.error("No refresh token available for token refresh")
                return False

            # Use existing Twitch instance if available, otherwise create one
            if not self.twitch:
                self.twitch = Twitch(self.client_id, self.client_secret)

            # Set user authentication with current tokens
            await self.twitch.set_user_authentication(
                self.auth_token, self.authscope, self.refresh_token
            )

            # Store current tokens before refresh to compare later
            old_twitch_auth_token = getattr(self.twitch, "_user_auth_token", None)
            old_twitch_refresh_token = getattr(
                self.twitch, "_user_auth_refresh_token", None
            )

            # Refresh the token
            await self.twitch.refresh_used_token()

            # Get the refreshed tokens from the Twitch instance
            # The refresh_used_token method updates tokens internally but doesn't return them
            new_auth_token = getattr(self.twitch, "_user_auth_token", None)
            new_refresh_token = getattr(self.twitch, "_user_auth_refresh_token", None)

            # Check if refresh was successful by comparing tokens and ensuring they're valid
            if (
                not new_auth_token
                or not new_refresh_token
                or (
                    new_auth_token == old_twitch_auth_token
                    and new_refresh_token == old_twitch_refresh_token
                )
            ):
                logger.error("Token refresh failed - tokens are empty or unchanged")

                # Clear invalid tokens
                self.auth_token = ""
                self.refresh_token = ""
                self.token_expiry = None

                # Save the cleared state
                self.save_auth_data()

                logger.warning(
                    "Cleared invalid tokens - OAuth re-authentication required"
                )
                return False

            # Update tokens
            old_auth_token = self.auth_token
            self.auth_token = new_auth_token
            self.refresh_token = new_refresh_token

            # Resolve the real (~4h) expiry so proactive refresh fires in time.
            self.token_expiry = await self._compute_token_expiry(self.auth_token)

            # Save new tokens to database
            save_success = self.save_auth_data()
            if not save_success:
                logger.warning(
                    "Failed to save refreshed tokens to database, but continuing with new tokens"
                )

            # IMPORTANT: Also sync to state manager to ensure all parts of the app get updated tokens
            self._sync_tokens_to_state_manager()

            logger.info(
                f"Successfully refreshed authentication token (old: {old_auth_token[:10]}..., new: {new_auth_token[:10]}...)"
            )
            return True
        except InvalidRefreshTokenException as e:
            err = str(e).lower()
            if "client secret" in err or "client_id" in err:
                logger.warning(
                    "Cannot refresh Twitch token (check client id/secret in Settings): %s",
                    e,
                )
                return False
            logger.warning("Twitch refresh token invalid: %s", e)
            self._clear_tokens_after_refresh_failure()
            return False
        except Exception as e:
            err = str(e).lower()
            if "client secret" in err:
                logger.warning(
                    "Cannot refresh Twitch token (check client secret in Settings): %s",
                    e,
                )
                return False
            logger.error("Failed to refresh authentication token: %s", e, exc_info=True)
            self._clear_tokens_after_refresh_failure()
            return False
        finally:
            # Always release the lock
            try:
                self._refresh_lock.release()
            except:
                pass  # Lock might already be released

    def _clear_tokens_after_refresh_failure(self) -> None:
        """Clear stored tokens after a failed refresh (invalid/revoked token)."""
        self.auth_token = ""
        self.refresh_token = ""
        self.token_expiry = None
        try:
            self.save_auth_data()
            self._sync_tokens_to_state_manager()
            logger.warning(
                "Cleared invalid tokens after refresh failure - OAuth re-authentication required"
            )
        except Exception as save_error:
            logger.error("Failed to save cleared token state: %s", save_error)

    async def authenticate_with_oauth(self):
        """Handle the OAuth flow to get new authentication tokens"""
        try:
            logger.debug("Starting OAuth authentication flow")

            # Create Twitch instance
            self.twitch = Twitch(self.client_id, self.client_secret)

            # Create authenticator
            self.authenticator = UserAuthenticator(
                self.twitch, self.authscope, force_verify=False
            )

            # Get tokens through OAuth (serialized app-wide on port 17563)
            (
                self.auth_token,
                self.refresh_token,
            ) = await run_user_authentication(self.authenticator)

            # Set user authentication with the tokens we just received
            await self.twitch.set_user_authentication(
                self.auth_token, self.authscope, self.refresh_token
            )

            # Now get user info
            self.user = await first(self.twitch.get_users())
            self.user_id = self.user.id

            # Resolve the real (~4h) expiry so proactive refresh fires in time.
            self.token_expiry = await self._compute_token_expiry(self.auth_token)

            # Save new tokens to Firebase
            self.save_auth_data()

            logger.debug(
                f"Successfully authenticated as user: {self.user.display_name}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to authenticate with OAuth: {str(e)}", exc_info=True)
            return False

    async def stage_twitch_api(self):
        """Stage the Twitch API for use"""
        try:
            logger.debug("Staging Twitch API connection")

            # Load tokens from state + client id/secret from api_credentials.json
            auth_data_loaded = self.load_auth_data()
            self.sync_helix_credentials_from_state()

            # Check if we have client credentials at minimum
            if not self.client_id or not self.client_secret:
                logger.warning(
                    "Missing Twitch client credentials - authentication will be required"
                )
                # We can't proceed without client credentials
                return False

            # Check if we have valid tokens
            if auth_data_loaded and self.auth_token and self.refresh_token:
                # Check if token is expired and refresh if needed
                if self.is_token_expired():
                    logger.info(
                        "Auth token expired during staging, attempting to refresh"
                    )
                    refresh_success = await self.refresh_auth_token()
                    if not refresh_success:
                        logger.warning(
                            "Failed to refresh token during staging, will attempt new authentication"
                        )
                        auth_data_loaded = False
                    else:
                        logger.info("Successfully refreshed token during staging")

            # If we don't have valid auth data, trigger OAuth flow
            if not auth_data_loaded or not self.auth_token or not self.refresh_token:
                logger.info("No valid authentication data, starting OAuth flow")
                notify_twitch_connect_failed()
                oauth_success = await self.authenticate_with_oauth()
                if not oauth_success:
                    logger.error("Failed to authenticate with OAuth")
                    return False  # Return False instead of raising exception
            else:
                # Use existing tokens
                self.twitch = Twitch(self.client_id, self.client_secret)

                # Set user authentication with existing tokens
                await self.twitch.set_user_authentication(
                    self.auth_token, self.authscope, self.refresh_token
                )

                # Verify the tokens still work
                try:
                    self.user = await first(self.twitch.get_users())
                    self.user_id = self.user.id
                    logger.debug(
                        f"Successfully authenticated with existing tokens as user: {self.user.display_name}"
                    )

                    # Refresh the stored expiry from Twitch so the proactive
                    # health-check refresh fires in time (older sessions saved a
                    # bogus 60-day expiry that disabled proactive refresh).
                    self.token_expiry = await self._compute_token_expiry(
                        self.auth_token
                    )

                    # Save the verified auth data to ensure it's persisted
                    self.save_auth_data()

                except Exception as e:
                    logger.warning(f"Existing tokens failed validation: {str(e)}")
                    logger.info(
                        "Attempting token refresh before OAuth after validation failure"
                    )
                    refresh_success = await self.refresh_auth_token()
                    if refresh_success:
                        try:
                            self.user = await first(self.twitch.get_users())
                            self.user_id = self.user.id
                            self.token_expiry = await self._compute_token_expiry(
                                self.auth_token
                            )
                            self.save_auth_data()
                            logger.info(
                                "Recovered Twitch session after token refresh"
                            )
                        except Exception as retry_err:
                            logger.warning(
                                "Tokens still invalid after refresh: %s", retry_err
                            )
                            refresh_success = False
                    if not refresh_success:
                        notify_twitch_connect_failed()
                        oauth_success = await self.authenticate_with_oauth()
                        if not oauth_success:
                            logger.error(
                                "Failed to authenticate with OAuth after token validation failure"
                            )
                            return False

            return True  # Successfully staged

        except Exception as e:
            logger.error(f"Failed to stage Twitch API: {str(e)}", exc_info=True)
            return False  # Return False instead of raising exception

    async def on_chat_message(self, data: ChannelChatMessageEvent):
        self._note_event_received()
        username = data.event.chatter_user_name
        message = data.event.message.text
        logger.debug(
            f"Chat message from {data.event.chatter_user_name}: {data.event.message.text}"
        )
        user_id = data.event.chatter_user_id
        twmsg_id = data.event.message_id
        color = data.event.color
        badges = data.event.badges
        fragments = data.event.message.fragments
        message_type = getattr(data.event, "message_type", "text")

        # Track chat message statistics
        try:
            stats_manager = statistics_manager.get_statistics_manager()
            stats_manager.increment_twitch_messages(username=username)
            logger.debug("Tracked incoming Twitch chat message")
        except Exception as e:
            logger.error(f"Error tracking chat message statistics: {e}")

        # Debug: Log the actual badges format
        logger.debug(f"Raw badges for {username}: {badges} (type: {type(badges)})")
        if badges:
            logger.debug(
                f"Badges details: {[f'{badge.set_id}/{badge.id}' for badge in badges]}"
            )

        # Extract emotes from fragments and format as traditional emote string
        emotes_string = None
        if fragments:
            emote_dict = {}
            char_pos = 0

            for fragment in fragments:
                fragment_text = fragment.text if hasattr(fragment, "text") else ""
                fragment_length = len(fragment_text)

                # Check if this fragment contains an emote
                if (
                    hasattr(fragment, "emote")
                    and fragment.emote
                    and hasattr(fragment.emote, "id")
                ):
                    emote_id = fragment.emote.id
                    start_pos = char_pos
                    end_pos = char_pos + fragment_length - 1

                    if emote_id not in emote_dict:
                        emote_dict[emote_id] = []
                    emote_dict[emote_id].append(f"{start_pos}-{end_pos}")

                char_pos += fragment_length

            # Convert emote_dict to traditional format: "emote_id:start-end,start-end/emote_id2:start-end"
            if emote_dict:
                emote_parts = []
                for emote_id, positions in emote_dict.items():
                    emote_parts.append(f"{emote_id}:{','.join(positions)}")
                emotes_string = "/".join(emote_parts)
                logger.debug(f"Extracted emotes for {username}: {emotes_string}")

        msg_dict = {
            "id": twmsg_id,
            "username": username,
            "userid": user_id,
            "message": message,
            "twmsgid": twmsg_id,
            "fragments": None,  # Convert fragments to JSON-serializable format below
            "color": color,
            "badges": ",".join([f"{badge.set_id}/{badge.id}" for badge in badges])
            if badges
            else None,
            "emotes": emotes_string,  # Now properly populated with emote data
            "timestamp": time.time(),
            "type": "chat",
            "message_type": message_type,
        }

        # Convert fragments to JSON-serializable format
        if fragments:
            try:
                serializable_fragments = []
                for fragment in fragments:
                    fragment_dict = {
                        "text": fragment.text if hasattr(fragment, "text") else "",
                        "type": "text",  # Default type
                    }

                    # Check if it's an emote fragment
                    if hasattr(fragment, "emote") and fragment.emote:
                        fragment_dict["type"] = "emote"
                        fragment_dict["emote_id"] = (
                            fragment.emote.id if hasattr(fragment.emote, "id") else None
                        )
                        fragment_dict["emote_name"] = (
                            fragment.emote.name
                            if hasattr(fragment.emote, "name")
                            else None
                        )

                    # Check if it's a cheermote fragment
                    elif hasattr(fragment, "cheermote") and fragment.cheermote:
                        fragment_dict["type"] = "cheermote"
                        fragment_dict["bits"] = (
                            fragment.cheermote.bits
                            if hasattr(fragment.cheermote, "bits")
                            else 0
                        )
                        fragment_dict["tier"] = (
                            fragment.cheermote.tier
                            if hasattr(fragment.cheermote, "tier")
                            else 1
                        )

                    # Check if it's a mention fragment
                    elif hasattr(fragment, "mention") and fragment.mention:
                        fragment_dict["type"] = "mention"
                        fragment_dict["user_id"] = (
                            fragment.mention.user_id
                            if hasattr(fragment.mention, "user_id")
                            else None
                        )
                        fragment_dict["user_name"] = (
                            fragment.mention.user_name
                            if hasattr(fragment.mention, "user_name")
                            else None
                        )

                    serializable_fragments.append(fragment_dict)

                msg_dict["fragments"] = serializable_fragments
                logger.debug(
                    f"Converted {len(serializable_fragments)} fragments to JSON-serializable format"
                )
            except Exception as e:
                logger.warning(
                    f"Error converting fragments to JSON-serializable format: {str(e)}"
                )
                msg_dict["fragments"] = None  # Fall back to None if conversion fails

        reply = getattr(data.event, "reply", None)
        if reply:
            msg_dict["reply"] = {
                "parent_message_id": reply.parent_message_id,
                "parent_message_body": reply.parent_message_body,
                "parent_user_name": reply.parent_user_name,
                "parent_user_login": reply.parent_user_login,
                "thread_message_id": reply.thread_message_id,
            }

        if is_alert_user_message(user_id, message):
            msg_dict["is_alert_user_message"] = True

        # Process greetings for new users
        try:
            chatbot_manager = get_chatbot_manager()

            greeting_message = chatbot_manager.process_greeting(user_id, username)

            if greeting_message:
                # Send greeting to chat
                try:
                    from .chatbot import send_chatbot_message

                    send_chatbot_message(greeting_message)
                    logger.debug(f"Sent greeting to {username}: {greeting_message}")
                except Exception as send_error:
                    logger.error(
                        f"Error sending greeting to {username}: {str(send_error)}"
                    )

                logger.info(f"Greeting sent to {username} (ID: {user_id})")
        except Exception as e:
            logger.error(
                f"Error processing greeting for {username}: {str(e)}", exc_info=True
            )

        # Process message through chatbot system
        try:
            chatbot_manager = get_chatbot_manager()
            logger.debug(
                "Processing chat message for commands: %s",
                safe_console_str(message),
            )
            chatbot_response = chatbot_manager.process_chat_message(msg_dict)

            if chatbot_response:
                response_message, command_name = chatbot_response
                logger.debug(
                    "Command '%s' triggered, response: %s",
                    command_name,
                    safe_console_str(response_message),
                )

                # Send chatbot response back to chat
                try:
                    from .chatbot import send_chatbot_message

                    send_chatbot_message(response_message)
                    logger.debug(
                        f"Chatbot responded to command '{command_name}': {response_message}"
                    )

                    # Log command usage
                    logger.info(
                        f"Command '{command_name}' processed by {username}: {response_message}"
                    )
                except Exception as send_error:
                    logger.error(f"Error sending chatbot response: {str(send_error)}")
            else:
                logger.debug(
                    "No command matched for message: %s",
                    safe_console_str(message),
                )
                try:
                    from .giveaway_manager import get_giveaway_manager

                    if get_giveaway_manager().try_register_entry(msg_dict):
                        logger.debug(
                            "Giveaway entry from %s",
                            msg_dict.get("username", "?"),
                        )
                except Exception as ge:
                    logger.error(
                        "Giveaway entry handling failed: %s", ge, exc_info=True
                    )

            # Process chat message events (after commands)
            try:
                from .chatbot_core import EventType

                chatbot_response = chatbot_manager.process_event(
                    EventType.CHAT_MESSAGE, msg_dict
                )

                if chatbot_response:
                    logger.debug(
                        "Chat message event triggered, response: %s",
                        safe_console_str(chatbot_response),
                    )

                    # Send chatbot response back to chat
                    try:
                        from .chatbot import send_chatbot_message

                        send_chatbot_message(chatbot_response)
                        logger.debug(
                            f"Chatbot responded to chat message event: {chatbot_response}"
                        )
                    except Exception as send_error:
                        logger.error(
                            f"Error sending chatbot chat message event response: {str(send_error)}"
                        )
            except Exception as event_error:
                logger.error(
                    f"Error processing chat message event: {str(event_error)}",
                    exc_info=True,
                )

        except Exception as e:
            logger.error(
                f"Error processing chat message through chatbot: {str(e)}",
                exc_info=True,
            )

        # Send message to WebSocket clients via web engine
        try:
            if not is_allowed_slash_message(message):
                logger.debug(
                    "Skipping Twitch slash command for overlay: %s",
                    safe_console_str(message),
                )
                return
            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                web_engine.web_engine_instance.new_message(msg_dict)
                logger.debug(
                    f"Sent chat message to WebSocket clients: {username}: {message}"
                )
            else:
                logger.warning(
                    "Web engine instance not available for sending chat messages"
                )
        except Exception as e:
            logger.error(
                f"Error sending chat message to WebSocket clients: {str(e)}",
                exc_info=True,
            )

    async def on_chat_notification(self, data: ChannelChatNotificationEvent):
        """Handle channel chat notifications; only watch streaks trigger alerts."""
        self._note_event_received()
        ev = data.event

        notice_raw = getattr(ev, "notice_type", None)
        notice_type = (
            notice_raw.value if hasattr(notice_raw, "value") else str(notice_raw or "")
        )
        if str(notice_type) != "watch_streak":
            return

        watch = getattr(ev, "watch_streak", None)
        if watch is None:
            logger.debug(
                "EventSub chat notification notice_type is watch_streak but "
                "watch_streak data is missing; check twitch_eventsub_patch and twitchapi"
            )
            return

        streak_count = int(getattr(watch, "streak_count", None) or 0)
        if streak_count < 1:
            logger.debug("Watch streak event with invalid streak_count, skipping")
            return

        channel_points_awarded = int(
            getattr(watch, "channel_points_awarded", None) or 0
        )
        username = getattr(ev, "chatter_user_name", None) or "Someone"

        msg_obj = getattr(ev, "message", None)
        user_msg = None
        if msg_obj is not None:
            user_msg = getattr(msg_obj, "text", None)
        if not user_msg:
            user_msg = getattr(ev, "system_message", None) or ""

        logger.debug(
            f"Watch streak from {username}: count={streak_count}, "
            f"points={channel_points_awarded}"
        )

        current_timestamp = time.time()
        alert_id = f"Alert{round(current_timestamp)}"

        alert = alertutils.fetch_streak_alert(streak_count)
        alert_name_for_stats = ""

        if alert:
            alert.username = username
            alert.alert_type = "streak"
            alert.streak_count = streak_count
            alert.channel_points_awarded = channel_points_awarded
            alert.message = user_msg
            alert.alert_id = alert_id
            alert.timestamp = current_timestamp

            alert_processor.ALERT_QUEUE.append(alert)
            alertutils.alert_state_manager.store_completed_alert(
                alert.alert_id, alert.__dict__
            )
            alert_name_for_stats = getattr(alert, "alert_name", None) or ""

            try:
                if (
                    hasattr(web_engine, "web_engine_instance")
                    and web_engine.web_engine_instance
                ):
                    alert_data = {
                        "type": "streak",
                        "alert_type": "streak",
                        "username": username,
                        "streak_count": streak_count,
                        "channel_points_awarded": channel_points_awarded,
                        "message": user_msg,
                        "alert_id": alert.alert_id,
                        "timestamp": alert.timestamp,
                    }
                    web_engine.web_engine_instance.instant_alert(alert_data)
                    logger.debug(f"Sent instant alert for watch streak: {username}")
            except Exception as e:
                logger.error(
                    f"Error sending instant alert for watch streak: {str(e)}",
                    exc_info=True,
                )
        else:
            logger.debug(
                "No streak alert configuration matched; activity feed/stats still recorded"
            )
            streak_storage = {
                "username": username,
                "alert_type": "streak",
                "streak_count": streak_count,
                "channel_points_awarded": channel_points_awarded,
                "message": user_msg,
                "alert_id": alert_id,
                "timestamp": current_timestamp,
                "alert_name": "",
            }
            alertutils.alert_state_manager.store_completed_alert(
                alert_id, streak_storage
            )

        add_alert_to_feed(
            alert_type="Streak",
            message=format_watch_streak_message(username, streak_count),
            badge_type="streak",
            timestamp=str(int(current_timestamp)),
            user_message=user_msg or None,
            alert_id=alert_id,
        )

        try:
            stats_manager = statistics_manager.get_statistics_manager()
            stats_manager.increment_watch_streak_alerts(
                streak_count=streak_count,
                username=username,
                alert_name=alert_name_for_stats,
            )
        except Exception as e:
            logger.debug("Watch streak statistics update failed: %s", e)

    async def on_moderate(self, data: ChannelModerateEvent):
        self._note_event_received()
        logger.debug(f"Moderation event received: {data.event}")
        warning = data.event.warn
        timeout = data.event.timeout
        delete = data.event.delete
        ban = data.event.ban

        # Format moderation data for WebSocket transmission
        moderation_data = {
            "type": "moderation",
            "timestamp": time.time(),
            "moderator_id": data.event.moderator_user_id,
            "moderator_name": data.event.moderator_user_name,
            "actions": {},
        }

        # Add specific moderation actions if they exist
        if warning:
            moderation_data["actions"]["warning"] = {
                "user_id": warning.user_id,
                "user_name": warning.user_name,
                "reason": warning.reason if hasattr(warning, "reason") else None,
            }

        if timeout:
            moderation_data["actions"]["timeout"] = {
                "user_id": timeout.user_id,
                "user_name": timeout.user_name,
                "duration": timeout.duration_seconds
                if hasattr(timeout, "duration_seconds")
                else None,
                "reason": timeout.reason if hasattr(timeout, "reason") else None,
            }

        if delete:
            moderation_data["actions"]["delete"] = {
                "user_id": delete.user_id,
                "user_name": delete.user_name,
                "message_id": delete.message_id
                if hasattr(delete, "message_id")
                else None,
            }

        if ban:
            moderation_data["actions"]["ban"] = {
                "user_id": ban.user_id,
                "user_name": ban.user_name,
                "reason": ban.reason if hasattr(ban, "reason") else None,
                "permanent": not hasattr(ban, "ends_at") or ban.ends_at is None,
            }

        # Send moderation data to WebSocket clients via web engine
        try:
            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                web_engine.web_engine_instance.message_moderation(moderation_data)
                logger.debug(
                    f"Sent moderation event to WebSocket clients: {moderation_data['actions']}"
                )
            else:
                logger.warning(
                    "Web engine instance not available for sending moderation events"
                )
        except Exception as e:
            logger.error(
                f"Error sending moderation event to WebSocket clients: {str(e)}",
                exc_info=True,
            )

    async def on_update(self, data: ChannelUpdateEvent):
        self._note_event_received()
        logger.debug(
            f"Channel update event: Category changed to {data.event.category_name}"
        )
        current_category = data.event.category_name

        # Update the current category in the state manager
        try:
            dataobjects.state_manager.update_twitch_field(
                "current_category", current_category
            )
            logger.debug(f"Updated current category to: {current_category}")
        except Exception as e:
            logger.error(
                f"Error updating current category in state manager: {str(e)}",
                exc_info=True,
            )

        try:
            from .notification_engine import maybe_suggest_game_hook_for_category

            maybe_suggest_game_hook_for_category(current_category)
        except Exception as e:
            logger.debug("game hook suggestion skipped: %s", e)

        # Send instant alert
        try:
            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                alert_data = {
                    "type": "channel_update",
                    "category": current_category,
                    "title": data.event.title,
                    "timestamp": time.time(),
                }
                web_engine.web_engine_instance.instant_alert(alert_data)
                logger.debug(
                    f"Sent instant alert for category change: {current_category}"
                )
        except Exception as e:
            logger.error(
                f"Error sending instant alert for category update: {str(e)}",
                exc_info=True,
            )

        # Send updated Twitch data to WebSocket clients for real-time updates
        try:
            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                # Get the updated Twitch data from state manager
                twitch_data = dataobjects.state_manager.get_twitch_data()

                if twitch_data:
                    # Convert to dict and remove sensitive fields
                    import dataclasses

                    twitch_data_dict = dataclasses.asdict(twitch_data)
                    sensitive_fields = ["auth_token", "refresh_token", "client_secret"]
                    for field in sensitive_fields:
                        if field in twitch_data_dict:
                            twitch_data_dict[field] = ""

                    # Emit the updated data to all connected clients
                    web_engine.web_engine_instance.socketio.emit(
                        "twitch_data_update", twitch_data_dict
                    )
                    logger.debug(
                        f"Broadcasted category update to WebSocket clients: {current_category}"
                    )
                else:
                    logger.warning(
                        "No Twitch data available for broadcasting category update"
                    )
            else:
                logger.warning(
                    "Web engine instance not available for broadcasting category updates"
                )
        except Exception as e:
            logger.error(
                f"Error broadcasting category update to WebSocket clients: {str(e)}",
                exc_info=True,
            )

    async def on_follow(self, data: ChannelFollowEvent):
        self._note_event_received()
        logger.debug(f"New follower: {data.event.user_name}")
        alert = alertutils.fetch_follow_alert()
        alert.username = data.event.user_name
        alert.alert_type = "follow"
        alert.alert_id = f"Alert{round(time.time())}"
        alert.timestamp = time.time()
        alert_processor.ALERT_QUEUE.append(alert)
        # Store completed alert using AlertStateManager
        alertutils.alert_state_manager.store_completed_alert(
            alert.alert_id, alert.__dict__
        )

        # Send instant alert
        try:
            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                alert_data = {
                    "type": "follow",
                    "username": alert.username,
                    "alert_id": alert.alert_id,
                    "timestamp": alert.timestamp,
                }
                web_engine.web_engine_instance.instant_alert(alert_data)
                logger.debug(f"Sent instant alert for follow: {alert.username}")
        except Exception as e:
            logger.error(
                f"Error sending instant alert for follow: {str(e)}", exc_info=True
            )

        # Track follow statistics immediately when event occurs
        try:
            stats_manager = statistics_manager.get_statistics_manager()
            stats_manager.increment_follow_alerts(
                username=alert.username, alert_name=alert.alert_name
            )
            logger.debug(f"Tracked follow statistics for {alert.username}")
        except Exception as e:
            logger.error(f"Error tracking follow statistics: {e}")

        # Process through chatbot system
        try:
            chatbot_manager = get_chatbot_manager()
            follow_data = {
                "username": alert.username,
                "timestamp": alert.timestamp,
                "source": "twitch",
            }
            chatbot_response = chatbot_manager.process_event(
                EventType.FOLLOW, follow_data
            )

            if chatbot_response:
                try:
                    from .chatbot import send_chatbot_message

                    send_chatbot_message(chatbot_response)
                    logger.debug(
                        f"Chatbot responded to follow from {alert.username}: {chatbot_response}"
                    )
                except Exception as send_error:
                    logger.error(
                        f"Error sending chatbot follow response: {str(send_error)}"
                    )

        except Exception as e:
            logger.error(
                f"Error processing follow through chatbot: {str(e)}", exc_info=True
            )

        # Add to activity feed
        add_alert_to_feed(
            alert_type="Follow",
            message=f"{alert.username} just followed!",
            badge_type="follow",
            timestamp=str(int(alert.timestamp)),
            alert_id=alert.alert_id,
        )

    async def on_sub(self, data: ChannelSubscriptionMessageEvent):
        self._note_event_received()
        logger.debug(
            f"Subscription message from {data.event.user_name}, cumulative months: {data.event.cumulative_months}"
        )

        username = data.event.user_name
        tier_str = str(data.event.tier)
        tier = int(tier_str[:-3]) if tier_str else 1  # Default to 1 if tier is weird
        user_msg = data.event.message.text if data.event.message else None
        cumulative_months = data.event.cumulative_months or 1  # Default to 1 if None
        current_timestamp = time.time()

        # If this is a resub (cumulative_months > 1), delegate to the resub handler
        if cumulative_months > 1:
            logger.debug(
                f"Delegating to resub handler for {username} with {cumulative_months} cumulative months"
            )
            await self.on_resub(data)
            return

        # Handle as new subscription (cumulative_months == 1)
        logger.debug(f"Processing as new sub: {username}")
        alert = alertutils.fetch_sub_alert(1)
        alert.username = username
        alert.alert_type = "sub"
        alert.tier = tier
        alert.alert_id = f"Alert{round(current_timestamp)}"
        alert.timestamp = current_timestamp
        alert.message = user_msg or ""  # Use empty string if None
        alert.emotes = _subscription_message_emotes(
            data.event.message if data.event.message else None
        )

        if user_msg:
            register_alert_user_message(str(data.event.user_id), user_msg)

        alert_processor.ALERT_QUEUE.append(alert)
        # Store completed alert using AlertStateManager
        alertutils.alert_state_manager.store_completed_alert(
            alert.alert_id, alert.__dict__
        )

        # Track new subscription statistics
        try:
            stats_manager = statistics_manager.get_statistics_manager()
            stats_manager.increment_new_subs(username=username)
            logger.debug(f"Tracked new subscription statistics for {username}")
        except Exception as e:
            logger.error(f"Error tracking new subscription statistics: {e}")

        # Send instant alert
        try:
            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                alert_data = {
                    "type": "sub",
                    "username": username,
                    "tier": tier,
                    "message": user_msg,
                    "emotes": alert.emotes,
                    "alert_id": alert.alert_id,
                    "timestamp": alert.timestamp,
                }
                web_engine.web_engine_instance.instant_alert(alert_data)
                logger.debug(f"Sent instant alert for sub: {username}")
        except Exception as e:
            logger.error(
                f"Error sending instant alert for sub: {str(e)}", exc_info=True
            )

        # Process through chatbot system
        try:
            chatbot_manager = get_chatbot_manager()
            sub_data = {
                "username": username,
                "tier": tier,
                "months": cumulative_months,
                "message": user_msg,
                "timestamp": alert.timestamp,
                "source": "twitch",
            }
            chatbot_response = chatbot_manager.process_event(
                EventType.SUBSCRIPTION, sub_data
            )

            if chatbot_response:
                try:
                    from .chatbot import send_chatbot_message

                    send_chatbot_message(chatbot_response)
                    logger.debug(
                        f"Chatbot responded to subscription from {username}: {chatbot_response}"
                    )
                except Exception as send_error:
                    logger.error(
                        f"Error sending chatbot subscription response: {str(send_error)}"
                    )

        except Exception as e:
            logger.error(
                f"Error processing subscription through chatbot: {str(e)}",
                exc_info=True,
            )

        # Add to activity feed for new subscription
        add_alert_to_feed(
            alert_type="Sub",
            message=f"{username} subscribed (Tier {tier})!",
            badge_type="sub",
            timestamp=str(int(alert.timestamp)),
            tier=tier,
            user_message=user_msg,
            alert_id=alert.alert_id,
        )

    async def on_resub(self, data: ChannelSubscriptionMessageEvent):
        """Handle resubscription events (cumulative_months > 1)"""
        self._note_event_received()
        logger.debug(
            f"Resubscription from {data.event.user_name} for {data.event.cumulative_months} months"
        )

        username = data.event.user_name
        tier_str = str(data.event.tier)
        tier = int(tier_str[:-3]) if tier_str else 1
        user_msg = data.event.message.text if data.event.message else None
        cumulative_months = data.event.cumulative_months or 1  # Default to 1 if None
        current_timestamp = time.time()

        # Fetch the appropriate resub alert from the database
        alert = alertutils.fetch_resub_alert(cumulative_months)
        alert.username = username
        alert.alert_type = "resub"
        alert.tier = tier
        alert.message = user_msg or ""  # Use empty string if None
        alert.emotes = _subscription_message_emotes(
            data.event.message if data.event.message else None
        )
        alert.months_prepaid = data.event.duration_months
        alert.resub_month = cumulative_months
        alert.alert_id = f"Alert{round(current_timestamp)}"
        alert.timestamp = current_timestamp

        if user_msg:
            register_alert_user_message(str(data.event.user_id), user_msg)

        alert_processor.ALERT_QUEUE.append(alert)
        # Store completed alert using AlertStateManager
        alertutils.alert_state_manager.store_completed_alert(
            alert.alert_id, alert.__dict__
        )

        # Track resubscription statistics
        try:
            stats_manager = statistics_manager.get_statistics_manager()
            stats_manager.increment_resubs(username=username)
            logger.debug(f"Tracked resubscription statistics for {username}")
        except Exception as e:
            logger.error(f"Error tracking resubscription statistics: {e}")

        # Send instant alert
        try:
            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                alert_data = {
                    "type": "resub",
                    "username": username,
                    "tier": tier,
                    "message": user_msg,
                    "emotes": alert.emotes,
                    "cumulative_months": cumulative_months,
                    "alert_id": alert.alert_id,
                    "timestamp": alert.timestamp,
                }
                web_engine.web_engine_instance.instant_alert(alert_data)
                logger.debug(f"Sent instant alert for resub: {username}")
        except Exception as e:
            logger.error(
                f"Error sending instant alert for resub: {str(e)}", exc_info=True
            )

        # Process through chatbot system
        try:
            chatbot_manager = get_chatbot_manager()
            resub_data = {
                "username": username,
                "tier": tier,
                "months": cumulative_months,
                "message": user_msg,
                "timestamp": alert.timestamp,
                "source": "twitch",
            }
            chatbot_response = chatbot_manager.process_event(
                EventType.RESUBSCRIPTION, resub_data
            )

            if chatbot_response:
                try:
                    from .chatbot import send_chatbot_message

                    send_chatbot_message(chatbot_response)
                    logger.debug(
                        f"Chatbot responded to resubscription from {username}: {chatbot_response}"
                    )
                except Exception as send_error:
                    logger.error(
                        f"Error sending chatbot resubscription response: {str(send_error)}"
                    )

        except Exception as e:
            logger.error(
                f"Error processing resubscription through chatbot: {str(e)}",
                exc_info=True,
            )

        # Add to activity feed for resubscription
        add_alert_to_feed(
            alert_type="Resub",
            message=f"{username} resubscribed for {cumulative_months} months (Tier {tier})!",
            badge_type="resub",
            timestamp=str(int(alert.timestamp)),
            tier=tier,
            user_message=user_msg,
            alert_id=alert.alert_id,
        )

    async def on_new_sub(self, data: ChannelSubscribeEvent):
        """Handle channel.subscribe events (subs without a shared chat message)."""
        self._note_event_received()
        if getattr(data.event, "is_gift", False):
            logger.debug(
                "Skipping channel.subscribe for gift recipient %s (handled via gift sub)",
                data.event.user_name,
            )
            return

        logger.debug(f"New subscription from {data.event.user_name}")

        if getattr(data.event, "cumulative_months", 1) > 1:
            logger.debug(
                f"Ignoring new subscription for {data.event.user_name} with {data.event.cumulative_months} cumulative months, handled by subscription_message callback."
            )
            return

        username = data.event.user_name
        tier_str = str(data.event.tier)
        tier = int(tier_str[:-3]) if tier_str else 1  # Default to 1 if tier is weird
        sub_message = getattr(data.event, "message", None)
        user_msg = getattr(sub_message, "text", None) if sub_message else None
        current_timestamp = time.time()

        # Handle as new subscription
        logger.debug(f"Processing as new sub: {username}")
        alert = alertutils.fetch_sub_alert(1)
        if alert is None:
            logger.warning("No sub alert configured, using default for %s", username)
            alert = alertutils.AlertObj()
            alert.alert_type = "sub"
        alert.username = username
        alert.alert_type = "sub"
        alert.tier = tier
        alert.alert_id = f"Alert{round(current_timestamp)}"
        alert.timestamp = current_timestamp
        alert.message = user_msg or ""  # Use empty string if None
        alert.emotes = _subscription_message_emotes(sub_message)

        alert_processor.ALERT_QUEUE.append(alert)
        # Store completed alert using AlertStateManager
        alertutils.alert_state_manager.store_completed_alert(
            alert.alert_id, alert.__dict__
        )

        # Track new subscription statistics
        try:
            stats_manager = statistics_manager.get_statistics_manager()
            stats_manager.increment_new_subs(username=username)
            logger.debug(f"Tracked new subscription statistics for {username}")
        except Exception as e:
            logger.error(f"Error tracking new subscription statistics: {e}")

        # Send instant alert
        try:
            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                alert_data = {
                    "type": "sub",
                    "username": username,
                    "tier": tier,
                    "message": user_msg,
                    "emotes": alert.emotes,
                    "alert_id": alert.alert_id,
                    "timestamp": alert.timestamp,
                }
                web_engine.web_engine_instance.instant_alert(alert_data)
                logger.debug(f"Sent instant alert for sub: {username}")
        except Exception as e:
            logger.error(
                f"Error sending instant alert for sub: {str(e)}", exc_info=True
            )

        # Process through chatbot system
        try:
            chatbot_manager = get_chatbot_manager()
            sub_data = {
                "username": username,
                "tier": tier,
                "months": 1,  # New subscription is always 1 month
                "message": user_msg,
                "timestamp": alert.timestamp,
                "source": "twitch",
            }
            chatbot_response = chatbot_manager.process_event(
                EventType.SUBSCRIPTION, sub_data
            )

            if chatbot_response:
                try:
                    from .chatbot import send_chatbot_message

                    send_chatbot_message(chatbot_response)
                    logger.debug(
                        f"Chatbot responded to subscription from {username}: {chatbot_response}"
                    )
                except Exception as send_error:
                    logger.error(
                        f"Error sending chatbot subscription response: {str(send_error)}"
                    )

        except Exception as e:
            logger.error(
                f"Error processing subscription through chatbot: {str(e)}",
                exc_info=True,
            )

        # Add to activity feed for new subscription
        add_alert_to_feed(
            alert_type="Sub",
            message=f"{username} subscribed (Tier {tier})!",
            badge_type="sub",
            timestamp=str(int(alert.timestamp)),
            tier=tier,
            user_message=user_msg,
            alert_id=alert.alert_id,
        )

    async def on_sub_gift(self, data: ChannelSubscriptionGiftEvent):
        self._note_event_received()
        logger.debug(
            f"Gift subscription from {data.event.user_name} for {data.event.total} months"
        )
        alert = alertutils.fetch_giftsub_alert(data.event.total)
        if alert is None:
            logger.warning(
                "No giftsub alert configured for qty %s, using default",
                data.event.total,
            )
            alert = alertutils.AlertObj()
            alert.alert_type = "giftsub"
        gifter_name = (
            data.event.user_name if not data.event.is_anonymous else "Anonymous"
        )
        alert.username = gifter_name or "Anonymous"
        alert.anonymous = bool(data.event.is_anonymous)
        alert.alert_type = "giftsub"
        alert.tier = int(str(data.event.tier)[:-3])
        alert.gift_qty = int(str(data.event.total))
        alert.alert_id = f"Alert{round(time.time())}"
        alert.timestamp = time.time()
        alert_processor.ALERT_QUEUE.append(alert)
        # Store completed alert using AlertStateManager
        alertutils.alert_state_manager.store_completed_alert(
            alert.alert_id, alert.__dict__
        )

        # Send instant alert
        try:
            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                alert_data = {
                    "type": "giftsub",
                    "username": gifter_name,
                    "anonymous": alert.anonymous,
                    "tier": alert.tier,
                    "gift_qty": alert.gift_qty,
                    "alert_id": alert.alert_id,
                    "timestamp": alert.timestamp,
                }
                web_engine.web_engine_instance.instant_alert(alert_data)
                logger.debug(f"Sent instant alert for giftsub: {gifter_name}")
        except Exception as e:
            logger.error(
                f"Error sending instant alert for giftsub: {str(e)}", exc_info=True
            )

        # Process through chatbot system
        try:
            chatbot_manager = get_chatbot_manager()
            giftsub_data = {
                "username": gifter_name,
                "tier": alert.tier,
                "amount": alert.gift_qty,
                "timestamp": alert.timestamp,
                "source": "twitch",
            }
            chatbot_response = chatbot_manager.process_event(
                EventType.GIFT_SUBSCRIPTION, giftsub_data
            )

            if chatbot_response:
                try:
                    from .chatbot import send_chatbot_message

                    send_chatbot_message(chatbot_response)
                    logger.debug(
                        f"Chatbot responded to gift subscription from {gifter_name}: {chatbot_response}"
                    )
                except Exception as send_error:
                    logger.error(
                        f"Error sending chatbot gift subscription response: {str(send_error)}"
                    )

        except Exception as e:
            logger.error(
                f"Error processing gift subscription through chatbot: {str(e)}",
                exc_info=True,
            )

        # Track gift sub statistics immediately when event occurs
        try:
            stats_manager = statistics_manager.get_statistics_manager()
            stats_manager.increment_gift_subs(
                gift_quantity=alert.gift_qty,
                username=gifter_name,
                alert_name=alert.alert_name,
            )
            logger.debug(
                f"Tracked gift sub statistics for {gifter_name}: {alert.gift_qty} subs"
            )
        except Exception as e:
            logger.error(f"Error tracking gift sub statistics: {e}")

        # Add to activity feed
        add_alert_to_feed(
            alert_type="Giftsub",
            message=f"{gifter_name} gifted {alert.gift_qty} Tier {alert.tier} subs!",
            badge_type="giftsub",
            timestamp=str(int(alert.timestamp)),
            tier=alert.tier,
            alert_id=alert.alert_id,
        )

    async def on_bits_use(self, data: ChannelBitsUseEvent):
        self._note_event_received()
        logger.debug(f"Bits cheered by {data.event.user_name}: {data.event.bits}")
        event_type = str(getattr(data.event, "type", "") or "").lower()
        bits_amount = int(str(data.event.bits))

        if (
            event_type == "cheer"
            and hasattr(data.event, "message")
            and data.event.message
        ):
            user_id = str(getattr(data.event, "user_id", "") or "anonymous")
            msg = data.event.message
            text = getattr(msg, "text", "") or ""
            fragments = getattr(msg, "fragments", None)
            serialized_fragments = (
                serialize_bits_message_fragments(fragments) if fragments else None
            )
            emote_positions = extract_emotes_positions_from_fragments(text, fragments)
            _store_cheer_bits_message(
                user_id, bits_amount, text, serialized_fragments, emote_positions
            )
            logger.debug(
                f"Cached cheer bits message for {user_id} ({bits_amount} bits)"
            )
            return

        alert = alertutils.fetch_cheer_alert(data.event.bits)
        is_anonymous = bool(getattr(data.event, "is_anonymous", False))
        username_display = (
            data.event.user_name if not is_anonymous else "Anonymous"
        )
        alert.username = username_display or "Anonymous"
        alert.anonymous = is_anonymous
        alert.alert_type = "bit"
        alert.amt_cheered = bits_amount
        alert.alert_id = f"Alert{round(time.time())}"
        alert.timestamp = time.time()
        if hasattr(data.event, "message") and data.event.message:
            _apply_bits_message_to_alert(alert, data.event.message)
        if (
            hasattr(data.event, "power_up")
            and data.event.power_up
            and hasattr(data.event.power_up, "type")
        ):
            alert_processor.ALERT_QUEUE.append(alert)
            alertutils.alert_state_manager.store_completed_alert(
                alert.alert_id, alert.__dict__
            )
            # Determine activity feed message based on power_up field
            power_up_type = str(data.event.power_up.type).replace("_", " ").title()
            activity_message = f"{alert.username} has redeemed {power_up_type} for {alert.amt_cheered} bits!"

            # Send instant alert
            try:
                if (
                    hasattr(web_engine, "web_engine_instance")
                    and web_engine.web_engine_instance
                ):
                    alert_data = {
                        "type": "bits_use",
                        "username": alert.username,
                        "anonymous": alert.anonymous,
                        "amt_cheered": alert.amt_cheered,
                        "message": alert.message,
                        "fragments": alert.fragments,
                        "emotes": alert.emotes,
                        "power_up_type": power_up_type,
                        "alert_id": alert.alert_id,
                        "timestamp": alert.timestamp,
                    }
                    web_engine.web_engine_instance.instant_alert(alert_data)
                    logger.debug(f"Sent instant alert for bits_use: {alert.username}")
            except Exception as e:
                logger.error(
                    f"Error sending instant alert for bits_use: {str(e)}", exc_info=True
                )

            # Process through chatbot system
            try:
                chatbot_manager = get_chatbot_manager()
                bits_data = {
                    "username": alert.username,
                    "amount": alert.amt_cheered,
                    "message": data.event.message
                    if hasattr(data.event, "message")
                    else "",
                    "timestamp": alert.timestamp,
                    "source": "twitch",
                }
                chatbot_response = chatbot_manager.process_event(
                    EventType.BITS, bits_data
                )

                if chatbot_response:
                    try:
                        from .chatbot import send_chatbot_message

                        send_chatbot_message(chatbot_response)
                        logger.debug(
                            f"Chatbot responded to bits from {alert.username}: {chatbot_response}"
                        )
                    except Exception as send_error:
                        logger.error(
                            f"Error sending chatbot bits response: {str(send_error)}"
                        )

            except Exception as e:
                logger.error(
                    f"Error processing bits through chatbot: {str(e)}", exc_info=True
                )

            # Track bit statistics immediately when power-up event occurs
            try:
                stats_manager = statistics_manager.get_statistics_manager()
                stats_manager.increment_bit_alerts(
                    bit_amount=alert.amt_cheered,
                    username=alert.username,
                    alert_name=alert.alert_name,
                )
                logger.debug(
                    f"Tracked power-up bit statistics for {alert.username}: {alert.amt_cheered} bits"
                )
            except Exception as e:
                logger.error(f"Error tracking power-up bit statistics: {e}")

            # Add to activity feed
            add_alert_to_feed(
                alert_type="Bits",
                message=activity_message,
                badge_type="bits",
                timestamp=str(int(alert.timestamp)),
                user_message="",
                alert_id=alert.alert_id,
            )

    async def on_cheer(self, data: ChannelCheerEvent):
        self._note_event_received()
        logger.debug(f"Bits cheered by {data.event.user_name}: {data.event.bits}")
        alert = alertutils.fetch_cheer_alert(data.event.bits)
        is_anonymous = bool(getattr(data.event, "is_anonymous", False))
        username_display = (
            data.event.user_name if not is_anonymous else "Anonymous"
        )
        alert.username = username_display or "Anonymous"
        alert.anonymous = is_anonymous
        alert.alert_type = "bit"
        alert.amt_cheered = int(str(data.event.bits))
        cache_user_id = (
            "anonymous"
            if data.event.is_anonymous
            else str(getattr(data.event, "user_id", "") or "anonymous")
        )
        cached = _pop_cheer_bits_message(cache_user_id, alert.amt_cheered)
        if cached:
            alert.message = cached.get("text") or data.event.message or ""
            alert.fragments = cached.get("fragments")
            alert.emotes = cached.get("emotes")
        else:
            alert.message = data.event.message or ""
            alert.fragments = None
            alert.emotes = None
        if alert.message:
            register_alert_user_message(cache_user_id, alert.message)
        alert.alert_id = f"Alert{round(time.time())}"
        alert.timestamp = time.time()
        alert_processor.ALERT_QUEUE.append(alert)
        # Store completed alert using AlertStateManager
        alertutils.alert_state_manager.store_completed_alert(
            alert.alert_id, alert.__dict__
        )

        # Send instant alert
        try:
            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                alert_data = {
                    "type": "cheer",
                    "username": username_display,
                    "anonymous": alert.anonymous,
                    "amt_cheered": alert.amt_cheered,
                    "message": alert.message,
                    "fragments": alert.fragments,
                    "emotes": alert.emotes,
                    "alert_id": alert.alert_id,
                    "timestamp": alert.timestamp,
                }
                web_engine.web_engine_instance.instant_alert(alert_data)
                logger.debug(f"Sent instant alert for cheer: {username_display}")
        except Exception as e:
            logger.error(
                f"Error sending instant alert for cheer: {str(e)}", exc_info=True
            )

        # Track bit statistics immediately when event occurs
        try:
            stats_manager = statistics_manager.get_statistics_manager()
            stats_manager.increment_bit_alerts(
                bit_amount=alert.amt_cheered,
                username=username_display,
                alert_name=alert.alert_name,
            )
            logger.debug(
                f"Tracked bit statistics for {username_display}: {alert.amt_cheered} bits"
            )
        except Exception as e:
            logger.error(f"Error tracking bit statistics: {e}")

        activity_message = f"{username_display} cheered {alert.amt_cheered} bits!"

        # Process through chatbot system
        try:
            chatbot_manager = get_chatbot_manager()
            cheer_data = {
                "username": username_display,
                "amount": alert.amt_cheered,
                "message": alert.message,
                "timestamp": alert.timestamp,
                "source": "twitch",
            }
            chatbot_response = chatbot_manager.process_event(EventType.BITS, cheer_data)

            if chatbot_response:
                try:
                    from .chatbot import send_chatbot_message

                    send_chatbot_message(chatbot_response)
                    logger.debug(
                        f"Chatbot responded to cheer from {username_display}: {chatbot_response}"
                    )
                except Exception as send_error:
                    logger.error(
                        f"Error sending chatbot cheer response: {str(send_error)}"
                    )

        except Exception as e:
            logger.error(
                f"Error processing cheer through chatbot: {str(e)}", exc_info=True
            )

        # Add to activity feed
        add_alert_to_feed(
            alert_type="Bits",
            message=activity_message,
            badge_type="bits",
            timestamp=str(int(alert.timestamp)),
            user_message=alert.message,
            alert_id=alert.alert_id,
        )

    async def on_raid(self, data: ChannelRaidEvent):
        self._note_event_received()
        logger.debug(
            f"Raid from {data.event.from_broadcaster_user_name} with {data.event.viewers} viewers"
        )
        alert = alertutils.fetch_raid_alert(data.event.viewers)
        alert.username = data.event.from_broadcaster_user_name
        alert.alert_type = "raid"
        alert.raider_count = int(str(data.event.viewers))
        alert.alert_id = f"Alert{round(time.time())}"
        alert.timestamp = time.time()

        game_name = None
        try:
            channel_infos = await self.twitch.get_channel_information(
                data.event.from_broadcaster_user_id
            )
            if channel_infos and channel_infos[0].game_name:
                game_name = channel_infos[0].game_name
                alert.game_name = game_name
        except Exception as e:
            logger.warning(
                f"Could not fetch channel info for raider {alert.username}: {e}"
            )

        alert_processor.ALERT_QUEUE.append(alert)
        # Store completed alert using AlertStateManager
        alertutils.alert_state_manager.store_completed_alert(
            alert.alert_id, alert.__dict__
        )

        # Send instant alert
        try:
            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                alert_data = {
                    "type": "raid",
                    "username": alert.username,
                    "raider_count": alert.raider_count,
                    "alert_id": alert.alert_id,
                    "timestamp": alert.timestamp,
                }
                web_engine.web_engine_instance.instant_alert(alert_data)
                logger.debug(f"Sent instant alert for raid: {alert.username}")
        except Exception as e:
            logger.error(
                f"Error sending instant alert for raid: {str(e)}", exc_info=True
            )

        # Track raid statistics immediately when event occurs
        try:
            stats_manager = statistics_manager.get_statistics_manager()
            stats_manager.increment_raids(
                username=alert.username, alert_name=alert.alert_name
            )
            logger.debug(
                f"Tracked raid statistics for {alert.username}: {alert.raider_count} viewers"
            )
        except Exception as e:
            logger.error(f"Error tracking raid statistics: {e}")

        # Send shoutout to the raider
        try:
            shoutout_game = game_name or "Unknown"
            shoutout_message = f"HEY CHAT! Check out @{data.event.from_broadcaster_user_name} 's channel: https://twitch.tv/{data.event.from_broadcaster_user_name} . How was {shoutout_game}?"
            if self._raid_shoutout_enabled("helix"):
                await self.send_shoutout(data.event.from_broadcaster_user_id)
            if self._raid_shoutout_enabled("chat"):
                from .chatbot import send_chatbot_message

                send_chatbot_message(shoutout_message)
            logger.debug(f"Sent shoutout to {alert.username}")
        except Exception as e:
            logger.error(
                f"Failed to send shoutout to {alert.username}: {str(e)}", exc_info=True
            )

        # Process through chatbot system
        try:
            chatbot_manager = get_chatbot_manager()
            raid_data = {
                "username": alert.username,
                "viewer_count": alert.raider_count,
                "timestamp": alert.timestamp,
                "source": "twitch",
            }
            chatbot_response = chatbot_manager.process_event(EventType.RAID, raid_data)

            if chatbot_response:
                try:
                    from .chatbot import send_chatbot_message

                    send_chatbot_message(chatbot_response)
                    logger.debug(
                        f"Chatbot responded to raid from {alert.username}: {chatbot_response}"
                    )
                except Exception as send_error:
                    logger.error(
                        f"Error sending chatbot raid response: {str(send_error)}"
                    )

        except Exception as e:
            logger.error(
                f"Error processing raid through chatbot: {str(e)}", exc_info=True
            )

        # Add to activity feed
        add_alert_to_feed(
            alert_type="Raid",
            message=format_raid_activity_message(
                alert.username, alert.raider_count, game_name
            ),
            badge_type="raid",
            timestamp=str(int(alert.timestamp)),
            alert_id=alert.alert_id,
        )

    async def no_point_reward_setup(self, data):
        # Store alert in AlertStateManager
        alert = alertutils.AlertObj()
        alert.alert_type = "point"
        alert.alert_name = data.event.reward.title
        alert.twitch_reward_id = data.event.reward.id
        alert.message = data.event.user_input
        alert.username = data.event.user_name
        alert.alert_id = f"Alert{round(time.time())}"
        alert.timestamp = time.time()
        alert.point_cost = int(data.event.reward.cost or 0)
        alertutils.alert_state_manager.store_completed_alert(
            alert.alert_id, alert.__dict__
        )

        dedicated = match_point_reward_dedicated_template(alert.alert_name)
        send_instant = True
        if dedicated and dedicated.get("queued"):
            hold = alertutils.AlertObj()
            hold.alert_type = "point"
            hold.alert_name = alert.alert_name
            hold.twitch_reward_id = alert.twitch_reward_id
            hold.message = alert.message
            hold.username = alert.username
            hold.alert_id = alert.alert_id
            hold.timestamp = alert.timestamp
            hold.point_cost = int(data.event.reward.cost or 0)
            hold.duration = float(dedicated["duration_seconds"])
            hold.hold_queue_only = True
            hold.enable_alert = False
            alert_processor.ALERT_QUEUE.append(hold)
            send_instant = False
            logger.debug(
                "Queued dedicated template point redemption (no DB alert): %s",
                alert.alert_name,
            )

        if send_instant:
            try:
                if (
                    hasattr(web_engine, "web_engine_instance")
                    and web_engine.web_engine_instance
                ):
                    alert_data = {
                        "type": "points",
                        "username": alert.username,
                        "alert_name": alert.alert_name,
                        "message": alert.message,
                        "twitch_reward_id": alert.twitch_reward_id,
                        "alert_id": alert.alert_id,
                        "timestamp": alert.timestamp,
                        "point_cost": int(data.event.reward.cost or 0),
                    }
                    web_engine.web_engine_instance.instant_alert(alert_data)
                    logger.debug(
                        "Sent instant alert for unconfigured points redemption: %s",
                        alert.username,
                    )
            except Exception as e:
                logger.error(
                    f"Error sending instant alert for unconfigured points redemption: {str(e)}",
                    exc_info=True,
                )

        # Add to activity feed
        add_alert_to_feed(
            alert_type="Points",
            message=f"{data.event.user_name} redeemed '{data.event.reward.title}'!",
            badge_type="points",
            timestamp=str(int(time.time())),
            user_message=data.event.user_input,
            alert_id=alert.alert_id,
            point_cost=int(data.event.reward.cost or 0),
        )

    async def on_points(self, data: ChannelPointsCustomRewardRedemptionAddEvent):
        self._note_event_received()
        logger.debug(
            f"Channel points redemption by {data.event.user_name} for {data.event.reward.title}"
        )
        alert = alertutils.fetch_point_alert(data.event.reward.id)
        if alert is None:
            await self.no_point_reward_setup(data)
            return
        alert.username = data.event.user_name
        alert.alert_name = data.event.reward.title
        alert.alert_type = "point"
        alert.message = data.event.user_input
        alert.twitch_reward_id = data.event.reward.id
        alert.alert_id = f"Alert{round(time.time())}"
        alert.timestamp = time.time()
        alert.point_cost = data.event.reward.cost
        if alert.enable_alert:
            dedicated = match_point_reward_dedicated_template(alert.alert_name)
            if dedicated and dedicated.get("queued"):
                gif_ok = (
                    alert.gif_dir
                    and str(alert.gif_dir).strip()
                    and alert.gif_name
                    and str(alert.gif_name).strip()
                )
                audio_ok = alert.single_audio_dir and alert.single_audio_name
                if not gif_ok and not audio_ok and not alert.randomized:
                    alert.hold_queue_only = True
                    alert.duration = float(dedicated["duration_seconds"])
            alert_processor.ALERT_QUEUE.append(alert)
        # Store completed alert using AlertStateManager
        alertutils.alert_state_manager.store_completed_alert(
            alert.alert_id, alert.__dict__
        )

        # Send instant alert
        try:
            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                alert_data = {
                    "type": "points",
                    "username": alert.username,
                    "alert_name": alert.alert_name,
                    "message": alert.message,
                    "twitch_reward_id": alert.twitch_reward_id,
                    "alert_id": alert.alert_id,
                    "timestamp": alert.timestamp,
                    "point_cost": int(alert.point_cost or 0),
                }
                web_engine.web_engine_instance.instant_alert(alert_data)
                logger.debug(
                    f"Sent instant alert for points redemption: {alert.username}"
                )
        except Exception as e:
            logger.error(
                f"Error sending instant alert for points redemption: {str(e)}",
                exc_info=True,
            )

        # Track point redemption statistics immediately when event occurs
        try:
            stats_manager = statistics_manager.get_statistics_manager()
            stats_manager.increment_point_alerts(
                username=alert.username,
                alert_name=alert.alert_name,
                points_amount=int(alert.point_cost or 0),
            )
            # Track total channel points redeemed
            stats_manager.increment_channel_points_redeemed(
                points_amount=alert.point_cost, username=alert.username
            )
            logger.debug(f"Tracked point redemption statistics for {alert.username}")
        except Exception as e:
            logger.error(f"Error tracking point redemption statistics: {e}")

        # Add to activity feed
        add_alert_to_feed(
            alert_type="Points",
            message=f"{alert.username} redeemed '{alert.alert_name}'!",
            badge_type="points",
            timestamp=str(int(alert.timestamp)),
            user_message=alert.message,
            alert_id=alert.alert_id,
            point_cost=int(alert.point_cost or 0),
        )

    async def on_hype_train_start(
        self, data: HypeTrainEvent
    ):  # data.event is HypeTrainBeginEventData
        self._note_event_received()
        # Correctly access conductor_user_name for HypeTrainBeginEventData
        conductor_name = getattr(data.event, "conductor_user_name", None) or "A viewer"
        level = data.event.level
        logger.debug(f"Hype train started by {conductor_name} at level {level}")

        current_ts = time.time()

        # Send instant alert
        try:
            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                alert_data = {
                    "type": "hype_train_start",
                    "conductor_name": conductor_name,
                    "level": level,
                    "timestamp": current_ts,
                }
                web_engine.web_engine_instance.instant_alert(alert_data)
                logger.debug(
                    f"Sent instant alert for hype train start: {conductor_name}"
                )
        except Exception as e:
            logger.error(
                f"Error sending instant alert for hype train start: {str(e)}",
                exc_info=True,
            )

        # For activity feed - Note: hype train start doesn't create an AlertObj, so no alert_id available
        add_alert_to_feed(
            alert_type="Hype Train",
            message=f"Hype Train started by {conductor_name}! Level {level}.",
            badge_type="hype_train",
            timestamp=str(int(current_ts)),
        )

        # Emit hype train active websocket event
        if (
            hasattr(web_engine, "web_engine_instance")
            and web_engine.web_engine_instance
        ):
            hype_train_data = {
                "level": level,
                "conductor_name": conductor_name,
                "event_type": "start",
            }
            web_engine.web_engine_instance.hype_train_status_update(
                True, hype_train_data
            )

    async def on_hype_train_progress(self, data: HypeTrainEvent):
        self._note_event_received()
        logger.debug(
            f"Hype train progress: Level {data.event.level}, Progress: {data.event.progress}/{data.event.goal}"
        )
        # username = data.event.user_name # HypeTrainEvent does not have user_name directly

        # Send instant alert
        try:
            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                alert_data = {
                    "type": "hype_train_progress",
                    "level": data.event.level,
                    "progress": data.event.progress,
                    "goal": data.event.goal,
                    "timestamp": time.time(),
                }
                web_engine.web_engine_instance.instant_alert(alert_data)
                logger.debug(
                    f"Sent instant alert for hype train progress: Level {data.event.level}"
                )
        except Exception as e:
            logger.error(
                f"Error sending instant alert for hype train progress: {str(e)}",
                exc_info=True,
            )

        # Emit hype train active websocket event with progress data
        if (
            hasattr(web_engine, "web_engine_instance")
            and web_engine.web_engine_instance
        ):
            hype_train_data = {
                "level": data.event.level,
                "progress": data.event.progress,
                "goal": data.event.goal,
                "event_type": "progress",
            }
            web_engine.web_engine_instance.hype_train_status_update(
                True, hype_train_data
            )

    async def on_hype_train_end(
        self, data: HypeTrainEndEvent
    ):  # data.event is HypeTrainEndEventData
        self._note_event_received()
        logger.debug(f"Hype train ended at level {data.event.level}")
        event_data = data.event
        level = event_data.level
        total_contributions = event_data.total
        current_ts = time.time()  # Use a consistent timestamp

        alert = alertutils.AlertObj()
        alert.alert_type = "hype_train_end"
        alert.hype_train_level = int(
            str(data.event.level)
        )  # data.event.level is already int
        alert.alert_id = f"Alert{round(current_ts)}"
        alert.timestamp = current_ts  # Set the timestamp for the AlertObj
        # Store completed alert using AlertStateManager
        alertutils.alert_state_manager.store_completed_alert(
            alert.alert_id, alert.__dict__
        )
        # Optional: if Hype Train End should trigger an OBS alert:
        # alert_processor.ALERT_QUEUE.append(alert)

        # Add to activity feed
        top_contrib_msgs = []
        if event_data.top_contributions:
            for tc in event_data.top_contributions[:2]:  # Show top 2 contributors
                # tc.type is the contribution type string
                contrib_type = str(tc.type) if hasattr(tc, "type") else "UNKNOWN"
                top_contrib_msgs.append(f"{tc.user_name} ({tc.total} {contrib_type})")
        user_msg_details = (
            f"Top: {', '.join(top_contrib_msgs)}" if top_contrib_msgs else None
        )

        # Send instant alert
        try:
            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                alert_data = {
                    "type": "hype_train_end",
                    "level": level,
                    "total_contributions": total_contributions,
                    "top_contributions": top_contrib_msgs,
                    "alert_id": alert.alert_id,
                    "timestamp": current_ts,
                }
                web_engine.web_engine_instance.instant_alert(alert_data)
                logger.debug(f"Sent instant alert for hype train end: Level {level}")
        except Exception as e:
            logger.error(
                f"Error sending instant alert for hype train end: {str(e)}",
                exc_info=True,
            )

        add_alert_to_feed(
            alert_type="Hype Train",
            message=f"Hype Train ended at Level {level}!",
            badge_type="hype_train",
            timestamp=str(int(current_ts)),
            user_message=user_msg_details,
            alert_id=alert.alert_id,
        )

        # Track hype train completion statistics immediately when event occurs
        try:
            stats_manager = statistics_manager.get_statistics_manager()
            logger.info(
                f"Tracking hype train completion - Level from Twitch: {level} (type: {type(level).__name__})"
            )
            stats_manager.increment_hype_train_completion(level)
            logger.info(f"Successfully tracked hype train completion at level {level}")
        except Exception as e:
            logger.error(
                f"Error tracking hype train completion statistics: {e}", exc_info=True
            )

        # Emit hype train inactive websocket event
        if (
            hasattr(web_engine, "web_engine_instance")
            and web_engine.web_engine_instance
        ):
            hype_train_data = {
                "level": level,
                "total_contributions": total_contributions,
                "top_contributions": top_contrib_msgs,
                "event_type": "end",
            }
            web_engine.web_engine_instance.hype_train_status_update(
                False, hype_train_data
            )

    async def send_shoutout(self, raider_id: str):
        """Send a shoutout to a raider using the Twitch API"""
        try:
            if not self.user_id:
                logger.warning("Cannot send shoutout - no user ID available")
                return False

            if not raider_id:
                logger.warning("Cannot send shoutout - no raider ID provided")
                return False

            url = f"https://api.twitch.tv/helix/chat/shoutouts"
            params = {
                "from_broadcaster_id": self.user_id,
                "to_broadcaster_id": raider_id,
                "moderator_id": self.user_id,
            }

            response = await self.generic_api_call(url, "POST", params=params)
            logger.info(f"Successfully sent shoutout to broadcaster ID: {raider_id}")
            return True

        except Exception as e:
            logger.error(
                f"Error sending shoutout to broadcaster ID {raider_id}: {str(e)}",
                exc_info=True,
            )
            return False

    def start_health_check(self):
        """Start the health check thread"""
        if self.health_check_thread is None or not self.health_check_thread.is_alive():
            self.health_check_thread = threading.Thread(target=self._health_check_loop)
            self.health_check_thread.daemon = True
            self.health_check_thread.start()
            logger.debug("Started Twitch connection health check thread")

    def is_eventsub_live(self) -> bool:
        """True when EventSub reports an active websocket session."""
        return (
            self.eventsub is not None
            and hasattr(self.eventsub, "active_session")
            and self.eventsub.active_session is not None
            and self.is_connected
        )

    def stop_health_check(self):
        """Stop the health check thread"""
        if self.health_check_thread and self.health_check_thread.is_alive():
            # The thread will exit on its own when the main thread exits
            # since it's a daemon thread
            logger.debug("Stopping Twitch connection health check thread")

    def cancel_oauth(self) -> None:
        """Stop an in-flight OAuth callback server for this integration."""
        if self.authenticator is not None:
            stop_active_oauth()
        self.authenticator = None

    def _health_check_loop(self):
        """Background thread that periodically checks connection health and token expiry"""
        from .shutdown import is_shutdown_in_progress

        global twitch_connected
        while True:
            try:
                if is_shutdown_in_progress():
                    break

                # Check if connection is alive
                if (
                    self.eventsub
                    and hasattr(self.eventsub, "active_session")
                    and self.eventsub.active_session is not None
                ):
                    self.last_health_check = datetime.now()
                    self.is_connected = True
                    twitch_connected = True
                    if self.last_event_time is not None:
                        idle_s = (datetime.now() - self.last_event_time).total_seconds()
                        logger.debug(
                            "Twitch connection health check passed (%.0fs since last event)",
                            idle_s,
                        )
                        # Silent death: the websocket session stays open (so this
                        # check keeps passing) but no events arrive because the
                        # subscriptions were revoked without a revocation message
                        # reaching us. Rebuild after a long idle stretch.
                        if (
                            idle_s > self.event_staleness_timeout
                            and self._auto_reconnect_enabled()
                            and not is_shutdown_in_progress()
                        ):
                            logger.warning(
                                "No Twitch events for %.0fs while session is open; "
                                "rebuilding EventSub (possible silent death)",
                                idle_s,
                            )
                            # Reset so a genuinely quiet channel only triggers one
                            # rebuild per staleness window instead of looping.
                            self.last_event_time = datetime.now()
                            self._schedule_eventsub_rebuild("event staleness")
                    else:
                        logger.debug(
                            "Twitch connection health check passed (no events yet)"
                        )
                else:
                    logger.warning(
                        "Twitch EventSub session missing or inactive; "
                        "connection monitor will attempt reconnect"
                    )
                    self.is_connected = False
                    twitch_connected = False

                # Proactive token expiry check
                if self.auth_token and self.refresh_token and self.is_token_expired():
                    logger.info(
                        "Token expired during health check, attempting proactive refresh"
                    )
                    try:
                        # Create a new event loop for the async refresh
                        import asyncio

                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            refresh_success = loop.run_until_complete(
                                self.refresh_auth_token()
                            )
                            if refresh_success:
                                logger.info(
                                    "Token successfully refreshed during health check"
                                )

                                # IMPORTANT: Sync refreshed tokens back to state manager
                                self._sync_tokens_to_state_manager()

                                # Rebuild the EventSub connection with the new
                                # token. Subscriptions were created with the old
                                # token and Twitch revokes them once it expires,
                                # so set_user_authentication alone is not enough —
                                # we must re-subscribe to keep receiving events.
                                if not is_shutdown_in_progress():
                                    logger.info(
                                        "Reconnecting EventSub to re-subscribe with refreshed token"
                                    )
                                    self.reconnect()
                            else:
                                logger.warning(
                                    "Token refresh failed during health check - authentication may be required"
                                )
                        finally:
                            loop.close()
                    except Exception as e:
                        logger.error(
                            f"Error refreshing token during health check: {str(e)}",
                            exc_info=True,
                        )

                # Sleep for the check interval
                time.sleep(self.health_check_interval)
            except Exception as e:
                logger.error(
                    f"Error in Twitch health check thread: {str(e)}", exc_info=True
                )
                self.is_connected = False
                twitch_connected = False
                # Sleep a bit before retrying
                time.sleep(10)

    def _sync_tokens_to_state_manager(self):
        """Sync current tokens back to state manager - used after token refresh"""
        try:
            logger.debug("Syncing refreshed tokens to state manager")
            twitch_data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_token": self.auth_token,
                "refresh_token": self.refresh_token,
                "user_id": self.user_id,
                "token_expiry": self.token_expiry.isoformat()
                if self.token_expiry
                else "",
            }

            dataobjects.state_manager.set_twitch_data(twitch_data)
            save_success = dataobjects.state_manager.save_changes()

            if save_success:
                logger.info("Successfully synced refreshed tokens to state manager")
            else:
                logger.warning("Failed to save refreshed tokens to state manager")

        except Exception as e:
            logger.error(
                f"Error syncing tokens to state manager: {str(e)}", exc_info=True
            )

    async def _on_eventsub_revocation(self, data) -> None:
        """Handle an EventSub subscription revocation.

        Twitch revokes subscriptions when the user access token they were created
        with expires or is revoked. The websocket session stays open (so the
        active_session health check keeps passing), but no events arrive — the
        silent death this app used to hit after a few hours. We refresh the token
        and rebuild every subscription, debounced so a burst of revocations
        (one per subscription) only triggers a single reconnect.
        """
        from .shutdown import is_shutdown_in_progress

        logger.warning("EventSub subscription revoked by Twitch: %s", data)

        if is_shutdown_in_progress():
            return

        now = datetime.now()
        if (
            self._last_revocation_reconnect
            and (now - self._last_revocation_reconnect).total_seconds() < 30
        ):
            logger.debug("Revocation reconnect already in progress, skipping duplicate")
            return
        self._last_revocation_reconnect = now

        # Most revocations are authorization_revoked (token expired/invalid), so
        # refresh before reconnecting; reconnect() also re-stages auth as a guard.
        try:
            if self.refresh_token:
                refreshed = await self.refresh_auth_token()
                if refreshed:
                    self._sync_tokens_to_state_manager()
        except Exception as e:
            logger.error(
                "Token refresh after EventSub revocation failed: %s", e, exc_info=True
            )

        if not is_shutdown_in_progress() and self._auto_reconnect_enabled():
            self.reconnect()

    def _auto_reconnect_enabled(self) -> bool:
        """Whether automatic EventSub reconnect/rebuild is allowed (user setting)."""
        try:
            return bool(dataobjects.state_manager.get_app_settings().auto_reconnect)
        except Exception:
            return True

    def _raid_shoutout_enabled(self, kind: str) -> bool:
        """Whether automatic raid shoutouts are enabled (Helix API vs chat message)."""
        try:
            settings = dataobjects.state_manager.get_app_settings()
            if kind == "helix":
                return bool(getattr(settings, "auto_raid_helix_shoutout", True))
            if kind == "chat":
                return bool(getattr(settings, "auto_raid_chat_shoutout", True))
        except Exception:
            pass
        return True

    def _stop_eventsub_safely(self) -> None:
        """Stop EventSub without raising when the session is already dead."""
        eventsub = self.eventsub
        if eventsub is None:
            return
        try:
            _run_twitch_coro_sync(eventsub.stop(), timeout=15)
            logger.debug("EventSub connection stopped")
        except AttributeError as e:
            if "close" in str(e).lower() or "none" in str(e).lower():
                logger.debug("EventSub stop skipped (session already closed): %s", e)
            else:
                logger.debug("EventSub stop AttributeError: %s", e)
        except RuntimeError as e:
            msg = str(e).lower()
            if "not running" in msg or "event loop" in msg:
                logger.debug("EventSub stop skipped: %s", e)
            else:
                logger.debug("EventSub stop RuntimeError: %s", e)
        except Exception as e:
            logger.debug("EventSub stop error (ignored): %s", e)
        finally:
            self.eventsub = None

    def _note_event_received(self) -> None:
        """Record that a Twitch EventSub event arrived (liveness signal)."""
        self.last_event_time = datetime.now()

    def _schedule_eventsub_rebuild(self, reason: str) -> None:
        """Rebuild EventSub subscriptions, debounced and gated by the setting.

        Shared entry point for the revocation handler, the Helix 401 refresh
        path, and the health-check staleness check so a burst of triggers only
        causes a single reconnect.
        """
        from .shutdown import is_shutdown_in_progress

        if is_shutdown_in_progress():
            return
        if not self._auto_reconnect_enabled():
            logger.debug(
                "Skipping EventSub rebuild (auto_reconnect disabled): %s", reason
            )
            return
        try:
            from .connection_monitor import (
                is_internet_available,
                is_service_reachable,
            )

            if not is_internet_available() or not is_service_reachable("twitch"):
                logger.debug(
                    "Skipping EventSub rebuild (%s) — connectivity check failed",
                    reason,
                )
                return
        except Exception:
            logger.debug("Connectivity check unavailable for EventSub rebuild", exc_info=True)
        now = datetime.now()
        if (
            self._last_revocation_reconnect
            and (now - self._last_revocation_reconnect).total_seconds() < 30
        ):
            logger.debug("EventSub rebuild already in progress, skipping (%s)", reason)
            return
        self._last_revocation_reconnect = now
        logger.info("Rebuilding EventSub subscriptions: %s", reason)
        self.reconnect()

    def reconnect(self):
        """Attempt to reconnect to Twitch"""
        from .shutdown import is_shutdown_in_progress

        if is_shutdown_in_progress():
            return

        try:
            from .connection_monitor import (
                is_internet_available,
                is_service_reachable,
            )

            if not is_internet_available() or not is_service_reachable("twitch"):
                logger.debug(
                    "Skipping Twitch reconnect — internet or Twitch host unreachable"
                )
                return
        except Exception:
            logger.debug("Connectivity check unavailable for Twitch reconnect", exc_info=True)

        # Serialize reconnects: if one is already running, don't spawn another
        # init thread on top of it.
        with self._reconnect_lock:
            if self._reconnect_in_progress:
                logger.debug("Reconnect already in progress, skipping duplicate")
                return
            self._reconnect_in_progress = True

        try:
            logger.debug("Attempting to reconnect to Twitch")

            # Stop the current connection if it exists
            if self.eventsub:
                self._stop_eventsub_safely()

            # Reset connection state
            self.is_connected = False
            global twitch_connected
            twitch_connected = False
            self._connection_epoch += 1
            self.last_health_check = None

            if is_shutdown_in_progress():
                self._reconnect_in_progress = False
                return

            def _run_reconnect() -> None:
                try:
                    asyncio.run(self.intialize_twitch_api())
                finally:
                    self._reconnect_in_progress = False

            # Create a new thread for the async reconnection
            reconnect_thread = threading.Thread(target=_run_reconnect)
            reconnect_thread.daemon = True
            reconnect_thread.start()

            logger.debug("Twitch reconnection thread started")
        except Exception as e:
            self._reconnect_in_progress = False
            logger.error(
                f"Failed to initiate Twitch reconnection: {str(e)}", exc_info=True
            )

    async def intialize_twitch_api(self):
        from .shutdown import is_shutdown_in_progress

        global twitch_connected
        logger.debug("Initializing Twitch API websocket connection")
        if is_shutdown_in_progress():
            return False
        epoch = self._connection_epoch
        try:
            # Stage the Twitch API (authenticate)
            staging_success = await self.stage_twitch_api()
            if not staging_success:
                logger.warning("Failed to stage Twitch API - authentication required")
                self.is_connected = False
                twitch_connected = False
                notify_twitch_connect_failed()
                return False  # Return False instead of raising exception

            if is_shutdown_in_progress() or self._connection_epoch != epoch:
                return False

            # Initialize the EventSub websocket with a revocation handler so a
            # token-expiry revocation triggers a refresh + re-subscribe instead
            # of silently leaving an open-but-empty session.
            try:
                self.eventsub = EventSubWebsocket(
                    self.twitch, revocation_handler=self._on_eventsub_revocation
                )
            except TypeError:
                # Older twitchAPI without the revocation_handler kwarg.
                self.eventsub = EventSubWebsocket(self.twitch)
                try:
                    self.eventsub.revocation_handler = self._on_eventsub_revocation
                except Exception:
                    logger.debug(
                        "EventSubWebsocket does not support revocation_handler"
                    )
            eventsub = self.eventsub

            # Start the websocket connection first
            eventsub.start()

            # Update connection state
            self.is_connected = True
            twitch_connected = True
            self.last_health_check = datetime.now()

            try:
                from .twitch_moderators import get_moderator_cache

                for _ in range(20):
                    if is_twitch_api_ready():
                        await get_moderator_cache().refresh(force=True)
                        break
                    await asyncio.sleep(0.25)
                else:
                    logger.debug(
                        "Moderator cache refresh deferred: Twitch HTTP session not ready"
                    )
            except Exception as mod_err:
                logger.debug("Moderator cache refresh on connect: %s", mod_err)

            if (
                is_shutdown_in_progress()
                or self._connection_epoch != epoch
                or self.eventsub is not eventsub
            ):
                return False

            # Register event handlers
            try:
                # Subscribe to channel chat messages
                try:
                    await eventsub.listen_channel_chat_message(
                        self.user.id, self.user.id, self.on_chat_message
                    )
                    logger.debug("Successfully subscribed to channel chat messages")
                except Exception as e:
                    logger.error(
                        f"Failed to subscribe to channel chat messages: {str(e)}"
                    )
                    raise

                try:
                    await eventsub.listen_channel_chat_notification(
                        self.user.id, self.user.id, self.on_chat_notification
                    )
                    logger.debug(
                        "Successfully subscribed to channel chat notification events"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to subscribe to channel chat notification events: {str(e)}"
                    )
                    raise

                # Subscribe to channel moderation events
                try:
                    await eventsub.listen_channel_moderate(
                        self.user.id, self.user.id, self.on_moderate
                    )
                    logger.debug("Successfully subscribed to channel moderation events")
                except Exception as e:
                    logger.error(
                        f"Failed to subscribe to channel moderation events: {str(e)}"
                    )
                    raise

                # Subscribe to channel update events (critical for category updates)
                try:
                    await eventsub.listen_channel_update(self.user.id, self.on_update)
                    logger.info(
                        "Successfully subscribed to channel update events - category changes will be detected"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to subscribe to channel update events: {str(e)}"
                    )
                    raise

                # Subscribe to channel follow events
                try:
                    await eventsub.listen_channel_follow_v2(
                        self.user.id, self.user.id, self.on_follow
                    )
                    logger.debug("Successfully subscribed to channel follow events")
                except Exception as e:
                    logger.error(
                        f"Failed to subscribe to channel follow events: {str(e)}"
                    )
                    raise

                # Subscribe to channel subscription messages
                try:
                    await eventsub.listen_channel_subscription_message(
                        self.user.id, self.on_sub
                    )
                    logger.debug(
                        "Successfully subscribed to channel subscription messages"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to subscribe to channel subscription messages: {str(e)}"
                    )
                    raise

                # Subscribe to channel subscription events
                # try:
                #     await eventsub.listen_channel_subscribe(
                #         self.user.id, self.on_new_sub
                #     )
                #     logger.debug(
                #         "Successfully subscribed to channel subscription events"
                #     )
                # except Exception as e:
                #     logger.error(
                #         f"Failed to subscribe to channel subscription events: {str(e)}"
                #     )
                #     raise

                await eventsub.listen_channel_subscription_gift(
                    self.user.id, self.on_sub_gift
                )
                await eventsub.listen_channel_cheer(self.user.id, self.on_cheer)
                await eventsub.listen_channel_bits_use(self.user.id, self.on_bits_use)
                await eventsub.listen_channel_raid(self.on_raid, self.user.id, None)
                await eventsub.listen_channel_points_custom_reward_redemption_add(
                    self.user.id, self.on_points
                )
                await eventsub.listen_hype_train_begin(
                    self.user.id, self.on_hype_train_start
                )
                await self.eventsub.listen_hype_train_progress(
                    self.user.id, self.on_hype_train_progress
                )
                await self.eventsub.listen_hype_train_end(
                    self.user.id, self.on_hype_train_end
                )
            except Exception as e:
                logger.error(f"Error subscribing to events: {str(e)}", exc_info=True)
                self.is_connected = False
                twitch_connected = False
                notify_twitch_connect_failed()
                return False  # Return False instead of raising exception

            if is_shutdown_in_progress() or self._connection_epoch != epoch:
                return False

            # Start the health check thread
            self.start_health_check()

            logger.info("Twitch API websocket connection established successfully")
            try:
                from . import web_engine

                web_engine.broadcast_overlay_recovery("twitch", "reconnected")
            except Exception as broadcast_err:
                logger.debug(
                    "overlay recovery broadcast failed: %s", broadcast_err
                )
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Twitch API: {str(e)}", exc_info=True)
            self.is_connected = False
            twitch_connected = False
            notify_twitch_connect_failed()
            return False  # Return False instead of raising exception

    async def generic_api_call(
        self,
        url: str,
        method: str = "GET",
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
    ) -> dict:
        """
        Makes a generic call to the Twitch API.

        Args:
            url (str): The full Twitch API URL to call.
            method (str, optional): HTTP method. Defaults to "GET".
            params (Optional[dict], optional): Query parameters for the request. Defaults to None.
            json_data (Optional[dict], optional): JSON body for POST/PUT requests. Defaults to None.

        Returns:
            dict: The JSON response from Twitch API.

        Raises:
            Exception: If the API call fails or returns an error.
        """
        logger.debug(
            f"Attempting generic API call: {method.upper()} {url} with params={params}, json_data={json_data}"
        )

        users_login = parse_helix_users_login(url, params)
        if users_login is not None and not twitch_user_lookup_allowed(users_login):
            logger.debug(
                "Skipping helix/users lookup for invalid or anonymous login: %r",
                users_login,
            )
            raise Exception(
                "User lookup skipped (anonymous or invalid login)"
            )

        # Helix proxy may run on another thread before this instance was updated
        self.sync_helix_credentials_from_state()

        if not self.auth_token or not self.client_id:
            logger.debug("Missing auth tokens, attempting to load from database")
            auth_loaded = self.load_auth_data()
            if not auth_loaded or not self.auth_token or not self.client_id:
                logger.warning(
                    "No valid authentication credentials available for API call"
                )
                raise Exception("Authentication required - no valid tokens available")

        # Proactive refresh only when we know expiry and have client secret for refresh
        if self.is_token_expired():
            if not self.client_secret:
                self._apply_api_credentials_from_store()
            if self.client_secret:
                logger.info("Token expired, attempting refresh for generic API call")
                refresh_success = await self.refresh_auth_token()
                if not refresh_success:
                    logger.warning("Token refresh failed for API call")
                    raise Exception("Authentication required - token refresh failed")
            else:
                logger.warning(
                    "Token appears expired but client secret not configured — "
                    "set Twitch API credentials in Settings"
                )

        if not self.auth_token:
            logger.warning("No auth token available for API call")
            raise Exception("Authentication token missing - authentication required")

        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.auth_token}",
        }
        # aiohttp sets Content-Type for json parameter automatically
        # If json_data is provided, ensure content type is application/json
        if json_data is not None:
            headers["Content-Type"] = "application/json"

        async def _perform_request(session_to_use: aiohttp.ClientSession) -> dict:
            # Make the API call
            async with session_to_use.request(
                method.upper(),
                url,
                params=params,
                json=json_data,  # aiohttp uses 'json' parameter for dict to json body
                headers=headers,
            ) as response:
                # Attempt to get JSON, but handle cases where response might not be JSON (e.g. 204 No Content)
                if response.content_type == "application/json":
                    response_data = await response.json()
                else:
                    response_data = await response.text()  # Or handle as appropriate

                if 200 <= response.status < 300:
                    logger.debug(
                        f"Generic API call successful ({response.status}): {url}"
                    )
                    # If response_data is not dict (e.g. for 204 No Content, it might be empty string), wrap it
                    return (
                        response_data
                        if isinstance(response_data, dict)
                        else {"status": response.status, "data": response_data}
                    )
                else:
                    # 403 is an expected permission boundary (e.g. channel points
                    # require Affiliate/Partner). Surface it quietly without an
                    # error-level traceback and skip the pointless token refresh.
                    if response.status == 403:
                        logger.warning(
                            f"Generic API call forbidden (403): {url} - Response: {response_data}"
                        )
                        raise TwitchPermissionError(
                            f"Twitch API Error 403: {response_data}"
                        )
                    logger.error(
                        f"Generic API call failed ({response.status}): {url} - Response: {response_data}"
                    )
                    # If we get a 401, try to refresh the token once more
                    if response.status == 401:
                        logger.warning(
                            "Received 401 Unauthorized, attempting token refresh and retry"
                        )
                        try:
                            refresh_success = await self.refresh_auth_token()
                            if refresh_success:
                                # Update headers with new token
                                headers["Authorization"] = f"Bearer {self.auth_token}"

                                # Update the existing Twitch instance with new authentication
                                if self.twitch:
                                    try:
                                        await self.twitch.set_user_authentication(
                                            self.auth_token,
                                            self.authscope,
                                            self.refresh_token,
                                        )
                                        logger.debug(
                                            "Updated existing Twitch instance with refreshed tokens"
                                        )
                                    except Exception as auth_error:
                                        logger.warning(
                                            f"Failed to update Twitch instance auth: {str(auth_error)}"
                                        )

                                # The refreshed token invalidates the EventSub
                                # subscriptions created with the old one (Twitch
                                # revokes them once it expires), so rebuild them —
                                # otherwise events silently stop while Helix calls
                                # keep working. Debounced + setting-gated.
                                self._schedule_eventsub_rebuild(
                                    "Helix 401 token refresh"
                                )

                                # NOTE: Token sync to state manager is now handled in refresh_auth_token method

                                # Retry the request
                                async with session_to_use.request(
                                    method.upper(),
                                    url,
                                    params=params,
                                    json=json_data,
                                    headers=headers,
                                ) as retry_response:
                                    if (
                                        retry_response.content_type
                                        == "application/json"
                                    ):
                                        retry_data = await retry_response.json()
                                    else:
                                        retry_data = await retry_response.text()

                                    if 200 <= retry_response.status < 300:
                                        logger.info(
                                            f"Generic API call succeeded after token refresh ({retry_response.status}): {url}"
                                        )
                                        return (
                                            retry_data
                                            if isinstance(retry_data, dict)
                                            else {
                                                "status": retry_response.status,
                                                "data": retry_data,
                                            }
                                        )
                                    elif retry_response.status == 403:
                                        logger.warning(
                                            f"Generic API call forbidden (403) after token refresh: {url} - Response: {retry_data}"
                                        )
                                        raise TwitchPermissionError(
                                            f"Twitch API Error 403: {retry_data}"
                                        )
                                    else:
                                        logger.error(
                                            f"Generic API call still failed after token refresh ({retry_response.status}): {url} - Response: {retry_data}"
                                        )
                                        raise Exception(
                                            f"Twitch API Error {retry_response.status}: {retry_data}"
                                        )
                            else:
                                logger.error(
                                    "Token refresh failed, cannot retry API call"
                                )
                                raise Exception(
                                    f"Authentication failed - token refresh unsuccessful"
                                )
                        except TwitchPermissionError:
                            raise
                        except Exception as refresh_error:
                            logger.error(
                                f"Error during token refresh retry: {str(refresh_error)}"
                            )
                            raise Exception(
                                f"Authentication failed - {str(refresh_error)}"
                            )
                    else:
                        raise Exception(
                            f"Twitch API Error {response.status}: {response_data}"
                        )

        try:
            if (
                self.twitch
                and hasattr(self.twitch, "_Twitch__session")
                and self.twitch._Twitch__session
            ):
                logger.debug("Using existing authenticated Twitch session for API call")
                return await _perform_request(self.twitch._Twitch__session)
            if self.auth_token and self.client_id:
                logger.debug(
                    "Twitch HTTP session not open yet; using ephemeral session for Helix call"
                )
                async with _ephemeral_client_session() as session:
                    return await _perform_request(session)
            if self.twitch:
                raise TwitchSessionNotReadyError("Twitch API session not ready")
            logger.debug(
                "Twitch library client not initialized; "
                "using ephemeral session for Helix API call"
            )
            async with _ephemeral_client_session() as session:
                return await _perform_request(session)
        except (TwitchSessionNotReadyError, TwitchPermissionError):
            raise
        except aiohttp.ClientError as e:
            try:
                from .connection_monitor import is_internet_available

                offline = not is_internet_available()
            except Exception:
                offline = False
            if offline:
                logger.warning(
                    "Network unavailable during API call to %s: %s", url, e
                )
            else:
                logger.error(
                    f"aiohttp.ClientError during generic API call to {url}: {str(e)}",
                    exc_info=True,
                )
            raise Exception(f"Network error during API call: {str(e)}") from e
        except Exception as e:
            if isinstance(e, TwitchSessionNotReadyError):
                raise
            logger.error(
                f"Error during generic API call to {url}: {str(e)}", exc_info=True
            )
            raise

    def stop_connection(self):
        """Stop the current Twitch connection cleanly"""
        try:
            logger.debug("Stopping Twitch connection")

            # Stop health check thread first
            self.stop_health_check()

            # Stop the EventSub websocket connection
            if self.eventsub:
                self._stop_eventsub_safely()

            # Close the Twitch API session if it exists
            if (
                self.twitch
                and hasattr(self.twitch, "_Twitch__session")
                and self.twitch._Twitch__session
            ):
                try:
                    # Note: This needs to be called in an async context, but we'll handle it gracefully
                    logger.debug("Twitch session cleanup noted for async closure")
                except Exception as e:
                    logger.error(
                        f"Error closing Twitch session: {str(e)}", exc_info=True
                    )

            # Reset connection state
            self.is_connected = False
            global twitch_connected
            twitch_connected = False
            self._connection_epoch += 1
            self.eventsub = None
            self.last_health_check = None

            logger.debug("Twitch connection stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping Twitch connection: {str(e)}", exc_info=True)
            return False

    async def reconnect_with_oauth(self):
        """Stop current connection and reconnect using OAuth flow"""
        try:
            logger.debug("Starting reconnect with OAuth flow")

            # Stop the current connection
            self.stop_connection()

            # Reload credentials from api_credentials_manager in case user updated them via the UI
            from modules import api_credentials_manager

            creds = api_credentials_manager.get_twitch_credentials()
            if creds.get("client_id"):
                self.client_id = creds["client_id"]
            if creds.get("client_secret"):
                self.client_secret = creds["client_secret"]

            # Clear existing auth data to force OAuth
            self.auth_token = ""
            self.refresh_token = ""
            self.user_id = ""
            self.token_expiry = None

            # Force OAuth authentication
            oauth_success = await self.authenticate_with_oauth()
            if not oauth_success:
                logger.error("OAuth authentication failed during reconnect")
                notify_twitch_connect_failed()
                return False

            # Initialize the connection with new credentials
            if not await self.intialize_twitch_api():
                return False

            logger.debug("Reconnect with OAuth completed successfully")
            return True
        except Exception as e:
            logger.error(f"Error during OAuth reconnection: {str(e)}", exc_info=True)
            notify_twitch_connect_failed()
            return False

    def get_token_status(self):
        """Get detailed token status for debugging"""
        try:
            status = {
                "has_auth_token": bool(self.auth_token),
                "has_refresh_token": bool(self.refresh_token),
                "token_expiry": self.token_expiry.isoformat()
                if self.token_expiry
                else None,
                "is_expired": self.is_token_expired(),
                "expires_in_minutes": None,
                "last_refresh_attempt": self._last_refresh_attempt.isoformat()
                if self._last_refresh_attempt
                else None,
                "is_connected": self.is_connected,
                "user_id": self.user_id,
            }

            if self.token_expiry:
                time_until_expiry = self.token_expiry - datetime.now()
                status["expires_in_minutes"] = int(
                    time_until_expiry.total_seconds() / 60
                )

            return status
        except Exception as e:
            logger.error(f"Error getting token status: {str(e)}", exc_info=True)
            return {"error": str(e)}

    def get_connection_status(self):
        """Get current connection status for UI display"""
        if self.is_eventsub_live():
            stale = (
                self.last_event_time is not None
                and (datetime.now() - self.last_event_time).total_seconds()
                > self.event_staleness_timeout
            )
            status = {
                "status": "Degraded (no recent events)" if stale else "Connected",
                "is_valid": True,
                "degraded": stale,
                "last_update": self.last_health_check.strftime("%Y-%m-%d %H:%M:%S")
                if self.last_health_check
                else "Unknown",
                "user_name": self.user.display_name if self.user else "Unknown",
            }
        elif self.auth_token and self.refresh_token:
            status = {
                "status": "Authenticated but Disconnected",
                "is_valid": False,
                "last_update": "Connection Lost",
                "user_name": self.user.display_name if self.user else "Unknown",
            }
        elif self.client_id and self.client_secret:
            status = {
                "status": "Configured but Not Authenticated",
                "is_valid": False,
                "last_update": "Never",
                "user_name": "None",
            }
        else:
            status = {
                "status": "Not Configured",
                "is_valid": False,
                "last_update": "Never",
                "user_name": "None",
            }

        status.update(
            build_token_timing_fields(
                token_expiry=self.token_expiry,
                has_auth_token=bool(self.auth_token),
            )
        )
        return status


# Point reward API functions
_CHANNEL_POINTS_ICON_CACHE: dict[str, tuple[float, dict[str, str]]] = {}
_CHANNEL_POINTS_ICON_CACHE_TTL = 3600.0

_CHANNEL_POINTS_ICON_GQL = """
query ChannelPointsChannelSettings($channelID: ID!) {
  channel(id: $channelID) {
    communityPoints {
      settings {
        image { url url2x url4x }
        defaultImage { url url2x url4x }
      }
    }
  }
}
"""


def _normalize_points_image(img: Optional[dict]) -> Optional[dict[str, str]]:
    if not img or not isinstance(img, dict):
        return None
    url_1x = img.get("url") or img.get("url_1x")
    url_2x = img.get("url2x") or img.get("url_2x")
    url_4x = img.get("url4x") or img.get("url_4x")
    if not url_1x and not url_2x and not url_4x:
        return None
    primary = url_1x or url_2x or url_4x
    return {
        "url_1x": url_1x or primary,
        "url_2x": url_2x or primary,
        "url_4x": url_4x or primary,
    }


def clear_channel_points_icon_cache(broadcaster_id: Optional[str] = None) -> None:
    """Clear cached channel points currency icon URLs."""
    if broadcaster_id is None:
        _CHANNEL_POINTS_ICON_CACHE.clear()
        return
    _CHANNEL_POINTS_ICON_CACHE.pop(str(broadcaster_id), None)


async def _helix_fallback_channel_points_icon() -> Optional[dict[str, str]]:
    """Fallback icon from Helix custom rewards default_image when GQL is unavailable."""
    rewards, status = await _fetch_channel_point_rewards_async()
    if status != "ok" or not rewards:
        return None
    for reward in rewards:
        normalized = _normalize_points_image(reward.get("default_image"))
        if normalized:
            return normalized
    return None


async def fetch_channel_points_currency_icon_async(
    broadcaster_id: str,
) -> Optional[dict[str, str]]:
    """Fetch per-channel community points currency icon URLs (GQL with Helix fallback)."""
    if not broadcaster_id:
        return None

    cache_key = str(broadcaster_id)
    now = time.time()
    cached = _CHANNEL_POINTS_ICON_CACHE.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    icon_urls: Optional[dict[str, str]] = None

    if twitch_api and twitch_api.auth_token and twitch_api.client_id:
        payload = [
            {
                "operationName": "ChannelPointsChannelSettings",
                "variables": {"channelID": cache_key},
                "query": _CHANNEL_POINTS_ICON_GQL,
            }
        ]
        headers = {
            "Client-Id": twitch_api.client_id,
            "Authorization": f"Bearer {twitch_api.auth_token}",
            "Content-Type": "application/json",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://gql.twitch.tv/gql",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        if isinstance(data, list) and data:
                            channel = (
                                (data[0].get("data") or {}).get("channel") or {}
                            )
                            settings = (
                                (channel.get("communityPoints") or {}).get("settings")
                                or {}
                            )
                            icon_urls = _normalize_points_image(settings.get("image"))
                            if not icon_urls:
                                icon_urls = _normalize_points_image(
                                    settings.get("defaultImage")
                                )
                    else:
                        logger.debug(
                            "Channel points GQL returned HTTP %s", response.status
                        )
        except Exception as e:
            logger.debug(
                "Channel points currency icon GQL failed: %s", e, exc_info=True
            )

    if not icon_urls:
        icon_urls = await _helix_fallback_channel_points_icon()

    if icon_urls:
        _CHANNEL_POINTS_ICON_CACHE[cache_key] = (
            now + _CHANNEL_POINTS_ICON_CACHE_TTL,
            icon_urls,
        )
    return icon_urls


def fetch_channel_points_currency_icon(
    broadcaster_id: str,
) -> Optional[dict[str, str]]:
    """Sync wrapper for channel points currency icon fetch."""
    try:
        return _run_twitch_coro_sync(
            fetch_channel_points_currency_icon_async(broadcaster_id)
        )
    except Exception as e:
        logger.debug("fetch_channel_points_currency_icon failed: %s", e)
        return None


def _is_channel_points_forbidden(exc: BaseException) -> bool:
    """True when Twitch denies custom rewards (e.g. broadcaster not Affiliate/Partner)."""
    if isinstance(exc, TwitchPermissionError):
        return True
    msg = str(exc).lower()
    if "partner or affiliate" in msg:
        return True
    if "403" in msg and "forbidden" in msg:
        return True
    return False


def _run_twitch_coro_sync(coro, timeout: float = 30):
    """Run a coroutine from sync code (safe when NiceGUI already has a loop running)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures

    def _run_in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_run_in_thread).result(timeout=timeout)


async def _fetch_channel_point_rewards_async():
    """Fetch custom channel point rewards; returns (rewards_or_none, status).

    status is one of: ok, not_connected, not_unlocked, error.
    On ok, rewards is a list (possibly empty).
    """
    if not twitch_api or not twitch_api.is_connected:
        logger.error("Twitch API not connected")
        return (None, "not_connected")

    url = f"https://api.twitch.tv/helix/channel_points/custom_rewards?broadcaster_id={twitch_api.user_id}"

    try:
        response = await twitch_api.generic_api_call(url, "GET")
        if response and "data" in response:
            return (response["data"], "ok")
        logger.error("Invalid response format from Twitch API")
        return (None, "error")
    except Exception as e:
        if _is_channel_points_forbidden(e):
            logger.warning(
                "Channel point rewards unavailable: the channel must have "
                "Affiliate or Partner status."
            )
            return (None, "not_unlocked")
        logger.error(f"Error getting point rewards: {str(e)}", exc_info=True)
        return (None, "error")


async def get_point_rewards_async():
    """Get all point rewards from Twitch API (async version)"""
    rewards, status = await _fetch_channel_point_rewards_async()
    if status == "ok":
        return rewards
    return None


def fetch_channel_point_rewards():
    """Sync: structured result for UI (rewards list only when status is ok)."""
    if not twitch_api or not twitch_api.is_connected:
        return {"rewards": None, "status": "not_connected"}

    try:
        rewards, status = _run_twitch_coro_sync(_fetch_channel_point_rewards_async())
    except Exception as e:
        logger.error(f"Error getting point rewards: {str(e)}", exc_info=True)
        return {"rewards": None, "status": "error"}

    if status == "ok":
        return {"rewards": rewards if rewards is not None else [], "status": "ok"}
    return {"rewards": None, "status": status}


def get_point_rewards():
    """Get all point rewards from Twitch API (sync wrapper)"""
    result = fetch_channel_point_rewards()
    if result["status"] == "ok":
        return result["rewards"]
    return None


def get_point_reward_by_id(reward_id: str):
    """Get a specific point reward by ID from Twitch API"""
    try:
        if not twitch_api or not twitch_api.is_connected:
            logger.error("Twitch API not connected")
            return None

        # Validate reward_id
        if not reward_id or reward_id in [
            "new",
            "loading",
            "error",
            "no_rewards",
            "not_connected",
            "no_channel_points",
        ]:
            logger.warning(f"Invalid reward ID provided: {reward_id}")
            return None

        url = f"https://api.twitch.tv/helix/channel_points/custom_rewards?broadcaster_id={twitch_api.user_id}&id={reward_id}"

        # Use a thread pool to run the async function
        async def get_reward_async():
            response = await twitch_api.generic_api_call(url, "GET")
            if response and "data" in response and len(response["data"]) > 0:
                return response["data"][0]
            else:
                logger.error(f"Point reward {reward_id} not found")
                return None

        return _run_twitch_coro_sync(get_reward_async())

    except Exception as e:
        logger.error(f"Error getting point reward {reward_id}: {str(e)}", exc_info=True)
        return None


def create_point_reward(reward_data: dict):
    """Create a new point reward on Twitch"""
    try:
        if not twitch_api or not twitch_api.is_connected:
            logger.error("Twitch API not connected")
            return None

        url = f"https://api.twitch.tv/helix/channel_points/custom_rewards?broadcaster_id={twitch_api.user_id}"

        # Convert our UI data format to Twitch API format
        api_data = _convert_ui_to_api_format(reward_data)

        # Use a thread pool to run the async function
        async def create_reward_async():
            response = await twitch_api.generic_api_call(
                url, "POST", json_data=api_data
            )
            if response and "data" in response and len(response["data"]) > 0:
                return response["data"][0]  # Return the full reward data
            else:
                logger.error("Failed to create point reward")
                return None

        return _run_twitch_coro_sync(create_reward_async())

    except Exception as e:
        logger.error(f"Error creating point reward: {str(e)}", exc_info=True)
        return None


def update_point_reward(reward_id: str, reward_data: dict):
    """Update an existing point reward on Twitch"""
    try:
        if not twitch_api or not twitch_api.is_connected:
            logger.error("Twitch API not connected")
            return False

        url = f"https://api.twitch.tv/helix/channel_points/custom_rewards?broadcaster_id={twitch_api.user_id}&id={reward_id}"

        # Convert our UI data format to Twitch API format
        api_data = _convert_ui_to_api_format(reward_data)

        # Use a thread pool to run the async function
        async def update_reward_async():
            response = await twitch_api.generic_api_call(
                url, "PATCH", json_data=api_data
            )
            return response is not None

        return _run_twitch_coro_sync(update_reward_async())

    except Exception as e:
        logger.error(
            f"Error updating point reward {reward_id}: {str(e)}", exc_info=True
        )
        return False


def delete_point_reward(reward_id: str):
    """Delete a point reward from Twitch"""
    try:
        if not twitch_api or not twitch_api.is_connected:
            logger.error("Twitch API not connected")
            return False

        url = f"https://api.twitch.tv/helix/channel_points/custom_rewards?broadcaster_id={twitch_api.user_id}&id={reward_id}"

        # Use a thread pool to run the async function
        async def delete_reward_async():
            response = await twitch_api.generic_api_call(url, "DELETE")
            return response is not None

        return _run_twitch_coro_sync(delete_reward_async())

    except Exception as e:
        logger.error(
            f"Error deleting point reward {reward_id}: {str(e)}", exc_info=True
        )
        return False


def _convert_ui_to_api_format(ui_data: dict) -> dict:
    """Convert UI data format to Twitch API format for point rewards"""
    api_data = {
        "title": ui_data.get("title", ""),
        "cost": int(ui_data.get("cost", 100)),
        "is_enabled": ui_data.get("is_enabled", True),
        "is_user_input_required": ui_data.get("is_user_input_required", False),
        "should_redemptions_skip_request_queue": ui_data.get(
            "should_redemptions_skip_request_queue", True
        ),
    }

    # Add prompt if user input is required
    if ui_data.get("is_user_input_required"):
        api_data["prompt"] = ui_data.get("prompt", "")

    # Handle max per stream
    max_per_stream = ui_data.get("max_per_stream")
    if max_per_stream and max_per_stream.get("is_enabled"):
        api_data["max_per_stream_setting"] = {
            "is_enabled": True,
            "max_per_stream": max_per_stream.get("max_per_stream", 1),
        }
    else:
        api_data["max_per_stream_setting"] = {"is_enabled": False}

    # Handle max per user per stream
    max_per_user = ui_data.get("max_per_user_per_stream")
    if max_per_user and max_per_user.get("is_enabled"):
        api_data["max_per_user_per_stream_setting"] = {
            "is_enabled": True,
            "max_per_user_per_stream": max_per_user.get("max_per_user_per_stream", 1),
        }
    else:
        api_data["max_per_user_per_stream_setting"] = {"is_enabled": False}

    # Handle global cooldown
    global_cooldown = ui_data.get("global_cooldown")
    if global_cooldown and global_cooldown.get("is_enabled"):
        api_data["global_cooldown_setting"] = {
            "is_enabled": True,
            "global_cooldown_seconds": global_cooldown.get(
                "global_cooldown_seconds", 60
            ),
        }
    else:
        api_data["global_cooldown_setting"] = {"is_enabled": False}

    return api_data


# Global instance
twitch_api = None


def initialize() -> None:
    """Initialize the Twitch API and start the websocket connection"""
    current_process = multiprocessing.current_process()
    logger = logging.getLogger(__name__)

    logger.debug(
        f"Initialize called from process: {current_process.name} (pid: {current_process.pid})"
    )

    # Only initialize in the main process
    if current_process.name != "MainProcess":
        logger.debug(
            f"Skipping Twitch initialization in {current_process.name} process"
        )
        return

    global twitch_api, _initialized

    # Use a lock to prevent multiple initializations
    with _init_lock:
        if _initialized:
            logger.debug("Twitch API already initialized, skipping")
            return

        logger.debug("Starting Twitch API initialization")

        # Create a new instance
        twitch_api = Twitch_API()

        # Alert processor initialization is now handled by the main UI initialization
        # to avoid duplicate initialization and improve startup performance
        # alert_init_thread = threading.Thread(target=alert_processor.initialize)
        # alert_init_thread.daemon = True
        # alert_init_thread.start()

        init_thread = threading.Thread(
            target=_run_twitch_init,
            name="TwitchInit",
        )
        init_thread.daemon = True
        init_thread.start()
        logger.debug("Twitch API initialization thread started")

        # Mark as initialized
        _initialized = True


def _run_twitch_init() -> None:
    """Run Twitch API init and signal staging completion for chatbot ordering."""
    try:
        asyncio.run(twitch_api.intialize_twitch_api())
    finally:
        _staging_complete.set()


def wait_for_staging_complete(timeout: float = 120.0) -> bool:
    """Wait until the first Twitch API init/staging pass finishes."""
    return _staging_complete.wait(timeout=timeout)


def get_twitch_api():
    """Get the global Twitch API instance"""
    return twitch_api


def is_twitch_api_ready() -> bool:
    """True when the Twitch library client has a live HTTP session (EventSub, etc.)."""
    api = twitch_api
    if api is None:
        return False
    if not api.is_connected or not api.twitch:
        return False
    session = getattr(api.twitch, "_Twitch__session", None)
    return session is not None


def can_proxy_helix_api_requests() -> bool:
    """True when Helix HTTP proxy calls can run (tokens required; library session optional)."""
    api = twitch_api
    if api is None:
        return False
    try:
        api.sync_helix_credentials_from_state()
        if not (api.auth_token and api.client_id):
            api.load_auth_data()
        return bool(api.auth_token and api.client_id)
    except Exception:
        return False


def notify_twitch_connect_failed() -> None:
    """Prompt user to reconnect Twitch when connection or auth fails."""
    try:
        from .notification_engine import nav_actions_settings, notify

        notify(
            "Twitch is not connected. If your browser opened for authorization, "
            "complete that login—or reconnect in Settings → Twitch.",
            type="warning",
            dedupe_key="twitch:connect_failed",
            dedupe_cooldown_sec=3600.0,
            timeout=12.0,
            actions=nav_actions_settings("Twitch"),
        )
    except Exception as e:
        logger.warning("Could not show Twitch connect failed notification: %s", e)


def trigger_oauth_reconnection():
    """Trigger OAuth reconnection from the UI (sync wrapper)"""
    try:
        import concurrent.futures
        import threading

        if not twitch_api:
            logger.error("Twitch API not initialized")
            return False

        # Use a thread pool to run the async function
        def run_async():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(twitch_api.reconnect_with_oauth())
                finally:
                    loop.close()
            except Exception as e:
                logger.error(f"Error in async OAuth reconnection thread: {str(e)}")
                return False

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_async)
            return future.result(timeout=30)  # 30 second timeout for OAuth

    except Exception as e:
        logger.error(f"Error triggering OAuth reconnection: {str(e)}", exc_info=True)
        return False


def get_twitch_connection_status():
    """Get current Twitch connection status (sync wrapper)"""
    try:
        if not twitch_api:
            info = {
                "status": "Not Initialized",
                "is_valid": False,
                "last_update": "Never",
                "user_name": "None",
            }
        else:
            info = twitch_api.get_connection_status()

        from .connection_status_tracker import apply_connectivity_overlay_to_info

        return apply_connectivity_overlay_to_info("twitch", info)
    except Exception as e:
        logger.error(f"Error getting Twitch connection status: {str(e)}", exc_info=True)
        return {
            "status": "Error",
            "is_valid": False,
            "last_update": "Error",
            "user_name": "Error",
        }


def twitch_has_tokens_configured() -> bool:
    """True when Twitch client credentials and user tokens are present."""
    try:
        from .api_credentials_manager import api_credentials_manager
        from .dataobjects import state_manager

        creds = api_credentials_manager.get_twitch_credentials()
        if not (
            (creds.get("client_id") or "").strip()
            and (creds.get("client_secret") or "").strip()
        ):
            return False
        twitch_data = state_manager.get_twitch_data()
        if not twitch_data:
            return False
        return bool(
            (getattr(twitch_data, "auth_token", "") or "").strip()
            and (getattr(twitch_data, "refresh_token", "") or "").strip()
        )
    except Exception:
        return False


def is_twitch_disconnected_for_monitor() -> bool:
    """True when Twitch should be auto-reconnected by the connection monitor."""
    if not twitch_has_tokens_configured():
        return False
    if twitch_api is None:
        return True
    if not twitch_api.is_eventsub_live():
        return True
    return not twitch_api.is_connected


def attempt_auto_reconnect() -> bool:
    """Monitor-driven Twitch reconnect entry point."""
    if not twitch_has_tokens_configured():
        return False
    api = twitch_api
    if api is None:
        return False
    try:
        from .connection_monitor import (
            is_internet_available,
            is_service_reachable,
        )

        if not is_internet_available() or not is_service_reachable("twitch"):
            return False
    except Exception:
        return False
    api.reconnect()
    return True


def get_twitch_token_status():
    """Get current Twitch token status for debugging (sync wrapper)"""
    try:
        if not twitch_api:
            return {"status": "Not Initialized", "error": "Twitch API not initialized"}

        return twitch_api.get_token_status()
    except Exception as e:
        logger.error(f"Error getting Twitch token status: {str(e)}", exc_info=True)
        return {"status": "Error", "error": str(e)}
