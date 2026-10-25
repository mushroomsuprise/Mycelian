# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Twitch lookups for chatbot commands and events (no NiceGUI).

Two modes, both using the live Twitch connection (broadcaster token; ids,
client id, tokens and headers are filled in automatically):

* Curated lookups (:data:`LOOKUPS`): plain-language actions such as
  "follow age" or "shout out a channel". Stored on an item as
  ``{"id": "<lookup id>", "target": {...}, "text": "<template>", ...}``.
* Advanced raw requests (``id == "advanced"``): any Helix endpoint,
  ``{"id": "advanced", "method", "path", "params", "body", "processing"}``.

Results are a flat dict of variables merged into the reply context, plus:

* ``lookup_ok`` (bool) -- the lookup did what it should;
* ``lookup_reason`` -- ``ok`` | ``not_following`` | ``no_target`` |
  ``not_found`` | ``not_connected`` | ``missing_scope`` | ``unknown`` |
  ``no_text`` | ``error``;
* ``lookup_error`` -- human readable error text ("" on success).

What gets posted afterwards is decided by
:func:`modules.chatbot_core.choose_lookup_outcome` (fallback text replaces the
reply on failure; follow_age "not following" without fallback still posts the
normal reply; other failures without fallback post nothing).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, quote, urlsplit

from .chatbot_core import (
    TEMPLATE_TOKEN,
    render_template,
    resolve_template_token,
)
from .chatbot_custom_variables import evaluate_expression, friendly_duration, to_local_naive
from .connector_placeholders import (
    _ctx_lookup_found,
    _json_bare_value,
    _json_scan,
    portable_strftime,
)

logger = logging.getLogger(__name__)

HELIX = "https://api.twitch.tv/helix"
ADVANCED_ID = "advanced"
LOOKUP_TIMEOUT_SECONDS = 20.0
_LOGIN_RE = re.compile(r"^[a-z0-9_]{1,25}$")


# --------------------------------------------------------------------------
# Date values
# --------------------------------------------------------------------------


class FriendlyDate(datetime):
    """Date variable: prints ``March 14, 2024``; accepts ``{var:%d/%m/%Y}``."""

    def __str__(self) -> str:
        return portable_strftime(self, "%B %-d, %Y")


class FriendlyDateTime(datetime):
    """Date-time variable: prints ``March 14, 2024 19:30``."""

    def __str__(self) -> str:
        return portable_strftime(self, "%B %-d, %Y %H:%M")


def _date_value(raw: Any, cls=FriendlyDate) -> Any:
    moment = to_local_naive(raw)
    if moment is None:
        return ""
    return cls(moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second)


_EXAMPLE_DATE = FriendlyDate(2024, 3, 14, 19, 30)
_EXAMPLE_DATETIME = FriendlyDateTime(2026, 3, 14, 18, 2)


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------


def _var(key: str, label: str, example: Any, date: bool = False) -> Dict[str, Any]:
    return {"key": key, "label": label, "example": example, "date": date}


_TARGET_VARS = [
    _var("target_name", "Channel name", "CoolStreamer"),
    _var("target_login", "Channel login", "coolstreamer"),
    _var("target_game", "Channel's game", "Just Chatting"),
    _var("target_title", "Channel's stream title", "Chill vibes"),
    _var("target_url", "Channel link", "https://twitch.tv/coolstreamer"),
    _var("target_live", "Is live now (yes/no)", "no"),
]

