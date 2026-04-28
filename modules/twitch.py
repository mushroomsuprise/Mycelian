#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024 Mycelian

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
from datetime import datetime, timedelta
from typing import Optional

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
    ChannelSubscriptionGiftEvent,
    ChannelSubscriptionMessageEvent,
    ChannelUpdateEvent,
    HypeTrainEndEvent,
    HypeTrainEvent,
)
from twitchAPI.twitch import Twitch
from twitchAPI.type import AuthScope

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
from .uiwindows.activity_feed import add_alert_to_feed

logger = logging.getLogger(__name__)

# Global flag to track initialization status
_initialized = False
_init_lock = threading.Lock()

# Global flag to track Twitch API connection status
twitch_connected = False


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
                if self.auth_token and self.refresh_token:
                    logger.debug("Successfully loaded Twitch authentication data")
                    return True
                else:
                    logger.info(
                        "Missing auth token or refresh token in state data - will need to authenticate"
                    )
                    return False
            else:
                logger.info(
                    "No Twitch authentication data found - will need to authenticate"
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

    def is_token_expired(self):
        """Check if the current auth token is expired"""
        if not self.token_expiry:
            return True
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

            # Set token expiry (Twitch tokens typically expire in 60 days)
            self.token_expiry = datetime.now() + timedelta(days=60)

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
        except Exception as e:
            logger.error(
                f"Failed to refresh authentication token: {str(e)}", exc_info=True
            )

            # If refresh completely failed, clear tokens to force re-authentication
            self.auth_token = ""
            self.refresh_token = ""
            self.token_expiry = None

            try:
                self.save_auth_data()
                # Also sync cleared state to state manager
                self._sync_tokens_to_state_manager()
                logger.warning(
                    "Cleared invalid tokens after refresh failure - OAuth re-authentication required"
                )
            except Exception as save_error:
                logger.error(f"Failed to save cleared token state: {str(save_error)}")

            return False
        finally:
            # Always release the lock
            try:
                self._refresh_lock.release()
            except:
                pass  # Lock might already be released

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

            # Get tokens through OAuth
            (
                self.auth_token,
                self.refresh_token,
            ) = await self.authenticator.authenticate()

            # Set user authentication with the tokens we just received
            print(f"Auth token: {self.auth_token}")
            print(f"Refresh token: {self.refresh_token}")
            await self.twitch.set_user_authentication(
                self.auth_token, self.authscope, self.refresh_token
            )

            # Now get user info
            self.user = await first(self.twitch.get_users())
            self.user_id = self.user.id

            # Set token expiry (Twitch tokens typically expire in 60 days)
            self.token_expiry = datetime.now() + timedelta(days=60)

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

            # Try to load existing auth data from database
            auth_data_loaded = self.load_auth_data()

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

                    # Save the verified auth data to ensure it's persisted
                    self.save_auth_data()

                except Exception as e:
                    logger.warning(f"Existing tokens failed validation: {str(e)}")
                    # Try OAuth flow as fallback
                    oauth_success = await self.authenticate_with_oauth()
                    if not oauth_success:
                        logger.error(
                            "Failed to authenticate with OAuth after token validation failure"
                        )
                        return False  # Return False instead of raising exception

            return True  # Successfully staged

        except Exception as e:
            logger.error(f"Failed to stage Twitch API: {str(e)}", exc_info=True)
            return False  # Return False instead of raising exception

    async def on_chat_message(self, data: ChannelChatMessageEvent):
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
            print(f"[CHATBOT] Processing message for commands: {message}")
            chatbot_response = chatbot_manager.process_chat_message(msg_dict)

            if chatbot_response:
                response_message, command_name = chatbot_response
                print(
                    f"[CHATBOT] Command '{command_name}' triggered, response: {response_message}"
                )

                # Send chatbot response back to chat
                try:
                    from .chatbot import send_chatbot_message

                    print(f"[CHATBOT] Attempting to send response: {response_message}")
                    send_chatbot_message(response_message)
                    logger.debug(
                        f"Chatbot responded to command '{command_name}': {response_message}"
                    )

                    # Log command usage
                    logger.info(
                        f"Command '{command_name}' processed by {username}: {response_message}"
                    )
                except Exception as send_error:
                    print(f"[CHATBOT] ERROR sending response: {str(send_error)}")
                    logger.error(f"Error sending chatbot response: {str(send_error)}")
            else:
                print(f"[CHATBOT] No command matched for message: {message}")
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
                    print(
                        f"[CHATBOT] Chat message event triggered, response: {chatbot_response}"
                    )

                    # Send chatbot response back to chat
                    try:
                        from .chatbot import send_chatbot_message

                        print(
                            f"[CHATBOT] Attempting to send chat message event response: {chatbot_response}"
                        )
                        send_chatbot_message(chatbot_response)
                        logger.debug(
                            f"Chatbot responded to chat message event: {chatbot_response}"
                        )
                    except Exception as send_error:
                        print(
                            f"[CHATBOT] ERROR sending chat message event response: {str(send_error)}"
                        )
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
        ev = data.event
        watch = getattr(ev, "watch_streak", None)
        if watch is None:
            return

        notice_raw = getattr(ev, "notice_type", None)
        notice_type = (
            notice_raw.value
            if hasattr(notice_raw, "value")
            else str(notice_raw or "")
        )
        if str(notice_type) != "watch_streak":
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

        alert = alertutils.fetch_streak_alert(streak_count)
        if not alert:
            logger.debug("No streak alert configuration matched, skipping")
            return

        current_timestamp = time.time()
        alert.username = username
        alert.alert_type = "streak"
        alert.streak_count = streak_count
        alert.channel_points_awarded = channel_points_awarded
        alert.message = user_msg
        alert.alert_id = f"Alert{round(current_timestamp)}"
        alert.timestamp = current_timestamp

        alert_processor.ALERT_QUEUE.append(alert)
        alertutils.alert_state_manager.store_completed_alert(
            alert.alert_id, alert.__dict__
        )

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

        add_alert_to_feed(
            alert_type="Streak",
            message=user_msg or f"{username} reached a {streak_count} stream streak!",
            badge_type="streak",
            timestamp=str(int(alert.timestamp)),
            user_message=user_msg,
            alert_id=alert.alert_id,
        )

        try:
            stats_manager = statistics_manager.get_statistics_manager()
            stats_manager.increment_watch_streak_alerts(
                streak_count=streak_count,
                username=username,
                alert_name=getattr(alert, "alert_name", None) or "",
            )
        except Exception as e:
            logger.debug("Watch streak statistics update failed: %s", e)

    async def on_moderate(self, data: ChannelModerateEvent):
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
        alert.emotes = (
            str(data.event.message.emotes)
            if data.event.message and data.event.message.emotes
            else ""
        )  # Use empty string if None
        alert.months_prepaid = data.event.duration_months
        alert.resub_month = cumulative_months
        alert.alert_id = f"Alert{round(current_timestamp)}"
        alert.timestamp = current_timestamp

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

    async def on_new_sub(self, data: ChannelSubscriptionMessageEvent):
        """Handle new subscription events (first-time subscribers)"""
        logger.debug(f"New subscription from {data.event.user_name}")

        username = data.event.user_name
        tier_str = str(data.event.tier)
        tier = int(tier_str[:-3]) if tier_str else 1  # Default to 1 if tier is weird
        user_msg = data.event.message.text if data.event.message else None
        current_timestamp = time.time()

        # Handle as new subscription
        logger.debug(f"Processing as new sub: {username}")
        alert = alertutils.fetch_sub_alert(1)
        alert.username = username
        alert.alert_type = "sub"
        alert.tier = tier
        alert.alert_id = f"Alert{round(current_timestamp)}"
        alert.timestamp = current_timestamp
        alert.message = user_msg or ""  # Use empty string if None

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
        logger.debug(
            f"Gift subscription from {data.event.user_name} for {data.event.total} months"
        )
        alert = alertutils.fetch_giftsub_alert(data.event.total)
        gifter_name = (
            data.event.user_name if not data.event.is_anonymous else "Anonymous Gifter"
        )
        alert.username = gifter_name or "Anonymous Gifter"  # Ensure not None
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
        logger.debug(f"Bits cheered by {data.event.user_name}: {data.event.bits}")
        alert = alertutils.fetch_cheer_alert(data.event.bits)
        alert.username = data.event.user_name
        alert.alert_type = "bit"
        alert.amt_cheered = int(str(data.event.bits))
        alert.alert_id = f"Alert{round(time.time())}"
        alert.timestamp = time.time()
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
                        "amt_cheered": alert.amt_cheered,
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
        logger.debug(f"Bits cheered by {data.event.user_name}: {data.event.bits}")
        alert = alertutils.fetch_cheer_alert(data.event.bits)
        username_display = (
            data.event.user_name if not data.event.is_anonymous else "Anonymous"
        )
        alert.username = username_display or "Anonymous"  # Ensure not None
        alert.alert_type = "bit"
        alert.amt_cheered = int(str(data.event.bits))
        alert.message = (
            data.event.message
        )  # Cheer message is directly data.event.message
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
                    "amt_cheered": alert.amt_cheered,
                    "message": alert.message,
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
        logger.debug(
            f"Raid from {data.event.from_broadcaster_user_name} with {data.event.viewers} viewers"
        )
        alert = alertutils.fetch_raid_alert(data.event.viewers)
        alert.username = data.event.from_broadcaster_user_name
        alert.alert_type = "raid"
        alert.raider_count = int(str(data.event.viewers))
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
            await self.send_shoutout(data.event.from_broadcaster_user_id)
            channel_infos = await self.twitch.get_channel_information(
                data.event.from_broadcaster_user_id
            )
            if channel_infos:
                game_name = (
                    channel_infos[0].game_name
                    if channel_infos[0].game_name
                    else "Unknown"
                )
                shoutout_message = f"HEY CHAT! Check out @{data.event.from_broadcaster_user_name} 's channel: https://twitch.tv/{data.event.from_broadcaster_user_name} . How was {game_name}?"
                await self.send_chat_message(shoutout_message)
                logger.debug(f"Sent shoutout to {alert.username}")
            else:
                logger.error(
                    f"No channel information found for {data.event.from_broadcaster_user_name}"
                )
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
            message=f"{alert.username} raided with {alert.raider_count} viewers!",
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
                }
                web_engine.web_engine_instance.instant_alert(alert_data)
                logger.debug(
                    f"Sent instant alert for unconfigured points redemption: {alert.username}"
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
        )

    async def on_points(self, data: ChannelPointsCustomRewardRedemptionAddEvent):
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
        )

    async def on_hype_train_start(
        self, data: HypeTrainEvent
    ):  # data.event is HypeTrainBeginEventData
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

    def stop_health_check(self):
        """Stop the health check thread"""
        if self.health_check_thread and self.health_check_thread.is_alive():
            # The thread will exit on its own when the main thread exits
            # since it's a daemon thread
            logger.debug("Stopping Twitch connection health check thread")

    def _health_check_loop(self):
        """Background thread that periodically checks connection health and token expiry"""
        global twitch_connected
        while True:
            try:
                # Check if connection is alive
                if (
                    self.eventsub
                    and hasattr(self.eventsub, "active_session")
                    and self.eventsub.active_session is not None
                ):
                    self.last_health_check = datetime.now()
                    self.is_connected = True
                    twitch_connected = True
                    logger.debug("Twitch connection health check passed")
                else:
                    # Check if we've exceeded the timeout
                    if (
                        self.last_health_check
                        and (datetime.now() - self.last_health_check).total_seconds()
                        > self.connection_timeout
                    ):
                        logger.warning(
                            "Twitch connection appears to be dead, attempting to reconnect"
                        )
                        self.is_connected = False
                        twitch_connected = False
                        self.reconnect()
                    else:
                        logger.warning(
                            "Twitch connection health check failed, will retry"
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

                                # Update the existing Twitch instance with new authentication if connected
                                if self.twitch and self.is_connected:
                                    try:
                                        loop.run_until_complete(
                                            self.twitch.set_user_authentication(
                                                self.auth_token,
                                                self.authscope,
                                                self.refresh_token,
                                            )
                                        )
                                        logger.debug(
                                            "Updated existing Twitch instance with refreshed tokens"
                                        )
                                    except Exception as auth_error:
                                        logger.warning(
                                            f"Failed to update Twitch instance auth after proactive refresh: {str(auth_error)}"
                                        )
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

    def reconnect(self):
        """Attempt to reconnect to Twitch"""
        try:
            logger.debug("Attempting to reconnect to Twitch")

            # Stop the current connection if it exists
            if self.eventsub:
                try:
                    # Create a temporary event loop to properly await the stop
                    import asyncio

                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(self.eventsub.stop())
                        logger.debug("EventSub connection stopped for reconnection")
                    finally:
                        loop.close()
                except Exception as e:
                    logger.error(
                        f"Error stopping current Twitch connection: {str(e)}",
                        exc_info=True,
                    )

            # Reset connection state
            self.is_connected = False
            global twitch_connected
            twitch_connected = False
            self.eventsub = None
            self.last_health_check = None

            # Create a new thread for the async reconnection
            reconnect_thread = threading.Thread(
                target=lambda: asyncio.run(self.intialize_twitch_api())
            )
            reconnect_thread.daemon = True
            reconnect_thread.start()

            logger.debug("Twitch reconnection thread started")
        except Exception as e:
            logger.error(
                f"Failed to initiate Twitch reconnection: {str(e)}", exc_info=True
            )

    async def intialize_twitch_api(self):
        global twitch_connected
        logger.debug("Initializing Twitch API websocket connection")
        try:
            # Stage the Twitch API (authenticate)
            staging_success = await self.stage_twitch_api()
            if not staging_success:
                logger.warning("Failed to stage Twitch API - authentication required")
                self.is_connected = False
                twitch_connected = False
                return False  # Return False instead of raising exception

            # Initialize the EventSub websocket
            self.eventsub = EventSubWebsocket(self.twitch)

            # Start the websocket connection first
            self.eventsub.start()

            # Update connection state
            self.is_connected = True
            twitch_connected = True
            self.last_health_check = datetime.now()

            # Register event handlers
            try:
                # Subscribe to channel chat messages
                try:
                    await self.eventsub.listen_channel_chat_message(
                        self.user.id, self.user.id, self.on_chat_message
                    )
                    logger.debug("Successfully subscribed to channel chat messages")
                except Exception as e:
                    logger.error(
                        f"Failed to subscribe to channel chat messages: {str(e)}"
                    )
                    raise

                try:
                    await self.eventsub.listen_channel_chat_notification(
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
                    await self.eventsub.listen_channel_moderate(
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
                    await self.eventsub.listen_channel_update(
                        self.user.id, self.on_update
                    )
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
                    await self.eventsub.listen_channel_follow_v2(
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
                    await self.eventsub.listen_channel_subscription_message(
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
                try:
                    await self.eventsub.listen_channel_subscribe(
                        self.user.id, self.on_new_sub
                    )
                    logger.debug(
                        "Successfully subscribed to channel subscription events"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to subscribe to channel subscription events: {str(e)}"
                    )
                    raise

                await self.eventsub.listen_channel_subscription_gift(
                    self.user.id, self.on_sub_gift
                )
                await self.eventsub.listen_channel_cheer(self.user.id, self.on_cheer)
                await self.eventsub.listen_channel_bits_use(
                    self.user.id, self.on_bits_use
                )
                await self.eventsub.listen_channel_raid(
                    self.on_raid, self.user.id, None
                )
                await self.eventsub.listen_channel_points_custom_reward_redemption_add(
                    self.user.id, self.on_points
                )
                # await self.eventsub.listen_hype_train_begin(
                #     self.user.id, self.on_hype_train_start
                # )
                # await self.eventsub.listen_hype_train_progress(
                #     self.user.id, self.on_hype_train_progress
                # )
                # await self.eventsub.listen_hype_train_end(
                #     self.user.id, self.on_hype_train_end
                # )
            except Exception as e:
                logger.error(f"Error subscribing to events: {str(e)}", exc_info=True)
                self.is_connected = False
                twitch_connected = False
                return False  # Return False instead of raising exception

            # Start the health check thread
            self.start_health_check()

            logger.info("Twitch API websocket connection established successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Twitch API: {str(e)}", exc_info=True)
            self.is_connected = False
            twitch_connected = False
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

        # Check if we have basic auth credentials
        if not self.auth_token or not self.client_id:
            logger.debug("Missing auth tokens, attempting to load from database")
            # Try to load existing auth data first
            auth_loaded = self.load_auth_data()
            if not auth_loaded or not self.auth_token or not self.client_id:
                logger.error(
                    "No valid authentication credentials available for API call"
                )
                raise Exception("Authentication required - no valid tokens available")

        # Check if token is expired and refresh if needed
        if self.is_token_expired():
            logger.info("Token expired, attempting refresh for generic API call")
            refresh_success = await self.refresh_auth_token()
            if not refresh_success:
                logger.error("Token refresh failed for API call")
                raise Exception("Authentication required - token refresh failed")

        # Wait for existing Twitch API instance or check if we need authentication
        if not self.twitch:
            # If there's no Twitch instance and we're not connected, this means
            # the main authentication flow hasn't completed yet
            if not self.is_connected:
                logger.warning(
                    "No authenticated Twitch instance available - main authentication may not be complete"
                )
                raise Exception(
                    "Twitch API not ready - authentication in progress or required"
                )
            else:
                # If we're connected but no twitch instance, something is wrong
                logger.error("Connected but no Twitch instance - this shouldn't happen")
                raise Exception("Twitch API instance missing despite connection status")

        # Verify we have the basic authentication requirements
        if not self.auth_token:
            logger.warning("No auth token available for API call")
            raise Exception("Authentication token missing - authentication required")

        logger.debug(
            "Using existing authenticated Twitch API instance for generic call"
        )

        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.auth_token}",
        }
        # aiohttp sets Content-Type for json parameter automatically
        # If json_data is provided, ensure content type is application/json
        if json_data is not None:
            headers["Content-Type"] = "application/json"

        # Use the existing Twitch session from the authenticated instance
        session_to_use = None
        close_session_after = False

        try:
            # Always try to use the existing session from the authenticated Twitch object
            if (
                self.twitch
                and hasattr(self.twitch, "_Twitch__session")
                and self.twitch._Twitch__session
            ):
                session_to_use = self.twitch._Twitch__session
                logger.debug("Using existing authenticated Twitch session for API call")
            else:
                # Only create a temporary session as a last resort
                logger.warning(
                    "No existing Twitch session available, creating temporary session"
                )
                import aiohttp

                session_to_use = aiohttp.ClientSession()
                close_session_after = True

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

        except aiohttp.ClientError as e:
            logger.error(
                f"aiohttp.ClientError during generic API call to {url}: {str(e)}",
                exc_info=True,
            )
            raise Exception(f"Network error during API call: {str(e)}")
        except Exception as e:
            logger.error(
                f"Error during generic API call to {url}: {str(e)}", exc_info=True
            )
            # Re-raise the exception to be handled by the caller
            raise
        finally:
            # Only close temporary session if we created one
            if close_session_after and session_to_use:
                try:
                    await session_to_use.close()
                    logger.debug("Closed temporary session")
                except Exception as e:
                    logger.warning(f"Error closing temporary session: {str(e)}")

    def stop_connection(self):
        """Stop the current Twitch connection cleanly"""
        try:
            logger.debug("Stopping Twitch connection")

            # Stop health check thread first
            self.stop_health_check()

            # Stop the EventSub websocket connection
            if self.eventsub:
                try:
                    # Create a temporary event loop to properly await the stop
                    import asyncio

                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(self.eventsub.stop())
                        logger.debug("EventSub connection stopped")
                    finally:
                        loop.close()
                except Exception as e:
                    logger.error(
                        f"Error stopping EventSub connection: {str(e)}", exc_info=True
                    )

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
                return False

            # Initialize the connection with new credentials
            await self.intialize_twitch_api()

            logger.debug("Reconnect with OAuth completed successfully")
            return True
        except Exception as e:
            logger.error(f"Error during OAuth reconnection: {str(e)}", exc_info=True)
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
        if self.is_connected and self.eventsub:
            return {
                "status": "Connected",
                "is_valid": True,
                "last_update": self.last_health_check.strftime("%Y-%m-%d %H:%M:%S")
                if self.last_health_check
                else "Unknown",
                "user_name": self.user.display_name if self.user else "Unknown",
            }
        elif self.auth_token and self.refresh_token:
            return {
                "status": "Authenticated but Disconnected",
                "is_valid": False,
                "last_update": "Connection Lost",
                "user_name": self.user.display_name if self.user else "Unknown",
            }
        elif self.client_id and self.client_secret:
            return {
                "status": "Configured but Not Authenticated",
                "is_valid": False,
                "last_update": "Never",
                "user_name": "None",
            }
        else:
            return {
                "status": "Not Configured",
                "is_valid": False,
                "last_update": "Never",
                "user_name": "None",
            }


# Point reward API functions
def _is_channel_points_forbidden(exc: BaseException) -> bool:
    """True when Twitch denies custom rewards (e.g. broadcaster not Affiliate/Partner)."""
    msg = str(exc).lower()
    if "partner or affiliate" in msg:
        return True
    if "403" in msg and "forbidden" in msg:
        return True
    return False


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
        logger.error(f"Error getting point rewards: {str(e)}", exc_info=True)
        if _is_channel_points_forbidden(e):
            return (None, "not_unlocked")
        return (None, "error")


async def get_point_rewards_async():
    """Get all point rewards from Twitch API (async version)"""
    rewards, status = await _fetch_channel_point_rewards_async()
    if status == "ok":
        return rewards
    return None


def fetch_channel_point_rewards():
    """Sync: structured result for UI (rewards list only when status is ok)."""
    import concurrent.futures

    if not twitch_api or not twitch_api.is_connected:
        return {"rewards": None, "status": "not_connected"}

    def run_async():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(_fetch_channel_point_rewards_async())
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"Error in async thread: {str(e)}")
            return (None, "error")

    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_async)
            rewards, status = future.result(timeout=10)
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
        import concurrent.futures

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

        def run_async():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(get_reward_async())
                finally:
                    loop.close()
            except Exception as e:
                logger.error(f"Error in async thread: {str(e)}")
                return None

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_async)
            return future.result(timeout=10)  # 10 second timeout

    except Exception as e:
        logger.error(f"Error getting point reward {reward_id}: {str(e)}", exc_info=True)
        return None


