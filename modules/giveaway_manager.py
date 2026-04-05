#!/usr/bin/env python3
"""
Giveaway pool, configuration, and draw logic for the chatbot.
"""

from __future__ import annotations

import logging
import random
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DATA_PATH = "BotData/Giveaway"

DEFAULT_CONFIG: Dict[str, Any] = {
    "keyword": "",
    "no_duplicate_entries": True,
    "unique_winners_per_draw": True,
    "num_winners": 1,
    "exclude_mods": False,
    "exclude_vips": False,
    "blocked_usernames": [],
    "winning_message_template": "Congratulations {winners}!",
}


@dataclass
class GiveawayEntry:
    user_id: str
    display_name: str


_giveaway_manager: Optional["GiveawayManager"] = None
_giveaway_lock = threading.Lock()


def get_giveaway_manager() -> "GiveawayManager":
    global _giveaway_manager
    with _giveaway_lock:
        if _giveaway_manager is None:
            _giveaway_manager = GiveawayManager()
        return _giveaway_manager


class GiveawayManager:
    """Thread-safe giveaway state and operations."""

    def __init__(self):
        self._lock = threading.RLock()
        self._accepting_entries = False
        self._pool: List[GiveawayEntry] = []
        self._config: Dict[str, Any] = dict(DEFAULT_CONFIG)
        self._last_error: str = ""
        self._load_config()

    def _load_config(self) -> None:
        try:
            from .database_manager import get_data

            raw = get_data(DATA_PATH)
            if isinstance(raw, dict) and raw:
                merged = dict(DEFAULT_CONFIG)
                merged.update(raw)
                self._normalize_config_in_place(merged)
                self._config = merged
        except Exception as e:
            logger.warning("Giveaway config load failed: %s", e)
            self._config = dict(DEFAULT_CONFIG)

    def _normalize_config_in_place(self, cfg: Dict[str, Any]) -> None:
        cfg["keyword"] = str(cfg.get("keyword", "") or "").strip()
        cfg["no_duplicate_entries"] = bool(cfg.get("no_duplicate_entries", True))
        cfg["unique_winners_per_draw"] = bool(cfg.get("unique_winners_per_draw", True))
        try:
            n = int(cfg.get("num_winners", 1))
        except (TypeError, ValueError):
            n = 1
        cfg["num_winners"] = max(1, min(100, n))
        cfg["exclude_mods"] = bool(cfg.get("exclude_mods", False))
        cfg["exclude_vips"] = bool(cfg.get("exclude_vips", False))
        blocked = cfg.get("blocked_usernames", [])
        if isinstance(blocked, str):
            blocked = [x.strip() for x in re.split(r"[\n,]+", blocked) if x.strip()]
        elif not isinstance(blocked, list):
            blocked = []
        cfg["blocked_usernames"] = [str(x).strip().lower() for x in blocked if str(x).strip()]
        cfg["winning_message_template"] = str(
            cfg.get("winning_message_template") or DEFAULT_CONFIG["winning_message_template"]
        )

    def _save_config(self) -> None:
        try:
            from .database_manager import set_data

            set_data(DATA_PATH, dict(self._config))
        except Exception as e:
            logger.error("Giveaway config save failed: %s", e)

    def get_config(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._config)

    def set_config_field(self, key: str, value: Any) -> None:
        with self._lock:
            if key not in DEFAULT_CONFIG:
                return
            self._config[key] = value
            self._normalize_config_in_place(self._config)
            if key == "keyword" and not self._config["keyword"]:
                self._accepting_entries = False
            self._save_config()

    def update_config(self, updates: Dict[str, Any]) -> None:
        with self._lock:
            for k, v in updates.items():
                if k in DEFAULT_CONFIG:
                    self._config[k] = v
            self._normalize_config_in_place(self._config)
            if not self._config["keyword"]:
                self._accepting_entries = False
            self._save_config()

    def is_giveaway_active(self) -> bool:
        with self._lock:
            return bool(self._accepting_entries and self._config.get("keyword", "").strip())

    def get_pool_size(self) -> int:
        with self._lock:
            return len(self._pool)

    def get_last_error(self) -> str:
        with self._lock:
            return self._last_error

    def set_last_error(self, msg: str) -> None:
        with self._lock:
            self._last_error = msg or ""

    def start_giveaway(self) -> Tuple[bool, str]:
        with self._lock:
            if not self._config.get("keyword", "").strip():
                return False, "Set an entry keyword before starting."
            self._accepting_entries = True
            return True, ""

    def stop_accepting(self) -> None:
        with self._lock:
            self._accepting_entries = False

    def clear_giveaway(self) -> None:
        with self._lock:
            self._pool.clear()
            self._config["keyword"] = ""
            self._accepting_entries = False
            self._normalize_config_in_place(self._config)
            self._save_config()

    def _badges_str(self, msg_dict: Dict[str, Any]) -> str:
        b = msg_dict.get("badges")
        return b if isinstance(b, str) else ""

    def _is_blocked_user(self, username: str) -> bool:
        u = username.lower().strip()
        return u in set(self._config.get("blocked_usernames") or [])

    def try_register_entry(self, msg_dict: Dict[str, Any]) -> bool:
        with self._lock:
            if not self._accepting_entries:
                return False
            kw = self._config.get("keyword", "").strip()
            if not kw:
                return False

            message = (msg_dict.get("message") or "").strip()
            if message.lower() != kw.lower():
                return False

            username = msg_dict.get("username") or "Unknown"
            user_id = str(msg_dict.get("userid") or msg_dict.get("user_id") or username)

            if self._is_blocked_user(username):
                return False

            badges = self._badges_str(msg_dict)
            if self._config.get("exclude_mods") and "moderator/" in badges:
                return False
            if self._config.get("exclude_vips") and "vip/" in badges:
                return False

            if self._config.get("no_duplicate_entries"):
                for e in self._pool:
                    if e.user_id == user_id:
                        return False

            self._pool.append(GiveawayEntry(user_id=user_id, display_name=username))
            try:
                from .statistics_manager import get_statistics_manager

                get_statistics_manager().record_giveaway_entry(username)
            except Exception as e:
                logger.debug("giveaway entry stat: %s", e)
            return True

    def draw_winners(self) -> Tuple[bool, str, List[str]]:
        """
        Pick up to num_winners, announce, update stats. Does not clear pool.
        Returns (success, message_for_ui, winner_display_names).
        """
        with self._lock:
            self._last_error = ""
            if not self._pool:
                self._last_error = "Pool is empty."
                return False, self._last_error, []

            num = int(self._config.get("num_winners", 1))
            unique_per_draw = bool(self._config.get("unique_winners_per_draw"))
            template = self._config.get("winning_message_template") or ""

            # Work on a copy of ticket list for selection
            tickets = list(self._pool)
            winners: List[str] = []

            if unique_per_draw:
                remaining = tickets[:]
                while len(winners) < num and remaining:
                    pick = random.choice(remaining)
                    winners.append(pick.display_name)
                    uid = pick.user_id
                    remaining = [t for t in remaining if t.user_id != uid]
            else:
                k = min(num, len(tickets))
                picks = random.sample(tickets, k=k)
                winners = [p.display_name for p in picks]

            if not winners:
                self._last_error = "No winners selected."
                return False, self._last_error, []

            winners_text = ", ".join(winners)
            text = template.replace("{winners}", winners_text).replace("{winner}", winners_text)

        try:
            from .chatbot import send_chatbot_announcement

            ok = send_chatbot_announcement(text, "primary")
            if not ok:
                with self._lock:
                    self._last_error = "Failed to send announcement (check chatbot connection)."
                return False, self._last_error, []
        except Exception as e:
            logger.error("Giveaway announcement error: %s", e, exc_info=True)
            with self._lock:
                self._last_error = str(e)
            return False, self._last_error, []

        try:
            from .statistics_manager import get_statistics_manager

            sm = get_statistics_manager()
            for w in winners:
                sm.record_giveaway_winner(w)
            sm.record_giveaway_round_complete()
        except Exception as e:
            logger.debug("giveaway draw stats: %s", e)

        return True, f"Drew {len(winners)} winner(s).", winners

