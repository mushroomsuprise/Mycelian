# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Twitch Predictions: limits, the scope gate, Helix create / lock / resolve / cancel, EventSub updates.

Chatbot → Predictions starts a prediction on Twitch; viewers bet channel points on Twitch, and
EventSub (``channel.prediction.begin`` / ``progress`` / ``lock`` / ``end``) keeps the prediction
manager and the overlays in step. Predictions started from the Twitch dashboard show up too.
Twitch runs one prediction per channel at a time and only for partners and affiliates.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from .twitch_polls import _broadcaster_and_api, _run, _value, check_scopes_in_background, parse_timestamp

logger = logging.getLogger(__name__)

PREDICTION_SCOPE = "channel:manage:predictions"
HELIX_PREDICTIONS = "https://api.twitch.tv/helix/predictions"

# Limits from Twitch's Create Prediction reference.
TITLE_MAX = 45
OUTCOME_TITLE_MAX = 25
OUTCOMES_MIN = 2
OUTCOMES_MAX = 10
WINDOW_MIN = 30
WINDOW_MAX = 1800

# Twitch's own outcome colours (first outcome blue, the rest pink).
OUTCOME_COLORS = ("blue", "pink")


# ---------------------------------------------------------------------------
#  Readiness (connection + scope)
# ---------------------------------------------------------------------------


def scope_status() -> str:
    """"ok", "missing" (sign in again), "offline" (Twitch not connected) or "unknown" (not validated yet)."""
    try:
        from . import twitch
        from .twitch_token_auth import token_scopes

        api = twitch.get_twitch_api()
        if api is None or not getattr(api, "is_connected", False) or not getattr(api, "auth_token", ""):
            return "offline"
        granted = token_scopes(api.auth_token)
    except Exception:
        logger.debug("Twitch prediction scope check failed", exc_info=True)
        return "unknown"
    if granted is None:
        return "unknown"
    return "ok" if PREDICTION_SCOPE in granted else "missing"


def readiness() -> str:
    """scope_status(), plus "not_eligible" when the channel isn't a partner or affiliate."""
    status = scope_status()
    if status == "unknown":
        check_scopes_in_background()
        return status
    if status != "ok":
        return status
    try:
        from . import twitch

        user = getattr(twitch.get_twitch_api(), "user", None)
        kind = getattr(user, "broadcaster_type", None)
    except Exception:
        kind = None
    if kind is not None and str(getattr(kind, "value", kind) or "").strip() == "":
        return "not_eligible"
    return "ok"


def readiness_message(status: str) -> str:
    return {
        "missing": "Predictions need a permission your Twitch sign-in doesn't have yet. Sign in to Twitch again "
                   "(Settings → Twitch → Connect) to use them.",
        "offline": "Twitch isn't connected, so the prediction can't be started.",
        "unknown": "Mycelian is still checking your Twitch sign-in. Try again in a moment.",
        "not_eligible": "Twitch only lets partners and affiliates run predictions, so this channel can't run one yet.",
    }.get(status, "")


def notify_scope_missing() -> None:
    """Ask the user to sign in again (once per session); called after Twitch connects without the scope."""
    try:
        from .notification_engine import nav_actions_settings, notify

        notify(
            "Predictions stay off until you sign in to Twitch again. Everything else works as before.",
            type="warning",
            source="twitch",
            title="Sign in to Twitch again to use predictions",
            hint="Open Settings → Twitch and click Connect, then approve Mycelian in your browser.",
            actions=nav_actions_settings("Twitch"),
            dedupe_key="twitch:prediction_scope_missing",
            dedupe_cooldown_sec=6 * 3600.0,
        )
    except Exception:
        logger.debug("Could not show the prediction sign-in notice", exc_info=True)


# ---------------------------------------------------------------------------
#  Validation
# ---------------------------------------------------------------------------


def clamp_title(text: Any) -> str:
    return str(text or "").strip()[:TITLE_MAX].rstrip()