def create_point_reward(reward_data: dict):
    """Create a new point reward on Twitch"""
    try:
        import concurrent.futures

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

        def run_async():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(create_reward_async())
                finally:
                    loop.close()
            except Exception as e:
                logger.error(f"Error in async thread: {str(e)}")
                return None

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_async)
            return future.result(timeout=10)  # 10 second timeout

    except Exception as e:
        logger.error(f"Error creating point reward: {str(e)}", exc_info=True)
        return None


def update_point_reward(reward_id: str, reward_data: dict):
    """Update an existing point reward on Twitch"""
    try:
        import concurrent.futures

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

        def run_async():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(update_reward_async())
                finally:
                    loop.close()
            except Exception as e:
                logger.error(f"Error in async thread: {str(e)}")
                return False

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_async)
            return future.result(timeout=10)  # 10 second timeout

    except Exception as e:
        logger.error(
            f"Error updating point reward {reward_id}: {str(e)}", exc_info=True
        )
        return False


def delete_point_reward(reward_id: str):
    """Delete a point reward from Twitch"""
    try:
        import concurrent.futures

        if not twitch_api or not twitch_api.is_connected:
            logger.error("Twitch API not connected")
            return False

        url = f"https://api.twitch.tv/helix/channel_points/custom_rewards?broadcaster_id={twitch_api.user_id}&id={reward_id}"

        # Use a thread pool to run the async function
        async def delete_reward_async():
            response = await twitch_api.generic_api_call(url, "DELETE")
            return response is not None

        def run_async():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(delete_reward_async())
                finally:
                    loop.close()
            except Exception as e:
                logger.error(f"Error in async thread: {str(e)}")
                return False

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_async)
            return future.result(timeout=10)  # 10 second timeout

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

        # Create a new thread for the async initialization
        init_thread = threading.Thread(
            target=lambda: asyncio.run(twitch_api.intialize_twitch_api())
        )
        init_thread.daemon = True
        init_thread.start()
        logger.debug("Twitch API initialization thread started")

        # Mark as initialized
        _initialized = True


def get_twitch_api():
    """Get the global Twitch API instance"""
    return twitch_api


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
            return {
                "status": "Not Initialized",
                "is_valid": False,
                "last_update": "Never",
                "user_name": "None",
            }

        return twitch_api.get_connection_status()
    except Exception as e:
        logger.error(f"Error getting Twitch connection status: {str(e)}", exc_info=True)
        return {
            "status": "Error",
            "is_valid": False,
            "last_update": "Error",
            "user_name": "Error",
        }


def get_twitch_token_status():
    """Get current Twitch token status for debugging (sync wrapper)"""
    try:
        if not twitch_api:
            return {"status": "Not Initialized", "error": "Twitch API not initialized"}

        return twitch_api.get_token_status()
    except Exception as e:
        logger.error(f"Error getting Twitch token status: {str(e)}", exc_info=True)
        return {"status": "Error", "error": str(e)}
        return twitch_api.get_token_status()
    except Exception as e:
        logger.error(f"Error getting Twitch token status: {str(e)}", exc_info=True)
        return {"status": "Error", "error": str(e)}
