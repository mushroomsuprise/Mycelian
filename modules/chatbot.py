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
from typing import Any, Dict, Optional

import aiohttp
from twitchAPI.helper import first
from twitchAPI.oauth import UserAuthenticator
from twitchAPI.twitch import Twitch
from twitchAPI.type import AuthScope

from . import dataobjects
from .api_credentials_manager import get_chatbot_credentials

logger = logging.getLogger(__name__)

# Global flag to track initialization status
_initialized = False
_init_lock = threading.Lock()

# Global flag to track Chatbot connection status
chatbot_connected = False


class Chatbot_API:
    def __init__(self):
        logger.debug("Initializing Chatbot API class")
        # Reduced auth scope for chatbot - only needs chat messaging
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
        self.token_expiry = None
        self.health_check_thread = None
        self.is_connected = False
        self.last_health_check = None
        self.health_check_interval = 60  # Check connection every 60 seconds
        self.connection_timeout = (
            300  # Consider connection dead after 5 minutes without successful check
        )
        self.using_fallback = (
            False  # Flag to track if we're using main Twitch API as fallback
        )

        # Token refresh synchronization
        self._refresh_lock = threading.Lock()
        self._last_refresh_attempt = None

    def load_auth_data(self):
        """Load chatbot-specific authentication data from the state manager"""
        try:
            # Get chatbot-specific credentials
            chatbot_creds = get_chatbot_credentials()

            if not chatbot_creds.get("client_id") or not chatbot_creds.get(
                "client_secret"
            ):
                logger.info(
                    "No dedicated chatbot credentials configured - will use main Twitch API as fallback"
                )
                return False

            # Use chatbot-specific credentials
            self.client_id = chatbot_creds.get("client_id", "")
            self.client_secret = chatbot_creds.get("client_secret", "")

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

            # Load tokens from state manager ChatbotData
            try:
                chatbot_data = dataobjects.state_manager.get_chatbot_data()
                if chatbot_data:
                    # Load tokens if they exist
                    if chatbot_data.auth_token:
                        self.auth_token = chatbot_data.auth_token
                    if chatbot_data.refresh_token:
                        self.refresh_token = chatbot_data.refresh_token
                    if chatbot_data.user_id:
                        self.user_id = chatbot_data.user_id
                    if chatbot_data.token_expiry:
                        try:
                            from datetime import datetime

                            self.token_expiry = datetime.fromisoformat(
                                chatbot_data.token_expiry
                            )
                        except Exception as e:
                            logger.warning(
                                f"Error parsing chatbot token expiry: {str(e)}"
                            )
                            self.token_expiry = None

                    logger.debug(
                        "Loaded chatbot tokens from state manager (if available)"
                    )
            except Exception as e:
                logger.warning(
                    f"Error loading chatbot tokens from state manager: {str(e)}"
                )
                # Continue without tokens - OAuth will be required

            logger.debug(
                "Dedicated chatbot credentials loaded - OAuth flow will be required if no valid tokens"
            )
            return True

        except Exception as e:
            logger.error(
                f"Error loading chatbot authentication data: {str(e)}", exc_info=True
            )
            return False

    def save_auth_data(self):
        """Save authentication data for chatbot to state manager and database"""
        global chatbot_connected
        try:
            # Check if state manager is initialized
            if (
                not hasattr(dataobjects.state_manager, "_initialized")
                or not dataobjects.state_manager._initialized
            ):
                logger.warning(
                    "State manager not initialized when saving chatbot auth data"
                )
                # Still update connection status flags
                chatbot_connected = True
                self.is_connected = True
                return False

            # Prepare chatbot data dictionary
            chatbot_data = {
                "auth_token": self.auth_token or "",
                "refresh_token": self.refresh_token or "",
                "user_id": self.user_id or "",
                "token_expiry": self.token_expiry.isoformat()
                if self.token_expiry
                else "",
            }

            # Log token details for debugging (without exposing full tokens)
            token_preview = self.auth_token[:10] + "..." if self.auth_token else "None"
            expiry_str = self.token_expiry.isoformat() if self.token_expiry else "None"
            logger.info(
                f"Saving chatbot auth data - Token: {token_preview}, Expiry: {expiry_str}"
            )

            # Save to state manager
            dataobjects.state_manager.set_chatbot_data(chatbot_data)

            # Force save changes to ensure data is persisted immediately
            save_changes_success = dataobjects.state_manager.save_changes()
            if not save_changes_success:
                logger.warning(
                    "Failed to save changes to database when saving chatbot auth data"
                )

            # Update connection status
            chatbot_connected = True
            self.is_connected = True

            logger.debug("Successfully saved chatbot authentication data")
            return True
        except Exception as e:
            logger.error(
                f"Error saving chatbot authentication data: {str(e)}", exc_info=True
            )
            # Still update connection status flags even if save fails
            chatbot_connected = True
            self.is_connected = True
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
            logger.info("Chatbot token refresh already in progress, waiting...")
            # Wait for the other refresh to complete
            with self._refresh_lock:
                logger.info(
                    "Other chatbot token refresh completed, checking if we still need to refresh"
                )
                if not self.is_token_expired():
                    logger.info(
                        "Chatbot token was already refreshed by another process"
                    )
                    return True

        try:
            # Check if we recently attempted a refresh (within last 30 seconds)
            now = datetime.now()
            if (
                self._last_refresh_attempt
                and (now - self._last_refresh_attempt).total_seconds() < 30
            ):
                logger.info(
                    "Chatbot token refresh attempted recently, skipping duplicate refresh"
                )
                return not self.is_token_expired()

            self._last_refresh_attempt = now

            logger.info("Refreshing chatbot authentication token")

            # Check if we have a refresh token
            if not self.refresh_token:
                logger.error("No refresh token available for chatbot token refresh")
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
                logger.error(
                    "Chatbot token refresh failed - tokens are empty or unchanged"
                )

                # Clear invalid tokens
                self.auth_token = ""
                self.refresh_token = ""
                self.token_expiry = None

                # Save the cleared state
                self.save_auth_data()

                logger.warning(
                    "Cleared invalid chatbot tokens - OAuth re-authentication required"
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
                logger.warning("Failed to save refreshed chatbot tokens")

            # Create token previews for logging
            if isinstance(old_auth_token, str) and old_auth_token:
                old_preview = (
                    old_auth_token[:10] + "..."
                    if len(old_auth_token) > 10
                    else old_auth_token
                )
            else:
                old_preview = "None"

            if isinstance(new_auth_token, str) and new_auth_token:
                new_preview = (
                    new_auth_token[:10] + "..."
                    if len(new_auth_token) > 10
                    else new_auth_token
                )
            else:
                new_preview = "None"
            logger.info(
                f"Successfully refreshed chatbot authentication token (old: {old_preview}, new: {new_preview})"
            )
            return True
        except Exception as e:
            logger.error(
                f"Failed to refresh chatbot authentication token: {str(e)}",
                exc_info=True,
            )

            # If refresh completely failed, clear tokens to force re-authentication
            self.auth_token = ""
            self.refresh_token = ""
            self.token_expiry = None

            try:
                self.save_auth_data()
                logger.warning(
                    "Cleared invalid chatbot tokens after refresh failure - OAuth re-authentication required"
                )
            except Exception as save_error:
                logger.error(
                    f"Failed to save cleared chatbot token state: {str(save_error)}"
                )

            return False
        finally:
            # Always release the lock
            try:
                self._refresh_lock.release()
            except Exception:
                pass  # Lock might already be released

    async def authenticate_with_oauth(self):
        """Handle the OAuth flow to get new authentication tokens"""
        try:
            logger.debug("Starting chatbot OAuth authentication flow")

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
            print(f"Chatbot Auth token: {self.auth_token}")
            print(f"Chatbot Refresh token: {self.refresh_token}")
            await self.twitch.set_user_authentication(
                self.auth_token, self.authscope, self.refresh_token
            )

            # Now get user info
            self.user = await first(self.twitch.get_users())
            self.user_id = self.user.id

            # Set token expiry (Twitch tokens typically expire in 60 days)
            self.token_expiry = datetime.now() + timedelta(days=60)

            # Save new tokens
            self.save_auth_data()

            # Explicitly set connection status after OAuth authentication
            global chatbot_connected
            self.is_connected = True
            chatbot_connected = True

            logger.debug(
                f"Successfully authenticated chatbot as user: {self.user.display_name}"
            )
            return True
        except Exception as e:
            logger.error(
                f"Failed to authenticate chatbot with OAuth: {str(e)}", exc_info=True
            )
            return False

    async def stage_chatbot_api(self):
        """Stage the Chatbot API for use"""
        try:
            logger.debug("Staging Chatbot API connection")

            # Try to load existing auth data from database
            auth_data_loaded = self.load_auth_data()

            # Check if we have dedicated chatbot credentials
            if not self.client_id or not self.client_secret:
                logger.info(
                    "No dedicated chatbot credentials - will use main Twitch API as fallback"
                )
                # Set a flag to indicate we're using fallback mode
                self.using_fallback = True
                return (
                    True  # Still return True since we can fallback to main Twitch API
                )

            # We have dedicated credentials, proceed with OAuth flow
            self.using_fallback = False

            # Check if we have valid tokens
            if auth_data_loaded and self.auth_token and self.refresh_token:
                # Check if token is expired and refresh if needed
                if self.is_token_expired():
                    logger.info(
                        "Chatbot auth token expired during staging, attempting to refresh"
                    )
                    refresh_success = await self.refresh_auth_token()
                    if not refresh_success:
                        logger.warning(
                            "Failed to refresh chatbot token during staging, will attempt new authentication"
                        )
                        auth_data_loaded = False
                    else:
                        logger.info(
                            "Successfully refreshed chatbot token during staging"
                        )

            # If we don't have valid auth data, trigger OAuth flow
            if not auth_data_loaded or not self.auth_token or not self.refresh_token:
                logger.info("No valid chatbot authentication data, starting OAuth flow")
                oauth_success = await self.authenticate_with_oauth()
                if not oauth_success:
                    logger.error("Failed to authenticate chatbot with OAuth")
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
                        f"Successfully authenticated chatbot with existing tokens as user: {self.user.display_name}"
                    )

                    # Save the verified auth data
                    self.save_auth_data()

                except Exception as e:
                    logger.warning(
                        f"Existing chatbot tokens failed validation: {str(e)}"
                    )
                    # Try OAuth flow as fallback
                    oauth_success = await self.authenticate_with_oauth()
                    if not oauth_success:
                        logger.error(
                            "Failed to authenticate chatbot with OAuth after token validation failure"
                        )
                        return False  # Return False instead of raising exception

            # Explicitly set connection status and start health check after successful staging
            global chatbot_connected
            self.is_connected = True
            chatbot_connected = True

            # Start health check thread to monitor connection
            self.start_health_check()

            # Notify chatbot manager to start any deferred interval events
            # This is critical for interval events to work properly
            try:
                from .chatbot_manager import get_manager
                manager = get_manager()
                manager.start_deferred_interval_events()
                logger.info("Successfully notified chatbot manager to start deferred interval events")
            except Exception as e:
                logger.error(f"Failed to start deferred interval events: {str(e)}", exc_info=True)
                # Schedule a retry in the background after a short delay
                def retry_start_deferred_events():
                    try:
                        time.sleep(5)  # Wait 5 seconds before retry
                        from .chatbot_manager import get_manager
                        manager = get_manager()
                        manager.start_deferred_interval_events()
                        logger.info("Successfully started deferred interval events on retry")
                    except Exception as retry_error:
                        logger.error(f"Failed to start deferred interval events on retry: {str(retry_error)}", exc_info=True)

                retry_thread = threading.Thread(target=retry_start_deferred_events, daemon=True)
                retry_thread.start()
                logger.info("Scheduled retry for starting deferred interval events")

            logger.info("Chatbot API successfully staged and connected")
            return True  # Successfully staged

        except Exception as e:
            logger.error(f"Failed to stage Chatbot API: {str(e)}", exc_info=True)
            return False  # Return False instead of raising exception

    def start_health_check(self):
        """Start the health check thread"""
        if self.health_check_thread is None or not self.health_check_thread.is_alive():
            self.health_check_thread = threading.Thread(target=self._health_check_loop)
            self.health_check_thread.daemon = True
            self.health_check_thread.start()
            logger.debug("Started chatbot connection health check thread")

    def stop_health_check(self):
        """Stop the health check thread"""
        if self.health_check_thread and self.health_check_thread.is_alive():
            # The thread will exit on its own when the main thread exits
            # since it's a daemon thread
            logger.debug("Stopping chatbot connection health check thread")

    def _health_check_loop(self):
        """Background thread that periodically checks connection health and token expiry"""
        global chatbot_connected
        while True:
            try:
                # Check if connection is alive (simplified check for chatbot)
                if self.is_connected and self.twitch and self.auth_token:
                    self.last_health_check = datetime.now()
                    self.is_connected = True
                    chatbot_connected = True
                    logger.debug("Chatbot connection health check passed")
                else:
                    logger.warning("Chatbot connection health check failed")
                    self.is_connected = False
                    chatbot_connected = False

                # Proactive token expiry check
                if self.auth_token and self.refresh_token and self.is_token_expired():
                    logger.info(
                        "Chatbot token expired during health check, attempting proactive refresh"
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
                                    "Chatbot token successfully refreshed during health check"
                                )
                        finally:
                            loop.close()
                    except Exception as e:
                        logger.error(
                            f"Error refreshing chatbot token during health check: {str(e)}",
                            exc_info=True,
                        )

                # Check if chatbot manager has deferred interval events that need to be started
                if self.is_connected:
                    try:
                        from .chatbot_manager import get_manager
                        manager = get_manager()
                        # Check if there are any deferred interval events and try to start them
                        if hasattr(manager, '_deferred_interval_events') and manager._deferred_interval_events:
                            logger.info(f"Health check: Found {len(manager._deferred_interval_events)} deferred interval events, attempting to start them")
                            manager.start_deferred_interval_events()
                        else:
                            deferred_count = len(manager._deferred_interval_events) if hasattr(manager, '_deferred_interval_events') else 0
                            logger.debug(f"Health check: No deferred interval events (count: {deferred_count})")
                    except Exception as e:
                        logger.error(f"Error checking for deferred interval events during health check: {str(e)}", exc_info=True)
                else:
                    logger.debug("Health check: Chatbot not connected, skipping deferred event check")

                # Sleep for the check interval
                time.sleep(self.health_check_interval)
            except Exception as e:
                logger.error(
                    f"Error in chatbot health check thread: {str(e)}", exc_info=True
                )
                self.is_connected = False
                chatbot_connected = False
                # Sleep a bit before retrying
                time.sleep(10)

    async def send_chat_message(
        self, message: str, reply_to_message_id: Optional[str] = None
    ) -> bool:
        """Send a chat message as the chatbot"""
        try:
            logger.debug(f"send_chat_message called with message: {message[:50]}...")

            # Check if we're using fallback mode (no dedicated chatbot credentials)
            if self.using_fallback:
                logger.debug(
                    "Using main Twitch API for chatbot message (fallback mode)"
                )
                return await self._send_via_main_twitch_api(
                    message, reply_to_message_id
                )

            # Use dedicated chatbot instance
            if not self.is_connected or not self.twitch or not self.user_id:
                logger.error(f"Chatbot not connected - cannot send message. is_connected: {self.is_connected}, twitch: {self.twitch is not None}, user_id: {self.user_id}")
                return False

            # Check if token is expired and refresh if needed
            if self.is_token_expired():
                logger.info("Token expired before sending message, attempting refresh")
                refresh_success = await self.refresh_auth_token()
                if not refresh_success:
                    logger.error("Failed to refresh token before sending message")
                    return False

            # Always use the main Twitch account's channel ID as broadcaster_id
            # The sender_id should be the chatbot account's ID (for authentication)
            from . import twitch

            if not twitch.twitch_api or not twitch.twitch_api.user_id:
                logger.error(
                    "Main Twitch API not available - cannot get broadcaster_id"
                )
                return False

            main_channel_id = twitch.twitch_api.user_id

            # Prepare the message data
            message_data = {
                "broadcaster_id": main_channel_id,  # Always use main account's channel
                "sender_id": self.user_id,  # Bot account sends the message
                "message": message,
            }

            logger.info(f"Sending to channel {main_channel_id} (main account) as sender {self.user_id} (bot account)")

            # Add reply information if provided
            if reply_to_message_id:
                message_data["reply_parent_message_id"] = reply_to_message_id

            # Send the message using Twitch API
            url = f"https://api.twitch.tv/helix/chat/messages"
            logger.info(f"Sending message (dedicated mode): {message}")
            response = await self.generic_api_call(url, "POST", json_data=message_data)

            if response:
                logger.info(f"✅essage sent successfully: {message}")
                return True
            else:
                logger.error(f"Failed to send message: {message}")
                return False

        except Exception as e:
            logger.error(f"Error sending chatbot message: {str(e)}", exc_info=True)
            return False

    async def _send_via_main_twitch_api(
        self, message: str, reply_to_message_id: Optional[str] = None
    ) -> bool:
        """Send message using the main Twitch API instance"""
        try:
            from . import twitch

            if not twitch.twitch_api or not twitch.twitch_api.is_connected:
                logger.error("Main Twitch API not available for chatbot fallback")
                return False

            # Prepare the message data using the main Twitch API's user ID
            message_data = {
                "broadcaster_id": twitch.twitch_api.user_id,
                "sender_id": twitch.twitch_api.user_id,
                "message": message,
            }

            if reply_to_message_id:
                message_data["reply_parent_message_id"] = reply_to_message_id

            # Use the main Twitch API's generic_api_call method
            url = f"https://api.twitch.tv/helix/chat/messages"
            print(f"[CHATBOT] Sending message (fallback mode): {message}")
            response = await twitch.twitch_api.generic_api_call(
                url, "POST", json_data=message_data
            )

            if response:
                print(f"[CHATBOT] Message sent successfully (fallback): {message}")
                logger.debug(f"Chatbot sent message via main Twitch API: {message}")
                return True
            else:
                print(f"[CHATBOT] Failed to send message (fallback): {message}")
                logger.error("Failed to send chatbot message via main Twitch API")
                return False

        except Exception as e:
            logger.error(
                f"Error sending chatbot message via main Twitch API: {str(e)}",
                exc_info=True,
            )
            return False

    async def send_chat_announcement(
        self, message: str, color: str = "primary"
    ) -> bool:
        """Send a chat announcement as the chatbot"""
        try:
            # Check if we're using fallback mode (no dedicated chatbot credentials)
            if self.using_fallback:
                logger.debug(
                    "Using main Twitch API for chatbot announcement (fallback mode)"
                )
                return await self._send_announcement_via_main_twitch_api(message, color)

            # Use dedicated chatbot instance
            if not self.is_connected or not self.twitch or not self.user_id:
                logger.error("Chatbot not connected - cannot send announcement")
                return False

            # Check if token is expired and refresh if needed
            if self.is_token_expired():
                logger.info(
                    "Token expired before sending announcement, attempting refresh"
                )
                refresh_success = await self.refresh_auth_token()
                if not refresh_success:
                    logger.error("Failed to refresh token before sending announcement")
                    return False

            # Validate color parameter
            valid_colors = ["primary", "blue", "green", "orange", "purple"]
            if color not in valid_colors:
                color = "primary"

            # Always use the main Twitch account's channel ID as broadcaster_id
            # The moderator_id should be the chatbot account's ID (for authentication)
            from . import twitch

            if not twitch.twitch_api or not twitch.twitch_api.user_id:
                logger.error(
                    "Main Twitch API not available - cannot get broadcaster_id"
                )
                return False

            main_channel_id = twitch.twitch_api.user_id

            # Prepare the announcement data
            announcement_data = {
                "broadcaster_id": main_channel_id,  # Always use main account's channel
                "moderator_id": self.user_id,  # Bot account sends the announcement
                "message": message,
                "color": color,
            }

            print(
                f"[CHATBOT] Sending announcement to channel {main_channel_id} (main account) as moderator {self.user_id} (bot account)"
            )

            # Send the announcement using Twitch API
            url = f"https://api.twitch.tv/helix/chat/announcements"
            print(
                f"[CHATBOT] Sending announcement (dedicated mode): {message} (color: {color})"
            )
            response = await self.generic_api_call(
                url, "POST", json_data=announcement_data
            )

            if response:
                print(f"[CHATBOT] Announcement sent successfully: {message}")
                logger.debug(f"Chatbot sent announcement: {message}")
                return True
            else:
                print(f"[CHATBOT] Failed to send announcement: {message}")
                logger.error("Failed to send chatbot announcement")
                return False

        except Exception as e:
            logger.error(f"Error sending chatbot announcement: {str(e)}", exc_info=True)
            return False

    async def _send_announcement_via_main_twitch_api(
        self, message: str, color: str = "primary"
    ) -> bool:
        """Send announcement using the main Twitch API instance"""
        try:
            from . import twitch

            if not twitch.twitch_api or not twitch.twitch_api.is_connected:
                logger.error("Main Twitch API not available for chatbot fallback")
                return False

            # Validate color parameter
            valid_colors = ["primary", "blue", "green", "orange", "purple"]
            if color not in valid_colors:
                color = "primary"

            # Prepare the announcement data using the main Twitch API's user ID
            announcement_data = {
                "broadcaster_id": twitch.twitch_api.user_id,
                "moderator_id": twitch.twitch_api.user_id,
                "message": message,
                "color": color,
            }

            # Use the main Twitch API's generic_api_call method
            url = f"https://api.twitch.tv/helix/chat/announcements"
            print(
                f"[CHATBOT] Sending announcement (fallback mode): {message} (color: {color})"
            )
            response = await twitch.twitch_api.generic_api_call(
                url, "POST", json_data=announcement_data
            )

            if response:
                print(f"[CHATBOT] Announcement sent successfully (fallback): {message}")
                logger.debug(
                    f"Chatbot sent announcement via main Twitch API: {message}"
                )
                return True
            else:
                print(f"[CHATBOT] Failed to send announcement (fallback): {message}")
                logger.error("Failed to send chatbot announcement via main Twitch API")
                return False

        except Exception as e:
            logger.error(
                f"Error sending chatbot announcement via main Twitch API: {str(e)}",
                exc_info=True,
            )
            return False

    async def generic_api_call(
        self,
        url: str,
        method: str = "GET",
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
    ) -> dict:
        """
        Makes a generic call to the Twitch API for chatbot operations.

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
            f"Attempting chatbot generic API call: {method.upper()} {url} with params={params}, json_data={json_data}"
        )

        # Check if we have basic auth credentials
        if not self.auth_token or not self.client_id:
            logger.debug(
                "Missing chatbot auth tokens, attempting to load from credentials"
            )
            # Try to load existing auth data first
            auth_loaded = self.load_auth_data()
            if not auth_loaded or not self.auth_token or not self.client_id:
                logger.error(
                    "No valid chatbot authentication credentials available for API call"
                )
                raise Exception(
                    "Chatbot authentication required - no valid tokens available"
                )

        # Check if token is expired and refresh if needed
        if self.is_token_expired():
            logger.info(
                "Chatbot token expired, attempting refresh for generic API call"
            )
            refresh_success = await self.refresh_auth_token()
            if not refresh_success:
                logger.error("Chatbot token refresh failed for API call")
                raise Exception(
                    "Chatbot authentication required - token refresh failed"
                )

        # Verify we have the basic authentication requirements
        if not self.auth_token:
            logger.warning("No chatbot auth token available for API call")
            raise Exception(
                "Chatbot authentication token missing - authentication required"
            )

        logger.debug(
            "Using existing authenticated chatbot Twitch API instance for generic call"
        )

        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.auth_token}",
        }
        # aiohttp sets Content-Type for json parameter automatically
        # If json_data is provided, ensure content type is application/json
        if json_data is not None:
            headers["Content-Type"] = "application/json"

        import aiohttp

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
                        f"Chatbot generic API call successful ({response.status}): {url}"
                    )
                    # If response_data is not dict (e.g. for 204 No Content, it might be empty string), wrap it
                    return (
                        response_data
                        if isinstance(response_data, dict)
                        else {"status": response.status, "data": response_data}
                    )
                else:
                    logger.error(
                        f"Chatbot generic API call failed ({response.status}): {url} - Response: {response_data}"
                    )
                    # If we get a 401, try to refresh the token once more
                    if response.status == 401:
                        logger.warning(
                            "Received 401 Unauthorized, attempting chatbot token refresh and retry"
                        )
                        try:
                            refresh_success = await self.refresh_auth_token()
                            if refresh_success:
                                # Update headers with new token
                                headers["Authorization"] = f"Bearer {self.auth_token}"

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
                                            f"Chatbot generic API call succeeded after token refresh ({retry_response.status}): {url}"
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
                                            f"Chatbot generic API call still failed after token refresh ({retry_response.status}): {url} - Response: {retry_data}"
                                        )
                                        raise Exception(
                                            f"Twitch API Error {retry_response.status}: {retry_data}"
                                        )
                            else:
                                logger.error(
                                    "Chatbot token refresh failed, cannot retry API call"
                                )
                                raise Exception(
                                    f"Chatbot authentication failed - token refresh unsuccessful"
                                )
                        except Exception as refresh_error:
                            logger.error(
                                f"Error during chatbot token refresh retry: {str(refresh_error)}"
                            )
                            raise Exception(
                                f"Chatbot authentication failed - {str(refresh_error)}"
                            )
                    else:
                        raise Exception(
                            f"Twitch API Error {response.status}: {response_data}"
                        )

        try:
            if (
                self.twitch
                and hasattr(self.twitch, "_Twitch__session")
                and getattr(self.twitch, "_Twitch__session", None)
            ):
                logger.debug(
                    "Using existing authenticated chatbot Twitch session for API call"
                )
                return await _perform_request(
                    getattr(self.twitch, "_Twitch__session")
                )
            if self.auth_token and self.client_id:
                logger.debug(
                    "Chatbot Twitch HTTP session not open yet; using ephemeral session for Helix call"
                )
                from .twitch import _ephemeral_client_session

                async with _ephemeral_client_session() as session:
                    return await _perform_request(session)
            logger.debug(
                "Chatbot library client not initialized; "
                "using ephemeral session for Helix API call"
            )
            from .twitch import _ephemeral_client_session

            async with _ephemeral_client_session() as session:
                return await _perform_request(session)
        except aiohttp.ClientError as e:
            logger.error(
                f"aiohttp.ClientError during chatbot generic API call to {url}: {str(e)}",
                exc_info=True,
            )
            raise Exception(f"Network error during chatbot API call: {str(e)}") from e
        except Exception as e:
            logger.error(
                f"Error during chatbot generic API call to {url}: {str(e)}",
                exc_info=True,
            )
            raise

    def get_connection_status(self):
        """Get current chatbot connection status for UI display"""
        from .twitch import build_token_timing_fields

        if self.using_fallback:
            # Using main Twitch API as fallback — show main account token timing.
            from . import twitch

            main_api = twitch.twitch_api
            if main_api and main_api.is_connected:
                status = {
                    "status": "Connected (Fallback Mode)",
                    "is_valid": True,
                    "last_update": "Using main Twitch API",
                    "user_name": main_api.user.display_name
                    if main_api.user
                    else "Main Account",
                }
            else:
                status = {
                    "status": "Disconnected (Fallback Mode)",
                    "is_valid": False,
                    "last_update": "Main API unavailable",
                    "user_name": "None",
                }
            status.update(
                build_token_timing_fields(
                    token_expiry=main_api.token_expiry if main_api else None,
                    has_auth_token=bool(main_api and main_api.auth_token),
                )
            )
            return status

        if self.is_connected and self.twitch:
            status = {
                "status": "Connected (Dedicated)",
                "is_valid": True,
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
                "status": "Not Configured (Using Fallback)",
                "is_valid": False,
                "last_update": "Will use main Twitch API",
                "user_name": "None",
            }

        status.update(
            build_token_timing_fields(
                token_expiry=self.token_expiry,
                has_auth_token=bool(self.auth_token),
            )
        )
        return status

    def get_token_status(self):
        """Get detailed chatbot token status for debugging"""
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
            logger.error(f"Error getting chatbot token status: {str(e)}", exc_info=True)
            return {"error": str(e)}


# Global instance
chatbot_api = None


def initialize() -> None:
    """Initialize the Chatbot API and start the websocket connection"""
    current_process = multiprocessing.current_process()
    logger = logging.getLogger(__name__)

    logger.debug(
        f"Chatbot initialize called from process: {current_process.name} (pid: {current_process.pid})"
    )

    # Only initialize in the main process
    if current_process.name != "MainProcess":
        logger.debug(
            f"Skipping chatbot initialization in {current_process.name} process"
        )
        return

    global chatbot_api, _initialized

    # Use a lock to prevent multiple initializations
    with _init_lock:
        if _initialized:
            logger.debug("Chatbot API already initialized, skipping")
            return

        logger.debug("Starting Chatbot API initialization")

        # Create a new instance
        chatbot_api = Chatbot_API()

        # Create a new thread for the async initialization
        init_thread = threading.Thread(
            target=lambda: asyncio.run(chatbot_api.stage_chatbot_api())
        )
        init_thread.daemon = True
        init_thread.start()
        logger.debug("Chatbot API initialization thread started")

        # Mark as initialized
        _initialized = True


def get_chatbot_api():
    """Get the global Chatbot API instance"""
    return chatbot_api


def is_chatbot_connected():
    """Check if the chatbot is connected and ready to send messages"""
    global chatbot_connected, chatbot_api

    # Debug logging for connection status
    logger.debug(f"is_chatbot_connected check - global chatbot_connected: {chatbot_connected}, chatbot_api exists: {chatbot_api is not None}")

    if chatbot_api:
        logger.debug(f"chatbot_api details - is_connected: {chatbot_api.is_connected if hasattr(chatbot_api, 'is_connected') else 'N/A'}, using_fallback: {chatbot_api.using_fallback if hasattr(chatbot_api, 'using_fallback') else 'N/A'}")

    # If we have a dedicated chatbot API and it's connected
    if chatbot_connected and chatbot_api and not chatbot_api.using_fallback:
        logger.debug("is_chatbot_connected: Returning True (dedicated chatbot connected)")
        return True

    # If we're using fallback mode, check if main Twitch API is available
    if chatbot_api and chatbot_api.using_fallback:
        from . import twitch
        main_connected = twitch.twitch_api and twitch.twitch_api.is_connected
        logger.debug(f"is_chatbot_connected: Using fallback mode, main Twitch connected: {main_connected}")
        return main_connected

    logger.debug("is_chatbot_connected: Returning False (no valid connection)")
    return False


def get_chatbot_connection_status():
    """Get current chatbot connection status (sync wrapper)"""
    try:
        if not chatbot_api:
            return {
                "status": "Not Initialized",
                "is_valid": False,
                "last_update": "Never",
                "user_name": "None",
            }

        return chatbot_api.get_connection_status()
    except Exception as e:
        logger.error(
            f"Error getting chatbot connection status: {str(e)}", exc_info=True
        )
        return {
            "status": "Error",
            "is_valid": False,
            "last_update": "Error",
            "user_name": "Error",
        }


def get_chatbot_token_status():
    """Get current chatbot token status for debugging (sync wrapper)"""
    try:
        if not chatbot_api:
            return {"status": "Not Initialized", "error": "Chatbot API not initialized"}

        return chatbot_api.get_token_status()
    except Exception as e:
        logger.error(f"Error getting chatbot token status: {str(e)}", exc_info=True)
        return {"status": "Error", "error": str(e)}


# External callable functions for sending messages as the bot
def send_chatbot_message(
    message: str, reply_to_message_id: Optional[str] = None
) -> bool:
    """Send a chat message as the chatbot (sync wrapper)"""
    try:
        import concurrent.futures

        logger.info(f"send_chatbot_message() called with: {message}")

        # Check if chatbot is available (either dedicated or fallback mode)
        if not chatbot_api:
            logger.warning("Chatbot API not initialized")
            return False

        # If we have a dedicated chatbot API, check if it's connected
        if not chatbot_api.using_fallback:
            logger.info(f"Using dedicated mode - chatbot_connected={chatbot_connected}, is_connected={chatbot_api.is_connected}")
            # For dedicated credentials, check both the global flag and instance status
            if not chatbot_connected and not chatbot_api.is_connected:
                logger.error("Chatbot API not connected (dedicated mode)")
                return False
            # If either flag indicates connection, proceed (dedicated API)
            logger.info("Connection check passed, proceeding with dedicated API")
        # If we're using fallback mode, check if main Twitch API is available
        elif chatbot_api.using_fallback:
            logger.info("Using fallback mode")
            from . import twitch

            if not twitch.twitch_api or not twitch.twitch_api.is_connected:
                logger.error("Main Twitch API not available for chatbot fallback")
                return False
        else:
            logger.error("Chatbot API not connected")
            return False

        # Use a thread pool to run the async function
        def run_async():
            try:
                logger.debug(f"Starting async thread for chatbot message: {message[:50]}...")
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    result = loop.run_until_complete(
                        chatbot_api.send_chat_message(message, reply_to_message_id)
                    )
                    logger.debug(f"Async message send result: {result}")
                    return result
                finally:
                    loop.close()
            except Exception as e:
                logger.error(f"Error in async chatbot message thread: {str(e)}", exc_info=True)
                return False

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_async)
            return future.result(timeout=10)  # 10 second timeout

    except Exception as e:
        logger.error(f"Error sending chatbot message: {str(e)}", exc_info=True)
        return False


def trigger_chatbot_oauth_reconnection():
    """Trigger chatbot OAuth reconnection from the UI (sync wrapper)"""
    try:
        import concurrent.futures
        import threading

        logger = logging.getLogger(__name__)

        if not chatbot_api:
            logger.error("Chatbot API not initialized")
            return False

        # Reload credentials from API credentials manager to ensure we have the latest
        logger.debug("Reloading chatbot credentials before OAuth connection")
        load_success = chatbot_api.load_auth_data()
        if not load_success:
            logger.warning("Failed to load chatbot credentials")
            return False

        # Check if we have dedicated chatbot credentials after reloading
        if not chatbot_api.client_id or not chatbot_api.client_secret:
            logger.warning("No dedicated chatbot credentials configured")
            return False

        # Create a thread to run the async reconnection
        def oauth_thread():
            try:
                import asyncio

                # Force re-authentication by clearing existing tokens
                chatbot_api.auth_token = None
                chatbot_api.refresh_token = None
                chatbot_api.save_auth_data()

                # Re-stage the API which will trigger OAuth flow
                success = asyncio.run(chatbot_api.stage_chatbot_api())
                return success
            except Exception as e:
                logger.error(f"Error in chatbot OAuth thread: {str(e)}", exc_info=True)
                return False

        # Run in thread pool to avoid blocking
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(oauth_thread)
            try:
                return future.result(timeout=120)  # 2 minute timeout
            except concurrent.futures.TimeoutError:
                logger.error("Chatbot OAuth reconnection timed out")
                return False

    except Exception as e:
        logger.error(
            f"Error triggering chatbot OAuth reconnection: {str(e)}", exc_info=True
        )
        return False


def send_chatbot_announcement(message: str, color: str = "primary") -> bool:
    """Send a chat announcement as the chatbot (sync wrapper)"""
    try:
        import concurrent.futures

        # Check if chatbot is available (either dedicated or fallback mode)
        if not chatbot_api:
            print("[CHATBOT] ERROR: Chatbot API not initialized (announcement)")
            logger.error("Chatbot API not initialized")
            return False

        logger.debug(f"send_chatbot_announcement() called with: {message} (color: {color})")

        # If we have a dedicated chatbot API, check if it's connected
        if not chatbot_api.using_fallback:
            logger.debug(f"Using dedicated mode (announcement) - chatbot_connected={chatbot_connected}, is_connected={chatbot_api.is_connected}")
            # For dedicated credentials, check both the global flag and instance status
            if not chatbot_connected and not chatbot_api.is_connected:
                logger.error("Chatbot API not connected (dedicated mode)")
                return False
            # If either flag indicates connection, proceed (dedicated API)
            logger.debug("Connection check passed, proceeding with dedicated API (announcement)")
        # If we're using fallback mode, check if main Twitch API is available
        elif chatbot_api.using_fallback:
            print("[CHATBOT] Using fallback mode (announcement)")
            from . import twitch

            if not twitch.twitch_api or not twitch.twitch_api.is_connected:
                print(
                    "[CHATBOT] ERROR: Main Twitch API not available for chatbot fallback (announcement)"
                )
                logger.error("Main Twitch API not available for chatbot fallback")
                return False
        else:
            print("[CHATBOT] ERROR: Chatbot API not connected (announcement)")
            logger.error("Chatbot API not connected")
            return False

        # Use a thread pool to run the async function
        def run_async():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(
                        chatbot_api.send_chat_announcement(message, color)
                    )
                finally:
                    loop.close()
            except Exception as e:
                logger.error(f"Error in async chatbot announcement thread: {str(e)}")
                return False

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_async)
            return future.result(timeout=10)  # 10 second timeout

    except Exception as e:
        logger.error(f"Error sending chatbot announcement: {str(e)}", exc_info=True)
        return False