def clamp_outcome_name(text: Any) -> str:
    return str(text or "").strip()[:OUTCOME_TITLE_MAX].rstrip()


def clamp_window(seconds: Any) -> int:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return 120
    return max(WINDOW_MIN, min(WINDOW_MAX, seconds))


def config_problems(config: Dict[str, Any]) -> List[str]:
    """Why this prediction can't start on Twitch as set up (empty when it can)."""
    problems: List[str] = []
    title = str(config.get("title") or "").strip()
    if not title:
        problems.append("Give the prediction a question.")
    elif len(title) > TITLE_MAX:
        problems.append(f"The question can be at most {TITLE_MAX} characters.")
    names = [str(o.get("name") or "").strip() for o in (config.get("outcomes") or [])]
    if not OUTCOMES_MIN <= len(names) <= OUTCOMES_MAX:
        problems.append(f"A prediction needs {OUTCOMES_MIN} to {OUTCOMES_MAX} outcomes.")
    if any(not n for n in names):
        problems.append("Every outcome needs a name.")
    long_names = [n for n in names if len(n) > OUTCOME_TITLE_MAX]
    if long_names:
        problems.append(f"Outcome names can be at most {OUTCOME_TITLE_MAX} characters ({long_names[0]}).")
    lowered = [n.lower() for n in names if n]
    if len(set(lowered)) != len(lowered):
        problems.append("Two outcomes have the same name.")
    window = int(config.get("window_seconds") or 0)
    if not WINDOW_MIN <= window <= WINDOW_MAX:
        problems.append("Predictions take bets for 30 seconds to 30 minutes, so set the time in that range.")
    return problems


# ---------------------------------------------------------------------------
#  Helix
# ---------------------------------------------------------------------------


def _twitch_error_text(exc: BaseException) -> str:
    text = str(exc) or type(exc).__name__
    lowered = text.lower()
    if "partner" in lowered or "affiliate" in lowered:
        return "Twitch only lets partners and affiliates run predictions."
    if "active" in lowered and "prediction" in lowered:
        return "A prediction is already running on your Twitch channel. End it there first."
    if "401" in lowered or "scope" in lowered:
        return readiness_message("missing")
    return f"Twitch said: {text}"


def _first_row(data: Any) -> Optional[Dict[str, Any]]:
    rows = data.get("data") if isinstance(data, dict) else None
    row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
    return row if row and row.get("id") else None


def create_prediction(title: str, outcomes: List[str], window_seconds: int) -> Tuple[bool, Any]:
    """Start a prediction on Twitch. (True, prediction dict) or (False, readable error)."""
    body: Dict[str, Any] = {
        "title": clamp_title(title),
        "outcomes": [{"title": clamp_outcome_name(o)} for o in outcomes],
        "prediction_window": clamp_window(window_seconds),
    }
    try:
        api, user_id = _broadcaster_and_api()
        body["broadcaster_id"] = user_id
        data = _run(api.generic_api_call(HELIX_PREDICTIONS, "POST", json_data=body))
    except Exception as exc:
        logger.warning("Creating the Twitch prediction failed: %s", exc)
        return False, _twitch_error_text(exc)
    row = _first_row(data)
    if row is None:
        return False, "Twitch didn't return the new prediction."
    return True, row


def end_prediction(prediction_id: str, status: str, winning_outcome_id: Optional[str] = None) -> Tuple[bool, str]:
    """Lock ("LOCKED"), resolve ("RESOLVED" + winner) or cancel and refund ("CANCELED") a prediction."""
    status = str(status or "").upper()
    if status not in ("LOCKED", "RESOLVED", "CANCELED"):
        return False, f"Unknown prediction status {status!r}."
    if status == "RESOLVED" and not winning_outcome_id:
        return False, "Pick the outcome that won."
    try:
        api, user_id = _broadcaster_and_api()
        body: Dict[str, Any] = {"broadcaster_id": user_id, "id": str(prediction_id), "status": status}
        if status == "RESOLVED":
            body["winning_outcome_id"] = str(winning_outcome_id)
        _run(api.generic_api_call(HELIX_PREDICTIONS, "PATCH", json_data=body))
    except Exception as exc:
        logger.warning("Updating the Twitch prediction (%s) failed: %s", status, exc)
        return False, _twitch_error_text(exc)
    return True, ""


