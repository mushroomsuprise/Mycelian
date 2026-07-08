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

import logging
import re
import threading
import time
from typing import Optional, Dict, Any
from urllib.parse import urlparse

import requests

from .dataobjects import YouTubeData, state_manager

logger = logging.getLogger(__name__)

# Global variables
youtube_client: Optional["YouTubeClient"] = None
youtube_thread: Optional[threading.Thread] = None
is_running = False


class YouTubeClient:
    """
    YouTube Data API v3 client for fetching channel information and latest videos
    """

    def __init__(self):
        self.youtube_data: YouTubeData = YouTubeData()
        self.is_connected = False
        self.update_interval = 1800.0  # 30 minutes default
        self.running = False
        self._last_api_success = False
        self._quota_exceeded_until = 0.0  # Timestamp when quota error expires
        self._excluded_video_ids_cache: Dict[str, set] = {}

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

        except Exception as e:
            logger.error(f"Error loading YouTube data: {str(e)}", exc_info=True)
            self.youtube_data = YouTubeData()

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
        self.is_connected = False
        self.update_field("connection_status", "Disconnected")
        logger.info("Disconnected from YouTube API")


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
    global youtube_thread, is_running, youtube_client

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

        # Start monitoring thread
        youtube_thread = threading.Thread(
            target=youtube_client.start_monitoring, daemon=True
        )
        youtube_thread.start()
        is_running = True

        logger.info("Started YouTube service")
        return True
    except Exception as e:
        logger.error(f"Error starting YouTube service: {str(e)}", exc_info=True)
        return False


def stop_youtube_service(*, join_timeout: float = 5.0) -> None:
    """Stop the YouTube service"""
    global youtube_thread, is_running, youtube_client

    if not is_running:
        logger.warning("YouTube service not running")
        return

    try:
        if youtube_client:
            youtube_client.stop_monitoring()

        is_running = False

        if youtube_thread and youtube_thread.is_alive():
            youtube_thread.join(timeout=join_timeout)

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
        },
        valid_field="is_connected",
    )


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
