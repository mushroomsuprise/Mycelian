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
import http.server
import logging
import socketserver
import threading
import time
import webbrowser
from dataclasses import asdict
from typing import Any, Dict, Optional, Union
from urllib.parse import parse_qs, urlparse

import requests

from .dataobjects import SpotifyData, state_manager

logger = logging.getLogger(__name__)

# Non-HTTPS Spotify redirects must use loopback literal; must match dashboard URI exactly.
SPOTIFY_OAUTH_REDIRECT_URI = "http://127.0.0.1:9973"

# Global variables
spotify_client: Optional["SpotifyClient"] = None
spotify_thread: Optional[threading.Thread] = None
is_running = False
_start_lock = threading.Lock()

_AUTH_FAILURE_COOLDOWN_SEC = 300.0
_API_BACKOFF_DEFAULT_SEC = 30.0


class CustomTCPServer(socketserver.TCPServer):
    """Custom TCP server with additional attributes for OAuth handling"""

    def __init__(self, server_address, RequestHandlerClass):
        super().__init__(server_address, RequestHandlerClass)
        self.auth_code: Optional[str] = None
        self.auth_error: Optional[str] = None


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for OAuth callbacks"""

    def do_GET(self):
        """Handle GET request for OAuth callback"""
        try:
            # Parse the URL to get the authorization code
            parsed_url = urlparse(self.path)
            query_params = parse_qs(parsed_url.query)

            if "code" in query_params:
                auth_code = query_params["code"][0]

                # Store the auth code for the main thread to process
                if isinstance(self.server, CustomTCPServer):
                    self.server.auth_code = auth_code

                # Send success response
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()

                success_html = """
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Spotify Authorization Complete</title>
                    <style>
                        body { font-family: Arial, sans-serif; text-align: center; margin-top: 50px; background-color: #1DB954; color: white; }
                        .container { max-width: 400px; margin: 0 auto; padding: 20px; }
                        h1 { margin-bottom: 20px; }
                        p { font-size: 16px; margin-bottom: 20px; }
                        .success { background-color: rgba(255,255,255,0.1); padding: 15px; border-radius: 5px; }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>🎵 Authorization Successful!</h1>
                        <div class="success">
                            <p>Spotify has been successfully connected to Mycelian.</p>
                            <p>You can close this window and return to the application.</p>
                        </div>
                    </div>
                    <script>
                        // Auto-close window after 3 seconds
                        setTimeout(function() {
                            window.close();
                        }, 3000);
                    </script>
                </body>
                </html>
                """
                self.wfile.write(success_html.encode())

            elif "error" in query_params:
                error = query_params["error"][0]
                if isinstance(self.server, CustomTCPServer):
                    self.server.auth_error = error

                # Send error response
                self.send_response(400)
                self.send_header("Content-type", "text/html")
                self.end_headers()

                error_html = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Spotify Authorization Error</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; text-align: center; margin-top: 50px; background-color: #e22134; color: white; }}
                        .container {{ max-width: 400px; margin: 0 auto; padding: 20px; }}
                        h1 {{ margin-bottom: 20px; }}
                        p {{ font-size: 16px; margin-bottom: 20px; }}
                        .error {{ background-color: rgba(255,255,255,0.1); padding: 15px; border-radius: 5px; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>❌ Authorization Failed</h1>
                        <div class="error">
                            <p>Error: {error}</p>
                            <p>Please try again from the Mycelian application.</p>
                        </div>
                    </div>
                    <script>
                        // Auto-close window after 5 seconds
                        setTimeout(function() {{
                            window.close();
                        }}, 5000);
                    </script>
                </body>
                </html>
                """
                self.wfile.write(error_html.encode())

        except Exception as e:
            logger.error(f"Error handling OAuth callback: {str(e)}", exc_info=True)
            self.send_response(500)
            self.end_headers()

    def log_message(self, format, *args):
        """Override to suppress default HTTP server logging"""
        pass


class OAuthCallbackServer:
    """Temporary HTTP server for handling OAuth callbacks"""

    def __init__(self, port=9973):
        self.port = port
        self.server: Optional[CustomTCPServer] = None
        self.server_thread = None
        self.auth_code = None
        self.auth_error = None

    def start(self):
        """Start the callback server"""
        try:
            # Create server
            self.server = CustomTCPServer(
                ("127.0.0.1", self.port), OAuthCallbackHandler
            )

            # Start in background thread
            self.server_thread = threading.Thread(
                target=self.server.serve_forever, daemon=True
            )
            self.server_thread.start()

            logger.debug(f"OAuth callback server started on port {self.port}")
            return True
        except Exception as e:
            logger.error(
                f"Error starting OAuth callback server: {str(e)}", exc_info=True
            )
            return False

    def stop(self):
        """Stop the callback server"""
        try:
            if self.server:
                self.server.shutdown()
                self.server.server_close()

            if self.server_thread and self.server_thread.is_alive():
                self.server_thread.join(timeout=2)

            logger.debug("OAuth callback server stopped")
        except Exception as e:
            logger.error(
                f"Error stopping OAuth callback server: {str(e)}", exc_info=True
            )

    def wait_for_callback(self, timeout=300):  # 5 minute timeout
        """Wait for OAuth callback and return result"""
        start_time = time.time()

        while time.time() - start_time < timeout:
            if self.server and self.server.auth_code:
                return {"success": True, "code": self.server.auth_code}
            elif self.server and self.server.auth_error:
                return {"success": False, "error": self.server.auth_error}

            time.sleep(0.5)

        return {"success": False, "error": "timeout"}


class SpotifyClient:
    """Simplified Spotify client using direct API calls"""

    def __init__(self):
        self.spotify_data: SpotifyData = SpotifyData()
        self.is_authenticated = False
        self.last_track_id = None
        self.update_interval = 5.0
        self.update_interval_playing = 5.0
        self.update_interval_idle = 5.0
        self.running = False
        self._last_api_success = False
        self._refresh_lock = threading.Lock()
        self._api_backoff_until = 0.0
        self._auth_failure_until = 0.0
        self._last_auth_error_code = ""
        self._auth_recovery_logged = False
        self._last_playback_state = "unknown"

        # Load existing data from state manager
        self.load_spotify_data()

    def load_spotify_data(self):
        """Load Spotify data from state manager"""
        try:
            state_manager.initialize()
            self.spotify_data = state_manager.get_spotify_data()

            # Only log detailed info if we have credentials or tokens
            has_credentials = bool(
                self.spotify_data.client_id and self.spotify_data.client_secret
            )
            has_tokens = bool(
                self.spotify_data.access_token or self.spotify_data.refresh_token
            )

            if has_credentials or has_tokens:
                logger.info(
                    f"Spotify data loaded - credentials: {'SET' if has_credentials else 'EMPTY'}, tokens: {'SET' if has_tokens else 'EMPTY'}"
                )
                logger.debug(
                    f"  - client_id: {'SET' if self.spotify_data.client_id else 'EMPTY'}"
                )
                logger.debug(
                    f"  - client_secret: {'SET' if self.spotify_data.client_secret else 'EMPTY'}"
                )
                logger.debug(
                    f"  - access_token: {'SET' if self.spotify_data.access_token else 'EMPTY'}"
                )
                logger.debug(
                    f"  - refresh_token: {'SET' if self.spotify_data.refresh_token else 'EMPTY'}"
                )
                logger.debug(f"  - token_expiry: {self.spotify_data.token_expiry}")
                logger.debug(
                    f"  - connection_status: {self.spotify_data.connection_status}"
                )
            else:
                logger.debug("Spotify data loaded - no credentials or tokens found")

            self._sync_client_credentials(persist=True)

        except Exception as e:
            logger.error(f"Error loading Spotify data: {str(e)}", exc_info=True)
            self.spotify_data = SpotifyData()

    def reload_and_sync(self, persist: bool = True) -> None:
        """Reload Spotify data from state manager and merge api_credentials."""
        self.load_spotify_data()
        if persist:
            self._sync_client_credentials(persist=True)

    def _sync_client_credentials(self, persist: bool = False) -> bool:
        """Merge client id/secret from api_credentials_manager into spotify_data."""
        try:
            from .api_credentials_manager import api_credentials_manager

            creds = api_credentials_manager.get_spotify_credentials()
            cid = (creds.get("client_id") or "").strip()
            secret = (creds.get("client_secret") or "").strip()
            if not cid and not secret:
                return bool(
                    self.spotify_data.client_id and self.spotify_data.client_secret
                )

            changed = False
            if cid and cid != self.spotify_data.client_id:
                self.update_field("client_id", cid)
                changed = True
            if secret and secret != self.spotify_data.client_secret:
                self.update_field("client_secret", secret)
                changed = True

            if persist and changed:
                state_manager.save_changes()
                logger.info(
                    "Synced Spotify client credentials from api_credentials into SpotifyData"
                )

            return bool(
                self.spotify_data.client_id and self.spotify_data.client_secret
            )
        except Exception as e:
            logger.error(
                f"Error syncing Spotify client credentials: {str(e)}", exc_info=True
            )
            return bool(
                self.spotify_data.client_id and self.spotify_data.client_secret
            )

    def _has_auth_tokens(self) -> bool:
        return bool(
            (self.spotify_data.refresh_token or "").strip()
            or (self.spotify_data.access_token or "").strip()
        )

    def is_in_auth_cooldown(self) -> bool:
        return time.monotonic() < self._auth_failure_until

    def is_in_api_backoff(self) -> bool:
        return time.monotonic() < self._api_backoff_until

    def should_skip_playback_poll(self) -> bool:
        if self.is_in_auth_cooldown() or self.is_in_api_backoff():
            return True
        if not self.is_authenticated:
            status = (self.spotify_data.connection_status or "").strip()
            if status in (
                "Token Refresh Failed",
                "Authorization Required",
                "Not Configured",
                "Token Refresh Error",
            ):
                return True
        return False

    def _access_token_needs_refresh(self) -> bool:
        if not (self.spotify_data.access_token or "").strip():
            return True
        exp = self._token_expiry_epoch(self.spotify_data.token_expiry)
        if exp is None:
            return True
        return time.time() >= exp - 30

    def _clear_auth_failure_state(self) -> None:
        self._auth_failure_until = 0.0
        self._last_auth_error_code = ""
        self._auth_recovery_logged = False

    def _register_auth_failure(self, error_code: str) -> None:
        self._last_auth_error_code = error_code
        self._auth_failure_until = time.monotonic() + _AUTH_FAILURE_COOLDOWN_SEC
        self.is_authenticated = False
        self.update_field("connection_status", "Authorization Required")
        if not self._auth_recovery_logged:
            self._auth_recovery_logged = True
            logger.warning(
                "Spotify auth failed (%s). Verify Client ID/Secret in Settings match "
                "your app at https://developer.spotify.com/dashboard, ensure redirect "
                "URI includes %s, then reconnect via OAuth.",
                error_code,
                SPOTIFY_OAUTH_REDIRECT_URI,
            )

    def _apply_rate_limit_backoff(
        self, response: requests.Response, default_sec: float = _API_BACKOFF_DEFAULT_SEC
    ) -> None:
        wait = default_sec
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                wait = float(retry_after)
            except ValueError:
                pass
        wait = max(1.0, min(wait, 3600.0))
        self._api_backoff_until = time.monotonic() + wait
        logger.warning(
            "Spotify API rate limited (429); backing off for %.0f seconds", wait
        )

    def _parse_oauth_error(self, response: requests.Response) -> str:
        try:
            return str(response.json().get("error", "") or "")
        except (ValueError, AttributeError):
            return ""

    def _api_request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[Dict[str, str]] = None,
        allow_during_auth_cooldown: bool = False,
    ) -> Optional[requests.Response]:
        now = time.monotonic()
        if now < self._api_backoff_until:
            logger.debug("Skipping Spotify API request — rate-limit backoff active")
            return None
        if not allow_during_auth_cooldown and now < self._auth_failure_until:
            logger.debug("Skipping Spotify API request — auth failure cooldown active")
            return None
        try:
            response = requests.request(
                method.upper(), url, headers=headers, data=data, timeout=10
            )
        except Exception as e:
            logger.error("Spotify API request failed: %s", e, exc_info=True)
            return None
        if response.status_code == 429:
            self._apply_rate_limit_backoff(response)
        return response

    def _get_monitor_sleep_seconds(self) -> float:
        now = time.monotonic()
        base = (
            self.update_interval_playing
            if self._last_playback_state == "playing"
            else self.update_interval_idle
        )
        backoff_rem = max(0.0, self._api_backoff_until - now)
        auth_rem = max(0.0, self._auth_failure_until - now)
        return max(base, backoff_rem, auth_rem)

    def save_spotify_data(self):
        """Save current Spotify data to state manager"""
        try:
            spotify_dict = asdict(self.spotify_data)
            state_manager.set_spotify_data(spotify_dict)
            logger.debug("Saved Spotify data to state manager")
        except Exception as e:
            logger.error(f"Error saving Spotify data: {str(e)}", exc_info=True)

    def update_field(self, field: str, value: Any):
        """Update a single field in Spotify data"""
        try:
            if hasattr(self.spotify_data, field):
                setattr(self.spotify_data, field, value)
                state_manager.update_spotify_field(field, value)
            else:
                logger.error(f"Invalid Spotify field: {field}")
        except Exception as e:
            logger.error(
                f"Error updating Spotify field {field}: {str(e)}", exc_info=True
            )

    @staticmethod
    def _token_expiry_epoch(token_expiry: Any) -> Optional[float]:
        """Parse stored token_expiry (epoch seconds) from DB / state; None if missing or invalid."""
        if token_expiry is None or token_expiry == "":
            return None
        try:
            return float(token_expiry)
        except (TypeError, ValueError):
            return None

    def refresh_token(self, force: bool = False) -> bool:
        """Refresh the Spotify access token using direct API call.

        When force is False, skips the token endpoint only if we have a non-empty
        access_token and token_expiry indicates it is still valid (avoids redundant
        refresh). When force is True (e.g. after HTTP 401), always performs the
        refresh grant if a refresh_token is present.
        """
        if not self._refresh_lock.acquire(blocking=False):
            logger.debug("Spotify token refresh already in progress, waiting...")
            with self._refresh_lock:
                if not force and self.spotify_data.access_token:
                    exp = self._token_expiry_epoch(self.spotify_data.token_expiry)
                    if exp is not None and time.time() < exp - 30:
                        return True
                return bool((self.spotify_data.access_token or "").strip())

        try:
            return self._refresh_token_locked(force)
        finally:
            self._refresh_lock.release()

    def _refresh_token_locked(self, force: bool = False) -> bool:
        try:
            if not force and self.is_in_auth_cooldown():
                return bool((self.spotify_data.access_token or "").strip())

            self._sync_client_credentials()

            if not self.spotify_data.refresh_token:
                logger.warning("No refresh token available")
                return False

            if not self.spotify_data.client_id or not self.spotify_data.client_secret:
                logger.warning(
                    "Spotify client ID or secret missing — cannot refresh token"
                )
                return False

            if not force and self.spotify_data.access_token:
                exp = self._token_expiry_epoch(self.spotify_data.token_expiry)
                if exp is not None and time.time() < exp - 30:
                    return True

            if self.is_in_api_backoff():
                logger.debug("Skipping token refresh — rate-limit backoff active")
                return bool((self.spotify_data.access_token or "").strip())

            logger.info("Refreshing Spotify access token...")

            url = "https://accounts.spotify.com/api/token"
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
            }
            data = {
                "grant_type": "refresh_token",
                "refresh_token": self.spotify_data.refresh_token,
                "client_id": self.spotify_data.client_id,
                "client_secret": self.spotify_data.client_secret,
            }

            response = self._api_request(
                "POST", url, headers=headers, data=data, allow_during_auth_cooldown=True
            )
            if response is None:
                return bool((self.spotify_data.access_token or "").strip())

            if response.status_code == 200:
                token_data = response.json()

                self.update_field("access_token", token_data["access_token"])
                if "refresh_token" in token_data:
                    self.update_field("refresh_token", token_data["refresh_token"])

                expires_in = token_data.get("expires_in", 3600)
                self.update_field("token_expiry", time.time() + expires_in - 30)

                self.update_field("connection_status", "Connected")
                self.is_authenticated = True
                self._clear_auth_failure_state()

                state_manager.save_changes()

                logger.info("Successfully refreshed Spotify access token")
                return True

            error_code = self._parse_oauth_error(response)
            if response.status_code == 400 and error_code in (
                "invalid_client",
                "invalid_grant",
            ):
                self._register_auth_failure(error_code)
            else:
                logger.error(
                    "Failed to refresh token. Status: %s, Response: %s",
                    response.status_code,
                    response.text,
                )
                self.is_authenticated = False
                self.update_field("connection_status", "Token Refresh Failed")
            return False

        except Exception as e:
            logger.error(f"Error refreshing Spotify token: {str(e)}", exc_info=True)
            self.is_authenticated = False
            self.update_field("connection_status", "Token Refresh Error")
            return False

    def get_current_playback(
        self, _allow_auth_retry: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Get current playback state using direct API call"""
        try:
            self._last_api_success = False

            if self.is_in_api_backoff() or self.is_in_auth_cooldown():
                return None

            if not self.spotify_data.access_token:
                if not (
                    self.spotify_data.refresh_token and self.refresh_token(force=True)
                ):
                    return None

            if not self.refresh_token():
                return None

            url = "https://api.spotify.com/v1/me/player"
            headers = {"Authorization": "Bearer " + self.spotify_data.access_token}

            logger.debug("Making Spotify API call to /me/player")
            response = self._api_request("GET", url, headers=headers)
            if response is None:
                return None

            if response.status_code == 200:
                logger.debug("Successfully retrieved playback data")
                self._last_api_success = True
                return response.json()
            if response.status_code == 204:
                logger.debug("No active playback (status 204)")
                self._last_api_success = True
                return None
            if response.status_code == 429:
                return None
            if response.status_code in (401, 403):
                logger.warning(
                    "Access token rejected (%s), forcing refresh",
                    response.status_code,
                )
                if _allow_auth_retry and self.refresh_token(force=True):
                    return self.get_current_playback(_allow_auth_retry=False)
                self.is_authenticated = False
                self.update_field("connection_status", "Authorization Required")
                return None
            if response.status_code == 502:
                logger.warning("Bad gateway (502), attempting token refresh")
                if _allow_auth_retry and self.refresh_token():
                    return self.get_current_playback(_allow_auth_retry=False)
                return None

            logger.warning(
                "Unexpected API response: %s - %s",
                response.status_code,
                response.text,
            )
            return None

        except Exception as e:
            logger.error(f"Error getting current playback: {str(e)}", exc_info=True)
            return None

    def authenticate(self) -> bool:
        """Test current authentication status and attempt token refresh if needed"""
        try:
            if self.is_in_auth_cooldown():
                logger.debug("Skipping Spotify authenticate — auth failure cooldown active")
                return False

            self._sync_client_credentials()

            if not self.spotify_data.client_id or not self.spotify_data.client_secret:
                logger.warning("Spotify client ID or secret not configured")
                self.update_field("connection_status", "Not Configured")
                self.is_authenticated = False
                return False

            if self.spotify_data.refresh_token and self._access_token_needs_refresh():
                logger.info(
                    "Spotify authenticate: refreshing access token (expired or missing)"
                )
                if not self.refresh_token(force=False):
                    if self.is_in_auth_cooldown():
                        return False
                    logger.warning("Token refresh on authenticate failed")
                    self.update_field("connection_status", "Authorization Required")
                    self.is_authenticated = False
                    return False

            if not self.spotify_data.access_token:
                logger.info("No access token available")
                self.update_field("connection_status", "Authorization Required")
                self.is_authenticated = False
                return False

            if self.is_in_auth_cooldown():
                return False

            self.is_authenticated = False
            self.get_current_playback()

            if self._last_api_success:
                self.is_authenticated = True
                self.update_field("connection_status", "Connected")
                logger.info("Spotify authentication successful")
                return True

            self.is_authenticated = False
            if not self.is_in_auth_cooldown():
                self.update_field("connection_status", "Authorization Required")
                logger.warning(
                    "Spotify authentication failed - may need re-authorization"
                )
            return False

        except Exception as e:
            logger.error(f"Error testing authentication: {str(e)}", exc_info=True)
            self.is_authenticated = False
            self.update_field("connection_status", "Authentication Error")
            return False

    def millis_to_timestamp(self, millis: int) -> str:
        """Convert milliseconds to MM:SS format - same as old script"""
        if not millis:
            return "0:00"

        millis = int(millis)
        seconds = (millis // 1000) % 60
        minutes = (millis // (1000 * 60)) % 60

        if minutes == 0:
            return f"0:{seconds:02d}"
        else:
            return f"{minutes}:{seconds:02d}"

    def set_empty_track_data(self):
        """Set empty track data when nothing is playing - same as old script"""
        self.update_field("track_name", "Nothing playing")
        self.update_field("artist_name", "Nothing playing")
        self.update_field("album_name", "")
        self.update_field("current_tracktime", "0:00")
        self.update_field("album_image_url", "")
        self.update_field("track_length", "0:00")
        self.update_field("current_tracktime_seconds", 0.0)
        self.update_field("track_length_seconds", 0.0)
        self.update_field("is_playing", False)
        self.update_field("progress_percentage", 0.0)

    def update_playback_data(self):
        """Update playback data - similar to old script's set_playback_data"""
        try:
            playback_data = self.get_current_playback()

            if playback_data is None:
                if self._last_api_success:
                    self._last_playback_state = "idle"
                    self.set_empty_track_data()
                return

            try:
                # Extract track data - same logic as old script
                track_name = playback_data["item"]["name"]
                artist_name = playback_data["item"]["artists"][0]["name"]
                current_tracktime = self.millis_to_timestamp(
                    playback_data["progress_ms"]
                )
                track_length = self.millis_to_timestamp(
                    playback_data["item"]["duration_ms"]
                )
                is_playing = playback_data["is_playing"]
                self._last_playback_state = "playing" if is_playing else "idle"
                current_tracktime_seconds = playback_data["progress_ms"] / 1000
                track_length_seconds = playback_data["item"]["duration_ms"] / 1000

                # Try to get album image
                try:
                    album_image_url = playback_data["item"]["album"]["images"][0]["url"]
                except (IndexError, KeyError):
                    album_image_url = ""

                # Calculate progress percentage
                progress_percentage = (
                    (current_tracktime_seconds / track_length_seconds * 100)
                    if track_length_seconds > 0
                    else 0
                )

                # Update all fields
                self.update_field("track_name", track_name)
                self.update_field("artist_name", artist_name)
                self.update_field(
                    "album_name", playback_data["item"].get("album", {}).get("name", "")
                )
                self.update_field("current_tracktime", current_tracktime)
                self.update_field("album_image_url", album_image_url)
                self.update_field("track_length", track_length)
                self.update_field("is_playing", is_playing)
                self.update_field(
                    "current_tracktime_seconds", current_tracktime_seconds
                )
                self.update_field("track_length_seconds", track_length_seconds)
                self.update_field("progress_percentage", progress_percentage)

                # Check if track changed
                current_track_id = playback_data["item"].get("id")
                if current_track_id != self.last_track_id:
                    self.last_track_id = current_track_id
                    logger.info(f"Now playing: {artist_name} - {track_name}")

                # Log timing if API call was slow
                # if api_time > 0.5:
                #     logger.warning(f"Slow Spotify API response: {api_time:.3f}s")

            except (KeyError, IndexError) as e:
                logger.warning(f"Error parsing playback data: {str(e)}")
                self.set_empty_track_data()

        except Exception as e:
            logger.error(f"Error updating playback data: {str(e)}", exc_info=True)
            self.set_empty_track_data()

    def start_monitoring(self):
        """Start monitoring Spotify playback"""
        self.running = True
        logger.info("Started Spotify playback monitoring")

        while self.running:
            start_time = time.time()
            try:
                if self._has_auth_tokens():
                    if not self.should_skip_playback_poll():
                        self.update_playback_data()
                        if self._last_api_success:
                            if not self.is_authenticated:
                                self.is_authenticated = True
                                self.update_field("connection_status", "Connected")
                    self.send_websocket_data()
                else:
                    self.set_empty_track_data()

                elapsed = time.time() - start_time
                sleep_time = max(0, self._get_monitor_sleep_seconds() - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    logger.debug(
                        f"Spotify update took longer than interval: {elapsed:.3f}s"
                    )
            except Exception as e:
                logger.error(f"Error in monitoring loop: {str(e)}", exc_info=True)
                time.sleep(5)  # Wait longer on error

    def send_websocket_data(self):
        """Send current Spotify data via websocket"""
        try:
            from . import web_engine

            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                spotify_dict = asdict(self.spotify_data)
                web_engine.web_engine_instance.socketio.emit(
                    "spotify_data_update", spotify_dict
                )
        except Exception as e:
            logger.debug(f"Error sending websocket data: {str(e)}")

    def stop_monitoring(self):
        """Stop monitoring"""
        self.running = False
        logger.info("Stopped Spotify monitoring")

    def disconnect(self):
        """Disconnect from Spotify"""
        self.stop_monitoring()
        self.is_authenticated = False
        self.update_field("connection_status", "Disconnected")
        logger.info("Disconnected from Spotify")

    # OAuth methods remain the same but simplified
    def start_oauth_flow(self) -> str:
        """Start OAuth flow and return authorization URL"""
        try:
            if not self.spotify_data.client_id or not self.spotify_data.client_secret:
                return ""

            # Simple OAuth URL construction
            auth_url = (
                f"https://accounts.spotify.com/authorize?"
                f"client_id={self.spotify_data.client_id}&"
                f"response_type=code&"
                f"redirect_uri={SPOTIFY_OAUTH_REDIRECT_URI}&"
                f"scope=user-read-playback-state user-read-currently-playing&"
                f"show_dialog=true"
            )

            self.update_field("connection_status", "Awaiting Authorization")
            logger.info("Generated Spotify OAuth URL")
            return auth_url
        except Exception as e:
            logger.error(f"Error starting OAuth flow: {str(e)}", exc_info=True)
            return ""

    def start_oauth_flow_with_server(self) -> bool:
        """Start OAuth flow with automatic callback handling"""
        try:
            if not self.spotify_data.client_id or not self.spotify_data.client_secret:
                return False

            # Start callback server
            oauth_server = OAuthCallbackServer()
            if not oauth_server.start():
                logger.error("Failed to start OAuth callback server")
                self.update_field("connection_status", "Server Error")
                return False

            # Get authorization URL and open in browser
            auth_url = self.start_oauth_flow()
            if not auth_url:
                oauth_server.stop()
                return False

            self.update_field("connection_status", "Opening Browser...")

            # Open browser automatically
            webbrowser.open(auth_url)
            logger.info("Opened Spotify authorization URL in browser")

            # Wait for callback
            result = oauth_server.wait_for_callback()
            oauth_server.stop()

            if result["success"]:
                # Complete OAuth flow
                if self.complete_oauth_flow(result["code"]):
                    logger.info("Successfully completed automatic OAuth flow")
                    return True
                else:
                    logger.error("Failed to complete OAuth flow")
                    return False
            else:
                error = result.get("error", "Unknown error")
                logger.error(f"OAuth callback failed: {error}")
                if error == "timeout":
                    self.update_field("connection_status", "Authorization Timeout")
                else:
                    self.update_field(
                        "connection_status", f"Authorization Error: {error}"
                    )
                return False

        except Exception as e:
            logger.error(
                f"Error starting OAuth flow with server: {str(e)}", exc_info=True
            )
            self.update_field("connection_status", "OAuth Error")
            return False

    def complete_oauth_flow(self, authorization_code: str) -> bool:
        """Complete OAuth flow with authorization code"""
        try:
            url = "https://accounts.spotify.com/api/token"
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            data = {
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": SPOTIFY_OAUTH_REDIRECT_URI,
                "client_id": self.spotify_data.client_id,
                "client_secret": self.spotify_data.client_secret,
            }

            response = requests.post(url, headers=headers, data=data, timeout=10)

            if response.status_code == 200:
                token_data = response.json()

                self.update_field("access_token", token_data["access_token"])
                self.update_field("refresh_token", token_data["refresh_token"])

                expires_in = token_data.get("expires_in", 3600)
                self.update_field("token_expiry", time.time() + expires_in - 30)

                self.is_authenticated = True
                self.update_field("connection_status", "Connected")
                self._clear_auth_failure_state()

                # Save to database
                state_manager.save_changes()

                logger.info("Successfully completed Spotify OAuth flow")
                return True
            else:
                error_code = self._parse_oauth_error(response)
                if response.status_code == 400 and error_code in (
                    "invalid_client",
                    "invalid_grant",
                ):
                    self._register_auth_failure(error_code)
                logger.error(
                    f"OAuth completion failed: {response.status_code} - {response.text}"
                )
                self.update_field("connection_status", "OAuth Failed")
                return False

        except Exception as e:
            logger.error(f"Error completing OAuth flow: {str(e)}", exc_info=True)
            self.update_field("connection_status", "OAuth Error")
            return False


# Module-level functions
def initialize_spotify():
    """Initialize the Spotify client"""
    global spotify_client
    try:
        if not spotify_client:
            spotify_client = SpotifyClient()
            logger.info("Initialized Spotify client")
        return spotify_client
    except Exception as e:
        logger.error(f"Error initializing Spotify client: {str(e)}", exc_info=True)
        return None


def should_auto_initialize() -> bool:
    """Check if Spotify should auto-initialize based on available credentials/tokens"""
    try:
        if not spotify_client:
            logger.debug("should_auto_initialize: No spotify_client available")
            return False

        data = spotify_client.spotify_data

        # Auto-initialize if we have client credentials
        has_credentials = bool(data.client_id and data.client_secret)

        # Or if we have tokens (even without current credentials, we can try to refresh)
        has_tokens = bool(data.refresh_token or data.access_token)

        logger.debug(
            f"Spotify auto-initialization check - has_credentials: {has_credentials}, has_tokens: {has_tokens}"
        )
        logger.debug(
            f"Spotify data - client_id: {bool(data.client_id)}, client_secret: {bool(data.client_secret)}"
        )
        logger.debug(
            f"Spotify data - refresh_token: {bool(data.refresh_token)}, access_token: {bool(data.access_token)}"
        )

        result = has_credentials or has_tokens
        if result:
            logger.info(
                "Spotify credentials/tokens found - enabling auto-initialization"
            )
        else:
            logger.debug(
                "No Spotify credentials or tokens found - skipping auto-initialization"
            )
        return result
    except Exception as e:
        logger.error(f"Error checking Spotify auto-initialization: {str(e)}")
        return False


def _authenticate_with_retry(
    client: SpotifyClient, delays: tuple = (0.0, 1.0, 3.0)
) -> bool:
    """Run authenticate() with backoff for startup reliability."""
    if client.is_in_auth_cooldown():
        logger.info("Skipping Spotify startup auth — auth failure cooldown active")
        return False

    for attempt, delay in enumerate(delays, start=1):
        if client.is_in_auth_cooldown():
            break
        if delay > 0:
            time.sleep(delay)
        client.reload_and_sync(persist=True)
        if client.authenticate():
            logger.info("Spotify startup authentication succeeded on attempt %s", attempt)
            return True
        logger.info(
            "Spotify startup authentication failed on attempt %s (status=%s)",
            attempt,
            client.spotify_data.connection_status,
        )
        if client.is_in_auth_cooldown():
            break
    return False


_spotify_start_thread: Optional[threading.Thread] = None


def _start_spotify_service_impl() -> None:
    """Blocking Spotify client setup (runs off the deferred init thread)."""
    global spotify_thread, is_running, spotify_client, _spotify_start_thread

    try:
        client = initialize_spotify()
        if not client:
            return

        with _start_lock:
            is_running = True

        if should_auto_initialize():
            logger.info(
                "Auto-initializing Spotify service with existing credentials/tokens"
            )
            _authenticate_with_retry(client)

        monitor = threading.Thread(
            target=client.start_monitoring, daemon=True, name="SpotifyMonitor"
        )
        with _start_lock:
            spotify_thread = monitor
        monitor.start()

        logger.info("Started Spotify service")
    except Exception as e:
        with _start_lock:
            is_running = False
        logger.error(f"Error starting Spotify service: {str(e)}", exc_info=True)


def start_spotify_service():
    """Start the Spotify service without blocking the caller."""
    global _spotify_start_thread, is_running, spotify_thread

    with _start_lock:
        if is_running and spotify_thread and spotify_thread.is_alive():
            logger.warning("Spotify service already running")
            return False
        if _spotify_start_thread is not None and _spotify_start_thread.is_alive():
            logger.debug("Spotify service start already in progress")
            return True
        if is_running:
            is_running = False

        _spotify_start_thread = threading.Thread(
            target=_start_spotify_service_impl,
            daemon=True,
            name="SpotifyServiceStart",
        )
        _spotify_start_thread.start()
        return True


def stop_spotify_service(*, join_timeout: float = 5.0) -> None:
    """Stop the Spotify service"""
    global spotify_thread, is_running, spotify_client

    if not is_running:
        logger.warning("Spotify service not running")
        return

    try:
        if spotify_client:
            spotify_client.stop_monitoring()

        is_running = False

        if spotify_thread and spotify_thread.is_alive():
            spotify_thread.join(timeout=join_timeout)

        logger.info("Stopped Spotify service")
    except Exception as e:
        logger.error(f"Error stopping Spotify service: {str(e)}", exc_info=True)


def get_spotify_client() -> Optional[SpotifyClient]:
    """Get the current Spotify client instance"""
    return spotify_client


def get_spotify_status() -> Dict[str, Any]:
    """Get current Spotify connection status"""
    from .connection_status_tracker import apply_connectivity_overlay_to_info

    if not spotify_client:
        return apply_connectivity_overlay_to_info(
            "spotify",
            {
                "status": "Not Initialized",
                "is_authenticated": False,
                "current_track": "N/A",
            },
            valid_field="is_authenticated",
        )

    return apply_connectivity_overlay_to_info(
        "spotify",
        {
            "status": spotify_client.spotify_data.connection_status,
            "is_authenticated": spotify_client.is_authenticated,
            "current_track": f"{spotify_client.spotify_data.artist_name} - {spotify_client.spotify_data.track_name}",
        },
        valid_field="is_authenticated",
    )


def trigger_oauth_flow() -> str:
    """Trigger OAuth flow and return authorization URL"""
    if not spotify_client:
        initialize_spotify()

    if spotify_client:
        return spotify_client.start_oauth_flow()
    return ""


def trigger_automatic_oauth_flow() -> bool:
    """Trigger automatic OAuth flow with callback handling"""
    if not spotify_client:
        initialize_spotify()

    if spotify_client:
        return spotify_client.start_oauth_flow_with_server()
    return False


def complete_oauth_with_code(code: str) -> bool:
    """Complete OAuth flow with authorization code"""
    if not spotify_client:
        return False

    return spotify_client.complete_oauth_flow(code)


def update_spotify_settings(
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    market_country: Optional[str] = None,
):
    """Update Spotify settings"""
    if not spotify_client:
        initialize_spotify()

    if spotify_client:
        if client_id is not None:
            spotify_client.update_field("client_id", client_id)
        if client_secret is not None:
            spotify_client.update_field("client_secret", client_secret)
        if market_country is not None:
            spotify_client.update_field("market_country", market_country)

        spotify_client._sync_client_credentials(persist=True)

        # If we now have credentials and aren't authenticated, try to authenticate
        if should_auto_initialize() and not spotify_client.is_authenticated:
            spotify_client.authenticate()


def trigger_authentication() -> bool:
    """Trigger Spotify authentication process - exposed function for UI"""
    if not spotify_client:
        initialize_spotify()

    if spotify_client:
        return spotify_client.authenticate()
    return False


def test_and_reconnect() -> bool:
    """Test current tokens and reconnect if possible without full OAuth"""
    if not spotify_client:
        return False

    # Run diagnosis to help identify potential issues
    diagnosis = diagnose_token_storage()
    logger.info("Running Spotify connection test with diagnosis...")

    # If no tokens found but there are potential other database locations,
    # inform the user about possible migration
    if not diagnosis["current_tokens_found"] and diagnosis["potential_locations"]:
        logger.warning(
            "No Spotify tokens found in current database, but other database files detected."
        )
        logger.info(
            "You may need to re-authorize Spotify if you were using a different database type previously."
        )
        logger.info(
            f"Detected potential database locations: {diagnosis['potential_locations']}"
        )

    return spotify_client.authenticate()


def diagnose_token_storage() -> Dict[str, Any]:
    """Diagnose where Spotify tokens might be stored across different database types"""
    diagnosis = {
        "current_database_type": "Unknown",
        "current_tokens_found": False,
        "potential_locations": [],
    }

    try:
        from .database_manager import database_manager
        from .dataobjects import state_manager

        # Try to get current database type from config if available
        try:
            from .dataobjects import state_manager

            db_settings = state_manager.get_database_settings()
            if db_settings:
                diagnosis["current_database_type"] = db_settings.database_type
        except:
            diagnosis["current_database_type"] = "Unknown"

        # Check current location
        current_spotify_data = state_manager.get_spotify_data()
        if current_spotify_data and (
            current_spotify_data.access_token or current_spotify_data.refresh_token
        ):
            diagnosis["current_tokens_found"] = True

        # Try to check different potential database locations
        try:
            # Check if there's a Firebase database config
            import os

            if os.path.exists("ServiceAccountKey.json"):
                diagnosis["potential_locations"].append(
                    "Firebase (ServiceAccountKey.json found)"
                )

            # Check for SQLite databases
            sqlite_files = [f for f in os.listdir(".") if f.endswith(".db")]
            for db_file in sqlite_files:
                diagnosis["potential_locations"].append(f"SQLite ({db_file})")

        except Exception as e:
            logger.debug(f"Error checking potential database locations: {e}")

        logger.info(f"Database diagnosis: {diagnosis}")
        return diagnosis

    except Exception as e:
        logger.error(f"Error in diagnose_token_storage: {str(e)}", exc_info=True)
        return diagnosis


def spotify_configured_for_monitor() -> bool:
    from .connection_status_tracker import spotify_configured

    return spotify_configured()


def spotify_has_stored_tokens() -> bool:
    if not spotify_client:
        return False
    data = spotify_client.spotify_data
    return bool(
        (data.access_token or "").strip() or (data.refresh_token or "").strip()
    )


def attempt_auto_reconnect() -> bool:
    if not spotify_client:
        return False
    if spotify_client.is_in_auth_cooldown() or spotify_client.is_in_api_backoff():
        return False
    if not spotify_has_stored_tokens():
        return False
    try:
        from .connection_monitor import (
            is_internet_available,
            is_service_reachable,
        )

        if not is_internet_available() or not is_service_reachable("spotify"):
            return False
    except Exception:
        return False
    if spotify_client.authenticate():
        try:
            from . import web_engine

            web_engine.broadcast_overlay_recovery("spotify", "reconnected")
        except Exception:
            pass
        return True
    return False
