# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Twitch Creator Goals: the channel's active goals, overlay updates, and goal-bar overlays.

Goals come from Helix (``GET /goals``, read when Twitch connects and on request) and EventSub
(``channel.goal.begin`` / ``progress`` / ``end``). Overlays get one event per goal:
``twitch_goal_begin``, ``twitch_goal_progress``, ``twitch_goal_end``, and ``twitch_goal_sync`` in
reply to ``twitch_goal_request_state``. ``create_goal_template`` builds a Spore Studio overlay
(label, progress bar, amount) whose data feeds follow the goal of one type, so it keeps working
when a goal ends and a new one of the same type starts. Needs ``channel:read:goals``.
"""

from __future__ import annotations

import copy
import logging
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from .twitch_polls import _broadcaster_and_api, _run, _value, check_scopes_in_background, parse_timestamp

logger = logging.getLogger(__name__)

GOAL_SCOPE = "channel:read:goals"
HELIX_GOALS = "https://api.twitch.tv/helix/goals"

GOAL_TYPES: Dict[str, str] = {
    "follower": "Followers",
    "subscription": "Sub points",
    "subscription_count": "Subscribers",
    "new_subscription": "New sub points",
    "new_subscription_count": "New subscribers",
}
GOAL_EVENTS = ("twitch_goal_sync", "twitch_goal_begin", "twitch_goal_progress", "twitch_goal_end")

_lock = threading.RLock()
_goals: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
#  Readiness
# ---------------------------------------------------------------------------


def scope_status() -> str:
    """"ok", "missing" (sign in again), "offline" or "unknown" (not validated yet)."""
    try:
        from . import twitch
        from .twitch_token_auth import token_scopes

        api = twitch.get_twitch_api()
        if api is None or not getattr(api, "is_connected", False) or not getattr(api, "auth_token", ""):
            return "offline"
        granted = token_scopes(api.auth_token)
    except Exception:
        logger.debug("Twitch goal scope check failed", exc_info=True)
        return "unknown"
    if granted is None:
        check_scopes_in_background()
        return "unknown"
    return "ok" if GOAL_SCOPE in granted else "missing"


def notify_scope_missing() -> None:
    try:
        from .notification_engine import nav_actions_settings, notify

        notify(
            "Goal overlays stay still until you sign in to Twitch again. Everything else works as before.",
            type="warning",
            source="twitch",
            title="Sign in to Twitch again to use Twitch goals",
            hint="Open Settings → Twitch and click Connect, then approve Mycelian in your browser.",
            actions=nav_actions_settings("Twitch"),
            dedupe_key="twitch:goal_scope_missing",
            dedupe_cooldown_sec=6 * 3600.0,
        )
    except Exception:
        logger.debug("Could not show the goal sign-in notice", exc_info=True)


# ---------------------------------------------------------------------------
#  Goal state
# ---------------------------------------------------------------------------


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def normalize(goal: Any, *, ended: bool = False) -> Dict[str, Any]:
    """The overlay payload for a Helix goal row or an EventSub goal event."""
    current = _int(_value(goal, "current_amount", 0))
    target = _int(_value(goal, "target_amount", 0))
    goal_type = str(getattr(_value(goal, "type", ""), "value", _value(goal, "type", "")) or "")
    achieved = _value(goal, "is_achieved", None)
    started = _value(goal, "started_at", None) or _value(goal, "created_at", None)
    return {
        "id": str(_value(goal, "id", "") or ""),
        "type": goal_type,
        "type_label": GOAL_TYPES.get(goal_type, goal_type.replace("_", " ").title()),
        "description": str(_value(goal, "description", "") or ""),
        "current_amount": current,
        "target_amount": target,
        "percent": round(min(100.0, current * 100 / target), 1) if target else 0.0,
        "is_achieved": bool(achieved) if achieved is not None else (bool(target) and current >= target),
        "status": "ended" if ended else "active",
        "started_at": parse_timestamp(started),
        "ended_at": parse_timestamp(_value(goal, "ended_at", None)) if ended else None,
    }


def active_goals() -> List[Dict[str, Any]]:
    with _lock:
        return [copy.deepcopy(g) for g in _goals.values()]


def _emit(event: str, payload: Dict[str, Any], to: Optional[str] = None) -> None:
    try:
        from . import web_engine

        instance = getattr(web_engine, "web_engine_instance", None)
        if instance:
            if to:
                instance.safe_emit(event, payload, to=to)
            else:
                instance.safe_emit(event, payload)
    except Exception as exc:
        logger.debug("goal emit %s: %s", event, exc)


def handle_event(kind: str, event: Any) -> None:
    """EventSub entry point: kind is "begin", "progress" or "end"."""
    goal = normalize(event, ended=kind == "end")
    if not goal["id"]:
        return
    with _lock:
        if kind == "end":
            _goals.pop(goal["id"], None)
        else:
            _goals[goal["id"]] = goal
    _emit(f"twitch_goal_{kind}", goal)


def refresh_from_twitch() -> Tuple[bool, str]:
    """Read the active goals from Twitch, replace the known ones and update the overlays."""
    try:
        api, user_id = _broadcaster_and_api()
        data = _run(api.generic_api_call(HELIX_GOALS, "GET", params={"broadcaster_id": user_id}))
    except Exception as exc:
        logger.debug("Reading Twitch goals failed: %s", exc)
        text = str(exc)
        if "401" in text or "scope" in text.lower():
            return False, "Mycelian needs permission to read goals. Sign in to Twitch again (Settings → Twitch → Connect)."
        return False, f"Couldn't read your Twitch goals: {text or type(exc).__name__}"
    rows = data.get("data") if isinstance(data, dict) else None
    goals = [normalize(row) for row in rows or [] if isinstance(row, dict)]
    with _lock:
        _goals.clear()
        for goal in goals:
            if goal["id"]:
                _goals[goal["id"]] = goal
    for goal in goals:
        _emit("twitch_goal_sync", goal)
    return True, ""


def send_state(to: Optional[str]) -> None:
    """Answer an overlay's ``twitch_goal_request_state`` with one ``twitch_goal_sync`` per goal."""
    for goal in active_goals():
        _emit("twitch_goal_sync", goal, to=to)


