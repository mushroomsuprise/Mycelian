# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Pure view-model helpers shared by the Activity Feed tab and the OBS dock.

Nothing here touches NiceGUI, the database or the overlay server, so every helper is
cheap to test. The tab and the dock both render ``feed_row_model`` rows and the
normalised settings / queue state with the shared renderer
(``assets/default_assets/activity_feed.js``).
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional

# --------------------------------------------------------------------------- settings

# short key -> (AppSettings field, default, allowed values or None for bool/int ranges)
FEED_SETTINGS: Dict[str, tuple] = {
    "layout": ("activity_feed_layout", "rows", ("rows", "cards", "ticker")),
    "density": ("activity_feed_density", "comfortable", ("compact", "comfortable", "spacious")),
    "color": ("activity_feed_color_mode", "icon", ("icon", "bar", "tint", "none")),
    "icons": ("activity_feed_show_icons", True, None),
    "tier": ("activity_feed_tier_badge", "icon", ("icon", "name", "hidden")),
    "platform": ("activity_feed_platform_marks", True, None),
    "messages": ("activity_feed_viewer_messages", True, None),
    "sub_duration": ("activity_feed_show_sub_duration", True, None),
    "wrap": ("activity_feed_wrap_text", False, None),
    "times": ("activity_feed_times", "relative", ("relative", "clock", "hidden")),
    "earlier_minutes": ("activity_feed_earlier_after_minutes", 30, (15, 30, 60, 120, 0)),
    "order_numbers": ("activity_feed_order_numbers", True, None),
    "dots": ("activity_feed_timeline_dots", True, None),
    "new_minutes": ("activity_feed_highlight_new_minutes", 5, (0, 1, 2, 5, 10, 15, 30)),
    "now_playing": ("activity_feed_now_playing", True, None),
    "timer": ("activity_feed_alert_timer", "strip", ("strip", "item", "off")),
    "up_next": ("activity_feed_up_next", 1, (0, 1, 3, 5)),
    "text_size": ("activity_feed_text_size", "medium", ("small", "medium", "large")),
    # 0 = never chosen: fall back to the old activity_feed.json value (or 12).
    "person_hours": ("activity_feed_person_hours", 0, (1, 2, 4, 6, 8, 12, 24, 48, 72, 168)),
}

FEED_SETTING_FIELDS: Dict[str, str] = {key: spec[0] for key, spec in FEED_SETTINGS.items()}
FIELD_TO_FEED_KEY: Dict[str, str] = {spec[0]: key for key, spec in FEED_SETTINGS.items()}
DEFAULT_PERSON_HOURS = 12


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return default


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _read(source: Any, field: str, key: str) -> Any:
    """Read a setting from an AppSettings-like object or a dict (field or short key)."""
    if source is None:
        return None
    if isinstance(source, Mapping):
        if field in source:
            return source[field]
        return source.get(key)
    return getattr(source, field, None)


def normalize_feed_settings(source: Any = None, *, legacy_person_hours: Any = None) -> Dict[str, Any]:
    """Complete, valid feed look settings from AppSettings (or a partial dict).

    Unknown values fall back to the defaults. ``person_hours`` 0 (never chosen) uses
    ``legacy_person_hours`` (the old activity_feed.json value) or 12.
    """
    out: Dict[str, Any] = {}
    for key, (field, default, allowed) in FEED_SETTINGS.items():
        raw = _read(source, field, key)
        if isinstance(default, bool):
            out[key] = _coerce_bool(raw, default) if raw is not None else default
            continue
        if isinstance(default, int):
            value = _coerce_int(raw, default) if raw is not None else default
            if key == "person_hours":
                if value <= 0:
                    value = _coerce_int(legacy_person_hours, 0) if legacy_person_hours is not None else 0
                if value <= 0:
                    value = DEFAULT_PERSON_HOURS
                out[key] = min(max(value, 1), 24 * 30)
                continue
            out[key] = value if (allowed is None or value in allowed) else default
            continue
        value = str(raw).strip().lower() if raw is not None else default
        out[key] = value if (allowed is None or value in allowed) else default
    return out


