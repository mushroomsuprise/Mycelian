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

from typing import Dict

"""
Preview mappings for Mycelian chatbot and event preview functionality.

This module contains all the built-in variable mappings used for previewing
chatbot commands and events with example values.
"""


def get_builtin_variable_mappings() -> Dict[str, str]:
    """
    Get the comprehensive mapping of built-in variables to example values.

    This mapping is used by the chatbot preview functionality to replace
    variables in command/event responses with realistic example values.

    Returns:
        Dict[str, str]: Dictionary mapping variable names to example values
    """
    return {
        # Basic variables
        "username": "TestUser",
        "amount": "100",
        "count": "42",
        "timestamp": "2024-01-01 12:00:00",
        # Follow variables
        "follower_count": "1,234",
        # Subscription variables
        "tier_name": "Tier 1",
        "message": "Thanks for the great stream!",
        "months": "3",
        "cumulative_months": "12",
        "streak": "5",
        # Gift subscription variables
        "recipient_name": "Viewer123",
        "gifter_name": "GenerousGifter",
        "total_gifts": "5",
        # Bits variables
        "power_up_type": "Lightning",
        "bits_amount": "500",
        # Raid variables
        "raider_name": "FriendlyRaider",
        "viewer_count": "25",
        # Hype train variables
        "conductor_name": "TrainLeader",
        "level": "3",
        "goal": "100",
        "progress": "75",
        "total_contributions": "87",
        "top_contributors": "User1 (50), User2 (25), User3 (12)",
        # Donation variables
        "donation_message": "Keep up the great work!",
        "formatted_amount": "$25.00",
        # Generic variables
        "source": "Twitch",
        # Time and date variables
        "time": "2:30 PM",
        "date": "January 15, 2024",
        "datetime": "2024-01-15 14:30:00",
        "day": "Monday",
        "month": "January",
        "year": "2024",
        "hour": "14",
        "minute": "30",
        "second": "45",
        # Statistics variables (sample values)
        "total_followers": "1,234",
        "total_subs": "567",
        "total_bits": "89,012",
        "total_donations": "$456.78",
        "stream_uptime": "2h 30m",
        "current_viewers": "89",
        "max_viewers": "156",
        "session_duration": "2.5 hours",
        "commands_used": "42",
        "events_triggered": "15",
        # Stream/game variables
        "game": "Just Chatting",
        "game_category": "Just Chatting",
        "stream_title": "Building Mycelian - Streaming Toolkit",
        "stream_description": "Developing the ultimate streaming toolkit",
        # User status variables
        "user_level": "VIP",
        "user_points": "1,500",
        "user_rank": "Gold Member",
        "user_badges": "Subscriber, VIP",
        # Counter variables
        "death_count": "7",
        "kill_count": "23",
        "win_count": "5",
        "loss_count": "2",
        "streak_count": "3",
        # Random/fun variables
        "random_number": "42",
        "random_choice": "Option A",
        "random_emote": "Kappa",
        "lucky_number": "7",
    }


def get_variable_categories() -> Dict[str, Dict[str, str]]:
    """
    Get variable mappings organized by categories for easier maintenance.

    Returns:
        Dict[str, Dict[str, str]]: Variables organized by category
    """
    mappings = get_builtin_variable_mappings()

    return {
        "basic": {
            "username": mappings["username"],
            "amount": mappings["amount"],
            "count": mappings["count"],
            "timestamp": mappings["timestamp"],
        },
        "follow": {
            "follower_count": mappings["follower_count"],
        },
        "subscription": {
            "tier_name": mappings["tier_name"],
            "message": mappings["message"],
            "months": mappings["months"],
            "cumulative_months": mappings["cumulative_months"],
            "streak": mappings["streak"],
        },
        "gift_subscription": {
            "recipient_name": mappings["recipient_name"],
            "gifter_name": mappings["gifter_name"],
            "total_gifts": mappings["total_gifts"],
        },
        "bits": {
            "power_up_type": mappings["power_up_type"],
            "bits_amount": mappings["bits_amount"],
        },
        "raid": {
            "raider_name": mappings["raider_name"],
            "viewer_count": mappings["viewer_count"],
        },
        "hype_train": {
            "conductor_name": mappings["conductor_name"],
            "level": mappings["level"],
            "goal": mappings["goal"],
            "progress": mappings["progress"],
            "total_contributions": mappings["total_contributions"],
            "top_contributors": mappings["top_contributors"],
        },
        "donation": {
            "donation_message": mappings["donation_message"],
            "formatted_amount": mappings["formatted_amount"],
        },
        "time_date": {
            "time": mappings["time"],
            "date": mappings["date"],
            "datetime": mappings["datetime"],
            "day": mappings["day"],
            "month": mappings["month"],
            "year": mappings["year"],
            "hour": mappings["hour"],
            "minute": mappings["minute"],
            "second": mappings["second"],
        },
        "statistics": {
            "total_followers": mappings["total_followers"],
            "total_subs": mappings["total_subs"],
            "total_bits": mappings["total_bits"],
            "total_donations": mappings["total_donations"],
            "stream_uptime": mappings["stream_uptime"],
            "current_viewers": mappings["current_viewers"],
            "max_viewers": mappings["max_viewers"],
            "session_duration": mappings["session_duration"],
            "commands_used": mappings["commands_used"],
            "events_triggered": mappings["events_triggered"],
        },
        "stream": {
            "game": mappings["game"],
            "game_category": mappings["game_category"],
            "stream_title": mappings["stream_title"],
            "stream_description": mappings["stream_description"],
        },
        "user_status": {
            "user_level": mappings["user_level"],
            "user_points": mappings["user_points"],
            "user_rank": mappings["user_rank"],
            "user_badges": mappings["user_badges"],
        },
        "counters": {
            "death_count": mappings["death_count"],
            "kill_count": mappings["kill_count"],
            "win_count": mappings["win_count"],
            "loss_count": mappings["loss_count"],
            "streak_count": mappings["streak_count"],
        },
        "random_fun": {
            "random_number": mappings["random_number"],
            "random_choice": mappings["random_choice"],
            "random_emote": mappings["random_emote"],
            "lucky_number": mappings["lucky_number"],
        },
        "generic": {
            "source": mappings["source"],
        },
    }


def get_category_variables(category: str) -> Dict[str, str]:
    """
    Get variables for a specific category.

    Args:
        category: The category name (e.g., 'basic', 'time_date', 'statistics')

    Returns:
        Dict[str, str]: Variables for the specified category
    """
    categories = get_variable_categories()
    return categories.get(category, {})
