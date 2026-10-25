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
import hmac
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .connector_core import EventData
from .connector_manager import get_manager
from .notification_engine import nav_actions_main_tab, nav_actions_settings, notify, notify_critical

logger = logging.getLogger(__name__)


class ConnectorIntegration:
    """Integration layer between the connector system and existing Mycelian modules"""

    def __init__(self):
        self.manager = get_manager()
        self._twitch_integration_setup = False
        self._twitch_retry_thread = None

    def setup_twitch_integration(self):
        """Set up integration with Twitch events"""
        if self._twitch_integration_setup:
            return

        try:
            # Import here to avoid circular imports
            from . import twitch

            # Store original event handlers
            self._original_twitch_handlers = {}

            # Wrap existing event handlers to also send to connector system
            if hasattr(twitch, "twitch_api") and twitch.twitch_api:
                self._wrap_twitch_handler(
                    twitch.twitch_api, "on_bits_use", self._handle_twitch_bits
                )
                # Subs and resubs are not wrapped: twitch.py emits twitch_sub only
                # for a confirmed new sub and twitch_resub only for a resub it
                # alerted, after its own de-duplication (see
                # twitch._emit_connector_new_sub / _emit_connector_resub).
                self._wrap_twitch_handler(
                    twitch.twitch_api, "on_sub_gift", self._handle_twitch_giftsub
                )
                self._wrap_twitch_handler(
                    twitch.twitch_api, "on_follow", self._handle_twitch_follow
                )
                self._wrap_twitch_handler(
                    twitch.twitch_api, "on_raid", self._handle_twitch_raid
                )
                self._wrap_twitch_handler(
                    twitch.twitch_api, "on_points", self._handle_twitch_points
                )
                self._wrap_twitch_handler(
                    twitch.twitch_api, "on_chat_message", self._handle_twitch_chat
                )
                self._wrap_twitch_handler(
                    twitch.twitch_api,
                    "on_hype_train_start",
                    self._handle_twitch_hype_train_start,
                )
                self._wrap_twitch_handler(
                    twitch.twitch_api,
                    "on_hype_train_end",
                    self._handle_twitch_hype_train_end,
                )
                self._wrap_twitch_handler(
                    twitch.twitch_api,
                    "on_stream_online",
                    self._handle_twitch_stream_online,
                )
                self._wrap_twitch_handler(
                    twitch.twitch_api,
                    "on_stream_offline",
                    self._handle_twitch_stream_offline,
                )

                logger.info("Twitch integration set up for connector system")
                self._twitch_integration_setup = True
            else:
                logger.warning(
                    "Twitch API not available for connector integration; "
                    "will retry when it becomes ready"
                )
                self._schedule_twitch_integration_retry()

        except Exception as e:
            logger.error(f"Error setting up Twitch integration: {e}", exc_info=True)

    def _schedule_twitch_integration_retry(self) -> None:
        """Retry Twitch handler wiring once the Twitch API finishes initializing.

        Connector deferred init can run before Twitch finishes connecting; without
        this, Twitch events would never trigger connectors until an app restart.
        """
        if self._twitch_integration_setup:
            return
        if self._twitch_retry_thread is not None and self._twitch_retry_thread.is_alive():
            return

        def _retry_loop() -> None:
            from .shutdown import is_shutdown_in_progress

            # Poll for up to ~5 minutes for the Twitch API to come up.
            deadline = time.time() + 300.0
            while time.time() < deadline:
                if is_shutdown_in_progress() or self._twitch_integration_setup:
                    return
                time.sleep(5.0)
                try:
                    from . import twitch

                    if getattr(twitch, "twitch_api", None):
                        logger.info(
                            "Twitch API now ready; wiring connector integration"
                        )
                        self.setup_twitch_integration()
                        return
                except Exception as e:
                    logger.debug("Twitch integration retry check failed: %s", e)
            if not self._twitch_integration_setup:
                logger.warning(
                    "Twitch API never became ready; connector integration not wired"
                )
                if _has_enabled_twitch_connectors():
                    notify(
                        "Connectors with Twitch triggers (follows, subs, bits…) won't run this session.",
                        type="warning",
                        source="connectors",
                        title="Twitch connectors aren't active",
                        hint="Connect Twitch in Settings → Twitch, then restart Mycelian.",
                        actions=nav_actions_settings("Twitch"),
                        dedupe_key="connector:twitch_unwired",
                        dedupe_cooldown_sec=12 * 3600,
                    )

        self._twitch_retry_thread = threading.Thread(
            target=_retry_loop,
            name="ConnectorTwitchIntegrationRetry",
            daemon=True,
        )
        self._twitch_retry_thread.start()

    def _wrap_twitch_handler(self, api_instance, handler_name: str, connector_handler):
        """Wrap a Twitch API handler to also send events to connector system"""
        try:
            original_handler = getattr(api_instance, handler_name, None)
            if not original_handler:
                logger.warning(f"Twitch handler {handler_name} not found")
                return

            # Store original handler
            self._original_twitch_handlers[handler_name] = original_handler

            # Create wrapped handler
            async def wrapped_handler(*args, **kwargs):
                # Call original handler
                result = await original_handler(*args, **kwargs)
                # Send to connector system
                try:
                    # Chatbot replies from this event may still be queued on the
                    # chatbot lane; connectors run after them, as before (TH-07).
                    from .twitch import wait_for_chatbot_lane

                    await wait_for_chatbot_lane()
                except Exception as e:
                    logger.debug("Chatbot lane wait failed: %s", e)
                try:
                    await connector_handler(*args, **kwargs)
                except Exception as e:
                    logger.error(
                        f"Error in connector handler for {handler_name}: {e}",
                        exc_info=True,
                    )
                return result

            # Replace handler
            setattr(api_instance, handler_name, wrapped_handler)
            logger.debug(f"Wrapped Twitch handler: {handler_name}")

        except Exception as e:
            logger.error(
                f"Error wrapping Twitch handler {handler_name}: {e}", exc_info=True
            )

    async def _handle_twitch_bits(self, data):
        """Handle Twitch bits event for connector system"""
        try:
            event = data.event
            is_anonymous = bool(getattr(event, "is_anonymous", False))
            event_data = EventData.from_twitch_bits(
                bits_amount=int(str(event.bits)),
                username=(event.user_name if not is_anonymous else None)
                or "Anonymous",
                message=_message_text(getattr(event, "message", None)),
                user_id=getattr(event, "user_id", None) or "",
                is_anonymous=is_anonymous,
                bits_type=str(getattr(event, "type", "") or ""),
            )
            await self.manager.add_event(event_data)
        except Exception as e:
            logger.error(
                f"Error handling Twitch bits for connector: {e}", exc_info=True
            )

    async def _handle_twitch_giftsub(self, data):
        """Handle Twitch gift subscription event for connector system.

        ``username`` is the gifter ("Anonymous" for anonymous gifts). EventSub's
        channel.subscription.gift has no recipient, so none is reported.
        """
        try:
            event = data.event
            is_anonymous = bool(getattr(event, "is_anonymous", False))
            event_data = EventData.from_twitch_giftsub(
                tier=getattr(event, "tier", None),
                username=(event.user_name if not is_anonymous else None)
                or "Anonymous",
                gift_count=int(str(getattr(event, "total", 1) or 1)),
                is_anonymous=is_anonymous,
                cumulative_total=getattr(event, "cumulative_total", None) or 0,
                user_id=(getattr(event, "user_id", None) if not is_anonymous else "")
                or "",
            )
            await self.manager.add_event(event_data)
        except Exception as e:
            logger.error(
                f"Error handling Twitch gift sub for connector: {e}", exc_info=True
            )

    async def _handle_twitch_follow(self, data):
        """Handle Twitch follow event for connector system"""
        try:
            event_data = EventData.from_twitch_follow(
                username=data.event.user_name,
                user_id=data.event.user_id,
                followed_at=data.event.followed_at,
            )
            await self.manager.add_event(event_data)
        except Exception as e:
            logger.error(
                f"Error handling Twitch follow for connector: {e}", exc_info=True
            )

    async def _handle_twitch_raid(self, data):
        """Handle Twitch raid event for connector system"""
        try:
            event_data = EventData.from_twitch_raid(
                username=data.event.from_broadcaster_user_name,
                viewer_count=data.event.viewers,
                from_user_id=data.event.from_broadcaster_user_id,
                to_user_id=data.event.to_broadcaster_user_id,
            )
            await self.manager.add_event(event_data)
        except Exception as e:
            logger.error(
                f"Error handling Twitch raid for connector: {e}", exc_info=True
            )

    async def _handle_twitch_points(self, data):
        """Handle Twitch channel points event for connector system"""
        try:
            event_data = {
                "event_type": "twitch_points",
                "username": data.event.user_name,
                "reward_id": data.event.reward.id,
                "reward_title": data.event.reward.title,
                "reward_name": data.event.reward.title,  # Add both fields for compatibility
                "reward_cost": data.event.reward.cost,
                "user_input": getattr(data.event, "user_input", ""),
                "user_id": data.event.user_id,
                "timestamp": data.event.redeemed_at,
                "source": "twitch",
            }
            await self.manager.add_event(event_data)
        except Exception as e:
            logger.error(
                f"Error handling Twitch points for connector: {e}", exc_info=True
            )

    async def _handle_twitch_chat(self, data):
        """Handle Twitch chat message event for connector system.

        Messages the app itself sent (the dedicated chatbot account, or an echo of
        a message it just sent from the broadcaster account) are skipped so a
        chat connector cannot trigger itself.
        """
        try:
            message = _message_text(data.event.message)
            is_command = message.startswith("!")
            command = message[1:].split()[0] if is_command and len(message) > 1 else ""
            user_id = data.event.chatter_user_id
            badges = getattr(data.event, "badges", None) or []
            if _is_own_chat_echo(user_id, message):
                logger.debug("Skipping connector chat event for the app's own message")
                return

            from . import twitch
            from .twitch_moderators import resolve_is_moderator_async

            broadcaster_id = (
                str(twitch.twitch_api.user_id)
                if twitch.twitch_api and twitch.twitch_api.user_id
                else None
            )
            is_moderator = await resolve_is_moderator_async(
                user_id, badges=badges, broadcaster_id=broadcaster_id
            )

            event_data = EventData.from_twitch_chat(
                username=data.event.chatter_user_name,
                message=message,
                is_command=is_command,
                command=command,
                user_id=user_id,
                badges=_badge_text(badges),
                emotes=_emote_text(data.event.message),
                is_moderator=is_moderator,
            )
            await self.manager.add_event(event_data)
        except Exception as e:
            logger.error(
                f"Error handling Twitch chat for connector: {e}", exc_info=True
            )

    async def _handle_twitch_hype_train_start(self, data):
        """Handle Twitch hype train start event for connector system"""
        try:
            event_data = {
                "event_type": "twitch_hype_train_start",
                "level": data.event.level,
                "total": data.event.total,
                "goal": data.event.goal,
                "top_contributors": getattr(data.event, "top_contributions", []),
                "started_at": data.event.started_at,
                "expires_at": data.event.expires_at,
                "timestamp": data.event.started_at,
                "source": "twitch",
            }
            await self.manager.add_event(event_data)
        except Exception as e:
            logger.error(
                f"Error handling Twitch hype train start for connector: {e}",
                exc_info=True,
            )

    async def _handle_twitch_hype_train_end(self, data):
        """Handle Twitch hype train end event for connector system"""
        try:
            event_data = {
                "event_type": "twitch_hype_train_end",
                "level": data.event.level,
                "total": data.event.total,
                "top_contributors": getattr(data.event, "top_contributions", []),
                "started_at": data.event.started_at,
                "ended_at": data.event.ended_at,
                "cooldown_ends_at": data.event.cooldown_ends_at,
                "timestamp": data.event.ended_at,
                "source": "twitch",
            }
            await self.manager.add_event(event_data)
        except Exception as e:
            logger.error(
                f"Error handling Twitch hype train end for connector: {e}",
                exc_info=True,
            )

    async def _handle_twitch_stream_online(self, data):
        """Handle Twitch stream.online for connector system"""
        try:
            event_data = {
                "event_type": "twitch_stream_online",
                "stream_id": getattr(data.event, "id", ""),
                "stream_type": getattr(data.event, "type", ""),
                "started_at": getattr(data.event, "started_at", ""),
                "timestamp": time.time(),
                "source": "twitch",
            }
            await self.manager.add_event(event_data)
        except Exception as e:
            logger.error(
                "Error handling Twitch stream online for connector: %s",
                e,
                exc_info=True,
            )

    async def _handle_twitch_stream_offline(self, data):
        """Handle Twitch stream.offline for connector system"""
        try:
            event_data = {
                "event_type": "twitch_stream_offline",
                "timestamp": time.time(),
                "source": "twitch",
            }
            await self.manager.add_event(event_data)
        except Exception as e:
            logger.error(
                "Error handling Twitch stream offline for connector: %s",
                e,
                exc_info=True,
            )

    async def send_hotkey_event(
        self, key_code: str, modifiers: List[str] = None, is_global: bool = True
    ):
        """Send a hotkey event to the connector system"""
        try:
            if modifiers is None:
                modifiers = []

            event_data = {
                "event_type": "hotkey",
                "key_code": key_code,
                "modifiers": modifiers,
                "is_global": is_global,
                "timestamp": time.time(),
                "source": "hotkey",
            }

            await self.manager.add_event(event_data)
            logger.debug(f"Hotkey event sent: {key_code} with modifiers {modifiers}")

        except Exception as e:
            logger.error(f"Error sending hotkey event: {e}", exc_info=True)

    async def send_timer_event(
        self,
        timer_id: str,
        interval_seconds: int,
        connector_id: Optional[str] = None,
    ):
        """Send a timer event to the connector system.

        The connector scheduler sends its own addressed timer events; this is
        kept for callers that want to fire a timer by hand. Without
        *connector_id* the event is flagged ``internal_tick`` so Any-event
        connectors ignore it; timer triggers only accept their own ``timer_id``.
        """
        try:
            event_data = {
                "event_type": "timer",
                "timer_id": timer_id,
                "interval_seconds": interval_seconds,
                "timestamp": time.time(),
                "source": "timer",
            }
            if connector_id:
                event_data["target_connector_id"] = connector_id
            else:
                event_data["internal_tick"] = True

            await self.manager.add_event(event_data)
            logger.debug(f"Timer event sent: {timer_id}")

        except Exception as e:
            logger.error(f"Error sending timer event: {e}", exc_info=True)

    async def send_webhook_event(
        self, webhook_data: Dict[str, Any], connector_id: Optional[str] = None
    ):
        """Send a webhook event to the connector system.

        With *connector_id* the event goes to that connector only.
        """
        try:
            await self.manager.add_event(build_webhook_event(webhook_data, connector_id))
            logger.info("Webhook event sent")

        except Exception as e:
            logger.error(f"Error sending webhook event: {e}", exc_info=True)

    def get_available_template_actions(self) -> Dict[str, Dict[str, Any]]:
        """Get available template actions from template configs

        Returns:
            Dict[str, Dict[str, Any]]: Template name -> {action_id: action_config} mapping
        """
        try:
            from . import template_config_parser

            config_parser = template_config_parser.TemplateConfigParser()
            config_files = config_parser.get_config_files()

            template_actions = {}

            for template_name in config_files:
                try:
                    config = config_parser.load_config(
                        template_name, include_dynamic_controls=True
                    )

                    actions = {}

                    # Look for connector_actions section
                    if isinstance(config, dict) and "connector_actions" in config:
                        connector_actions = config["connector_actions"]

                        # connector_actions is a dict where each key is an action ID
                        # and the value contains the full action configuration including elements
                        for action_id, action_config in connector_actions.items():
                            if isinstance(action_config, dict):
                                # Return the full action config, not just the name
                                actions[action_id] = action_config

                    if actions:
                        template_actions[template_name] = actions

                except Exception as e:
                    logger.warning(
                        f"Error processing template {template_name} for actions: {e}"
                    )

            logger.debug(
                f"Found template actions for {len(template_actions)} templates"
            )
            return template_actions

        except Exception as e:
            logger.error(f"Error getting template actions: {e}", exc_info=True)
            return {}