def feed_setting_value(key: str, value: Any) -> Any:
    """Validate one setting before it is stored (raises ValueError for unknown keys)."""
    if key not in FEED_SETTINGS:
        raise ValueError(f"unknown feed setting {key!r}")
    normalized = normalize_feed_settings({key: value})
    if key == "person_hours":
        hours = _coerce_int(value, DEFAULT_PERSON_HOURS)
        return hours if hours > 0 else DEFAULT_PERSON_HOURS
    return normalized[key]


# --------------------------------------------------------------------------- multi-month subs


def duration_months_value(value: Any) -> int:
    """Months a sub or gift covers (Twitch ``duration_months``); 0 when unknown or 1."""
    try:
        months = int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return 0
    return months if months > 1 else 0


def alert_duration_months(alert: Mapping[str, Any]) -> int:
    """``duration_months`` of a payload or stored alert (resubs stored it as months_prepaid)."""
    if not isinstance(alert, Mapping):
        return 0
    return max(
        duration_months_value(_pick(alert, "duration_months")),
        duration_months_value(_pick(alert, "months_prepaid")),
    )


def format_sub_duration(months: Any, kind: str = "sub") -> str:
    """Chip text: "6-month sub" / "3-month gift" (empty for single-month or unknown).

    Says "sub"/"gift" so it isn't confused with a resub's total months shown beside it.
    """
    n = duration_months_value(months)
    if not n:
        return ""
    return f"{n}-month gift" if kind == "giftsub" else f"{n}-month sub"


def sub_message(username: str, tier: Any, duration_months: Any = 0, *, show_duration: bool = True) -> str:
    """Plain feed text for a first-time sub."""
    n = duration_months_value(duration_months) if show_duration else 0
    if n:
        return f"{username} subscribed for {n} months (Tier {tier})!"
    return f"{username} subscribed (Tier {tier})!"


def resub_message(
    username: str, months: Any, tier: Any = None, duration_months: Any = 0, *, show_duration: bool = True
) -> str:
    """Plain feed text for a resub (``months`` is the cumulative total)."""
    n = duration_months_value(duration_months) if show_duration else 0
    text = f"{username} resubscribed for {months} months" + (f" (Tier {tier})" if tier else "")
    if n:
        text += f" with a {n}-month sub"
    return text + "!"


def giftsub_message(
    gifter: str,
    tier: Any,
    qty: Any = 1,
    recipient: Optional[str] = None,
    duration_months: Any = 0,
    *,
    show_duration: bool = True,
) -> str:
    """Plain feed text for a gift: "gifted a 3-month Tier 1 sub to X"."""
    n = duration_months_value(duration_months) if show_duration else 0
    try:
        count = int(qty or 1)
    except (TypeError, ValueError):
        count = 1
    recipient = str(recipient or "").strip()
    if count <= 1:
        sub = f"a {n}-month Tier {tier} sub" if n else f"a Tier {tier} sub"
        return f"{gifter} gifted {sub}" + (f" to {recipient}" if recipient else "") + "!"
    return f"{gifter} gifted {count:,} Tier {tier} subs" + (f" ({n} months each)" if n else "") + "!"


# --------------------------------------------------------------------------- kinds