LOOKUPS: List[Dict[str, Any]] = [
    {
        "id": "follow_age",
        "label": "How long someone has followed",
        "group": "Viewers",
        "hint": "Follow age of the viewer (or the user they name).",
        "target": "user",
        "text_arg": None,
        "options": [],
        "variables": [
            _var("followage", "Follow age", "2 years, 3 months"),
            _var("followed_since", "Followed since", _EXAMPLE_DATE, date=True),
            _var("follow_days", "Days followed", "812"),
            _var("is_following", "Is following (yes/no)", "yes"),
            _var("target_name", "User name", "TestViewer"),
        ],
        "scopes": ["moderator:read:followers"],
        "token": "broadcaster",
    },
    {
        "id": "account_age",
        "label": "How old an account is",
        "group": "Viewers",
        "hint": "When the viewer's Twitch account was created.",
        "target": "user",
        "text_arg": None,
        "options": [],
        "variables": [
            _var("account_age", "Account age", "5 years, 2 months"),
            _var("account_created", "Account created", FriendlyDate(2021, 1, 9), date=True),
            _var("account_age_days", "Account age in days", "1904"),
            _var("display_name", "Display name", "TestViewer"),
        ],
        "scopes": [],
        "token": "broadcaster",
    },
    {
        "id": "subscription",
        "label": "Someone's subscription",
        "group": "Viewers",
        "hint": "Whether the viewer subscribes, and at what tier.",
        "target": "user",
        "text_arg": None,
        "options": [],
        "variables": [
            _var("is_subscribed", "Is subscribed (yes/no)", "yes"),
            _var("sub_tier", "Tier (1-3, 0 = none)", "1"),
            _var("is_gift", "Gifted (yes/no)", "no"),
            _var("gifter", "Gifted by", ""),
            _var("target_name", "User name", "TestViewer"),
        ],
        "scopes": ["channel:read:subscriptions"],
        "token": "broadcaster",
    },
    {
        "id": "uptime",
        "label": "Stream uptime",
        "group": "Your stream",
        "hint": "How long you have been live, with viewers, title and game.",
        "target": None,
        "text_arg": None,
        "options": [],
        "variables": [
            _var("uptime", "Uptime", "3 hours, 12 minutes"),
            _var("live_since", "Live since", _EXAMPLE_DATETIME, date=True),
            _var("is_live", "Is live (yes/no)", "yes"),
            _var("viewer_count", "Viewers", "42"),
            _var("stream_title", "Stream title", "Speedrun practice"),
            _var("stream_game", "Game", "Celeste"),
        ],
        "scopes": [],
        "token": "broadcaster",
    },
    {
        "id": "title_game",
        "label": "Current title and game",
        "group": "Your stream",
        "hint": "Your channel's title, game, tags and language.",
        "target": None,
        "text_arg": None,
        "options": [],
        "variables": [
            _var("title", "Title", "Speedrun practice"),
            _var("game", "Game", "Celeste"),
            _var("tags", "Tags", "English, Speedrun"),
            _var("language", "Language", "en"),
        ],
        "scopes": [],
        "token": "broadcaster",
    },
    {
        "id": "follower_count",
        "label": "Follower count",
        "group": "Your stream",
        "hint": "How many followers your channel has.",
        "target": None,
        "text_arg": None,
        "options": [],
        "variables": [_var("follower_count", "Followers", "1234")],
        "scopes": [],
        "token": "broadcaster",
    },
    {
        "id": "subscriber_count",
        "label": "Subscriber count",
        "group": "Your stream",
        "hint": "How many subscribers and sub points you have.",
        "target": None,
        "text_arg": None,
        "options": [],
        "variables": [
            _var("sub_count", "Subscribers", "87"),
            _var("sub_points", "Sub points", "102"),
        ],
        "scopes": ["channel:read:subscriptions"],
        "token": "broadcaster",
    },
    {
        "id": "top_cheerer",
        "label": "Top cheerer",
        "group": "Your stream",
        "hint": "Who has cheered the most bits.",
        "target": None,
        "text_arg": None,
        "options": [
            {
                "key": "period",
                "label": "Period",
                "choices": {"all": "All time", "year": "This year", "month": "This month",
                            "week": "This week", "day": "Today"},
                "default": "all",
            }
        ],
        "variables": [
            _var("top_cheerer", "Top cheerer", "BitsFan"),
            _var("top_cheer_amount", "Bits cheered", "25000"),
        ],
        "scopes": ["bits:read"],
        "token": "broadcaster",
    },
    {
        "id": "channel_info",
        "label": "Another channel's info",
        "group": "Other channels",
        "hint": "Name, game, title and link of a channel the viewer names.",
        "target": "channel",
        "text_arg": None,
        "options": [],
        "variables": list(_TARGET_VARS),
        "scopes": [],
        "token": "broadcaster",
    },
    {
        "id": "shoutout",
        "label": "Shout out a channel",
        "group": "Other channels",
        "hint": "Sends a Twitch shoutout (needs you to be live) and gives the channel's info.",
        "target": "channel",
        "text_arg": None,
        "options": [],
        "variables": [*_TARGET_VARS, _var("shoutout_sent", "Twitch shoutout sent (yes/no)", "yes")],
        "scopes": ["moderator:manage:shoutouts"],
        "token": "broadcaster",
    },
    {
        "id": "set_title",
        "label": "Change the stream title",
        "group": "Do something",
        "hint": "Sets your stream title.",
        "target": None,
        "text_arg": {"label": "New title", "default": "{command_message}",
                     "placeholder": "Title text, e.g. {command_message}"},
        "options": [],
        "variables": [_var("new_title", "New title", "Speedrun practice")],
        "scopes": ["channel:manage:broadcast"],
        "token": "broadcaster",
    },
    {
        "id": "set_game",
        "label": "Change the game",
        "group": "Do something",
        "hint": "Searches Twitch categories and sets the best match.",
        "target": None,
        "text_arg": {"label": "Game name", "default": "{command_message}",
                     "placeholder": "Game name, e.g. {command_message}"},
        "options": [],
        "variables": [_var("new_game", "New game", "Celeste")],
        "scopes": ["channel:manage:broadcast"],
        "token": "broadcaster",
    },
    {
        "id": "stream_marker",
        "label": "Add a stream marker",
        "group": "Do something",
        "hint": "Marks this moment in the VOD (you must be live).",
        "target": None,
        "text_arg": {"label": "Marker description", "default": "{command_message}",
                     "placeholder": "Optional description"},
        "options": [],
        "variables": [_var("marker_time", "Marker time in stream", "1:23:45")],
        "scopes": ["channel:manage:broadcast"],
        "token": "broadcaster",
    },
    {
        "id": "raid",
        "label": "Raid a channel",
        "group": "Do something",
        "hint": "Starts a raid to the named channel.",
        "target": "channel",
        "text_arg": None,
        "options": [],
        "variables": [
            _var("target_name", "Channel name", "CoolStreamer"),
            _var("target_login", "Channel login", "coolstreamer"),
            _var("target_url", "Channel link", "https://twitch.tv/coolstreamer"),
        ],
        "scopes": ["channel:manage:raids"],
        "token": "broadcaster",
    },
    {
        "id": "snooze_ad",
        "label": "Snooze the next ad",
        "group": "Do something",
        "hint": "Pushes the next scheduled ad back by 5 minutes.",
        "target": None,
        "text_arg": None,
        "options": [],
        "variables": [_var("snooze_count", "Snoozes left", "2")],
        "scopes": ["channel:manage:ads"],
        "token": "broadcaster",
    },
]

ADVANCED_VARIABLES: List[Dict[str, Any]] = [
    _var("api_status", "HTTP status", "200"),
    _var("api_success", "Request succeeded (true/false)", "true"),
    _var("api_total", "Total (when Twitch returns one)", "1234"),
    _var("api_count", "Items returned", "1"),
    {"key": "api_FIELD", "label": "Field of the first result (api_<field>)",
     "example": "value", "date": False, "pattern": True},
    {"key": "api_data_N_FIELD", "label": "Field of result N (0-based)",
     "example": "value", "date": False, "pattern": True},
]

