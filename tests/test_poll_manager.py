#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Vote rules, winner selection, and poll reload behavior."""

from __future__ import annotations

import threading
import time
import unittest

from modules.poll_manager import (
    PollManager,
    earliest_category,
    keyword_match_span,
    normalize_keyword,
)
from modules.statistics_manager import StatisticsData, StatisticsManager


def _open_poll() -> PollManager:
    manager = PollManager(persist=False)
    manager.ensure_template("poll")
    manager.add_category("poll", "Yes")
    manager.add_category("poll", "No")
    ok, message = manager.start_poll("poll")
    if not ok:
        raise AssertionError(message)
    return manager


def _votes(manager: PollManager, template: str = "poll") -> dict:
    snapshot = manager.get_overlay_state(template)
    return {row["name"]: row["votes"] for row in snapshot["options"]}


class PollKeywordTests(unittest.TestCase):
    def test_keyword_is_a_token_and_case_insensitive(self):
        self.assertIsNotNone(keyword_match_span("I vote #yes", "#yes"))
        self.assertIsNotNone(keyword_match_span("#YES!", "#yes"))
        self.assertIsNone(keyword_match_span("#yesterday", "#yes"))
        self.assertIsNone(keyword_match_span("yes", "#yes"))

    def test_normalize_adds_hash_and_removes_spaces(self):
        self.assertEqual(normalize_keyword("Pizza Time"), "#PizzaTime")
        self.assertEqual(normalize_keyword("yes"), "#yes")
        self.assertEqual(normalize_keyword("#Hello World"), "#HelloWorld")

    def test_earliest_keyword_wins(self):
        categories = [
            {"id": "a", "name": "Yes", "keyword": "#yes"},
            {"id": "b", "name": "No", "keyword": "#no"},
        ]
        chosen = earliest_category("I pick #no before #yes", categories)
        self.assertEqual(chosen["id"], "b")


class PollVoteTests(unittest.TestCase):
    def test_first_vote_locks_until_switching_is_enabled(self):
        manager = _open_poll()
        message = {
            "username": "Ada",
            "userid": "1",
            "message": "I vote #Yes",
            "platform": "twitch",
        }
        self.assertTrue(manager.try_register_vote(message))
        self.assertFalse(
            manager.try_register_vote({**message, "message": "actually #No"})
        )
        self.assertEqual(_votes(manager), {"Yes": 1, "No": 0})

        manager.update_config("poll", {"allow_vote_switch": True})
        self.assertTrue(manager.try_register_vote({**message, "message": "#No"}))
        self.assertEqual(_votes(manager), {"Yes": 0, "No": 1})
        self.assertFalse(manager.try_register_vote({**message, "message": "#No again"}))
        self.assertEqual(_votes(manager)["No"], 1)

    def test_without_one_entry_every_message_counts(self):
        manager = _open_poll()
        manager.update_config(
            "poll", {"one_entry_per_user": False, "allow_vote_switch": True}
        )
        message = {"username": "Ada", "userid": "1", "message": "#Yes", "platform": "twitch"}
        self.assertTrue(manager.try_register_vote(message))
        self.assertTrue(manager.try_register_vote(message))
        self.assertEqual(_votes(manager)["Yes"], 2)

    def test_options_sort_highest_votes_first_and_ties_both_win(self):
        manager = _open_poll()
        manager.try_register_vote(
            {"username": "A", "userid": "1", "message": "#No", "platform": "twitch"}
        )
        manager.try_register_vote(
            {"username": "B", "userid": "2", "message": "#Yes", "platform": "twitch"}
        )
        manager.try_register_vote(
            {"username": "C", "userid": "3", "message": "#No", "platform": "youtube"}
        )
        snapshot = manager.get_overlay_state("poll")
        self.assertEqual([row["name"] for row in snapshot["options"]], ["No", "Yes"])
        ok, _message = manager.stop_poll("poll")
        self.assertTrue(ok)
        ended = manager.get_overlay_state("poll")
        self.assertEqual(ended["status"], "ended")
        self.assertEqual([row["name"] for row in ended["winners"]], ["No"])

        fresh = _open_poll()
        fresh.try_register_vote(
            {"username": "A", "userid": "1", "message": "#Yes", "platform": "twitch"}
        )
        fresh.try_register_vote(
            {"username": "B", "userid": "2", "message": "#No", "platform": "twitch"}
        )
        fresh.stop_poll("poll")
        winners = [row["name"] for row in fresh.get_overlay_state("poll")["winners"]]
        self.assertEqual(winners, ["Yes", "No"])

    def test_zero_votes_has_no_winner(self):
        manager = _open_poll()
        ok, message = manager.stop_poll("poll")
        self.assertTrue(ok)
        self.assertIn("no votes", message)
        self.assertEqual(manager.get_overlay_state("poll")["winners"], [])

    def test_banner_tokens_replace_options_before_option(self):
        manager = PollManager(persist=False)
        single = manager._banner_text(
            "Poll winner: {option} ({votes})",
            [{"name": "Yes", "keyword": "#Yes", "votes": 4}],
        )
        self.assertEqual(single, "Poll winner: Yes (4)")
        tied = manager._banner_text(
            "{options}",
            [
                {"name": "Yes", "keyword": "#Yes", "votes": 2},
                {"name": "No", "keyword": "#No", "votes": 2},
            ],
        )
        self.assertEqual(tied, "Yes, No")