# kind -> (Material icon, colour variable, label, filter key, platform)
KIND_META: Dict[str, tuple] = {
    "follow": ("favorite", "var(--color-primary)", "Follow", "follows", "twitch"),
    "sub": ("star", "var(--color-info)", "Sub", "subs", "twitch"),
    "resub": ("autorenew", "var(--color-info)", "Resub", "resubs", "twitch"),
    "giftsub": ("redeem", "var(--color-success)", "Gift", "giftsubs", "twitch"),
    "bits": ("diamond", "var(--color-warning)", "Bits", "bits", "twitch"),
    "points": ("toll", "var(--color-error)", "Points", "points", "twitch"),
    "raid": ("groups", "var(--color-error)", "Raid", "raids", "twitch"),
    "donation": ("payments", "var(--color-success)", "Tip", "donations", ""),
    "streak": ("local_fire_department", "var(--color-warning)", "Streak", "streaks", "twitch"),
    "hype": ("rocket_launch", "var(--color-primary)", "Hype Train", "hype_train", "twitch"),
    "modiversary": ("shield", "var(--color-info)", "Modiversary", "modiversaries", "twitch"),
    "member": ("workspace_premium", "var(--color-info)", "Member", "subs", "youtube"),
    "milestone": ("military_tech", "var(--color-info)", "Milestone", "resubs", "youtube"),
    "giftmember": ("card_giftcard", "var(--color-success)", "Gift", "giftsubs", "youtube"),
    "superchat": ("chat", "var(--color-success)", "Super Chat", "donations", "youtube"),
    "supersticker": ("sticky_note_2", "var(--color-warning)", "Super Sticker", "donations", "youtube"),
    "alert": ("notifications", "var(--color-primary)", "Alert", "", "twitch"),
}

_KIND_ALIASES = {
    "follow": "follow",
    "sub": "sub",
    "subscription": "sub",
    "resub": "resub",
    "giftsub": "giftsub",
    "gift_sub": "giftsub",
    "bits": "bits",
    "bit": "bits",
    "cheer": "bits",
    "points": "points",
    "point": "points",
    "channel points": "points",
    "raid": "raid",
    "donation": "donation",
    "tip": "donation",
    "streak": "streak",
    "watch_streak": "streak",
    "hype train": "hype",
    "hype_train": "hype",
    "hype_train_start": "hype",
    "hype_train_progress": "hype",
    "hype_train_end": "hype",
    "hypetrain": "hype",
    "modiversary": "modiversary",
    "membership": "member",
    "member": "member",
    "member milestone": "milestone",
    "member_milestone": "milestone",
    "gift membership": "giftmember",
    "gift_membership": "giftmember",
    "super chat": "superchat",
    "superchat": "superchat",
    "super sticker": "supersticker",
    "supersticker": "supersticker",
}

_YOUTUBE_KINDS = frozenset({"member", "milestone", "giftmember", "superchat", "supersticker"})
_TIP_PLATFORMS = frozenset({"streamlabs", "streamelements"})


def feed_kind(alert: Mapping[str, Any]) -> str:
    """Canonical event kind for an alert payload, stored alert or queue entry."""
    if not isinstance(alert, Mapping):
        return "alert"
    source = str(alert.get("source") or "").lower()
    candidates = [alert.get("badge_type"), alert.get("type"), alert.get("alert_type")]
    kind = ""
    for value in candidates:
        key = str(value or "").strip().lower()
        if key in _KIND_ALIASES:
            kind = _KIND_ALIASES[key]
            # badge_type carries the YouTube-native label; prefer it when present
            break
    if not kind:
        return "alert"
    if source == "youtube" and kind in ("sub", "resub", "giftsub", "donation"):
        kind = {"sub": "member", "resub": "milestone", "giftsub": "giftmember", "donation": "superchat"}[kind]
        stored = alert.get("stored_alert_data") if isinstance(alert.get("stored_alert_data"), Mapping) else {}
        if kind == "superchat" and (alert.get("is_supersticker") or stored.get("is_supersticker")):
            kind = "supersticker"
    return kind


def kind_filter_key(kind: str) -> str:
    return KIND_META.get(kind, KIND_META["alert"])[3]


# --------------------------------------------------------------------------- rows

