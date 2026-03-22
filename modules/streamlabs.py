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

# pylint: disable=line-too-long, too-many-lines, global-statement, invalid-name, bare-except, wrong-import-order, missing-module-docstring, unused-argument, unused-variable
import logging
import os
import secrets
import string
import threading
import time
import urllib.parse
import webbrowser
from typing import Any, Dict, Optional

import requests
import socketio
from flask import Flask, request
from requests_oauthlib import OAuth2Session

# Allow insecure transport for localhost OAuth flows
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# Import database manager
from . import alert_processor, database_manager

# Import alert system
from .alertutils import AlertObj, fetch_donation_alert
from .api_credentials_manager import get_encrypted_streamlabs_socket_token

# Import StateManager for centralized data management
from .dataobjects import state_manager

# Import encryption utilities
from .encryption_utils import ensure_decrypted, ensure_encrypted

# Import for activity feed
from .uiwindows.activity_feed import add_alert_to_feed

logger = logging.getLogger(__name__)


class StreamlabsIntegration:
    """
    Streamlabs API integration class for OAuth flow and donation alerts.

    Socket Token Refresh Strategy:
    - Socket tokens are always refreshed during authentication/token refresh
    - This ensures compatibility since Streamlabs doesn't document socket token longevity
    - OAuth socket tokens are preferred over static tokens for better reliability
    - Socket connections are re-established after token refresh for clean state
    """

    def __init__(self):
        self.client_id = ""
        self.client_secret = ""
        self.redirect_uri = (
            "http://127.0.0.1:5001/streamlabs"  # Changed to HTTP to avoid SSL issues
        )
        self.authorization_base_url = "https://streamlabs.com/api/v2.0/authorize"
        self.token_url = "https://streamlabs.com/api/v2.0/token"
        self.scope = ["donations.read", "alerts.create", "socket.token", "alerts.write"]

        # Connection state
        self.is_connected = False
        self.socket_client = None
        self.oauth_session = None
        self.flask_app = None
        self.flask_thread = None
        self.oauth_state = None
        self.oauth_code = None

        # Background token monitoring
        self.token_monitor_thread = None
        self.token_monitor_running = False
        self.token_check_interval = 300  # Check every 5 minutes

        # Load credentials from state manager
        self._load_credentials()

    def _load_credentials(self):
        """Load Streamlabs credentials from state manager"""
        try:
            state_manager.initialize()
            streamlabs_data = state_manager.get_streamlabs_data()
            if streamlabs_data:
                # Try to decrypt credentials when loading, fallback to plain text
                try:
                    self.client_id = (
                        ensure_decrypted(streamlabs_data.client_id)
                        if streamlabs_data.client_id
                        else ""
                    )
                    self.client_secret = (
                        ensure_decrypted(streamlabs_data.client_secret)
                        if streamlabs_data.client_secret
                        else ""
                    )
                except:
                    # Fallback to treating as plain text if decryption fails
                    self.client_id = streamlabs_data.client_id or ""
                    self.client_secret = streamlabs_data.client_secret or ""
                logger.debug("Loaded Streamlabs credentials from state manager")
            else:
                logger.warning("No Streamlabs credentials found in state manager")
                self.client_id = ""
                self.client_secret = ""

            # If credentials are still empty, try to load from API credentials manager
            if not self.client_id or not self.client_secret:
                try:
                    from .api_credentials_manager import api_credentials_manager

                    api_creds = api_credentials_manager.get_streamlabs_credentials()
                    if api_creds:
                        if not self.client_id and api_creds.get("client_id"):
                            self.client_id = api_creds["client_id"]
                            logger.debug(
                                "Loaded Streamlabs client_id from API credentials manager"
                            )
                        if not self.client_secret and api_creds.get("client_secret"):
                            self.client_secret = api_creds["client_secret"]
                            logger.debug(
                                "Loaded Streamlabs client_secret from API credentials manager"
                            )
                except Exception as e:
                    logger.warning(
                        f"Could not load from API credentials manager: {str(e)}"
                    )

            if self.client_id and self.client_secret:
                logger.info("Successfully loaded Streamlabs credentials")
            else:
                logger.warning("Streamlabs credentials not configured")

        except Exception as e:
            logger.error(
                f"Error loading Streamlabs credentials: {str(e)}", exc_info=True
            )
            self.client_id = ""
            self.client_secret = ""

    def update_connection_status(self, status: str):
        """Update the connection status in state manager"""
        try:
            state_manager.update_streamlabs_field("connection_status", status)
            logger.debug(f"Updated Streamlabs connection status to: {status}")
        except Exception as e:
            logger.error(f"Error updating Streamlabs connection status: {str(e)}")

    def get_oauth_url(self) -> Optional[str]:
        """Generate OAuth authorization URL"""
        try:
            if not self.client_id:
                raise ValueError("Client ID not configured")

            # Create OAuth session
            self.oauth_session = OAuth2Session(
                self.client_id, redirect_uri=self.redirect_uri, scope=self.scope
            )

            # Generate authorization URL with state
            authorization_url, state = self.oauth_session.authorization_url(
                self.authorization_base_url
            )

            self.oauth_state = state
            logger.info(f"Generated OAuth URL: {authorization_url}")
            return authorization_url

        except Exception as e:
            logger.error(f"Error generating OAuth URL: {str(e)}", exc_info=True)
            return None

    def start_oauth_server(self):
        """Start Flask server to handle OAuth callback"""
        try:
            if self.flask_app is not None:
                logger.warning("OAuth server already running")
                return

            # Create Flask app
            self.flask_app = Flask(__name__)

            # Suppress Flask logging
            logging.getLogger("werkzeug").setLevel(logging.ERROR)

            @self.flask_app.route("/streamlabs")
            def oauth_callback():
                try:
                    self.oauth_state = request.args.get("state")
                    self.oauth_code = request.args.get("code")
                    error = request.args.get("error")

                    if error:
                        logger.error(f"OAuth error: {error}")
                        return f"<h1>OAuth Error</h1><p>{error}</p><p>You can close this window.</p>"

                    if self.oauth_code:
                        logger.info("OAuth code received successfully")
                        return "<h1>Success!</h1><p>You can close this window and return to Mycelian.</p>"
                    else:
                        logger.error("No OAuth code received")
                        return "<h1>Error</h1><p>No authorization code received. You can close this window.</p>"

                except Exception as e:
                    logger.error(f"Error in OAuth callback: {str(e)}", exc_info=True)
                    return f"<h1>Error</h1><p>{str(e)}</p><p>You can close this window.</p>"

            # Start Flask server in a separate thread
            def run_server():
                try:
                    # Use HTTP instead of HTTPS to avoid SSL certificate issues
                    self.flask_app.run(
                        host="127.0.0.1", port=5001, debug=False, use_reloader=False
                    )
                except Exception as e:
                    logger.error(f"Error running OAuth server: {str(e)}", exc_info=True)

            self.flask_thread = threading.Thread(target=run_server, daemon=True)
            self.flask_thread.start()

            logger.info("OAuth server started on http://127.0.0.1:5001")

        except Exception as e:
            logger.error(f"Error starting OAuth server: {str(e)}", exc_info=True)

    def stop_oauth_server(self):
        """Stop the OAuth server"""
        try:
            if self.flask_app:
                # Flask doesn't have a built-in way to stop from another thread
                # The server will stop when the thread ends or the process exits
                self.flask_app = None
                logger.info("OAuth server marked for shutdown")
        except Exception as e:
            logger.error(f"Error stopping OAuth server: {str(e)}", exc_info=True)

    def start_token_monitor(self):
        """Start the background token monitoring thread"""
        try:
            if self.token_monitor_thread and self.token_monitor_thread.is_alive():
                logger.info("Token monitor already running")
                return

            self.token_monitor_running = True
            self.token_monitor_thread = threading.Thread(
                target=self._token_monitor_loop,
                daemon=True,
                name="StreamlabsTokenMonitor",
            )
            self.token_monitor_thread.start()
            logger.info("Started Streamlabs token monitor (checks every 5 minutes)")

        except Exception as e:
            logger.error(f"Error starting token monitor: {str(e)}", exc_info=True)

    def stop_token_monitor(self):
        """Stop the background token monitoring thread"""
        try:
            self.token_monitor_running = False
            if self.token_monitor_thread and self.token_monitor_thread.is_alive():
                self.token_monitor_thread.join(timeout=5)
                logger.info("Stopped Streamlabs token monitor")
        except Exception as e:
            logger.error(f"Error stopping token monitor: {str(e)}", exc_info=True)

    def _token_monitor_loop(self):
        """Background loop that periodically checks and refreshes tokens"""
        logger.debug("Token monitor loop started")

        while self.token_monitor_running:
            try:
                # Check tokens every 5 minutes
                time.sleep(self.token_check_interval)

                if not self.token_monitor_running:
                    break

                logger.debug("Performing periodic token validation...")

                # Perform token validation and refresh
                self._check_and_refresh_tokens()

            except Exception as e:
                logger.error(f"Error in token monitor loop: {str(e)}", exc_info=True)
                # Continue running despite errors
                time.sleep(60)  # Wait a minute before retrying

        logger.debug("Token monitor loop ended")

    def _check_and_refresh_tokens(self):
        """Check token validity and refresh if necessary"""
        try:
            streamlabs_data = state_manager.get_streamlabs_data()
            if not streamlabs_data:
                return

            needs_refresh = False
            reason = ""

            # Check if we have tokens
            if not streamlabs_data.access_token:
                logger.debug("No access token to validate")
                return

            # Check token expiry (with 10 minute buffer)
            if streamlabs_data.token_expiry:
                time_until_expiry = streamlabs_data.token_expiry - time.time()
                if time_until_expiry < 600:  # Less than 10 minutes
                    needs_refresh = True
                    reason = f"Token expires in {time_until_expiry:.0f} seconds"
            else:
                # No expiry time stored, check validity
                if not self.test_access_token():
                    needs_refresh = True
                    reason = "Token validation failed"

            if needs_refresh:
                logger.info(f"Token refresh needed: {reason}")

                # Try to refresh the token
                if streamlabs_data.refresh_token:
                    if self.refresh_token():
                        logger.info(
                            "Successfully refreshed Streamlabs tokens in background"
                        )

                        # Also refresh socket token
                        socket_token = self.get_socket_token()
                        if socket_token:
                            state_manager.update_streamlabs_field(
                                "socket_token", socket_token
                            )
                            logger.info(
                                "Refreshed socket token during background token refresh"
                            )

                        # Save changes
                        state_manager.save_changes()

                        # If we were connected, reconnect with new tokens
                        if self.is_connected:
                            logger.info("Reconnecting socket with refreshed tokens")
                            self.disconnect_socket()
                            time.sleep(2)
                            self.connect_socket()

                    else:
                        logger.warning(
                            "Failed to refresh Streamlabs tokens in background"
                        )
                        self.update_connection_status("Token Refresh Failed")
                else:
                    logger.warning("No refresh token available for background refresh")
                    self.update_connection_status("Refresh Token Missing")
            else:
                logger.debug("Streamlabs tokens are still valid")

        except Exception as e:
            logger.error(
                f"Error during background token check: {str(e)}", exc_info=True
            )

    def exchange_code_for_tokens(self, code: str, state: str) -> bool:
        """Exchange authorization code for access tokens and save them persistently"""
        try:
            if not self.oauth_session:
                logger.error("No OAuth session available")
                return False

            if state != self.oauth_state:
                logger.error("OAuth state mismatch")
                return False

            # Build authorization response URL
            authorization_response = f"{self.redirect_uri}?code={code}&state={state}"

            # Fetch tokens
            token = self.oauth_session.fetch_token(
                self.token_url,
                authorization_response=authorization_response,
                client_secret=self.client_secret,
            )

            # Extract tokens
            access_token = token.get("access_token")
            refresh_token = token.get("refresh_token")
            expires_in = token.get("expires_in", 3600)  # Default to 1 hour
            token_expiry = time.time() + expires_in

            if access_token:
                # Save tokens to state manager
                state_manager.update_streamlabs_field("access_token", access_token)
                if refresh_token:
                    state_manager.update_streamlabs_field(
                        "refresh_token", refresh_token
                    )
                state_manager.update_streamlabs_field("token_expiry", token_expiry)
                state_manager.update_streamlabs_field("enabled", True)

                # Get and save socket token for real-time events (always refresh)
                logger.info("Obtaining fresh socket token for OAuth flow")
                socket_token = self.get_socket_token()
                if socket_token:
                    state_manager.update_streamlabs_field("socket_token", socket_token)
                    logger.info(
                        "Successfully obtained and saved fresh socket token during OAuth"
                    )
                else:
                    logger.warning(
                        "Failed to obtain socket token during OAuth - access token is valid but socket events may not work"
                    )

                # Force save all changes to ensure persistence
                try:
                    if state_manager.save_changes():
                        logger.info(
                            "Successfully saved all Streamlabs tokens to database"
                        )
                    else:
                        logger.error("Failed to save Streamlabs tokens to database")
                except Exception as e:
                    logger.error(f"Error saving Streamlabs tokens: {e}")

                logger.info("Successfully obtained and saved Streamlabs access tokens")
                self.update_connection_status("Authenticated")
                return True
            else:
                logger.error("No access token received")
                self.update_connection_status("Authentication Failed")
                return False

        except Exception as e:
            logger.error(f"Error exchanging code for tokens: {str(e)}", exc_info=True)
            self.update_connection_status("Authentication Failed")
            return False

    def trigger_oauth_flow(self) -> bool:
        """Trigger the complete OAuth flow"""
        try:
            logger.info("Starting Streamlabs OAuth flow")

            # Load latest credentials
            self._load_credentials()

            if not self.client_id or not self.client_secret:
                logger.error("Streamlabs credentials not configured")
                self.update_connection_status("Credentials Missing")
                return False

            # Reset OAuth state
            self.oauth_code = None
            self.oauth_state = None

            # Start OAuth server
            self.start_oauth_server()
            time.sleep(1)  # Give server time to start

            # Generate and open OAuth URL
            oauth_url = self.get_oauth_url()
            if not oauth_url:
                logger.error("Failed to generate OAuth URL")
                return False

            # Open URL in browser
            webbrowser.open(oauth_url)
            logger.info("Opened Streamlabs OAuth URL in browser")

            # Wait for OAuth callback
            self.update_connection_status("Waiting for Authorization")

            # Wait up to 120 seconds for user to complete OAuth
            timeout = 120
            start_time = time.time()

            while (time.time() - start_time) < timeout and not self.oauth_code:
                time.sleep(0.5)

            if self.oauth_code:
                # Exchange code for tokens
                success = self.exchange_code_for_tokens(
                    self.oauth_code, self.oauth_state
                )
                if success:
                    # Get socket token
                    socket_token = self.get_socket_token()
                    if socket_token:
                        state_manager.update_streamlabs_field(
                            "socket_token", socket_token
                        )
                        logger.info("Streamlabs OAuth flow completed successfully")
                        return True
                    else:
                        logger.warning(
                            "OAuth successful but failed to get socket token"
                        )
                        return True  # Still consider it successful
                else:
                    logger.error("Failed to exchange code for tokens")
                    return False
            else:
                logger.warning(
                    "OAuth flow timed out - user may not have completed authorization"
                )
                self.update_connection_status("Authorization Timeout")
                return False

        except Exception as e:
            logger.error(f"Error in OAuth flow: {str(e)}", exc_info=True)
            self.update_connection_status("OAuth Error")
            return False
        finally:
            # Clean up
            self.stop_oauth_server()

    def get_socket_token(self) -> Optional[str]:
        """Get socket token for real-time events"""
        try:
            streamlabs_data = state_manager.get_streamlabs_data()
            if not streamlabs_data.access_token:
                logger.error("No access token available for socket token request")
                return None

            url = "https://streamlabs.com/api/v2.0/socket/token"
            headers = {
                "accept": "application/json",
                "Authorization": f"Bearer {streamlabs_data.access_token}",
            }

            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            data = response.json()
            socket_token = data.get("socket_token")

            if socket_token:
                logger.info("Successfully obtained socket token")
                return socket_token
            else:
                logger.error("No socket token in response")
                return None

        except Exception as e:
            logger.error(f"Error getting socket token: {str(e)}", exc_info=True)
            return None

    def refresh_socket_token_and_reconnect(self) -> bool:
        """Refresh socket token and reconnect to socket"""
        try:
            logger.info("Refreshing socket token and reconnecting...")

            # Get new socket token
            socket_token = self.get_socket_token()
            if not socket_token:
                logger.error("Failed to obtain new socket token")
                return False

            # Save the new socket token
            state_manager.update_streamlabs_field("socket_token", socket_token)

            # Disconnect existing connection
            if self.is_connected:
                logger.info("Disconnecting existing socket connection")
                self.disconnect_socket()
                time.sleep(1)  # Give time for clean disconnection

            # Reconnect with new token
            success = self.connect_socket()
            if success:
                logger.info("Successfully reconnected with refreshed socket token")

                # Save changes
                try:
                    state_manager.save_changes()
                    logger.debug("Saved refreshed socket token")
                except Exception as e:
                    logger.warning(f"Failed to save refreshed socket token: {e}")

                return True
            else:
                logger.error("Failed to reconnect with refreshed socket token")
                return False

        except Exception as e:
            logger.error(
                f"Error refreshing socket token and reconnecting: {str(e)}",
                exc_info=True,
            )
            return False

    def connect_socket(self, retry_on_auth_error: bool = True) -> bool:
        """Connect to Streamlabs socket for real-time events using available tokens"""
        try:
            if self.is_connected:
                logger.info("Already connected to Streamlabs socket")
                return True

            # Try to get OAuth socket token first (more reliable)
            socket_token = None
            streamlabs_data = state_manager.get_streamlabs_data()

            if streamlabs_data and streamlabs_data.socket_token:
                socket_token = streamlabs_data.socket_token
                logger.info("Using OAuth socket token for connection")
            else:
                # Fall back to static socket token
                try:
                    socket_token = ensure_decrypted(
                        get_encrypted_streamlabs_socket_token()
                    )
                    logger.info("Using static socket token for connection")
                except:
                    logger.warning("Could not decrypt static socket token")
                    socket_token = None

            if not socket_token:
                logger.error("No socket token available (neither OAuth nor static)")
                self.update_connection_status("No Socket Token")
                return False

            # Disconnect existing connection if any
            if self.socket_client:
                try:
                    self.socket_client.disconnect()
                except:
                    pass
                self.socket_client = None

            # Create socket client with websocket transport (as per Streamlabs docs)
            self.socket_client = socketio.Client(
                logger=False,  # Disable built-in logging to avoid spam
                engineio_logger=False,
            )

            @self.socket_client.event
            def connect():
                logger.info("Connected to Streamlabs socket successfully")
                self.is_connected = True
                # Preserve existing authentication status if available
                current_status = (
                    streamlabs_data.connection_status if streamlabs_data else ""
                )
                if current_status == "Authenticated":
                    self.update_connection_status("Connected (Authenticated)")
                else:
                    self.update_connection_status("Connected")

            @self.socket_client.event
            def connect_error(data):
                logger.error(f"Socket connection error: {data}")
                self.is_connected = False
                self.update_connection_status("Connection Error")

            @self.socket_client.event
            def disconnect():
                logger.info("Disconnected from Streamlabs socket")
                self.is_connected = False
                # Check if we still have valid authentication
                if streamlabs_data and streamlabs_data.access_token:
                    self.update_connection_status("Authenticated (Disconnected)")
                else:
                    self.update_connection_status("Disconnected")

            @self.socket_client.event
            def event(data):
                logger.info(f"Received Streamlabs event: {data}")
                self.handle_socket_event(data)

            # Connect to socket with token and websocket transport
            socket_url = f"https://sockets.streamlabs.com?token={socket_token}"
            logger.info(f"Connecting to Streamlabs socket...")

            try:
                self.socket_client.connect(
                    socket_url,
                    transports=["websocket"],  # Force websocket transport as per docs
                )
            except socketio.exceptions.ConnectionError as conn_e:
                # If this is an authentication error and we haven't retried yet, handle it
                if retry_on_auth_error and "Authentication error" in str(conn_e):
                    logger.warning(
                        "Socket authentication failed - attempting to refresh socket token and reconnect"
                    )

                    # Try to refresh the socket token
                    refreshed_token = self.get_socket_token()
                    if refreshed_token:
                        # Update the stored token
                        state_manager.update_streamlabs_field(
                            "socket_token", refreshed_token
                        )
                        try:
                            state_manager.save_changes()
                            logger.info(
                                "Refreshed socket token due to authentication error"
                            )

                            # Try to reconnect with the new token (prevent infinite recursion)
                            logger.info(
                                "Attempting reconnection with refreshed token..."
                            )
                            return self.connect_socket(retry_on_auth_error=False)

                        except Exception as save_e:
                            logger.error(
                                f"Failed to save refreshed socket token: {save_e}"
                            )
                            self.update_connection_status("Token Save Failed")
                            return False
                    else:
                        logger.error("Failed to refresh socket token")
                        self.update_connection_status("Token Refresh Failed")
                        return False
                else:
                    # Re-raise non-authentication errors or if we've already retried
                    raise

            # Wait a moment for connection to establish
            time.sleep(2)

            if self.socket_client.connected:
                logger.info("Socket connection verified as active")
                return True
            else:
                logger.warning("Socket connection may not be active")
                self.is_connected = False
                self.update_connection_status("Connection Failed")
                return False

        except Exception as e:
            logger.error(
                f"Error connecting to Streamlabs socket: {str(e)}", exc_info=True
            )
            self.is_connected = False
            self.update_connection_status("Connection Failed")
            return False

    def disconnect_socket(self):
        """Disconnect from Streamlabs socket"""
        try:
            if self.socket_client and self.is_connected:
                self.socket_client.disconnect()
                self.socket_client = None
                self.is_connected = False
                logger.info("Disconnected from Streamlabs socket")
        except Exception as e:
            logger.error(
                f"Error disconnecting from Streamlabs socket: {str(e)}", exc_info=True
            )

    def handle_socket_event(self, data: Dict[str, Any]):
        """Handle incoming socket events"""
        try:
            event_type = data.get("type")
            event_for = data.get(
                "for"
            )  # Don't default - some events like donations have no 'for' field

            logger.info(f"Processing event - type: {event_type}, for: {event_for}")

            # Handle donations (no 'for' field according to docs)
            if event_type == "donation" and not event_for:
                self.handle_donation_event(data)
            else:
                logger.info(
                    f"Unhandled Streamlabs event - type: {event_type}, for: {event_for}"
                )
                logger.debug(f"Full event data: {data}")

        except Exception as e:
            logger.error(f"Error handling socket event: {str(e)}", exc_info=True)

    def handle_donation_event(self, data: Dict[str, Any]):
        """Handle donation events and create alerts"""
        try:
            # Extract donation data
            message_data = data.get("message", [])
            if not message_data:
                logger.warning("No message data in donation event")
                return

            # Handle multiple donations in one event
            for donation in message_data:
                try:
                    # Extract donation details
                    username = donation.get("name", "Anonymous")
                    amount = float(donation.get("amount", 0))
                    currency = donation.get("currency", "USD")
                    message = donation.get("message", "")
                    current_timestamp = time.time()

                    # Fetch the proper donation alert configuration based on amount
                    alert = fetch_donation_alert(
                        int(amount)
                    )  # Use existing alert system

                    # If no alert configuration exists, create a default one
                    if alert is None:
                        logger.warning(
                            f"No donation alert configuration found for amount {amount}, creating default"
                        )
                        # Create a default AlertObj if no configuration is found
                        alert = AlertObj()
                        alert.alert_type = "donation"
                        alert.alert_name = f"donation{int(amount)}"
                        alert.duration = 15
                        alert.volume = 100
                        alert.globaldelay = 3  # Default global delay
                        alert.skip_alert = False

                    # Update alert with donation-specific data
                    alert.username = username
                    alert.message = message
                    alert.alert_id = f"Alert{round(current_timestamp)}"
                    alert.timestamp = current_timestamp
                    alert.anonymous = False
                    alert.deleted = False
                    alert.played = False
                    alert.skip_alert = False

                    # Store donation amount for display purposes
                    alert.donation_amount = amount
                    alert.currency = currency

                    # Add to alert queue for processing
                    alert_processor.ALERT_QUEUE.append(alert)

                    # Store completed alert
                    from .alertutils import alert_state_manager

                    alert_state_manager.store_completed_alert(
                        alert.alert_id, alert.__dict__
                    )

                    # Add to activity feed
                    add_alert_to_feed(
                        alert_type="Donation",
                        message=f"{username} donated {currency}{amount:.2f}!",
                        badge_type="donation",
                        timestamp=current_timestamp,
                        user_message=message,
                        alert_id=alert.alert_id,
                    )

                    logger.info(
                        f"Processed Streamlabs donation: {username} - {currency}{amount:.2f}"
                    )

                except Exception as e:
                    logger.error(
                        f"Error processing individual donation: {str(e)}", exc_info=True
                    )

        except Exception as e:
            logger.error(f"Error handling donation event: {str(e)}", exc_info=True)

    def get_connection_status(self) -> Dict[str, Any]:
        """Get current connection status information"""
        try:
            streamlabs_data = state_manager.get_streamlabs_data()
            static_socket_token = ensure_decrypted(
                get_encrypted_streamlabs_socket_token()
            )

            status_info = {
                "status": (
                    streamlabs_data.connection_status
                    if streamlabs_data
                    else "No OAuth Data"
                ),
                "is_authenticated": (
                    bool(streamlabs_data.access_token) if streamlabs_data else False
                ),
                "is_connected": self.is_connected,
                "has_static_socket_token": bool(static_socket_token),
                "has_oauth_socket_token": (
                    bool(streamlabs_data.socket_token) if streamlabs_data else False
                ),
                "using_static_token": True,
                "last_update": time.strftime("%Y-%m-%d %H:%M:%S"),
            }

            return status_info

        except Exception as e:
            logger.error(f"Error getting connection status: {str(e)}", exc_info=True)
            return {
                "status": "Error",
                "is_authenticated": False,
                "is_connected": False,
                "has_static_socket_token": False,
                "has_oauth_socket_token": False,
                "using_static_token": True,
                "last_update": "Error",
            }

    def refresh_token(self) -> bool:
        """Refresh the access token using refresh token and save persistently"""
        try:
            streamlabs_data = state_manager.get_streamlabs_data()
            if not streamlabs_data.refresh_token:
                logger.error("No refresh token available")
                return False

            token_data = {
                "grant_type": "refresh_token",
                "refresh_token": streamlabs_data.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }

            response = requests.post(self.token_url, data=token_data, timeout=10)
            response.raise_for_status()

            data = response.json()
            new_access_token = data.get("access_token")
            new_refresh_token = data.get("refresh_token")
            expires_in = data.get("expires_in", 3600)
            token_expiry = time.time() + expires_in

            if new_access_token:
                # Update tokens in state manager
                state_manager.update_streamlabs_field("access_token", new_access_token)
                if new_refresh_token:
                    state_manager.update_streamlabs_field(
                        "refresh_token", new_refresh_token
                    )
                state_manager.update_streamlabs_field("token_expiry", token_expiry)

                # Always refresh socket token since longevity is undocumented
                socket_token = self.get_socket_token()
                if socket_token:
                    state_manager.update_streamlabs_field("socket_token", socket_token)
                    logger.info("Refreshed socket token during token refresh")
                else:
                    logger.warning("Failed to obtain socket token during refresh")

                # Force save all changes to ensure persistence
                try:
                    if state_manager.save_changes():
                        logger.info(
                            "Successfully saved refreshed Streamlabs tokens to database"
                        )
                    else:
                        logger.error(
                            "Failed to save refreshed Streamlabs tokens to database"
                        )
                except Exception as e:
                    logger.error(f"Error saving refreshed Streamlabs tokens: {e}")

                logger.info("Successfully refreshed and saved Streamlabs tokens")
                return True
            else:
                logger.error("No access token in refresh response")
                return False

        except Exception as e:
            logger.error(f"Error refreshing token: {str(e)}", exc_info=True)
            return False

    def test_socket_connection(self) -> Dict[str, Any]:
        """Test the socket connection and return diagnostic information"""
        try:
            streamlabs_data = state_manager.get_streamlabs_data()
            static_socket_token = ensure_decrypted(
                get_encrypted_streamlabs_socket_token()
            )

            test_results = {
                "has_static_socket_token": bool(static_socket_token),
                "static_socket_token_length": (
                    len(static_socket_token) if static_socket_token else 0
                ),
                "has_oauth_socket_token": (
                    bool(streamlabs_data.socket_token) if streamlabs_data else False
                ),
                "socket_client_exists": self.socket_client is not None,
                "is_connected": self.is_connected,
                "socket_client_connected": (
                    self.socket_client.connected if self.socket_client else False
                ),
                "connection_status": (
                    streamlabs_data.connection_status if streamlabs_data else "No data"
                ),
                "using_static_token": True,  # We're now using static token
            }

            # Check if static token looks valid (JWT format)
            if static_socket_token:
                test_results["static_token_format"] = (
                    "JWT" if static_socket_token.startswith("eyJ") else "Unknown"
                )

            logger.info(f"Socket connection test results: {test_results}")
            return test_results

        except Exception as e:
            logger.error(f"Error testing socket connection: {str(e)}", exc_info=True)
            return {"error": str(e)}

    def test_access_token(self) -> bool:
        """Test if the current access token is valid by making a simple API call"""
        try:
            streamlabs_data = state_manager.get_streamlabs_data()
            if not streamlabs_data.access_token:
                return False

            # Make a simple API call to test token validity
            url = "https://streamlabs.com/api/v2.0/user"
            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {streamlabs_data.access_token}",
            }

            response = requests.get(url, headers=headers, timeout=10)

            if response.status_code == 200:
                logger.debug("Streamlabs access token is valid")
                return True
            else:
                logger.debug(
                    f"Streamlabs access token test failed with status: {response.status_code}"
                )
                return False

        except Exception as e:
            logger.debug(f"Streamlabs access token test failed: {str(e)}")
            return False

    def authenticate(self) -> bool:
        """Authenticate with Streamlabs API using robust 3-step process"""
        try:
            logger.debug("Starting Streamlabs OAuth authentication process...")

            # Load latest credentials and data
            self._load_credentials()
            streamlabs_data = state_manager.get_streamlabs_data()

            if not streamlabs_data:
                logger.debug("No Streamlabs data found - OAuth not configured")
                self.update_connection_status("Not Connected")
                return False

            # Step 1: Test current access token
            logger.info("Step 1: Testing current access token...")
            if (
                streamlabs_data.access_token
                and streamlabs_data.token_expiry
                and time.time() < streamlabs_data.token_expiry - 300
            ):  # 5 minutes buffer
                if self.test_access_token():
                    logger.info("Step 1 successful: Using existing valid access token")

                    # Always refresh socket token since longevity is undocumented
                    socket_token = self.get_socket_token()
                    if socket_token:
                        state_manager.update_streamlabs_field(
                            "socket_token", socket_token
                        )
                        logger.info("Refreshed socket token during authentication")
                    else:
                        logger.warning(
                            "Failed to refresh socket token during authentication"
                        )

                    self.update_connection_status("Authenticated")

                    # Save the current state to ensure persistence
                    try:
                        state_manager.save_changes()
                        logger.debug(
                            "Saved authentication state after successful token validation"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to save authentication state: {e}")

                    return True
                else:
                    logger.info(
                        "Step 1 failed: Access token test failed despite valid expiry"
                    )
            else:
                logger.info("Step 1 skipped: No access token or token expired")

            # Step 2: Try refresh token
            logger.info("Step 2: Attempting to refresh access token...")
            if streamlabs_data.refresh_token:
                if self.refresh_token():
                    # Re-test the refreshed token
                    if self.test_access_token():
                        logger.info(
                            "Step 2 successful: Refreshed and validated access token"
                        )

                        # Reconnect socket with refreshed tokens
                        if self.is_connected:
                            logger.info("Reconnecting socket with refreshed tokens")
                            self.disconnect_socket()
                            # Give a moment for clean disconnection
                            time.sleep(1)

                        self.update_connection_status("Authenticated")

                        # Save the refreshed tokens
                        try:
                            state_manager.save_changes()
                            logger.debug(
                                "Saved refreshed tokens after successful refresh"
                            )
                        except Exception as e:
                            logger.warning(f"Failed to save refreshed tokens: {e}")

                        return True
                    else:
                        logger.warning(
                            "Token refresh succeeded but new token validation failed"
                        )
                else:
                    logger.info("Step 2 failed: Token refresh failed")
            else:
                logger.info("Step 2 skipped: No refresh token available")

            # Step 3: Check if we have any tokens at all and provide appropriate messaging
            if streamlabs_data.access_token or streamlabs_data.refresh_token:
                logger.warning(
                    "Step 3: Have tokens but they're invalid - reconnection required"
                )
                self.update_connection_status("Reconnection Required")
                logger.warning(
                    "STREAMLABS: Stored tokens are invalid. Please reconnect your Streamlabs account."
                )
            else:
                logger.info(
                    "Step 3: No OAuth tokens found - using static token fallback or manual connection required"
                )
                self.update_connection_status("Not Connected")
                logger.debug(
                    "STREAMLABS: No OAuth tokens configured. Will attempt static token or require manual connection."
                )

            return False

        except Exception as e:
            logger.error(
                f"Error in Streamlabs authentication process: {str(e)}", exc_info=True
            )
            self.update_connection_status("Authentication Error")
            return False


# Global instance
streamlabs_integration = StreamlabsIntegration()


def trigger_oauth_connection() -> bool:
    """Trigger OAuth connection - exposed function for UI"""
    return streamlabs_integration.trigger_oauth_flow()


def get_streamlabs_status() -> Dict[str, Any]:
    """Get Streamlabs connection status - exposed function for UI"""
    return streamlabs_integration.get_connection_status()


def connect_to_streamlabs() -> bool:
    """Connect to Streamlabs socket - exposed function"""
    return streamlabs_integration.connect_socket()


def disconnect_from_streamlabs():
    """Disconnect from Streamlabs - exposed function"""
    streamlabs_integration.disconnect_socket()


def stop_streamlabs_token_monitor():
    """Stop the background token monitor - exposed function"""
    streamlabs_integration.stop_token_monitor()


def get_token_monitor_status() -> Dict[str, Any]:
    """Get the status of the background token monitor - exposed function"""
    return {
        "running": streamlabs_integration.token_monitor_running,
        "thread_alive": (
            streamlabs_integration.token_monitor_thread.is_alive()
            if streamlabs_integration.token_monitor_thread
            else False
        ),
        "check_interval": streamlabs_integration.token_check_interval,
        "next_check_in": "Background monitoring active"
        if streamlabs_integration.token_monitor_running
        else "Not running",
    }


def test_streamlabs_socket() -> Dict[str, Any]:
    """Test Streamlabs socket connection - exposed function for debugging"""
    return streamlabs_integration.test_socket_connection()


def refresh_streamlabs_socket() -> bool:
    """Refresh socket token and reconnect - exposed function for UI/manual refresh"""
    return streamlabs_integration.refresh_socket_token_and_reconnect()


def update_streamlabs_settings(client_id: str = None, client_secret: str = None):
    """Update Streamlabs settings and reload credentials"""
    try:
        if client_id is not None:
            # Try to encrypt the client ID before storing, fallback to plain text
            try:
                encrypted_client_id = ensure_encrypted(client_id)
                state_manager.update_streamlabs_field("client_id", encrypted_client_id)
            except:
                # Fallback to plain text storage if encryption fails
                state_manager.update_streamlabs_field("client_id", client_id)
            streamlabs_integration.client_id = client_id

        if client_secret is not None:
            # Try to encrypt the client secret before storing, fallback to plain text
            try:
                encrypted_client_secret = ensure_encrypted(client_secret)
                state_manager.update_streamlabs_field(
                    "client_secret", encrypted_client_secret
                )
            except:
                # Fallback to plain text storage if encryption fails
                state_manager.update_streamlabs_field("client_secret", client_secret)
            streamlabs_integration.client_secret = client_secret

        logger.info("Updated Streamlabs settings")

    except Exception as e:
        logger.error(f"Error updating Streamlabs settings: {str(e)}", exc_info=True)


def should_auto_initialize() -> bool:
    """Check if Streamlabs should auto-initialize based on static socket token"""
    try:
        static_socket_token = ensure_decrypted(get_encrypted_streamlabs_socket_token())

        # Auto-initialize if we have the static socket token
        return bool(static_socket_token)

    except Exception as e:
        logger.error(f"Error checking Streamlabs auto-initialization: {str(e)}")
        return False


def trigger_authentication() -> bool:
    """Trigger Streamlabs authentication process - exposed function for UI"""
    return streamlabs_integration.authenticate()


def start_streamlabs_service():
    """Start the Streamlabs service with robust authentication and reconnection"""
    try:
        logger.info("Starting Streamlabs service...")

        # Step 1: Try to authenticate with stored OAuth tokens
        logger.debug("Step 1: Attempting OAuth token authentication...")
        auth_success = streamlabs_integration.authenticate()

        # Step 2: If authentication is successful, ensure socket connection with refreshed tokens
        if auth_success:
            logger.info(
                "OAuth authentication successful, establishing socket connection with refreshed tokens"
            )

            # Disconnect any existing connection to ensure clean reconnection with new tokens
            if streamlabs_integration.is_connected:
                logger.info(
                    "Disconnecting existing socket connection to refresh with new tokens"
                )
                streamlabs_integration.disconnect_socket()

            success = streamlabs_integration.connect_socket()
            if success:
                logger.info(
                    "Streamlabs service started successfully with OAuth authentication"
                )
                # Start background token monitor
                streamlabs_integration.start_token_monitor()
                return True
            else:
                logger.warning(
                    "OAuth authentication successful but socket connection failed"
                )

        # Step 3: If OAuth authentication failed, try static socket token as fallback
        logger.debug(
            "Step 2: OAuth authentication not available, checking static token fallback..."
        )
        if should_auto_initialize():
            logger.info("Attempting connection with static socket token")
            success = streamlabs_integration.connect_socket()
            if success:
                logger.info("Streamlabs service started successfully with static token")
                # Start background token monitor (will monitor for OAuth tokens if available)
                streamlabs_integration.start_token_monitor()
                return True
            else:
                logger.warning("Failed to connect with static socket token")

        # Step 4: No connection method worked
        logger.info(
            "ℹ️  Streamlabs service not started - no tokens available (manual connection required)"
        )
        streamlabs_integration.update_connection_status("Not Connected")
        return False

    except Exception as e:
        logger.error(f" Error starting Streamlabs service: {str(e)}", exc_info=True)
        streamlabs_integration.update_connection_status("Startup Error")
        return False


def test_streamlabs_system() -> Dict[str, Any]:
    """Test the entire Streamlabs system and return diagnostic information"""
    try:
        logger.info("Testing Streamlabs system...")

        # Get current state
        streamlabs_data = state_manager.get_streamlabs_data()
        status_info = streamlabs_integration.get_connection_status()
        socket_test = streamlabs_integration.test_socket_connection()

        # Test results
        test_results = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "auth_state": {
                "has_access_token": (
                    bool(streamlabs_data.access_token) if streamlabs_data else False
                ),
                "has_refresh_token": (
                    bool(streamlabs_data.refresh_token) if streamlabs_data else False
                ),
                "token_expired": False,
                "access_token_valid": False,
            },
            "connection_state": {
                "is_connected": streamlabs_integration.is_connected,
                "socket_client_exists": streamlabs_integration.socket_client
                is not None,
                "socket_client_connected": (
                    streamlabs_integration.socket_client.connected
                    if streamlabs_integration.socket_client
                    else False
                ),
            },
            "status_info": status_info,
            "socket_test": socket_test,
            "recommendations": [],
        }

        # Check token expiry
        if streamlabs_data and streamlabs_data.token_expiry:
            test_results["auth_state"]["token_expired"] = (
                time.time() > streamlabs_data.token_expiry
            )

        # Test access token validity
        if streamlabs_data and streamlabs_data.access_token:
            test_results["auth_state"]["access_token_valid"] = (
                streamlabs_integration.test_access_token()
            )

        # Add socket token information
        test_results["socket_info"] = {
            "has_oauth_socket_token": (
                bool(streamlabs_data.socket_token) if streamlabs_data else False
            ),
            "has_static_socket_token": socket_test.get(
                "has_static_socket_token", False
            ),
            "using_static_token": socket_test.get("using_static_token", True),
            "can_refresh_socket_token": test_results["auth_state"][
                "access_token_valid"
            ],
        }

        # Generate recommendations
        if not test_results["auth_state"]["has_access_token"]:
            test_results["recommendations"].append(
                "No access token found - run OAuth connection"
            )
        elif test_results["auth_state"]["token_expired"]:
            test_results["recommendations"].append(
                "Access token expired - will attempt refresh on next authentication"
            )
        elif not test_results["auth_state"]["access_token_valid"]:
            test_results["recommendations"].append(
                "Access token invalid - may need to reconnect"
            )

        if not test_results["connection_state"]["is_connected"]:
            test_results["recommendations"].append(
                "Not connected to socket - check network and tokens"
            )

        if (
            test_results["auth_state"]["access_token_valid"]
            and not test_results["socket_info"]["has_oauth_socket_token"]
        ):
            test_results["recommendations"].append(
                "Have valid access token but no OAuth socket token - refresh recommended"
            )

        if (
            test_results["connection_state"]["is_connected"]
            and test_results["socket_info"]["using_static_token"]
        ):
            test_results["recommendations"].append(
                "Connected with static token - OAuth connection recommended for better reliability"
            )

        logger.info(
            f"Streamlabs system test completed: {len(test_results['recommendations'])} recommendations"
        )
        return test_results

    except Exception as e:
        logger.error(f"Error testing Streamlabs system: {str(e)}", exc_info=True)
        return {"error": str(e), "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}
