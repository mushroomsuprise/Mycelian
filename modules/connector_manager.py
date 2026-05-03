#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024 Mycelian

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
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from . import database_manager
from .connector_actions import create_action
from .connector_core import ActionType, Connector, EventData, TriggerType
from .connector_triggers import create_trigger
from .hotkey_listener import get_listener
from .statistics_manager import get_statistics_manager
from .notification_engine import nav_actions_main_tab, notify_critical

logger = logging.getLogger(__name__)


class ConnectorManager:
    """Manages connectors and processes events through the trigger-action system"""

    def __init__(self):
        self.connectors: Dict[str, Connector] = {}
        self.event_queue = asyncio.Queue()
        self.processing_task = None
        self.connector_thread = None
        self.is_running = False
        self._lock = threading.RLock()
        self.hotkey_listener = get_listener()

        # Statistics
        self.stats = {
            "events_processed": 0,
            "triggers_fired": 0,
            "actions_executed": 0,
            "errors": 0,
            "started_at": time.time(),
        }

    def start(self):
        """Start the connector manager"""
        if self.is_running:
            logger.warning("Connector manager is already running")
            return

        self.is_running = True
        logger.info("Starting connector manager")

        # Load connectors from database
        self.load_connectors()

        # Start event processing task only if we have a running event loop
        try:
            loop = asyncio.get_running_loop()
            self.processing_task = asyncio.create_task(self._process_events())
            logger.info("Started async event processing task")
        except RuntimeError:
            # No event loop running, defer task creation
            logger.info("No event loop running, will start processing task later")
            self.processing_task = None

    def start_connector_thread(self):
        """Start connector processing in a separate thread with its own event loop"""
        import threading

        def run_connector_loop():
            """Run the connector event processing in a separate event loop"""
            try:
                # Create a new event loop for this thread
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                # Start the processing task
                self.processing_task = loop.create_task(self._process_events())

                # Run the event loop
                loop.run_forever()

            except Exception as e:
                logger.error(f"Error in connector thread: {e}", exc_info=True)
                notify_critical(
                    "Connector background processing crashed. Connectors may not run until you restart the app.",
                    dedupe_key="connector:thread_crash",
                    actions=nav_actions_main_tab("Connectors"),
                )

        # Start the connector processing in a background thread
        self.connector_thread = threading.Thread(
            target=run_connector_loop, daemon=True, name="ConnectorProcessor"
        )
        self.connector_thread.start()
        logger.info("Started connector processing thread")

    def ensure_processing_task(self):
        """Ensure the processing task is running if we have an event loop"""
        if self.is_running and self.processing_task is None:
            try:
                loop = asyncio.get_running_loop()
                self.processing_task = asyncio.create_task(self._process_events())
                logger.info("Started deferred async event processing task")
            except RuntimeError:
                # Try to get or create an event loop
                try:
                    try:
                        loop = asyncio.get_event_loop()
                    except RuntimeError:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)

                    self.processing_task = loop.create_task(self._process_events())

                    # Try to run the event loop if it's not running
                    if not loop.is_running():
                        try:
                            loop.run_until_complete(asyncio.sleep(0))
                        except Exception:
                            pass

                    logger.info(
                        "Started deferred async event processing task (fallback)"
                    )
                except Exception:
                    pass

    async def process_event(self, event_data: Dict[str, Any]) -> bool:
        """Process an event through all enabled connectors"""
        if not self.is_running:
            return False

        # Ensure processing task is running
        self.ensure_processing_task()

        # Add event to queue for processing
        await self.event_queue.put(event_data)
        return True

    async def stop(self):
        """Stop the connector manager"""
        if not self.is_running:
            return

        self.is_running = False
        logger.info("Stopping connector manager")

        if self.processing_task:
            self.processing_task.cancel()
            try:
                await self.processing_task
            except asyncio.CancelledError:
                pass

        # Cancel any active audio restoration tasks and clean up registry
        try:
            from .connector_actions import AudioControlAction

            AudioControlAction.cancel_all_restoration_tasks()
            AudioControlAction.cleanup_audio_change_registry()
        except Exception as e:
            logger.error(f"Error cancelling audio restoration tasks: {e}")

    async def add_event(self, event_data: Dict[str, Any]):
        """Add an event to the processing queue"""
        if not self.is_running:
            logger.warning("Connector manager not running, dropping event")
            return

        self.ensure_processing_task()
        await self.event_queue.put(event_data)
        logger.debug(f"Added event to queue: {event_data.get('event_type', 'unknown')}")

    async def _process_events(self):
        """Process events from the queue"""
        logger.info("Event processing started")

        while self.is_running:
            try:
                # Wait for an event with timeout
                try:
                    event_data = await asyncio.wait_for(
                        self.event_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                # Process the event
                await self._process_single_event(event_data)
                self.stats["events_processed"] += 1

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing event: {e}", exc_info=True)
                self.stats["errors"] += 1

        logger.info("Event processing stopped")

    async def _process_single_event(self, event_data: Dict[str, Any]):
        """Process a single event through all connectors"""
        event_type = event_data.get("event_type", "unknown")
        logger.debug(f"Processing event: {event_type}")

        triggered_connectors = 0
        triggered_connector_names = []

        # Process through all enabled connectors
        for connector_id, connector in self.connectors.items():
            # Check if connector is enabled
            if not connector.enabled:
                continue

            try:
                triggered = await connector.process_event(event_data)
                if triggered:
                    self.stats["triggers_fired"] += 1
                    triggered_connectors += 1
                    triggered_connector_names.append(connector.name)
                    # Note: action execution count is tracked in the connector

            except Exception as e:
                logger.error(
                    f"Error processing event in connector '{connector.name}': {e}",
                    exc_info=True,
                )
                self.stats["errors"] += 1

        # Track statistics for triggered connectors
        if triggered_connectors > 0:
            try:
                stats_manager = get_statistics_manager()
                username = event_data.get("username")

                # Track each individual connector that was triggered
                for connector_name in triggered_connector_names:
                    stats_manager.increment_connector_triggers(
                        1,  # One trigger per connector
                        username=username,
                        connector_name=connector_name,
                    )

                logger.debug(
                    f"Tracked {triggered_connectors} connector trigger(s) for event: {event_type}"
                )
            except Exception as e:
                logger.error(f"Error tracking connector trigger statistics: {e}")

    def load_connectors(self):
        """Load connectors from database"""
        try:
            with self._lock:
                # Get connectors from database
                connectors_data = database_manager.get_data("connectors", {})

                self.connectors.clear()

                for connector_id, connector_data in connectors_data.items():
                    try:
                        connector = self._deserialize_connector(connector_data)
                        if connector:
                            self.connectors[connector_id] = connector

                            # Register hotkey if it's a hotkey trigger and enabled
                            if connector.enabled:
                                self._register_connector_hotkey(connector)

                            logger.debug(f"Loaded connector: {connector.name}")
                    except Exception as e:
                        logger.error(
                            f"Error loading connector {connector_id}: {e}",
                            exc_info=True,
                        )

                logger.info(
                    f"🔌 Loaded {len(self.connectors)} connectors from database"
                )
                # Log connector names for debugging
                if self.connectors:
                    connector_names = list(self.connectors.keys())
                    logger.debug(f" Connector names: {connector_names}")

        except Exception as e:
            logger.error(f"Error loading connectors: {e}", exc_info=True)
            notify_critical(
                "Could not load connectors from the database.",
                dedupe_key="connector:load_failed",
                actions=nav_actions_main_tab("Connectors"),
            )

    def save_connectors(self):
        """Save connectors to database"""
        try:
            with self._lock:
                # Serialize all connectors
                connectors_data = {}
                for connector_id, connector in self.connectors.items():
                    try:
                        connectors_data[connector_id] = self._serialize_connector(
                            connector
                        )
                    except Exception as e:
                        logger.error(
                            f"Error serializing connector {connector_id}: {e}",
                            exc_info=True,
                        )

                # Save to database
                database_manager.set_data("connectors", connectors_data)
                logger.info(f"Saved {len(connectors_data)} connectors")

        except Exception as e:
            logger.error(f"Error saving connectors: {e}", exc_info=True)
            notify_critical(
                "Could not save connectors to the database.",
                dedupe_key="connector:save_failed",
                actions=nav_actions_main_tab("Connectors"),
            )

    def add_connector(self, connector: Connector) -> bool:
        """Add a new connector"""
        try:
            with self._lock:
                if connector.connector_id in self.connectors:
                    logger.warning(f"Connector {connector.connector_id} already exists")
                    return False

                # Validate connector
                if not self._validate_connector(connector):
                    return False

                self.connectors[connector.connector_id] = connector

                # Register hotkey if it's a hotkey trigger and enabled
                if connector.enabled:
                    self._register_connector_hotkey(connector)

                logger.info(f"Added connector: {connector.name}")

                # Note: We don't track connector creation count in statistics anymore
                # since we get it dynamically from the connector_manager itself

                # Save to database
                self.save_connectors()
                return True

        except Exception as e:
            logger.error(f"Error adding connector: {e}", exc_info=True)
            return False

    def update_connector(self, connector_id: str, connector: Connector) -> bool:
        """Update an existing connector"""
        try:
            with self._lock:
                if connector_id not in self.connectors:
                    logger.warning(f"Connector {connector_id} does not exist")
                    return False

                # Validate connector
                if not self._validate_connector(connector):
                    return False

                self.connectors[connector_id] = connector
                logger.info(f"Updated connector: {connector.name}")

                # Save to database
                self.save_connectors()
                return True

        except Exception as e:
            logger.error(f"Error updating connector: {e}", exc_info=True)
            return False

    def remove_connector(self, connector_id: str) -> bool:
        """Remove a connector"""
        try:
            with self._lock:
                if connector_id not in self.connectors:
                    logger.warning(f"Connector {connector_id} does not exist")
                    return False

                connector = self.connectors.pop(connector_id)

                # Unregister hotkey if it's a hotkey trigger
                self._unregister_connector_hotkey(connector)

                logger.info(f"Removed connector: {connector.name}")

                # Save to database
                self.save_connectors()
                return True

        except Exception as e:
            logger.error(f"Error removing connector: {e}", exc_info=True)
            return False

    def get_connector(self, connector_id: str) -> Optional[Connector]:
        """Get a connector by ID"""
        with self._lock:
            return self.connectors.get(connector_id)

    def get_all_connectors(self) -> Dict[str, Connector]:
        """Get all connectors"""
        with self._lock:
            return self.connectors.copy()

    def get_connectors_by_trigger_type(
        self, trigger_type: TriggerType
    ) -> List[Connector]:
        """Get connectors with a specific trigger type"""
        with self._lock:
            return [
                connector
                for connector in self.connectors.values()
                if connector.trigger and connector.trigger.trigger_type == trigger_type
            ]

    def toggle_connector(self, connector_id: str) -> bool:
        """Toggle a connector's enabled state"""
        try:
            with self._lock:
                if connector_id not in self.connectors:
                    return False

                connector = self.connectors[connector_id]
                connector.enabled = not connector.enabled

                # Register/unregister hotkey if it's a hotkey trigger
                if (
                    connector.trigger
                    and connector.trigger.trigger_type == TriggerType.HOTKEY
                ):
                    if connector.enabled:
                        self._register_connector_hotkey(connector)
                    else:
                        self._unregister_connector_hotkey(connector)

                logger.info(
                    f"Connector '{connector.name}' {'enabled' if connector.enabled else 'disabled'}"
                )

                # Save to database
                self.save_connectors()
                return True

        except Exception as e:
            logger.error(f"Error toggling connector: {e}", exc_info=True)
            return False

    def get_statistics(self) -> Dict[str, Any]:
        """Get connector manager statistics"""
        uptime = time.time() - self.stats["started_at"]
        return {
            **self.stats,
            "uptime_seconds": uptime,
            "connector_count": len(self.connectors),
            "enabled_connectors": sum(1 for c in self.connectors.values() if c.enabled),
        }

    def _validate_connector(self, connector: Connector) -> bool:
        """Validate a connector configuration"""
        if not connector.trigger:
            logger.error("Connector missing trigger")
            return False

        if not connector.actions:
            logger.error("Connector missing actions")
            return False

        # Validate trigger
        if not hasattr(connector.trigger, "should_trigger"):
            logger.error("Invalid trigger type")
            return False

        # Validate actions
        for action in connector.actions:
            if not hasattr(action, "execute"):
                logger.error("Invalid action type")
                return False

            if not action.validate_parameters():
                logger.error(f"Action '{action.name}' has invalid parameters")
                return False

        return True

    def _serialize_connector(self, connector: Connector) -> Dict[str, Any]:
        """Serialize a connector to dictionary"""
        return {
            "connector_id": connector.connector_id,
            "name": connector.name,
            "description": connector.description,
            "enabled": connector.enabled,
            "created_at": connector.created_at,
            "last_triggered": connector.last_triggered,
            "trigger_count": connector.trigger_count,
            "metadata": connector.metadata,
            "trigger": self._serialize_trigger(connector.trigger)
            if connector.trigger
            else None,
            "actions": [self._serialize_action(action) for action in connector.actions],
        }

    def _deserialize_connector(self, data: Dict[str, Any]) -> Optional[Connector]:
        """Deserialize a connector from dictionary"""
        try:
            # Deserialize trigger
            trigger = None
            if data.get("trigger"):
                trigger = self._deserialize_trigger(data["trigger"])

            # Deserialize actions
            actions = []
            for action_data in data.get("actions", []):
                action = self._deserialize_action(action_data)
                if action:
                    actions.append(action)

            return Connector(
                connector_id=data["connector_id"],
                name=data["name"],
                description=data.get("description", ""),
                enabled=data.get("enabled", True),
                trigger=trigger,
                actions=actions,
                created_at=data.get("created_at", time.time()),
                last_triggered=data.get("last_triggered", 0),
                trigger_count=data.get("trigger_count", 0),
                metadata=data.get("metadata", {}),
            )
        except Exception as e:
            logger.error(f"Error deserializing connector: {e}", exc_info=True)
            return None

    def _serialize_trigger(self, trigger) -> Dict[str, Any]:
        """Serialize a trigger to dictionary"""
        return {
            "trigger_id": trigger.trigger_id,
            "trigger_type": trigger.trigger_type.value,
            "name": trigger.name,
            "description": trigger.description,
            "enabled": trigger.enabled,
            "conditions": [
                {
                    "field": cond.field,
                    "operator": cond.operator.value,
                    "value": cond.value,
                    "case_sensitive": cond.case_sensitive,
                }
                for cond in trigger.conditions
            ],
            "cooldown_seconds": trigger.cooldown_seconds,
            "last_triggered": trigger.last_triggered,
            "metadata": trigger.metadata,
            # Add trigger-specific fields
            **{
                key: value
                for key, value in trigger.__dict__.items()
                if key
                not in [
                    "trigger_id",
                    "trigger_type",
                    "name",
                    "description",
                    "enabled",
                    "conditions",
                    "cooldown_seconds",
                    "last_triggered",
                    "metadata",
                ]
            },
        }

    def _deserialize_trigger(self, data: Dict[str, Any]):
        """Deserialize a trigger from dictionary"""
        try:
            from .connector_core import ComparisonOperator, TriggerCondition

            trigger_type = TriggerType(data["trigger_type"])

            # Deserialize conditions
            conditions = []
            for cond_data in data.get("conditions", []):
                condition = TriggerCondition(
                    field=cond_data["field"],
                    operator=ComparisonOperator(cond_data["operator"]),
                    value=cond_data["value"],
                    case_sensitive=cond_data.get("case_sensitive", True),
                )
                conditions.append(condition)

            # Extract trigger-specific parameters
            trigger_params = {
                key: value
                for key, value in data.items()
                if key
                not in [
                    "trigger_id",
                    "trigger_type",
                    "name",
                    "description",
                    "enabled",
                    "conditions",
                    "cooldown_seconds",
                    "last_triggered",
                    "metadata",
                ]
            }

            # Create trigger
            trigger = create_trigger(
                trigger_type=trigger_type,
                trigger_id=data["trigger_id"],
                name=data["name"],
                description=data.get("description", ""),
                enabled=data.get("enabled", True),
                conditions=conditions,
                cooldown_seconds=data.get("cooldown_seconds", 0),
                metadata=data.get("metadata", {}),
                **trigger_params,
            )

            trigger.last_triggered = data.get("last_triggered", 0)
            return trigger

        except Exception as e:
            logger.error(f"Error deserializing trigger: {e}", exc_info=True)
            return None

    def _serialize_action(self, action) -> Dict[str, Any]:
        """Serialize an action to dictionary"""
        return {
            "action_id": action.action_id,
            "action_type": action.action_type.value,
            "name": action.name,
            "description": action.description,
            "enabled": action.enabled,
            "parameters": action.parameters,
            "metadata": action.metadata,
            # Add action-specific fields
            **{
                key: value
                for key, value in action.__dict__.items()
                if key
                not in [
                    "action_id",
                    "action_type",
                    "name",
                    "description",
                    "enabled",
                    "parameters",
                    "metadata",
                ]
            },
        }

    def _deserialize_action(self, data: Dict[str, Any]):
        """Deserialize an action from dictionary"""
        try:
            action_type = ActionType(data["action_type"])

            # Extract action-specific parameters
            action_params = {
                key: value
                for key, value in data.items()
                if key
                not in [
                    "action_id",
                    "action_type",
                    "name",
                    "description",
                    "enabled",
                    "parameters",
                    "metadata",
                ]
            }

            # Create action
            action = create_action(
                action_type=action_type,
                action_id=data["action_id"],
                name=data["name"],
                description=data.get("description", ""),
                enabled=data.get("enabled", True),
                parameters=data.get("parameters", {}),
                metadata=data.get("metadata", {}),
                **action_params,
            )

            return action

        except Exception as e:
            logger.error(f"Error deserializing action: {e}", exc_info=True)
            return None

    def _register_connector_hotkey(self, connector: Connector):
        """Register hotkey for a connector"""
        try:
            if (
                not connector.trigger
                or connector.trigger.trigger_type != TriggerType.HOTKEY
            ):
                return

            key_combination = getattr(connector.trigger, "key_combination", "")
            if not key_combination:
                return

            is_global = getattr(connector.trigger, "is_global", True)
            self.hotkey_listener.register_hotkey(
                connector.connector_id, key_combination, is_global
            )

        except Exception as e:
            logger.error(
                f"Error registering hotkey for connector {connector.connector_id}: {e}",
                exc_info=True,
            )

    def _unregister_connector_hotkey(self, connector: Connector):
        """Unregister hotkey for a connector"""
        try:
            if (
                not connector.trigger
                or connector.trigger.trigger_type != TriggerType.HOTKEY
            ):
                return

            self.hotkey_listener.unregister_hotkey(connector.connector_id)

        except Exception as e:
            logger.error(
                f"Error unregistering hotkey for connector {connector.connector_id}: {e}",
                exc_info=True,
            )

    async def test_connector(
        self, connector_id: str, test_event_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Test a connector with provided event data"""
        connector = self.get_connector(connector_id)
        if not connector:
            return {"success": False, "error": "Connector not found"}

        try:
            # Process test event
            result = await connector.process_event(test_event_data)

            return {
                "success": True,
                "triggered": result,
                "connector_name": connector.name,
                "trigger_type": connector.trigger.trigger_type.value
                if connector.trigger
                else None,
                "action_count": len(connector.actions),
            }

        except Exception as e:
            logger.error(f"Error testing connector: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


# Global connector manager instance
connector_manager = ConnectorManager()


def initialize():
    """Initialize the connector system"""
    global connector_manager

    try:
        logger.info("Initializing connector system")

        # Initialize hotkey listener
        from .hotkey_listener import initialize as init_hotkey_listener

        init_hotkey_listener()

        connector_manager.start()
        logger.info("Connector system initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing connector system: {e}", exc_info=True)
        notify_critical(
            "Connector system failed to initialize (hotkeys/connectors may be unavailable).",
            dedupe_key="connector:init_failed",
            actions=nav_actions_main_tab("Connectors"),
        )


async def cleanup():
    """Cleanup the connector system"""
    global connector_manager

    try:
        logger.info("Cleaning up connector system")
        await connector_manager.stop()

        # Cleanup hotkey listener
        from .hotkey_listener import cleanup as cleanup_hotkey_listener

        cleanup_hotkey_listener()

        logger.info("Connector system cleaned up successfully")
    except Exception as e:
        logger.error(f"Error cleaning up connector system: {e}", exc_info=True)


def get_manager() -> ConnectorManager:
    """Get the global connector manager instance"""
    return connector_manager