_USERNAME_PREFIX_RE = re.compile(r"^\s*(\S+)\s+")
_MONTHS_RE = re.compile(r"(\d+)\s*months?", re.IGNORECASE)
_BITS_RE = re.compile(r"(\d[\d,]*)\s*bits?", re.IGNORECASE)
_VIEWERS_RE = re.compile(r"(\d[\d,]*)\s*viewers?", re.IGNORECASE)
_GIFT_RE = re.compile(r"gifted\s+(\d[\d,]*)", re.IGNORECASE)
_REWARD_RE = re.compile(r"redeemed\s+['‘“\"](.+?)['’”\"]\s*!?", re.IGNORECASE)
_DONATED_RE = re.compile(r"donated\s+(.+?)!?\s*$", re.IGNORECASE)
_PAREN_AMOUNT_RE = re.compile(r"\(([^()]*\d[^()]*)\)\s*!?\s*$")
_STREAK_RE = re.compile(r"(\d+)\s*consecutive\s+streams", re.IGNORECASE)
_LEVEL_RE = re.compile(r"level\s*(\d+)", re.IGNORECASE)
_PLAYING_RE = re.compile(r"last playing\s+(.+?)\.?\s*$", re.IGNORECASE)

_CURRENCY_SYMBOLS = {"USD": "$", "CAD": "CA$", "AUD": "A$", "EUR": "€", "GBP": "£", "JPY": "¥"}


def _stored(alert: Mapping[str, Any]) -> Mapping[str, Any]:
    stored = alert.get("stored_alert_data")
    return stored if isinstance(stored, Mapping) else {}


def _pick(alert: Mapping[str, Any], *keys: str) -> Any:
    stored = _stored(alert)
    for key in keys:
        value = alert.get(key)
        if value not in (None, ""):
            return value
        value = stored.get(key)
        if value not in (None, ""):
            return value
    return None


def _to_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _num(n: int) -> str:
    return f"{n:,}"


def format_money(amount: Any, currency: Any = None) -> str:
    value = _to_float(amount)
    if value is None:
        return str(amount or "")
    code = str(currency or "USD").upper().strip() or "USD"
    symbol = _CURRENCY_SYMBOLS.get(code)
    if symbol:
        return f"{symbol}{value:,.2f}"
    return f"{value:,.2f} {code}"


def _username(alert: Mapping[str, Any], kind: str) -> str:
    name = _pick(alert, "username", "user_name", "display_name")
    if name and str(name).strip() and str(name) not in ("Unknown", "Unknown User"):
        return str(name).strip()
    message = str(alert.get("message") or "")
    if kind == "hype":
        return "Hype Train"
    match = _USERNAME_PREFIX_RE.match(message)
    if match and kind != "alert":
        return match.group(1)
    return "Someone"


def _strip_user(message: str, user: str) -> str:
    text = str(message or "").strip()
    if user and text.lower().startswith(user.lower()):
        text = text[len(user):].strip()
    return text.rstrip("!").strip()


def _ts(alert: Mapping[str, Any]) -> float:
    for key in ("timestamp", "created_at"):
        value = alert.get(key)
        if value in (None, "", "now"):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    stored_ts = _stored(alert).get("timestamp")
    try:
        return float(stored_ts)
    except (TypeError, ValueError):
        return time.time()


