# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Twitch polls for chatbot polls: limits, the scope gate, Helix create / end, EventSub updates.

A chatbot poll with "Also run it on Twitch" on creates a Twitch poll when it starts. Votes then
come from Twitch (EventSub ``channel.poll.progress`` / ``channel.poll.end``) instead of chat
keywords, so both show the same numbers. Twitch runs one poll per channel at a time, and
``channel:manage:polls`` is optional for the sign-in (see twitch_token_auth.OPTIONAL_SCOPES).
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

POLL_SCOPE = "channel:manage:polls"
HELIX_POLLS = "https://api.twitch.tv/helix/polls"

# Limits from Twitch's Create Poll reference.
TITLE_MAX = 60
CHOICE_TITLE_MAX = 25
CHOICES_MIN = 2
CHOICES_MAX = 5
DURATION_MIN = 15
DURATION_MAX = 1800
POINTS_MIN = 1
POINTS_MAX = 1_000_000

_CALL_TIMEOUT = 20.0


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
        logger.debug("Twitch poll scope check failed", exc_info=True)
        return "unknown"
    if granted is None:
        return "unknown"
    return "ok" if POLL_SCOPE in granted else "missing"


def scope_missing() -> bool:
    return scope_status() == "missing"


def readiness_message(status: str) -> str:
    return {
        "missing": "Twitch polls need a permission your Twitch sign-in doesn't have yet. Sign in to Twitch again "
                   "(Settings → Twitch → Connect) to use them.",
        "offline": "Twitch isn't connected, so the Twitch poll can't be created.",
        "unknown": "Mycelian is still checking your Twitch sign-in. Try again in a moment.",
    }.get(status, "")


def notify_scope_missing() -> None:
    """Ask the user to sign in again (once per session); called after Twitch connects without the scope."""
    try:
        from .notification_engine import nav_actions_settings, notify

        notify(
            "Twitch polls stay off until you sign in to Twitch again. Everything else works as before.",
            type="warning",
            source="twitch",
            title="Sign in to Twitch again to use Twitch polls",
            hint="Open Settings → Twitch and click Connect, then approve Mycelian in your browser.",
            actions=nav_actions_settings("Twitch"),
            dedupe_key="twitch:poll_scope_missing",
            dedupe_cooldown_sec=6 * 3600.0,
        )
    except Exception:
        logger.debug("Could not show the Twitch poll sign-in notice", exc_info=True)


# ---------------------------------------------------------------------------
#  Validation
# ---------------------------------------------------------------------------


def config_problems(config: Dict[str, Any]) -> List[str]:
    """Why this poll can't run on Twitch as set up (empty when it can)."""
    problems: List[str] = []
    title = str(config.get("twitch_title") or "").strip()
    if not title:
        problems.append("Give the Twitch poll a question.")
    elif len(title) > TITLE_MAX:
        problems.append(f"The Twitch poll question can be at most {TITLE_MAX} characters.")
    options = config.get("categories") or []
    if not CHOICES_MIN <= len(options) <= CHOICES_MAX:
        problems.append(f"A Twitch poll needs {CHOICES_MIN} to {CHOICES_MAX} options.")
    long_names = [str(o.get("name") or "") for o in options if len(str(o.get("name") or "")) > CHOICE_TITLE_MAX]
    if long_names:
        problems.append(f"Twitch option names can be at most {CHOICE_TITLE_MAX} characters ({long_names[0]}).")
    duration = int(config.get("duration_seconds") or 0)
    if not DURATION_MIN <= duration <= DURATION_MAX:
        problems.append("A Twitch poll runs for 15 seconds to 30 minutes, so set a timer in that range.")
    return problems


def clamp_duration(seconds: int) -> int:
    seconds = int(seconds or 0)
    if seconds <= 0:
        return 180
    return max(DURATION_MIN, min(DURATION_MAX, seconds))


def clamp_points(value: Any) -> int:
    try:
        return max(POINTS_MIN, min(POINTS_MAX, int(value)))
    except (TypeError, ValueError):
        return 100


# ---------------------------------------------------------------------------
#  Helix
# ---------------------------------------------------------------------------


