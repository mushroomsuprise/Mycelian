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

import http.server
import logging
import re
import socketserver
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Set
from urllib.parse import parse_qs, quote, urlparse

import requests

from .dataobjects import YouTubeData, state_manager

logger = logging.getLogger(__name__)

# Global variables
youtube_client: Optional["YouTubeClient"] = None
youtube_thread: Optional[threading.Thread] = None
youtube_live_thread: Optional[threading.Thread] = None
is_running = False

# Google OAuth for live chat (loopback; must match Google Cloud redirect URI)
YOUTUBE_OAUTH_REDIRECT_URI = "http://127.0.0.1:9974"
YOUTUBE_OAUTH_PORT = 9974
YOUTUBE_OAUTH_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
YOUTUBE_OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
YOUTUBE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

_LIVE_OFFLINE_BACKOFF_SEC = 60.0
_SEEN_MESSAGE_IDS_MAX = 5000


class _YouTubeOAuthTCPServer(socketserver.TCPServer):
    """TCP server with OAuth callback attributes."""

    def __init__(self, server_address, RequestHandlerClass):
        super().__init__(server_address, RequestHandlerClass)
        self.auth_code: Optional[str] = None
        self.auth_error: Optional[str] = None


class _YouTubeOAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for Google OAuth redirect callbacks."""

    def do_GET(self):
        try:
            parsed_url = urlparse(self.path)
            query_params = parse_qs(parsed_url.query)

            if "code" in query_params:
                auth_code = query_params["code"][0]
                if isinstance(self.server, _YouTubeOAuthTCPServer):
                    self.server.auth_code = auth_code

                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                success_html = """
                <!DOCTYPE html>
                <html>
                <head><title>YouTube Authorization Complete</title>
                <style>
                    body { font-family: Arial, sans-serif; text-align: center;
                           margin-top: 50px; background-color: #FF0000; color: white; }
                    .container { max-width: 400px; margin: 0 auto; padding: 20px; }
                    .success { background-color: rgba(255,255,255,0.1); padding: 15px;
                               border-radius: 5px; }
                </style>
                </head>
                <body>
                    <div class="container">
                        <h1>Authorization Successful!</h1>
                        <div class="success">
                            <p>YouTube has been successfully connected to Mycelian.</p>
                            <p>You can close this window and return to the application.</p>
                        </div>
                    </div>
                    <script>setTimeout(function() { window.close(); }, 3000);</script>
                </body>
                </html>
                """
                self.wfile.write(success_html.encode())

            elif "error" in query_params:
                error = query_params["error"][0]
                if isinstance(self.server, _YouTubeOAuthTCPServer):
                    self.server.auth_error = error

                self.send_response(400)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                error_html = f"""
                <!DOCTYPE html>
                <html>
                <head><title>YouTube Authorization Error</title>
                <style>
                    body {{ font-family: Arial, sans-serif; text-align: center;
                           margin-top: 50px; background-color: #333; color: white; }}
                    .container {{ max-width: 400px; margin: 0 auto; padding: 20px; }}
                    .error {{ background-color: rgba(255,255,255,0.1); padding: 15px;
                              border-radius: 5px; }}
                </style>
                </head>
                <body>
                    <div class="container">
                        <h1>Authorization Failed</h1>
                        <div class="error">
                            <p>Error: {error}</p>
                            <p>Please try again from the Mycelian application.</p>
                        </div>
                    </div>
                    <script>setTimeout(function() {{ window.close(); }}, 5000);</script>
                </body>
                </html>
                """
                self.wfile.write(error_html.encode())
        except Exception as e:
            logger.error("Error handling YouTube OAuth callback: %s", e, exc_info=True)
            self.send_response(500)
            self.end_headers()

    def log_message(self, format, *args):
        pass


class YouTubeOAuthCallbackServer:
    """Temporary HTTP server for Google OAuth callbacks on loopback."""

    def __init__(self, port: int = YOUTUBE_OAUTH_PORT):
        self.port = port
        self.server: Optional[_YouTubeOAuthTCPServer] = None
        self.server_thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        try:
            self.server = _YouTubeOAuthTCPServer(
                ("127.0.0.1", self.port), _YouTubeOAuthCallbackHandler
            )
            self.server_thread = threading.Thread(
                target=self.server.serve_forever, daemon=True
            )
            self.server_thread.start()
            logger.debug("YouTube OAuth callback server started on port %s", self.port)
            return True
        except Exception as e:
            logger.error(
                "Error starting YouTube OAuth callback server: %s", e, exc_info=True
            )
            return False

    def stop(self) -> None:
        try:
            if self.server:
                self.server.shutdown()
                self.server.server_close()
            if self.server_thread and self.server_thread.is_alive():
                self.server_thread.join(timeout=2)
            logger.debug("YouTube OAuth callback server stopped")
        except Exception as e:
            logger.error(
                "Error stopping YouTube OAuth callback server: %s", e, exc_info=True
            )

    def wait_for_callback(self, timeout: int = 300) -> Dict[str, Any]:
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.server and self.server.auth_code:
                return {"success": True, "code": self.server.auth_code}
            if self.server and self.server.auth_error:
                return {"success": False, "error": self.server.auth_error}
            time.sleep(0.5)
        return {"success": False, "error": "timeout"}


def _is_youtube_chat_overlay_enabled() -> bool:
    """Read EnableYouTubeChat from chat template config (default False)."""
    try:
        from .template_config_parser import TemplateConfigParser, _config_element_value

        cfg = TemplateConfigParser().load_config(
            "chat", include_dynamic_controls=False
        )
        return bool(_config_element_value(cfg, "EnableYouTubeChat"))
    except Exception as e:
        logger.debug("Could not read EnableYouTubeChat: %s", e)
        return False


class YouTubeClient:
    """
    YouTube Data API v3 client for fetching channel information and latest videos,
    plus optional OAuth live chat / membership / Super Chat ingestion.
    """

    def __init__(self):
        self.youtube_data: YouTubeData = YouTubeData()
        self.is_connected = False
        self.update_interval = 1800.0  # 30 minutes default
        self.running = False
        self._last_api_success = False
        self._quota_exceeded_until = 0.0  # Timestamp when quota error expires
        self._excluded_video_ids_cache: Dict[str, set] = {}
        self._refresh_lock = threading.Lock()
        self._live_running = False
        self._live_chat_id: Optional[str] = None
        self._live_page_token: Optional[str] = None
        self._seen_message_ids: Set[str] = set()
        self._seen_message_id_order: list = []
        self._live_bootstrap_done = False

        # Load existing data from state manager
        self.load_youtube_data()

    def is_quota_blocked(self) -> bool:
        """True while YouTube API quota backoff is active."""
        return time.time() < self._quota_exceeded_until

    def load_youtube_data(self):
        """Load YouTube data from state manager"""
        try:
            state_manager.initialize()
            self.youtube_data = state_manager.get_youtube_data()

            # Only log detailed info if we have credentials
            has_api_key = bool(self.youtube_data.api_key)
            has_channel_urls = bool(self.youtube_data.channel_urls)

            if has_api_key or has_channel_urls:
                logger.info(
                    f"YouTube data loaded - API key: {'SET' if has_api_key else 'EMPTY'}, channel URLs: {'SET' if has_channel_urls else 'EMPTY'}"
                )
                logger.debug(f"  - channel_urls: {self.youtube_data.channel_urls}")
                logger.debug(f"  - channels count: {len(self.youtube_data.channels)}")
                logger.debug(
                    f"  - connection_status: {self.youtube_data.connection_status}"
                )
            else:
                logger.debug("YouTube data loaded - no API key or channel URLs found")

            self._sync_oauth_client_credentials(persist=True)

        except Exception as e:
            logger.error(f"Error loading YouTube data: {str(e)}", exc_info=True)
            self.youtube_data = YouTubeData()

    def _sync_oauth_client_credentials(self, persist: bool = False) -> bool:
        """Merge OAuth client id/secret from api_credentials_manager into YouTubeData."""
        try:
            from .api_credentials_manager import api_credentials_manager

            creds = api_credentials_manager.get_youtube_credentials()
            cid = (creds.get("client_id") or "").strip()
            secret = (creds.get("client_secret") or "").strip()
            if not cid and not secret:
                return bool(
                    self.youtube_data.oauth_client_id
                    and self.youtube_data.oauth_client_secret
                )

            changed = False
            if cid and cid != self.youtube_data.oauth_client_id:
                self.update_field("oauth_client_id", cid)
                changed = True
            if secret and secret != self.youtube_data.oauth_client_secret:
                self.update_field("oauth_client_secret", secret)
                changed = True

            if persist and changed:
                state_manager.save_changes()
                logger.info(
                    "Synced YouTube OAuth client credentials from api_credentials"
                )
            return bool(
                self.youtube_data.oauth_client_id
                and self.youtube_data.oauth_client_secret
            )
        except Exception as e:
            logger.error(
                "Error syncing YouTube OAuth credentials: %s", e, exc_info=True
            )
            return bool(
                self.youtube_data.oauth_client_id
                and self.youtube_data.oauth_client_secret
            )

    def parse_channel_urls(self) -> list[str]:
        """Parse channel URLs from the pipe-separated string"""
        if not self.youtube_data.channel_urls:
            return []

        # Split by pipe and clean up whitespace
        urls = [
            url.strip()
            for url in self.youtube_data.channel_urls.split("|")
            if url.strip()
        ]
        return urls

    def get_channel_key(self, channel_url: str) -> str:
        """Generate a consistent key for a channel based on its URL"""
        # Use the channel ID or a hash of the URL as the key
        channel_id = self.extract_channel_id_from_url(channel_url)
        if channel_id:
            return channel_id
        # Fallback to URL hash if we can't extract channel ID
        return str(hash(channel_url))

    def save_youtube_data(self):
        """Save current YouTube data to state manager"""
        try:
            youtube_dict = {
                field.name: getattr(self.youtube_data, field.name)
                for field in YouTubeData.__dataclass_fields__.values()
                if not field.name.startswith("_")
            }
            state_manager.set_youtube_data(youtube_dict)
            logger.debug("Saved YouTube data to state manager")
        except Exception as e:
            logger.error(f"Error saving YouTube data: {str(e)}", exc_info=True)

    def update_field(self, field: str, value: Any):
        """Update a single field in YouTube data"""
        try:
            if hasattr(self.youtube_data, field):
                setattr(self.youtube_data, field, value)
                state_manager.update_youtube_field(field, value)
                logger.debug(f"Updated YouTube field {field}: {value}")
            else:
                logger.error(f"Invalid YouTube field: {field}")
        except Exception as e:
            logger.error(
                f"Error updating YouTube field {field}: {str(e)}", exc_info=True
            )

    def extract_channel_id_from_url(self, url: str) -> Optional[str]:
        """
        Extract channel ID from various YouTube channel URL formats.
        Checks cached channel IDs first to avoid unnecessary API calls.

        Args:
            url (str): YouTube channel URL

        Returns:
            Optional[str]: Channel ID if found, None otherwise
        """
        try:
            # Clean the URL
            url = url.strip()

            # First, check if we already have this channel ID cached
            for channel_key, channel_data in self.youtube_data.channels.items():
                if isinstance(channel_data, dict):
                    cached_url = channel_data.get("channel_url", "")
                    cached_id = channel_data.get("channel_id", "")
                    if cached_url == url and cached_id:
                        logger.debug(
                            f"Using cached channel ID {cached_id} for URL: {url}"
                        )
                        return cached_id

            # Handle different YouTube URL formats
            patterns = [
                # /channel/UC... format
                r"/channel/([UC][^/?#&]+)",
                # /c/ or /user/ format (these need to be resolved via API)
                r"/c/([^/?#&]+)",
                r"/user/([^/?#&]+)",
                r"/@([^/?#&]+)",
                # youtube.com/channel/UC...
                r"youtube\.com/channel/([UC][^/?#&]+)",
                # youtube.com/c/ or youtube.com/user/
                r"youtube\.com/c/([^/?#&]+)",
                r"youtube\.com/user/([^/?#&]+)",
                r"youtube\.com/@([^/?#&]+)",
            ]

            parsed_url = urlparse(url)
            path = parsed_url.path

            for pattern in patterns:
                match = re.search(pattern, path, re.IGNORECASE)
                if match:
                    channel_identifier = match.group(1)
                    # If it's already a channel ID (starts with UC), return it
                    if (
                        channel_identifier.startswith("UC")
                        and len(channel_identifier) == 24
                    ):
                        return channel_identifier
                    # Otherwise, we need to resolve it via API
                    return self._resolve_channel_identifier(channel_identifier)

            logger.warning(f"Could not extract channel identifier from URL: {url}")
            return None

        except Exception as e:
            logger.error(
                f"Error extracting channel ID from URL {url}: {str(e)}", exc_info=True
            )
            return None

    def _resolve_channel_identifier(self, identifier: str) -> Optional[str]:
        """
        Resolve a channel identifier (username, custom URL, @handle) to channel ID

        Args:
            identifier (str): Channel identifier to resolve

        Returns:
            Optional[str]: Channel ID if found, None otherwise
        """
        try:
            if not self.youtube_data.api_key:
                logger.warning("No API key available to resolve channel identifier")
                return None

            # Check if quota is exceeded
            if time.time() < self._quota_exceeded_until:
                logger.warning(
                    "Skipping channel resolution - quota exceeded, waiting for retry"
                )
                return None

            # Try to resolve via search API
            search_url = "https://www.googleapis.com/youtube/v3/search"
            params = {
                "part": "snippet",
                "q": identifier,
                "type": "channel",
                "key": self.youtube_data.api_key,
                "maxResults": 1,
            }

            response = requests.get(search_url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                if data.get("items"):
                    channel_id = data["items"][0]["snippet"]["channelId"]
                    logger.info(
                        f"Resolved channel identifier '{identifier}' to channel ID: {channel_id}"
                    )
                    return channel_id
                else:
                    logger.warning(f"No channel found for identifier: {identifier}")
                    return None
            else:
                # Check for quota errors
                if self._handle_quota_error(response):
                    return None
                logger.error(
                    f"YouTube API search error: {response.status_code} - {response.text}"
                )
                return None

        except Exception as e:
            logger.error(
                f"Error resolving channel identifier {identifier}: {str(e)}",
                exc_info=True,
            )
            return None

    def get_channel_info(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """
        Get channel information from YouTube API

        Args:
            channel_id (str): YouTube channel ID

        Returns:
            Optional[Dict[str, Any]]: Channel information if successful, None otherwise
        """
        try:
            if not self.youtube_data.api_key:
                logger.warning("No API key available for channel info request")
                return None

            url = "https://www.googleapis.com/youtube/v3/channels"
            params = {
                "part": "snippet,contentDetails",
                "id": channel_id,
                "key": self.youtube_data.api_key,
            }

            # Check if quota is exceeded
            if time.time() < self._quota_exceeded_until:
                logger.warning(
                    "Skipping channel info request - quota exceeded, waiting for retry"
                )
                return None

            logger.debug(
                f"Making YouTube API call to get channel info for: {channel_id}"
            )
            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                if data.get("items"):
                    channel_item = data["items"][0]
                    # Return both snippet and contentDetails
                    channel_info = {
                        **channel_item.get("snippet", {}),
                        **channel_item.get("contentDetails", {}),
                    }
                    logger.debug(
                        "Successfully retrieved channel info with uploads playlist"
                    )
                    # Reset quota error flag on successful call
                    self._quota_exceeded_until = 0.0
                    return channel_info
                else:
                    logger.warning(f"No channel found with ID: {channel_id}")
                    return None
            else:
                # Check for quota errors
                if self._handle_quota_error(response):
                    return None
                logger.error(
                    f"YouTube API channels error: {response.status_code} - {response.text}"
                )
                return None

        except Exception as e:
            logger.error(
                f"Error getting channel info for {channel_id}: {str(e)}", exc_info=True
            )
            return None

    def get_latest_videos(
        self,
        channel_id: str,
        uploads_playlist_id: Optional[str] = None,
        max_results: int = 10,
    ) -> list[Dict[str, Any]]:
        """
        Get the most recent uploaded videos from a YouTube channel.

        Args:
            channel_id: YouTube channel ID
            uploads_playlist_id: Uploads playlist ID if already cached
            max_results: Maximum number of videos to return (default 10)

        Returns:
            List of video snippet dicts, newest first. Empty list on failure.
        """
        try:
            if not self.youtube_data.api_key:
                logger.warning("No API key available for latest video request")
                return []

            if time.time() < self._quota_exceeded_until:
                logger.warning(
                    "Skipping latest video request - quota exceeded, waiting for retry"
                )
                return []

            if not uploads_playlist_id:
                channel_info = self.get_channel_info(channel_id)
                if not channel_info:
                    return []

                related_playlists = channel_info.get("relatedPlaylists", {})
                uploads_playlist_id = related_playlists.get("uploads")
                if not uploads_playlist_id:
                    logger.warning(
                        f"No uploads playlist found for channel: {channel_id}"
                    )
                    return []

            url = "https://www.googleapis.com/youtube/v3/playlistItems"
            params = {
                "part": "snippet",
                "playlistId": uploads_playlist_id,
                "key": self.youtube_data.api_key,
                "maxResults": max_results,
                "order": "date",
            }

            logger.debug(
                f"Making YouTube API call to get latest videos for channel: {channel_id}"
            )
            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                items = data.get("items", [])
                if items:
                    self._quota_exceeded_until = 0.0
                    return [item["snippet"] for item in items]
                else:
                    logger.warning(
                        f"No videos found in uploads playlist for channel: {channel_id}"
                    )
                    return []
            else:
                if self._handle_quota_error(response):
                    return []
                logger.error(
                    f"YouTube API playlistItems error: {response.status_code} - {response.text}"
                )
                return []

        except Exception as e:
            logger.error(
                f"Error getting latest videos for channel {channel_id}: {str(e)}",
                exc_info=True,
            )
            return []

    def _handle_quota_error(self, response: requests.Response) -> bool:
        """
        Detect quota exceeded errors from API responses

        Args:
            response (requests.Response): API response to check

        Returns:
            bool: True if quota error detected, False otherwise
        """
        if response.status_code == 403:
            try:
                error_data = response.json()
                error_info = error_data.get("error", {})
                errors = error_info.get("errors", [])
                for error in errors:
                    if error.get("reason") == "quotaExceeded":
                        # Set quota error expiration to 1 hour from now
                        self._quota_exceeded_until = time.time() + 3600
                        self.update_field(
                            "connection_status", "Quota Exceeded (retry in 1 hour)"
                        )
                        logger.warning(
                            "YouTube API quota exceeded - will retry after 1 hour"
                        )
                        return True
            except (ValueError, KeyError):
                # If we can't parse the error, check if it's a 403
                pass
        return False

    def get_channel_playlists(self, channel_id: str) -> list[Dict[str, str]]:
        """
        Fetch all playlists for a YouTube channel.

        Args:
            channel_id: YouTube channel ID

        Returns:
            List of dicts with 'playlist_id' and 'title' keys
        """
        try:
            if not self.youtube_data.api_key:
                return []

            if time.time() < self._quota_exceeded_until:
                logger.warning(
                    "Skipping playlists request - quota exceeded, waiting for retry"
                )
                return []

            url = "https://www.googleapis.com/youtube/v3/playlists"
            params = {
                "part": "snippet",
                "channelId": channel_id,
                "key": self.youtube_data.api_key,
                "maxResults": 50,
            }

            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                self._quota_exceeded_until = 0.0
                return [
                    {
                        "playlist_id": item["id"],
                        "title": item["snippet"]["title"],
                    }
                    for item in data.get("items", [])
                ]
            else:
                if self._handle_quota_error(response):
                    return []
                logger.error(
                    f"YouTube API playlists error: {response.status_code} - {response.text}"
                )
                return []

        except Exception as e:
            logger.error(
                f"Error fetching playlists for channel {channel_id}: {str(e)}",
                exc_info=True,
            )
            return []

    def get_playlist_video_ids(self, playlist_id: str) -> set[str]:
        """
        Fetch video IDs from a YouTube playlist.

        Args:
            playlist_id: YouTube playlist ID

        Returns:
            Set of video IDs in the playlist
        """
        try:
            if not self.youtube_data.api_key:
                return set()

            if time.time() < self._quota_exceeded_until:
                return set()

            url = "https://www.googleapis.com/youtube/v3/playlistItems"
            params = {
                "part": "contentDetails",
                "playlistId": playlist_id,
                "key": self.youtube_data.api_key,
                "maxResults": 50,
            }

            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                self._quota_exceeded_until = 0.0
                return {
                    item["contentDetails"]["videoId"]
                    for item in data.get("items", [])
                    if "contentDetails" in item and "videoId" in item["contentDetails"]
                }
            else:
                if self._handle_quota_error(response):
                    return set()
                logger.error(
                    f"YouTube API playlistItems error: {response.status_code} - {response.text}"
                )
                return set()

        except Exception as e:
            logger.error(
                f"Error fetching video IDs for playlist {playlist_id}: {str(e)}",
                exc_info=True,
            )
            return set()

    def _get_excluded_video_ids(self, channel_id: str) -> set[str]:
        """
        Build a set of video IDs that should be excluded based on playlist filter.

        Checks which channel playlists match the filter names (case-insensitive
        exact match) and collects all video IDs from those playlists.

        Args:
            channel_id: YouTube channel ID

        Returns:
            Set of video IDs to exclude
        """
        filter_list = self.youtube_data.playlist_filter
        if not filter_list:
            return set()

        # Use cache if available for this channel within the same update cycle
        if channel_id in self._excluded_video_ids_cache:
            return self._excluded_video_ids_cache[channel_id]

        filter_names_lower = {name.lower() for name in filter_list}

        channel_playlists = self.get_channel_playlists(channel_id)
        if not channel_playlists:
            self._excluded_video_ids_cache[channel_id] = set()
            return set()

        matching_playlists = [
            pl
            for pl in channel_playlists
            if pl["title"].lower() in filter_names_lower
        ]

        if not matching_playlists:
            logger.debug(
                f"No playlists matched the filter for channel {channel_id}"
            )
            self._excluded_video_ids_cache[channel_id] = set()
            return set()

        excluded_ids: set[str] = set()
        for pl in matching_playlists:
            video_ids = self.get_playlist_video_ids(pl["playlist_id"])
            excluded_ids |= video_ids
            logger.debug(
                f"Playlist '{pl['title']}' has {len(video_ids)} videos to exclude"
            )

        logger.info(
            f"Excluding {len(excluded_ids)} videos from {len(matching_playlists)} "
            f"filtered playlists for channel {channel_id}"
        )
        self._excluded_video_ids_cache[channel_id] = excluded_ids
        return excluded_ids

    def authenticate(self) -> bool:
        """
        Test current authentication and channel configuration

        Returns:
            bool: True if authentication and channel setup is successful, False otherwise
        """
        try:
            if not self.youtube_data.api_key:
                logger.warning("YouTube API key not configured")
                self.update_field("connection_status", "API Key Required")
                self.is_connected = False
                return False

            # Parse channel URLs
            channel_urls = self.parse_channel_urls()
            if not channel_urls:
                logger.warning("No YouTube channel URLs configured")
                self.update_field("connection_status", "Channel URLs Required")
                self.is_connected = False
                return False

            # Test each channel
            successful_channels = 0
            failed_channels = 0

            for channel_url in channel_urls:
                try:
                    # Extract channel ID from URL (will use cache if available)
                    channel_id = self.extract_channel_id_from_url(channel_url)
                    if not channel_id:
                        logger.warning(
                            f"Could not extract channel ID from URL: {channel_url}"
                        )
                        failed_channels += 1
                        continue

                    channel_key = self.get_channel_key(channel_url)

                    # Check if we already have channel info cached
                    cached_channel_data = self.youtube_data.channels.get(
                        channel_key, {}
                    )
                    cached_playlist_id = cached_channel_data.get("uploads_playlist_id")

                    # Test API connection by getting channel info (only if not cached)
                    channel_info = None
                    if cached_playlist_id:
                        # Use cached data, but verify channel still exists
                        channel_info = self.get_channel_info(channel_id)
                        if not channel_info:
                            # Channel info fetch failed, but we have cached playlist ID
                            # Use cached data for now
                            channel_title = cached_channel_data.get(
                                "channel_title", "Unknown Channel"
                            )
                            logger.info(
                                f"Using cached data for channel: {channel_title}"
                            )
                            successful_channels += 1
                            continue
                    else:
                        # No cached playlist ID, fetch channel info
                        channel_info = self.get_channel_info(channel_id)

                    if channel_info:
                        channel_title = channel_info.get("title", "Unknown Channel")

                        # Extract uploads playlist ID
                        related_playlists = channel_info.get("relatedPlaylists", {})
                        uploads_playlist_id = related_playlists.get("uploads")

                        # Store channel data with uploads playlist ID
                        from .dataobjects import YouTubeChannelData

                        channel_data = YouTubeChannelData(
                            channel_url=channel_url,
                            channel_id=channel_id,
                            channel_title=channel_title,
                        )

                        # Add uploads playlist ID to channel data
                        channel_dict = channel_data.__dict__
                        if uploads_playlist_id:
                            channel_dict["uploads_playlist_id"] = uploads_playlist_id

                        self.youtube_data.channels[channel_key] = channel_dict
                        successful_channels += 1

                        logger.info(
                            f"Successfully authenticated channel: {channel_title}"
                        )
                    else:
                        # If we have cached data, use it
                        if cached_channel_data:
                            logger.warning(
                                f"Failed to refresh channel info for {channel_url}, using cached data"
                            )
                            successful_channels += 1
                        else:
                            logger.warning(
                                f"Failed to get info for channel: {channel_url}"
                            )
                            failed_channels += 1

                except Exception as e:
                    logger.error(f"Error processing channel {channel_url}: {str(e)}")
                    failed_channels += 1

            # Determine overall status
            if successful_channels > 0:
                self.is_connected = True
                if failed_channels == 0:
                    self.update_field(
                        "connection_status",
                        f"Connected ({successful_channels} channels)",
                    )
                else:
                    self.update_field(
                        "connection_status",
                        f"Partial ({successful_channels}/{successful_channels + failed_channels} channels)",
                    )
                logger.info(
                    f"YouTube authentication successful for {successful_channels} channels"
                )
                return True
            else:
                self.is_connected = False
                self.update_field("connection_status", "All Channels Failed")
                logger.error("YouTube authentication failed for all channels")
                return False

        except Exception as e:
            logger.error(
                f"Error during YouTube authentication: {str(e)}", exc_info=True
            )
            self.is_connected = False
            self.update_field("connection_status", f"Error: {str(e)}")
            return False

    def update_video_data(self):
        """Update latest video data for all configured channels and find global latest"""
        try:
            start_time = time.time()

            # Check if quota is exceeded before starting
            if time.time() < self._quota_exceeded_until:
                logger.warning(
                    "Skipping video update - quota exceeded, waiting for retry"
                )
                return

            if not self.is_connected:
                if not self.authenticate():
                    return

            # Reload playlist_filter from state manager so UI changes take effect
            self.load_youtube_data()

            # Clear per-cycle exclusion cache
            self._excluded_video_ids_cache.clear()

            # Parse channel URLs
            channel_urls = self.parse_channel_urls()
            if not channel_urls:
                logger.warning("No channel URLs available for video update")
                return

            # Track all latest videos to find the global latest
            all_videos = []
            updated_channels = 0

            for channel_url in channel_urls:
                try:
                    channel_id = self.extract_channel_id_from_url(channel_url)
                    if not channel_id:
                        logger.warning(
                            f"Could not extract channel ID from URL: {channel_url}"
                        )
                        continue

                    channel_key = self.get_channel_key(channel_url)

                    cached_channel_data = self.youtube_data.channels.get(
                        channel_key, {}
                    )
                    uploads_playlist_id = cached_channel_data.get("uploads_playlist_id")

                    recent_videos = self.get_latest_videos(
                        channel_id, uploads_playlist_id=uploads_playlist_id
                    )

                    # Cache the uploads playlist ID if we didn't have it
                    if recent_videos and not uploads_playlist_id:
                        channel_info = self.get_channel_info(channel_id)
                        if channel_info:
                            related_playlists = channel_info.get("relatedPlaylists", {})
                            new_playlist_id = related_playlists.get("uploads")
                            if new_playlist_id:
                                if channel_key not in self.youtube_data.channels:
                                    self.youtube_data.channels[channel_key] = {}
                                self.youtube_data.channels[channel_key][
                                    "uploads_playlist_id"
                                ] = new_playlist_id

                    if not recent_videos:
                        logger.warning(
                            f"Failed to get latest video for channel: {channel_url}"
                        )
                        continue

                    # Build exclusion set for this channel
                    excluded_ids = self._get_excluded_video_ids(channel_id)

                    # Find the first non-excluded video
                    chosen_video = None
                    for video_snippet in recent_videos:
                        vid = video_snippet.get("resourceId", {}).get(
                            "videoId"
                        ) or video_snippet.get("videoId", "")
                        if vid and vid not in excluded_ids:
                            chosen_video = video_snippet
                            break
                        elif vid:
                            logger.debug(
                                f"Video '{video_snippet.get('title', '')}' excluded by playlist filter"
                            )

                    if not chosen_video:
                        logger.debug(
                            f"All recent videos for channel {channel_url} were excluded by playlist filter"
                        )
                        continue

                    video_id = chosen_video.get("resourceId", {}).get(
                        "videoId"
                    ) or chosen_video.get("videoId", "")
                    video_title = chosen_video.get("title", "")
                    published_at = chosen_video.get("publishedAt", "")
                    thumbnail = (
                        chosen_video.get("thumbnails", {})
                        .get("default", {})
                        .get("url", "")
                    )

                    channel_title = "Unknown Channel"
                    if channel_key in self.youtube_data.channels:
                        channel_data = self.youtube_data.channels[channel_key]
                        channel_title = channel_data.get(
                            "channel_title", "Unknown Channel"
                        )

                    video_data = {
                        "video_id": video_id,
                        "video_title": video_title,
                        "published_at": published_at,
                        "thumbnail": thumbnail,
                        "channel_id": channel_id,
                        "channel_title": channel_title,
                        "channel_key": channel_key,
                    }

                    if channel_key not in self.youtube_data.channels:
                        self.youtube_data.channels[channel_key] = {}

                    update_data = {
                        "latest_video_id": video_id,
                        "latest_video_title": video_title,
                        "latest_video_url": f"https://www.youtube.com/watch?v={video_id}",
                        "latest_video_published_at": published_at,
                        "latest_video_thumbnail": thumbnail,
                        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    if (
                        "uploads_playlist_id"
                        in self.youtube_data.channels[channel_key]
                    ):
                        update_data["uploads_playlist_id"] = (
                            self.youtube_data.channels[channel_key][
                                "uploads_playlist_id"
                            ]
                        )

                    self.youtube_data.channels[channel_key].update(update_data)

                    all_videos.append(video_data)
                    updated_channels += 1

                    logger.debug(f"Updated video data for channel: {channel_title}")

                except Exception as e:
                    logger.error(
                        f"Error updating video data for channel {channel_url}: {str(e)}"
                    )

            # Find the global latest video across all channels
            if all_videos:
                # Sort by published date (most recent first)
                all_videos.sort(key=lambda x: x["published_at"], reverse=True)
                global_latest = all_videos[0]

                # Check if this is a new global latest video
                if global_latest["video_id"] != self.youtube_data.latest_video_id:
                    self.youtube_data.latest_video_id = global_latest["video_id"]
                    self.youtube_data.latest_video_title = global_latest["video_title"]
                    self.youtube_data.latest_video_published_at = global_latest[
                        "published_at"
                    ]
                    self.youtube_data.latest_video_thumbnail = global_latest[
                        "thumbnail"
                    ]
                    self.youtube_data.latest_video_url = (
                        f"https://www.youtube.com/watch?v={global_latest['video_id']}"
                    )
                    self.youtube_data.latest_video_channel = global_latest[
                        "channel_title"
                    ]
                    self.youtube_data.last_updated = time.strftime("%Y-%m-%d %H:%M:%S")

                    # Save changes
                    self.save_youtube_data()

                    logger.info(
                        f"New global latest YouTube video: {global_latest['video_title']} from {global_latest['channel_title']}"
                    )
                else:
                    logger.debug("Global latest YouTube video unchanged")

            if updated_channels == 0:
                logger.warning("Failed to update video data for any channels")

            api_time = time.time() - start_time
            if api_time > 10:
                logger.warning("Slow YouTube API response: %.3fs", api_time)

        except Exception as e:
            logger.error(f"Error updating YouTube video data: {str(e)}", exc_info=True)

    def start_monitoring(self):
        """Start monitoring YouTube channel for new videos"""
        self.running = True
        logger.info("Started YouTube video monitoring")

        while self.running:
            start_time = time.time()
            try:
                if self.is_connected:
                    self.update_video_data()
                    self.send_websocket_data()
                else:
                    if not (self.youtube_data.api_key or "").strip():
                        pass
                    elif self.authenticate():
                        self.update_video_data()

                # Calculate precise sleep time to maintain exact intervals
                elapsed = time.time() - start_time
                sleep_time = max(0, self.update_interval - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    logger.debug(
                        "YouTube update loop exceeded interval: %.3fs", elapsed
                    )
            except Exception as e:
                logger.error(
                    f"Error in YouTube monitoring loop: {str(e)}", exc_info=True
                )
                time.sleep(30)  # Wait longer on error

    def send_websocket_data(self):
        """Send current YouTube data via websocket"""
        try:
            from . import web_engine

            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                youtube_dict = {
                    field.name: getattr(self.youtube_data, field.name)
                    for field in YouTubeData.__dataclass_fields__.values()
                    if not field.name.startswith("_")
                }
                web_engine.web_engine_instance.safe_emit(
                    "youtube_data_update", youtube_dict
                )
        except Exception as e:
            logger.debug(f"Error sending YouTube websocket data: {str(e)}")

    def stop_monitoring(self):
        """Stop monitoring"""
        self.running = False
        logger.info("Stopped YouTube monitoring")

    def disconnect(self):
        """Disconnect from YouTube API"""
        self.stop_monitoring()
        self.stop_live_chat_monitoring()
        self.is_connected = False
        self.update_field("connection_status", "Disconnected")
        logger.info("Disconnected from YouTube API")

    # ------------------------------------------------------------------
    # Google OAuth (live chat / memberships / Super Chats)
    # ------------------------------------------------------------------

    def has_oauth_credentials(self) -> bool:
        return bool(
            (self.youtube_data.oauth_client_id or "").strip()
            and (self.youtube_data.oauth_client_secret or "").strip()
        )

    def has_oauth_tokens(self) -> bool:
        return bool((self.youtube_data.refresh_token or "").strip())

    def build_oauth_url(self) -> str:
        client_id = (self.youtube_data.oauth_client_id or "").strip()
        if not client_id:
            return ""
        params = (
            f"client_id={quote(client_id)}"
            f"&redirect_uri={quote(YOUTUBE_OAUTH_REDIRECT_URI)}"
            f"&response_type=code"
            f"&scope={quote(YOUTUBE_OAUTH_SCOPE)}"
            f"&access_type=offline"
            f"&prompt=consent"
        )
        return f"{YOUTUBE_OAUTH_AUTH_URL}?{params}"

    def start_oauth_flow_with_server(self) -> bool:
        """Open browser and complete Google OAuth via localhost callback."""
        try:
            if not self.has_oauth_credentials():
                self.update_field("live_chat_status", "OAuth credentials required")
                return False

            oauth_server = YouTubeOAuthCallbackServer()
            if not oauth_server.start():
                self.update_field("live_chat_status", "OAuth server error")
                return False

            auth_url = self.build_oauth_url()
            if not auth_url:
                oauth_server.stop()
                return False

            self.update_field("live_chat_status", "Opening browser...")
            webbrowser.open(auth_url)
            logger.info("Opened YouTube Google OAuth URL in browser")

            result = oauth_server.wait_for_callback()
            oauth_server.stop()

            if result.get("success"):
                if self.complete_oauth_flow(result["code"]):
                    logger.info("Successfully completed YouTube OAuth flow")
                    return True
                self.update_field("live_chat_status", "Token exchange failed")
                return False

            error = result.get("error", "Unknown error")
            logger.error("YouTube OAuth callback failed: %s", error)
            if error == "timeout":
                self.update_field("live_chat_status", "Authorization timeout")
            else:
                self.update_field("live_chat_status", f"Authorization error: {error}")
            return False
        except Exception as e:
            logger.error(
                "Error in YouTube OAuth flow with server: %s", e, exc_info=True
            )
            self.update_field("live_chat_status", "OAuth error")
            return False

    def complete_oauth_flow(self, authorization_code: str) -> bool:
        """Exchange authorization code for tokens and resolve channel identity."""
        try:
            data = {
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": YOUTUBE_OAUTH_REDIRECT_URI,
                "client_id": self.youtube_data.oauth_client_id,
                "client_secret": self.youtube_data.oauth_client_secret,
            }
            response = requests.post(
                YOUTUBE_OAUTH_TOKEN_URL, data=data, timeout=15
            )
            if response.status_code != 200:
                logger.error(
                    "YouTube token exchange failed: %s %s",
                    response.status_code,
                    response.text[:300],
                )
                return False

            token_data = response.json()
            access_token = token_data.get("access_token", "")
            refresh_token = token_data.get("refresh_token") or self.youtube_data.refresh_token
            expires_in = int(token_data.get("expires_in", 3600))
            expiry = (
                datetime.now(timezone.utc) + timedelta(seconds=max(expires_in - 60, 60))
            ).isoformat()

            self.update_field("access_token", access_token)
            if refresh_token:
                self.update_field("refresh_token", refresh_token)
            self.update_field("token_expiry", expiry)

            if not self._fetch_oauth_channel_identity():
                self.update_field("live_chat_status", "Authorized (channel unknown)")
            else:
                self.update_field("live_chat_status", "Offline")

            state_manager.save_changes()
            return True
        except Exception as e:
            logger.error("Error completing YouTube OAuth: %s", e, exc_info=True)
            return False

    def disconnect_oauth(self) -> None:
        """Clear OAuth tokens (keeps client id/secret). Live loop keeps idling."""
        self.update_field("access_token", "")
        self.update_field("refresh_token", "")
        self.update_field("token_expiry", "")
        self.update_field("oauth_channel_id", "")
        self.update_field("oauth_channel_title", "")
        self.update_field("live_chat_status", "Not authorized")
        self._live_chat_id = None
        self._live_page_token = None
        self._live_bootstrap_done = False
        self._seen_message_ids.clear()
        self._seen_message_id_order.clear()
        state_manager.save_changes()
        logger.info("Disconnected YouTube OAuth")

    def _parse_token_expiry(self) -> Optional[datetime]:
        raw = (self.youtube_data.token_expiry or "").strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    def ensure_valid_access_token(self) -> bool:
        """Refresh access token if missing or near expiry."""
        with self._refresh_lock:
            if not self.has_oauth_tokens():
                return False
            expiry = self._parse_token_expiry()
            now = datetime.now(timezone.utc)
            if (
                (self.youtube_data.access_token or "").strip()
                and expiry
                and expiry > now
            ):
                return True
            return self._refresh_access_token()

    def _refresh_access_token(self) -> bool:
        try:
            refresh_token = (self.youtube_data.refresh_token or "").strip()
            if not refresh_token:
                return False
            data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.youtube_data.oauth_client_id,
                "client_secret": self.youtube_data.oauth_client_secret,
            }
            response = requests.post(
                YOUTUBE_OAUTH_TOKEN_URL, data=data, timeout=15
            )
            if response.status_code != 200:
                logger.error(
                    "YouTube token refresh failed: %s %s",
                    response.status_code,
                    response.text[:300],
                )
                self.update_field("live_chat_status", "Token refresh failed")
                return False

            token_data = response.json()
            access_token = token_data.get("access_token", "")
            expires_in = int(token_data.get("expires_in", 3600))
            expiry = (
                datetime.now(timezone.utc) + timedelta(seconds=max(expires_in - 60, 60))
            ).isoformat()
            self.update_field("access_token", access_token)
            self.update_field("token_expiry", expiry)
            if token_data.get("refresh_token"):
                self.update_field("refresh_token", token_data["refresh_token"])
            state_manager.save_changes()
            return True
        except Exception as e:
            logger.error("Error refreshing YouTube access token: %s", e, exc_info=True)
            return False

    def _oauth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.youtube_data.access_token}"}

    def _oauth_get(self, path: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Authenticated GET against YouTube Data API. Handles quota / auth errors."""
        if self.is_quota_blocked():
            return None
        if not self.ensure_valid_access_token():
            return None
        try:
            url = f"{YOUTUBE_API_BASE}/{path.lstrip('/')}"
            response = requests.get(
                url, headers=self._oauth_headers(), params=params, timeout=15
            )
            if response.status_code == 401:
                if self._refresh_access_token():
                    response = requests.get(
                        url,
                        headers=self._oauth_headers(),
                        params=params,
                        timeout=15,
                    )
                else:
                    return None

            if response.status_code == 403:
                try:
                    err = response.json().get("error", {})
                    reasons = [
                        e.get("reason", "")
                        for e in err.get("errors", [])
                        if isinstance(e, dict)
                    ]
                    if "quotaExceeded" in reasons or "dailyLimitExceeded" in reasons:
                        self._quota_exceeded_until = time.time() + 3600
                        self.update_field("live_chat_status", "Quota exceeded")
                        logger.warning("YouTube live chat hit API quota")
                        return None
                    if "liveChatEnded" in reasons or "liveChatDisabled" in reasons:
                        return {"__live_chat_ended__": True}
                except Exception:
                    pass
                logger.error(
                    "YouTube OAuth GET 403: %s", response.text[:300]
                )
                return None

            if response.status_code != 200:
                logger.error(
                    "YouTube OAuth GET %s failed: %s %s",
                    path,
                    response.status_code,
                    response.text[:300],
                )
                return None
            return response.json()
        except Exception as e:
            logger.error("YouTube OAuth GET error (%s): %s", path, e, exc_info=True)
            return None

    def _fetch_oauth_channel_identity(self) -> bool:
        data = self._oauth_get(
            "channels",
            {"part": "snippet", "mine": "true"},
        )
        if not data or not data.get("items"):
            return False
        item = data["items"][0]
        channel_id = item.get("id", "")
        title = (item.get("snippet") or {}).get("title", "")
        if channel_id:
            self.update_field("oauth_channel_id", channel_id)
        if title:
            self.update_field("oauth_channel_title", title)
        return bool(channel_id)

    # ------------------------------------------------------------------
    # Live chat poller
    # ------------------------------------------------------------------

    def should_run_live_chat(self) -> bool:
        return (
            bool(self.youtube_data.live_chat_enabled)
            and self.has_oauth_tokens()
            and self.has_oauth_credentials()
        )

    def start_live_chat_monitoring(self) -> None:
        if self._live_running:
            return
        if not self.should_run_live_chat():
            if not self.has_oauth_tokens():
                self.update_field("live_chat_status", "Not authorized")
            return
        self._live_running = True
        logger.info("Started YouTube live chat monitoring loop")
        while self._live_running:
            try:
                if self.is_quota_blocked():
                    wait = max(5.0, self._quota_exceeded_until - time.time())
                    self._live_sleep(min(wait, 60.0))
                    continue

                if not self.should_run_live_chat():
                    self.update_field("live_chat_status", "Not authorized")
                    self._live_sleep(_LIVE_OFFLINE_BACKOFF_SEC)
                    continue

                if not self._live_chat_id:
                    chat_id = self._discover_active_live_chat_id()
                    if not chat_id:
                        self.update_field("live_chat_status", "Offline")
                        self._live_sleep(_LIVE_OFFLINE_BACKOFF_SEC)
                        continue
                    self._live_chat_id = chat_id
                    self._live_page_token = None
                    self._live_bootstrap_done = False
                    prev_status = (
                        getattr(self.youtube_data, "live_chat_status", "") or ""
                    )
                    self.update_field("live_chat_status", "Live")
                    logger.info("YouTube live chat discovered: %s", chat_id)
                    if prev_status != "Live":
                        try:
                            from . import discord_service

                            discord_service.notify_platform_live("youtube")
                        except Exception:
                            logger.debug(
                                "Discord YouTube go-live hook failed",
                                exc_info=True,
                            )

                interval_ms = self._poll_live_chat_once()
                if interval_ms is None:
                    # Ended or error — rediscover
                    was_live = (
                        getattr(self.youtube_data, "live_chat_status", "") or ""
                    ) == "Live"
                    self._live_chat_id = None
                    self._live_page_token = None
                    self._live_bootstrap_done = False
                    if was_live:
                        self.update_field("live_chat_status", "Offline")
                        try:
                            from . import discord_service

                            discord_service.notify_platform_offline("youtube")
                        except Exception:
                            logger.debug(
                                "Discord YouTube offline hook failed",
                                exc_info=True,
                            )
                    self._live_sleep(_LIVE_OFFLINE_BACKOFF_SEC)
                else:
                    self._live_sleep(max(interval_ms / 1000.0, 1.0))
            except Exception as e:
                logger.error(
                    "Error in YouTube live chat loop: %s", e, exc_info=True
                )
                self.update_field("live_chat_status", "Error")
                self._live_sleep(_LIVE_OFFLINE_BACKOFF_SEC)

        logger.info("Stopped YouTube live chat monitoring loop")

    def stop_live_chat_monitoring(self) -> None:
        self._live_running = False
        logger.info("Stopping YouTube live chat monitoring")

    def _live_sleep(self, seconds: float) -> None:
        end = time.time() + max(0.1, seconds)
        while self._live_running and time.time() < end:
            time.sleep(min(0.5, end - time.time()))

    def _discover_active_live_chat_id(self) -> Optional[str]:
        data = self._oauth_get(
            "liveBroadcasts",
            {
                "part": "snippet",
                "mine": "true",
                "broadcastStatus": "active",
                # Default is "event"; stream-key / persistent lives need "all"
                "broadcastType": "all",
            },
        )
        if not data or data.get("__live_chat_ended__"):
            return None
        items = data.get("items") or []
        for item in items:
            snippet = item.get("snippet") or {}
            chat_id = snippet.get("liveChatId")
            if chat_id:
                return chat_id
        return None

    def _poll_live_chat_once(self) -> Optional[int]:
        """
        Poll liveChatMessages once.

        Returns pollingIntervalMillis on success, or None if chat ended / fatal.
        """
        if not self._live_chat_id:
            return None

        params: Dict[str, Any] = {
            "part": "snippet,authorDetails",
            "liveChatId": self._live_chat_id,
            "maxResults": 200,
        }
        if self._live_page_token:
            params["pageToken"] = self._live_page_token

        data = self._oauth_get("liveChat/messages", params)
        if data is None:
            return None
        if data.get("__live_chat_ended__"):
            self.update_field("live_chat_status", "Offline")
            return None

        next_token = data.get("nextPageToken")
        if next_token:
            self._live_page_token = next_token

        items = data.get("items") or []
        # First page without a prior token is history — mark seen, don't fire alerts
        is_bootstrap = not self._live_bootstrap_done
        for item in items:
            msg_id = item.get("id") or ""
            if not msg_id:
                continue
            if msg_id in self._seen_message_ids:
                continue
            self._remember_message_id(msg_id)
            if is_bootstrap:
                continue
            try:
                self._route_live_chat_message(item)
            except Exception as e:
                logger.error(
                    "Error routing YouTube live chat message: %s", e, exc_info=True
                )

        if is_bootstrap:
            self._live_bootstrap_done = True
            logger.debug(
                "YouTube live chat bootstrap complete (%s messages marked seen)",
                len(items),
            )

        if any(
            (i.get("snippet") or {}).get("type") == "chatEndedEvent" for i in items
        ):
            self.update_field("live_chat_status", "Offline")
            return None

        interval = data.get("pollingIntervalMillis")
        try:
            return int(interval) if interval is not None else 5000
        except (TypeError, ValueError):
            return 5000

    def _remember_message_id(self, msg_id: str) -> None:
        if msg_id in self._seen_message_ids:
            return
        self._seen_message_ids.add(msg_id)
        self._seen_message_id_order.append(msg_id)
        while len(self._seen_message_id_order) > _SEEN_MESSAGE_IDS_MAX:
            old = self._seen_message_id_order.pop(0)
            self._seen_message_ids.discard(old)

    def _route_live_chat_message(self, item: Dict[str, Any]) -> None:
        snippet = item.get("snippet") or {}
        author = item.get("authorDetails") or {}
        msg_type = snippet.get("type") or ""
        username = author.get("displayName") or "YouTube User"
        user_id = author.get("channelId") or snippet.get("authorChannelId") or ""

        if msg_type == "textMessageEvent":
            self._emit_youtube_chat_message(item, username, user_id)
            return
        if msg_type == "newSponsorEvent":
            self._emit_membership_sub_alert(username, snippet)
            return
        if msg_type == "memberMilestoneChatEvent":
            self._emit_membership_resub_alert(username, snippet)
            return
        if msg_type == "membershipGiftingEvent":
            self._emit_membership_gift_alert(username, snippet)
            return
        if msg_type in ("superChatEvent", "superStickerEvent", "fanFundingEvent"):
            self._emit_superchat_donation_alert(username, snippet, msg_type)
            return

    def _alerts_enabled(self) -> bool:
        return bool(getattr(self.youtube_data, "alerts_enabled", True))

    def _schedule_connector_event(self, event_data: Dict[str, Any]) -> None:
        try:
            from .connector_manager import get_manager

            mgr = get_manager()
            if mgr and mgr.is_running:
                mgr._schedule_event_on_connector_loop(event_data)
        except Exception as e:
            logger.debug("YouTube connector event drop: %s", e)

    def _process_chatbot_event(self, event_type, event_data: Dict[str, Any]) -> None:
        try:
            from .chatbot_manager import get_manager as get_chatbot_manager
            from .chatbot import dispatch_chatbot_response

            chatbot_manager = get_chatbot_manager()
            result = chatbot_manager.process_event(event_type, event_data)
            if not result:
                return
            if isinstance(result, tuple):
                response = result[0]
                targets = result[1] if len(result) > 1 else ["youtube"]
                discord_channels = result[2] if len(result) > 2 else None
            else:
                response, targets, discord_channels = result, ["youtube"], None
            dispatch_chatbot_response(
                response, targets, discord_channels=discord_channels
            )
        except Exception as e:
            logger.error(
                "Error processing YouTube chatbot event: %s", e, exc_info=True
            )

    def _enqueue_alert(self, alert) -> None:
        if not self._alerts_enabled():
            return
        from . import alert_processor, alertutils

        stored = dict(alert.__dict__)
        stored["source"] = "youtube"
        alert_processor.ALERT_QUEUE.append(alert)
        alertutils.alert_state_manager.store_completed_alert(alert.alert_id, stored)

    def send_live_chat_message(self, text: str) -> bool:
        message = (text or "").strip()
        if not message:
            return False
        if not self._live_chat_id:
            logger.warning("Cannot send YouTube chat: no active liveChatId")
            return False
        if not self.ensure_valid_access_token():
            logger.warning("Cannot send YouTube chat: OAuth token unavailable")
            return False
        try:
            url = f"{YOUTUBE_API_BASE}/liveChat/messages"
            body = {
                "snippet": {
                    "liveChatId": self._live_chat_id,
                    "type": "textMessageEvent",
                    "textMessageDetails": {"messageText": message},
                }
            }
            response = requests.post(
                url,
                headers={
                    **self._oauth_headers(),
                    "Content-Type": "application/json",
                },
                params={"part": "snippet"},
                json=body,
                timeout=15,
            )
            if response.status_code in (200, 201):
                logger.info("Sent YouTube live chat message")
                return True
            logger.error(
                "YouTube live chat insert failed: %s %s",
                response.status_code,
                response.text[:300],
            )
            return False
        except Exception as e:
            logger.error("Error sending YouTube live chat: %s", e, exc_info=True)
            return False

    def _emit_youtube_chat_message(
        self, item: Dict[str, Any], username: str, user_id: str
    ) -> None:
        snippet = item.get("snippet") or {}
        text_details = snippet.get("textMessageDetails") or {}
        message = (
            text_details.get("messageText")
            or snippet.get("displayMessage")
            or ""
        )
        if not message:
            return

        from .connector_core import EventData
        from .chatbot_core import EventType

        self._schedule_connector_event(
            EventData.from_youtube_chat(
                username=username, message=message, user_id=user_id
            )
        )
        self._process_chatbot_event(
            EventType.YOUTUBE_CHAT_MESSAGE,
            {
                "username": username,
                "message": message,
                "userid": user_id,
                "timestamp": time.time(),
                "source": "youtube",
            },
        )

        if not _is_youtube_chat_overlay_enabled():
            return

        msg_dict = {
            "id": item.get("id") or f"yt-{int(time.time() * 1000)}",
            "username": username,
            "userid": user_id,
            "message": message,
            "twmsgid": item.get("id") or "",
            "fragments": None,
            "color": "",
            "badges": "",
            "emotes": "",
            "timestamp": time.time(),
            "type": "chat",
            "message_type": "text",
            "platform": "youtube",
        }
        try:
            from . import web_engine

            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                web_engine.web_engine_instance.new_message(msg_dict)
        except Exception as e:
            logger.error("Error emitting YouTube chat message: %s", e, exc_info=True)

    def _emit_membership_sub_alert(
        self, username: str, snippet: Dict[str, Any]
    ) -> None:
        from . import alertutils
        from .chatbot_core import EventType
        from .connector_core import EventData
        from .uiwindows.activity_feed import add_alert_to_feed

        details = snippet.get("newSponsorDetails") or {}
        level = details.get("memberLevelName") or ""
        current_timestamp = time.time()
        message = snippet.get("displayMessage") or ""

        self._schedule_connector_event(
            EventData.from_youtube_member(
                username=username, member_level=level, message=message
            )
        )
        self._process_chatbot_event(
            EventType.YOUTUBE_MEMBERSHIP,
            {
                "username": username,
                "tier": 1,
                "months": 1,
                "message": message,
                "member_level": level,
                "timestamp": current_timestamp,
                "source": "youtube",
            },
        )

        alert = alertutils.fetch_sub_alert(1)
        if alert is None:
            alert = alertutils.AlertObj()
        alert.username = username
        alert.alert_type = "sub"
        alert.tier = 1
        alert.message = message
        alert.alert_id = f"Alert{round(current_timestamp)}"
        alert.timestamp = current_timestamp
        try:
            alert.member_level = level
        except Exception:
            pass
        self._enqueue_alert(alert)
        if not self._alerts_enabled():
            return
        self._send_instant_alert(
            {
                "type": "sub",
                "username": username,
                "tier": 1,
                "message": alert.message,
                "alert_id": alert.alert_id,
                "timestamp": alert.timestamp,
                "source": "youtube",
                "member_level": level,
            }
        )
        add_alert_to_feed(
            alert_type="Membership",
            message=(
                f"{username} became a channel member"
                + (f" ({level})!" if level else "!")
            ),
            badge_type="membership",
            timestamp=str(int(alert.timestamp)),
            tier=1,
            user_message=alert.message,
            alert_id=alert.alert_id,
            username=username,
        )

    def _emit_membership_resub_alert(
        self, username: str, snippet: Dict[str, Any]
    ) -> None:
        from . import alertutils
        from .chatbot_core import EventType
        from .connector_core import EventData
        from .uiwindows.activity_feed import add_alert_to_feed

        details = snippet.get("memberMilestoneChatDetails") or {}
        try:
            months = int(details.get("memberMonth") or 1)
        except (TypeError, ValueError):
            months = 1
        months = max(months, 1)
        level = details.get("memberLevelName") or ""
        user_msg = details.get("userComment") or ""
        current_timestamp = time.time()
        message = user_msg or snippet.get("displayMessage") or ""

        self._schedule_connector_event(
            EventData.from_youtube_member_milestone(
                username=username,
                months=months,
                member_level=level,
                message=message,
            )
        )
        self._process_chatbot_event(
            EventType.YOUTUBE_MEMBERSHIP_MILESTONE,
            {
                "username": username,
                "tier": 1,
                "months": months,
                "cumulative_months": months,
                "message": message,
                "member_level": level,
                "timestamp": current_timestamp,
                "source": "youtube",
            },
        )

        if months <= 1:
            alert = alertutils.fetch_sub_alert(1)
            alert_type = "sub"
            feed_type = "Membership"
            badge = "membership"
        else:
            alert = alertutils.fetch_resub_alert(months)
            alert_type = "resub"
            feed_type = "Member Milestone"
            badge = "member_milestone"
        if alert is None:
            alert = alertutils.AlertObj()

        alert.username = username
        alert.alert_type = alert_type
        alert.tier = 1
        alert.message = message
        alert.resub_month = months
        alert.alert_id = f"Alert{round(current_timestamp)}"
        alert.timestamp = current_timestamp
        try:
            alert.member_level = level
        except Exception:
            pass
        self._enqueue_alert(alert)
        if not self._alerts_enabled():
            return
        instant = {
            "type": alert_type,
            "username": username,
            "tier": 1,
            "message": alert.message,
            "alert_id": alert.alert_id,
            "timestamp": alert.timestamp,
            "source": "youtube",
            "member_level": level,
        }
        if alert_type == "resub":
            instant["cumulative_months"] = months
        self._send_instant_alert(instant)
        if alert_type == "resub":
            feed_msg = f"{username} continued membership for {months} months!"
        else:
            feed_msg = f"{username} became a channel member!"
        if level:
            feed_msg = feed_msg[:-1] + f" ({level})!"
        add_alert_to_feed(
            alert_type=feed_type,
            message=feed_msg,
            badge_type=badge,
            timestamp=str(int(alert.timestamp)),
            tier=1,
            user_message=alert.message,
            alert_id=alert.alert_id,
            username=username,
        )

    def _emit_membership_gift_alert(
        self, username: str, snippet: Dict[str, Any]
    ) -> None:
        from . import alertutils
        from .chatbot_core import EventType
        from .connector_core import EventData
        from .uiwindows.activity_feed import add_alert_to_feed

        details = snippet.get("membershipGiftingDetails") or {}
        try:
            qty = int(details.get("giftMembershipsCount") or 1)
        except (TypeError, ValueError):
            qty = 1
        qty = max(qty, 1)
        level = details.get("giftMembershipsLevelName") or ""
        current_timestamp = time.time()
        message = snippet.get("displayMessage") or ""

        self._schedule_connector_event(
            EventData.from_youtube_gift_membership(
                username=username, gift_count=qty, member_level=level
            )
        )
        self._process_chatbot_event(
            EventType.YOUTUBE_GIFT_MEMBERSHIP,
            {
                "username": username,
                "tier": 1,
                "amount": qty,
                "total_gifts": qty,
                "quantity": qty,
                "member_level": level,
                "timestamp": current_timestamp,
                "source": "youtube",
            },
        )

        alert = alertutils.fetch_giftsub_alert(qty)
        if alert is None:
            alert = alertutils.AlertObj()
            alert.alert_type = "giftsub"
        alert.username = username
        alert.alert_type = "giftsub"
        alert.tier = 1
        alert.gift_qty = qty
        alert.message = message
        alert.alert_id = f"Alert{round(current_timestamp)}"
        alert.timestamp = current_timestamp
        try:
            alert.member_level = level
        except Exception:
            pass
        self._enqueue_alert(alert)
        if not self._alerts_enabled():
            return
        self._send_instant_alert(
            {
                "type": "giftsub",
                "username": username,
                "tier": 1,
                "gift_qty": qty,
                "alert_id": alert.alert_id,
                "timestamp": alert.timestamp,
                "source": "youtube",
                "member_level": level,
            }
        )
        add_alert_to_feed(
            alert_type="Gift Membership",
            message=(
                f"{username} gifted {qty} membership"
                + ("s" if qty != 1 else "")
                + (f" ({level})!" if level else "!")
            ),
            badge_type="gift_membership",
            timestamp=str(int(alert.timestamp)),
            tier=1,
            alert_id=alert.alert_id,
            username=username,
        )

    def _emit_superchat_donation_alert(
        self, username: str, snippet: Dict[str, Any], msg_type: str
    ) -> None:
        from . import alertutils
        from .chatbot_core import EventType
        from .connector_core import EventData
        from .uiwindows.activity_feed import add_alert_to_feed

        if msg_type == "superChatEvent":
            details = snippet.get("superChatDetails") or {}
        elif msg_type == "superStickerEvent":
            details = snippet.get("superStickerDetails") or {}
        else:
            details = snippet.get("fanFundingEventDetails") or {}

        try:
            micros = int(details.get("amountMicros") or 0)
        except (TypeError, ValueError):
            micros = 0
        amount = micros / 1_000_000.0 if micros else 0.0
        currency = details.get("currency") or "USD"
        display_amount = details.get("amountDisplayString") or f"{amount:.2f}"
        user_msg = details.get("userComment") or ""
        if msg_type == "superStickerEvent" and not user_msg:
            meta = details.get("superStickerMetadata") or {}
            user_msg = meta.get("altText") or snippet.get("displayMessage") or ""
        message = user_msg or snippet.get("displayMessage") or ""
        current_timestamp = time.time()

        is_sticker = msg_type == "superStickerEvent"
        connector_type = (
            "youtube_supersticker" if is_sticker else "youtube_superchat"
        )
        self._schedule_connector_event(
            EventData.from_youtube_superchat(
                username=username,
                amount=amount,
                currency=currency,
                message=message,
                display_amount=display_amount,
                event_type=connector_type,
            )
        )
        chatbot_type = (
            EventType.YOUTUBE_SUPERSTICKER
            if is_sticker
            else EventType.YOUTUBE_SUPERCHAT
        )
        self._process_chatbot_event(
            chatbot_type,
            {
                "username": username,
                "amount": amount,
                "currency": currency,
                "formatted_amount": display_amount,
                "display_amount": display_amount,
                "message": message,
                "donation_message": message,
                "timestamp": current_timestamp,
                "source": "youtube",
            },
        )

        quantity = max(1, int(round(amount))) if amount > 0 else 1
        alert = alertutils.fetch_donation_alert(quantity)
        if alert is None:
            alert = alertutils.AlertObj()
        alert.username = username
        alert.alert_type = "donation"
        alert.donation_amount = float(amount)
        alert.currency = currency
        alert.message = message
        alert.alert_id = f"Alert{round(current_timestamp)}"
        alert.timestamp = current_timestamp
        try:
            alert.is_supersticker = bool(is_sticker)
            alert.display_amount = display_amount
        except Exception:
            pass
        self._enqueue_alert(alert)
        if not self._alerts_enabled():
            return
        self._send_instant_alert(
            {
                "type": "donation",
                "username": username,
                "donation_amount": alert.donation_amount,
                "currency": currency,
                "message": alert.message,
                "alert_id": alert.alert_id,
                "timestamp": alert.timestamp,
                "source": "youtube",
                "display_amount": display_amount,
            }
        )
        if is_sticker:
            feed_type, badge, label = "Super Sticker", "supersticker", "Super Sticker"
        else:
            feed_type, badge, label = "Super Chat", "superchat", "Super Chat"
        add_alert_to_feed(
            alert_type=feed_type,
            message=f"{username} sent a {label} of {display_amount}!",
            badge_type=badge,
            timestamp=str(int(alert.timestamp)),
            user_message=alert.message,
            alert_id=alert.alert_id,
            username=username,
        )

    def _send_instant_alert(self, alert_data: Dict[str, Any]) -> None:
        try:
            from . import web_engine

            if (
                hasattr(web_engine, "web_engine_instance")
                and web_engine.web_engine_instance
            ):
                web_engine.web_engine_instance.instant_alert(alert_data)
        except Exception as e:
            logger.error(
                "Error sending YouTube instant alert: %s", e, exc_info=True
            )


# Module-level functions
def initialize_youtube():
    """Initialize the YouTube client"""
    global youtube_client
    try:
        if not youtube_client:
            youtube_client = YouTubeClient()
            logger.info("Initialized YouTube client")
        return youtube_client
    except Exception as e:
        logger.error(f"Error initializing YouTube client: {str(e)}", exc_info=True)
        return None


def should_auto_initialize() -> bool:
    """Check if YouTube should auto-initialize based on available credentials"""
    try:
        if not youtube_client:
            logger.debug("should_auto_initialize: No youtube_client available")
            return False

        data = youtube_client.youtube_data

        # Auto-initialize if we have API key and channel URLs
        has_api_key = bool(data.api_key)
        has_channel_urls = bool(data.channel_urls)

        logger.debug(
            f"YouTube auto-initialization check - has_api_key: {has_api_key}, has_channel_urls: {has_channel_urls}"
        )

        result = has_api_key and has_channel_urls
        if result:
            logger.info("YouTube credentials found - enabling auto-initialization")
        else:
            logger.debug("No YouTube credentials found - skipping auto-initialization")
        return result
    except Exception as e:
        logger.error(f"Error checking YouTube auto-initialization: {str(e)}")
        return False


def start_youtube_service():
    """Start the YouTube service in a background thread"""
    global youtube_thread, youtube_live_thread, is_running, youtube_client

    if is_running:
        logger.warning("YouTube service already running")
        return False

    try:
        youtube_client = initialize_youtube()
        if not youtube_client:
            return False

        # Auto-initialize if credentials are present
        if should_auto_initialize():
            logger.info("Auto-initializing YouTube service with existing credentials")
            youtube_client.authenticate()

        # Start monitoring thread (upload / latest-video)
        youtube_thread = threading.Thread(
            target=youtube_client.start_monitoring, daemon=True
        )
        youtube_thread.start()

        # Separate live chat poller (OAuth); harmless if not authorized
        youtube_live_thread = threading.Thread(
            target=youtube_client.start_live_chat_monitoring, daemon=True
        )
        youtube_live_thread.start()

        is_running = True

        logger.info("Started YouTube service")
        return True
    except Exception as e:
        logger.error(f"Error starting YouTube service: {str(e)}", exc_info=True)
        return False


def stop_youtube_service(*, join_timeout: float = 5.0) -> None:
    """Stop the YouTube service"""
    global youtube_thread, youtube_live_thread, is_running, youtube_client

    if not is_running:
        logger.warning("YouTube service not running")
        return

    try:
        if youtube_client:
            youtube_client.stop_monitoring()
            youtube_client.stop_live_chat_monitoring()

        is_running = False

        if youtube_thread and youtube_thread.is_alive():
            youtube_thread.join(timeout=join_timeout)
        if youtube_live_thread and youtube_live_thread.is_alive():
            youtube_live_thread.join(timeout=join_timeout)

        logger.info("Stopped YouTube service")
    except Exception as e:
        logger.error(f"Error stopping YouTube service: {str(e)}", exc_info=True)


def get_youtube_client() -> Optional[YouTubeClient]:
    """Get the current YouTube client instance"""
    return youtube_client


def get_youtube_status() -> Dict[str, Any]:
    """Get current YouTube connection status"""
    from .connection_status_tracker import apply_connectivity_overlay_to_info

    if not youtube_client:
        return apply_connectivity_overlay_to_info(
            "youtube",
            {
                "status": "Not Initialized",
                "is_connected": False,
                "latest_video": "N/A",
            },
            valid_field="is_connected",
        )

    return apply_connectivity_overlay_to_info(
        "youtube",
        {
            "status": youtube_client.youtube_data.connection_status,
            "is_connected": youtube_client.is_connected,
            "latest_video": youtube_client.youtube_data.latest_video_title
            or "No video found",
            "channel_count": len(youtube_client.youtube_data.channels)
            if youtube_client.youtube_data.channels
            else 0,
            "latest_video_channel": youtube_client.youtube_data.latest_video_channel
            or "N/A",
            "live_chat_status": youtube_client.youtube_data.live_chat_status
            or "Not authorized",
            "oauth_connected": youtube_client.has_oauth_tokens(),
            "oauth_channel_title": youtube_client.youtube_data.oauth_channel_title
            or "",
            "live_chat_enabled": bool(youtube_client.youtube_data.live_chat_enabled),
        },
        valid_field="is_connected",
    )


def trigger_youtube_oauth_flow() -> bool:
    """Start the automatic Google OAuth browser flow for live chat."""
    if not youtube_client:
        initialize_youtube()
    if not youtube_client:
        return False
    # Ensure latest credentials from state + api_credentials.json
    youtube_client.load_youtube_data()
    youtube_client._sync_oauth_client_credentials(persist=True)
    return youtube_client.start_oauth_flow_with_server()


def disconnect_youtube_oauth() -> None:
    """Clear YouTube OAuth tokens."""
    if not youtube_client:
        initialize_youtube()
    if youtube_client:
        youtube_client.load_youtube_data()
        youtube_client.disconnect_oauth()


def restart_youtube_live_chat_if_needed() -> None:
    """
    Ensure the live chat loop is running after settings/OAuth changes.

    The loop is already started with the service; this reloads data so the
    next iteration picks up new tokens / enable flags.
    """
    if not youtube_client:
        return
    youtube_client.load_youtube_data()


def send_youtube_chat_message(message: str) -> bool:
    """Send a message to the active YouTube live chat (OAuth required)."""
    if not youtube_client:
        initialize_youtube()
    if not youtube_client:
        return False
    return youtube_client.send_live_chat_message(message)

def update_youtube_settings(
    api_key: Optional[str] = None, channel_url: Optional[str] = None
):
    """Update YouTube settings"""
    if not youtube_client:
        initialize_youtube()

    if youtube_client:
        if api_key is not None:
            youtube_client.update_field("api_key", api_key)
        if channel_url is not None:
            youtube_client.update_field("channel_url", channel_url)
            # Clear channel ID when URL changes so it gets re-resolved
            youtube_client.update_field("channel_id", "")

        # If we now have credentials, try to authenticate
        if should_auto_initialize() and not youtube_client.is_connected:
            youtube_client.authenticate()


def get_latest_video_url() -> Optional[str]:
    """Get the URL of the most recent uploaded video"""
    if not youtube_client:
        return None

    if youtube_client.youtube_data.latest_video_url:
        return youtube_client.youtube_data.latest_video_url

    # Try to fetch latest video if not available
    if youtube_client.is_connected:
        youtube_client.update_video_data()
        return youtube_client.youtube_data.latest_video_url

    return None


def trigger_authentication() -> bool:
    """Trigger YouTube authentication process"""
    if not youtube_client:
        initialize_youtube()

    if youtube_client:
        return youtube_client.authenticate()
    return False


def test_connection() -> bool:
    """Test YouTube connection (wrapper for UI compatibility)"""
    return trigger_authentication()


def youtube_configured_for_monitor() -> bool:
    from .connection_status_tracker import youtube_configured

    return youtube_configured()


def attempt_auto_reconnect() -> bool:
    if not youtube_client:
        return False
    if not youtube_configured_for_monitor():
        return False
    if youtube_client.is_quota_blocked():
        return False
    if not (youtube_client.youtube_data.api_key or "").strip():
        return False
    try:
        from .connection_monitor import (
            is_internet_available,
            is_service_reachable,
        )

        if not is_internet_available() or not is_service_reachable("youtube"):
            return False
    except Exception:
        return False
    return youtube_client.authenticate()