def feed_row_model(alert: Mapping[str, Any], *, seq: Optional[int] = None) -> Dict[str, Any]:
    """Slim, render-ready description of one feed event (same for tab and dock)."""
    if not isinstance(alert, Mapping):
        alert = {}
    kind = feed_kind(alert)
    icon, color, label, filter_key, platform = KIND_META.get(kind, KIND_META["alert"])
    user = _username(alert, kind)
    message = str(alert.get("message") or "")
    what = ""
    amount = ""
    tier = None

    raw_tier = _to_int(_pick(alert, "tier"))
    if kind in ("sub", "resub") and raw_tier and 1 <= raw_tier <= 3:
        tier = f"T{raw_tier}"
    # Months one sub or gift covers ("6-month sub"); the renderer hides it when the setting is off.
    duration = ""
    if kind in ("sub", "resub", "giftsub"):
        duration = format_sub_duration(alert_duration_months(alert), kind)

    if kind == "follow":
        what = "followed"
    elif kind == "sub":
        what = "subscribed"
    elif kind == "resub":
        months = _to_int(_pick(alert, "resub_month", "months", "total_months"))
        if not months:
            match = _MONTHS_RE.search(message)
            months = int(match.group(1)) if match else None
        what = f"resubscribed · {months} months" if months and months > 1 else "resubscribed"
    elif kind == "giftsub":
        qty = _to_int(_pick(alert, "gift_qty"))
        if not qty:
            match = _GIFT_RE.search(message)
            qty = _to_int(match.group(1)) if match else 1
        qty = qty or 1
        t = raw_tier if raw_tier and 1 <= raw_tier <= 3 else 1
        recipient = str(_pick(alert, "recipient") or "").strip()
        if qty == 1:
            what = f"gifted a Tier {t} sub" + (f" to {recipient}" if recipient else "")
        else:
            what = f"gifted {_num(qty)} Tier {t} subs"
            amount = f"×{_num(qty)}"
    elif kind == "bits":
        bits = _to_int(_pick(alert, "amt_cheered", "bits"))
        if not bits:
            match = _BITS_RE.search(message)
            bits = _to_int(match.group(1)) if match else None
        what = "cheered"
        if bits:
            amount = f"{_num(bits)} bits"
    elif kind == "points":
        reward = _pick(alert, "alert_name", "reward_title", "reward")
        if not reward:
            match = _REWARD_RE.search(message)
            reward = match.group(1) if match else ""
        what = f"redeemed “{reward}”" if reward else "redeemed a reward"
        cost = _to_int(_pick(alert, "point_cost"))
        if cost:
            amount = f"{_num(cost)} pts"
    elif kind == "raid":
        viewers = _to_int(_pick(alert, "raider_count", "viewers"))
        if viewers is None:
            match = _VIEWERS_RE.search(message)
            viewers = _to_int(match.group(1)) if match else None
        game = _pick(alert, "game_name")
        if not game:
            match = _PLAYING_RE.search(message)
            game = match.group(1) if match else ""
        what = "raided" + (f" · last playing {game}" if game else "")
        if viewers is not None:
            amount = f"{_num(viewers)} viewers"
    elif kind == "donation":
        value = _pick(alert, "donation_amount", "amount")
        currency = _pick(alert, "currency")
        text = _pick(alert, "donation_text")
        what = "tipped"
        if text:
            amount = str(text)
        elif value not in (None, ""):
            amount = format_money(value, currency)
        else:
            match = _DONATED_RE.search(message)
            amount = match.group(1).strip() if match else ""
    elif kind == "streak":
        count = _to_int(_pick(alert, "streak_count"))
        if count is None:
            match = _STREAK_RE.search(message)
            count = int(match.group(1)) if match else None
        what = f"watched {count} streams in a row" if count else "is on a watch streak"
    elif kind == "hype":
        level = _to_int(_pick(alert, "level", "hype_train_level"))
        if level is None:
            match = _LEVEL_RE.search(message)
            level = int(match.group(1)) if match else None
        lowered = message.lower()
        if "ended" in lowered or "end" == str(alert.get("alert_type") or "")[-3:]:
            what = "Hype Train ended" + (f" at level {level}" if level else "")
        else:
            what = "Hype Train started" + (f" · level {level}" if level else "")
        if user == "Hype Train":
            what = what.replace("Hype Train ", "", 1)
    elif kind == "modiversary":
        months = _to_int(_pick(alert, "months", "modiversary_months"))
        if not months:
            match = _MONTHS_RE.search(message)
            months = int(match.group(1)) if match else None
        unit = "month" if months == 1 else "months"
        what = f"has been a moderator for {months} {unit}" if months else "celebrated a modiversary"
    elif kind == "member":
        level = str(_pick(alert, "member_level") or "").strip()
        what = "became a member" + (f" · {level}" if level else "")
    elif kind == "milestone":
        months = _to_int(_pick(alert, "resub_month", "months", "total_months"))
        if not months:
            match = _MONTHS_RE.search(message)
            months = int(match.group(1)) if match else None
        what = f"member for {months} months" if months else "reached a member milestone"
    elif kind == "giftmember":
        qty = _to_int(_pick(alert, "gift_qty"))
        if not qty:
            match = _GIFT_RE.search(message)
            qty = _to_int(match.group(1)) if match else 1
        qty = qty or 1
        what = f"gifted {_num(qty)} membership" + ("s" if qty != 1 else "")
        if qty > 1:
            amount = f"×{_num(qty)}"
    elif kind in ("superchat", "supersticker"):
        what = "sent a Super Sticker" if kind == "supersticker" else "sent a Super Chat"
        display = _pick(alert, "display_amount")
        if display:
            amount = str(display)
        else:
            value = _pick(alert, "donation_amount", "amount")
            if value not in (None, ""):
                amount = format_money(value, _pick(alert, "currency"))
            else:
                match = _PAREN_AMOUNT_RE.search(message)
                amount = match.group(1).strip() if match else ""

    if not what:
        what = _strip_user(message, user) or label.lower()

    viewer_msg = alert.get("user_message")
    if viewer_msg in (None, ""):
        viewer_msg = None
    msg = str(viewer_msg).strip() if viewer_msg else ""
    if kind == "streak" and msg and _STREAK_RE.search(msg) is None and msg.endswith("streams in a row!"):
        msg = ""

    source = str(alert.get("source") or _stored(alert).get("source") or "").lower()
    if kind in _YOUTUBE_KINDS or source == "youtube":
        platform = "youtube"
    elif kind == "donation" or source in _TIP_PLATFORMS:
        # Tips show the service they came through (Streamlabs / StreamElements), else no mark.
        tip_source = str(_pick(alert, "donation_source") or source).lower()
        platform = tip_source if tip_source in _TIP_PLATFORMS else ""

    alert_id = alert.get("alert_id")
    alert_id = str(alert_id) if alert_id and not str(alert_id).startswith("generated_") else None
    if seq is None:
        seq = _to_int(alert.get("seq"))
    row_id = alert_id or f"row-{seq if seq is not None else int(_ts(alert) * 1000)}"
    return {
        "id": row_id,
        "alert_id": alert_id,
        "kind": kind,
        "icon": icon,
        "color": color,
        "label": label,
        "filter": filter_key,
        "user": user,
        "what": what,
        "amount": amount,
        "tier": tier,
        "duration": duration,
        "msg": msg,
        "ts": _ts(alert),
        "platform": platform,
        "restored": bool(alert.get("is_restored")),
        "seq": seq,
    }