# ---------------------------------------------------------------------------
#  Goal-bar overlays (Spore Studio)
# ---------------------------------------------------------------------------


_ANIM = {"anim_delay_ms": 0, "anim_easing": "ease-out", "anim_in": "none", "anim_in_ms": 300, "anim_out": "none", "anim_out_ms": 300}


def _feed(feed_id: str, label: str, goal_type: str, target: str, mapping: Dict[str, Any]) -> Dict[str, Any]:
    base_mapping = {"text_field": "", "value_field": "", "template": "", "split_field": "", "prefix": "", "suffix": "",
                    "decimals": "", "fallback": ""}
    base_mapping.update(mapping)
    return {
        "id": feed_id,
        "enabled": True,
        "label": label,
        "source": {"kind": "event", "events": list(GOAL_EVENTS),
                   "template_output": {"template": "", "event": "", "match": {}},
                   "value": {"kind": "data_source", "source": ""}, "refresh_ms": 0, "refresh_on": []},
        "filters": {"alert_type": "any", "tier": "any", "rules": [{"field": "type", "op": "eq", "value": goal_type}],
                    "match": "all", "users_allow": "", "users_block": "", "cooldown_ms": 0, "max_per_minute": 0,
                    "dedupe_ms": 0, "skip_first": 0},
        "mapping": base_mapping,
        "timing": {"delay_ms": 0},
        "target": {"id": target, "options": {}},
    }