LOOKUP_INDEX: Dict[str, Dict[str, Any]] = {entry["id"]: entry for entry in LOOKUPS}


def get_lookup(lookup_id: Any) -> Optional[Dict[str, Any]]:
    if isinstance(lookup_id, dict):
        lookup_id = lookup_id.get("id")
    return LOOKUP_INDEX.get(str(lookup_id or "").strip().lower())


def lookup_variable_specs(lookup: Any) -> List[Dict[str, Any]]:
    """Variable specs for a lookup id / config dict (advanced: generic api_*)."""
    lookup_id = lookup.get("id") if isinstance(lookup, dict) else lookup
    if str(lookup_id or "").lower() == ADVANCED_ID:
        specs = list(ADVANCED_VARIABLES)
        if isinstance(lookup, dict):
            for line in lookup.get("processing") or []:
                name = str(line).split("=", 1)[0].strip() if "=" in str(line) else ""
                if name and re.match(r"^[\w\-]+$", name):
                    specs.append(_var(name, f"Processed: {name}", ""))
        return specs
    entry = get_lookup(lookup_id)
    return [dict(v) for v in entry["variables"]] if entry else []


def example_variables(lookup: Any) -> Dict[str, Any]:
    """Example values for previews (no network)."""
    out: Dict[str, Any] = {"lookup_ok": True, "lookup_reason": "ok", "lookup_error": ""}
    for spec in lookup_variable_specs(lookup):
        if not spec.get("pattern"):
            out[spec["key"]] = spec["example"]
    return out


# --------------------------------------------------------------------------
# Twitch access
# --------------------------------------------------------------------------