def _message_text(message: Any) -> str:
    """Plain text of a twitchAPI message object (or a str / None)."""
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    text = getattr(message, "text", None)
    if text is None:
        return ""
    return str(text)


def _badge_text(badges: Any) -> str:
    """Comma-separated badge set ids ("broadcaster,subscriber") from twitchAPI badges."""
    names = []
    for badge in badges or []:
        if isinstance(badge, dict):
            name = badge.get("set_id") or badge.get("id")
        elif isinstance(badge, str):
            name = badge
        else:
            name = getattr(badge, "set_id", None) or getattr(badge, "id", None)
        if name:
            names.append(str(name))
    return ",".join(names)


def _emote_text(message: Any) -> str:
    """Comma-separated emote names used in a twitchAPI chat message."""
    names = []
    for fragment in getattr(message, "fragments", None) or []:
        ftype = (
            fragment.get("type") if isinstance(fragment, dict) else getattr(fragment, "type", None)
        )
        if str(ftype or "") != "emote":
            continue
        text = fragment.get("text") if isinstance(fragment, dict) else getattr(fragment, "text", None)
        if text:
            names.append(str(text))
    return ",".join(names)


def _is_own_chat_echo(user_id: Any, message: str) -> bool:
    """True for chat the app itself sent (see chatbot.is_own_chat_message)."""
    try:
        from .chatbot import is_own_chat_message

        return bool(is_own_chat_message(user_id, message))
    except Exception:
        return False