class PollStatisticsTests(unittest.TestCase):
    def test_lifetime_most_votes_and_wins(self):
        stats = StatisticsManager.__new__(StatisticsManager)
        stats.data = StatisticsData()
        stats._data_lock = threading.RLock()
        stats._stats_db_initialized = False
        stats._STATS_SYSTEM_USERNAME = "__system__"
        stats.record_poll_finished(
            [
                {"name": "Yes", "keyword": "#Yes", "votes": 3},
                {"name": "No", "keyword": "#No", "votes": 1},
            ],
            [{"name": "Yes", "keyword": "#Yes", "votes": 3}],
        )
        stats.record_poll_finished(
            [
                {"name": "Yes", "keyword": "#yes", "votes": 2},
                {"name": "No", "keyword": "#No", "votes": 2},
            ],
            [
                {"name": "Yes", "keyword": "#Yes", "votes": 2},
                {"name": "No", "keyword": "#No", "votes": 2},
            ],
        )
        summary = stats.get_all_statistics()["polls"]
        self.assertEqual(summary["polls_completed"], 2)
        self.assertEqual(summary["total_votes"], 8)
        self.assertEqual(summary["most_votes"]["name"], "Yes")
        self.assertEqual(summary["most_votes"]["count"], 5)
        self.assertEqual(summary["most_wins"]["name"], "Yes")
        self.assertEqual(summary["most_wins"]["count"], 2)
        self.assertEqual(summary["options"]["#no"]["wins"], 1)


class PollReloadTests(unittest.TestCase):
    def _stored(self, *, store_data, duration, ends_at, votes):
        now = time.time()
        return now, {
            "templates": {
                "poll": {
                    "config": {
                        "categories": [
                            {"id": "a", "name": "Yes", "keyword": "#Yes"},
                            {"id": "b", "name": "No", "keyword": "#No"},
                        ],
                        "duration_seconds": duration,
                        "store_data": store_data,
                        "one_entry_per_user": True,
                        "allow_vote_switch": False,
                    },
                    "runtime": {
                        "status": "open",
                        "started_at": now - 30,
                        "ends_at": ends_at,
                        "votes": votes,
                        "voters": {"twitch:1": "a"},
                    },
                }
            }
        }

    def test_expired_poll_is_not_restored(self):
        manager = PollManager(persist=False)
        now, raw = self._stored(
            store_data=True, duration=60, ends_at=time.time() - 30, votes={"a": 3, "b": 1}
        )
        manager._apply_stored(raw, now, schedule=False)
        snapshot = manager.get_overlay_state("poll")
        self.assertEqual(snapshot["status"], "idle")
        self.assertEqual(snapshot["total_votes"], 0)
        self.assertEqual(len(manager.get_config("poll")["categories"]), 2)

    def test_time_remaining_reloads(self):
        manager = PollManager(persist=False)
        now, raw = self._stored(
            store_data=True, duration=90, ends_at=time.time() + 40, votes={"a": 2, "b": 1}
        )
        manager._apply_stored(raw, now, schedule=False)
        snapshot = manager.get_overlay_state("poll")
        self.assertEqual(snapshot["status"], "open")
        self.assertEqual(snapshot["total_votes"], 3)
        self.assertGreater(snapshot["remaining_seconds"], 0)
        self.assertFalse(
            manager.try_register_vote(
                {"username": "Ada", "userid": "1", "message": "#No", "platform": "twitch"}
            )
        )
        self.assertEqual(_votes(manager)["Yes"], 2)

    def test_permanent_poll_always_reloads(self):
        manager = PollManager(persist=False)
        now, raw = self._stored(
            store_data=True, duration=0, ends_at=time.time() - 50, votes={"a": 5, "b": 1}
        )
        manager._apply_stored(raw, now, schedule=False)
        snapshot = manager.get_overlay_state("poll")
        self.assertEqual(snapshot["status"], "open")
        self.assertTrue(snapshot["permanent"])
        self.assertIsNone(snapshot["remaining_seconds"])
        self.assertEqual(snapshot["total_votes"], 6)

    def test_store_data_off_drops_runtime(self):
        manager = PollManager(persist=False)
        now, raw = self._stored(
            store_data=False, duration=0, ends_at=None, votes={"a": 4, "b": 0}
        )
        manager._apply_stored(raw, now, schedule=False)
        self.assertEqual(manager.get_overlay_state("poll")["status"], "idle")
        self.assertTrue(manager.get_config("poll")["categories"])


if __name__ == "__main__":
    unittest.main()
