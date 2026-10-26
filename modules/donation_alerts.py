# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Turn a cash donation into a Mycelian donation alert."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Optional

from .donation_currency import (
    convert_amount,
    donation_amount_phrase,
    donation_match_quantity,
    normalize_currency,
)
from .donation_settings import get_settings

logger = logging.getLogger(__name__)

_seen_lock = threading.Lock()
# Insertion-ordered, so eviction drops the oldest ids.
_seen_ids: "OrderedDict[str, None]" = OrderedDict()
_SEEN_LIMIT = 500


def _remember_event(event_id: str) -> bool:
    """Return True if this id was already handled."""
    key = (event_id or "").strip()
    if not key:
        return False
    with _seen_lock:
        if key in _seen_ids:
            return True
        _seen_ids[key] = None
        while len(_seen_ids) > _SEEN_LIMIT:
            _seen_ids.popitem(last=False)
        return False


def emit_donation_alert(
    *,
    username: str,
    amount: float,
    currency: str,
    message: str = "",
    source: str,
    event_id: str = "",
    enqueue: bool = True,
    is_supersticker: bool = False,
) -> Optional[object]:
    """Match a donation alert in the user's currency and show it.

    Chatbot and connector donation events still fire when ``enqueue`` is false,
    so a disabled YouTube alert switch does not drop the chatbot event.
    """
    if event_id and _remember_event(f"{source}:{event_id}"):
        logger.debug("Skipping duplicate donation %s:%s", source, event_id)
        return None

    donor = (username or "").strip() or "Anonymous"
    note = (message or "").strip()
    source_currency = normalize_currency(currency)
    try:
        original_amount = float(amount)
    except (TypeError, ValueError):
        original_amount = 0.0

    target_currency = normalize_currency(get_settings().alert_currency)
    converted, used_original = convert_amount(
        original_amount, source_currency, target_currency
    )
    match_currency = source_currency if used_original else target_currency
    match_amount = original_amount if used_original else converted
    phrase = donation_amount_phrase(
        original_amount,
        source_currency,
        match_amount,
        match_currency,
        show_original=True,
    )
    quantity = donation_match_quantity(match_amount)
    now = time.time()
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "", event_id or "")[:80]
    from . import alert_processor, alertutils

    alert_id = (
        f"Donation{source}{safe_id}"
        if safe_id
        else alertutils.new_alert_id(f"Donation{source}")
    )

    alert = alertutils.fetch_donation_alert(quantity)
    if alert is None:
        alert = alertutils.AlertObj()
    alert.username = donor
    alert.alert_type = "donation"
    alert.donation_amount = float(match_amount)
    alert.currency = match_currency
    alert.original_amount = float(original_amount)
    alert.original_currency = source_currency
    alert.donation_source = source
    alert.donation_text = phrase
    alert.message = note
    alert.alert_id = alert_id
    alert.timestamp = now
    if is_supersticker:
        # Not an AlertObj field: rides along on the object, the stored record,
        # instant_alert and the feed row so every surface labels it a Super Sticker.
        alert.is_supersticker = True

    _fire_chatbot(
        donor,
        match_amount,
        match_currency,
        note,
        phrase,
        source,
        original_amount,
        source_currency,
        now,
    )
    _fire_connector(
        donor,
        match_amount,
        match_currency,
        note,
        source,
        original_amount,
        source_currency,
        phrase,
    )

    if not enqueue:
        return alert

    stored = dict(alert.__dict__)
    stored["source"] = source
    try:
        # Store first: the queue thread may start playing it right away.
        alertutils.alert_state_manager.store_completed_alert(alert.alert_id, stored)
        alert_processor.enqueue_alert(alert)
    except Exception as exc:
        logger.error("Failed to enqueue donation alert", exc_info=True)
        try:
            from .notification_engine import nav_actions_main_tab, notify

            service = {"streamlabs": "Streamlabs", "streamelements": "StreamElements"}.get(
                str(source).lower(), str(source)
            )
            notify(
                f"The {phrase} {service} donation from {donor} wasn't shown on stream. "
                "Connectors for it still ran.",
                type="error",
                source="alerts",
                title="A donation alert couldn't play",
                hint="Check that the donation alert is set up in the Alerts tab. If it keeps happening, restart Mycelian.",
                details=f"{alert_id}: {type(exc).__name__}: {exc}",
                dedupe_key="alert:donation_enqueue",
                dedupe_cooldown_sec=300.0,
                actions=nav_actions_main_tab("Alerts"),
            )
        except Exception:
            logger.debug("Donation alert failure notice failed", exc_info=True)
        return alert

    _send_instant(alert, source, phrase)
    _send_feed(alert, phrase)
    logger.info(
        "Donation alert from %s: %s %s via %s -> %s",
        donor,
        f"{original_amount:.2f}",
        source_currency,
        source,
        phrase,
    )
    return alert


