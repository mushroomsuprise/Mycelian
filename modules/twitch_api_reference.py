# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Twitch API Reference Documentation
==================================

This module contains structured information about all Twitch API endpoints
including URLs, request parameters, and response structures.

All endpoints use the base URL: https://api.twitch.tv/helix/

Source: https://dev.twitch.tv/docs/api/reference/
"""

from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class APIEndpoint:
    """Represents a Twitch API endpoint with its specifications."""
    name: str
    endpoint: str
    method: str
    description: str
    authorization: List[str]
    required_params: Dict[str, str]
    optional_params: Dict[str, str]
    response_fields: List[str]
    rate_limits: Optional[str] = None
    auto_fill_params: Optional[Dict[str, str]] = None  # Auto-fill values (e.g., {"broadcaster_id": "user_id", "user_id": "trigger_user_id"})


class TwitchAPIReference:
    """Container for all Twitch API endpoints organized by category."""

    def __init__(self):
        self.ads = self._get_ads_endpoints()
        self.analytics = self._get_analytics_endpoints()
        self.bits = self._get_bits_endpoints()
        self.channels = self._get_channels_endpoints()
        self.channel_points = self._get_channel_points_endpoints()
        self.charity = self._get_charity_endpoints()
        self.chat = self._get_chat_endpoints()
        self.clips = self._get_clips_endpoints()
        self.conduits = self._get_conduits_endpoints()
        self.drops = self._get_drops_endpoints()
        self.extensions = self._get_extensions_endpoints()
        self.games = self._get_games_endpoints()
        self.goals = self._get_goals_endpoints()
        self.guest_star = self._get_guest_star_endpoints()
        self.hype_train = self._get_hype_train_endpoints()
        self.moderation = self._get_moderation_endpoints()
        self.polls = self._get_polls_endpoints()
        self.predictions = self._get_predictions_endpoints()
        self.raid = self._get_raid_endpoints()
        self.schedule = self._get_schedule_endpoints()
        self.search = self._get_search_endpoints()
        self.streams = self._get_streams_endpoints()
        self.subscriptions = self._get_subscriptions_endpoints()
        self.tags = self._get_tags_endpoints()
        self.teams = self._get_teams_endpoints()
        self.users = self._get_users_endpoints()
        self.videos = self._get_videos_endpoints()
        self.whispers = self._get_whispers_endpoints()

    def _get_ads_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Ads-related endpoints."""
        return {
            "start_commercial": APIEndpoint(
                name="Start Commercial",
                endpoint="/channels/commercial",
                method="POST",
                description="Starts a commercial on the specified channel",
                authorization=["channel:edit:commercial"],
                required_params={"broadcaster_id": "string"},
                optional_params={"length": "integer (30, 60, 90, 120, 150, 180)"},
                response_fields=["data", "length", "message", "retry_after"]
            ),
            "get_ad_schedule": APIEndpoint(
                name="Get Ad Schedule",
                endpoint="/channels/ads",
                method="GET",
                description="Returns ad schedule related information",
                authorization=["channel:read:ads"],
                required_params={"broadcaster_id": "string"},
                optional_params={},
                response_fields=["data", "broadcaster_id", "next_ad_at", "last_ad_at", "duration", "preroll_free_time", "snooze_count", "snooze_refresh_at"]
            ),
            "snooze_next_ad": APIEndpoint(
                name="Snooze Next Ad",
                endpoint="/channels/ads/snooze",
                method="POST",
                description="Pushes back the timestamp of the upcoming automatic mid-roll ad by 5 minutes",
                authorization=["channel:manage:ads"],
                required_params={"broadcaster_id": "string"},
                optional_params={},
                response_fields=["data", "snooze_refresh_at", "snooze_count"]
            )
        }

    def _get_analytics_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Analytics-related endpoints."""
        return {
            "get_extension_analytics": APIEndpoint(
                name="Get Extension Analytics",
                endpoint="/analytics/extensions",
                method="GET",
                description="Gets an analytics report for one or more extensions",
                authorization=["analytics:read:extensions"],
                required_params={},
                optional_params={"extension_id": "string", "type": "string (overview_v2)", "started_at": "RFC3339 timestamp", "ended_at": "RFC3339 timestamp", "first": "integer", "after": "string"},
                response_fields=["data", "extension_id", "URL", "type", "date_range"]
            ),
            "get_game_analytics": APIEndpoint(
                name="Get Game Analytics",
                endpoint="/analytics/games",
                method="GET",
                description="Gets an analytics report for one or more games",
                authorization=["analytics:read:games"],
                required_params={},
                optional_params={"game_id": "string", "type": "string (overview_v2)", "started_at": "RFC3339 timestamp", "ended_at": "RFC3339 timestamp", "first": "integer", "after": "string"},
                response_fields=["data", "game_id", "URL", "type", "date_range"]
            )
        }

    def _get_bits_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Bits-related endpoints."""
        return {
            "get_bits_leaderboard": APIEndpoint(
                name="Get Bits Leaderboard",
                endpoint="/bits/leaderboard",
                method="GET",
                description="Gets the Bits leaderboard for the authenticated broadcaster",
                authorization=["bits:read"],
                required_params={},
                optional_params={"count": "integer (1-100)", "period": "string (all, day, week, month, year)", "started_at": "RFC3339 timestamp", "user_id": "string"},
                response_fields=["data", "date_range", "total", "user_id", "user_login", "user_name", "rank", "score"],
                auto_fill_params={"user_id": "trigger_user_id"}
            ),
            "get_cheermotes": APIEndpoint(
                name="Get Cheermotes",
                endpoint="/bits/cheermotes",
                method="GET",
                description="Gets a list of Cheermotes that users can use to cheer Bits",
                authorization=[],
                required_params={},
                optional_params={"broadcaster_id": "string"},
                response_fields=["data", "prefix", "tiers", "type", "order", "last_updated", "is_charitable", "images", "can_cheer", "show_in_bits_card"],
                auto_fill_params={"broadcaster_id": "user_id"}
            ),
            "get_extension_transactions": APIEndpoint(
                name="Get Extension Transactions",
                endpoint="/extensions/transactions",
                method="GET",
                description="Gets an extension's list of transactions",
                authorization=["user:read:broadcast"],
                required_params={"extension_id": "string"},
                optional_params={"id": "string", "first": "integer", "after": "string"},
                response_fields=["data", "id", "timestamp", "broadcaster_id", "broadcaster_login", "broadcaster_name", "user_id", "user_login", "user_name", "product_type", "product_data", "broadcast"]
            )
        }

    def _get_channels_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Channels-related endpoints."""
        return {
            "get_channel_information": APIEndpoint(
                name="Get Channel Information",
                endpoint="/channels",
                method="GET",
                description="Gets information about one or more channels",
                authorization=[],
                required_params={"broadcaster_id": "string"},
                optional_params={},
                response_fields=["data", "broadcaster_id", "broadcaster_login", "broadcaster_name", "broadcaster_language", "game_name", "game_id", "title", "delay", "tags", "content_classification_labels", "is_branded_content"],
                auto_fill_params={"broadcaster_id": "user_id"}
            ),
            "modify_channel_information": APIEndpoint(
                name="Modify Channel Information",
                endpoint="/channels",
                method="PATCH",
                description="Updates a channel's properties",
                authorization=["channel:manage:broadcast"],
                required_params={"broadcaster_id": "string"},
                optional_params={"game_id": "string", "broadcaster_language": "string", "title": "string", "delay": "integer", "tags": "array", "content_classification_labels": "array", "is_branded_content": "boolean"},
                response_fields=[]
            ),
            "get_channel_editors": APIEndpoint(
                name="Get Channel Editors",
                endpoint="/channels/editors",
                method="GET",
                description="Gets the broadcaster's list editors",
                authorization=["channel:read:editors"],
                required_params={"broadcaster_id": "string"},
                optional_params={},
                response_fields=["data", "user_id", "user_name", "created_at"]
            ),
            "get_followed_channels": APIEndpoint(
                name="Get Followed Channels",
                endpoint="/channels/followed",
                method="GET",
                description="Gets a list of broadcasters that the specified user follows",
                authorization=["user:read:follows"],
                required_params={"user_id": "string"},
                optional_params={"broadcaster_id": "string", "first": "integer", "after": "string"},
                response_fields=["data", "broadcaster_id", "broadcaster_login", "broadcaster_name", "followed_at", "total", "pagination"]
            ),
            "get_channel_followers": APIEndpoint(
                name="Get Channel Followers",
                endpoint="/channels/followers",
                method="GET",
                description="Gets a list of users that follow the specified broadcaster",
                authorization=["moderator:read:followers"],
                required_params={"broadcaster_id": "string"},
                optional_params={"user_id": "string", "first": "integer", "after": "string"},
                response_fields=["data", "user_id", "user_login", "user_name", "followed_at", "total", "pagination"]
            )
        }

    def _get_channel_points_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Channel Points-related endpoints."""
        return {
            "create_custom_rewards": APIEndpoint(
                name="Create Custom Rewards",
                endpoint="/channel_points/custom_rewards",
                method="POST",
                description="Creates a Custom Reward in the broadcaster's channel",
                authorization=["channel:manage:redemptions"],
                required_params={"broadcaster_id": "string", "title": "string", "cost": "integer"},
                optional_params={"prompt": "string", "is_enabled": "boolean", "background_color": "string", "is_user_input_required": "boolean", "is_max_per_stream_enabled": "boolean", "max_per_stream": "integer", "is_max_per_user_per_stream_enabled": "boolean", "max_per_user_per_stream": "integer", "is_global_cooldown_enabled": "boolean", "global_cooldown_seconds": "integer", "should_redemptions_skip_request_queue": "boolean"},
                response_fields=["data", "broadcaster_id", "broadcaster_login", "broadcaster_name", "id", "title", "prompt", "cost", "image", "default_image", "background_color", "is_enabled", "is_user_input_required", "max_per_stream_setting", "max_per_user_per_stream_setting", "global_cooldown_setting", "is_paused", "is_in_stock", "redemptions_redeemed_current_stream", "cooldown_expires_at", "redemptions_redeemed_current_stream", "cooldown_expires_at"]
            ),
            "delete_custom_reward": APIEndpoint(
                name="Delete Custom Reward",
                endpoint="/channel_points/custom_rewards",
                method="DELETE",
                description="Deletes a custom reward that the broadcaster created",
                authorization=["channel:manage:redemptions"],
                required_params={"broadcaster_id": "string", "id": "string"},
                optional_params={},
                response_fields=[]
            ),
            "get_custom_reward": APIEndpoint(
                name="Get Custom Reward",
                endpoint="/channel_points/custom_rewards",
                method="GET",
                description="Gets a list of custom rewards that the specified broadcaster created",
                authorization=["channel:read:redemptions"],
                required_params={"broadcaster_id": "string"},
                optional_params={"id": "string", "only_manageable_rewards": "boolean"},
                response_fields=["data", "broadcaster_id", "broadcaster_login", "broadcaster_name", "id", "title", "prompt", "cost", "image", "default_image", "background_color", "is_enabled", "is_user_input_required", "max_per_stream_setting", "max_per_user_per_stream_setting", "global_cooldown_setting", "is_paused", "is_in_stock", "redemptions_redeemed_current_stream", "cooldown_expires_at"]
            ),
            "get_custom_reward_redemption": APIEndpoint(
                name="Get Custom Reward Redemption",
                endpoint="/channel_points/custom_rewards/redemptions",
                method="GET",
                description="Gets a list of redemptions for a custom reward",
                authorization=["channel:read:redemptions"],
                required_params={"broadcaster_id": "string", "reward_id": "string"},
                optional_params={"id": "string", "status": "string (CANCELED, FULFILLED, UNFULFILLED)", "sort": "string (OLDEST, NEWEST)", "after": "string", "first": "integer"},
                response_fields=["data", "broadcaster_id", "broadcaster_login", "broadcaster_name", "id", "user_id", "user_login", "user_name", "user_input", "status", "redeemed_at", "reward", "pagination"]
            ),
            "update_custom_reward": APIEndpoint(
                name="Update Custom Reward",
                endpoint="/channel_points/custom_rewards",
                method="PATCH",
                description="Updates a custom reward",
                authorization=["channel:manage:redemptions"],
                required_params={"broadcaster_id": "string", "id": "string"},
                optional_params={"title": "string", "prompt": "string", "cost": "integer", "background_color": "string", "is_enabled": "boolean", "is_user_input_required": "boolean", "is_max_per_stream_enabled": "boolean", "max_per_stream": "integer", "is_max_per_user_per_stream_enabled": "boolean", "max_per_user_per_stream": "integer", "is_global_cooldown_enabled": "boolean", "global_cooldown_seconds": "integer", "is_paused": "boolean", "should_redemptions_skip_request_queue": "boolean"},
                response_fields=["data", "broadcaster_id", "broadcaster_login", "broadcaster_name", "id", "title", "prompt", "cost", "image", "default_image", "background_color", "is_enabled", "is_user_input_required", "max_per_stream_setting", "max_per_user_per_stream_setting", "global_cooldown_setting", "is_paused", "is_in_stock", "redemptions_redeemed_current_stream", "cooldown_expires_at"]
            ),
            "update_redemption_status": APIEndpoint(
                name="Update Redemption Status",
                endpoint="/channel_points/custom_rewards/redemptions",
                method="PATCH",
                description="Updates a redemption's status",
                authorization=["channel:manage:redemptions"],
                required_params={"id": "string", "broadcaster_id": "string", "reward_id": "string", "status": "string (CANCELED, FULFILLED)"},
                optional_params={},
                response_fields=["data", "broadcaster_id", "broadcaster_login", "broadcaster_name", "id", "user_id", "user_login", "user_name", "user_input", "status", "redeemed_at", "reward"]
            )
        }

    def _get_charity_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Charity-related endpoints."""
        return {
            "get_charity_campaign": APIEndpoint(
                name="Get Charity Campaign",
                endpoint="/charity/campaigns",
                method="GET",
                description="Gets information about the broadcaster's active charity campaign",
                authorization=["channel:read:charity"],
                required_params={"broadcaster_id": "string"},
                optional_params={},
                response_fields=["data", "id", "broadcaster_id", "broadcaster_login", "broadcaster_name", "charity_name", "charity_description", "charity_logo", "charity_website", "current_amount", "target_amount"]
            ),
            "get_charity_campaign_donations": APIEndpoint(
                name="Get Charity Campaign Donations",
                endpoint="/charity/donations",
                method="GET",
                description="Gets the list of donations that users have made to the broadcaster's active charity campaign",
                authorization=["channel:read:charity"],
                required_params={"broadcaster_id": "string"},
                optional_params={"first": "integer", "after": "string"},
                response_fields=["data", "id", "broadcaster_id", "broadcaster_login", "broadcaster_name", "user_id", "user_login", "user_name", "amount", "donated_at", "pagination"]
            )
        }

    def _get_chat_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Chat-related endpoints."""
        return {
            "get_chatters": APIEndpoint(
                name="Get Chatters",
                endpoint="/chat/chatters",
                method="GET",
                description="Gets the list of users that are connected to the broadcaster's chat session",
                authorization=["moderator:read:chatters"],
                required_params={"broadcaster_id": "string", "moderator_id": "string"},
                optional_params={"first": "integer", "after": "string"},
                response_fields=["data", "user_id", "user_login", "user_name", "pagination", "total"]
            ),
            "get_channel_emotes": APIEndpoint(
                name="Get Channel Emotes",
                endpoint="/chat/emotes",
                method="GET",
                description="Gets the broadcaster's list of custom emotes",
                authorization=[],
                required_params={"broadcaster_id": "string"},
                optional_params={},
                response_fields=["data", "id", "name", "images", "tier", "emote_type", "emote_set_id", "format", "scale", "theme_mode", "template"],
                auto_fill_params={"broadcaster_id": "user_id"}
            ),
            "get_global_emotes": APIEndpoint(
                name="Get Global Emotes",
                endpoint="/chat/emotes/global",
                method="GET",
                description="Gets all global emotes",
                authorization=[],
                required_params={},
                optional_params={},
                response_fields=["data", "id", "name", "images", "format", "scale", "theme_mode", "template"]
            ),
            "get_emote_sets": APIEndpoint(
                name="Get Emote Sets",
                endpoint="/chat/emotes/set",
                method="GET",
                description="Gets emotes for one or more specified emote sets",
                authorization=[],
                required_params={"emote_set_id": "string"},
                optional_params={},
                response_fields=["data", "id", "name", "images", "emote_type", "emote_set_id", "owner_id", "format", "scale", "theme_mode", "template"]
            ),
            "get_channel_chat_badges": APIEndpoint(
                name="Get Channel Chat Badges",
                endpoint="/chat/badges",
                method="GET",
                description="Gets the broadcaster's list of custom chat badges",
                authorization=[],
                required_params={"broadcaster_id": "string"},
                optional_params={},
                response_fields=["data", "set_id", "versions", "id", "image_url_1x", "image_url_2x", "image_url_4x", "title", "description", "click_action", "click_url"],
                auto_fill_params={"broadcaster_id": "user_id"}
            ),
            "get_global_chat_badges": APIEndpoint(
                name="Get Global Chat Badges",
                endpoint="/chat/badges/global",
                method="GET",
                description="Gets Twitch's list of chat badges",
                authorization=[],
                required_params={},
                optional_params={},
                response_fields=["data", "set_id", "versions", "id", "image_url_1x", "image_url_2x", "image_url_4x", "title", "description", "click_action", "click_url"]
            ),
            "get_chat_settings": APIEndpoint(
                name="Get Chat Settings",
                endpoint="/chat/settings",
                method="GET",
                description="Gets the broadcaster's chat settings",
                authorization=["moderator:read:chat_settings"],
                required_params={"broadcaster_id": "string", "moderator_id": "string"},
                optional_params={},
                response_fields=["data", "broadcaster_id", "moderator_id", "slow_mode", "slow_mode_wait_time", "follower_mode", "follower_mode_duration", "subscriber_mode", "emote_mode", "unique_chat_mode", "non_moderator_chat_delay", "non_moderator_chat_delay_duration"]
            ),
            "get_shared_chat_session": APIEndpoint(
                name="Get Shared Chat Session",
                endpoint="/shared_chat/session",
                method="GET",
                description="Retrieves the active shared chat session for a channel",
                authorization=["user:read:chat"],
                required_params={"broadcaster_id": "string"},
                optional_params={},
                response_fields=["data", "session_id", "host_broadcaster_id", "is_active", "participants", "broadcaster_id", "is_connected"]
            ),
            "get_user_emotes": APIEndpoint(
                name="Get User Emotes",
                endpoint="/chat/emotes/user",
                method="GET",
                description="Retrieves emotes available to the user across all channels",
                authorization=["user:read:emotes"],
                required_params={"user_id": "string"},
                optional_params={"broadcaster_id": "string", "after": "string", "first": "integer"},
                response_fields=["data", "id", "name", "images", "tier", "emote_type", "emote_set_id", "format", "scale", "theme_mode", "template", "broadcaster_id", "broadcaster_name", "owner_id", "template"]
            ),
            "update_chat_settings": APIEndpoint(
                name="Update Chat Settings",
                endpoint="/chat/settings",
                method="PATCH",
                description="Updates the broadcaster's chat settings",
                authorization=["moderator:manage:chat_settings"],
                required_params={"broadcaster_id": "string", "moderator_id": "string"},
                optional_params={"slow_mode": "boolean", "slow_mode_wait_time": "integer", "follower_mode": "boolean", "follower_mode_duration": "integer", "subscriber_mode": "boolean", "emote_mode": "boolean", "unique_chat_mode": "boolean", "non_moderator_chat_delay": "boolean", "non_moderator_chat_delay_duration": "integer"},
                response_fields=["data", "broadcaster_id", "moderator_id", "slow_mode", "slow_mode_wait_time", "follower_mode", "follower_mode_duration", "subscriber_mode", "emote_mode", "unique_chat_mode", "non_moderator_chat_delay", "non_moderator_chat_delay_duration"]
            ),
            "send_chat_announcement": APIEndpoint(
                name="Send Chat Announcement",
                endpoint="/chat/announcements",
                method="POST",
                description="Sends an announcement to the broadcaster's chat room",
                authorization=["moderator:manage:announcements"],
                required_params={"broadcaster_id": "string", "moderator_id": "string", "message": "string"},
                optional_params={"color": "string (blue, green, orange, purple, primary)"},
                response_fields=[]
            ),
            "send_shoutout": APIEndpoint(
                name="Send a Shoutout",
                endpoint="/chat/shoutouts",
                method="POST",
                description="Sends a Shoutout to the specified broadcaster",
                authorization=["moderator:manage:shoutouts"],
                required_params={"from_broadcaster_id": "string", "to_broadcaster_id": "string", "moderator_id": "string"},
                optional_params={},
                response_fields=[]
            ),
            "send_chat_message": APIEndpoint(
                name="Send Chat Message",
                endpoint="/chat/messages",
                method="POST",
                description="Sends a message to the broadcaster's chat room",
                authorization=["user:write:chat"],
                required_params={"broadcaster_id": "string", "sender_id": "string", "message": "string"},
                optional_params={"reply_parent_message_id": "string"},
                response_fields=["data", "message_id", "is_sent", "drop_reason", "message", "message_text", "message_type"]
            ),
            "get_user_chat_color": APIEndpoint(
                name="Get User Chat Color",
                endpoint="/chat/color",
                method="GET",
                description="Gets the color used for the user's name in chat",
                authorization=["user:read:chat"],
                required_params={"user_id": "string"},
                optional_params={},
                response_fields=["data", "user_id", "user_login", "user_name", "color"]
            ),
            "update_user_chat_color": APIEndpoint(
                name="Update User Chat Color",
                endpoint="/chat/color",
                method="PUT",
                description="Updates the color used for the user's name in chat",
                authorization=["user:manage:chat_color"],
                required_params={"user_id": "string", "color": "string"},
                optional_params={},
                response_fields=["data", "user_id", "user_login", "user_name", "color"]
            )
        }

    def _get_clips_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Clips-related endpoints."""
        return {
            "create_clip": APIEndpoint(
                name="Create Clip",
                endpoint="/clips",
                method="POST",
                description="Creates a clip from the broadcaster's stream",
                authorization=["clips:edit"],
                required_params={"broadcaster_id": "string"},
                optional_params={"has_delay": "boolean"},
                response_fields=["data", "id", "edit_url"]
            ),
            "get_clips": APIEndpoint(
                name="Get Clips",
                endpoint="/clips",
                method="GET",
                description="Gets one or more video clips",
                authorization=[],
                required_params={},
                optional_params={"broadcaster_id": "string", "game_id": "string", "id": "string", "started_at": "RFC3339 timestamp", "ended_at": "RFC3339 timestamp", "first": "integer", "before": "string", "after": "string"},
                response_fields=["data", "id", "url", "embed_url", "broadcaster_id", "broadcaster_name", "creator_id", "creator_name", "video_id", "game_id", "language", "title", "view_count", "created_at", "thumbnail_url", "duration", "vod_offset", "is_featured", "pagination"]
            )
        }

    def _get_conduits_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Conduits-related endpoints."""
        return {
            "get_conduits": APIEndpoint(
                name="Get Conduits",
                endpoint="/eventsub/conduits",
                method="GET",
                description="Get a list of conduits",
                authorization=["user:read:conduits"],
                required_params={},
                optional_params={},
                response_fields=["data", "id", "shard_count"]
            ),
            "create_conduits": APIEndpoint(
                name="Create Conduits",
                endpoint="/eventsub/conduits",
                method="POST",
                description="Creates a new conduit",
                authorization=["user:write:conduits"],
                required_params={"shard_count": "integer"},
                optional_params={},
                response_fields=["data", "id", "shard_count"]
            ),
            "update_conduits": APIEndpoint(
                name="Update Conduits",
                endpoint="/eventsub/conduits",
                method="PATCH",
                description="Updates a conduit's shard count",
                authorization=["user:write:conduits"],
                required_params={"id": "string", "shard_count": "integer"},
                optional_params={},
                response_fields=["data", "id", "shard_count"]
            ),
            "delete_conduit": APIEndpoint(
                name="Delete Conduit",
                endpoint="/eventsub/conduits",
                method="DELETE",
                description="Deletes a conduit",
                authorization=["user:write:conduits"],
                required_params={"id": "string"},
                optional_params={},
                response_fields=[]
            ),
            "get_conduit_shards": APIEndpoint(
                name="Get Conduit Shards",
                endpoint="/eventsub/conduits/shards",
                method="GET",
                description="Gets a list of conduit shards",
                authorization=["user:read:conduits"],
                required_params={"conduit_id": "string"},
                optional_params={"status": "string", "after": "string"},
                response_fields=["data", "id", "status", "transport", "session_id", "connected_at", "disconnected_at"]
            ),
            "update_conduit_shards": APIEndpoint(
                name="Update Conduit Shards",
                endpoint="/eventsub/conduits/shards",
                method="PATCH",
                description="Updates conduit shard(s)",
                authorization=["user:write:conduits"],
                required_params={"conduit_id": "string", "shards": "array"},
                optional_params={},
                response_fields=["data", "id", "status", "transport", "session_id", "connected_at", "disconnected_at"]
            )
        }

    def _get_drops_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Drops-related endpoints."""
        return {
            "get_content_classification_labels": APIEndpoint(
                name="Get Content Classification Labels",
                endpoint="/content_classification_labels",
                method="GET",
                description="Gets information about Twitch content classification labels",
                authorization=[],
                required_params={},
                optional_params={"locale": "string"},
                response_fields=["data", "id", "description", "name"]
            ),
            "get_drops_entitlements": APIEndpoint(
                name="Get Drops Entitlements",
                endpoint="/entitlements/drops",
                method="GET",
                description="Gets a list of entitlements for a given organization that have been granted to a game, user, or both",
                authorization=["user:read:entitlements"],
                required_params={},
                optional_params={"id": "string", "user_id": "string", "game_id": "string", "fulfillment_status": "string", "after": "string", "first": "integer"},
                response_fields=["data", "id", "benefit_id", "timestamp", "user_id", "game_id", "fulfillment_status", "last_updated", "pagination"]
            ),
            "update_drops_entitlements": APIEndpoint(
                name="Update Drops Entitlements",
                endpoint="/entitlements/drops",
                method="PATCH",
                description="Updates the fulfillment status on a set of Drops entitlements",
                authorization=["user:edit:entitlements"],
                required_params={"entitlement_ids": "array", "fulfillment_status": "string"},
                optional_params={},
                response_fields=["data", "status", "ids"]
            )
        }

    def _get_extensions_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Extensions-related endpoints."""
        return {
            "get_extension_configuration_segment": APIEndpoint(
                name="Get Extension Configuration Segment",
                endpoint="/extensions/configurations",
                method="GET",
                description="Gets the specified extension's configuration segment",
                authorization=[],
                required_params={"broadcaster_id": "string"},
                optional_params={"extension_id": "string", "segment": "string"},
                response_fields=["data", "segment", "record", "broadcaster_id", "extension_id"]
            ),
            "set_extension_configuration_segment": APIEndpoint(
                name="Set Extension Configuration Segment",
                endpoint="/extensions/configurations",
                method="PUT",
                description="Sets the specified extension's configuration segment",
                authorization=[],
                required_params={"broadcaster_id": "string", "extension_id": "string", "segment": "string"},
                optional_params={"content": "string", "version": "string"},
                response_fields=["data", "segment", "record", "broadcaster_id", "extension_id"]
            ),
            "set_extension_required_configuration": APIEndpoint(
                name="Set Extension Required Configuration",
                endpoint="/extensions/required_configuration",
                method="PUT",
                description="Set which configuration segment to use on a specified channel",
                authorization=[],
                required_params={"broadcaster_id": "string", "extension_id": "string", "extension_version": "string"},
                optional_params={"configuration_version": "string"},
                response_fields=[]
            ),
            "send_extension_pubsub_message": APIEndpoint(
                name="Send Extension PubSub Message",
                endpoint="/extensions/pubsub",
                method="POST",
                description="Sends a message to one or more viewers of an extension",
                authorization=[],
                required_params={"target": "array", "broadcaster_id": "string", "is_global_broadcast": "boolean", "message": "string"},
                optional_params={},
                response_fields=[]
            ),
            "get_extension_live_channels": APIEndpoint(
                name="Get Extension Live Channels",
                endpoint="/extensions/live",
                method="GET",
                description="Gets information about live channels that have installed or activated a specific extension",
                authorization=[],
                required_params={"extension_id": "string"},
                optional_params={"first": "integer", "after": "string"},
                response_fields=["data", "broadcaster_id", "broadcaster_name", "game_name", "game_id", "title", "pagination"]
            ),
            "get_extension_secrets": APIEndpoint(
                name="Get Extension Secrets",
                endpoint="/extensions/jwt/secrets",
                method="GET",
                description="Gets an extension's list of secrets",
                authorization=[],
                required_params={"extension_id": "string"},
                optional_params={},
                response_fields=["data", "format_version", "secrets", "content", "active_at", "expires_at"]
            ),
            "create_extension_secret": APIEndpoint(
                name="Create Extension Secret",
                endpoint="/extensions/jwt/secrets",
                method="POST",
                description="Creates a JWT secret for an extension",
                authorization=[],
                required_params={"extension_id": "string", "delay": "integer"},
                optional_params={},
                response_fields=["data", "format_version", "secrets", "content", "active_at", "expires_at"]
            ),
            "send_extension_chat_message": APIEndpoint(
                name="Send Extension Chat Message",
                endpoint="/extensions/chat",
                method="POST",
                description="Sends a message to the specified broadcaster's chat room",
                authorization=[],
                required_params={"broadcaster_id": "string", "text": "string", "extension_id": "string", "extension_version": "string"},
                optional_params={},
                response_fields=[]
            ),
            "get_extensions": APIEndpoint(
                name="Get Extensions",
                endpoint="/extensions",
                method="GET",
                description="Gets information about an extension",
                authorization=[],
                required_params={},
                optional_params={"extension_id": "string", "extension_version": "string"},
                response_fields=["data", "author_name", "bits_enabled", "can_install", "configuration_location", "description", "eula_tos_url", "has_chat_support", "icon_url", "icon_urls", "id", "name", "privacy_policy_url", "request_identity_link", "screenshot_urls", "state", "subscriptions_support_level", "summary", "support_email", "version", "viewer_summary", "views", "allowlisted_config_urls", "allowlisted_panel_urls"]
            ),
            "get_released_extensions": APIEndpoint(
                name="Get Released Extensions",
                endpoint="/extensions/released",
                method="GET",
                description="Gets a list of all extensions (both active and inactive) that the broadcaster has installed",
                authorization=[],
                required_params={"broadcaster_id": "string"},
                optional_params={"extension_id": "string", "first": "integer", "after": "string"},
                response_fields=["data", "author_name", "bits_enabled", "can_install", "configuration_location", "description", "eula_tos_url", "has_chat_support", "icon_url", "icon_urls", "id", "name", "privacy_policy_url", "request_identity_link", "screenshot_urls", "state", "subscriptions_support_level", "summary", "support_email", "version", "viewer_summary", "views", "allowlisted_config_urls", "allowlisted_panel_urls", "pagination"]
            ),
            "get_extension_bits_products": APIEndpoint(
                name="Get Extension Bits Products",
                endpoint="/extensions/bits/products",
                method="GET",
                description="Gets a list of Bits products that belongs to the extension",
                authorization=[],
                required_params={"extension_id": "string"},
                optional_params={"should_include_all": "boolean"},
                response_fields=["data", "sku", "cost", "in_development", "display_name", "expiration", "is_broadcast", "broadcast"]
            ),
            "update_extension_bits_product": APIEndpoint(
                name="Update Extension Bits Product",
                endpoint="/extensions/bits/products",
                method="PUT",
                description="Updates a Bits product that belongs to the extension",
                authorization=[],
                required_params={"extension_id": "string", "sku": "string"},
                optional_params={"cost": "object", "display_name": "string", "in_development": "boolean", "expiration": "string", "is_broadcast": "boolean"},
                response_fields=["data", "sku", "cost", "in_development", "display_name", "expiration", "is_broadcast", "broadcast"]
            )
        }

    def _get_games_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Games-related endpoints."""
        return {
            "get_top_games": APIEndpoint(
                name="Get Top Games",
                endpoint="/games/top",
                method="GET",
                description="Gets information about the top games and categories",
                authorization=[],
                required_params={},
                optional_params={"first": "integer", "before": "string", "after": "string"},
                response_fields=["data", "id", "name", "box_art_url", "igdb_id", "pagination"]
            ),
            "get_games": APIEndpoint(
                name="Get Games",
                endpoint="/games",
                method="GET",
                description="Gets information about specified categories or games",
                authorization=[],
                required_params={},
                optional_params={"id": "string", "name": "string", "igdb_id": "string"},
                response_fields=["data", "id", "name", "box_art_url", "igdb_id"]
            )
        }

    def _get_goals_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Goals-related endpoints."""
        return {
            "get_creator_goals": APIEndpoint(
                name="Get Creator Goals",
                endpoint="/goals",
                method="GET",
                description="Gets the broadcaster's list of active goals",
                authorization=["channel:read:goals"],
                required_params={"broadcaster_id": "string"},
                optional_params={},
                response_fields=["data", "id", "broadcaster_id", "broadcaster_name", "broadcaster_login", "type", "description", "current_amount", "target_amount", "created_at"]
            )
        }

    def _get_guest_star_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Guest Star-related endpoints."""
        return {
            "get_guest_star_session": APIEndpoint(
                name="Get Guest Star Session",
                endpoint="/guest_star/session",
                method="GET",
                description="Gets information about the guest star session",
                authorization=["moderator:read:guest_star", "moderator:manage:guest_star"],
                required_params={"broadcaster_id": "string", "moderator_id": "string"},
                optional_params={},
                response_fields=["data", "id", "is_live", "host", "speakers", "guests", "slot", "guest_id", "guest_name", "guest_user_id", "guest_login", "is_live", "volume", "assigned_at", "audio_enabled", "mute_on_join"]
            ),
            "create_guest_star_session": APIEndpoint(
                name="Create Guest Star Session",
                endpoint="/guest_star/session",
                method="POST",
                description="Creates a guest star session",
                authorization=["moderator:manage:guest_star"],
                required_params={"broadcaster_id": "string", "moderator_id": "string"},
                optional_params={},
                response_fields=["data", "id", "is_live", "host", "speakers", "guests", "slot", "guest_id", "guest_name", "guest_user_id", "guest_login", "is_live", "volume", "assigned_at", "audio_enabled", "mute_on_join"]
            ),
            "end_guest_star_session": APIEndpoint(
                name="End Guest Star Session",
                endpoint="/guest_star/session",
                method="DELETE",
                description="Ends a guest star session",
                authorization=["moderator:manage:guest_star"],
                required_params={"broadcaster_id": "string", "moderator_id": "string", "session_id": "string"},
                optional_params={},
                response_fields=[]
            ),
            "get_guest_star_invites": APIEndpoint(
                name="Get Guest Star Invites",
                endpoint="/guest_star/invites",
                method="GET",
                description="Gets a list of guest star invites",
                authorization=["moderator:read:guest_star"],
                required_params={"broadcaster_id": "string", "moderator_id": "string"},
                optional_params={"session_id": "string"},
                response_fields=["data", "id", "invited_at", "expires_at", "is_expired", "user_id", "user_login", "user_name", "status"]
            ),
            "send_guest_star_invite": APIEndpoint(
                name="Send Guest Star Invite",
                endpoint="/guest_star/invites",
                method="POST",
                description="Sends an invite to a guest star",
                authorization=["moderator:manage:guest_star"],
                required_params={"broadcaster_id": "string", "moderator_id": "string", "guest_id": "string", "session_id": "string"},
                optional_params={},
                response_fields=["data", "id", "invited_at", "expires_at", "is_expired", "user_id", "user_login", "user_name", "status"]
            ),
            "delete_guest_star_invite": APIEndpoint(
                name="Delete Guest Star Invite",
                endpoint="/guest_star/invites",
                method="DELETE",
                description="Deletes a guest star invite",
                authorization=["moderator:manage:guest_star"],
                required_params={"broadcaster_id": "string", "moderator_id": "string", "session_id": "string", "guest_id": "string"},
                optional_params={},
                response_fields=[]
            ),
            "assign_guest_star_slot": APIEndpoint(
                name="Assign Guest Star Slot",
                endpoint="/guest_star/slot",
                method="POST",
                description="Assigns a slot to a guest star",
                authorization=["moderator:manage:guest_star"],
                required_params={"broadcaster_id": "string", "moderator_id": "string", "session_id": "string", "guest_id": "string", "slot_id": "string"},
                optional_params={},
                response_fields=["data", "slot", "guest_id", "guest_name", "guest_user_id", "guest_login", "is_live", "volume", "assigned_at", "audio_enabled", "mute_on_join"]
            ),
            "update_guest_star_slot": APIEndpoint(
                name="Update Guest Star Slot",
                endpoint="/guest_star/slot",
                method="PATCH",
                description="Updates a guest star slot",
                authorization=["moderator:manage:guest_star"],
                required_params={"broadcaster_id": "string", "moderator_id": "string", "session_id": "string", "source_slot_id": "string"},
                optional_params={"is_audio_enabled": "boolean", "is_video_enabled": "boolean", "is_muted": "boolean", "volume": "integer"},
                response_fields=["data", "slot", "guest_id", "guest_name", "guest_user_id", "guest_login", "is_live", "volume", "assigned_at", "audio_enabled", "mute_on_join"]
            ),
            "delete_guest_star_slot": APIEndpoint(
                name="Delete Guest Star Slot",
                endpoint="/guest_star/slot",
                method="DELETE",
                description="Deletes a guest star slot",
                authorization=["moderator:manage:guest_star"],
                required_params={"broadcaster_id": "string", "moderator_id": "string", "session_id": "string", "guest_id": "string", "slot_id": "string"},
                optional_params={},
                response_fields=[]
            ),
            "update_guest_star_slot_settings": APIEndpoint(
                name="Update Guest Star Slot Settings",
                endpoint="/guest_star/slot_settings",
                method="PATCH",
                description="Updates guest star slot settings",
                authorization=["moderator:manage:guest_star"],
                required_params={"broadcaster_id": "string", "moderator_id": "string", "session_id": "string", "slot_id": "string"},
                optional_params={"is_audio_enabled": "boolean", "is_video_enabled": "boolean", "is_muted": "boolean", "volume": "integer"},
                response_fields=["data", "slot", "guest_id", "guest_name", "guest_user_id", "guest_login", "is_live", "volume", "assigned_at", "audio_enabled", "mute_on_join"]
            )
        }

    def _get_hype_train_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Hype Train-related endpoints."""
        return {
            "get_hype_train_events": APIEndpoint(
                name="Get Hype Train Events",
                endpoint="/hypetrain/events",
                method="GET",
                description="Gets the broadcaster's hype train events",
                authorization=["channel:read:hype_train"],
                required_params={"broadcaster_id": "string"},
                optional_params={"first": "integer", "cursor": "string"},
                response_fields=["data", "id", "event_type", "event_timestamp", "version", "event_data", "broadcaster_id", "expires_at", "goal", "id", "level", "started_at", "top_contributions", "total", "type", "user", "user_id", "user_login", "user_name", "pagination"]
            ),
            "get_hype_train_status": APIEndpoint(
                name="Get Hype Train Status",
                endpoint="/hypetrain/status",
                method="GET",
                description="Gets information about the broadcaster's current hype train",
                authorization=["channel:read:hype_train"],
                required_params={"broadcaster_id": "string"},
                optional_params={},
                response_fields=["data", "id", "broadcaster_id", "level", "total", "progress", "goal", "expires_at", "started_at", "top_contributions", "last_contribution", "cooldown_end_time", "user", "user_id", "user_login", "user_name", "type", "total"]
            )
        }

    def _get_moderation_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Moderation-related endpoints."""
        return {
            "check_automod_status": APIEndpoint(
                name="Check AutoMod Status",
                endpoint="/moderation/enforcements/status",
                method="POST",
                description="Determines whether a string message meets the channel's AutoMod requirements",
                authorization=["moderation:read"],
                required_params={"broadcaster_id": "string", "msg_id": "string", "msg_text": "string"},
                optional_params={"user_id": "string"},
                response_fields=["data", "msg_id", "is_permitted"]
            ),
            "manage_held_automod_messages": APIEndpoint(
                name="Manage Held AutoMod Messages",
                endpoint="/moderation/automod/message",
                method="POST",
                description="Allows or denies a message that AutoMod flagged for review",
                authorization=["moderator:manage:automod"],
                required_params={"user_id": "string", "msg_id": "string", "action": "string (ALLOW, DENY)"},
                optional_params={},
                response_fields=[]
            ),
            "get_automod_settings": APIEndpoint(
                name="Get AutoMod Settings",
                endpoint="/moderation/automod/settings",
                method="GET",
                description="Gets the broadcaster's AutoMod settings",
                authorization=["moderator:read:automod_settings"],
                required_params={"broadcaster_id": "string", "moderator_id": "string"},
                optional_params={},
                response_fields=["data", "broadcaster_id", "moderator_id", "overall_level", "disability", "aggression", "sexuality_sex_or_gender", "misogyny", "bullying", "swearing", "race_ethnicity_or_religion", "sex_based_terms"]
            ),
            "update_automod_settings": APIEndpoint(
                name="Update AutoMod Settings",
                endpoint="/moderation/automod/settings",
                method="PUT",
                description="Updates the broadcaster's AutoMod settings",
                authorization=["moderator:manage:automod_settings"],
                required_params={"broadcaster_id": "string", "moderator_id": "string"},
                optional_params={"overall_level": "integer", "disability": "integer", "aggression": "integer", "sexuality_sex_or_gender": "integer", "misogyny": "integer", "bullying": "integer", "swearing": "integer", "race_ethnicity_or_religion": "integer", "sex_based_terms": "integer"},
                response_fields=["data", "broadcaster_id", "moderator_id", "overall_level", "disability", "aggression", "sexuality_sex_or_gender", "misogyny", "bullying", "swearing", "race_ethnicity_or_religion", "sex_based_terms"]
            ),
            "get_banned_users": APIEndpoint(
                name="Get Banned Users",
                endpoint="/moderation/banned",
                method="GET",
                description="Gets a list of users who are banned from the specified broadcaster's channel",
                authorization=["moderation:read"],
                required_params={"broadcaster_id": "string"},
                optional_params={"user_id": "string", "before": "string", "after": "string", "first": "integer"},
                response_fields=["data", "user_id", "user_login", "user_name", "expires_at", "created_at", "reason", "moderator_id", "moderator_login", "moderator_name", "pagination"]
            ),
            "ban_user": APIEndpoint(
                name="Ban User",
                endpoint="/moderation/bans",
                method="POST",
                description="Bans a user from participating in the specified broadcaster's channel",
                authorization=["moderator:manage:banned_users"],
                required_params={"broadcaster_id": "string", "moderator_id": "string", "user_id": "string"},
                optional_params={"duration": "integer", "reason": "string"},
                response_fields=["data", "broadcaster_id", "moderator_id", "user_id", "created_at", "end_time"]
            ),
            "unban_user": APIEndpoint(
                name="Unban User",
                endpoint="/moderation/bans",
                method="DELETE",
                description="Removes the ban or timeout on a user",
                authorization=["moderator:manage:banned_users"],
                required_params={"broadcaster_id": "string", "moderator_id": "string", "user_id": "string"},
                optional_params={},
                response_fields=[]
            ),
            "get_unban_requests": APIEndpoint(
                name="Get Unban Requests",
                endpoint="/moderation/unban_requests",
                method="GET",
                description="Gets a list of unban requests for the broadcaster's channel",
                authorization=["moderator:read:unban_requests"],
                required_params={"broadcaster_id": "string", "moderator_id": "string"},
                optional_params={"status": "string", "user_id": "string", "sort": "string", "after": "string", "first": "integer"},
                response_fields=["data", "id", "broadcaster_id", "broadcaster_login", "broadcaster_name", "user_id", "user_login", "user_name", "text", "status", "created_at", "resolved_at", "resolution_text", "moderator_id", "moderator_login", "moderator_name", "pagination"]
            ),
            "resolve_unban_request": APIEndpoint(
                name="Resolve Unban Request",
                endpoint="/moderation/unban_requests",
                method="PATCH",
                description="Resolves an unban request by approving or denying it",
                authorization=["moderator:manage:unban_requests"],
                required_params={"unban_request_id": "string", "status": "string (APPROVED, DENIED)", "broadcaster_id": "string", "moderator_id": "string"},
                optional_params={"resolution_text": "string"},
                response_fields=["data", "id", "broadcaster_id", "broadcaster_login", "broadcaster_name", "user_id", "user_login", "user_name", "text", "status", "created_at", "resolved_at", "resolution_text", "moderator_id", "moderator_login", "moderator_name"]
            ),
            "get_blocked_terms": APIEndpoint(
                name="Get Blocked Terms",
                endpoint="/moderation/blocked_terms",
                method="GET",
                description="Gets the broadcaster's list of blocked terms",
                authorization=["moderator:read:blocked_terms"],
                required_params={"broadcaster_id": "string", "moderator_id": "string"},
                optional_params={"first": "integer", "after": "string"},
                response_fields=["data", "id", "text", "created_at", "updated_at", "expires_at", "pagination"]
            ),
            "add_blocked_term": APIEndpoint(
                name="Add Blocked Term",
                endpoint="/moderation/blocked_terms",
                method="POST",
                description="Adds a word or phrase to the broadcaster's list of blocked terms",
                authorization=["moderator:manage:blocked_terms"],
                required_params={"broadcaster_id": "string", "moderator_id": "string", "text": "string"},
                optional_params={},
                response_fields=["data", "id", "text", "created_at", "updated_at", "expires_at"]
            ),
            "remove_blocked_term": APIEndpoint(
                name="Remove Blocked Term",
                endpoint="/moderation/blocked_terms",
                method="DELETE",
                description="Removes a word or phrase from the broadcaster's list of blocked terms",
                authorization=["moderator:manage:blocked_terms"],
                required_params={"broadcaster_id": "string", "moderator_id": "string", "id": "string"},
                optional_params={},
                response_fields=[]
            ),
            "delete_chat_messages": APIEndpoint(
                name="Delete Chat Messages",
                endpoint="/moderation/chat",
                method="DELETE",
                description="Removes a single chat message or all chat messages from the broadcaster's chat room",
                authorization=["moderator:manage:chat_messages"],
                required_params={"broadcaster_id": "string", "moderator_id": "string"},
                optional_params={"message_id": "string", "user_id": "string"},
                response_fields=[]
            ),
            "get_moderated_channels": APIEndpoint(
                name="Get Moderated Channels",
                endpoint="/moderation/channels",
                method="GET",
                description="Gets a list of channels that the specified user has moderator privileges in",
                authorization=["user:read:moderated_channels"],
                required_params={"user_id": "string"},
                optional_params={"after": "string", "first": "integer"},
                response_fields=["data", "broadcaster_id", "broadcaster_login", "broadcaster_name", "pagination"]
            ),
            "get_moderators": APIEndpoint(
                name="Get Moderators",
                endpoint="/moderation/moderators",
                method="GET",
                description="Gets a list of moderators in the specified broadcaster's channel",
                authorization=["moderation:read"],
                required_params={"broadcaster_id": "string"},
                optional_params={"user_id": "string", "first": "integer", "after": "string"},
                response_fields=["data", "user_id", "user_login", "user_name", "pagination"]
            ),
            "add_channel_moderator": APIEndpoint(
                name="Add Channel Moderator",
                endpoint="/moderation/moderators",
                method="POST",
                description="Adds a moderator to the broadcaster's channel",
                authorization=["channel:manage:moderators"],
                required_params={"broadcaster_id": "string", "user_id": "string"},
                optional_params={},
                response_fields=["data", "broadcaster_id", "broadcaster_login", "broadcaster_name", "user_id", "user_login", "user_name"]
            ),
            "remove_channel_moderator": APIEndpoint(
                name="Remove Channel Moderator",
                endpoint="/moderation/moderators",
                method="DELETE",
                description="Removes a moderator from the broadcaster's channel",
                authorization=["channel:manage:moderators"],
                required_params={"broadcaster_id": "string", "user_id": "string"},
                optional_params={},
                response_fields=[]
            ),
            "get_vips": APIEndpoint(
                name="Get VIPs",
                endpoint="/channels/vips",
                method="GET",
                description="Gets a list of the channel's VIPs",
                authorization=["channel:read:vips"],
                required_params={"broadcaster_id": "string"},
                optional_params={"user_id": "string", "first": "integer", "after": "string"},
                response_fields=["data", "user_id", "user_login", "user_name", "pagination"]
            ),
            "add_channel_vip": APIEndpoint(
                name="Add Channel VIP",
                endpoint="/channels/vips",
                method="POST",
                description="Adds the specified user as a VIP in the broadcaster's channel",
                authorization=["channel:manage:vips"],
                required_params={"broadcaster_id": "string", "user_id": "string"},
                optional_params={},
                response_fields=["data", "broadcaster_id", "broadcaster_login", "broadcaster_name", "user_id", "user_login", "user_name"]
            ),
            "remove_channel_vip": APIEndpoint(
                name="Remove Channel VIP",
                endpoint="/channels/vips",
                method="DELETE",
                description="Removes the specified user as a VIP in the broadcaster's channel",
                authorization=["channel:manage:vips"],
                required_params={"broadcaster_id": "string", "user_id": "string"},
                optional_params={},
                response_fields=[]
            ),
            "update_shield_mode_status": APIEndpoint(
                name="Update Shield Mode Status",
                endpoint="/moderation/shield_mode",
                method="PUT",
                description="Activates or deactivates the broadcaster's Shield Mode",
                authorization=["moderator:manage:shield_mode"],
                required_params={"broadcaster_id": "string", "moderator_id": "string", "is_active": "boolean"},
                optional_params={},
                response_fields=["data", "is_active", "moderator_id", "moderator_login", "moderator_name", "last_activated_at"]
            ),
            "get_shield_mode_status": APIEndpoint(
                name="Get Shield Mode Status",
                endpoint="/moderation/shield_mode",
                method="GET",
                description="Gets the broadcaster's Shield Mode activation status",
                authorization=["moderator:read:shield_mode"],
                required_params={"broadcaster_id": "string", "moderator_id": "string"},
                optional_params={},
                response_fields=["data", "is_active", "moderator_id", "moderator_login", "moderator_name", "last_activated_at"]
            ),
            "warn_chat_user": APIEndpoint(
                name="Warn Chat User",
                endpoint="/moderation/warnings",
                method="POST",
                description="Warns a user in the specified broadcaster's channel",
                authorization=["moderator:manage:warnings"],
                required_params={"broadcaster_id": "string", "moderator_id": "string", "user_id": "string", "reason": "string"},
                optional_params={},
                response_fields=["data", "broadcaster_id", "moderator_id", "user_id", "expires_at", "created_at", "reason", "moderator_id", "moderator_login", "moderator_name"]
            )
        }

    def _get_polls_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Polls-related endpoints."""
        return {
            "get_polls": APIEndpoint(
                name="Get Polls",
                endpoint="/polls",
                method="GET",
                description="Gets a list of polls for the specified broadcaster",
                authorization=["channel:read:polls"],
                required_params={"broadcaster_id": "string"},
                optional_params={"id": "string", "first": "integer", "after": "string"},
                response_fields=["data", "id", "broadcaster_id", "broadcaster_login", "broadcaster_name", "title", "choices", "bits_voting_enabled", "bits_per_vote", "channel_points_voting_enabled", "channel_points_per_vote", "status", "duration", "started_at", "ended_at", "pagination"]
            ),
            "create_poll": APIEndpoint(
                name="Create Poll",
                endpoint="/polls",
                method="POST",
                description="Creates a poll for the specified broadcaster",
                authorization=["channel:manage:polls"],
                required_params={"broadcaster_id": "string", "title": "string", "choices": "array", "duration": "integer"},
                optional_params={"bits_voting_enabled": "boolean", "bits_per_vote": "integer", "channel_points_voting_enabled": "boolean", "channel_points_per_vote": "integer"},
                response_fields=["data", "id", "broadcaster_id", "broadcaster_login", "broadcaster_name", "title", "choices", "bits_voting_enabled", "bits_per_vote", "channel_points_voting_enabled", "channel_points_per_vote", "status", "duration", "started_at", "ended_at"]
            ),
            "end_poll": APIEndpoint(
                name="End Poll",
                endpoint="/polls",
                method="PATCH",
                description="Ends an active poll",
                authorization=["channel:manage:polls"],
                required_params={"broadcaster_id": "string", "id": "string", "status": "string (TERMINATED, ARCHIVED)"},
                optional_params={},
                response_fields=["data", "id", "broadcaster_id", "broadcaster_login", "broadcaster_name", "title", "choices", "bits_voting_enabled", "bits_per_vote", "channel_points_voting_enabled", "channel_points_per_vote", "status", "duration", "started_at", "ended_at"]
            )
        }

    def _get_predictions_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Predictions-related endpoints."""
        return {
            "get_predictions": APIEndpoint(
                name="Get Predictions",
                endpoint="/predictions",
                method="GET",
                description="Gets a list of Channel Points Predictions for the specified broadcaster",
                authorization=["channel:read:predictions"],
                required_params={"broadcaster_id": "string"},
                optional_params={"id": "string", "first": "integer", "after": "string"},
                response_fields=["data", "id", "broadcaster_id", "broadcaster_login", "broadcaster_name", "title", "winning_outcome_id", "outcomes", "prediction_window", "status", "created_at", "ended_at", "locked_at", "pagination"]
            ),
            "create_prediction": APIEndpoint(
                name="Create Prediction",
                endpoint="/predictions",
                method="POST",
                description="Creates a Channel Points Prediction for the specified broadcaster",
                authorization=["channel:manage:predictions"],
                required_params={"broadcaster_id": "string", "title": "string", "outcomes": "array", "prediction_window": "integer"},
                optional_params={},
                response_fields=["data", "id", "broadcaster_id", "broadcaster_login", "broadcaster_name", "title", "winning_outcome_id", "outcomes", "prediction_window", "status", "created_at", "ended_at", "locked_at"]
            ),
            "end_prediction": APIEndpoint(
                name="End Prediction",
                endpoint="/predictions",
                method="PATCH",
                description="Ends an active Channel Points Prediction",
                authorization=["channel:manage:predictions"],
                required_params={"broadcaster_id": "string", "id": "string", "status": "string (RESOLVED, CANCELED, LOCKED)"},
                optional_params={"winning_outcome_id": "string"},
                response_fields=["data", "id", "broadcaster_id", "broadcaster_login", "broadcaster_name", "title", "winning_outcome_id", "outcomes", "prediction_window", "status", "created_at", "ended_at", "locked_at"]
            )
        }

    def _get_raid_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Raid-related endpoints."""
        return {
            "start_raid": APIEndpoint(
                name="Start a Raid",
                endpoint="/raids",
                method="POST",
                description="Raids another channel by sending the broadcaster's viewers to the targeted channel",
                authorization=["channel:manage:raids"],
                required_params={"from_broadcaster_id": "string", "to_broadcaster_id": "string"},
                optional_params={},
                response_fields=["data", "created_at", "is_mature"]
            ),
            "cancel_raid": APIEndpoint(
                name="Cancel a Raid",
                endpoint="/raids",
                method="DELETE",
                description="Cancels the current raid",
                authorization=["channel:manage:raids"],
                required_params={"broadcaster_id": "string"},
                optional_params={},
                response_fields=[]
            )
        }

    def _get_schedule_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Schedule-related endpoints."""
        return {
            "get_channel_stream_schedule": APIEndpoint(
                name="Get Channel Stream Schedule",
                endpoint="/schedule",
                method="GET",
                description="Gets the broadcaster's streaming schedule",
                authorization=["user:read:schedule"],
                required_params={"broadcaster_id": "string"},
                optional_params={"id": "string", "start_time": "RFC3339 timestamp", "utc_offset": "string", "first": "integer", "after": "string"},
                response_fields=["data", "segments", "id", "start_time", "end_time", "title", "canceled_until", "category", "is_recurring", "broadcaster_id", "broadcaster_name", "broadcaster_login", "vacation", "start_time", "end_time", "pagination"]
            ),
            "get_channel_icalendar": APIEndpoint(
                name="Get Channel iCalendar",
                endpoint="/schedule/icalendar",
                method="GET",
                description="Gets the broadcaster's streaming schedule as an iCalendar",
                authorization=["user:read:schedule"],
                required_params={"broadcaster_id": "string"},
                optional_params={},
                response_fields=[]
            ),
            "update_channel_stream_schedule": APIEndpoint(
                name="Update Channel Stream Schedule",
                endpoint="/schedule/settings",
                method="PATCH",
                description="Updates the broadcaster's schedule settings",
                authorization=["channel:manage:schedule"],
                required_params={"broadcaster_id": "string"},
                optional_params={"is_vacation_enabled": "boolean", "vacation_start_time": "RFC3339 timestamp", "vacation_end_time": "RFC3339 timestamp", "timezone": "string"},
                response_fields=["data", "broadcaster_id", "broadcaster_name", "broadcaster_login", "vacation", "start_time", "end_time"]
            ),
            "create_channel_stream_schedule_segment": APIEndpoint(
                name="Create Channel Stream Schedule Segment",
                endpoint="/schedule/segment",
                method="POST",
                description="Creates a single scheduled broadcast or recurring scheduled broadcasts for the broadcaster's channel",
                authorization=["channel:manage:schedule"],
                required_params={"broadcaster_id": "string", "start_time": "RFC3339 timestamp", "timezone": "string", "is_recurring": "boolean"},
                optional_params={"end_time": "RFC3339 timestamp", "category_id": "string", "title": "string"},
                response_fields=["data", "segments", "id", "start_time", "end_time", "title", "canceled_until", "category", "is_recurring"]
            ),
            "update_channel_stream_schedule_segment": APIEndpoint(
                name="Update Channel Stream Schedule Segment",
                endpoint="/schedule/segment",
                method="PATCH",
                description="Updates a scheduled broadcast segment",
                authorization=["channel:manage:schedule"],
                required_params={"broadcaster_id": "string", "id": "string"},
                optional_params={"start_time": "RFC3339 timestamp", "end_time": "RFC3339 timestamp", "category_id": "string", "title": "string", "is_canceled": "boolean", "timezone": "string"},
                response_fields=["data", "segments", "id", "start_time", "end_time", "title", "canceled_until", "category", "is_recurring"]
            ),
            "delete_channel_stream_schedule_segment": APIEndpoint(
                name="Delete Channel Stream Schedule Segment",
                endpoint="/schedule/segment",
                method="DELETE",
                description="Deletes a scheduled broadcast segment",
                authorization=["channel:manage:schedule"],
                required_params={"broadcaster_id": "string", "id": "string"},
                optional_params={},
                response_fields=[]
            )
        }

    def _get_search_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Search-related endpoints."""
        return {
            "search_categories": APIEndpoint(
                name="Search Categories",
                endpoint="/search/categories",
                method="GET",
                description="Returns a list of games or categories that match the query",
                authorization=[],
                required_params={"query": "string"},
                optional_params={"first": "integer", "after": "string"},
                response_fields=["data", "id", "name", "box_art_url", "pagination"]
            ),
            "search_channels": APIEndpoint(
                name="Search Channels",
                endpoint="/search/channels",
                method="GET",
                description="Returns a list of channels that match the query",
                authorization=[],
                required_params={"query": "string"},
                optional_params={"first": "integer", "after": "string", "live_only": "boolean"},
                response_fields=["data", "broadcaster_language", "broadcaster_login", "display_name", "game_id", "game_name", "id", "is_live", "tag_ids", "tags", "thumbnail_url", "title", "started_at", "pagination"]
            )
        }

    def _get_streams_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Streams-related endpoints."""
        return {
            "get_stream_key": APIEndpoint(
                name="Get Stream Key",
                endpoint="/streams/key",
                method="GET",
                description="Gets the channel stream key for the specified user",
                authorization=["channel:read:stream_key"],
                required_params={"broadcaster_id": "string"},
                optional_params={},
                response_fields=["data", "stream_key"]
            ),
            "get_streams": APIEndpoint(
                name="Get Streams",
                endpoint="/streams",
                method="GET",
                description="Gets information about active streams",
                authorization=[],
                required_params={},
                optional_params={"game_id": "string", "language": "string", "type": "string (all, live)", "first": "integer", "before": "string", "after": "string"},
                response_fields=["data", "id", "user_id", "user_login", "user_name", "game_id", "game_name", "type", "title", "tags", "viewer_count", "started_at", "language", "thumbnail_url", "tag_ids", "is_mature", "pagination"]
            ),
            "get_followed_streams": APIEndpoint(
                name="Get Followed Streams",
                endpoint="/streams/followed",
                method="GET",
                description="Gets information about active streams from channels the user follows",
                authorization=["user:read:follows"],
                required_params={"user_id": "string"},
                optional_params={"first": "integer", "after": "string"},
                response_fields=["data", "id", "user_id", "user_login", "user_name", "game_id", "game_name", "type", "title", "tags", "viewer_count", "started_at", "language", "thumbnail_url", "tag_ids", "is_mature", "pagination"]
            ),
            "create_stream_marker": APIEndpoint(
                name="Create Stream Marker",
                endpoint="/streams/markers",
                method="POST",
                description="Creates a marker in the stream of a user specified by user ID",
                authorization=["channel:manage:broadcast"],
                required_params={"user_id": "string"},
                optional_params={"description": "string"},
                response_fields=["data", "id", "created_at", "description", "position_seconds", "URL"]
            ),
            "get_stream_markers": APIEndpoint(
                name="Get Stream Markers",
                endpoint="/streams/markers",
                method="GET",
                description="Gets a list of markers from the stream of a user",
                authorization=["user:read:broadcast"],
                required_params={"user_id": "string"},
                optional_params={"video_id": "string", "first": "integer", "before": "string", "after": "string"},
                response_fields=["data", "user_id", "user_name", "user_login", "videos", "id", "stream_id", "created_at", "description", "position_seconds", "URL", "pagination"]
            )
        }

    def _get_subscriptions_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Subscriptions-related endpoints."""
        return {
            "get_broadcaster_subscriptions": APIEndpoint(
                name="Get Broadcaster Subscriptions",
                endpoint="/subscriptions",
                method="GET",
                description="Gets a list of users who have subscribed to the specified broadcaster",
                authorization=["channel:read:subscriptions"],
                required_params={"broadcaster_id": "string"},
                optional_params={"user_id": "string", "first": "integer", "before": "string", "after": "string"},
                response_fields=["data", "broadcaster_id", "broadcaster_login", "broadcaster_name", "gifter_id", "gifter_login", "gifter_name", "is_gift", "tier", "plan_name", "user_id", "user_login", "user_name", "pagination"]
            ),
            "check_user_subscription": APIEndpoint(
                name="Check User Subscription",
                endpoint="/subscriptions/user",
                method="GET",
                description="Checks if a user is subscribed to the specified broadcaster",
                authorization=["user:read:subscriptions"],
                required_params={"broadcaster_id": "string", "user_id": "string"},
                optional_params={},
                response_fields=["data", "broadcaster_id", "broadcaster_login", "broadcaster_name", "is_gift", "tier", "plan_name", "user_id", "user_login", "user_name"]
            )
        }

    def _get_tags_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Tags-related endpoints."""
        return {
            "get_stream_tags": APIEndpoint(
                name="Get Stream Tags",
                endpoint="/streams/tags",
                method="GET",
                description="Gets the list of tags for a specified stream (channel)",
                authorization=[],
                required_params={"broadcaster_id": "string"},
                optional_params={},
                response_fields=["data", "tag_id", "is_auto", "localization_names", "localization_descriptions"]
            ),
            "get_all_stream_tags": APIEndpoint(
                name="Get All Stream Tags",
                endpoint="/tags/streams",
                method="GET",
                description="Gets the list of all stream tags",
                authorization=[],
                required_params={},
                optional_params={"tag_id": "string", "first": "integer", "after": "string"},
                response_fields=["data", "tag_id", "is_auto", "localization_names", "localization_descriptions", "pagination"]
            )
        }

    def _get_teams_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Teams-related endpoints."""
        return {
            "get_channel_teams": APIEndpoint(
                name="Get Channel Teams",
                endpoint="/teams/channel",
                method="GET",
                description="Retrieves a list of Twitch Teams of which the specified channel is a member",
                authorization=[],
                required_params={"broadcaster_id": "string"},
                optional_params={},
                response_fields=["data", "broadcaster_id", "broadcaster_login", "broadcaster_name", "background_image_url", "banner", "created_at", "updated_at", "info", "thumbnail_url", "team_name", "team_display_name", "id"]
            ),
            "get_teams": APIEndpoint(
                name="Get Teams",
                endpoint="/teams",
                method="GET",
                description="Gets information about the specified Twitch Team",
                authorization=[],
                required_params={"name": "string"},
                optional_params={"id": "string"},
                response_fields=["data", "users", "background_image_url", "banner", "created_at", "updated_at", "info", "thumbnail_url", "team_name", "team_display_name", "id", "user_id", "user_login", "user_name"]
            )
        }

    def _get_users_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Users-related endpoints."""
        return {
            "get_users": APIEndpoint(
                name="Get Users",
                endpoint="/users",
                method="GET",
                description="Gets information about one or more users",
                authorization=[],
                required_params={},
                optional_params={"id": "string", "login": "string"},
                response_fields=["data", "id", "login", "display_name", "type", "broadcaster_type", "description", "profile_image_url", "offline_image_url", "view_count", "email", "created_at"],
                auto_fill_params={"id": "trigger_user_id", "login": "trigger_username"}
            ),
            "update_user": APIEndpoint(
                name="Update User",
                endpoint="/users",
                method="PUT",
                description="Updates the specified user's information",
                authorization=["user:edit"],
                required_params={},
                optional_params={"description": "string", "email": "string"},
                response_fields=["data", "id", "login", "display_name", "type", "broadcaster_type", "description", "profile_image_url", "offline_image_url", "view_count", "email", "created_at"]
            ),
            "get_user_block_list": APIEndpoint(
                name="Get User Block List",
                endpoint="/users/blocks",
                method="GET",
                description="Gets a list of users that the broadcaster has blocked",
                authorization=["user:read:blocked_users"],
                required_params={},
                optional_params={"broadcaster_id": "string", "first": "integer", "after": "string"},
                response_fields=["data", "user_id", "user_login", "display_name", "pagination"]
            ),
            "block_user": APIEndpoint(
                name="Block User",
                endpoint="/users/blocks",
                method="PUT",
                description="Blocks the specified user from interacting with or having contact with the broadcaster",
                authorization=["user:manage:blocked_users"],
                required_params={"target_user_id": "string"},
                optional_params={},
                response_fields=["data", "user_id", "user_login", "display_name"]
            ),
            "unblock_user": APIEndpoint(
                name="Unblock User",
                endpoint="/users/blocks",
                method="DELETE",
                description="Unblocks the specified user from interacting with or having contact with the broadcaster",
                authorization=["user:manage:blocked_users"],
                required_params={"target_user_id": "string"},
                optional_params={},
                response_fields=[]
            )
        }

    def _get_videos_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Videos-related endpoints."""
        return {
            "get_videos": APIEndpoint(
                name="Get Videos",
                endpoint="/videos",
                method="GET",
                description="Gets video information by video ID, user ID, or game ID",
                authorization=[],
                required_params={},
                optional_params={"id": "string", "user_id": "string", "game_id": "string", "language": "string", "period": "string (all, day, month, week)", "sort": "string (time, trending, views)", "type": "string (all, upload, archive, highlight)", "first": "integer", "before": "string", "after": "string"},
                response_fields=["data", "id", "stream_id", "user_id", "user_login", "user_name", "title", "description", "created_at", "published_at", "url", "thumbnail_url", "viewable", "view_count", "language", "type", "duration", "muted_segments", "pagination"]
            ),
            "delete_videos": APIEndpoint(
                name="Delete Videos",
                endpoint="/videos",
                method="DELETE",
                description="Deletes one or more videos",
                authorization=["channel:manage:videos"],
                required_params={"id": "string"},
                optional_params={},
                response_fields=["data", "id"]
            )
        }

    def _get_whispers_endpoints(self) -> Dict[str, APIEndpoint]:
        """Get Whispers-related endpoints."""
        return {
            "send_whisper": APIEndpoint(
                name="Send Whisper",
                endpoint="/whispers",
                method="POST",
                description="Sends a whisper message to the specified user",
                authorization=["user:manage:whispers"],
                required_params={"from_user_id": "string", "to_user_id": "string", "message": "string"},
                optional_params={},
                response_fields=[]
            )
        }

    def get_all_endpoints(self) -> Dict[str, Dict[str, APIEndpoint]]:
        """Get all endpoints organized by category."""
        return {
            "ads": self.ads,
            "analytics": self.analytics,
            "bits": self.bits,
            "channels": self.channels,
            "channel_points": self.channel_points,
            "charity": self.charity,
            "chat": self.chat,
            "clips": self.clips,
            "conduits": self.conduits,
            "drops": self.drops,
            "extensions": self.extensions,
            "games": self.games,
            "goals": self.goals,
            "guest_star": self.guest_star,
            "hype_train": self.hype_train,
            "moderation": self.moderation,
            "polls": self.polls,
            "predictions": self.predictions,
            "raid": self.raid,
            "schedule": self.schedule,
            "search": self.search,
            "streams": self.streams,
            "subscriptions": self.subscriptions,
            "tags": self.tags,
            "teams": self.teams,
            "users": self.users,
            "videos": self.videos,
            "whispers": self.whispers
        }

    def get_endpoint_by_name(self, category: str, endpoint_name: str) -> Optional[APIEndpoint]:
        """Get a specific endpoint by category and name."""
        category_endpoints = getattr(self, category.lower().replace(" ", "_"), None)
        if category_endpoints and endpoint_name in category_endpoints:
            return category_endpoints[endpoint_name]
        return None

    def get_endpoints_by_method(self, method: str) -> List[APIEndpoint]:
        """Get all endpoints that use a specific HTTP method."""
        all_endpoints = self.get_all_endpoints()
        matching_endpoints = []

        for category_endpoints in all_endpoints.values():
            for endpoint in category_endpoints.values():
                if endpoint.method == method.upper():
                    matching_endpoints.append(endpoint)

        return matching_endpoints

    def get_endpoints_requiring_auth(self, scope: Optional[str] = None) -> List[APIEndpoint]:
        """Get all endpoints that require authentication, optionally filtered by scope."""
        all_endpoints = self.get_all_endpoints()
        auth_endpoints = []

        for category_endpoints in all_endpoints.values():
            for endpoint in category_endpoints.values():
                if endpoint.authorization:  # Has some authorization requirements
                    if not scope or any(scope in auth for auth in endpoint.authorization):
                        auth_endpoints.append(endpoint)

        return auth_endpoints

    def get_base_url(self) -> str:
        """Get the base URL for all Twitch API endpoints."""
        return "https://api.twitch.tv/helix"