def row_matches(row: Mapping[str, Any], filters: Mapping[str, Any], search: str = "") -> bool:
    """Filter + search rule used by both renderers (search: name, event text, message)."""
    if not filters.get("all", True):
        key = row.get("filter") or ""
        if not (key and filters.get(key, True)):
            return False
    query = (search or "").strip().lower()
    if not query:
        return True
    hay = " ".join(str(row.get(k) or "") for k in ("user", "what", "msg", "amount", "label")).lower()
    return query in hay


# --------------------------------------------------------------------------- queue state

def _queue_entry(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return None
    alert_id = raw.get("alert_id")
    entry = {
        "alert_id": str(alert_id) if alert_id else None,
        "type": str(raw.get("type") or raw.get("alert_type") or ""),
        "username": str(raw.get("username") or ""),
        "is_replay": bool(raw.get("is_replay")),
    }
    return entry


def normalize_queue_state(
    raw: Any,
    *,
    paused: bool = False,
    muted: bool = False,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Make an ``alert_queue_state`` payload safe to render (or build one from the flags)."""
    source = raw if isinstance(raw, Mapping) else {}
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    playing_raw = source.get("playing")
    playing = None
    if isinstance(playing_raw, Mapping):
        playing = _queue_entry(playing_raw) or {}
        started = _to_int(playing_raw.get("started_at_ms"))
        duration = _to_int(playing_raw.get("duration_ms"))
        playing["started_at_ms"] = started
        playing["duration_ms"] = duration if duration and duration > 0 else None
        playing["queue_seq"] = playing_raw.get("queue_seq")
    queue: List[Dict[str, Any]] = []
    for item in source.get("queue") or []:
        entry = _queue_entry(item)
        if entry is not None:
            queue.append(entry)
    return {
        "paused": bool(source.get("paused", paused)),
        "muted": bool(source.get("muted", muted)),
        "playing": playing,
        "queue": queue,
        "server_now_ms": _to_int(source.get("server_now_ms")) or now_ms,
        "revision": _to_int(source.get("revision")) if source.get("revision") is not None else -1,
        "contract": bool(source),
    }


def queue_tags(state: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """alert_id -> {"state": "playing"|"queued", "pos": 1-based play order}."""
    tags: Dict[str, Dict[str, Any]] = {}
    playing = state.get("playing") if isinstance(state, Mapping) else None
    if isinstance(playing, Mapping) and playing.get("alert_id"):
        tags[str(playing["alert_id"])] = {"state": "playing", "pos": 0}
    for pos, item in enumerate((state or {}).get("queue") or [], start=1):
        alert_id = item.get("alert_id") if isinstance(item, Mapping) else None
        if alert_id and str(alert_id) not in tags:
            tags[str(alert_id)] = {"state": "queued", "pos": pos}
    return tags


def ordinal(n: int) -> str:
    n = int(n)
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def queue_tag_text(tag: Mapping[str, Any], *, paused: bool) -> str:
    if tag.get("state") == "playing":
        return "Playing"
    return f"{'Waiting' if paused else 'Up next'} · {ordinal(int(tag.get('pos') or 1))}"


# --------------------------------------------------------------------------- view mode

VIEW_MODES = ("live", "history", "person")


def view_mode(view: Mapping[str, Any]) -> str:
    """live / history / person from the shared view (tab + condensed)."""
    if not isinstance(view, Mapping):
        return "live"
    if view.get("tab") == "previous":
        return "history"
    return "person" if view.get("condensed") else "live"


def mode_patch(mode: str) -> Dict[str, Any]:
    """View patch that selects one mode."""
    if mode == "history":
        return {"tab": "previous", "page": 1}
    if mode == "person":
        return {"tab": "current", "condensed": True}
    return {"tab": "current", "condensed": False}


def normalize_search(value: Any, limit: int = 120) -> str:
    text = str(value or "")
    text = " ".join(text.split())
    return text[:limit]


# --------------------------------------------------------------------------- by person

def person_totals(items: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Header chips for one person: the headline number per event kind."""
    acc: Dict[str, Dict[str, Any]] = {}
    for item in items:
        kind = item.get("kind") or "alert"
        slot = acc.setdefault(kind, {"count": 0, "amount": 0.0, "max": 0, "tier": None})
        slot["count"] += int(item.get("count") or 1)
        slot["amount"] += float(item.get("amount") or 0)
        slot["max"] = max(slot["max"], int(item.get("max") or 0))
        if item.get("tier"):
            slot["tier"] = item.get("tier")
    totals: List[Dict[str, Any]] = []
    for kind, slot in acc.items():
        if kind == "bits":
            text = f"{int(slot['amount']):,} bits"
        elif kind in ("donation", "superchat", "supersticker"):
            text = format_money(slot["amount"]) if slot["amount"] else str(slot["count"])
        elif kind in ("giftsub", "giftmember"):
            n = int(slot["amount"]) or slot["count"]
            text = f"{n:,} gift" + ("s" if n != 1 else "")
        elif kind == "raid":
            text = f"{slot['max']:,}"
        elif kind in ("resub", "milestone"):
            text = f"{slot['max']} mo" if slot["max"] else str(slot["count"])
        elif kind == "sub":
            text = f"T{slot['tier']}" if slot["tier"] else "Sub"
        elif kind == "streak":
            text = f"{slot['max']} streak" if slot["max"] else "Streak"
        elif kind == "follow":
            text = "Follow"
        else:
            text = str(slot["count"])
        totals.append({"kind": kind, "icon": KIND_META.get(kind, KIND_META["alert"])[0],
                       "color": KIND_META.get(kind, KIND_META["alert"])[1], "text": text})
    return totals
