# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Donation service filters, moderation, and currency conversion."""

from __future__ import annotations

import base64
import json
import time
import unittest

from modules.donation_currency import (
    convert_amount,
    donation_amount_phrase,
    donation_match_quantity,
)
from modules.streamlabs import (
    DonationDeduper,
    is_streamlabs_donation_event,
    iter_streamlabs_donations,
    parse_engineio_event,
)
from modules.streamelements import (
    TipGate,
    classify_tip,
    jwt_is_expired,
    jwt_payload,
    normalize_realtime_tip,
    parse_socketio_message,
)


LIVE_DONATION = {
    "type": "donation",
    "for": "streamlabs",
    "event_id": "evt_live",
    "message": [
        {
            "id": 42,
            "name": "Ada",
            "amount": "13.37",
            "message": "hello",
            "currency": "USD",
            "_id": "abc",
        }
    ],
}

TEST_DONATION = {
    "type": "donation",
    "message": [
        {
            "name": "test",
            "amount": 5,
            "message": "test donation",
            "currency": "USD",
            "_id": "test-id",
        }
    ],
}

BITS_EVENT = {
    "type": "bits",
    "for": "twitch_account",
    "message": [{"name": "cheer", "amount": "100"}],
}


class StreamlabsFilterTests(unittest.TestCase):
    def test_live_donation_with_for_streamlabs_is_accepted(self) -> None:
        donations = iter_streamlabs_donations(LIVE_DONATION)
        self.assertEqual(len(donations), 1)
        self.assertEqual(donations[0]["username"], "Ada")
        self.assertAlmostEqual(donations[0]["amount"], 13.37)
        self.assertIn("42", donations[0]["ids"])

    def test_test_donation_without_for_is_accepted(self) -> None:
        self.assertTrue(is_streamlabs_donation_event(TEST_DONATION))
        donations = iter_streamlabs_donations(TEST_DONATION)
        self.assertEqual(donations[0]["username"], "test")

    def test_twitch_bits_are_ignored(self) -> None:
        self.assertFalse(is_streamlabs_donation_event(BITS_EVENT))
        self.assertEqual(iter_streamlabs_donations(BITS_EVENT), [])

    def test_engineio_event_packet(self) -> None:
        packet = '42["event", {"type": "donation", "for": "streamlabs", "message": []}]'
        event = parse_engineio_event(packet)
        self.assertIsNotNone(event)
        self.assertEqual(event["type"], "donation")
        self.assertIsNone(parse_engineio_event("40"))

    def test_poll_prime_does_not_replay_then_live_id_alerts_once(self) -> None:
        deduper = DonationDeduper()
        history = iter_streamlabs_donations(LIVE_DONATION)
        self.assertEqual(deduper.accept_poll_batch(history), [])
        self.assertFalse(deduper.accept_live(history[0]))
        newer = {
            "type": "donation",
            "for": "streamlabs",
            "message": [
                {
                    "donation_id": "99",
                    "name": "Bea",
                    "amount": "2.00",
                    "currency": "EUR",
                    "message": "new",
                    "created_at": str(int(time.time()) + 30),
                }
            ],
        }
        fresh = iter_streamlabs_donations(newer)[0]
        self.assertTrue(deduper.accept_live(fresh))
        self.assertEqual(deduper.accept_poll_batch([fresh]), [])


class StreamElementsModerationTests(unittest.TestCase):
    def _tip(self, approved: str, tip_id: str = "tip-1") -> dict:
        return {
            "_id": tip_id,
            "approved": approved,
            "status": "success",
            "donation": {
                "user": {"username": "Styler"},
                "message": "nice",
                "amount": 4.2,
                "currency": "USD",
            },
        }

    def test_pending_waits_allowed_alerts_once_rejected_drops(self) -> None:
        gate = TipGate()
        self.assertIsNone(gate.consider(self._tip("pending")))
        self.assertEqual(classify_tip(self._tip("pending")), "hold")
        first = gate.consider(self._tip("allowed"))
        self.assertIsNotNone(first)
        self.assertEqual(first["username"], "Styler")
        self.assertAlmostEqual(first["amount"], 4.2)
        self.assertIsNone(gate.consider(self._tip("allowed")))
        self.assertIsNone(gate.consider(self._tip("rejected", "tip-2")))
        self.assertEqual(classify_tip(self._tip("rejected", "tip-2")), "drop")

    def test_overlay_emulated_tip_is_a_donation(self) -> None:
        packet = (
            '42["event:test", {"listener": "tip-latest", "event": '
            '{"name": "OverlayTest", "amount": 10, "message": "emulated"}}]'
        )
        parsed = parse_socketio_message(packet)
        self.assertIsNotNone(parsed)
        name, payload = parsed
        self.assertEqual(name, "event:test")
        tip = normalize_realtime_tip(payload)
        self.assertIsNotNone(tip)
        self.assertEqual(tip["username"], "OverlayTest")
        self.assertAlmostEqual(tip["amount"], 10)
        self.assertEqual(tip["message"], "emulated")
        self.assertEqual(tip["currency"], "")

    def test_emulated_follow_is_ignored(self) -> None:
        self.assertIsNone(
            normalize_realtime_tip(
                {"listener": "follower-latest", "event": {"name": "Viewer"}}
            )
        )

    def test_realtime_activity_tip_is_accepted(self) -> None:
        tip = normalize_realtime_tip(
            {
                "type": "tip",
                "data": {
                    "username": "Ada",
                    "amount": "8.5",
                    "currency": "EUR",
                    "message": "hello",
                    "_id": "tip-9",
                },
            }
        )
        self.assertEqual(tip["username"], "Ada")
        self.assertAlmostEqual(tip["amount"], 8.5)
        self.assertEqual(tip["currency"], "EUR")
        self.assertEqual(tip["event_id"], "tip-9")

    def test_jwt_exp_in_the_past_is_expired(self) -> None:
        payload = base64.urlsafe_b64encode(
            json.dumps({"exp": 1, "channel": "abc"}).encode()
        ).decode().rstrip("=")
        token = f"header.{payload}.sig"
        self.assertTrue(jwt_is_expired(token))
        self.assertEqual(jwt_payload(token)["channel"], "abc")

        future = base64.urlsafe_b64encode(
            json.dumps({"exp": 9_999_999_999}).encode()
        ).decode().rstrip("=")
        self.assertFalse(jwt_is_expired(f"header.{future}.sig"))
        self.assertFalse(jwt_is_expired("not-a-jwt"))


class DonationCurrencyTests(unittest.TestCase):
    def test_eur_conversion_matches_rounded_amount_and_phrase(self) -> None:
        from modules import donation_currency

        donation_currency._store_rate("EUR", "USD", 1.08)
        converted, failed = convert_amount(10, "EUR", "USD")
        self.assertFalse(failed)
        self.assertAlmostEqual(converted, 10.8)
        self.assertEqual(donation_match_quantity(converted), int(round(10.8)))
        self.assertEqual(
            donation_amount_phrase(10, "EUR", converted, "USD", show_original=True),
            "€10.00 ($10.80)",
        )

    def test_same_currency_is_a_single_amount(self) -> None:
        converted, failed = convert_amount(5, "USD", "USD")
        self.assertFalse(failed)
        self.assertEqual(converted, 5)
        self.assertEqual(
            donation_amount_phrase(5, "USD", converted, "USD", show_original=True),
            "$5.00",
        )
        self.assertEqual(
            donation_amount_phrase(5, "USD", converted, "USD", show_original=False),
            "$5.00",
        )


if __name__ == "__main__":
    unittest.main()
