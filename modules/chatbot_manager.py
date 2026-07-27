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

import asyncio
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .chatbot_core import (
    ChatCommand,
    ChatEvent,
    CommandType,
    EventType,
    Greeting,
    Quote,
)
from .statistics_manager import get_statistics_manager
from .notification_engine import nav_actions_main_tab, notify_critical

logger = logging.getLogger(__name__)


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
                            # New format
                            self.greeted_flags = {
                                uid: bool(flag)
                                for uid, flag in data["greeted_flags"].items()
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
            import json

            data = {
                "greeted_flags": self.greeted_flags,
                "last_reset_time": self.last_reset_time,
            }

            with open(self.file_path, "w") as f:
                json.dump(data, f, indent=2)
            self._last_save = time.time()
            self._dirty = False
            logger.debug(
                f"Saved {len(self.greeted_flags)} greeting flags to {self.file_path}"
            )
        except Exception as e:
            logger.error(f"Failed to save greeting flags to {self.file_path}: {e}")

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
            self.greeted_flags = {uid: False for uid in self.greeted_flags.keys()}
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
        self.quote_last_used: float = 0.0  # Timestamp of last quote usage

        # Initialize flag manager for efficient default greeting tracking
        self.flag_manager = GreetingFlagManager()

        self._lock = threading.RLock()

        # Command cooldown tracking
        self.command_cooldowns: Dict[str, float] = {}

        # Repeating command scheduler
        self.repeating_tasks: Dict[str, asyncio.Task] = {}
        self._scheduler_lock = threading.Lock()
        self._deferred_interval_events: List[
            ChatEvent
        ] = []  # Events waiting for chatbot readiness

        # Load saved data
        self._load_data()

    def _migrate_legacy_data(self):
        """Migrate data from legacy JSON files to database manager"""
        try:
            import json
            import os

            from .database_manager import get_data, set_data

            data_dir = os.path.join(os.path.dirname(__file__), "..", "data")

            # Check if migration is needed (if database is empty but JSON files exist)
            existing_db_data = get_data("BotData/Commands")
            if existing_db_data is not None:
                logger.debug("Database already has data, skipping migration")
                return

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
                "Chatbot data migration from old files failed. Check logs.",
                dedupe_key="chatbot:migrate_failed",
                actions=nav_actions_main_tab("Chatbot"),
            )

    def _load_data(self):
        """Load commands and events from database manager"""
        try:
            # First, check if we need to migrate legacy data
            self._migrate_legacy_data()

            # Import database manager here to avoid circular imports
            from .database_manager import get_data

            # Load commands
            commands_data = get_data("BotData/Commands")
            if commands_data and isinstance(commands_data, list):
                for cmd_data in commands_data:
                    try:
                        command = ChatCommand.from_dict(cmd_data)
                        self.commands[command.command_id] = command
                    except Exception as e:
                        logger.error(
                            f"Error loading command {cmd_data.get('command_id', 'unknown')}: {e}"
                        )

            # Load events
            events_data = get_data("BotData/Events")
            if events_data and isinstance(events_data, list):
                for event_data in events_data:
                    try:
                        event = ChatEvent.from_dict(event_data)
                        self.events[event.event_id] = event
                    except Exception as e:
                        logger.error(
                            f"Error loading event {event_data.get('event_id', 'unknown')}: {e}"
                        )

                # Restart repeating tasks for events that have intervals or are specific time events
                # Separate Interval events for staggered startup
                interval_events = []
                specific_time_events = []
                other_events = []

                logger.info(f"Processing {len(self.events)} loaded events for startup")
                for event in self.events.values():
                    logger.info(
                        f"Event: {event.name} (ID: {event.event_id}), enabled: {event.enabled}, type: {event.event_type}, interval: {event.interval}"
                    )
                    if event.enabled:
                        if event.event_type == EventType.SPECIFIC_TIME:
                            specific_time_events.append(event)
                            logger.info(f"Added {event.name} to specific_time_events")
                        elif event.interval > 0:
                            if event.event_type == EventType.INTERVAL:
                                interval_events.append(event)
                                logger.info(f"Added {event.name} to interval_events")
                            else:
                                other_events.append(event)
                                logger.info(f"Added {event.name} to other_events")

                # Always defer interval events during manager initialization to avoid event loop issues
                # Only the chatbot API staging process should start interval events when it knows
                # there's a proper event loop available
                logger.info(
                    f"Deferring {len(interval_events)} interval events during manager initialization - they will start when chatbot API is staged"
                )

                # Store ALL interval events as deferred - they'll be started by chatbot API staging
                self._deferred_interval_events.extend(interval_events)
                logger.info(
                    f"Deferred events list now contains: {[e.name for e in self._deferred_interval_events]}"
                )

                # Start specific time events immediately (they handle their own event loop issues)
                for event in specific_time_events:
                    try:
                        self._start_repeating_event(event)
                    except Exception as e:
                        logger.error(
                            f"Error restarting specific time task for event {event.name}: {e}"
                        )

                # Start non-Interval events immediately (they handle their own event loop issues)
                for event in other_events:
                    try:
                        self._start_repeating_event(event)
                    except Exception as e:
                        logger.error(
                            f"Error restarting repeating task for event {event.name}: {e}"
                        )

            # Load quotes
            quotes_data = get_data("BotData/Quotes")
            if quotes_data and isinstance(quotes_data, list):
                for quote_data in quotes_data:
                    try:
                        quote = Quote.from_dict(quote_data)
                        self.quotes[quote.quote_id] = quote
                    except Exception as e:
                        logger.error(
                            f"Error loading quote {quote_data.get('quote_id', 'unknown')}: {e}"
                        )

            # Load settings with defensive type validation
            settings_data = get_data("BotData/Settings")
            if settings_data and isinstance(settings_data, dict):
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

            # Load greetings
            greetings_data = get_data("BotData/Greetings")
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

            logger.info(
                f"Loaded {len(self.commands)} commands, {len(self.events)} events, {len(self.quotes)} quotes, and {len(self.greetings)} greetings"
            )

        except Exception as e:
            logger.error(f"Error loading chatbot data: {e}")
            notify_critical(
                "Could not load chatbot commands and settings from the database.",
                dedupe_key="chatbot:load_failed",
                actions=nav_actions_main_tab("Chatbot"),
            )

    def _save_data(self):
        """Save commands and events to database manager"""
        try:
            # Import database manager here to avoid circular imports
            from .database_manager import set_data

            # Save commands
            commands_data = [cmd.to_dict() for cmd in self.commands.values()]
            set_data("BotData/Commands", commands_data)

            # Save events
            events_data = [evt.to_dict() for evt in self.events.values()]
            set_data("BotData/Events", events_data)

            # Save quotes
            quotes_data = [quote.to_dict() for quote in self.quotes.values()]
            set_data("BotData/Quotes", quotes_data)

            # Save greetings
            greetings_data = [
                greeting.to_dict() for greeting in self.greetings.values()
            ]
            set_data("BotData/Greetings", greetings_data)

            # Save settings - ensure all values are JSON-serializable primitives
            # This guards against NiceGUI event objects or UI components being stored
            settings_data = {
                "quotes_enabled": bool(self.quotes_enabled) if not isinstance(self.quotes_enabled, bool) else self.quotes_enabled,
                "greetings_enabled": bool(self.greetings_enabled) if not isinstance(self.greetings_enabled, bool) else self.greetings_enabled,
                "default_greeting_enabled": bool(self.default_greeting_enabled) if not isinstance(self.default_greeting_enabled, bool) else self.default_greeting_enabled,
                "custom_greeting_enabled": bool(self.custom_greeting_enabled) if not isinstance(self.custom_greeting_enabled, bool) else self.custom_greeting_enabled,
                "default_greeting_text": str(self.default_greeting_text) if self.default_greeting_text else "@{username} welcome to the stream!",
                "greeting_cooldown_hours": int(self.greeting_cooldown_hours) if not isinstance(self.greeting_cooldown_hours, int) else self.greeting_cooldown_hours,
                "greeting_reset_interval_hours": int(self.greeting_reset_interval_hours) if not isinstance(self.greeting_reset_interval_hours, int) else self.greeting_reset_interval_hours,
                "quote_cooldown_seconds": int(self.quote_cooldown_seconds) if not isinstance(self.quote_cooldown_seconds, int) else self.quote_cooldown_seconds,
            }
            set_data("BotData/Settings", settings_data)

            logger.debug("Saved chatbot data")

        except Exception as e:
            logger.error(f"Error saving chatbot data: {e}")
            notify_critical(
                "Could not save chatbot data to the database.",
                dedupe_key="chatbot:save_failed",
                actions=nav_actions_main_tab("Chatbot"),
            )

    def get_manager(self):
        """Get the manager instance (for compatibility)"""
        return self

    # Command Management
    def add_command(self, command: ChatCommand) -> bool:
        """Add a new command"""
        with self._lock:
            if not command.command_id:
                command.command_id = str(uuid.uuid4())

            self.commands[command.command_id] = command
            self._save_data()

            # Note: We don't track command creation count in statistics anymore
            # since we get it dynamically from the chatbot_manager itself

            # Start repeating task if enabled
            if command.repeating_enabled:
                self._start_repeating_command(command)

            logger.info(f"Added command: {command.command_name}")
            return True

    def update_command(self, command_id: str, command: ChatCommand) -> bool:
        """Update an existing command"""
        with self._lock:
            if command_id not in self.commands:
                return False

            old_command = self.commands[command_id]

            # Stop old repeating task if it exists
            if old_command.repeating_enabled:
                self._stop_repeating_command(command_id)

            command.command_id = command_id
            self.commands[command_id] = command
            self._save_data()

            # Start new repeating task if enabled
            if command.repeating_enabled:
                self._start_repeating_command(command)

            logger.info(f"Updated command: {command.command_name}")
            return True

    def remove_command(self, command_id: str) -> bool:
        """Remove a command"""
        with self._lock:
            if command_id not in self.commands:
                return False

            command = self.commands[command_id]

            # Stop repeating task if it exists
            if command.repeating_enabled:
                self._stop_repeating_command(command_id)

            del self.commands[command_id]
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
    def add_event(self, event: ChatEvent) -> bool:
        """Add a new event"""
        with self._lock:
            # Validate Interval events require an interval
            if event.event_type == EventType.INTERVAL and event.interval <= 0:
                logger.error(
                    f"Cannot add Interval event '{event.name}' without an interval set"
                )
                return False

            # Validate Specific Time events require a time
            if event.event_type == EventType.SPECIFIC_TIME and not event.specific_time:
                logger.error(
                    f"Cannot add Specific Time event '{event.name}' without a time set"
                )
                return False

            if not event.event_id:
                event.event_id = str(uuid.uuid4())

            self.events[event.event_id] = event
            self._save_data()

            # Start repeating task if event has an interval or is a specific time event
            if event.enabled:
                if event.event_type == EventType.SPECIFIC_TIME:
                    self._start_repeating_event(event)
                elif event.interval > 0:
                    # For Interval events, use staggered startup
                    if event.event_type == EventType.INTERVAL:
                        # Calculate delay based on number of existing Interval events
                        existing_interval_count = sum(
                            1
                            for e in self.events.values()
                            if e.event_type == EventType.INTERVAL
                            and e.event_id != event.event_id
                        )
                        initial_delay = (
                            (existing_interval_count * 2) % event.interval
                            if event.interval > 0
                            else 0
                        )
                        self._start_repeating_event(event, initial_delay=initial_delay)
                    else:
                        self._start_repeating_event(event)

            # Note: We don't track event creation count in statistics anymore
            # since we get it dynamically from the chatbot_manager itself

            logger.info(f"Added event: {event.name}")
            return True

    def update_event(self, event_id: str, event: ChatEvent) -> bool:
        """Update an existing event"""
        with self._lock:
            if event_id not in self.events:
                return False

            # Validate Interval events require an interval
            if event.event_type == EventType.INTERVAL and event.interval <= 0:
                logger.error(
                    f"Cannot update Interval event '{event.name}' without an interval set"
                )
                return False

            old_event = self.events[event_id]

            # Stop old repeating task if it exists
            if (
                old_event.interval > 0
                or old_event.event_type == EventType.SPECIFIC_TIME
            ):
                self._stop_repeating_event(event_id)

            event.event_id = event_id
            self.events[event_id] = event
            self._save_data()

            # Start new repeating task if event has an interval or is a specific time event
            if event.enabled:
                if event.event_type == EventType.SPECIFIC_TIME:
                    self._start_repeating_event(event)
                elif event.interval > 0:
                    # For Interval events, use staggered startup
                    if event.event_type == EventType.INTERVAL:
                        # Calculate delay based on number of existing Interval events (excluding this one)
                        existing_interval_count = sum(
                            1
                            for e in self.events.values()
                            if e.event_type == EventType.INTERVAL
                            and e.event_id != event_id
                        )
                        initial_delay = (
                            (existing_interval_count * 2) % event.interval
                            if event.interval > 0
                            else 0
                        )
                        self._start_repeating_event(event, initial_delay=initial_delay)
                    else:
                        self._start_repeating_event(event)

            logger.info(f"Updated event: {event.name}")
            return True

    def remove_event(self, event_id: str) -> bool:
        """Remove an event"""
        with self._lock:
            if event_id not in self.events:
                return False

            event = self.events[event_id]

            # Stop repeating task if it exists
            if event.interval > 0:
                self._stop_repeating_event(event_id)

            del self.events[event_id]
            self._save_data()

            logger.info(f"Removed event: {event.name}")
            return True

    def get_event(self, event_id: str) -> Optional[ChatEvent]:
        """Get an event by ID"""
        return self.events.get(event_id)

    def get_all_events(self) -> Dict[str, ChatEvent]:
        """Get all events"""
        return self.events.copy()

    # Command Processing
    def process_chat_message(
        self, message_data: Dict[str, Any]
    ) -> Optional[tuple]:
        """Process a chat message for commands

        Args:
            message_data: Dictionary containing message information

        Returns:
            On success: (response_message, command_name, reply_targets, discord_channels).
            Quote/blocked paths may return a shorter tuple. None if no command matched.
        """
        try:
            message = message_data.get("message", "").strip()
            username = message_data.get("username", "Unknown")
            if not message:
                return None

            # Check if message starts with command prefix
            if not message.startswith("!"):
                return None

            logger.debug("[CHATBOT] Processing command from %s: %s", username, message)

            # Extract command name and arguments
            parts = message[1:].split()
            if not parts:
                return None

            command_name = parts[0].lower()
            arguments = parts[1:] if len(parts) > 1 else []
            command_message = " ".join(arguments)  # Full message after command name
            logger.debug(
                "[CHATBOT] Extracted command: '%s' with args: %s",
                command_name,
                arguments,
            )

            # Add command-specific data to message_data for variable processing
            message_data["command_message"] = command_message
            message_data["command_args"] = arguments
            message_data["command_words"] = (
                arguments  # Alias for backwards compatibility
            )

            # Handle built-in quote commands first
            if self.quotes_enabled:
                if command_name == "quote":
                    return self._handle_quote_command(arguments, message_data)
                elif command_name == "add_quote":
                    return self._handle_add_quote_command(arguments, message_data)

            # Find matching custom command
            with self._lock:
                for command in self.commands.values():
                    if (
                        command.enabled
                        and command.command_name.lower() == command_name
                        and command.command_type != CommandType.RESET
                    ):  # Reset commands are handled separately
                        # Check if command should trigger based on conditions
                        if not command.should_trigger(message_data):
                            continue

                        # Check if command can be used
                        can_use, reason = command.can_use(message_data)
                        if not can_use:
                            logger.debug(
                                "[CHATBOT] Command '%s' blocked: %s",
                                command_name,
                                reason,
                            )
                            return reason, command_name

                        # Use the command and get response
                        logger.debug(
                            "[CHATBOT] Executing command '%s' for %s",
                            command_name,
                            username,
                        )
                        response = command.use_command(message_data)
                        logger.debug(
                            "[CHATBOT] Command '%s' returned response: %s",
                            command_name,
                            response,
                        )

                        # Save data after command use
                        self._save_data()

                        logger.info(
                            f"Command triggered: {command_name} by {message_data.get('username', 'Unknown')}"
                        )
                        targets = list(
                            getattr(command, "reply_targets", None) or ["twitch"]
                        )
                        discord_channels = list(
                            getattr(command, "discord_channels", None) or []
                        )
                        return response, command_name, targets, discord_channels

            return None

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
                remaining_time = int(
                    self.quote_cooldown_seconds - (current_time - self.quote_last_used)
                )
                return (
                    f"Quote command is on cooldown. Please wait {remaining_time} seconds.",
                    "quote",
                )

            if not arguments:
                # Random quote
                quote = self.get_random_quote()
                if quote:
                    response = f"{quote.quote_number}. {quote.text}"

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
                    response = f"{quote.quote_number}. {quote.text}"

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
                    response = f"{quote.quote_number}. {quote.text}"

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
    ) -> Optional[str]:
        """Process an automatic event

        Args:
            event_type: Type of event that occurred
            event_data: Dictionary containing event information

        Returns:
            (response, reply_targets) if event triggered, None otherwise
        """
        try:
            with self._lock:
                for event in self.events.values():
                    # Skip Interval events - they only trigger via their scheduled loop
                    if event.event_type == EventType.INTERVAL:
                        continue
                    if (
                        event.enabled
                        and event.event_type == event_type
                        and event.should_trigger(event_data)
                    ):
                        # Trigger the event and get response
                        response = event.trigger_event(event_data)

                        # Save data after event trigger
                        self._save_data()

                        logger.info(
                            f"Event triggered: {event.name} for {event_type.value}"
                        )
                        targets = list(
                            getattr(event, "reply_targets", None) or ["twitch"]
                        )
                        discord_channels = list(
                            getattr(event, "discord_channels", None) or []
                        )
                        return response, targets, discord_channels

            return None

        except Exception as e:
            logger.error(f"Error processing event {event_type}: {e}", exc_info=True)
            return None

    # Utility Methods
    def toggle_item(self, item_id: str, enabled: bool) -> bool:
        """Toggle enabled state of a command or event"""
        with self._lock:
            if item_id in self.commands:
                self.commands[item_id].enabled = enabled
                if enabled and self.commands[item_id].repeating_enabled:
                    self._start_repeating_command(self.commands[item_id])
                elif not enabled:
                    self._stop_repeating_command(item_id)
                self._save_data()
                return True
            elif item_id in self.events:
                event = self.events[item_id]
                event.enabled = enabled
                if enabled:
                    if event.event_type == EventType.SPECIFIC_TIME:
                        self._start_repeating_event(event)
                    elif event.interval > 0:
                        # For Interval events, use staggered startup
                        if event.event_type == EventType.INTERVAL:
                            # Calculate delay based on number of existing Interval events (excluding this one)
                            existing_interval_count = sum(
                                1
                                for e in self.events.values()
                                if e.event_type == EventType.INTERVAL
                                and e.event_id != item_id
                                and e.enabled
                            )
                            initial_delay = (
                                (existing_interval_count * 2) % event.interval
                                if event.interval > 0
                                else 0
                            )
                            self._start_repeating_event(
                                event, initial_delay=initial_delay
                            )
                        else:
                            self._start_repeating_event(event)
                elif not enabled:
                    self._stop_repeating_event(item_id)
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

    # Repeating Commands
    def _start_repeating_command(self, command: ChatCommand):
        """Start a repeating command task using threading"""
        with self._scheduler_lock:
            # Check if task already exists
            if command.command_id in self.repeating_tasks:
                logger.info(
                    f"Command {command.command_name} already has a running task, skipping"
                )
                return

            def repeating_thread():
                try:
                    while command.enabled and command.repeating_enabled:
                        # Create mock event data for repeating commands
                        event_data = {
                            "username": "System",
                            "timestamp": time.time(),
                            "source": "repeating",
                        }

                        # Check if command should trigger and can be used
                        if command.should_trigger(event_data):
                            can_use, reason = command.can_use(event_data)
                            if can_use:
                                response = command.use_command(event_data)
                                try:
                                    from .chatbot import dispatch_chatbot_response

                                    dispatch_chatbot_response(
                                        response,
                                        getattr(command, "reply_targets", None),
                                        discord_channels=getattr(
                                            command, "discord_channels", None
                                        ),
                                    )
                                except Exception as send_err:
                                    logger.error(
                                        "Error sending repeating command: %s",
                                        send_err,
                                    )
                                logger.info(
                                    f"Repeating command {command.command_name} executed: {response}"
                                )

                        # Wait for the interval
                        interval = (
                            command.repeat_interval
                            if command.repeat_interval > 0
                            else 60
                        )
                        time.sleep(interval)

                except Exception as e:
                    logger.error(
                        f"Error in repeating thread for command {command.command_name}: {e}"
                    )

            thread = threading.Thread(
                target=repeating_thread,
                daemon=True,
                name=f"Command-{command.command_name}",
            )
            thread.start()
            self.repeating_tasks[command.command_id] = thread
            logger.info(f"Started repeating thread for command: {command.command_name}")

    def _stop_repeating_command(self, command_id: str):
        """Stop a repeating command task"""
        with self._scheduler_lock:
            if command_id in self.repeating_tasks:
                # For threading, we can't really stop a thread cleanly, but we can remove the reference
                # The thread will exit naturally when the command is disabled
                del self.repeating_tasks[command_id]
                logger.info(
                    f"Removed repeating task reference for command ID: {command_id}"
                )

    def _start_repeating_event_with_thread(
        self, event: ChatEvent, initial_delay: float = 0
    ):
        """Start a repeating event using threading instead of async tasks

        Args:
            event: The event to start repeating
            initial_delay: Optional initial delay in seconds before first trigger (for staggering)
        """
        try:
            logger.info(
                f"_start_repeating_event_with_thread called for event {event.name}"
            )
            logger.info(f"Event object type: {type(event)}")
            logger.info(
                f"Event details: ID={event.event_id}, enabled={event.enabled}, interval={event.interval}, type={event.event_type}"
            )
            logger.info(f"Event has response_text: {hasattr(event, 'response_text')}")
            if hasattr(event, "response_text"):
                response_text = getattr(event, "response_text", None)
                logger.info(
                    f"Event response_text length: {len(response_text) if response_text else 0}"
                )
                preview = (
                    response_text[:50]
                    if response_text and len(response_text) > 0
                    else "None"
                )
                logger.info(f"Event response_text preview: '{preview}...'")

            # Scheduler lock is already held by caller (start_deferred_interval_events)

        except Exception as e:
            logger.error(
                f"Exception in _start_repeating_event_with_thread setup for event {event.name}: {e}",
                exc_info=True,
            )
            return

        try:
            # Check if task already exists
            if event.event_id in self.repeating_tasks:
                logger.info(f"Event {event.name} already has a running task, skipping")
                return

            def repeating_thread():
                try:
                    logger.debug(
                        "=== THREAD STARTED: repeating thread for event %s (ID: %s) ===",
                        event.name,
                        event.event_id,
                    )
                    logger.info(
                        f"Thread function called successfully - event: {event.name}"
                    )
                    logger.info(
                        f"Event enabled: {event.enabled}, interval: {event.interval}, type: {event.event_type}"
                    )

                    # Apply initial delay for staggered startup (especially for Interval events)
                    if initial_delay > 0:
                        logger.info(
                            f"Applying {initial_delay}s initial delay for event {event.name}"
                        )
                        time.sleep(initial_delay)

                    logger.info(
                        f"Entering main loop for event {event.name} with interval {event.interval}s"
                    )

                    # Track if this is the first iteration to prevent double triggering
                    first_iteration = True

                    while event.enabled and event.interval > 0:
                        logger.debug(
                            f"Loop iteration for event {event.name} - enabled: {event.enabled}, interval: {event.interval}"
                        )

                        # Wait for the interval before triggering (skip sleep on first iteration since we already triggered)
                        if not first_iteration:
                            logger.debug(
                                f"Sleeping for {event.interval}s for event {event.name}"
                            )
                            time.sleep(event.interval)
                        else:
                            logger.debug(
                                f"Skipping initial sleep for event {event.name} (already triggered immediately)"
                            )
                            first_iteration = False

                        # Create mock event data for repeating events
                        event_data = {
                            "username": "System",
                            "timestamp": time.time(),
                            "source": "repeating_event",
                        }

                        # Check if event should trigger
                        # INTERVAL events trigger automatically on schedule - bypass condition checking
                        should_trigger = True
                        if event.event_type != EventType.INTERVAL:
                            should_trigger = event.should_trigger(event_data)

                        logger.debug(
                            f"Event {event.name} should_trigger: {should_trigger}"
                        )

                        if should_trigger:
                            logger.info(
                                f"Triggering event {event.name} in loop (first_iteration={first_iteration})"
                            )
                            response = event.trigger_event(event_data)
                            logger.info(
                                f"Event {event.name} generated response in loop: '{response}'"
                            )

                            # Send the response through the chatbot
                            try:
                                from .chatbot import (
                                    is_chatbot_connected,
                                    dispatch_chatbot_response,
                                )

                                # Check if chatbot is ready before attempting to send
                                if not is_chatbot_connected():
                                    logger.warning(
                                        f"Skipping repeating event {event.name} - chatbot not connected"
                                    )
                                    # Don't send the message, but continue the loop
                                    # The event will try again on the next interval
                                else:
                                    logger.info(
                                        f"Sending message for event {event.name} in loop"
                                    )
                                    success = dispatch_chatbot_response(
                                        response,
                                        getattr(event, "reply_targets", None),
                                        discord_channels=getattr(
                                            event, "discord_channels", None
                                        ),
                                    )
                                    if success:
                                        logger.info(
                                            f"Repeating event {event.name} sent message: {response}"
                                        )
                                    else:
                                        logger.warning(
                                            f"Failed to send repeating event message for {event.name}"
                                        )
                            except Exception as e:
                                logger.error(
                                    f"Error sending repeating event message for {event.name}: {e}"
                                )

                    logger.info(
                        f"Exiting repeating thread for event {event.name} - enabled: {event.enabled}, interval: {event.interval}"
                    )

                except Exception as e:
                    logger.error(
                        f"Error in repeating thread for event {event.name}: {e}",
                        exc_info=True,
                    )

            # Create and start the real thread directly
            logger.debug("Creating thread for event %s", event.name)
            thread = threading.Thread(
                target=repeating_thread, daemon=True, name=f"Event-{event.name}"
            )
            logger.debug("Thread created: %s", thread.name)
            thread.start()
            logger.debug(
                "Thread started: %s, is_alive: %s", thread.name, thread.is_alive()
            )

            # Store the thread reference (we'll use this for cleanup)
            self.repeating_tasks[event.event_id] = thread
            logger.debug("Stored thread reference for event: %s", event.name)
        except Exception as e:
            logger.error(
                f"Exception in _start_repeating_event_with_thread thread creation for event {event.name}: {e}",
                exc_info=True,
            )

    def _start_repeating_event(self, event: ChatEvent, initial_delay: float = 0):
        """Start a repeating event task

        Args:
            event: The event to start repeating
            initial_delay: Optional initial delay in seconds before first trigger (for staggering)
        """
        # For backwards compatibility, delegate to the threading version
        self._start_repeating_event_with_thread(event, initial_delay)

    def _start_specific_time_event(self, event: ChatEvent):
        """Start a specific time event task that checks every minute for the target time"""
        with self._scheduler_lock:
            # Check if task already exists
            if event.event_id in self.repeating_tasks:
                existing_task = self.repeating_tasks[event.event_id]
                # If task is done or cancelled, clean it up and allow restart
                if existing_task.done():
                    del self.repeating_tasks[event.event_id]
                else:
                    return  # Task is still running, skip

            async def specific_time_task():
                try:
                    from datetime import datetime

                    while event.enabled and event.event_type == EventType.SPECIFIC_TIME:
                        # Check every minute if current time matches the target time
                        current_time = datetime.now()
                        current_hour = current_time.hour
                        current_minute = current_time.minute

                        # Parse the target time (HH:MM format)
                        if event.specific_time:
                            try:
                                time_parts = event.specific_time.split(":")
                                if len(time_parts) == 2:
                                    target_hour = int(time_parts[0])
                                    target_minute = int(time_parts[1])

                                    # Check if current time matches target time
                                    if (
                                        current_hour == target_hour
                                        and current_minute == target_minute
                                    ):
                                        # Check if we already triggered today (avoid multiple triggers in same minute)
                                        last_triggered = (
                                            datetime.fromtimestamp(event.last_triggered)
                                            if event.last_triggered > 0
                                            else None
                                        )
                                        if (
                                            last_triggered is None
                                            or last_triggered.date()
                                            != current_time.date()
                                            or last_triggered.hour != current_hour
                                            or last_triggered.minute != current_minute
                                        ):
                                            # Create event data
                                            event_data = {
                                                "username": "System",
                                                "timestamp": time.time(),
                                                "source": "specific_time",
                                                "time": event.specific_time,
                                            }

                                            # Trigger the event
                                            if event.should_trigger(event_data):
                                                response = event.trigger_event(
                                                    event_data
                                                )
                                                # Send the response through the chatbot
                                                try:
                                                    from .chatbot import (
                                                        dispatch_chatbot_response,
                                                    )

                                                    success = dispatch_chatbot_response(
                                                        response,
                                                        getattr(
                                                            event, "reply_targets", None
                                                        ),
                                                        discord_channels=getattr(
                                                            event, "discord_channels", None
                                                        ),
                                                    )
                                                    if success:
                                                        logger.info(
                                                            f"Specific time event {event.name} triggered at {event.specific_time}, sent message: {response}"
                                                        )
                                                    else:
                                                        logger.warning(
                                                            f"Failed to send specific time event message for {event.name}"
                                                        )
                                                except Exception as e:
                                                    logger.error(
                                                        f"Error sending specific time event message for {event.name}: {e}"
                                                    )
                            except (ValueError, IndexError) as e:
                                logger.error(
                                    f"Invalid time format for event {event.name}: {event.specific_time} - {e}"
                                )

                        # Sleep for 60 seconds (check every minute)
                        await asyncio.sleep(60)

                except Exception as e:
                    logger.error(
                        f"Error in specific time task for event {event.name}: {e}"
                    )

            task = asyncio.create_task(specific_time_task())
            self.repeating_tasks[event.event_id] = task
            logger.info(
                f"Started specific time task for event: {event.name} at {event.specific_time}"
            )

    def _stop_repeating_event(self, event_id: str):
        """Stop a repeating event task"""
        with self._scheduler_lock:
            if event_id in self.repeating_tasks:
                # For threading, we can't really stop a thread cleanly, but we can remove the reference
                # The thread will exit naturally when the event is disabled
                del self.repeating_tasks[event_id]
                logger.info(
                    f"Removed repeating task reference for event ID: {event_id}"
                )

    def start_deferred_interval_events(self):
        """Start any interval events that were deferred due to chatbot not being ready"""
        with self._scheduler_lock:
            logger.info(
                f"start_deferred_interval_events called - deferred events: {len(self._deferred_interval_events)}"
            )

            if not self._deferred_interval_events:
                logger.debug("No deferred interval events to start")
                return

            from .chatbot import is_chatbot_connected

            chatbot_connected = is_chatbot_connected()
            logger.info(f"Chatbot connected status: {chatbot_connected}")

            if not chatbot_connected:
                logger.warning(
                    f"Chatbot not ready (is_chatbot_connected returned False), cannot start {len(self._deferred_interval_events)} deferred interval events"
                )
                # Log detailed connection status for debugging
                try:
                    from .chatbot import get_chatbot_connection_status

                    status = get_chatbot_connection_status()
                    logger.info(f"Detailed chatbot status: {status}")
                except Exception as e:
                    logger.error(f"Error getting detailed chatbot status: {e}")
                return

            logger.info(
                f"Starting {len(self._deferred_interval_events)} deferred interval events"
            )

            started_count = 0
            for index, event in enumerate(self._deferred_interval_events):
                try:
                    # Calculate staggered delay: spread events across their interval
                    # Use modulo to ensure delay is within the interval range
                    initial_delay = (
                        (index * 2) % event.interval if event.interval > 0 else 0
                    )
                    logger.info(
                        f"About to start deferred Interval event {event.name} (ID: {event.event_id}) with {initial_delay}s initial delay, interval: {event.interval}s"
                    )

                    # Instead of creating async tasks that may not persist, create threading-based timers
                    # This ensures the events run independently of the event loop lifecycle
                    logger.info(
                        f"Calling _start_repeating_event_with_thread for event {event.name}"
                    )
                    self._start_repeating_event_with_thread(event, initial_delay)
                    logger.info(
                        f"Returned from _start_repeating_event_with_thread for event {event.name}"
                    )
                    logger.info(
                        f"Successfully started deferred Interval event {event.name} (ID: {event.event_id}) with threading approach"
                    )
                    started_count += 1

                except Exception as e:
                    logger.error(
                        f"Error starting deferred repeating task for Interval event {event.name}: {e}",
                        exc_info=True,
                    )

            # Clear the deferred list
            self._deferred_interval_events.clear()
            logger.info(
                f"All deferred interval events have been processed - started {started_count} events"
            )

    # Async wrappers for testing
    async def test_command(
        self, command_id: str, test_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Test a command with sample data and send message to Twitch chat"""
        try:
            command = self.commands.get(command_id)
            if not command:
                return {"success": False, "error": "Command not found"}

            if not command.should_trigger(test_data):
                return {
                    "success": True,
                    "triggered": False,
                    "message": "Command conditions not met",
                }

            can_use, reason = command.can_use(test_data)
            if not can_use:
                return {"success": True, "triggered": False, "message": reason}

            response = command.use_command(test_data)

            # Send the response to configured reply targets
            try:
                from .chatbot import dispatch_chatbot_response

                success = dispatch_chatbot_response(
                    response,
                    getattr(command, "reply_targets", None),
                    discord_channels=getattr(command, "discord_channels", None),
                )
                if success:
                    logger.info(
                        f"Test command '{command.command_name}' sent message: {response}"
                    )
                else:
                    logger.warning(
                        f"Failed to send test command message for '{command.command_name}'"
                    )
                    return {
                        "success": False,
                        "error": "Failed to send message to chat targets",
                    }
            except Exception as send_error:
                logger.error(
                    f"Error sending test command message: {send_error}", exc_info=True
                )
                return {
                    "success": False,
                    "error": f"Error sending message: {str(send_error)}",
                }

            return {
                "success": True,
                "triggered": True,
                "message": response,
                "command_name": command.command_name,
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def test_event(
        self, event_id: str, test_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Test an event with sample data and send message to Twitch chat"""
        try:
            event = self.events.get(event_id)
            if not event:
                return {"success": False, "error": "Event not found"}

            if not event.should_trigger(test_data):
                return {
                    "success": True,
                    "triggered": False,
                    "message": "Event conditions not met",
                }

            response = event.trigger_event(test_data)

            # Send the response to configured reply targets
            try:
                from .chatbot import dispatch_chatbot_response

                success = dispatch_chatbot_response(
                    response,
                    getattr(event, "reply_targets", None),
                    discord_channels=getattr(event, "discord_channels", None),
                )
                if success:
                    logger.info(f"Test event '{event.name}' sent message: {response}")
                else:
                    logger.warning(
                        f"Failed to send test event message for '{event.name}'"
                    )
                    return {
                        "success": False,
                        "error": "Failed to send message to chat targets",
                    }
            except Exception as send_error:
                logger.error(
                    f"Error sending test event message: {send_error}", exc_info=True
                )
                return {
                    "success": False,
                    "error": f"Error sending message: {str(send_error)}",
                }

            return {
                "success": True,
                "triggered": True,
                "message": response,
                "event_name": event.name,
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

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

    def update_quote(
        self, quote_id: str, text: str = None, author: str = None
    ) -> bool:
        """Update an existing quote's text and/or author."""
        with self._lock:
            if quote_id not in self.quotes:
                return False

            quote = self.quotes[quote_id]

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

            if greeting and self.custom_greeting_enabled:
                # Update username if it has changed
                greeting.update_username(username)

                # Check if user should be greeted based on custom greeting flag and reset interval
                if not greeting.greeted_flag:
                    # Mark as greeted and save
                    greeting.greeted_flag = True
                    greeting.update_last_greeted(current_time)
                    self._save_data()

                    # Process variables in custom greeting text
                    greeting_text = greeting.greeting_text.replace(
                        "{username}", username
                    )
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

                            # Process variables in custom greeting text
                            greeting_text = greeting.greeting_text.replace(
                                "{username}", username
                            )
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

                    # Process variables in default greeting text
                    greeting_text = self.default_greeting_text.replace(
                        "{username}", username
                    )
                    logger.info(f"Sending default greeting to {username}")
                    return greeting_text

                return None  # User was already greeted with default greeting
        return None

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
            # For threading, we just clear the references - threads will exit when events are disabled
            self.repeating_tasks.clear()

        # Force save any pending flag changes
        if hasattr(self, "flag_manager"):
            self.flag_manager.force_save()

        logger.info("Chatbot manager cleanup completed")


# Global instance
_chatbot_manager = None
_manager_lock = threading.Lock()


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