# Example usage and helper functions
def print_endpoint_summary(api_ref: TwitchAPIReference, category: Optional[str] = None):
    """Print a summary of endpoints, optionally filtered by category."""
    if category:
        category_endpoints = getattr(api_ref, category.lower().replace(" ", "_"), {})
        print(f"\n=== {category.upper()} ENDPOINTS ===")
        for _, endpoint in category_endpoints.items():
            print(f"  {endpoint.name}: {endpoint.method} {endpoint.endpoint}")
    else:
        all_endpoints = api_ref.get_all_endpoints()
        for category_name, category_endpoints in all_endpoints.items():
            print(f"\n=== {category_name.upper()} ENDPOINTS ===")
            for _, endpoint in category_endpoints.items():
                print(f"  {endpoint.name}: {endpoint.method} {endpoint.endpoint}")


def get_endpoint_details(api_ref: TwitchAPIReference, category: str, endpoint_name: str):
    """Get detailed information about a specific endpoint."""
    endpoint = api_ref.get_endpoint_by_name(category, endpoint_name)
    if endpoint:
        print(f"\n{endpoint.name}")
        print(f"Method: {endpoint.method}")
        print(f"Endpoint: {endpoint.endpoint}")
        print(f"Description: {endpoint.description}")
        print(f"Authorization: {endpoint.authorization}")
        print(f"Required Params: {endpoint.required_params}")
        print(f"Optional Params: {endpoint.optional_params}")
        print(f"Response Fields: {endpoint.response_fields}")
        if endpoint.rate_limits:
            print(f"Rate Limits: {endpoint.rate_limits}")
    else:
        print(f"Endpoint {endpoint_name} not found in category {category}")


if __name__ == "__main__":
    # Example usage
    twitch_api = TwitchAPIReference()

    # Print all endpoints
    print_endpoint_summary(twitch_api)

    # Print specific category
    print_endpoint_summary(twitch_api, "chat")

    # Get specific endpoint details
    get_endpoint_details(twitch_api, "chat", "get_chat_settings")