# Keys a webhook payload may not overwrite in the connector event.
_WEBHOOK_RESERVED_KEYS = frozenset(
    {"event_type", "target_connector_id", "connector_id", "payload", "source", "internal_tick"}
)


def build_webhook_event(
    payload: Dict[str, Any], connector_id: Optional[str] = None
) -> Dict[str, Any]:
    """Connector event for a webhook call.

    Payload fields are copied to the top level (so ``{username}`` works), except
    reserved keys; the raw payload is also under ``payload``. With *connector_id*
    the event is addressed to that connector only.
    """
    payload = dict(payload or {})
    event_data: Dict[str, Any] = {
        key: value for key, value in payload.items() if key not in _WEBHOOK_RESERVED_KEYS
    }
    event_data.update(
        {
            "event_type": "webhook",
            "payload": payload,
            "webhook_data": payload,
            "timestamp": payload.get("timestamp") or time.time(),
            "source": "webhook",
        }
    )
    if connector_id:
        event_data["connector_id"] = connector_id
        event_data["target_connector_id"] = connector_id
    return event_data


def handle_connector_webhook(
    connector_id: str, provided_key: Optional[str], payload: Dict[str, Any]
) -> Tuple[int, Dict[str, Any]]:
    """Validate and dispatch one webhook call. Returns (HTTP status, JSON body).

    The connector must exist, be enabled, use a webhook trigger and have a
    ``secret_key``; *provided_key* must match it (constant-time compare).
    """
    from .connector_core import TriggerType

    manager = get_manager()
    connector = manager.get_connector(connector_id) if connector_id else None
    if connector is None:
        return 404, {"ok": False, "error": "Connector not found"}
    trigger = connector.trigger
    if trigger is None or trigger.trigger_type != TriggerType.WEBHOOK:
        return 400, {"ok": False, "error": "Connector is not a webhook trigger"}
    secret = str(getattr(trigger, "secret_key", "") or "")
    if not secret:
        return 401, {"ok": False, "error": "Connector has no webhook key set"}
    if not provided_key or not hmac.compare_digest(
        str(provided_key).encode("utf-8"), secret.encode("utf-8")
    ):
        return 401, {"ok": False, "error": "Invalid webhook key"}
    if not connector.enabled:
        return 409, {"ok": False, "error": "Connector is disabled"}

    event_data = build_webhook_event(payload, connector_id)
    if not _enqueue_connector_event(event_data):
        return 503, {"ok": False, "error": "Connector system is not ready"}
    return 200, {"ok": True}


