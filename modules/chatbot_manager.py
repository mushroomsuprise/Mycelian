#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024-2026 Mycelian

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .chatbot_core import (
    ChatCommand,
    ChatEvent,
    CommandType,
    EventType,
    Greeting,
    Quote,
    normalize_permission,
    normalize_specific_time,
    permission_allows,
    render_template,
)
from .statistics_manager import get_statistics_manager
from .notification_engine import nav_chatbot, notify, notify_critical

logger = logging.getLogger(__name__)

DEFAULT_QUOTE_REPLY_FORMAT = '#{number}: "{text}" \u2014 {author}'


def _json_fingerprint(data: Any) -> Optional[str]:
    """Stable text form of a blob for change detection; None means always write."""
    try:
        return json.dumps(data, sort_keys=True, default=str)
    except Exception:
        return None


def _database_backend_token() -> tuple:
    """Identify where set_data writes, so a backend switch rewrites every blob."""
    try:
        from . import database_manager as db_module

        manager = db_module.database_manager
        config = manager.get_config()
        return (
            getattr(manager, "_database", None),
            getattr(config, "database_type", None),
            getattr(config, "streamer_name", None),
        )
    except Exception:
        return (object(),)


def _command_matches_name(command: ChatCommand, command_name: str) -> bool:
    """True if chat text matches the primary name or any alias (case-insensitive)."""
    needle = (command_name or "").lower()
    if (command.command_name or "").lower() == needle:
        return True
    for alias in getattr(command, "aliases", None) or []:
        if str(alias).lower().lstrip("!") == needle:
            return True
    return False


def _find_reset_target(
    commands: Dict[str, ChatCommand], reset_command: str, skip_id: str
) -> Optional[ChatCommand]:
    """Resolve the counter command named by a RESET command's reset_command field."""
    target_name = (reset_command or "").strip().lstrip("!").lower()
    if not target_name:
        return None
    for command in commands.values():
        if command.command_id == skip_id:
            continue
        if _command_matches_name(command, target_name):
            return command
    return None


_COMMAND_RUNTIME_FIELDS = (
    "counter_value",
    "usage_count",
    "last_used",
    "trigger_count",
    "last_triggered",
)


def _silent_result(command_name: str) -> tuple:
    """A matched command that posts nothing (blocked, or lookup failed)."""
    return ("", command_name, [], [], "chat")


class TestResult(dict):
    """``{ok, triggered, response, reason, lookup}`` from a test run.

    Also carries the old keys (``success``, ``message``, ``error``) and can be
    awaited, so the old editor's ``await manager.test_command(...)`` works.
    """

    __test__ = False  # not a pytest class

    def __init__(self, *, ok: bool, triggered: bool, response: str, reason: str, lookup=None):
        super().__init__(ok=ok, triggered=triggered, response=response, reason=reason, lookup=lookup)
        self["success"] = ok
        self["message"] = response or reason
        if not ok:
            self["error"] = reason

    def __await__(self):
        if False:  # pragma: no cover - makes this a generator
            yield None
        return self


