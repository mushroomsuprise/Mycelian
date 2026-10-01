# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Convert donation amounts into the currency used by donation alert thresholds."""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

FRANKFURTER_LATEST = "https://api.frankfurter.app/latest"
RATE_TTL_SEC = 3600.0

# ECB currencies Frankfurter can convert. Alert thresholds are amounts in one of these.
ALERT_CURRENCIES = (
    "USD",
    "EUR",
    "GBP",
    "CAD",
    "AUD",
    "JPY",
    "CHF",
    "SEK",
    "NOK",
    "DKK",
    "NZD",
    "PLN",
    "CZK",
    "HUF",
    "MXN",
    "BRL",
    "ZAR",
    "SGD",
    "HKD",
    "INR",
    "KRW",
    "TRY",
    "ILS",
    "PHP",
    "THB",
    "CNY",
    "RON",
    "BGN",
    "ISK",
    "IDR",
    "MYR",
)

# Prefix symbols. Alphabetic symbols are placed after the number.
_SYMBOLS: Dict[str, str] = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "CAD": "$",
    "AUD": "$",
    "NZD": "$",
    "SGD": "$",
    "HKD": "$",
    "MXN": "$",
    "JPY": "¥",
    "CNY": "¥",
    "CHF": "CHF",
    "SEK": "kr",
    "NOK": "kr",
    "DKK": "kr",
    "ISK": "kr",
    "PLN": "zł",
    "CZK": "Kč",
    "HUF": "Ft",
    "BRL": "R$",
    "ZAR": "R",
    "INR": "₹",
    "KRW": "₩",
    "TRY": "₺",
    "ILS": "₪",
    "PHP": "₱",
    "THB": "฿",
    "RON": "lei",
    "BGN": "лв",
    "IDR": "Rp",
    "MYR": "RM",
}

_cache_lock = threading.Lock()
# (source, target) -> (rate, monotonic timestamp)
_RATE_CACHE: Dict[Tuple[str, str], Tuple[float, float]] = {}


def normalize_currency(code: Optional[str], default: str = "USD") -> str:
    text = (code or "").strip().upper()
    if len(text) == 3 and text.isalpha():
        return text
    fallback = (default or "USD").strip().upper()
    return fallback if len(fallback) == 3 else "USD"


def format_money(amount: float, currency: str) -> str:
    """Format an amount with a currency symbol. Always two decimal places."""
    code = normalize_currency(currency)
    number = f"{float(amount):.2f}"
    symbol = _SYMBOLS.get(code, "")
    if not symbol:
        return f"{code} {number}"
    if symbol.isalpha():
        return f"{number} {symbol}"
    return f"{symbol}{number}"


def donation_amount_phrase(
    original_amount: float,
    original_currency: str,
    converted_amount: float,
    converted_currency: str,
    *,
    show_original: bool,
) -> str:
    """Amount text for an alert header.

    When ``show_original`` is on and the currencies differ, the sent amount
    comes first and the converted amount follows in parentheses.
    """
    target = normalize_currency(converted_currency)
    source = normalize_currency(original_currency, default=target)
    converted_text = format_money(converted_amount, target)
    if not show_original or source == target:
        return converted_text
    return f"{format_money(original_amount, source)} ({converted_text})"


def donation_match_quantity(amount: float) -> int:
    """Integer used to pick an exact or range donation alert.

    Matches the existing YouTube rule: round to the nearest whole unit, and
    never below 1 when the amount is positive.
    """
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return 1
    if value <= 0:
        return 1
    return max(1, int(round(value)))


def _cached_rate(source: str, target: str) -> Optional[float]:
    with _cache_lock:
        hit = _RATE_CACHE.get((source, target))
        if not hit:
            return None
        rate, stored = hit
        if time.monotonic() - stored > RATE_TTL_SEC:
            return None
        return rate


def _store_rate(source: str, target: str, rate: float) -> None:
    with _cache_lock:
        _RATE_CACHE[(source, target)] = (float(rate), time.monotonic())


def _fetch_rate(source: str, target: str) -> Optional[float]:
    try:
        response = requests.get(
            FRANKFURTER_LATEST,
            params={"from": source, "to": target},
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        rates = payload.get("rates") or {}
        rate = rates.get(target)
        if rate is None:
            return None
        value = float(rate)
        if value <= 0:
            return None
        return value
    except Exception as exc:
        logger.warning(
            "Currency rate %s to %s unavailable: %s", source, target, exc
        )
        return None


def convert_amount(
    amount: float, source_currency: str, target_currency: str
) -> Tuple[float, bool]:
    """Return ``(converted_amount, used_original)``.

    ``used_original`` is True when no rate was available and the caller should
    match the donor's amount as-is.
    """
    source = normalize_currency(source_currency)
    target = normalize_currency(target_currency)
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return 0.0, True
    if source == target:
        return value, False
    rate = _cached_rate(source, target)
    if rate is None:
        rate = _fetch_rate(source, target)
        if rate is None:
            logger.warning(
                "No conversion for %s to %s; matching the original amount",
                source,
                target,
            )
            return value, True
        _store_rate(source, target, rate)
    return value * rate, False