def _enqueue_connector_event(event_data: Dict[str, Any]) -> bool:
    """Queue an event on the connector loop from any thread. False if not running."""
    from .connector_manager import get_connector_loop

    manager = get_manager()
    if not manager.is_running:
        return False
    loop = get_connector_loop()
    if loop is None or not loop.is_running():
        return False
    try:
        asyncio.run_coroutine_threadsafe(manager.add_event(event_data), loop)
        return True
    except Exception as e:
        logger.debug("Connector enqueue failed: %s", e)
        return False


# Global connector integration instance
connector_integration = ConnectorIntegration()


def _has_enabled_twitch_connectors() -> bool:
    """True when an enabled connector waits for a Twitch event (worth a warning)."""
    try:
        from .connector_manager import get_manager

        for connector in list(get_manager().connectors.values()):
            trigger = getattr(connector, "trigger", None)
            value = str(getattr(getattr(trigger, "trigger_type", None), "value", "") or "")
            if connector.enabled and value.startswith("twitch"):
                return True
    except Exception:
        logger.debug("Could not check for Twitch connectors", exc_info=True)
    return False


def initialize_integration():
    """Initialize the connector integration system"""
    global connector_integration

    try:
        logger.info("Initializing connector integration")

        # Set up integrations
        connector_integration.setup_twitch_integration()

        logger.info("Connector integration initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing connector integration: {e}", exc_info=True)
        notify_critical(
            "Twitch events may not trigger your connectors this session.",
            source="connectors",
            title="Connectors couldn't link to Twitch",
            hint="Restart Mycelian. If it keeps happening, copy the details and report it.",
            details=f"{type(e).__name__}: {e}",
            dedupe_key="connector:integration_init",
            actions=nav_actions_main_tab("Connectors"),
        )


def get_integration() -> ConnectorIntegration:
    """Get the global connector integration instance"""
    return connector_integration