class GreetingFlagManager:
    """Manages greeting flags in a local file for simple boolean-based greeting system"""

    def __init__(self, file_path: str = None):
        if file_path is None:
            # Default path in the data directory
            data_dir = Path(__file__).parent.parent / "data"
            data_dir.mkdir(exist_ok=True)
            file_path = data_dir / "greeting_flags.json"

        self.file_path = Path(file_path)
        self.greeted_flags: Dict[str, bool] = {}  # user_id -> has_been_greeted
        self.last_reset_time: float = 0  # Global reset timestamp
        self._lock = threading.RLock()
        self._file_lock = threading.Lock()
        self._dirty = False
        self._last_save = 0
        self._save_interval = 60  # Save every 60 seconds if dirty

        # Load existing data
        self._load_flags()

        # Start background save thread
        self._save_thread = threading.Thread(
            target=self._background_save_loop, daemon=True
        )
        self._save_thread.start()

    def _load_flags(self):
        """Load greeting flags from file"""
        try:
            if self.file_path.exists():
                import json

                with open(self.file_path, "r") as f:
                    data = json.load(f)

                    # Handle migration from old timestamp format
                    if isinstance(data, dict):
                        if "greeted_flags" in data:
                            # New format. A False flag behaves like a missing one,
                            # so only greeted users are kept (RT-12).
                            self.greeted_flags = {
                                uid: True
                                for uid, flag in data["greeted_flags"].items()
                                if flag
                            }
                            self.last_reset_time = data.get("last_reset_time", 0)
                        else:
                            # Old timestamp format - migrate to boolean flags
                            logger.info(
                                "Migrating from timestamp format to boolean flags"
                            )
                            self.greeted_flags = {
                                uid: True for uid in data.keys()
                            }  # All were greeted
                            self.last_reset_time = time.time()  # Reset now
                            self._dirty = True  # Force save new format

                    logger.debug(
                        f"Loaded {len(self.greeted_flags)} greeting flags from {self.file_path}"
                    )
            else:
                # New file - initialize with reset
                self.last_reset_time = time.time()
                self._dirty = True

        except Exception as e:
            logger.warning(f"Failed to load greeting flags from {self.file_path}: {e}")
            self.greeted_flags = {}
            self.last_reset_time = time.time()
            self._dirty = True

    def _save_flags(self):
        """Save greeting flags to file"""
        try:
            # Snapshot under the lock: chat threads add flags while we write (TH-11).
            with self._lock:
                data = {
                    "greeted_flags": dict(self.greeted_flags),
                    "last_reset_time": self.last_reset_time,
                }
                self._dirty = False

            with self._file_lock:
                tmp_path = self.file_path.with_name(self.file_path.name + ".tmp")
                with open(tmp_path, "w") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp_path, self.file_path)
            self._last_save = time.time()
            logger.debug(
                f"Saved {len(data['greeted_flags'])} greeting flags to {self.file_path}"
            )
        except Exception as e:
            with self._lock:
                self._dirty = True
            logger.error(f"Failed to save greeting flags to {self.file_path}: {e}")
            notify(
                "Viewers who were already greeted may be greeted again after a restart.",
                type="warning",
                source="chatbot",
                title="Couldn't save who has been greeted",
                hint="Check that the Mycelian data folder isn't read-only and the disk isn't full. Mycelian keeps retrying.",
                details=f"{self.file_path}: {e}",
                actions=nav_chatbot("Greetings"),
                dedupe_key="chatbot:greeting_flags_save",
                dedupe_cooldown_sec=3600,
            )

    def _background_save_loop(self):
        """Background thread to periodically save flags"""
        while True:
            try:
                current_time = time.time()
                if (
                    self._dirty
                    and (current_time - self._last_save) > self._save_interval
                ):
                    self._save_flags()
                time.sleep(10)  # Check every 10 seconds
            except Exception as e:
                logger.error(f"Error in greeting flag save loop: {e}")
                time.sleep(30)  # Wait longer on error

    def should_greet_user(self, user_id: str) -> bool:
        """Check if a user should be greeted (returns True if not yet greeted)"""
        with self._lock:
            return not self.greeted_flags.get(user_id, False)

    def mark_user_greeted(self, user_id: str):
        """Mark a user as having been greeted"""
        with self._lock:
            self.greeted_flags[user_id] = True
            self._dirty = True

    def reset_all_flags(self):
        """Reset all greeting flags to False and update reset timestamp"""
        with self._lock:
            old_count = len([uid for uid, flag in self.greeted_flags.items() if flag])
            # Every flag is now False, which is the same as no entry (RT-12).
            self.greeted_flags = {}
            self.last_reset_time = time.time()
            self._dirty = True
            logger.info(f"Reset greeting flags for {old_count} users")

    def should_reset_flags(
        self, reset_interval_hours: int, current_time: float = None
    ) -> bool:
        """Check if greeting flags should be reset based on time interval"""
        if current_time is None:
            current_time = time.time()

        with self._lock:
            if reset_interval_hours <= 0:
                return False  # No reset interval set

            hours_since_reset = (current_time - self.last_reset_time) / 3600
            return hours_since_reset >= reset_interval_hours

    def perform_reset_if_needed(
        self, reset_interval_hours: int, current_time: float = None
    ):
        """Check and perform reset if needed"""
        if self.should_reset_flags(reset_interval_hours, current_time):
            self.reset_all_flags()
            return True
        return False

    def force_save(self):
        """Force an immediate save of flags"""
        with self._lock:
            if self._dirty:
                self._save_flags()

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about the flags file"""
        with self._lock:
            greeted_count = sum(1 for flag in self.greeted_flags.values() if flag)
            return {
                "total_users": len(self.greeted_flags),
                "greeted_users": greeted_count,
                "ungreeted_users": len(self.greeted_flags) - greeted_count,
                "last_reset_time": self.last_reset_time,
                "file_path": str(self.file_path),
                "file_exists": self.file_path.exists(),
                "dirty": self._dirty,
                "last_save": self._last_save,
            }


class ChatbotManager:
    """Manages chat commands and automatic event responses"""

    def __init__(self):
        self.commands: Dict[str, ChatCommand] = {}
        self.events: Dict[str, ChatEvent] = {}
        self.quotes: Dict[str, Quote] = {}
        self.greetings: Dict[str, Greeting] = {}  # greeting_id -> Greeting
        self.greetings_by_user_id: Dict[
            str, Greeting
        ] = {}  # user_id -> Greeting for fast lookup
        self.quotes_enabled: bool = True
        self.greetings_enabled: bool = True  # Global greetings toggle (legacy)
        self.default_greeting_enabled: bool = True  # Toggle for default greeting
        self.custom_greeting_enabled: bool = True  # Toggle for custom greetings
        self.default_greeting_text: str = (
            "@{username} welcome to the stream!"  # Default greeting
        )
        self.greeting_cooldown_hours: int = 24  # Hours between greetings (legacy)
        self.greeting_reset_interval_hours: int = 24  # Hours between greeting resets
        self.quote_cooldown_seconds: int = 30  # Global cooldown for quote commands
        self.add_quote_permission: str = "moderator"  # who may use !add_quote
        self.quote_reply_format: str = DEFAULT_QUOTE_REPLY_FORMAT
        self.quote_last_used: float = 0.0  # Timestamp of last quote usage

        # Initialize flag manager for efficient default greeting tracking
        self.flag_manager = GreetingFlagManager()

        self._lock = threading.RLock()
        self._save_lock = threading.Lock()
        self._save_dirty = False
        self._save_thread: Optional[threading.Thread] = None
        # Last JSON written per BotData path; unchanged blobs are skipped (DBX-04).
        self._save_write_lock = threading.Lock()
        self._saved_json: Dict[str, str] = {}
        self._saved_backend: Any = None
        # BotData paths whose load failed (a failed read looks like empty data).
        # They are re-read before they are written so defaults never replace
        # good stored data. Value: baseline for Settings, else None.
        self._unreadable_paths: Dict[str, Any] = {}
        self._blocked_save_pending = False

        # Command cooldown tracking
        self.command_cooldowns: Dict[str, float] = {}

        # Timer threads of INTERVAL / SPECIFIC_TIME events, by event id.
        self.repeating_tasks: Dict[str, threading.Thread] = {}
        # Stop flag per running timer thread, so a stopped thread never fires
        # again after a quick re-enable starts its replacement (TH-12).
        self._repeating_stops: Dict[str, threading.Event] = {}
        self._scheduler_lock = threading.Lock()
        # Kept for compatibility (status display); timers no longer wait.
        self._deferred_interval_events: List[ChatEvent] = []

        # Load saved data
        self._load_data()

    def _migrate_legacy_data(self):
        """Migrate data from legacy JSON files to database manager"""
        try:
            import json
            import os

            from .database_manager import set_data

            data_dir = os.path.join(os.path.dirname(__file__), "..", "data")

            # Check if migration is needed (if database is empty but JSON files exist)
            existing_db_data = self._read_checked("BotData/Commands")
            if existing_db_data is not None:
                logger.debug("Database already has data, skipping migration")
                # _load_data reuses this read instead of fetching it again (DBX-12).
                return existing_db_data

            # Migrate commands
            commands_file = os.path.join(data_dir, "chatbot_commands.json")
            if os.path.exists(commands_file):
                with open(commands_file, "r") as f:
                    commands_data = json.load(f)
                    set_data("BotData/Commands", commands_data)
                    logger.info(
                        f"Migrated {len(commands_data)} commands from JSON to database"
                    )

            # Migrate events
            events_file = os.path.join(data_dir, "chatbot_events.json")
            if os.path.exists(events_file):
                with open(events_file, "r") as f:
                    events_data = json.load(f)
                    set_data("BotData/Events", events_data)
                    logger.info(
                        f"Migrated {len(events_data)} events from JSON to database"
                    )

            # Migrate quotes
            quotes_file = os.path.join(data_dir, "chatbot_quotes.json")
            if os.path.exists(quotes_file):
                with open(quotes_file, "r") as f:
                    quotes_data = json.load(f)
                    set_data("BotData/Quotes", quotes_data)
                    logger.info(
                        f"Migrated {len(quotes_data)} quotes from JSON to database"
                    )

            # Migrate settings
            settings_file = os.path.join(data_dir, "chatbot_settings.json")
            if os.path.exists(settings_file):
                with open(settings_file, "r") as f:
                    settings_data = json.load(f)
                    set_data("BotData/Settings", settings_data)
                    logger.info("Migrated chatbot settings from JSON to database")

        except Exception as e:
            logger.error(f"Error during migration: {e}")
            notify_critical(
                "Some commands, events, quotes or settings from the old chatbot files may be missing.",
                source="chatbot",
                title="Couldn't import the old chatbot files",
                hint="Check your commands in the Chatbot tab. The old files in the data folder were left as they were.",
                details=f"{type(e).__name__}: {e}",
                dedupe_key="chatbot:migrate_failed",
                actions=nav_chatbot("Commands"),
            )

    def _load_data(self):
        """Load commands and events from database manager"""
        try:
            # First, check if we need to migrate legacy data
            preloaded_commands = self._migrate_legacy_data()

            # Load commands
            if preloaded_commands is not None:
                commands_data = preloaded_commands
            else:
                commands_data = self._read_checked("BotData/Commands")
            skipped: List[str] = []
            if commands_data and isinstance(commands_data, list):
                for cmd_data in commands_data:
                    try:
                        command = ChatCommand.from_dict(cmd_data)
                        self.commands[command.command_id] = command
                    except Exception as e:
                        logger.error(
                            f"Error loading command {cmd_data.get('command_id', 'unknown')}: {e}"
                        )
                        skipped.append(f"command {cmd_data.get('command_id', '?') if isinstance(cmd_data, dict) else '?'}: {e}")

            # Load events
            events_data = self._read_checked("BotData/Events")
            if events_data and isinstance(events_data, list):
                for event_data in events_data:
                    try:
                        event = ChatEvent.from_dict(event_data)
                        self.events[event.event_id] = event
                    except Exception as e:
                        logger.error(
                            f"Error loading event {event_data.get('event_id', 'unknown')}: {e}"
                        )
                        skipped.append(f"event {event_data.get('event_id', '?') if isinstance(event_data, dict) else '?'}: {e}")

            migrated = self._apply_load_migrations(commands_data, events_data)

            # Load quotes
            quotes_data = self._read_checked("BotData/Quotes")
            if quotes_data and isinstance(quotes_data, list):
                for quote_data in quotes_data:
                    try:
                        quote = Quote.from_dict(quote_data)
                        self.quotes[quote.quote_id] = quote
                    except Exception as e:
                        logger.error(
                            f"Error loading quote {quote_data.get('quote_id', 'unknown')}: {e}"
                        )
                        skipped.append(f"quote {quote_data.get('quote_id', '?') if isinstance(quote_data, dict) else '?'}: {e}")

            # Load settings with defensive type validation
            settings_data = self._read_checked("BotData/Settings")
            if settings_data and isinstance(settings_data, dict):
                self._apply_settings(settings_data)
            if "BotData/Settings" in self._unreadable_paths:
                # Defaults are the baseline; later edits are applied over the
                # stored copy once it can be read.
                self._unreadable_paths["BotData/Settings"] = self._settings_payload()

            # Load greetings
            greetings_data = self._read_checked("BotData/Greetings")
            if greetings_data and isinstance(greetings_data, list):
                for greeting_data in greetings_data:
                    try:
                        greeting = Greeting.from_dict(greeting_data)
                        self.greetings[greeting.greeting_id] = greeting
                        self.greetings_by_user_id[greeting.user_id] = greeting
                    except Exception as e:
                        logger.error(
                            f"Error loading greeting {greeting_data.get('greeting_id', 'unknown')}: {e}"
                        )
                        skipped.append(f"greeting {greeting_data.get('greeting_id', '?') if isinstance(greeting_data, dict) else '?'}: {e}")

            if skipped:
                count = len(skipped)
                notify(
                    f"{count} chatbot item{'s' if count != 1 else ''} couldn't be read and "
                    f"{'were' if count != 1 else 'was'} skipped. They'll be dropped the next time chatbot changes are saved.",
                    type="warning",
                    source="chatbot",
                    title="Some chatbot items couldn't be loaded",
                    hint="Copy the details to see which items failed. Recreate them in the Chatbot tab if they're gone.",
                    details="\n".join(skipped),
                    actions=nav_chatbot("Commands"),
                    dedupe_key="chatbot:items_skipped",
                    dedupe_cooldown_sec=3600,
                )

            logger.info(
                f"Loaded {len(self.commands)} commands, {len(self.events)} events, {len(self.quotes)} quotes, and {len(self.greetings)} greetings"
            )

            # Timers run on their own threads from now on; posts wait one full
            # interval and are skipped while chat is unavailable.
            for event in list(self.events.values()):
                try:
                    self._schedule_event(event)
                except Exception as e:
                    logger.error(f"Error starting timer for event {event.name}: {e}")

            if migrated:
                logger.info("Chatbot data migrated to the current format; saving")
                self._save_data()

        except Exception as e:
            logger.error(f"Error loading chatbot data: {e}")
            notify_critical(
                "Commands, events, quotes and greetings may be missing until this is fixed.",
                source="chatbot",
                title="Couldn't load the chatbot from the database",
                hint="Check Settings → Database, then restart Mycelian.",
                details=f"{type(e).__name__}: {e}",
                dedupe_key="chatbot:load_failed",
                actions=nav_chatbot("Commands"),
            )

    def _apply_load_migrations(self, commands_data: Any, events_data: Any) -> bool:
        """One-time repairs after load. Returns True when anything must be saved.

        * repeating commands become INTERVAL events (only when both the
          commands and the events could be read, so nothing is lost);
        * counters with ``persistent_counter`` False restart at 0;
        * everything the constructors normalized (aliases, ``!`` prefixes,
          legacy API -> lookup, mod_only -> permission, schema version) is
          detected by comparing the normalized form with what was stored.
        """
        both_readable = (
            "BotData/Commands" not in self._unreadable_paths
            and "BotData/Events" not in self._unreadable_paths
        )
        changed = False
        for command in list(self.commands.values()):
            if command.command_type == CommandType.COUNTER and not command.persistent_counter:
                if command.counter_value:
                    command.counter_value = 0
                    changed = True
            if command.repeating_enabled and both_readable:
                self._convert_repeating_command(command)
                changed = True
        if "BotData/Commands" not in self._unreadable_paths and isinstance(commands_data, list):
            stored = _json_fingerprint(commands_data)
            current = _json_fingerprint([c.to_dict() for c in self.commands.values()])
            changed = changed or stored != current
        if "BotData/Events" not in self._unreadable_paths and isinstance(events_data, list):
            stored = _json_fingerprint(events_data)
            current = _json_fingerprint([e.to_dict() for e in self.events.values()])
            changed = changed or stored != current
        return changed

    def _convert_repeating_command(self, command: ChatCommand) -> ChatEvent:
        """Move a command's repeat settings into a new INTERVAL event."""
        try:
            interval = float(command.repeat_interval or 0)
        except (TypeError, ValueError):
            interval = 0
        event = ChatEvent(
            event_id=str(uuid.uuid4()),
            name=f"{command.name or ('!' + command.command_name)} (timer)",
            description=f"Moved from the repeating setting of !{command.command_name}",
            event_type=EventType.INTERVAL,
            interval=int(interval) if interval > 0 else 60,
            response_text=command.response_text,
            enabled=command.enabled,
            reply_targets=list(command.reply_targets),
            discord_channels=list(command.discord_channels or []),
            output_mode=command.output_mode,
            lookup=dict(command.lookup) if command.lookup else None,
            lookup_fallback=command.lookup_fallback,
        )
        self.events[event.event_id] = event
        command.repeating_enabled = False
        logger.info(
            "Moved repeating command !%s into interval event %s",
            command.command_name,
            event.name,
        )
        return event

    def _read_checked(self, path: str):
        from .database_manager import get_data_checked

        data, ok = get_data_checked(path)
        if not ok:
            self._unreadable_paths[path] = None
            logger.warning(
                "%s could not be read; it will be re-read before it is saved", path
            )
        return data

    def _recover_unreadable_paths(self) -> set:
        """Re-read paths whose load failed. Returns paths that must not be written."""
        from .database_manager import get_data_checked

        blocked = set()
        for path in list(self._unreadable_paths):
            data, ok = get_data_checked(path)
            if not ok:
                logger.warning(
                    "Not saving %s: it could not be read from the database", path
                )
                blocked.add(path)
                continue
            with self._lock:
                if path not in self._unreadable_paths:
                    continue
                baseline = self._unreadable_paths.pop(path)
                self._merge_stored_path(path, data, baseline)
            logger.info("Re-read %s after a failed load; local changes kept", path)
        return blocked

    def _merge_stored_path(self, path: str, data: Any, baseline: Any) -> None:
        """Use the stored copy as the base; keep what changed in memory since.

        Call with ``_lock`` held. Nothing was loaded for this path, so every
        in-memory item is a later change and wins over a stored one with its id.
        """
        if path == "BotData/Settings":
            current = self._settings_payload()
            changed = {
                key: value
                for key, value in current.items()
                if not isinstance(baseline, dict) or baseline.get(key) != value
            }
            if data and isinstance(data, dict):
                self._apply_settings(data)
            for key, value in changed.items():
                setattr(self, key, value)
            return

        specs = {
            "BotData/Commands": (self.commands, ChatCommand, "command_id"),
            "BotData/Events": (self.events, ChatEvent, "event_id"),
            "BotData/Quotes": (self.quotes, Quote, "quote_id"),
            "BotData/Greetings": (self.greetings, Greeting, "greeting_id"),
        }
        target, cls, id_attr = specs[path]
        merged: Dict[str, Any] = {}
        added: List[Any] = []
        for item in data if isinstance(data, list) else []:
            try:
                obj = cls.from_dict(item)
            except Exception as e:
                logger.error(f"Error loading {path} item: {e}")
                continue
            item_id = getattr(obj, id_attr)
            if item_id in target:
                merged[item_id] = target[item_id]
            else:
                merged[item_id] = obj
                added.append(obj)
        stored_numbers = {
            getattr(obj, "quote_number", None) for obj in merged.values()
        }
        for item_id, obj in target.items():
            if item_id in merged:
                continue
            if path == "BotData/Quotes" and obj.quote_number in stored_numbers:
                # Numbered while the stored quotes were unknown: renumber after them.
                obj.quote_number = max(
                    n for n in stored_numbers if isinstance(n, int)
                ) + 1
                stored_numbers.add(obj.quote_number)
            merged[item_id] = obj
        target.clear()
        target.update(merged)
        for obj in added:
            if path == "BotData/Greetings":
                self.greetings_by_user_id.setdefault(obj.user_id, obj)
            elif path == "BotData/Events" and obj.enabled:
                # Same start rules as _load_data.
                self._schedule_event(obj)

    def _apply_settings(self, settings_data: Dict[str, Any]) -> None:
        """Apply a stored BotData/Settings dict to this manager."""
        # Ensure boolean values are actually booleans (guard against corrupted data)
        quotes_val = settings_data.get("quotes_enabled", True)
        self.quotes_enabled = bool(quotes_val) if not isinstance(quotes_val, bool) else quotes_val
        
        greetings_val = settings_data.get("greetings_enabled", True)
        self.greetings_enabled = bool(greetings_val) if not isinstance(greetings_val, bool) else greetings_val
        
        # New separate toggle settings with backwards compatibility
        default_greet_val = settings_data.get("default_greeting_enabled", self.greetings_enabled)
        self.default_greeting_enabled = bool(default_greet_val) if not isinstance(default_greet_val, bool) else default_greet_val
        
        custom_greet_val = settings_data.get("custom_greeting_enabled", self.greetings_enabled)
        self.custom_greeting_enabled = bool(custom_greet_val) if not isinstance(custom_greet_val, bool) else custom_greet_val
        
        greeting_text_val = settings_data.get("default_greeting_text", "@{username} welcome to the stream!")
        self.default_greeting_text = str(greeting_text_val) if greeting_text_val else "@{username} welcome to the stream!"
        
        cooldown_val = settings_data.get("greeting_cooldown_hours", 24)
        self.greeting_cooldown_hours = int(cooldown_val) if not isinstance(cooldown_val, int) else cooldown_val
        
        reset_val = settings_data.get("greeting_reset_interval_hours", 24)
        self.greeting_reset_interval_hours = int(reset_val) if not isinstance(reset_val, int) else reset_val
        
        quote_cd_val = settings_data.get("quote_cooldown_seconds", 30)
        self.quote_cooldown_seconds = int(quote_cd_val) if not isinstance(quote_cd_val, int) else quote_cd_val
        self.add_quote_permission = normalize_permission(settings_data.get("add_quote_permission", "moderator"))
        fmt = settings_data.get("quote_reply_format")
        self.quote_reply_format = str(fmt) if isinstance(fmt, str) and fmt.strip() else DEFAULT_QUOTE_REPLY_FORMAT

    def _save_data(self) -> None:
        """Queue a save of commands and events. The latest snapshot is written."""
        with self._save_lock:
            self._save_dirty = True
            thread = self._save_thread
            if thread is not None and thread.is_alive():
                return
            self._save_thread = threading.Thread(
                target=self._save_worker,
                name="ChatbotSave",
                daemon=True,
            )
            self._save_thread.start()

    def _save_worker(self) -> None:
        while True:
            with self._save_lock:
                if not self._save_dirty:
                    self._save_thread = None
                    return
                self._save_dirty = False
            try:
                self._save_data_now()
            except Exception:
                logger.error("Chatbot save failed", exc_info=True)

    def flush_pending_saves(self, timeout: float = 4.0) -> None:
        """Finish any queued save now (shutdown). Also writes greeting flags."""
        thread = self._save_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
        with self._save_lock:
            dirty = self._save_dirty or self._blocked_save_pending
            self._save_dirty = False
        if dirty:
            self._save_data_now()
        if hasattr(self, "flag_manager"):
            self.flag_manager.force_save()

    def _settings_payload(self) -> Dict[str, Any]:
        # Save settings - ensure all values are JSON-serializable primitives
        # This guards against NiceGUI event objects or UI components being stored
        return {
            "quotes_enabled": bool(self.quotes_enabled) if not isinstance(self.quotes_enabled, bool) else self.quotes_enabled,
            "greetings_enabled": bool(self.greetings_enabled) if not isinstance(self.greetings_enabled, bool) else self.greetings_enabled,
            "default_greeting_enabled": bool(self.default_greeting_enabled) if not isinstance(self.default_greeting_enabled, bool) else self.default_greeting_enabled,
            "custom_greeting_enabled": bool(self.custom_greeting_enabled) if not isinstance(self.custom_greeting_enabled, bool) else self.custom_greeting_enabled,
            "default_greeting_text": str(self.default_greeting_text) if self.default_greeting_text else "@{username} welcome to the stream!",
            "greeting_cooldown_hours": int(self.greeting_cooldown_hours) if not isinstance(self.greeting_cooldown_hours, int) else self.greeting_cooldown_hours,
            "greeting_reset_interval_hours": int(self.greeting_reset_interval_hours) if not isinstance(self.greeting_reset_interval_hours, int) else self.greeting_reset_interval_hours,
            "quote_cooldown_seconds": int(self.quote_cooldown_seconds) if not isinstance(self.quote_cooldown_seconds, int) else self.quote_cooldown_seconds,
            "add_quote_permission": self.add_quote_permission,
            "quote_reply_format": self.quote_reply_format,
        }

    def _build_save_payloads(self) -> List[tuple]:
        """Serialize everything under the lock; UI and chat threads mutate it (TH-11)."""
        with self._lock:
            # Save commands
            commands_data = [cmd.to_dict() for cmd in self.commands.values()]

            # Save events
            events_data = [evt.to_dict() for evt in self.events.values()]

            # Save quotes
            quotes_data = [quote.to_dict() for quote in self.quotes.values()]

            # Save greetings
            greetings_data = [
                greeting.to_dict() for greeting in self.greetings.values()
            ]

            settings_data = self._settings_payload()
        return [
            ("BotData/Commands", commands_data),
            ("BotData/Events", events_data),
            ("BotData/Quotes", quotes_data),
            ("BotData/Greetings", greetings_data),
            ("BotData/Settings", settings_data),
        ]

    def _save_data_now(self):
        """Save commands and events to database manager"""
        try:
            # Import database manager here to avoid circular imports
            from .database_manager import last_write_problem, quiet_notifications, set_data

            blocked = self._recover_unreadable_paths() if self._unreadable_paths else set()
            self._blocked_save_pending = bool(blocked)
            payloads = self._build_save_payloads()
            failed: List[str] = []
            with self._save_write_lock:
                # A different backend (or streamer) has none of our blobs yet.
                backend = _database_backend_token()
                if backend != self._saved_backend:
                    self._saved_json.clear()
                    self._saved_backend = backend
                for path, data in payloads:
                    if path in blocked:
                        continue
                    fingerprint = _json_fingerprint(data)
                    if fingerprint is not None and self._saved_json.get(path) == fingerprint:
                        continue
                    self._saved_json.pop(path, None)
                    # The failure is reported below with chatbot context, not by the database layer.
                    with quiet_notifications():
                        ok = set_data(path, data)
                        problem = last_write_problem()
                    if ok:
                        if fingerprint is not None:
                            self._saved_json[path] = fingerprint
                    else:
                        reason = ": ".join(str(p) for p in problem if p) if problem else ""
                        failed.append(f"{path} ({reason})" if reason else path)

            if failed:
                logger.error("Chatbot data not saved for: %s", ", ".join(failed))
                self._notify_save_failed(f"Database write returned no success for: {', '.join(failed)}")
            elif blocked:
                notify(
                    "Your latest chatbot changes are kept in memory and saved once the database can be read.",
                    type="warning",
                    source="chatbot",
                    title="Chatbot changes not saved yet",
                    hint="Check Settings → Database. Don't close Mycelian until this clears, or the changes may be lost.",
                    details=f"Could not read before saving: {', '.join(sorted(blocked))}",
                    actions=nav_chatbot("Commands"),
                    dedupe_key="chatbot:save_blocked",
                    dedupe_cooldown_sec=600,
                )
            else:
                logger.debug("Saved chatbot data")

        except Exception as e:
            logger.error(f"Error saving chatbot data: {e}")
            self._notify_save_failed(f"{type(e).__name__}: {e}")

    @staticmethod
    def _notify_save_failed(details: str) -> None:
        notify_critical(
            "Your latest chatbot changes may be lost when Mycelian closes.",
            source="chatbot",
            title="Couldn't save chatbot changes",
            hint="Check Settings → Database, then make the change again.",
            details=details,
            dedupe_key="chatbot:save_failed",
            actions=nav_chatbot("Commands"),
        )

    def get_manager(self):
        """Get the manager instance (for compatibility)"""
        return self

    # Command Management
    #
    # UI contract: build a full ChatCommand / ChatEvent from the editor and
    # call add_* / update_*. update_* keeps the runtime stats of the stored item
    # (counter_value, usage_count, last_used, trigger_count, last_triggered)
    # unless keep_runtime=False, and keeps stored conditions when the new item
    # has none (there is no conditions editor yet) unless keep_conditions=False.
    # Every other field (permission, lookup, lookup_fallback,
    # argument_mappings, outputs ...) is taken from the object passed in.

    def validate_command(self, command: ChatCommand) -> Optional[str]:
        """Error text for a command the editor wants to save, or None."""
        if not command.command_name:
            return "Give the command a name (the word after !)."
        names = set(command.all_names())
        if self.quotes_enabled and names & {"quote", "add_quote"}:
            return "!quote and !add_quote are built in."
        with self._lock:
            for other in self.commands.values():
                if other.command_id == command.command_id:
                    continue
                clash = names & set(other.all_names())
                if clash:
                    return f"!{sorted(clash)[0]} is already used by another command."
        return None

    def add_command(self, command: ChatCommand) -> bool:
        """Add a new command"""
        with self._lock:
            if not command.command_id:
                command.command_id = str(uuid.uuid4())
            if command.repeating_enabled:
                event = self._convert_repeating_command(command)
                self._schedule_event(event)
            self.commands[command.command_id] = command
            self._save_data()
            logger.info(f"Added command: {command.command_name}")
            return True

    def update_command(
        self,
        command_id: str,
        command: ChatCommand,
        *,
        keep_runtime: bool = True,
        keep_conditions: bool = True,
    ) -> bool:
        """Replace a stored command (see the UI contract above)."""
        with self._lock:
            old_command = self.commands.get(command_id)
            if old_command is None:
                return False
            command.command_id = command_id
            if keep_runtime:
                for attr in _COMMAND_RUNTIME_FIELDS:
                    setattr(command, attr, getattr(old_command, attr))
            if keep_conditions and not command.conditions and old_command.conditions:
                command.conditions = list(old_command.conditions)
            if command.repeating_enabled:
                event = self._convert_repeating_command(command)
                self._schedule_event(event)
            self.commands[command_id] = command
            self._save_data()
            logger.info(f"Updated command: {command.command_name}")
            return True

    def remove_command(self, command_id: str) -> bool:
        """Remove a command"""
        with self._lock:
            command = self.commands.pop(command_id, None)
            if command is None:
                return False
            self._save_data()
            logger.info(f"Removed command: {command.command_name}")
            return True

    def get_command(self, command_id: str) -> Optional[ChatCommand]:
        """Get a command by ID"""
        return self.commands.get(command_id)

    def get_all_commands(self) -> Dict[str, ChatCommand]:
        """Get all commands"""
        return self.commands.copy()

    # Event Management
    @staticmethod
    def validate_event(event: ChatEvent) -> Optional[str]:
        """Error text for an event that cannot be saved, or None.

        Also normalizes ``specific_time`` to ``HH:MM``.
        """
        if event.event_type == EventType.INTERVAL and not event.interval > 0:
            return "Timer events need an interval."
        if event.event_type == EventType.SPECIFIC_TIME:
            normalized = normalize_specific_time(event.specific_time)
            if normalized is None:
                return "Pick a time as HH:MM (24-hour), e.g. 18:30."
            event.specific_time = normalized
        return None

    def add_event(self, event: ChatEvent) -> bool:
        """Add a new event (False when validate_event fails)."""
        error = self.validate_event(event)
        if error:
            logger.error("Cannot add event '%s': %s", event.name, error)
            return False
        with self._lock:
            if not event.event_id:
                event.event_id = str(uuid.uuid4())
            self.events[event.event_id] = event
            self._save_data()
            self._schedule_event(event)
            logger.info(f"Added event: {event.name}")
            return True

    def update_event(
        self,
        event_id: str,
        event: ChatEvent,
        *,
        keep_runtime: bool = True,
        keep_conditions: bool = True,
    ) -> bool:
        """Replace a stored event (False when missing or validate_event fails)."""
        error = self.validate_event(event)
        if error:
            logger.error("Cannot update event '%s': %s", event.name, error)
            return False
        with self._lock:
            old_event = self.events.get(event_id)
            if old_event is None:
                return False
            event.event_id = event_id
            if keep_runtime:
                event.trigger_count = old_event.trigger_count
                event.last_triggered = old_event.last_triggered
            if keep_conditions and not event.conditions and old_event.conditions:
                event.conditions = list(old_event.conditions)
            self.events[event_id] = event
            self._save_data()
            # Stops the old timer; starts a new one only for INTERVAL /
            # SPECIFIC_TIME events that are enabled.
            self._schedule_event(event)
            logger.info(f"Updated event: {event.name}")
            return True

    def remove_event(self, event_id: str) -> bool:
        """Remove an event"""
        with self._lock:
            event = self.events.pop(event_id, None)
            if event is None:
                return False
            self._unschedule_event(event_id)
            self._save_data()
            logger.info(f"Removed event: {event.name}")
            return True

    def get_event(self, event_id: str) -> Optional[ChatEvent]:
        """Get an event by ID"""
        return self.events.get(event_id)

    def get_all_events(self) -> Dict[str, ChatEvent]:
        """Get all events"""
        return self.events.copy()

    def message_may_call_api(self, message_data: Dict[str, Any]) -> bool:
        """True when the chat text names an enabled command that runs a lookup.

        Lock-free on purpose: EventSub calls this while another thread may hold
        ``_lock``. A wrong guess only picks another thread.
        """
        message = str((message_data or {}).get("message") or "").strip()
        if not message.startswith("!"):
            return False
        parts = message[1:].split()
        if not parts:
            return False
        command_name = parts[0].lower()
        try:
            commands = list(self.commands.values())
        except RuntimeError:
            return True
        for command in commands:
            if (
                command.enabled
                and getattr(command, "lookup", None)
                and _command_matches_name(command, command_name)
            ):
                return True
        return False

    # Command Processing
    def process_chat_message(
        self, message_data: Dict[str, Any]
    ) -> Optional[tuple]:
        """Process a chat message for commands.

        Returns ``(response, command_name, reply_targets, discord_channels,
        output_mode)`` when a command matched. ``response`` is "" when the
        command was blocked (permission / cooldown / usage limit) or its lookup
        failed without fallback: callers must post nothing (the bot stays
        silent). Built-in quote commands return ``(response, name)``.
        None when no command matched.

        A lookup runs after the command lock is released; the use (cooldown,
        usage count, counter) is consumed before it, so a failed lookup still
        counts as a use.
        """
        try:
            message = str(message_data.get("message") or "").strip()
            username = message_data.get("username", "Unknown")
            if not message.startswith("!"):
                return None

            parts = message[1:].split()
            if not parts:
                return None

            command_name = parts[0].lower()
            arguments = parts[1:]
            logger.debug("[CHATBOT] Command %r from %s args=%s", command_name, username, arguments)

            # Command variables (kept on message_data for chat-message events).
            message_data["command_message"] = " ".join(arguments)
            message_data["command_args"] = arguments
            message_data["command_words"] = arguments
            if not message_data.get("user_id") and message_data.get("userid"):
                message_data["user_id"] = str(message_data["userid"])

            # Handle built-in quote commands first
            if self.quotes_enabled:
                if command_name == "quote":
                    return self._handle_quote_command(arguments, message_data)
                elif command_name == "add_quote":
                    return self._handle_add_quote_command(arguments, message_data)

            lookup_job = None
            with self._lock:
                command = None
                for candidate in self.commands.values():
                    if (
                        candidate.enabled
                        and _command_matches_name(candidate, command_name)
                        and candidate.should_trigger(message_data)
                    ):
                        command = candidate
                        break
                if command is None:
                    return None

                can_use, reason = command.can_use(message_data)
                if not can_use:
                    logger.debug("[CHATBOT] Command '%s' blocked: %s", command_name, reason)
                    return _silent_result(command_name)

                data = message_data
                if command.command_type == CommandType.RESET:
                    target = _find_reset_target(
                        self.commands, command.reset_command, command.command_id
                    )
                    if target is None:
                        logger.debug(
                            "[CHATBOT] Reset command '%s' has unknown target '%s'",
                            command_name,
                            command.reset_command,
                        )
                        return _silent_result(command_name)
                    data = dict(message_data)
                    data["target_command"] = target.command_name
                    data["target_count"] = getattr(target, "counter_value", 0)
                    target.reset_counter()

                command.begin_use(data)
                if command.lookup:
                    lookup_job = (dict(command.lookup), command.build_context(data))
                    response = ""
                else:
                    response = command.render_response(data)
                targets = list(command.reply_targets)
                discord_channels = list(command.discord_channels or [])
                output_mode = command.output_mode or "chat"
                self._save_data()

            if lookup_job is not None:
                from .chatbot_lookups import run_lookup_blocking

                lookup_vars = run_lookup_blocking(lookup_job[0], lookup_job[1], kind="command")
                response = command.respond_after_lookup(data, lookup_vars)

            logger.info(f"Command triggered: {command_name} by {username}")
            if not str(response or "").strip():
                return _silent_result(command_name)
            return (response, command_name, targets, discord_channels, output_mode)

        except Exception as e:
            logger.error(f"Error processing chat message: {e}", exc_info=True)
            return None

    def _handle_quote_command(
        self, arguments: List[str], message_data: Dict[str, Any]
    ) -> Optional[tuple[str, str]]:
        """Handle the !quote command"""
        try:
            username = message_data.get("username", "Unknown")

            # Check global quote cooldown
            current_time = time.time()
            if (
                self.quote_cooldown_seconds > 0
                and current_time - self.quote_last_used < self.quote_cooldown_seconds
            ):
                # Blocked commands stay silent.
                logger.debug("[CHATBOT] !quote blocked: cooldown")
                return _silent_result("quote")

            if not arguments:
                # Random quote
                quote = self.get_random_quote()
                if quote:
                    response = self.format_quote_reply(quote)

                    # Update cooldown timestamp
                    self.quote_last_used = time.time()

                    # Track quote redemption
                    try:
                        stats_manager = get_statistics_manager()
                        stats_manager.increment_quote_redeemed(
                            quote.quote_id, username=username
                        )
                        logger.debug(
                            f"Tracked redemption of random quote #{quote.quote_number}"
                        )
                    except Exception as e:
                        logger.error(f"Error tracking quote redemption statistics: {e}")

                    logger.info(
                        f"Random quote #{quote.quote_number} requested by {username}"
                    )
                    return response, "quote"
                else:
                    return "No quotes available yet! Add some with !add_quote", "quote"

            # Check if first argument is a number
            try:
                quote_number = int(arguments[0])
                quote = self.get_quote_by_number(quote_number)
                if quote:
                    response = self.format_quote_reply(quote)

                    # Update cooldown timestamp
                    self.quote_last_used = time.time()

                    # Track quote redemption
                    try:
                        stats_manager = get_statistics_manager()
                        stats_manager.increment_quote_redeemed(
                            quote.quote_id, username=username
                        )
                        logger.debug(
                            f"Tracked redemption of specific quote #{quote.quote_number}"
                        )
                    except Exception as e:
                        logger.error(f"Error tracking quote redemption statistics: {e}")

                    logger.info(f"Quote #{quote_number} requested by {username}")
                    return response, "quote"
                else:
                    return f"Quote #{quote_number} not found!", "quote"
            except ValueError:
                # Not a number, treat as search text
                search_text = " ".join(arguments)
                matches = self.search_quotes(search_text, limit=1)
                if matches:
                    quote = matches[0]
                    response = self.format_quote_reply(quote)

                    # Update cooldown timestamp
                    self.quote_last_used = time.time()

                    # Track quote redemption
                    try:
                        stats_manager = get_statistics_manager()
                        stats_manager.increment_quote_redeemed(
                            quote.quote_id, username=username
                        )
                        logger.debug(
                            f"Tracked redemption of searched quote #{quote.quote_number}"
                        )
                    except Exception as e:
                        logger.error(f"Error tracking quote redemption statistics: {e}")

                    logger.info(
                        f"Quote search '{search_text}' by {username} returned #{quote.quote_number}"
                    )
                    return response, "quote"
                else:
                    return f"No quotes found matching '{search_text}'", "quote"

        except Exception as e:
            logger.error(f"Error handling quote command: {e}", exc_info=True)
            return "Error processing quote command", "quote"

    def _handle_add_quote_command(
        self, arguments: List[str], message_data: Dict[str, Any]
    ) -> Optional[tuple[str, str]]:
        """Handle the !add_quote command"""
        try:
            username = message_data.get("username", "Unknown")
            if not permission_allows(self.add_quote_permission, message_data):
                # Blocked commands stay silent.
                logger.debug("[CHATBOT] !add_quote blocked: %s needs %s", username, self.add_quote_permission)
                return _silent_result("add_quote")

            if not arguments:
                return 'Usage: !add_quote "quote text" [author]', "add_quote"

            # Parse the quote text and author
            quote_text = " ".join(arguments)

            # Check if the quote is in quotes
            author = ""
            if quote_text.startswith('"') and quote_text.count('"') >= 2:
                # Extract text between first set of quotes
                first_quote_end = quote_text.find('"', 1)
                if first_quote_end > 0:
                    text_part = quote_text[1:first_quote_end]
                    remaining = quote_text[first_quote_end + 1 :].strip()
                    # Check if there's an author after the quote
                    if remaining and not remaining.startswith('"'):
                        author = remaining.strip()
                    quote_text = text_part
            else:
                # No quotes, use the whole text as quote
                quote_text = quote_text.strip()

            if not quote_text:
                return "Quote text cannot be empty!", "add_quote"

            # Add the quote
            success, error, quote_number = self.add_quote(quote_text, author, username)

            if success:
                response = f"Quote #{quote_number} added successfully!"
                logger.info(f"Quote #{quote_number} added by {username}")
                return response, "add_quote"
            else:
                return f"Error adding quote: {error}", "add_quote"

        except Exception as e:
            logger.error(f"Error handling add_quote command: {e}", exc_info=True)
            return "Error processing add_quote command", "add_quote"

    def process_event(
        self, event_type: EventType, event_data: Dict[str, Any]
    ) -> Optional[Any]:
        """Process an automatic event.

        Returns None, one ``(response, reply_targets, discord_channels,
        output_mode)`` tuple, or a list of them. Events with a lookup are not
        returned: their lookup and delivery run on a background thread so the
        caller (often the EventSub loop) never waits for Twitch. Timer events
        (INTERVAL / SPECIFIC_TIME) only fire from their schedulers.
        """
        try:
            matches = []
            deferred = []
            with self._lock:
                for event in self.events.values():
                    if event.event_type in (EventType.INTERVAL, EventType.SPECIFIC_TIME):
                        continue
                    if not (
                        event.enabled
                        and event.event_type == event_type
                        and event.should_trigger(event_data)
                    ):
                        continue
                    event.begin_trigger(event_data)
                    logger.info(f"Event triggered: {event.name} for {event_type.value}")
                    if event.lookup:
                        deferred.append(event)
                        continue
                    response = event.render_response(event_data)
                    if str(response or "").strip():
                        matches.append(
                            (
                                response,
                                list(event.reply_targets),
                                list(event.discord_channels or []),
                                event.output_mode or "chat",
                            )
                        )
                if matches or deferred:
                    self._save_data()

            for event in deferred:
                threading.Thread(
                    target=self._lookup_and_deliver_event,
                    args=(event, dict(event_data or {})),
                    daemon=True,
                    name=f"ChatbotEventLookup-{event.name}",
                ).start()

            if not matches:
                return None
            if len(matches) == 1:
                return matches[0]
            return matches

        except Exception as e:
            logger.error(f"Error processing event {event_type}: {e}", exc_info=True)
            return None

    def _lookup_and_deliver_event(self, event: ChatEvent, data: Dict[str, Any]) -> None:
        """Run an event's lookup and send the reply (background thread)."""
        try:
            from .chatbot_lookups import run_lookup_blocking

            lookup_vars = run_lookup_blocking(event.lookup, event.build_context(data), kind="event")
            response = event.respond_after_lookup(data, lookup_vars)
            if not str(response or "").strip():
                return
            from .chatbot import deliver_chatbot_output

            deliver_chatbot_output(
                response,
                list(event.reply_targets),
                discord_channels=list(event.discord_channels or []),
                output_mode=event.output_mode,
            )
        except Exception as e:
            logger.error("Event %s lookup/delivery failed: %s", event.name, e, exc_info=True)

    # Utility Methods
    def toggle_item(self, item_id: str, enabled: bool) -> bool:
        """Enable / disable a command or event (nothing else changes).

        Re-enabling an interval event restarts its timer (first post after one
        full interval).
        """
        with self._lock:
            if item_id in self.commands:
                self.commands[item_id].enabled = bool(enabled)
                self._save_data()
                return True
            if item_id in self.events:
                event = self.events[item_id]
                event.enabled = bool(enabled)
                self._schedule_event(event)
                self._save_data()
                return True
        return False

    def reset_command_counter(self, command_id: str) -> bool:
        """Reset a command's counter"""
        with self._lock:
            if command_id in self.commands:
                self.commands[command_id].reset_counter()
                self._save_data()
                return True
        return False

    def reset_usage(self, command_id: str) -> bool:
        """Reset a command's usage count (its usage limit starts over)."""
        with self._lock:
            command = self.commands.get(command_id)
            if command is None:
                return False
            command.usage_count = 0
            self._save_data()
            return True

    def get_statistics(self) -> Dict[str, Any]:
        """Get chatbot statistics"""
        with self._lock:
            total_commands = len(self.commands)
            total_events = len(self.events)
            enabled_items = sum(
                1 for cmd in self.commands.values() if cmd.enabled
            ) + sum(1 for evt in self.events.values() if evt.enabled)
            total_triggers = sum(
                cmd.trigger_count for cmd in self.commands.values()
            ) + sum(evt.trigger_count for evt in self.events.values())

            # Count interval events specifically
            interval_events = [
                evt
                for evt in self.events.values()
                if evt.event_type == EventType.INTERVAL
            ]
            active_interval_events = sum(1 for evt in interval_events if evt.enabled)
            running_interval_tasks = sum(
                1 for evt in interval_events if evt.event_id in self.repeating_tasks
            )

            return {
                "total_commands": total_commands,
                "total_events": total_events,
                "enabled_items": enabled_items,
                "total_triggers": total_triggers,
                "interval_events": {
                    "total": len(interval_events),
                    "enabled": active_interval_events,
                    "running_tasks": running_interval_tasks,
                    "deferred": len(self._deferred_interval_events),
                },
            }

    # Timers (INTERVAL and SPECIFIC_TIME events)
    #
    # Each enabled timer event has one daemon thread with its own stop flag.
    # Threads start when the manager loads (no waiting for chat): interval
    # events post after every full interval (never right away) and skip a post
    # while chat is unavailable; specific-time events fire once in the
    # matching local minute. Only EventType.INTERVAL repeats.

    def _schedule_event(self, event: ChatEvent) -> None:
        """Stop any timer of this event, then start the right one (if any)."""
        self._unschedule_event(event.event_id)
        if not event.enabled or self.events.get(event.event_id) is not event:
            return
        if event.repeats():
            self._start_interval_thread(event)
        elif (
            event.event_type == EventType.SPECIFIC_TIME
            and normalize_specific_time(event.specific_time) is not None
        ):
            self._start_specific_time_event(event)

    def _unschedule_event(self, event_id: str) -> None:
        """Stop an event's timer thread (does not change the event)."""
        with self._scheduler_lock:
            stop = self._repeating_stops.pop(event_id, None)
            if stop is not None:
                stop.set()
            self.repeating_tasks.pop(event_id, None)

    def _timer_still_valid(self, event: ChatEvent, stop: threading.Event) -> bool:
        return (
            not stop.is_set()
            and event.enabled
            and self.events.get(event.event_id) is event
        )

    def _start_interval_thread(self, event: ChatEvent) -> None:
        with self._scheduler_lock:
            existing = self.repeating_tasks.get(event.event_id)
            if existing is not None and getattr(existing, "is_alive", lambda: False)():
                return
            stop = threading.Event()

            def interval_loop():
                try:
                    while True:
                        interval = float(event.interval or 0)
                        if interval <= 0 or stop.wait(interval):
                            break
                        if not self._timer_still_valid(event, stop) or not event.repeats():
                            break
                        self._fire_timer_event(
                            event,
                            {"username": "System", "timestamp": time.time(), "source": "interval"},
                        )
                except Exception as e:
                    logger.error(f"Error in interval timer for event {event.name}: {e}", exc_info=True)
                    self._notify_timer_stopped(event, e)

            thread = threading.Thread(target=interval_loop, daemon=True, name=f"Event-{event.name}")
            self._repeating_stops[event.event_id] = stop
            self.repeating_tasks[event.event_id] = thread
            thread.start()
            logger.debug("Started interval timer for event %s (%ss)", event.name, event.interval)

    # Backwards-compatible names
    def _start_repeating_event(self, event: ChatEvent, initial_delay: float = 0):
        if event.repeats():
            self._start_interval_thread(event)

    def _start_repeating_event_with_thread(self, event: ChatEvent, initial_delay: float = 0):
        self._start_repeating_event(event, initial_delay)

    def _stop_repeating_event(self, event_id: str):
        self._unschedule_event(event_id)

    @staticmethod
    def _specific_time_due(event: ChatEvent, now: datetime, last_key: Optional[str]) -> Tuple[bool, str]:
        """(fire now?, minute key) for a specific-time event at *now*.

        Fires once per matching local minute, also across restarts within the
        same minute (``last_triggered``).
        """
        key = now.strftime("%Y-%m-%d %H:%M")
        target = normalize_specific_time(event.specific_time)
        if target is None or now.strftime("%H:%M") != target or key == last_key:
            return False, key
        if event.last_triggered:
            try:
                fired = datetime.fromtimestamp(float(event.last_triggered)).strftime("%Y-%m-%d %H:%M")
            except (OverflowError, OSError, ValueError):
                fired = ""
            if fired == key:
                return False, key
        return True, key

    def _start_specific_time_event(self, event: ChatEvent):
        """Start a specific-time event on a daemon thread (checks every second)."""
        with self._scheduler_lock:
            existing = self.repeating_tasks.get(event.event_id)
            if existing is not None and getattr(existing, "is_alive", lambda: False)():
                return
            stop = threading.Event()

            def specific_time_loop():
                last_key: Optional[str] = None
                try:
                    while self._timer_still_valid(event, stop):
                        if event.event_type != EventType.SPECIFIC_TIME:
                            break
                        due, key = self._specific_time_due(event, datetime.now(), last_key)
                        if due:
                            last_key = key
                            self._fire_timer_event(
                                event,
                                {
                                    "username": "System",
                                    "timestamp": time.time(),
                                    "source": "specific_time",
                                    "scheduled_time": event.specific_time,
                                },
                            )
                        stop.wait(1.0)
                except Exception as e:
                    logger.error("Error in specific time thread for event %s: %s", event.name, e)
                    self._notify_timer_stopped(event, e)

            thread = threading.Thread(
                target=specific_time_loop, daemon=True, name=f"SpecificTime-{event.name}"
            )
            self._repeating_stops[event.event_id] = stop
            self.repeating_tasks[event.event_id] = thread
            thread.start()
            logger.debug("Started specific time timer for %s at %s", event.name, event.specific_time)

    @staticmethod
    def _notify_timer_stopped(event: ChatEvent, exc: Exception) -> None:
        notify(
            f"The timer event “{event.name}” stopped and won't post again until it's restarted.",
            type="warning",
            source="chatbot",
            title="A chatbot timer stopped",
            hint="Turn the event off and on again in Chatbot → Events, or restart Mycelian.",
            details=f"{type(exc).__name__}: {exc}",
            actions=nav_chatbot("Events"),
            dedupe_key=f"chatbot:timer:{event.event_id}",
            dedupe_cooldown_sec=3600,
        )

    def _fire_timer_event(self, event: ChatEvent, data: Dict[str, Any]) -> bool:
        """Post a timer event's reply. False when skipped."""
        from .chatbot import deliver_chatbot_output, is_chatbot_connected

        targets = list(event.reply_targets)
        if (
            event.output_mode != "banner"
            and targets == ["twitch"]
            and not event.discord_channels
            and not is_chatbot_connected()
        ):
            logger.debug("Skipping timer event %s: chat not connected", event.name)
            return False
        with self._lock:
            event.begin_trigger(data)
            context = event.build_context(data)
            response = "" if event.lookup else event.render_response(data)
            self._save_data()
        if event.lookup:
            from .chatbot_lookups import run_lookup_blocking

            lookup_vars = run_lookup_blocking(event.lookup, context, kind="event")
            response = event.respond_after_lookup(data, lookup_vars)
        if not str(response or "").strip():
            return False
        try:
            return bool(
                deliver_chatbot_output(
                    response,
                    targets,
                    discord_channels=list(event.discord_channels or []),
                    output_mode=event.output_mode,
                )
            )
        except Exception as e:
            logger.error(f"Error sending timer event {event.name}: {e}")
            return False

    def start_deferred_interval_events(self):
        """Make sure every enabled timer event has a running timer (idempotent).

        Kept for callers from chatbot staging (dedicated and fallback modes).
        """
        with self._lock:
            events = list(self.events.values())
        self._deferred_interval_events.clear()
        for event in events:
            if not event.enabled:
                continue
            thread = self.repeating_tasks.get(event.event_id)
            if thread is not None and getattr(thread, "is_alive", lambda: False)():
                continue
            try:
                self._schedule_event(event)
            except Exception as e:
                logger.error(f"Error starting timer for event {event.name}: {e}")

    # Test runs (no side effects)
    def _test_lookup_vars(self, item: Any, data: Dict[str, Any], kind: str, live_lookup: bool):
        if not item.lookup:
            return None, None
        from .chatbot_lookups import get_lookup, run_lookup_blocking
        from .chatbot_variables import example_lookup_variables

        lookup_id = item.lookup.get("id")
        entry = get_lookup(lookup_id)
        read_only = (entry is not None and entry.get("group") != "Do something") or (
            lookup_id == "advanced" and str(item.lookup.get("method") or "GET").upper() == "GET"
        )
        if live_lookup and read_only:
            return run_lookup_blocking(item.lookup, item.build_context(data), kind=kind), "live"
        return example_lookup_variables(item.lookup), "example"

    def test_command(
        self,
        command_id: str,
        sample: Optional[Dict[str, Any]] = None,
        *,
        send: bool = False,
        live_lookup: bool = False,
    ) -> "TestResult":
        """Dry-run a command on a copy: no counter / usage / cooldown / stats changes.

        Returns ``{"ok", "triggered", "response", "reason", "lookup"}``
        (``lookup``: None | "example" | "live"). The sample defaults to
        ``chatbot_variables.sample_data("command", item=...)`` (sent by the
        broadcaster, so permissions pass). Cooldown and usage limit are ignored
        for test runs. Lookups use example values unless ``live_lookup`` is
        True and the lookup only reads (never for "Do something" lookups).
        ``send=True`` also posts the reply to the command's outputs. The
        result can be awaited for old callers.
        """
        command = self.commands.get(command_id)
        if command is None:
            return TestResult(ok=False, triggered=False, response="", reason="Command not found")
        try:
            from .chatbot_variables import sample_data

            copy = ChatCommand.from_dict(command.to_dict())
            data = sample_data("command", item=copy)
            if sample:
                data.update(dict(sample))
            if not copy.enabled:
                return TestResult(ok=True, triggered=False, response="", reason="Command is disabled")
            if not copy.should_trigger(data):
                return TestResult(ok=True, triggered=False, response="", reason="Conditions not met")
            if not permission_allows(copy.permission, data):
                return TestResult(ok=True, triggered=False, response="",
                                  reason=f"Only {copy.permission} can use this command")
            if copy.command_type == CommandType.RESET:
                target = _find_reset_target(self.commands, copy.reset_command, copy.command_id)
                data["target_command"] = target.command_name if target else copy.reset_command
                data["target_count"] = target.counter_value if target else 0
            copy.begin_use(data, track_stats=False)
            lookup_vars, lookup_mode = self._test_lookup_vars(copy, data, "command", live_lookup)
            if lookup_vars is None:
                response = copy.render_response(data)
            else:
                response = copy.respond_after_lookup(data, lookup_vars)
            reason = "" if response.strip() else "Lookup failed; nothing would be posted"
            if send and response.strip():
                self._send_test_output(copy, response)
            return TestResult(ok=True, triggered=bool(response.strip()), response=response,
                              reason=reason, lookup=lookup_mode)
        except Exception as e:
            logger.error("Command test failed: %s", e, exc_info=True)
            return TestResult(ok=False, triggered=False, response="", reason=str(e))

    def test_event(
        self,
        event_id: str,
        sample: Optional[Dict[str, Any]] = None,
        *,
        send: bool = False,
        live_lookup: bool = False,
    ) -> "TestResult":
        """Dry-run an event on a copy with sample data for its own type.

        Same result shape and options as :meth:`test_command`.
        """
        event = self.events.get(event_id)
        if event is None:
            return TestResult(ok=False, triggered=False, response="", reason="Event not found")
        try:
            from .chatbot_variables import sample_data

            copy = ChatEvent.from_dict(event.to_dict())
            copy.enabled = True
            data = sample_data("event", copy.event_type.value, item=copy)
            if sample:
                data.update(dict(sample))
            if not copy.should_trigger(data):
                return TestResult(ok=True, triggered=False, response="",
                                  reason="This sample does not pass the event's filters")
            copy.begin_trigger(data, track_stats=False)
            lookup_vars, lookup_mode = self._test_lookup_vars(copy, data, "event", live_lookup)
            if lookup_vars is None:
                response = copy.render_response(data)
            else:
                response = copy.respond_after_lookup(data, lookup_vars)
            reason = "" if response.strip() else "Lookup failed; nothing would be posted"
            if send and response.strip():
                self._send_test_output(copy, response)
            return TestResult(ok=True, triggered=bool(response.strip()), response=response,
                              reason=reason, lookup=lookup_mode)
        except Exception as e:
            logger.error("Event test failed: %s", e, exc_info=True)
            return TestResult(ok=False, triggered=False, response="", reason=str(e))

    @staticmethod
    def _send_test_output(item: Any, response: str) -> None:
        from .chatbot import deliver_chatbot_output

        deliver_chatbot_output(
            response,
            list(item.reply_targets),
            discord_channels=list(item.discord_channels or []),
            output_mode=item.output_mode,
        )

    # Quote Management Methods
    def add_quote(
        self, text: str, author: str = "", added_by: str = ""
    ) -> tuple[bool, str, int]:
        """Add a new quote

        Args:
            text: The quote text
            author: The author of the quote (optional)
            added_by: Username of who added the quote

        Returns:
            tuple of (success, error_message, quote_number)
        """
        with self._lock:
            if not text.strip():
                return False, "Quote text cannot be empty", 0

            # Get the next quote number
            if self.quotes:
                next_number = (
                    max(quote.quote_number for quote in self.quotes.values()) + 1
                )
            else:
                next_number = 1

            # Create the quote
            quote_id = str(uuid.uuid4())
            quote = Quote(
                quote_id=quote_id,
                quote_number=next_number,
                text=text.strip(),
                author=author.strip(),
                date_added=time.time(),
                added_by=added_by,
            )

            self.quotes[quote_id] = quote
            self._save_data()

            # Note: We don't track quote creation count in statistics anymore
            # since we get it dynamically from the chatbot_manager itself

            logger.info(f"Added quote #{next_number}: '{text[:50]}...' by {added_by}")
            return True, "", next_number

    def get_quote_by_number(self, quote_number: int) -> Optional[Quote]:
        """Get a quote by its number"""
        with self._lock:
            for quote in self.quotes.values():
                if quote.quote_number == quote_number:
                    return quote
            return None

    def get_quote_by_id(self, quote_id: str) -> Optional[Quote]:
        """Get a quote by its ID"""
        return self.quotes.get(quote_id)

    def get_random_quote(self) -> Optional[Quote]:
        """Get a random quote"""
        with self._lock:
            if not self.quotes:
                return None

            import random

            quotes_list = list(self.quotes.values())
            return random.choice(quotes_list)

    def search_quotes(self, search_text: str, limit: int = 5) -> List[Quote]:
        """Search quotes by text content

        Args:
            search_text: Text to search for
            limit: Maximum number of results to return

        Returns:
            List of matching quotes ordered by relevance
        """
        if not search_text.strip():
            return []

        search_text = search_text.lower().strip()
        matches = []

        with self._lock:
            for quote in self.quotes.values():
                search_content = quote.get_search_text()
                # Simple text matching - could be enhanced with more sophisticated algorithms
                if search_text in search_content:
                    # Calculate a simple relevance score based on position and exact matches
                    score = 0
                    if search_content.startswith(search_text):
                        score = 100  # Exact match at beginning
                    elif search_content.find(f" {search_text} ") >= 0:
                        score = 75  # Word match
                    else:
                        score = 50  # Partial match

                    matches.append((quote, score))

        # Sort by score (highest first) and return top results
        matches.sort(key=lambda x: x[1], reverse=True)
        return [quote for quote, score in matches[:limit]]

    def get_all_quotes(self) -> Dict[str, Quote]:
        """Get all quotes"""
        return self.quotes.copy()

    def delete_quote(self, quote_id: str) -> bool:
        """Delete a quote by ID"""
        with self._lock:
            if quote_id in self.quotes:
                quote = self.quotes[quote_id]
                del self.quotes[quote_id]
                self._save_data()
                logger.info(
                    f"Deleted quote #{quote.quote_number}: '{quote.text[:50]}...'"
                )
                return True
            return False

    def delete_quote_by_number(self, quote_number: int) -> bool:
        """Delete a quote by its number"""
        with self._lock:
            for quote_id, quote in self.quotes.items():
                if quote.quote_number == quote_number:
                    return self.delete_quote(quote_id)
            return False

    def format_quote_reply(self, quote: Quote, template: Optional[str] = None) -> str:
        """The chat reply for *quote* using the quote reply format.

        Variables: {number} {text} {author} {date_added} {added_by}. When the quote has
        no author, the " — {author}" part (with -, – or —) is left out.
        """
        fmt = template if template is not None else (self.quote_reply_format or DEFAULT_QUOTE_REPLY_FORMAT)
        if not str(quote.author or "").strip():
            fmt = re.sub(r"\s*(?:[-\u2013\u2014]|by)\s*\{author\}", "", fmt)
            fmt = fmt.replace("{author}", "")
        when = datetime.fromtimestamp(quote.date_added) if quote.date_added else None
        context = {
            "number": quote.quote_number,
            "text": quote.text,
            "author": quote.author or "",
            "date_added": when.strftime("%b %d, %Y").replace(" 0", " ") if when else "",
            "added_by": quote.added_by or "",
            "quote_number": quote.quote_number,
        }
        return render_template(fmt, context).strip()

    def renumber_quotes(self) -> int:
        """Number quotes 1..N in their current order (closes gaps). Returns how many changed."""
        with self._lock:
            ordered = sorted(self.quotes.values(), key=lambda q: (q.quote_number, q.date_added))
            changed = 0
            for index, quote in enumerate(ordered, 1):
                if quote.quote_number != index:
                    quote.quote_number = index
                    changed += 1
            if changed:
                self._save_data()
            return changed

    def quote_number_gaps(self) -> List[int]:
        with self._lock:
            numbers = sorted(q.quote_number for q in self.quotes.values())
        if not numbers:
            return []
        present = set(numbers)
        return [n for n in range(1, numbers[-1]) if n not in present]

    def save_quote_settings(self, *, enabled: bool, cooldown_seconds: int, add_permission: str, reply_format: str) -> bool:
        """Save every quote setting at once (the settings dialog's Save)."""
        with self._lock:
            self.quotes_enabled = bool(enabled)
            self.quote_cooldown_seconds = max(0, int(cooldown_seconds or 0))
            self.add_quote_permission = normalize_permission(add_permission)
            self.quote_reply_format = reply_format.strip() if str(reply_format or "").strip() else DEFAULT_QUOTE_REPLY_FORMAT
            self._save_data()
            return True

    def update_quote(
        self, quote_id: str, text: str = None, author: str = None, *, number: Optional[int] = None,
        added_by: Optional[str] = None,
    ) -> bool:
        """Update an existing quote's text, author, number and/or who added it."""
        with self._lock:
            if quote_id not in self.quotes:
                return False

            quote = self.quotes[quote_id]
            if number is not None:
                number = int(number)
                if number < 1 or any(q.quote_number == number for qid, q in self.quotes.items() if qid != quote_id):
                    return False
                quote.quote_number = number
            if added_by is not None:
                quote.added_by = added_by.strip()

            if text is not None:
                if not text.strip():
                    return False
                quote.text = text.strip()

            if author is not None:
                quote.author = author.strip()

            self._save_data()
            logger.info(f"Updated quote #{quote.quote_number}")
            return True

    def toggle_quotes_enabled(self, enabled: bool) -> bool:
        """Toggle whether the quote system is enabled"""
        with self._lock:
            self.quotes_enabled = enabled
            self._save_data()
            logger.info(f"Quote system {'enabled' if enabled else 'disabled'}")
            return True

    def get_quotes_enabled(self) -> bool:
        """Check if quotes system is enabled"""
        return self.quotes_enabled

    def get_quote_cooldown(self) -> int:
        """Get the global quote cooldown in seconds"""
        return self.quote_cooldown_seconds

    def set_quote_cooldown(self, cooldown_seconds: int) -> bool:
        """Set the global quote cooldown in seconds"""
        with self._lock:
            if cooldown_seconds < 0:
                cooldown_seconds = 0
            self.quote_cooldown_seconds = cooldown_seconds
            self._save_data()
            logger.info(f"Quote cooldown set to {cooldown_seconds} seconds")
            return True

    def get_quotes_statistics(self) -> Dict[str, Any]:
        """Get quotes statistics"""
        with self._lock:
            return {
                "total_quotes": len(self.quotes),
                "quotes_enabled": self.quotes_enabled,
                "oldest_quote": min([q.date_added for q in self.quotes.values()])
                if self.quotes
                else None,
                "newest_quote": max([q.date_added for q in self.quotes.values()])
                if self.quotes
                else None,
            }

    # Greeting Management Methods
    def add_greeting(
        self, user_id: str, username: str, greeting_text: str, enabled: bool = True
    ) -> tuple[bool, str, str]:
        """Add a new greeting for a user

        Args:
            user_id: Twitch user ID (primary key)
            username: Current username
            greeting_text: Custom greeting text
            enabled: Whether greeting is enabled

        Returns:
            tuple of (success, error_message, greeting_id)
        """
        with self._lock:
            if not user_id.strip():
                return False, "User ID cannot be empty", ""

            if not username.strip():
                return False, "Username cannot be empty", ""

            if not greeting_text.strip():
                return False, "Greeting text cannot be empty", ""

            # Check if user already has a greeting
            if user_id in self.greetings_by_user_id:
                return False, f"User {username} already has a greeting", ""

            # Create the greeting
            greeting_id = str(uuid.uuid4())
            greeting = Greeting(
                greeting_id=greeting_id,
                user_id=user_id,
                username=username,
                greeting_text=greeting_text,
                enabled=enabled,
                last_greeted=0,  # Never greeted
                date_created=time.time(),
                last_username_update=time.time(),
            )

            self.greetings[greeting_id] = greeting
            self.greetings_by_user_id[user_id] = greeting
            self._save_data()

            logger.info(f"Added greeting for user {username} (ID: {user_id})")
            return True, "", greeting_id

    def update_greeting(
        self, greeting_id: str, greeting_text: str = None, enabled: bool = None
    ) -> bool:
        """Update an existing greeting"""
        with self._lock:
            if greeting_id not in self.greetings:
                return False

            greeting = self.greetings[greeting_id]

            if greeting_text is not None:
                greeting.greeting_text = greeting_text

            if enabled is not None:
                greeting.enabled = enabled

            self._save_data()
            logger.info(f"Updated greeting for user {greeting.username}")
            return True

    def update_greeting_username(self, user_id: str, new_username: str) -> bool:
        """Update username for a greeting (when username changes)"""
        with self._lock:
            if user_id not in self.greetings_by_user_id:
                return False

            greeting = self.greetings_by_user_id[user_id]
            greeting.update_username(new_username)
            self._save_data()

            logger.debug(f"Updated username for greeting: {user_id} -> {new_username}")
            return True

    def remove_greeting(self, greeting_id: str) -> bool:
        """Remove a greeting"""
        with self._lock:
            if greeting_id not in self.greetings:
                return False

            greeting = self.greetings[greeting_id]
            user_id = greeting.user_id

            # Remove from both dictionaries
            del self.greetings[greeting_id]
            if user_id in self.greetings_by_user_id:
                del self.greetings_by_user_id[user_id]

            self._save_data()
            logger.info(f"Removed greeting for user {greeting.username}")
            return True

    def get_greeting(self, greeting_id: str) -> Optional[Greeting]:
        """Get a greeting by ID"""
        return self.greetings.get(greeting_id)

    def get_greeting_by_user_id(self, user_id: str) -> Optional[Greeting]:
        """Get a greeting by user ID (fast lookup)"""
        return self.greetings_by_user_id.get(user_id)

    def get_all_greetings(self) -> Dict[str, Greeting]:
        """Get all greetings"""
        return self.greetings.copy()

    def process_greeting(
        self, user_id: str, username: str, current_time: float = None
    ) -> Optional[str]:
        """Process a greeting for a user

        Args:
            user_id: Twitch user ID
            username: Current username
            current_time: Current timestamp (optional)

        Returns:
            Greeting message if user should be greeted, None otherwise
        """
        if current_time is None:
            current_time = time.time()

        with self._lock:
            # Check and perform global reset if needed
            global_reset_performed = self.flag_manager.perform_reset_if_needed(
                self.greeting_reset_interval_hours, current_time
            )

            # If global reset was performed, also reset custom greeting flags
            if global_reset_performed:
                for greeting in self.greetings.values():
                    greeting.greeted_flag = False
                self._save_data()
                logger.info("Reset custom greeting flags due to global reset interval")

            # Check if user has a custom greeting first
            greeting = self.greetings_by_user_id.get(user_id)

            # A personal greeting that is switched off falls through to the greeting for everyone else.
            if greeting and self.custom_greeting_enabled and greeting.enabled:
                # Update username if it has changed
                greeting.update_username(username)

                # Check if user should be greeted based on custom greeting flag and reset interval
                if not greeting.greeted_flag:
                    # Mark as greeted and save
                    greeting.greeted_flag = True
                    greeting.update_last_greeted(current_time)
                    self._save_data()

                    greeting_text = self.render_greeting(greeting.greeting_text, user_id, username)
                    logger.info(f"Sending custom greeting to {username}")
                    return greeting_text
                else:
                    # User has been greeted before, check if we should reset their flag based on interval
                    if (
                        self.greeting_reset_interval_hours > 0
                        and greeting.last_greeted > 0
                    ):
                        hours_since_greeted = (
                            current_time - greeting.last_greeted
                        ) / 3600
                        if hours_since_greeted >= self.greeting_reset_interval_hours:
                            # Reset the flag so user can be greeted again
                            greeting.greeted_flag = False
                            logger.debug(
                                f"Reset custom greeting flag for {username} after {hours_since_greeted:.1f} hours"
                            )
                            # Don't save here - we'll save when we greet them

                            # Now greet them since flag was reset
                            greeting.greeted_flag = True
                            greeting.update_last_greeted(current_time)
                            self._save_data()

                            greeting_text = self.render_greeting(greeting.greeting_text, user_id, username)
                            logger.info(
                                f"Sending custom greeting to {username} (reset after interval)"
                            )
                            return greeting_text

                return None  # User was already greeted with custom greeting

            # No custom greeting, check if default greeting is enabled
            if self.default_greeting_enabled and self.default_greeting_text:
                # Use flag manager for simple boolean-based greeting tracking
                should_greet = self.flag_manager.should_greet_user(user_id)

                if should_greet:
                    # Mark user as greeted
                    self.flag_manager.mark_user_greeted(user_id)

                    greeting_text = self.render_greeting(self.default_greeting_text, user_id, username)
                    logger.info(f"Sending default greeting to {username}")
                    return greeting_text

                return None  # User was already greeted with default greeting
        return None

    @staticmethod
    def greeting_context(user_id: str, username: str, user_login: str = "") -> Dict[str, Any]:
        return {"username": username, "user_id": user_id, "user_login": user_login or str(username or "").lower()}

    def render_greeting(self, text: str, user_id: str, username: str, user_login: str = "") -> str:
        """Greeting text with every reply variable filled in (single pass, viewer text never expanded)."""
        return render_template(text or "", self.greeting_context(user_id, username, user_login)).strip()

    def reset_greeting_for(self, greeting_id: str) -> bool:
        """Let one viewer with a personal greeting be greeted again next time they chat."""
        with self._lock:
            greeting = self.greetings.get(greeting_id)
            if greeting is None:
                return False
            greeting.greeted_flag = False
            greeting.last_greeted = 0
            self._save_data()
            return True

    def greeted_since_reset(self) -> Dict[str, Any]:
        """How many viewers have been greeted since the last reset, and when that was."""
        stats = self.flag_manager.get_stats()
        with self._lock:
            custom = sum(1 for g in self.greetings.values() if g.greeted_flag)
        return {"count": int(stats.get("greeted_users", 0)) + custom, "since": stats.get("last_reset_time") or 0}

    def save_greeting_settings(self, *, default_enabled: bool, custom_enabled: bool, default_text: str,
                               reset_interval_hours: int) -> bool:
        """Save every greeting setting at once (the dialogs' Save)."""
        if not str(default_text or "").strip():
            return False
        with self._lock:
            self.default_greeting_enabled = bool(default_enabled)
            self.custom_greeting_enabled = bool(custom_enabled)
            self.greetings_enabled = self.default_greeting_enabled or self.custom_greeting_enabled
            self.default_greeting_text = default_text.strip()
            self.greeting_reset_interval_hours = max(1, int(reset_interval_hours or 24))
            self._save_data()
            return True

    def toggle_greetings_enabled(self, enabled: bool) -> bool:
        """Toggle whether the greetings system is enabled (legacy method)"""
        with self._lock:
            self.greetings_enabled = enabled
            # Also update the separate toggles for backwards compatibility
            self.default_greeting_enabled = enabled
            self.custom_greeting_enabled = enabled
            self._save_data()
            logger.info(f"Greetings system {'enabled' if enabled else 'disabled'}")
            return True

    def toggle_default_greeting_enabled(self, enabled: bool) -> bool:
        """Toggle whether default greetings are enabled"""
        with self._lock:
            self.default_greeting_enabled = enabled
            self._save_data()
            logger.info(f"Default greetings {'enabled' if enabled else 'disabled'}")
            return True

    def toggle_custom_greeting_enabled(self, enabled: bool) -> bool:
        """Toggle whether custom greetings are enabled"""
        with self._lock:
            self.custom_greeting_enabled = enabled
            self._save_data()
            logger.info(f"Custom greetings {'enabled' if enabled else 'disabled'}")
            return True

    def get_default_greeting_enabled(self) -> bool:
        """Check if default greetings are enabled"""
        return self.default_greeting_enabled

    def get_custom_greeting_enabled(self) -> bool:
        """Check if custom greetings are enabled"""
        return self.custom_greeting_enabled

    def update_default_greeting(self, greeting_text: str) -> bool:
        """Update the default greeting text"""
        with self._lock:
            self.default_greeting_text = greeting_text
            self._save_data()
            logger.info("Updated default greeting text")
            return True

    def update_greeting_cooldown(self, hours: int) -> bool:
        """Update the greeting cooldown in hours"""
        with self._lock:
            self.greeting_cooldown_hours = hours
            self._save_data()
            logger.info(f"Updated greeting cooldown to {hours} hours")
            return True

    def get_greetings_enabled(self) -> bool:
        """Check if greetings system is enabled"""
        return self.greetings_enabled

    def get_default_greeting(self) -> str:
        """Get the default greeting text"""
        return self.default_greeting_text

    def get_greeting_cooldown(self) -> int:
        """Get the greeting cooldown in hours"""
        return self.greeting_cooldown_hours

    def get_greeting_flag_stats(self) -> Dict[str, Any]:
        """Get greeting flag file statistics"""
        return self.flag_manager.get_stats()

    def reset_all_greeting_flags(self):
        """Reset all greeting flags (both custom and default)"""
        with self._lock:
            # Reset all custom greeting flags
            for greeting in self.greetings.values():
                greeting.greeted_flag = False
            self._save_data()

            # Reset all default greeting flags
            self.flag_manager.reset_all_flags()

            logger.info("Reset all greeting flags for all users")

    def get_greeting_reset_interval(self) -> int:
        """Get the greeting reset interval in hours"""
        return self.greeting_reset_interval_hours

    def set_greeting_reset_interval(self, hours: int) -> bool:
        """Set the greeting reset interval in hours"""
        if hours < 1:
            return False

        with self._lock:
            self.greeting_reset_interval_hours = hours
            self._save_data()
            logger.info(f"Set greeting reset interval to {hours} hours")
            return True

    def get_greetings_statistics(self) -> Dict[str, Any]:
        """Get greetings statistics"""
        with self._lock:
            custom_greetings = len(
                [
                    g
                    for g in self.greetings.values()
                    if g.greeting_text != self.default_greeting_text
                ]
            )
            default_greetings_used = len(self.greetings) - custom_greetings

            return {
                "total_greetings": len(self.greetings),
                "custom_greetings": custom_greetings,
                "default_greetings_used": default_greetings_used,
                "greetings_enabled": self.greetings_enabled,
                "default_greeting_text": self.default_greeting_text,
                "greeting_cooldown_hours": self.greeting_cooldown_hours,
            }

    def cleanup(self):
        """Cleanup resources"""
        with self._scheduler_lock:
            for stop in self._repeating_stops.values():
                stop.set()
            self._repeating_stops.clear()
            self.repeating_tasks.clear()

        # Force save any pending flag changes
        if hasattr(self, "flag_manager"):
            self.flag_manager.force_save()

        logger.info("Chatbot manager cleanup completed")