class LookupFailure(Exception):
    def __init__(self, reason: str, message: str, variables: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.variables = variables or {}


class HelixError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _api():
    twitch = sys.modules.get("modules.twitch")
    if twitch is None:
        try:
            from . import twitch as twitch  # noqa: PLW0127
        except Exception:
            return None
    return getattr(twitch, "twitch_api", None)


def granted_scopes() -> set:
    api = _api()
    scopes = getattr(api, "authscope", None) or []
    return {str(getattr(scope, "value", scope)) for scope in scopes}


def broadcaster_id() -> str:
    return str(getattr(_api(), "user_id", "") or "")


def broadcaster_login() -> str:
    user = getattr(_api(), "user", None)
    return str(getattr(user, "login", "") or "")


def lookup_available(lookup: Any) -> Tuple[bool, str]:
    """(usable, reason) for a LOOKUPS entry / id / config dict."""
    lookup_id = lookup.get("id") if isinstance(lookup, dict) else lookup
    is_advanced = str(lookup_id or "").lower() == ADVANCED_ID
    entry = None if is_advanced else get_lookup(lookup_id)
    if entry is None and not is_advanced:
        return False, "Unknown lookup."
    api = _api()
    if api is None or not getattr(api, "is_connected", False):
        return False, "Connect your Twitch account in Settings to use this."
    if entry is not None:
        missing = [s for s in entry.get("scopes") or [] if s not in granted_scopes()]
        if missing:
            return False, "Reconnect Twitch to allow: " + ", ".join(missing)
    return True, ""


_STATUS_RE = re.compile(r"\b(?:Error|status)\s*\(?(\d{3})\b")


def _status_from_exception(exc: Exception) -> int:
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return status
    if type(exc).__name__ == "TwitchPermissionError":
        return 403
    match = _STATUS_RE.search(str(exc))
    return int(match.group(1)) if match else 0


async def helix_request(
    method: str,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    json_data: Any = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Any]:
    """(HTTP status, response) through ``twitch_api.generic_api_call``.

    Raises LookupFailure("not_connected") or HelixError(status, text).
    """
    api = _api()
    if api is None or not getattr(api, "is_connected", False):
        raise LookupFailure("not_connected", "Twitch is not connected")
    kwargs: Dict[str, Any] = {}
    if headers:
        kwargs["headers"] = headers
    try:
        data = await api.generic_api_call(
            url, method.upper(), params=params or None, json_data=json_data, **kwargs
        )
    except LookupFailure:
        raise
    except Exception as exc:
        raise HelixError(_status_from_exception(exc), str(exc)) from None
    status = 200
    if isinstance(data, dict) and set(data) == {"status", "data"} and isinstance(data["status"], int):
        status = data["status"]
    return status, data


async def _get(path: str, params: Dict[str, Any]) -> Any:
    try:
        return (await helix_request("GET", f"{HELIX}{path}", params))[1]
    except HelixError as exc:
        raise LookupFailure("error", f"Twitch said: {exc}") from None


def _rows(data: Any) -> List[Dict[str, Any]]:
    rows = data.get("data") if isinstance(data, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _yes(value: bool) -> str:
    return "yes" if value else "no"


# --------------------------------------------------------------------------
# Targets
# --------------------------------------------------------------------------


def clean_login(value: Any) -> str:
    text = str(value or "").strip().lstrip("@").strip()
    if not text:
        return ""
    text = text.split()[0].lstrip("@")
    if text.lower().startswith(("https://twitch.tv/", "twitch.tv/", "https://www.twitch.tv/")):
        text = text.rstrip("/").rsplit("/", 1)[-1]
    return text.lower()


def _target_spec(config: Dict[str, Any], kind: str) -> Dict[str, Any]:
    spec = config.get("target") if isinstance(config.get("target"), dict) else {}
    source = str(spec.get("from") or "").strip().lower()
    if source not in ("chatter", "event_user", "input", "text"):
        source = "event_user" if kind == "event" else "chatter"
    return {**spec, "from": source}


def _target_login_or_id(config: Dict[str, Any], context: Dict[str, Any], kind: str) -> Tuple[str, str]:
    """(login, user_id) named by the target spec (one may be empty)."""
    spec = _target_spec(config, kind)
    source = spec["from"]
    if source in ("chatter", "event_user"):
        user_id = str(context.get("user_id") or context.get("userid") or "")
        login = clean_login(context.get("user_login") or context.get("username"))
        return login, user_id
    if source == "input":
        wanted = spec.get("input", 1)
        args = context.get("command_args") or []
        if isinstance(wanted, int) or str(wanted).strip().isdigit():
            index = int(wanted) - 1
            value = args[index] if 0 <= index < len(args) else ""
        else:
            value = context.get(str(wanted).strip().strip("{}"), "")
        if clean_login(value):
            return clean_login(value), ""
        # Nothing typed for that input: the lookup is about whoever used the command.
        return clean_login(context.get("user_login") or context.get("username")), str(context.get("user_id") or context.get("userid") or "")
    return clean_login(render_template(spec.get("text") or "", context)), ""


async def resolve_target_user(config: Dict[str, Any], context: Dict[str, Any], kind: str = "command") -> Dict[str, Any]:
    """Helix user row for the lookup's target. Raises LookupFailure."""
    login, user_id = _target_login_or_id(config, context, kind)
    if user_id:
        rows = _rows(await _get("/users", {"id": user_id}))
        if rows:
            return rows[0]
    if not login:
        raise LookupFailure("no_target", "No user or channel was named")
    if not _LOGIN_RE.match(login):
        raise LookupFailure("not_found", f"'{login}' is not a Twitch name", {"target_name": login})
    rows = _rows(await _get("/users", {"login": login}))
    if not rows:
        raise LookupFailure("not_found", f"Couldn't find {login} on Twitch", {"target_name": login})
    return rows[0]


def _target_basics(user: Dict[str, Any]) -> Dict[str, Any]:
    login = str(user.get("login") or "")
    return {
        "target_name": str(user.get("display_name") or login),
        "target_login": login,
        "target_url": f"https://twitch.tv/{login}" if login else "",
    }


async def _channel_vars(user: Dict[str, Any]) -> Dict[str, Any]:
    out = _target_basics(user)
    user_id = str(user.get("id") or "")
    channel = _rows(await _get("/channels", {"broadcaster_id": user_id}))
    stream = _rows(await _get("/streams", {"user_id": user_id}))
    info = channel[0] if channel else {}
    out["target_game"] = str(info.get("game_name") or "")
    out["target_title"] = str(info.get("title") or "")
    out["target_live"] = _yes(bool(stream))
    return out


def _text_arg(config: Dict[str, Any], context: Dict[str, Any], entry: Dict[str, Any]) -> str:
    template = config.get("text")
    if template is None:
        template = (entry.get("text_arg") or {}).get("default", "")
    return render_template(str(template or ""), context).strip()


# --------------------------------------------------------------------------
# Curated implementations
# --------------------------------------------------------------------------


async def _follow_age(config, context, kind, entry):
    user = await resolve_target_user(config, context, kind)
    name = str(user.get("display_name") or user.get("login") or "")
    rows = _rows(await _get("/channels/followers", {"broadcaster_id": broadcaster_id(), "user_id": user.get("id")}))
    if not rows:
        variables = {"followage": "not following", "followed_since": "", "follow_days": "0",
                     "is_following": "no", "target_name": name}
        raise LookupFailure("not_following", f"{name} is not following", variables)
    followed_at = rows[0].get("followed_at")
    moment = to_local_naive(followed_at)
    days = (datetime.now() - moment).days if moment else 0
    return {
        "followage": friendly_duration(moment) if moment else "",
        "followed_since": _date_value(followed_at),
        "follow_days": str(max(days, 0)),
        "is_following": "yes",
        "target_name": name,
    }


async def _account_age(config, context, kind, entry):
    user = await resolve_target_user(config, context, kind)
    created = to_local_naive(user.get("created_at"))
    days = (datetime.now() - created).days if created else 0
    return {
        "account_age": friendly_duration(created) if created else "",
        "account_created": _date_value(user.get("created_at")),
        "account_age_days": str(max(days, 0)),
        "display_name": str(user.get("display_name") or user.get("login") or ""),
    }


async def _subscription(config, context, kind, entry):
    user = await resolve_target_user(config, context, kind)
    name = str(user.get("display_name") or user.get("login") or "")
    try:
        rows = _rows((await helix_request(
            "GET", f"{HELIX}/subscriptions",
            {"broadcaster_id": broadcaster_id(), "user_id": user.get("id")},
        ))[1])
    except HelixError as exc:
        raise LookupFailure("error", f"Twitch said: {exc}") from None
    if not rows:
        return {"is_subscribed": "no", "sub_tier": "0", "is_gift": "no", "gifter": "", "target_name": name}
    row = rows[0]
    tier = str(row.get("tier") or "1000")
    return {
        "is_subscribed": "yes",
        "sub_tier": tier[0] if tier[:1] in "123" else "1",
        "is_gift": _yes(bool(row.get("is_gift"))),
        "gifter": str(row.get("gifter_name") or ""),
        "target_name": name,
    }


async def _uptime(config, context, kind, entry):
    rows = _rows(await _get("/streams", {"user_id": broadcaster_id()}))
    if not rows:
        return {"uptime": "offline", "live_since": "", "is_live": "no", "viewer_count": "0",
                "stream_title": "", "stream_game": ""}
    row = rows[0]
    started = to_local_naive(row.get("started_at"))
    return {
        "uptime": friendly_duration(started) if started else "",
        "live_since": _date_value(row.get("started_at"), FriendlyDateTime),
        "is_live": "yes",
        "viewer_count": str(row.get("viewer_count") or 0),
        "stream_title": str(row.get("title") or ""),
        "stream_game": str(row.get("game_name") or ""),
    }


async def _title_game(config, context, kind, entry):
    rows = _rows(await _get("/channels", {"broadcaster_id": broadcaster_id()}))
    row = rows[0] if rows else {}
    return {
        "title": str(row.get("title") or ""),
        "game": str(row.get("game_name") or ""),
        "tags": ", ".join(str(t) for t in row.get("tags") or []),
        "language": str(row.get("broadcaster_language") or ""),
    }


async def _follower_count(config, context, kind, entry):
    data = await _get("/channels/followers", {"broadcaster_id": broadcaster_id(), "first": "1"})
    return {"follower_count": str((data or {}).get("total", 0) if isinstance(data, dict) else 0)}


async def _subscriber_count(config, context, kind, entry):
    data = await _get("/subscriptions", {"broadcaster_id": broadcaster_id(), "first": "1"})
    data = data if isinstance(data, dict) else {}
    return {"sub_count": str(data.get("total", 0)), "sub_points": str(data.get("points", 0))}


async def _top_cheerer(config, context, kind, entry):
    period = str(config.get("period") or "all")
    if period not in ("all", "year", "month", "week", "day"):
        period = "all"
    rows = _rows(await _get("/bits/leaderboard", {"count": "1", "period": period}))
    if not rows:
        return {"top_cheerer": "", "top_cheer_amount": "0"}
    return {"top_cheerer": str(rows[0].get("user_name") or ""), "top_cheer_amount": str(rows[0].get("score") or 0)}


async def _channel_info(config, context, kind, entry):
    return await _channel_vars(await resolve_target_user(config, context, kind))


async def _shoutout(config, context, kind, entry):
    user = await resolve_target_user(config, context, kind)
    out = await _channel_vars(user)
    try:
        await helix_request("POST", f"{HELIX}/chat/shoutouts", {
            "from_broadcaster_id": broadcaster_id(),
            "to_broadcaster_id": user.get("id"),
            "moderator_id": broadcaster_id(),
        })
        out["shoutout_sent"] = "yes"
    except HelixError as exc:
        # Twitch only allows shoutouts while live and with cooldowns; the chat
        # reply with the channel's info is still useful.
        logger.info("Twitch shoutout to %s not sent: %s", out.get("target_login"), exc)
        out["shoutout_sent"] = "no"
    return out


async def _patch_channel(body: Dict[str, Any]) -> None:
    try:
        await helix_request("PATCH", f"{HELIX}/channels", {"broadcaster_id": broadcaster_id()}, body)
    except HelixError as exc:
        raise LookupFailure("error", f"Twitch said: {exc}") from None


async def _set_title(config, context, kind, entry):
    title = _text_arg(config, context, entry)
    if not title:
        raise LookupFailure("no_text", "No title was given")
    title = title[:140]
    await _patch_channel({"title": title})
    return {"new_title": title}


async def _set_game(config, context, kind, entry):
    name = _text_arg(config, context, entry)
    if not name:
        raise LookupFailure("no_text", "No game was given")
    rows = _rows(await _get("/search/categories", {"query": name, "first": "10"}))
    if not rows:
        raise LookupFailure("not_found", f"No game called {name}", {"new_game": name})
    wanted = name.casefold()
    best = next((r for r in rows if str(r.get("name") or "").casefold() == wanted), rows[0])
    await _patch_channel({"game_id": str(best.get("id") or "")})
    return {"new_game": str(best.get("name") or name)}


async def _stream_marker(config, context, kind, entry):
    body: Dict[str, Any] = {"user_id": broadcaster_id()}
    description = _text_arg(config, context, entry)
    if description:
        body["description"] = description[:140]
    try:
        _, data = await helix_request("POST", f"{HELIX}/streams/markers", None, body)
    except HelixError as exc:
        raise LookupFailure("error", f"Twitch said: {exc}") from None
    rows = _rows(data)
    seconds = int((rows[0].get("position_seconds") if rows else 0) or 0)
    hours, rest = divmod(seconds, 3600)
    return {"marker_time": f"{hours}:{rest // 60:02d}:{rest % 60:02d}"}


async def _raid(config, context, kind, entry):
    user = await resolve_target_user(config, context, kind)
    try:
        await helix_request("POST", f"{HELIX}/raids", {
            "from_broadcaster_id": broadcaster_id(),
            "to_broadcaster_id": user.get("id"),
        })
    except HelixError as exc:
        raise LookupFailure("error", f"Twitch said: {exc}", _target_basics(user)) from None
    return _target_basics(user)


async def _snooze_ad(config, context, kind, entry):
    try:
        _, data = await helix_request(
            "POST", f"{HELIX}/channels/ads/schedule/snooze", {"broadcaster_id": broadcaster_id()}
        )
    except HelixError as exc:
        raise LookupFailure("error", f"Twitch said: {exc}") from None
    rows = _rows(data)
    return {"snooze_count": str(rows[0].get("snooze_count", 0) if rows else 0)}


_IMPLEMENTATIONS = {
    "follow_age": _follow_age,
    "account_age": _account_age,
    "subscription": _subscription,
    "uptime": _uptime,
    "title_game": _title_game,
    "follower_count": _follower_count,
    "subscriber_count": _subscriber_count,
    "top_cheerer": _top_cheerer,
    "channel_info": _channel_info,
    "shoutout": _shoutout,
    "set_title": _set_title,
    "set_game": _set_game,
    "stream_marker": _stream_marker,
    "raid": _raid,
    "snooze_ad": _snooze_ad,
}


def _failure(reason: str, message: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = dict(extra or {})
    out.update({"lookup_ok": False, "lookup_reason": reason, "lookup_error": message})
    return out


async def run_lookup(
    lookup_id: str,
    config: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
    *,
    kind: str = "command",
) -> Dict[str, Any]:
    """Run a curated lookup (or ``"advanced"``) and return its variables.

    *config* is the item's ``lookup`` dict; *context* the reply context
    (chatter / event user, command args, argument names). Never raises.
    """
    config = dict(config or {})
    context = dict(context or {})
    lookup_id = str(lookup_id or config.get("id") or "").strip().lower()
    if lookup_id == ADVANCED_ID:
        return await run_raw_request(
            config.get("method") or "GET",
            config.get("path") or "",
            config.get("params") or {},
            config.get("body") or "",
            context,
            headers=config.get("headers"),
            processing=config.get("processing"),
            response_format=config.get("response_format"),
        )
    entry = get_lookup(lookup_id)
    if entry is None or lookup_id not in _IMPLEMENTATIONS:
        return _failure("unknown", f"Unknown lookup {lookup_id!r}")
    ok, reason = lookup_available(entry)
    if not ok:
        code = "not_connected" if "Connect" in reason else "missing_scope"
        return _failure(code, reason)
    try:
        variables = await _IMPLEMENTATIONS[lookup_id](config, context, kind, entry)
    except LookupFailure as exc:
        return _failure(exc.reason, exc.message, exc.variables)
    except Exception as exc:
        logger.warning("Lookup %s failed: %s", lookup_id, exc, exc_info=True)
        return _failure("error", str(exc))
    variables.update({"lookup_ok": True, "lookup_reason": "ok", "lookup_error": ""})
    return variables


def _run_coro_in_thread(coro, timeout: float):
    import concurrent.futures

    future: concurrent.futures.Future = concurrent.futures.Future()

    def runner():
        try:
            future.set_result(asyncio.run(coro))
        except BaseException as exc:  # noqa: BLE001
            future.set_exception(exc)

    threading.Thread(target=runner, daemon=True, name="ChatbotLookup").start()
    return future.result(timeout=timeout)


def run_lookup_blocking(
    lookup: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
    *,
    kind: str = "command",
    timeout: float = LOOKUP_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Run an item's lookup from sync code. Never call while holding a lock.

    On a thread with a running asyncio loop the request runs on a helper
    thread (the caller still waits, up to *timeout*).
    """
    if not isinstance(lookup, dict) or not lookup.get("id"):
        return _failure("unknown", "No lookup configured")

    async def _bounded():
        return await asyncio.wait_for(run_lookup(lookup["id"], lookup, context, kind=kind), timeout)

    try:
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        if on_loop:
            return _run_coro_in_thread(_bounded(), timeout + 1)
        return asyncio.run(_bounded())
    except Exception as exc:
        logger.warning("Lookup %s did not finish: %s", lookup.get("id"), exc)
        return _failure("error", "Twitch did not answer in time" if isinstance(
            exc, (asyncio.TimeoutError, TimeoutError)) else str(exc))


# --------------------------------------------------------------------------
# Advanced raw requests
# --------------------------------------------------------------------------

_ID_PARAMS = ("broadcaster_id", "moderator_id", "from_broadcaster_id")
# Endpoints whose user_id must be the broadcaster (the token's own user).
_BROADCASTER_USER_ID_PATHS = {"/streams/markers", "/streams/followed", "/channels/followed"}
_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
_reference_index: Optional[Dict[Tuple[str, str], Dict[str, str]]] = None
_reference_lock = threading.Lock()


def _required_params(method: str, path: str) -> Dict[str, str]:
    global _reference_index
    with _reference_lock:
        if _reference_index is None:
            index: Dict[Tuple[str, str], Dict[str, str]] = {}
            try:
                from .twitch_api_reference import TwitchAPIReference

                for endpoints in TwitchAPIReference().get_all_endpoints().values():
                    for endpoint in endpoints.values():
                        key = (endpoint.method.upper(), endpoint.endpoint.rstrip("/"))
                        index.setdefault(key, dict(endpoint.required_params or {}))
            except Exception as exc:
                logger.debug("Twitch API reference unavailable: %s", exc)
            _reference_index = index
        return _reference_index.get((method.upper(), path.rstrip("/")), {})


def raw_request_context(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Context for advanced requests: ids of the broadcaster and the chatter."""
    ctx = dict(context or {})
    bid = broadcaster_id()
    ctx.setdefault("broadcaster_id", bid)
    ctx.setdefault("moderator_id", bid)
    ctx.setdefault("broadcaster_login", broadcaster_login())
    chatter_id = str(ctx.get("chatter_id") or ctx.get("user_id") or ctx.get("userid") or "")
    chatter_login = clean_login(ctx.get("chatter_login") or ctx.get("user_login") or ctx.get("username"))
    ctx["chatter_id"] = chatter_id
    ctx["chatter_login"] = chatter_login
    ctx.setdefault("user_id", chatter_id)
    ctx.setdefault("user_login", chatter_login)
    if not isinstance(ctx.get("command_args"), (list, tuple)):
        words = str(ctx.get("message") or "").split()
        ctx["command_args"] = words[1:] if words and words[0].startswith("!") else []
    return ctx


def _render_transform(text: str, ctx: Dict[str, Any], transform) -> str:
    def repl(match: re.Match) -> str:
        value = resolve_template_token(match.group(1), ctx)
        return match.group(0) if value is None else transform(value)

    return TEMPLATE_TOKEN.sub(repl, str(text or ""))


def substitute_json_text(text: str, ctx: Dict[str, Any]) -> str:
    """Fill variables into JSON source so it stays valid JSON."""
    out: List[str] = []
    pos = 0
    in_string = False
    for match in TEMPLATE_TOKEN.finditer(text):
        literal = text[pos:match.start()]
        out.append(literal)
        in_string = _json_scan(literal, in_string)
        value = resolve_template_token(match.group(1), ctx)
        if value is None:
            out.append(match.group(0))
        elif in_string:
            out.append(json.dumps(value, ensure_ascii=False)[1:-1])
        else:
            out.append(_json_bare_value(value))
        pos = match.end()
    out.append(text[pos:])
    return "".join(out)


def _substitute_obj(value: Any, ctx: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        return render_template(value, ctx)
    if isinstance(value, list):
        return [_substitute_obj(v, ctx) for v in value]
    if isinstance(value, dict):
        return {render_template(str(k), ctx): _substitute_obj(v, ctx) for k, v in value.items()}
    return value


def build_helix_url(path_or_url: str, ctx: Dict[str, Any]) -> Tuple[str, str, Dict[str, str]]:
    """(url without query, helix path, query params) for a path or Twitch URL.

    Absolute URLs must point at api.twitch.tv (the broadcaster token is
    attached to the request). Raises LookupFailure otherwise.
    """
    raw = _render_transform(str(path_or_url or "").strip(), ctx, lambda v: quote(str(v), safe=""))
    if not raw:
        raise LookupFailure("error", "No API path given")
    if re.match(r"^https?://", raw, re.IGNORECASE):
        parts = urlsplit(raw)
        if parts.scheme.lower() != "https" or parts.hostname != "api.twitch.tv":
            raise LookupFailure("error", "Only https://api.twitch.tv addresses are allowed")
        path = parts.path
        query = parts.query
    else:
        path, _, query = raw.partition("?")
        path = "/" + path.lstrip("/")
    if path.startswith("/helix"):
        path = path[len("/helix"):] or "/"
    path = "/" + path.lstrip("/")
    params = {k: v for k, v in parse_qsl(query, keep_blank_values=True)}
    return f"{HELIX}{path}", path, params


def _flat(value: Any) -> Any:
    if isinstance(value, list) and all(isinstance(v, (str, int, float, bool)) or v is None for v in value):
        return ", ".join("" if v is None else str(v) for v in value)
    return value


def flatten_response(data: Any, status: int) -> Dict[str, Any]:
    """``{api_<field>}`` variables from a Helix response."""
    out: Dict[str, Any] = {
        "api_status": status,
        "api_success": 200 <= status < 300,
        "api_response": data,
    }
    if isinstance(data, dict):
        rows = data.get("data")
        if isinstance(rows, list):
            out["api_count"] = len(rows)
            if rows and isinstance(rows[0], dict):
                for key, value in rows[0].items():
                    out[f"api_{key}"] = _flat(value)
            for index, row in enumerate(rows[:25]):
                if isinstance(row, dict):
                    for key, value in row.items():
                        out[f"api_data_{index}_{key}"] = _flat(value)
        elif isinstance(rows, dict):
            for key, value in rows.items():
                out[f"api_{key}"] = _flat(value)
        for key, value in data.items():
            if key in ("data", "pagination", "status"):
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[f"api_{key}"] = value
    created = to_local_naive(out.get("api_created_at")) if out.get("api_created_at") else None
    if created is not None:
        days = (datetime.now() - created).days
        out.setdefault("account_age_days", days)
        out.setdefault("account_age_years", days // 365)
        out.setdefault("account_age_remaining_days", days % 365)
        out.setdefault("account_created_datetime", created.strftime("%Y-%m-%d %H:%M:%S"))
    return out


def _apply_response_format(spec_text: str, data: Any) -> Dict[str, Any]:
    """Legacy ``api_response_format``: ``name=path, path2`` extraction."""
    out: Dict[str, Any] = {}
    for spec in str(spec_text or "").split(","):
        spec = spec.strip()
        if not spec:
            continue
        if "=" in spec:
            name, path = (s.strip() for s in spec.split("=", 1))
            inner = path[1:-1] if path.startswith("{") and path.endswith("}") else path
            found, value = _ctx_lookup_found(data, inner)
            out[name] = value if found else path
        else:
            found, value = _ctx_lookup_found(data, spec)
            if found:
                out["api_" + re.sub(r"[.\[\]]+", "_", spec).strip("_")] = value
    return out


def apply_processing(lines: Any, variables: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    """``name=expression`` lines (math()/compare()/date_to_age()/if(), safe)."""
    out: Dict[str, Any] = {}
    scope = {**ctx, **variables}
    for line in lines or []:
        text = str(line or "").strip()
        if not text:
            continue
        name, sep, expression = text.partition("=")
        name = name.strip()
        if not sep or not re.match(r"^[\w\-]+$", name):
            expression = text
            name = "processed_" + re.sub(r"[^\w]+", "_", text).strip("_")
        try:
            value = evaluate_expression(expression.strip(), scope)
        except Exception as exc:
            logger.debug("Processing %r failed: %s", text, exc)
            value = ""
        out[name] = value
        scope[name] = value
    return out


async def run_raw_request(
    method: str,
    path_or_url: str,
    params: Optional[Dict[str, Any]] = None,
    body: Any = "",
    context: Optional[Dict[str, Any]] = None,
    *,
    headers: Optional[Dict[str, Any]] = None,
    processing: Optional[List[str]] = None,
    response_format: Optional[str] = None,
) -> Dict[str, Any]:
    """Advanced Helix request with auto-filled ids. Never raises.

    * Relative paths (``/users``, ``users``, ``/helix/users``) become
      ``https://api.twitch.tv/helix/...``; absolute URLs must be api.twitch.tv.
    * ``{broadcaster_id}``, ``{broadcaster_login}``, ``{moderator_id}``,
      ``{chatter_id}``/``{user_id}``, ``{chatter_login}``/``{user_login}``,
      ``{command_word_N}`` and every context key are filled into the path
      (URL-encoded), params and body (JSON-aware).
    * broadcaster_id / moderator_id / from_broadcaster_id (and user_id on
      endpoints where it must be the broadcaster) are filled when left empty,
      or added when the endpoint requires them.
    * Response: ``api_status`` (real HTTP status), ``api_success``,
      ``api_<field>`` of the first ``data`` item, ``api_data_N_<field>``,
      ``api_total`` and other top-level values, ``api_count``, plus
      ``processing`` results.
    """
    ctx = raw_request_context(context)
    method = str(method or "GET").upper()
    if method not in _METHODS:
        method = "GET"
    try:
        url, path, query = build_helix_url(path_or_url, ctx)
    except LookupFailure as exc:
        return _failure(exc.reason, exc.message)
    final_params: Dict[str, Any] = dict(query)
    for key, value in (params or {}).items():
        name = render_template(str(key), ctx).strip()
        if name:
            final_params[name] = render_template("" if value is None else str(value), ctx).strip()
    bid = ctx.get("broadcaster_id") or ""
    required = _required_params(method, path)
    fill_names = list(_ID_PARAMS)
    if path in _BROADCASTER_USER_ID_PATHS:
        fill_names.append("user_id")
    for name in fill_names:
        if name in final_params and not str(final_params[name]).strip():
            final_params[name] = bid
        elif name not in final_params and name in required and name != "user_id":
            final_params[name] = bid

    json_body: Any = None
    if method in ("POST", "PUT", "PATCH", "DELETE") and body not in (None, "", {}, []):
        if isinstance(body, (dict, list)):
            json_body = _substitute_obj(body, ctx)
        else:
            text = substitute_json_text(str(body).strip(), ctx)
            try:
                json_body = json.loads(text)
            except ValueError:
                json_body = render_template(str(body), ctx)
        if isinstance(json_body, dict):
            for name in fill_names:
                if name in json_body and not str(json_body[name] or "").strip():
                    json_body[name] = bid
    if path == "/streams/markers" and method == "POST":
        json_body = json_body if isinstance(json_body, dict) else {}
        json_body.setdefault("user_id", bid)

    extra_headers = None
    if isinstance(headers, dict) and headers:
        blocked = {"authorization", "client-id"}
        extra_headers = {
            render_template(str(k), ctx): render_template(str(v), ctx)
            for k, v in headers.items()
            if str(k).strip() and str(k).strip().lower() not in blocked
        } or None

    try:
        status, data = await helix_request(method, url, final_params, json_body, extra_headers)
        variables = flatten_response(data, status)
        error = ""
    except LookupFailure as exc:
        return _failure(exc.reason, exc.message)
    except HelixError as exc:
        variables = flatten_response(None, exc.status)
        data = None
        error = str(exc)
    if response_format:
        variables.update(_apply_response_format(response_format, data))
    if processing:
        variables.update(apply_processing(processing, variables, ctx))
    ok = bool(variables.get("api_success"))
    variables.update({
        "lookup_ok": ok,
        "lookup_reason": "ok" if ok else "error",
        "lookup_error": "" if ok else (error or f"HTTP {variables.get('api_status')}"),
    })
    return variables


# --------------------------------------------------------------------------
# Legacy migration
# --------------------------------------------------------------------------

_LEGACY_PLACEHOLDERS = {
    "[Command user's ID]": "{chatter_id}",
    "[Command user's username]": "{chatter_login}",
    "{trigger_user_id}": "{chatter_id}",
    "{trigger_username}": "{chatter_login}",
}


def _migrate_text(value: Any, frozen_id: str) -> Any:
    if not isinstance(value, str):
        return value
    text = value
    for old, new in _LEGACY_PLACEHOLDERS.items():
        text = text.replace(old, new)
    if frozen_id:
        if text.strip() == frozen_id:
            return "{broadcaster_id}"
        text = re.sub(rf"(?<=[=:\"]){re.escape(frozen_id)}(?=[&\"]|$)", "{broadcaster_id}", text)
    return text


def migrate_legacy_api_fields(command: Any) -> Dict[str, Any]:
    """Advanced lookup dict from a legacy command's ``api_*`` fields.

    Placeholder literals ("[Command user's ID]" ...) become ``{chatter_id}`` /
    ``{chatter_login}``; a hard-coded id equal to the connected broadcaster
    becomes ``{broadcaster_id}`` (only detectable while Twitch is connected;
    otherwise the id is kept and still works).
    """
    frozen = broadcaster_id()
    params = {
        str(k): _migrate_text("" if v is None else str(v), frozen)
        for k, v in (getattr(command, "api_parameters", None) or {}).items()
    }
    body = getattr(command, "api_body", "") or ""
    lookup: Dict[str, Any] = {
        "id": ADVANCED_ID,
        "method": str(getattr(command, "api_method", "GET") or "GET").upper(),
        "path": _migrate_text(str(getattr(command, "api_endpoint", "") or ""), frozen),
        "params": params,
        "body": _migrate_text(body, frozen) if isinstance(body, str) else body,
        "processing": [str(p) for p in getattr(command, "api_variable_processing", None) or []],
    }
    headers = getattr(command, "api_headers", None)
    if isinstance(headers, dict) and headers:
        lookup["headers"] = dict(headers)
    response_format = getattr(command, "api_response_format", "")
    if response_format:
        lookup["response_format"] = str(response_format)
    return lookup
