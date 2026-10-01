# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Turn a cash donation into a Mycelian donation alert."""

from __future__ import annotations

import logging
import re
import threading
import time
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
_seen_ids: set[str] = set()
_SEEN_LIMIT = 500


def _remember_event(event_id: str) -> bool:
    """Return True if this id was already handled."""
    key = (event_id or "").strip()
    if not key:
        return False
    with _seen_lock:
        if key in _seen_ids:
            return True
        _seen_ids.add(key)
        if len(_seen_ids) > _SEEN_LIMIT:
            for old in list(_seen_ids)[:100]:
                _seen_ids.discard(old)
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
    alert_id = f"Donation{source}{safe_id or str(int(now * 1000))}"

    from . import alert_processor, alertutils

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
        alert_processor.enqueue_alert(alert)
        alertutils.alert_state_manager.store_completed_alert(alert.alert_id, stored)
    except Exception:
        logger.error("Failed to enqueue donation alert", exc_info=True)
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
                }
            )
    except Exception:
        logger.debug("Donation instant alert skipped", exc_info=True)


def _send_feed(alert, phrase: str) -> None:
    try:
        from .uiwindows.activity_feed import add_alert_to_feed

        add_alert_to_feed(
            alert_type="Donation",
            message=f"{alert.username} donated {phrase}!",
            badge_type="donation",
            timestamp=str(int(alert.timestamp)),
            user_message=alert.message or None,
            alert_id=alert.alert_id,
        )
    except Exception:
        logger.error("Donation activity feed update failed", exc_info=True)
