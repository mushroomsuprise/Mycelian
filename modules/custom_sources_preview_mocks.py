#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Per-template mock Socket.IO actions for the Custom Sources preview toolbar."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

from .path_utils import get_template_path

logger = logging.getLogger(__name__)

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
        {"event": "new-message", "label": "Chat reply", "alert_type": "reply"},
        {"event": "message_moderation", "label": "Moderation"},
        {"event": "activity_feed_alert", "label": "Alert (random)"},
        {"event": "activity_feed_alert", "label": "Alert: Sub", "alert_type": "sub"},
        {"event": "activity_feed_alert", "label": "Alert: Bits", "alert_type": "bit"},
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


def _load_preview_mocks_from_config(config_name: str) -> List[Dict[str, Any]]:
    """Read ``preview_mocks`` from ``templates/template_configs/{config_name}.json``."""
    if not config_name:
        return []
    path = get_template_path(
        os.path.join("template_configs", f"{config_name}.json")
    )
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("Could not load preview_mocks from %s: %s", path, e)
        return []
    if not isinstance(cfg, dict):
        return []
    raw = cfg.get("preview_mocks")
    if not isinstance(raw, list) or not raw:
        return []
    out: List[Dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        ev = entry.get("event")
        if not ev or not isinstance(ev, str):
            continue
        act: Dict[str, Any] = {
            "event": ev.strip(),
            "label": str(entry.get("label") or ev),
        }
        at = entry.get("alert_type")
        if at and isinstance(at, str):
            act["alert_type"] = at.strip()
        if entry.get("tooltip"):
            act["tooltip"] = str(entry["tooltip"])
        out.append(act)
    return out


def get_mock_actions(config_name: str) -> List[Dict[str, Any]]:
    """
    Return mock toolbar entries for a template config stem.

    Spore Studio templates store ``preview_mocks`` in their public JSON;
    legacy templates fall back to the static ``_MOCKS`` map keyed by route name.
    """
    from_config = _load_preview_mocks_from_config(config_name)
    if from_config:
        return from_config
    return list(_MOCKS.get(config_name, []))