def _fire_chatbot(
    username: str,
    amount: float,
    currency: str,
    message: str,
    phrase: str,
    source: str,
    original_amount: float,
    original_currency: str,
    timestamp: float,
) -> None:
    try:
        from .chatbot import dispatch_process_event_result
        from .chatbot_core import EventType
        from .chatbot_manager import get_manager as get_chatbot_manager

        result = get_chatbot_manager().process_event(
            EventType.DONATION,
            {
                "username": username,
                "amount": amount,
                "currency": currency,
                "formatted_amount": phrase,
                "message": message,
                "donation_message": message,
                "original_amount": original_amount,
                "original_currency": original_currency,
                "timestamp": timestamp,
                "source": source,
            },
        )
        dispatch_process_event_result(
            result,
            default_targets=["youtube"] if source == "youtube" else ["twitch"],
        )
    except Exception:
        logger.debug("Donation chatbot event skipped", exc_info=True)


def _fire_connector(
    username: str,
    amount: float,
    currency: str,
    message: str,
    source: str,
    original_amount: float,
    original_currency: str,
    phrase: str,
) -> None:
    try:
        from .connector_core import EventData
        from .connector_manager import get_manager

        manager = get_manager()
        if manager and manager.is_running:
            manager._schedule_event_on_connector_loop(
                EventData.from_donation(
                    amount=amount,
                    username=username,
                    message=message,
                    currency=currency,
                    source=source,
                    original_amount=original_amount,
                    original_currency=original_currency,
                    formatted_amount=phrase,
                    display_amount=phrase,
                )
            )
    except Exception:
        logger.debug("Donation connector event skipped", exc_info=True)


def _send_instant(alert, source: str, phrase: str) -> None:
    try:
        from . import web_engine

        engine = getattr(web_engine, "web_engine_instance", None)
        if engine:
            engine.instant_alert(
                {
                    "type": "donation",
                    "username": alert.username,
                    "donation_amount": alert.donation_amount,
                    "currency": alert.currency,
                    "original_amount": alert.original_amount,
                    "original_currency": alert.original_currency,
                    "donation_text": phrase,
                    "message": alert.message,
                    "alert_id": alert.alert_id,
                    "timestamp": alert.timestamp,
                    "source": source,
                    "is_supersticker": bool(getattr(alert, "is_supersticker", False)),
                }
            )
    except Exception:
        logger.debug("Donation instant alert skipped", exc_info=True)


def _send_feed(alert, phrase: str) -> None:
    try:
        import inspect

        from .uiwindows.activity_feed import add_alert_to_feed

        kwargs = dict(
            alert_type="Donation",
            message=f"{alert.username} donated {phrase}!",
            badge_type="donation",
            timestamp=str(int(alert.timestamp)),
            user_message=alert.message or None,
            alert_id=alert.alert_id,
            donation_source=getattr(alert, "donation_source", "") or None,
        )
        if getattr(alert, "is_supersticker", False):
            params = inspect.signature(add_alert_to_feed).parameters
            if "is_supersticker" in params or any(
                p.kind is p.VAR_KEYWORD for p in params.values()
            ):
                kwargs["is_supersticker"] = True
        add_alert_to_feed(**kwargs)
    except Exception:
        logger.error("Donation activity feed update failed", exc_info=True)
