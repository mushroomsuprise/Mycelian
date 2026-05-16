#!/usr/bin/env python3
"""Per-template mock Socket.IO actions for the Custom Sources preview toolbar."""

from __future__ import annotations

from typing import Any, Dict, List

# event: passed to :func:`spore_studio.preview_mocks.build_mock_payload`
_MOCKS: Dict[str, List[Dict[str, Any]]] = {
    "alerts": [
        {"event": "next_alert", "label": "Queue alert"},
        {"event": "alerts_play_alert", "label": "Play alert"},
        {"event": "refresh-alerts", "label": "Refresh"},
    ],
    "pausedalerts": [
        {"event": "pause_status_on", "label": "Paused on"},
        {"event": "pause_status_off", "label": "Paused off"},
    ],
    "chat": [
        {"event": "new-message", "label": "Chat (Twitch)"},
        {"event": "chat_add_message", "label": "Chat (connector)"},
        {"event": "message_moderation", "label": "Moderation"},
    ],
    "activity_feed": [
        {"event": "activity_feed_alert", "label": "Sample feed item"},
    ],
    "giveaway": [
        {"event": "giveaway_start", "label": "Start"},
        {"event": "giveaway_entry", "label": "Entry"},
        {"event": "giveaway_stop", "label": "Stop entries"},
        {"event": "giveaway_winner", "label": "Winner"},
        {"event": "giveaway_clear", "label": "Clear"},
    ],
    "title": [
        {"event": "streamer-info", "label": "Streamer info"},
        {"event": "twitch_data_update", "label": "Category update"},
    ],
    "bitbar": [
        {"event": "bitbar_add_bits", "label": "Add bits"},
        {"event": "bitbar_reset", "label": "Reset"},
    ],
    "subbar": [
        {"event": "subbar_instant_alert", "label": "Instant alert"},
        {"event": "subbar_reset", "label": "Reset"},
    ],
    "counter": [
        {"event": "counter_message", "label": "Set value"},
    ],
    "roulette": [
        {"event": "roulette_spin", "label": "Spin"},
        {"event": "roulette_refresh", "label": "Refresh wheel"},
    ],
    "ff7": [],
    "bitboss": [],
}


def get_mock_actions(template_name: str) -> List[Dict[str, Any]]:
    """Return mock toolbar entries for ``template_name`` (may be empty)."""
    return list(_MOCKS.get(template_name, []))