# Global instance
_chatbot_manager = None
_manager_lock = threading.Lock()


def _register_shutdown_flush(name: str, func) -> None:
    try:
        from .shutdown import register_shutdown_flush
    except ImportError:
        return
    register_shutdown_flush(name, func)


def get_manager() -> ChatbotManager:
    """Get the global chatbot manager instance"""
    global _chatbot_manager

    if _chatbot_manager is None:
        with _manager_lock:
            if _chatbot_manager is None:
                logger.info(" Creating new ChatbotManager instance")
                _chatbot_manager = ChatbotManager()
                logger.info(
                    f"ChatbotManager created with {len(_chatbot_manager.commands)} commands, {len(_chatbot_manager.events)} events, {len(_chatbot_manager.quotes)} quotes"
                )
                _register_shutdown_flush(
                    "chatbot_data", _chatbot_manager.flush_pending_saves
                )

    return _chatbot_manager

    if _chatbot_manager is None:
        with _manager_lock:
            if _chatbot_manager is None:
                logger.info(" Creating new ChatbotManager instance")
                _chatbot_manager = ChatbotManager()
                logger.info(
                    f"ChatbotManager created with {len(_chatbot_manager.commands)} commands, {len(_chatbot_manager.events)} events, {len(_chatbot_manager.quotes)} quotes"
                )

    return _chatbot_manager