def _run(coro, timeout: float = _CALL_TIMEOUT) -> Any:
    """Run a coroutine on its own loop in a worker thread (safe from any thread, even a running loop)."""
    box: Dict[str, Any] = {}

    def worker() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - reported to the caller
            box["error"] = exc

    thread = threading.Thread(target=worker, name="TwitchPollCall", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError("Twitch didn't answer in time")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _broadcaster_and_api():
    from . import twitch

    api = twitch.get_twitch_api()
    if api is None or not getattr(api, "is_connected", False):
        raise RuntimeError("Twitch is not connected")
    user_id = str(getattr(api, "user_id", "") or "")
    if not user_id:
        raise RuntimeError("The Twitch account isn't known yet")
    return api, user_id


def _twitch_error_text(exc: BaseException) -> str:
    text = str(exc) or type(exc).__name__
    lowered = text.lower()
    if "partner" in lowered or "affiliate" in lowered:
        return "Twitch only lets partners and affiliates run polls."
    if "active" in lowered and "poll" in lowered:
        return "A poll is already running on your Twitch channel. End it there first."
    if "401" in lowered or "scope" in lowered:
        return readiness_message("missing")
    return f"Twitch said: {text}"


def create_poll(title: str, choices: List[str], duration: int, *, points_enabled: bool = False,
                points_per_vote: int = 0) -> Tuple[bool, Any]:
    """Create the Twitch poll. (True, poll dict with id + choices) or (False, readable error)."""
    body: Dict[str, Any] = {
        "title": str(title)[:TITLE_MAX],
        "choices": [{"title": str(c)[:CHOICE_TITLE_MAX]} for c in choices],
        "duration": max(DURATION_MIN, min(DURATION_MAX, int(duration))),
    }
    if points_enabled:
        body["channel_points_voting_enabled"] = True
        body["channel_points_per_vote"] = clamp_points(points_per_vote)
    try:
        api, user_id = _broadcaster_and_api()
        body["broadcaster_id"] = user_id
        data = _run(api.generic_api_call(HELIX_POLLS, "POST", json_data=body))
    except Exception as exc:
        logger.warning("Creating the Twitch poll failed: %s", exc)
        return False, _twitch_error_text(exc)
    rows = data.get("data") if isinstance(data, dict) else None
    poll = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
    if not poll or not poll.get("id"):
        return False, "Twitch didn't return the new poll."
    return True, poll


def choice_map(poll: Dict[str, Any], categories: List[Dict[str, Any]]) -> Dict[str, str]:
    """{Twitch choice id: option id}. Matches by title (as sent), then by position for any left over."""
    choices = [c for c in (poll.get("choices") or []) if isinstance(c, dict) and c.get("id")]
    mapping: Dict[str, str] = {}
    unused = list(categories)
    for choice in choices:
        title = str(choice.get("title") or "")
        match = next((cat for cat in unused if str(cat.get("name") or "")[:CHOICE_TITLE_MAX] == title), None)
        if match is not None:
            mapping[str(choice["id"])] = match["id"]
            unused.remove(match)
    for choice, cat in zip([c for c in choices if str(c["id"]) not in mapping], unused):
        mapping[str(choice["id"])] = cat["id"]
    return mapping


def end_poll(poll_id: str, *, show_results: bool = True) -> None:
    """End a Twitch poll in the background (TERMINATED shows the results in chat, ARCHIVED hides it)."""
    poll_id = str(poll_id or "").strip()
    if not poll_id:
        return

    def worker() -> None:
        try:
            api, user_id = _broadcaster_and_api()
            body = {"broadcaster_id": user_id, "id": poll_id, "status": "TERMINATED" if show_results else "ARCHIVED"}
            _run(api.generic_api_call(HELIX_POLLS, "PATCH", json_data=body))
        except Exception as exc:
            logger.warning("Ending the Twitch poll failed: %s", exc)
            try:
                from .notification_engine import notify

                notify("The poll ended in Mycelian but may still be open on Twitch.", type="warning", source="chatbot",
                       title="Couldn't end the Twitch poll", hint="End it from your Twitch dashboard or chat.",
                       details=f"{type(exc).__name__}: {exc}", dedupe_key="twitch:poll_end_failed")
            except Exception:
                pass

    threading.Thread(target=worker, name="TwitchPollEnd", daemon=True).start()


# ---------------------------------------------------------------------------
#  EventSub
# ---------------------------------------------------------------------------


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def event_choice_votes(event: Any) -> Dict[str, int]:
    """{twitch choice id: total votes} from a poll progress / end event."""
    votes: Dict[str, int] = {}
    for choice in _value(event, "choices", None) or []:
        choice_id = str(_value(choice, "id", "") or "")
        if not choice_id:
            continue
        try:
            votes[choice_id] = max(0, int(_value(choice, "votes", 0) or 0))
        except (TypeError, ValueError):
            votes[choice_id] = 0
    return votes


def parse_timestamp(value: Any) -> Optional[float]:
    """Unix time from a Twitch timestamp ("2021-03-19T06:08:33.871278372Z" or a datetime)."""
    if value in (None, ""):
        return None
    if hasattr(value, "timestamp"):
        try:
            return float(value.timestamp())
        except Exception:
            return None
    try:
        from datetime import datetime

        text = str(value).strip().replace("Z", "+00:00")
        # Twitch sends nanoseconds; Python reads up to microseconds.
        text = re.sub(r"(\.\d{6})\d+", r"\1", text)
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def handle_begin(event: Any) -> None:
    from .poll_manager import get_poll_manager

    get_poll_manager().twitch_poll_began(str(_value(event, "id", "") or ""), parse_timestamp(_value(event, "ends_at", None)))


def handle_progress(event: Any) -> None:
    from .poll_manager import get_poll_manager

    get_poll_manager().apply_twitch_votes(str(_value(event, "id", "") or ""), event_choice_votes(event))


def handle_end(event: Any) -> None:
    from .poll_manager import get_poll_manager

    status = _value(event, "status", "")
    status = str(getattr(status, "value", status) or "").lower()
    get_poll_manager().twitch_poll_ended(str(_value(event, "id", "") or ""), event_choice_votes(event), status)