def get_current_prediction() -> Optional[Dict[str, Any]]:
    """The channel's latest prediction from Twitch (any status), or None."""
    try:
        api, user_id = _broadcaster_and_api()
        data = _run(api.generic_api_call(HELIX_PREDICTIONS, "GET", params={"broadcaster_id": user_id, "first": 1}))
    except Exception as exc:
        logger.debug("Reading the current Twitch prediction failed: %s", exc)
        return None
    return _first_row(data)


# ---------------------------------------------------------------------------
#  Normalising Helix rows and EventSub events
# ---------------------------------------------------------------------------


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _status_text(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def normalize(prediction: Any, *, kind: str = "") -> Dict[str, Any]:
    """A plain dict from a Helix prediction row or an EventSub prediction event.

    ``kind`` is the EventSub topic ("begin", "progress", "lock", "end") when it is an event;
    the status then comes from the topic (EventSub only sends one on ``end``).
    """
    outcomes = []
    for index, outcome in enumerate(_value(prediction, "outcomes", None) or []):
        top = []
        for p in _value(outcome, "top_predictors", None) or []:
            won = _value(p, "channel_points_won", None)
            top.append({
                "name": str(_value(p, "user_name", None) or _value(p, "user_login", None) or ""),
                "points_used": _int(_value(p, "channel_points_used", 0)),
                "points_won": None if won is None else _int(won),
            })
        color = _status_text(_value(outcome, "color", "")) or OUTCOME_COLORS[min(index, 1)]
        outcomes.append({
            "id": str(_value(outcome, "id", "") or ""),
            "name": str(_value(outcome, "title", "") or ""),
            "color": color,
            "users": _int(_value(outcome, "users", 0)),
            "points": _int(_value(outcome, "channel_points", 0)),
            "top_predictors": top,
        })
    raw_status = _status_text(_value(prediction, "status", ""))
    if kind in ("begin", "progress"):
        status = "active"
    elif kind == "lock":
        status = "locked"
    else:
        status = {"active": "active", "locked": "locked", "resolved": "resolved", "canceled": "canceled"}.get(
            raw_status, raw_status or "active")
    created = _value(prediction, "created_at", None) or _value(prediction, "started_at", None)
    window = _value(prediction, "prediction_window", None)
    locks_at = parse_timestamp(_value(prediction, "locks_at", None))
    started_at = parse_timestamp(created)
    if locks_at is None and started_at is not None and window:
        locks_at = started_at + _int(window)
    ended_at = parse_timestamp(_value(prediction, "ended_at", None))
    locked_at = parse_timestamp(_value(prediction, "locked_at", None))
    if status == "locked" and locked_at is None:
        locked_at = time.time()
    return {
        "id": str(_value(prediction, "id", "") or ""),
        "title": str(_value(prediction, "title", "") or ""),
        "status": status,
        "outcomes": outcomes,
        "started_at": started_at,
        "locks_at": locks_at,
        "locked_at": locked_at,
        "ended_at": ended_at if status in ("resolved", "canceled") else None,
        "winning_outcome_id": str(_value(prediction, "winning_outcome_id", "") or "") or None,
    }


def handle_event(kind: str, event: Any) -> None:
    """EventSub entry point (begin / progress / lock / end); runs off the EventSub loop."""
    from .prediction_manager import get_prediction_manager

    get_prediction_manager().apply_twitch_update(normalize(event, kind=kind), kind=kind)