def goal_template_model(name: str, goal_type: str, label: str = "") -> Dict[str, Any]:
    """A Spore Studio model: label, progress bar and "current / target", fed by the goal events."""
    title = label or GOAL_TYPES.get(goal_type, "Goal")
    text_props = {"color": "#ffffff", "font_family": "Renogare.ttf", "font_weight": "normal",
                  "text_shadow": "2px 2px 4px rgba(0,0,0,0.8)", "vertical_align": "center", "white_space": "nowrap",
                  "overflow": "hidden", "text_overflow": "ellipsis"}
    text_expose = {"color": True, "font_family": True, "font_size": True, "font_weight": True, "text": True}

    def element(eid, etype, category, x, y, w, h, props, expose, feeds, **extra):
        el = {"animations": dict(_ANIM), "bindings": [], "category": category, "id": eid, "position": {"x": x, "y": y},
              "size": {"w": w, "h": h}, "props": props, "source_settings_expose": expose, "type": etype, "data_feed": feeds}
        el.update(extra)
        return el

    return {
        "template_name": name,
        "alert_system": "instant",
        "design": {"width": 600, "height": 96},
        "duration_seconds": 5.0,
        "queued": False,
        "advanced_js": "",
        "streamdeck_options": {"description": "", "actions": {}},
        "dynamic_controls": {"elements": []},
        "elements": [
            element("goal_label", "text", "Goal Label", 12, 6, 400, 30, {**text_props, "text": title, "font_size": 20},
                    text_expose, [_feed("feed1", "Goal description", goal_type, "set_text",
                                        {"template": "{description|default:" + title + "}"})]),
            element("goal_amount", "text", "Goal Amount", 412, 6, 176, 30,
                    {**text_props, "text": "0 / 0", "font_size": 18, "text_align": "right"}, dict(text_expose, text_align=True),
                    [_feed("feed1", "Current / target", goal_type, "set_text",
                           {"template": "{current_amount|number} / {target_amount|number}"})]),
            element("goal_bar", "progress_bar", "Goal Bar", 12, 44, 576, 36,
                    {"border_color": "#000000", "border_radius": 12, "border_width": 0, "fill_animation_duration_ms": 800,
                     "fill_animation_easing": "ease-out", "fill_color": "#9146ff", "track_color": "#1e293b",
                     "near_goal_threshold": 90, "near_goal_effect": "shimmer"},
                    {"fill_color": True, "track_color": True, "border_radius": True, "border_width": True,
                     "border_color": True, "near_goal_effect": True, "fill_animation_duration_ms": True},
                    [_feed("feed1", "Current amount", goal_type, "set_value", {"value_field": "current_amount"}),
                     _feed("feed2", "Target amount", goal_type, "set_max", {"value_field": "target_amount"})],
                    progress_source={"counter_id": "", "max_kind": "fixed", "max": 100, "max_counter_id": ""}),
        ],
    }


def suggested_template_name(goal_type: str) -> str:
    """A free template name like ``twitch_goal_followers`` (``_2``, ``_3``... when taken)."""
    base = "twitch_goal_" + re.sub(r"[^a-z0-9_]+", "_", GOAL_TYPES.get(goal_type, goal_type or "goal").lower()).strip("_")
    try:
        import os

        from .path_utils import get_template_path

        def taken(name: str) -> bool:
            return os.path.exists(get_template_path(f"{name}.html"))
    except Exception:
        def taken(name: str) -> bool:
            return False
    name, n = base, 2
    while taken(name):
        name, n = f"{base}_{n}", n + 1
    return name


def create_goal_template(goal_type: str, *, name: str = "", label: str = "") -> Tuple[bool, str]:
    """Build and save a goal-bar overlay for ``goal_type``. (True, template name) or (False, readable error)."""
    if goal_type not in GOAL_TYPES:
        return False, f"Unknown goal type {goal_type!r}."
    name = (name or suggested_template_name(goal_type)).strip()
    try:
        from .spore_studio.save_pipeline import save_template

        save_template(goal_template_model(name, goal_type, label))
    except Exception as exc:
        logger.warning("Creating the goal overlay %s failed: %s", name, exc)
        return False, str(exc) or type(exc).__name__
    # Fill the new overlay straight away when the goal is already known.
    for goal in active_goals():
        if goal["type"] == goal_type:
            _emit("twitch_goal_sync", goal)
    return True, name


def reset_for_tests() -> None:
    with _lock:
        _goals.clear()


__all__ = [
    "GOAL_EVENTS", "GOAL_SCOPE", "GOAL_TYPES", "active_goals", "create_goal_template", "goal_template_model",
    "handle_event", "normalize", "notify_scope_missing", "refresh_from_twitch", "scope_status", "send_state",
    "suggested_template_name",
]
