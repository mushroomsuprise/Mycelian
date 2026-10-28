# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/donation_currency.py (rates are faked; no real currency API calls)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import donation_currency as dc  # noqa: E402


class FakeResponse:
    def __init__(self, payload=None, status_error=None, json_error=None):
        self.payload = payload
        self.status_error = status_error
        self.json_error = json_error

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    monkeypatch.setattr(dc, "_RATE_CACHE", {})


@pytest.fixture
def fake_get(monkeypatch):
    calls = []
    state = {"response": FakeResponse({"rates": {"USD": 1.1}})}

    def get(url, params=None, timeout=None):
        calls.append((url, params, timeout))
        resp = state["response"]
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(dc.requests, "get", get)
    state["calls"] = calls
    return state


@pytest.mark.parametrize("code,default,expected", [
    ("eur", "USD", "EUR"),
    ("  gbp ", "USD", "GBP"),
    (None, "USD", "USD"),
    ("", "cad", "CAD"),
    ("EURO", "JPY", "JPY"),
    ("E1R", "USD", "USD"),
    ("xx", "toolong", "USD"),
    ("xx", None, "USD"),
    ("xx", "", "USD"),
])
def test_normalize_currency(code, default, expected):
    assert dc.normalize_currency(code, default) == expected


def test_normalize_currency_default_arg():
    assert dc.normalize_currency("bad!") == "USD"


@pytest.mark.parametrize("amount,cur,expected", [
    (5, "USD", "$5.00"),
    (5.555, "EUR", "€5.55" if f"{5.555:.2f}" == "5.55" else "€5.56"),
    (10, "chf", "10.00 CHF"),
    (3, "SEK", "3.00 kr"),
    (2, "RON", "2.00 lei"),
    (7, "XYZ", "XYZ 7.00"),
    (1, "BGN", "1.00 лв"),  # Cyrillic letters count as alphabetic
    ("4", "INR", "₹4.00"),
    (1, "BRL", "R$1.00"),
])
def test_format_money(amount, cur, expected):
    assert dc.format_money(amount, cur) == expected


def test_donation_amount_phrase_same_currency():
    assert dc.donation_amount_phrase(5, "USD", 5, "USD", show_original=True) == "$5.00"


def test_donation_amount_phrase_hidden_original():
    assert dc.donation_amount_phrase(5, "EUR", 5.5, "USD", show_original=False) == "$5.50"


def test_donation_amount_phrase_shows_original():
    assert dc.donation_amount_phrase(5, "EUR", 5.5, "USD", show_original=True) == "€5.00 ($5.50)"


def test_donation_amount_phrase_bad_source_falls_back_to_target():
    assert dc.donation_amount_phrase(5, "??", 5, "GBP", show_original=True) == "£5.00"


@pytest.mark.parametrize("amount,expected", [
    (0, 1), (-3, 1), (0.2, 1), (0.6, 1), (1.4, 1), (1.6, 2), (2.5, 2), (10, 10),
    ("7.7", 8), ("abc", 1), (None, 1),
])
def test_donation_match_quantity(amount, expected):
    assert dc.donation_match_quantity(amount) == expected


def test_cache_store_and_expiry(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(dc.time, "monotonic", lambda: now[0])
    assert dc._cached_rate("EUR", "USD") is None
    dc._store_rate("EUR", "USD", 2)
    assert dc._RATE_CACHE[("EUR", "USD")] == (2.0, 1000.0)
    now[0] += dc.RATE_TTL_SEC
    assert dc._cached_rate("EUR", "USD") == 2.0
    now[0] += 1
    assert dc._cached_rate("EUR", "USD") is None


def test_fetch_rate_success(fake_get):
    assert dc._fetch_rate("EUR", "USD") == 1.1
    url, params, timeout = fake_get["calls"][0]
    assert url == dc.FRANKFURTER_LATEST
    assert params == {"from": "EUR", "to": "USD"}
    assert timeout == 8


@pytest.mark.parametrize("response", [
    FakeResponse({"rates": {}}),
    FakeResponse({"rates": None}),
    FakeResponse({}),
    FakeResponse({"rates": {"USD": 0}}),
    FakeResponse({"rates": {"USD": -2}}),
    FakeResponse({"rates": {"USD": "bad"}}),
    FakeResponse(status_error=RuntimeError("500")),
    FakeResponse(json_error=ValueError("not json")),
    ConnectionError("offline"),
])
def test_fetch_rate_failures(fake_get, response):
    fake_get["response"] = response
    assert dc._fetch_rate("EUR", "USD") is None


def test_convert_bad_amount(fake_get):
    assert dc.convert_amount("abc", "EUR", "USD") == (0.0, True)
    assert dc.convert_amount(None, "EUR", "USD") == (0.0, True)
    assert fake_get["calls"] == []


def test_convert_same_currency(fake_get):
    assert dc.convert_amount("3.5", "usd", "USD") == (3.5, False)
    assert fake_get["calls"] == []


def test_convert_fetches_then_caches(fake_get):
    assert dc.convert_amount(10, "EUR", "USD") == pytest.approx((11.0, False))
    assert dc.convert_amount(20, "eur", "usd") == pytest.approx((22.0, False))
    assert len(fake_get["calls"]) == 1
    assert ("EUR", "USD") in dc._RATE_CACHE


def test_convert_no_rate_uses_original(fake_get):
    fake_get["response"] = ConnectionError("down")
    assert dc.convert_amount(10, "EUR", "USD") == (10.0, True)
    assert dc._RATE_CACHE == {}


def test_currency_tables_consistent():
    assert len(set(dc.ALERT_CURRENCIES)) == len(dc.ALERT_CURRENCIES)
    assert set(dc._SYMBOLS) == set(dc.ALERT_CURRENCIES)
