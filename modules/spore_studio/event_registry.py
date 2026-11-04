#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Curated registry of websocket events that Spore Studio templates can bind to.

The "binding picker" in the editor needs a stable, human-readable list of
events, the payload fields each event exposes, and the actions a bound
element can perform when the event fires. We intentionally do NOT
auto-discover events from ``modules/web_engine.py`` at runtime: most of the
events listed there are internal plumbing (chat backfill, statistics
ingest, Stream Deck control plane, etc.) and would only confuse a user
who is trying to drag-drop an alert overlay.

Add a new entry here when an event is genuinely useful to expose to
overlay authors. The shape is:

::

    {
        "event": "next_alert",
        "label": "Alert (queue)",
        "alert_system": "queue",
        "description": "Fired by the alert processor for queued alerts...",
        "payload": [
            {"key": "alert_type", "label": "Alert type", "type": "string",
             "examples": ["follow", "sub", "raid"]},
            ...
        ],
    }

The "actions" list is shared across events (everything an overlay can do
in response is the same — show, hide, set text, animate, etc.).
"""

from __future__ import annotations

from typing import Any, Dict, List


_EVENTS: List[Dict[str, Any]] = [
    {
        "event": "next_alert",
        "label": "Alert (queue)",
        "alert_system": "queue",
        "description": (
            "Fired by the alert processor for queued alerts (follows, subs, "
            "raids, donations, etc.). Only overlays listed in ``queue_holders`` "
            "may emit ``alert_playing`` / ``alert_complete`` for that item "
            "(typically ``alerts`` plus a matching Queued point-reward template). "
            "Spectator overlays such as bars and counters must not handshake."
        ),
        "payload": [
            {"key": "queue_seq", "label": "Queue completion id", "type": "number",
             "examples": ["1", "2", "3"]},
            {"key": "queue_holders", "label": "Overlay routes allowed to hold the queue",
             "type": "array",
             "examples": ["alerts", "boo game"]},
            {"key": "alert_type", "label": "Alert type", "type": "string",
             "examples": ["follow", "sub", "resub", "giftsub", "bit",
                          "donation", "raid", "hype_train", "point", "streak"]},
            {"key": "username", "label": "Username", "type": "string"},
            {"key": "message", "label": "Message", "type": "string"},
            {"key": "amount", "label": "Amount", "type": "number"},
            {"key": "currency", "label": "Currency", "type": "string"},
            {"key": "tier", "label": "Sub tier", "type": "string",
             "examples": ["1000", "2000", "3000"]},
            {"key": "cumulative_months", "label": "Cumulative months",
             "type": "number"},
            {"key": "raider_count", "label": "Raider count", "type": "number"},
            {"key": "amt_cheered", "label": "Bits cheered", "type": "number"},
            {"key": "quantity", "label": "Quantity", "type": "number"},
            {"key": "gift_qty", "label": "Gift sub quantity", "type": "number"},
            {"key": "recipient", "label": "Gift recipient", "type": "string"},
            {"key": "alert_name", "label": "Point reward name", "type": "string"},
            {"key": "streak_count", "label": "Watch streak", "type": "number"},
            {"key": "channel_points_awarded", "label": "Channel points awarded",
             "type": "number"},
            {"key": "level", "label": "Hype train level", "type": "number"},
            {"key": "total", "label": "Hype train total", "type": "number"},
            {"key": "timestamp", "label": "Timestamp", "type": "number"},
            {"key": "gif_dir", "label": "GIF directory", "type": "string"},
            {"key": "gif_name", "label": "GIF file name", "type": "string"},
            {"key": "hold_queue_only", "label": "Main overlay holds timing only",
             "type": "boolean"},
            {"key": "duration", "label": "Hold duration (seconds)",
             "type": "number"},
        ],
    },
    {
        "event": "instant_alert",
        "label": "Alert (instant)",
        "alert_system": "instant",
        "description": (
            "Fired by the alert processor for non-blocking alerts that do "
            "not participate in the queue handshake. Use this for sub bars, "
            "counters, and anything that updates without holding the queue."
        ),
        "payload": [
            {"key": "alert_type", "label": "Alert type", "type": "string",
             "examples": ["follow", "sub", "resub", "giftsub", "bit",
                          "donation", "raid", "hype_train", "point", "streak"]},
            {"key": "username", "label": "Username", "type": "string"},
            {"key": "message", "label": "Message", "type": "string"},
            {"key": "amount", "label": "Donation amount", "type": "number"},
            {"key": "currency", "label": "Currency", "type": "string"},
            {"key": "tier", "label": "Sub tier", "type": "string",
             "examples": ["1000", "2000", "3000"]},
            {"key": "quantity", "label": "Quantity", "type": "number"},
            {"key": "gift_qty", "label": "Gift sub quantity", "type": "number"},
            {"key": "cumulative_months", "label": "Cumulative months",
             "type": "number"},
            {"key": "raider_count", "label": "Raider count", "type": "number"},
            {"key": "amt_cheered", "label": "Bits cheered", "type": "number"},
            {"key": "recipient", "label": "Gift recipient", "type": "string"},
            {"key": "alert_name", "label": "Point reward name", "type": "string"},
            {"key": "streak_count", "label": "Watch streak", "type": "number"},
            {"key": "channel_points_awarded", "label": "Channel points awarded",
             "type": "number"},
            {"key": "level", "label": "Hype train level", "type": "number"},
            {"key": "total", "label": "Hype train total", "type": "number"},
            {"key": "duration", "label": "Hold duration (seconds)",
             "type": "number"},
            {"key": "timestamp", "label": "Timestamp", "type": "number"},
        ],
    },
    {
        "event": "giveaway_start",
        "label": "Giveaway started",
        "alert_system": "either",
        "description": "Fired when entries open (Start giveaway or Reopen entries). keyword is what viewers type.",
        "payload": [
            {"key": "keyword", "label": "Keyword", "type": "string"},
        ],
    },
    {
        "event": "giveaway_entry",
        "label": "Giveaway entry",
        "alert_system": "either",
        "description": "Fired when a chat user is added to the giveaway pool.",
        "payload": [
            {"key": "username", "label": "Username", "type": "string"},
            {"key": "pool_size", "label": "Pool size", "type": "number"},
            {"key": "pool_entries", "label": "Pool entries (newest first)", "type": "array"},
        ],
    },
    {
        "event": "giveaway_stop",
        "label": "Giveaway entries closed",
        "alert_system": "either",
        "description": "Fired when entries close. The pool stays until a new giveaway.",
        "payload": [],
    },
    {
        "event": "giveaway_clear",
        "label": "Giveaway cleared",
        "alert_system": "either",
        "description": "Fired by New giveaway: the pool and keyword are cleared.",
        "payload": [],
    },
    {
        "event": "giveaway_sync",
        "label": "Giveaway state",
        "alert_system": "either",
        "description": (
            "The whole giveaway at once: sent when an overlay loads or refreshes, and when an "
            "entrant is removed in Mycelian. Use it to rebuild lists and labels."
        ),
        "payload": [
            {"key": "keyword", "label": "Keyword", "type": "string"},
            {"key": "accepting", "label": "Accepting entries", "type": "boolean"},
            {"key": "pool_size", "label": "Pool size", "type": "number"},
            {"key": "pool_entries", "label": "Pool entries (newest first)", "type": "array"},
        ],
    },
    {
        "event": "giveaway_winner",
        "label": "Giveaway winner",
        "alert_system": "either",
        "description": (
            "Fired when winners are drawn. Winners is a list of names; "
            "pool_entries is every name that was in the pool."
        ),
        "payload": [
            {"key": "winners", "label": "Winners", "type": "array"},
            {"key": "pool_size", "label": "Pool size", "type": "number"},
            {"key": "pool_entries", "label": "Pool entries", "type": "array"},
        ],
    },
    {
        "event": "poll_state",
        "label": "Poll update",
        "alert_system": "either",
        "description": (
            "Fired when a chat poll starts, receives a vote, or changes. "
            "Options are ordered by votes, highest first."
        ),
        "payload": [
            {"key": "template", "label": "Template", "type": "string"},
            {"key": "status", "label": "Status", "type": "string",
             "examples": ["idle", "open", "ended"]},
            {"key": "total_votes", "label": "Total votes", "type": "number"},
            {"key": "options", "label": "Options", "type": "array"},
            {"key": "winners", "label": "Winners", "type": "array"},
            {"key": "remaining_seconds", "label": "Seconds remaining", "type": "number"},
        ],
    },
    {
        "event": "poll_ended",
        "label": "Poll ended",
        "alert_system": "either",
        "description": (
            "Fired when a poll is stopped or its timer runs out. "
            "Winners lists every option tied for the most votes."
        ),
        "payload": [
            {"key": "template", "label": "Template", "type": "string"},
            {"key": "status", "label": "Status", "type": "string"},
            {"key": "total_votes", "label": "Total votes", "type": "number"},
            {"key": "options", "label": "Options", "type": "array"},
            {"key": "winners", "label": "Winners", "type": "array"},
        ],
    },
    {
        "event": "prediction_state",
        "label": "Prediction update",
        "alert_system": "either",
        "description": (
            "Fired when a Twitch prediction starts, gets channel points, locks or ends. "
            "Outcomes keep Twitch's order (blue first)."
        ),
        "payload": [
            {"key": "template", "label": "Template", "type": "string"},
            {"key": "status", "label": "Status", "type": "string",
             "examples": ["idle", "active", "locked", "resolved", "canceled"]},
            {"key": "title", "label": "Question", "type": "string"},
            {"key": "outcomes", "label": "Outcomes", "type": "array"},
            {"key": "total_points", "label": "Total points", "type": "number"},
            {"key": "total_users", "label": "Total predictors", "type": "number"},
            {"key": "remaining_seconds", "label": "Seconds until it locks", "type": "number"},
            {"key": "winning_outcome_id", "label": "Winning outcome id", "type": "string"},
        ],
    },
    {
        "event": "prediction_ended",
        "label": "Prediction ended",
        "alert_system": "either",
        "description": (
            "Fired when a Twitch prediction is resolved (status resolved, with a winner) "
            "or refunded (status canceled)."
        ),
        "payload": [
            {"key": "template", "label": "Template", "type": "string"},
            {"key": "status", "label": "Status", "type": "string", "examples": ["resolved", "canceled"]},
            {"key": "title", "label": "Question", "type": "string"},
            {"key": "outcomes", "label": "Outcomes", "type": "array"},
            {"key": "total_points", "label": "Total points", "type": "number"},
            {"key": "winning_outcome_id", "label": "Winning outcome id", "type": "string"},
        ],
    },
    {
        "event": "twitch_goal_begin",
        "label": "Twitch goal started",
        "alert_system": "either",
        "description": "Fired when a Creator Goal starts on your Twitch channel.",
        "payload": [
            {"key": "id", "label": "Goal id", "type": "string"},
            {"key": "type", "label": "Goal type", "type": "string",
             "examples": ["follower", "subscription", "subscription_count", "new_subscription", "new_subscription_count"]},
            {"key": "description", "label": "Description", "type": "string"},
            {"key": "current_amount", "label": "Current amount", "type": "number"},
            {"key": "target_amount", "label": "Target amount", "type": "number"},
            {"key": "percent", "label": "Percent of target", "type": "number"},
            {"key": "is_achieved", "label": "Achieved", "type": "boolean"},
            {"key": "status", "label": "Status", "type": "string", "examples": ["active", "ended"]},
            {"key": "started_at", "label": "Started (unix time)", "type": "number"},
            {"key": "ended_at", "label": "Ended (unix time)", "type": "number"},
        ],
    },
    {
        "event": "twitch_goal_progress",
        "label": "Twitch goal progress",
        "alert_system": "either",
        "description": "Fired when a Twitch Creator Goal's current amount changes.",
        "payload": [
            {"key": "id", "label": "Goal id", "type": "string"},
            {"key": "type", "label": "Goal type", "type": "string",
             "examples": ["follower", "subscription", "subscription_count", "new_subscription", "new_subscription_count"]},
            {"key": "description", "label": "Description", "type": "string"},
            {"key": "current_amount", "label": "Current amount", "type": "number"},
            {"key": "target_amount", "label": "Target amount", "type": "number"},
            {"key": "percent", "label": "Percent of target", "type": "number"},
            {"key": "is_achieved", "label": "Achieved", "type": "boolean"},
            {"key": "status", "label": "Status", "type": "string", "examples": ["active", "ended"]},
            {"key": "started_at", "label": "Started (unix time)", "type": "number"},
            {"key": "ended_at", "label": "Ended (unix time)", "type": "number"},
        ],
    },
    {
        "event": "twitch_goal_end",
        "label": "Twitch goal ended",
        "alert_system": "either",
        "description": "Fired when a Twitch Creator Goal ends (achieved or not).",
        "payload": [
            {"key": "id", "label": "Goal id", "type": "string"},
            {"key": "type", "label": "Goal type", "type": "string",
             "examples": ["follower", "subscription", "subscription_count", "new_subscription", "new_subscription_count"]},
            {"key": "description", "label": "Description", "type": "string"},
            {"key": "current_amount", "label": "Current amount", "type": "number"},
            {"key": "target_amount", "label": "Target amount", "type": "number"},
            {"key": "percent", "label": "Percent of target", "type": "number"},
            {"key": "is_achieved", "label": "Achieved", "type": "boolean"},
            {"key": "status", "label": "Status", "type": "string", "examples": ["active", "ended"]},
            {"key": "started_at", "label": "Started (unix time)", "type": "number"},
            {"key": "ended_at", "label": "Ended (unix time)", "type": "number"},
        ],
    },
    {
        "event": "twitch_goal_sync",
        "label": "Twitch goal (current state)",
        "alert_system": "either",
        "description": "Sent to an overlay that asks for the current goals: one per active Twitch Creator Goal.",
        "payload": [
            {"key": "id", "label": "Goal id", "type": "string"},
            {"key": "type", "label": "Goal type", "type": "string",
             "examples": ["follower", "subscription", "subscription_count", "new_subscription", "new_subscription_count"]},
            {"key": "description", "label": "Description", "type": "string"},
            {"key": "current_amount", "label": "Current amount", "type": "number"},
            {"key": "target_amount", "label": "Target amount", "type": "number"},
            {"key": "percent", "label": "Percent of target", "type": "number"},
            {"key": "is_achieved", "label": "Achieved", "type": "boolean"},
            {"key": "status", "label": "Status", "type": "string", "examples": ["active", "ended"]},
            {"key": "started_at", "label": "Started (unix time)", "type": "number"},
            {"key": "ended_at", "label": "Ended (unix time)", "type": "number"},
        ],
    },
    {
        "event": "spotify_data_update",
        "label": "Now playing (Spotify)",
        "alert_system": "either",
        "description": "Fired when the Spotify track or playback position changes.",
        "payload": [
            {"key": "track_name", "label": "Track name", "type": "string"},
            {"key": "artist_name", "label": "Artist", "type": "string"},
            {"key": "album_image_url", "label": "Album art URL", "type": "string"},
            {"key": "progress_percentage", "label": "Progress (percent)", "type": "number"},
        ],
    },
    {
        "event": "steam_data_update",
        "label": "Steam game & achievements",
        "alert_system": "either",
        "description": (
            "Fired about every 15 seconds with the tracked Steam profile, the game it is playing "
            "and that game's achievements."
        ),
        "payload": [
            {"key": "persona_name", "label": "Profile name", "type": "string"},
            {"key": "avatar_url", "label": "Profile picture URL", "type": "string"},
            {"key": "persona_state", "label": "Status (Online, Away...)", "type": "string"},
            {"key": "current_game_name", "label": "Game name", "type": "string"},
            {"key": "current_game_art_url", "label": "Game art URL", "type": "string"},
            {"key": "achievement_counts.unlocked", "label": "Achievements unlocked", "type": "number"},
            {"key": "achievement_counts.total", "label": "Achievements in the game", "type": "number"},
            {"key": "current_game_progress", "label": "Progress (percent)", "type": "number"},
            {"key": "steam_level", "label": "Steam level", "type": "number"},
            {"key": "last_unlock.name", "label": "Latest achievement", "type": "string"},
        ],
    },
    {
        "event": "steam_achievement_unlocked",
        "label": "Steam achievement unlocked",
        "alert_system": "either",
        "description": "Fired once for each new achievement the tracked Steam profile unlocks.",
        "payload": [
            {"key": "name", "label": "Achievement name", "type": "string"},
            {"key": "description", "label": "Description", "type": "string"},
            {"key": "icon_url", "label": "Icon URL", "type": "string"},
            {"key": "global_percent", "label": "Players who have it (percent)", "type": "number"},
            {"key": "game_name", "label": "Game name", "type": "string"},
        ],
    },
    {
        "event": "game_tracker_event",
        "label": "Games Tracker change",
        "alert_system": "either",
        "description": (
            "Fired for each change the Games Tracker finds after its first import: a trophy or achievement "
            "unlocked (kind unlock), a game completed (completed), a new game (started), a completion "
            "milestone (milestone), or a game goal that moved (goal_progress) or got done (goal_completed)."
        ),
        "payload": [
            {"key": "kind", "label": "What happened", "type": "string",
             "examples": ["unlock", "completed", "started", "milestone", "goal_progress", "goal_completed"]},
            {"key": "platform", "label": "Platform key", "type": "string", "examples": ["psn", "steam"]},
            {"key": "platform_name", "label": "Platform", "type": "string", "examples": ["PlayStation", "Steam"]},
            {"key": "game_title", "label": "Game", "type": "string"},
            {"key": "progress", "label": "Completion (percent)", "type": "number"},
            {"key": "earned", "label": "Trophies / achievements earned", "type": "number"},
            {"key": "total", "label": "Trophies / achievements in the game", "type": "number"},
            {"key": "unlock_name", "label": "Trophy / achievement name", "type": "string"},
            {"key": "unlock_grade", "label": "Grade", "type": "string",
             "examples": ["bronze", "silver", "gold", "platinum", "achievement"]},
            {"key": "unlock_description", "label": "Description", "type": "string"},
            {"key": "unlock_rarity", "label": "Players who have it (percent)", "type": "number"},
            {"key": "unlock_icon", "label": "Icon URL", "type": "string"},
            {"key": "milestone", "label": "Milestone reached (percent)", "type": "number"},
            {"key": "goal_name", "label": "Goal", "type": "string"},
            {"key": "goal_type", "label": "Goal type", "type": "string",
             "examples": ["manual", "complete", "count", "percent", "unlock"]},
            {"key": "goal_current", "label": "Goal: done so far", "type": "number"},
            {"key": "goal_target", "label": "Goal: target", "type": "number"},
            {"key": "goal_progress", "label": "Goal: percent done", "type": "number"},
            {"key": "goals_done", "label": "Goals done in this game", "type": "number"},
            {"key": "goals_total", "label": "Goals in this game", "type": "number"},
            {"key": "all_goals_done", "label": "Every goal of this game done", "type": "boolean"},
        ],
    },
    {
        "event": "game_tracker_game",
        "label": "Games Tracker game",
        "alert_system": "either",
        "description": (
            "Fired with one game whenever its progress or goals change (and when it's edited). The Game panel "
            "block listens to this to stay live."
        ),
        "payload": [
            {"key": "key", "label": "Game key (psn:<NPWR ID> or steam:<app ID>)", "type": "string"},
            {"key": "title", "label": "Game", "type": "string"},
            {"key": "platform", "label": "Platform key", "type": "string", "examples": ["psn", "steam"]},
            {"key": "platform_label", "label": "System", "type": "string", "examples": ["PS5", "Steam"]},
            {"key": "progress", "label": "Completion (percent)", "type": "number"},
            {"key": "earned", "label": "Trophies / achievements earned", "type": "number"},
            {"key": "total", "label": "Trophies / achievements in the game", "type": "number"},
            {"key": "completed", "label": "Platinum / 100%", "type": "boolean"},
            {"key": "cover", "label": "Cover image URL", "type": "string"},
            {"key": "goals_total", "label": "Goals", "type": "number"},
            {"key": "goals_done", "label": "Goals done", "type": "number"},
            {"key": "goals_progress", "label": "Goals: average percent done", "type": "number"},
            {"key": "next_goal.name", "label": "Next goal", "type": "string"},
            {"key": "next_goal.progress", "label": "Next goal: percent done", "type": "number"},
            {"key": "speedruns", "label": "Speedrun info (one row per LiveSplit category: category, pb, attempts, finished)",
             "type": "array"},
        ],
    },
    {
        "event": "game_tracker_games",
        "label": "Games Tracker games (reply)",
        "alert_system": "either",
        "description": (
            "The reply to a Game panel's request (game_tracker_request_games): the chosen games, every game "
            "with goals, or the last played game, each with its goals."
        ),
        "payload": [
            {"key": "mode", "label": "Which games", "type": "string", "examples": ["selected", "backlog", "current"]},
            {"key": "games", "label": "Games", "type": "array"},
        ],
    },
    {
        "event": "game_tracker_update",
        "label": "Games Tracker totals",
        "alert_system": "either",
        "description": "Fired after each library sync or change with the tracker's totals and the most recently played game.",
        "payload": [
            {"key": "games", "label": "Games tracked", "type": "number"},
            {"key": "psn_games", "label": "PlayStation games", "type": "number"},
            {"key": "steam_games", "label": "Steam games", "type": "number"},
            {"key": "platinums", "label": "Platinum trophies", "type": "number"},
            {"key": "perfect", "label": "Perfect Steam games", "type": "number"},
            {"key": "completed", "label": "Games completed", "type": "number"},
            {"key": "trophies", "label": "Trophies earned", "type": "number"},
            {"key": "achievements", "label": "Steam achievements earned", "type": "number"},
            {"key": "avg_completion", "label": "Average completion (percent)", "type": "number"},
            {"key": "current.title", "label": "Latest game", "type": "string"},
            {"key": "current.progress", "label": "Latest game completion (percent)", "type": "number"},
            {"key": "backlog", "label": "Backlog: games with goals", "type": "number"},
            {"key": "backlog_active", "label": "Backlog: games with goals still open", "type": "number"},
            {"key": "goals_total", "label": "Goals set", "type": "number"},
            {"key": "goals_done", "label": "Goals done", "type": "number"},
        ],
    },
    {
        "event": "refresh-alerts",
        "label": "Refresh alerts",
        "alert_system": "either",
        "description": (
            "Broadcast when alert settings change. Bind to this if your "
            "overlay needs to reset state or reload assets."
        ),
        "payload": [],
    },
    {
        "event": "alerts_paused",
        "label": "Alerts paused",
        "alert_system": "either",
        "description": "Sent when the user pauses the alert system.",
        "payload": [
            {"key": "paused", "label": "Paused", "type": "boolean"},
        ],
    },
    {
        "event": "alerts_resumed",
        "label": "Alerts resumed",
        "alert_system": "either",
        "description": "Sent when the user resumes the alert system.",
        "payload": [
            {"key": "paused", "label": "Paused", "type": "boolean"},
        ],
    },
    {
        "event": "pause_status_update",
        "label": "Pause status update",
        "alert_system": "either",
        "description": "Sent on every pause/resume toggle and at startup.",
        "payload": [
            {"key": "paused", "label": "Paused", "type": "boolean"},
        ],
    },
    {
        "event": "new-message",
        "label": "Chat message (Twitch)",
        "alert_system": "either",
        "description": (
            "Fired for every Twitch chat message ingested by Mycelian. "
            "Carries the full message payload — username, message body, "
            "user color, badges, emote map, and message type."
        ),
        "payload": [
            {"key": "username", "label": "Username", "type": "string"},
            {"key": "message", "label": "Message text", "type": "string"},
            {"key": "color", "label": "Username color", "type": "string"},
            {"key": "badges", "label": "Badges (csv)", "type": "string"},
            {"key": "emotes", "label": "Emotes (string)", "type": "string"},
            {"key": "message_type", "label": "Message type", "type": "string",
             "examples": ["text", "action", "highlight"]},
            {"key": "userid", "label": "User ID", "type": "string"},
            {"key": "timestamp", "label": "Timestamp", "type": "number"},
        ],
    },
    {
        "event": "chat_display_banner",
        "label": "Chat banner",
        "alert_system": "either",
        "description": (
            "Overlay banner on the chat template. Twitch announcements, "
            "connectors, chatbot, giveaways, Source Controls, and other "
            "templates all use this payload. Style fields override Banner Options."
        ),
        "payload": [
            {"key": "message", "label": "Message", "type": "string"},
            {"key": "duration", "label": "Duration (seconds, 0 = permanent)", "type": "number"},
            {"key": "permanent", "label": "Permanent", "type": "boolean"},
            {"key": "source", "label": "Source", "type": "string"},
            {"key": "style.background", "label": "Background override", "type": "string"},
            {"key": "style.background_image", "label": "Background image override", "type": "string"},
            {"key": "style.font", "label": "Font file override", "type": "string"},
            {"key": "style.font_size", "label": "Font size override", "type": "number"},
            {"key": "style.color", "label": "Text color override", "type": "string"},
            {"key": "style.animation", "label": "Animation override", "type": "string"},
        ],
    },
    {
        "event": "chat_add_message",
        "label": "Chat message (connector)",
        "alert_system": "either",
        "description": (
            "Connector-driven chat message — fired by the chat "
            "template's 'Add message' connector action (handy for "
            "bot replies, Stream Deck shoutouts, etc.)."
        ),
        "payload": [
            {"key": "username", "label": "Username", "type": "string"},
            {"key": "message_text", "label": "Message text", "type": "string"},
            {"key": "is_moderator", "label": "Moderator", "type": "boolean"},
        ],
    },
    {
        "event": "message_moderation",
        "label": "Chat moderation event",
        "alert_system": "either",
        "description": (
            "Sent when a moderator deletes a chat message or times out "
            "a user; chat overlays use this to fade or strikethrough "
            "the affected messages."
        ),
        "payload": [
            {"key": "action", "label": "Action", "type": "string",
             "examples": ["delete", "timeout", "ban"]},
            {"key": "user_id", "label": "User ID", "type": "string"},
            {"key": "message_id", "label": "Message ID", "type": "string"},
        ],
    },
    {
        "event": "twitch-api-response",
        "label": "Twitch API call (response)",
        "alert_system": "either",
        "description": (
            "Configure the Helix URL and optional query/body JSON in the inspector. "
            "The overlay emits ``twitch-api-request`` after connect using a binding id "
            "as ``requestId`` (also applied to response filters automatically). "
            "Add filters for fields on the socket payload — e.g. ``success`` with value "
            "``true`` (JSON)."
        ),
        "payload": [],
    },
]


_ACTIONS: List[Dict[str, Any]] = [
    {
        "action": "show",
        "label": "Show element",
        "description": "Make the element visible.",
        "args": [],
    },
    {
        "action": "hide",
        "label": "Hide element",
        "description": "Hide the element (sets data-spore-hidden=true).",
        "args": [],
    },
    {
        "action": "toggle",
        "label": "Toggle visibility",
        "description": (
            "Show the element if it is hidden, or hide it if it is visible "
            "(toggles data-spore-hidden)."
        ),
        "args": [],
    },
    {
        "action": "show_for",
        "label": "Show for N seconds",
        "description": "Make the element visible, then hide it after N seconds.",
        "args": [
            {"key": "seconds", "label": "Seconds", "type": "number",
             "default": 5, "min": 0.1, "max": 600},
            {"key": "anim_in", "label": "Entrance animation", "type": "select",
             "options": ["none", "fade", "slideIn", "scaleIn"],
             "default": "fade"},
            {"key": "anim_out", "label": "Exit animation", "type": "select",
             "options": ["none", "fade", "slideOut", "scaleOut"],
             "default": "fade"},
        ],
    },
    {
        "action": "set_text",
        "label": "Set text content",
        "description": (
            "Write a value from the event payload (or a literal string) into "
            "the element's text."
        ),
        "args": [
            {"key": "from_payload", "label": "Payload field", "type": "string",
             "default": "", "description":
             "Payload key whose value is used (e.g. 'username'). "
             "Leave blank to use a literal."},
            {"key": "literal", "label": "Literal text", "type": "string",
             "default": "", "description":
             "Used when 'Payload field' is blank."},
        ],
    },
    {
        "action": "set_image",
        "label": "Set image source",
        "description": "Update an <img> element's src attribute.",
        "args": [
            {"key": "from_payload", "label": "Payload field", "type": "string",
             "default": ""},
            {"key": "literal", "label": "Literal URL", "type": "string",
             "default": ""},
        ],
    },
    {
        "action": "play_audio",
        "label": "Play audio element",
        "description": (
            "Start playback on this element's audio (or nested <audio> "
            "inside a video bundle). Honors Spore Studio volume / fade-in."
        ),
        "args": [
            {"key": "volume", "label": "Volume override (0–1)", "type": "number",
             "default": "", "min": 0, "max": 1, "step": 0.05,
             "description": "Blank = element default"},
            {"key": "fade_in_ms", "label": "Fade-in override (ms)", "type": "number",
             "default": "", "min": 0, "max": 120000,
             "description": "Blank = element default"},
        ],
    },
    {
        "action": "set_visual_src",
        "label": "Set visual source (GIF/video)",
        "description": (
            "Update nested <img> or <video> source URL for a bundled video "
            "element (GIF swaps, etc.). Payload or literal."
        ),
        "args": [
            {"key": "from_payload", "label": "Payload field", "type": "string",
             "default": ""},
            {"key": "literal", "label": "Literal URL", "type": "string",
             "default": ""},
        ],
    },
    {
        "action": "randomize_position",
        "label": "Randomize position within bounds",
        "description": (
            "Set left/top to random integers in pixel bounds. Omit x_max/y_max "
            "(or pass -1) to align with canvas edge minus element width/height."
        ),
        "args": [
            {"key": "x_min", "label": "X min (px)", "type": "number",
             "default": 0},
            {"key": "x_max", "label": "X max (px, -1=canvas)", "type": "number",
             "default": -1},
            {"key": "y_min", "label": "Y min (px)", "type": "number",
             "default": 0},
            {"key": "y_max", "label": "Y max (px, -1=canvas)", "type": "number",
             "default": -1},
        ],
    },
    {
        "action": "set_transform",
        "label": "Set CSS transform",
        "description": "Replace transform with translate / rotate / scale (blank args skipped).",
        "args": [
            {"key": "translate_x", "label": "Translate X (px)", "type": "number"},
            {"key": "translate_y", "label": "Translate Y (px)", "type": "number"},
            {"key": "rotate_deg", "label": "Rotate (degrees)", "type": "number"},
            {"key": "scale", "label": "Scale (uniform)", "type": "number"},
        ],
    },
    {
        "action": "transform_jitter",
        "label": "Random transform jitter",
        "description": (
            "Random translate ±range, rotate ±rotate_range°, and/or "
            "uniform scale in [scale_min, scale_max]."
        ),
        "args": [
            {"key": "rotate_range", "label": "Rotate range (degrees)", "type": "number",
             "default": 0},
            {"key": "translate_range", "label": "Translate range (px)", "type": "number",
             "default": 0},
            {"key": "scale_min", "label": "Scale min", "type": "number"},
            {"key": "scale_max", "label": "Scale max", "type": "number"},
        ],
    },
    {
        "action": "flash_class",
        "label": "Play CSS class animation",
        "description": (
            "Add a class briefly (restart if re-triggered). Try sporeShake or sporePop."
        ),
        "args": [
            {"key": "class_name", "label": "Class name", "type": "string",
             "default": "sporeShake"},
            {"key": "duration_ms", "label": "Hold (ms)", "type": "number",
             "default": 420, "min": 0, "max": 60000},
        ],
    },
    {
        "action": "counter_adjust",
        "label": "Adjust counter",
        "description": (
            "Increment, decrement, set, or reset a Spore Studio counter on this "
            "template (text element in Counter mode)."
        ),
        "args": [
            {"key": "counter_id", "label": "Counter id", "type": "string",
             "default": "", "description": "counter_id from the text element."},
            {"key": "operation", "label": "Operation", "type": "select",
             "options": ["increment", "decrement", "set", "reset"],
             "default": "increment"},
            {"key": "delta_kind", "label": "Delta kind", "type": "select",
             "options": ["fixed", "random_int", "random_float", "data_source"],
             "default": "fixed"},
            {"key": "delta_value", "label": "Fixed delta / fallback", "type": "number",
             "default": 1},
            {"key": "delta_source", "label": "Data source id", "type": "string",
             "default": "", "description": "When delta kind is data_source."},
            {"key": "delta_min", "label": "Random min", "type": "number", "default": 1},
            {"key": "delta_max", "label": "Random max", "type": "number", "default": 5},
        ],
    },
    {
        "action": "timer_start",
        "label": "Start timer",
        "description": "Start or resume a timer element's count up/down.",
        "args": [],
    },
    {
        "action": "timer_pause",
        "label": "Pause timer",
        "description": "Pause a timer element.",
        "args": [],
    },
    {
        "action": "timer_reset",
        "label": "Reset timer",
        "description": "Reset a timer element to zero and apply auto-start setting.",
        "args": [],
    },
    {
        "action": "list_add",
        "label": "Add list row",
        "description": (
            "Append a row to this List widget. Uses the event payload, or the "
            "literal text when the payload has no matching fields."
        ),
        "args": [
            {"key": "text", "label": "Literal text", "type": "string",
             "default": "", "description": "Used when the payload has no row text."},
        ],
    },
    {
        "action": "list_clear",
        "label": "Clear list",
        "description": "Remove every row from this List widget.",
        "args": [],
    },
    {
        "action": "wheel_spin",
        "label": "Spin wheel",
        "description": (
            "Spin this Wheel widget. When it stops, the winning label is stored "
            "on window.__sporeLastPayload.wheel and emitted as wheel_result."
        ),
        "args": [],
    },
]


def _annotate_alert_fields() -> None:
    """Tag alert payload fields with the alert types that carry them."""
    from .data_source_registry import ALERT_FIELD_TYPES

    for ev in _EVENTS:
        if ev.get("event") not in ("next_alert", "instant_alert"):
            continue
        for field in ev.get("payload", []):
            types = ALERT_FIELD_TYPES.get(field.get("key"))
            if types:
                field["alert_types"] = list(types)


_annotate_alert_fields()


def get_event_registry() -> Dict[str, Any]:
    """
    Return the full event + action registry as a JSON-serializable dict.

    Returns:
        A dict with two top-level keys: ``events`` (list of event
        descriptors) and ``actions`` (list of action descriptors).
    """
    events = [dict(event) for event in _EVENTS]
    try:
        from .template_outputs import merge_into_events

        events = merge_into_events(events)
    except Exception:  # pragma: no cover - discovery must never break the picker
        pass
    return {
        "events": events,
        "actions": [dict(action) for action in _ACTIONS],
    }


def get_event(event_name: str) -> Dict[str, Any]:
    """Return a single event descriptor by name, or an empty dict if unknown."""
    for ev in _EVENTS:
        if ev.get("event") == event_name:
            return dict(ev)
    return {}


def get_action(action_name: str) -> Dict[str, Any]:
    """Return a single action descriptor by name, or an empty dict if unknown."""
    for act in _ACTIONS:
        if act.get("action") == action_name:
            return dict(act)
    return {}
