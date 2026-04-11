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
import logging
import os
import subprocess
from dataclasses import dataclass, field, fields
from typing import Any, Dict, Optional

from .connector_core import ActionType, BaseAction

logger = logging.getLogger(__name__)


@dataclass
class TemplateControlAction(BaseAction):
    """Action to control template elements via WebSocket"""

    template_name: str = ""
    control_action: str = ""  # e.g., "counter_increment", "spin", "reset"
    control_data: Dict[str, Any] = None

    def __post_init__(self):
        self.action_type = ActionType.TEMPLATE_CONTROL
        if self.control_data is None:
            self.control_data = {}

    async def execute(
        self, trigger_data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> bool:
        """Execute template control action"""
        try:
            # Import web_engine here to avoid circular imports
            from . import web_engine

            if (
                not web_engine.web_engine_instance
                or not web_engine.web_engine_instance.socketio
            ):
                logger.error("Web engine not available for template control action")
                return False

            # Prepare control data with event context
            control_data = self.control_data.copy()
            control_data.update(
                {
                    "trigger_id": trigger_data.get("trigger_id"),
                    "event_type": event_data.get("event_type"),
                    "timestamp": trigger_data.get("triggered_at"),
                }
            )

            # Replace placeholders in control data with event data
            control_data = self._replace_placeholders(control_data, event_data)

            # Emit WebSocket event
            event_name = f"{self.template_name}_{self.control_action}"
            web_engine.web_engine_instance.socketio.emit(event_name, control_data)

            logger.info(
                f"Template control action executed: {event_name} with data: {control_data}"
            )
            return True

        except Exception as e:
            logger.error(f"Error executing template control action: {e}", exc_info=True)
            return False

    def _replace_placeholders(
        self, data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Replace placeholders in data with values from event_data"""
        result = {}
        for key, value in data.items():
            if (
                isinstance(value, str)
                and value.startswith("{{")
                and value.endswith("}}")
            ):
                # Extract placeholder name
                placeholder = value[2:-2].strip()
                # Get value from event data
                result[key] = self._get_nested_value(event_data, placeholder, value)
            elif isinstance(value, dict):
                result[key] = self._replace_placeholders(value, event_data)
            else:
                result[key] = value
        return result

    def _get_nested_value(
        self, data: Dict[str, Any], path: str, default: Any = None
    ) -> Any:
        """Get value from nested dictionary using dot notation"""
        keys = path.split(".")
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    def validate_parameters(self) -> bool:
        """Validate template control action parameters"""
        if not self.template_name:
            logger.error("Template control action missing template_name")
            return False
        if not self.control_action:
            logger.error("Template control action missing control_action")
            return False
        return True


@dataclass
class WebSocketEmitAction(BaseAction):
    """Action to emit a custom WebSocket event"""

    event_name: str = ""
    event_data: Dict[str, Any] = None

    def __post_init__(self):
        self.action_type = ActionType.WEBSOCKET_EMIT
        if self.event_data is None:
            self.event_data = {}

    async def execute(
        self, trigger_data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> bool:
        """Execute WebSocket emit action"""
        try:
            from . import web_engine

            if (
                not web_engine.web_engine_instance
                or not web_engine.web_engine_instance.socketio
            ):
                logger.error("Web engine not available for WebSocket emit action")
                return False

            # Prepare event data
            websocket_data = self.event_data.copy()
            websocket_data.update(
                {
                    "trigger_id": trigger_data.get("trigger_id"),
                    "triggered_at": trigger_data.get("triggered_at"),
                }
            )

            # Replace placeholders
            websocket_data = self._replace_placeholders(websocket_data, event_data)

            # Emit event
            web_engine.web_engine_instance.socketio.emit(
                self.event_name, websocket_data
            )

            logger.info(
                f"WebSocket event emitted: {self.event_name} with data: {websocket_data}"
            )
            return True

        except Exception as e:
            logger.error(f"Error executing WebSocket emit action: {e}", exc_info=True)
            return False

    def _replace_placeholders(
        self, data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Replace placeholders in data with values from event_data"""
        result = {}
        for key, value in data.items():
            if (
                isinstance(value, str)
                and value.startswith("{{")
                and value.endswith("}}")
            ):
                placeholder = value[2:-2].strip()
                result[key] = self._get_nested_value(event_data, placeholder, value)
            elif isinstance(value, dict):
                result[key] = self._replace_placeholders(value, event_data)
            else:
                result[key] = value
        return result

    def _get_nested_value(
        self, data: Dict[str, Any], path: str, default: Any = None
    ) -> Any:
        """Get value from nested dictionary using dot notation"""
        keys = path.split(".")
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    def validate_parameters(self) -> bool:
        """Validate WebSocket emit action parameters"""
        if not self.event_name:
            logger.error("WebSocket emit action missing event_name")
            return False
        return True


@dataclass
class TriggerAlertAction(BaseAction):
    """Action to trigger an alert through the alert system"""

    alert_type: str = ""
    alert_id: str = ""
    alert_data: Dict[str, Any] = None

    def __post_init__(self):
        self.action_type = ActionType.TRIGGER_ALERT
        if self.alert_data is None:
            self.alert_data = {}

    async def execute(
        self, trigger_data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> bool:
        """Execute trigger alert action"""
        try:
            from . import alert_processor, alertutils

            # Create alert object
            alert = alertutils.AlertObj()
            alert.alert_type = self.alert_type
            alert.alert_id = (
                self.alert_id
                or f"connector_{trigger_data.get('trigger_id')}_{int(trigger_data.get('triggered_at', 0))}"
            )
            alert.timestamp = trigger_data.get("triggered_at", 0)
            alert.is_test = False

            # Fill alert data from event and action parameters
            alert_data = self.alert_data.copy()
            alert_data = self._replace_placeholders(alert_data, event_data)

            # Set alert properties from data
            for key, value in alert_data.items():
                if hasattr(alert, key):
                    setattr(alert, key, value)

            # Add to alert queue
            alert_processor.ALERT_QUEUE.append(alert)

            logger.info(f"Alert triggered: {alert.alert_type} ({alert.alert_id})")
            return True

        except Exception as e:
            logger.error(f"Error executing trigger alert action: {e}", exc_info=True)
            return False

    def _replace_placeholders(
        self, data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Replace placeholders in data with values from event_data"""
        result = {}
        for key, value in data.items():
            if (
                isinstance(value, str)
                and value.startswith("{{")
                and value.endswith("}}")
            ):
                placeholder = value[2:-2].strip()
                result[key] = self._get_nested_value(event_data, placeholder, value)
            elif isinstance(value, dict):
                result[key] = self._replace_placeholders(value, event_data)
            else:
                result[key] = value
        return result

    def _get_nested_value(
        self, data: Dict[str, Any], path: str, default: Any = None
    ) -> Any:
        """Get value from nested dictionary using dot notation"""
        keys = path.split(".")
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    def validate_parameters(self) -> bool:
        """Validate trigger alert action parameters"""
        if not self.alert_type:
            logger.error("Trigger alert action missing alert_type")
            return False
        return True


@dataclass
class SendChatMessageAction(BaseAction):
    """Action to send a message to Twitch chat"""

    message: str = ""

    def __post_init__(self):
        self.action_type = ActionType.SEND_CHAT_MESSAGE

    async def execute(
        self, trigger_data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> bool:
        """Execute send chat message action"""
        try:
            from . import chatbot

            # Check if chatbot is available and connected
            if not chatbot.is_chatbot_connected():
                logger.warning(
                    "Chatbot not connected, attempting to send via main Twitch API as fallback"
                )

                # Fallback to main Twitch API if chatbot is not available
                from . import twitch

                if not twitch.twitch_api or not twitch.twitch_api.is_connected:
                    logger.error(
                        "Neither chatbot nor main Twitch API available for chat message action"
                    )
                    return False

                # Replace placeholders in message
                message = self._replace_placeholders(self.message, event_data)

                # Send via main Twitch API (this would need implementation in twitch.py)
                logger.info(f"Would send chat message via main API: {message}")
                # TODO: Implement direct chat message sending in twitch.py if needed
                return True

            # Replace placeholders in message
            message = self._replace_placeholders(self.message, event_data)

            # Send chat message using chatbot system
            success = chatbot.send_chatbot_message(message)
            if success:
                logger.info(f"Sent chat message via chatbot: {message}")
                return True
            else:
                logger.error("Failed to send chat message via chatbot")
                return False

        except Exception as e:
            logger.error(
                f"Error executing send chat message action: {e}", exc_info=True
            )
            return False

    def _replace_placeholders(self, text: str, event_data: Dict[str, Any]) -> str:
        """Replace placeholders in text with values from event_data"""
        import re

        def replace_func(match):
            placeholder = match.group(1).strip()
            return str(self._get_nested_value(event_data, placeholder, match.group(0)))

        return re.sub(r"\{\{\s*([^}]+)\s*\}\}", replace_func, text)

    def _get_nested_value(
        self, data: Dict[str, Any], path: str, default: Any = None
    ) -> Any:
        """Get value from nested dictionary using dot notation"""
        keys = path.split(".")
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    def validate_parameters(self) -> bool:
        """Validate send chat message action parameters"""
        if not self.message.strip():
            logger.error("Send chat message action missing message")
            return False
        return True


@dataclass
class ApiCallAction(BaseAction):
    """Action to make an HTTP API call"""

    url: str = ""
    method: str = "GET"
    headers: Dict[str, str] = None
    body: Dict[str, Any] = None

    def __post_init__(self):
        self.action_type = ActionType.API_CALL
        if self.headers is None:
            self.headers = {}
        if self.body is None:
            self.body = {}

    async def execute(
        self, trigger_data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> bool:
        """Execute API call action"""
        try:
            import aiohttp

            # Replace placeholders in URL, headers, and body
            url = self._replace_placeholders_text(self.url, event_data)
            headers = self._replace_placeholders_dict(self.headers, event_data)
            body = self._replace_placeholders_dict(self.body, event_data)

            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method=self.method,
                    url=url,
                    headers=headers,
                    json=body if body else None,
                ) as response:
                    if response.status < 400:
                        logger.info(
                            f"API call successful: {self.method} {url} -> {response.status}"
                        )
                        return True
                    else:
                        logger.error(
                            f"API call failed: {self.method} {url} -> {response.status}"
                        )
                        return False

        except Exception as e:
            logger.error(f"Error executing API call action: {e}", exc_info=True)
            return False

    def _replace_placeholders_text(self, text: str, event_data: Dict[str, Any]) -> str:
        """Replace placeholders in text"""
        import re

        def replace_func(match):
            placeholder = match.group(1).strip()
            return str(self._get_nested_value(event_data, placeholder, match.group(0)))

        return re.sub(r"\{\{\s*([^}]+)\s*\}\}", replace_func, text)

    def _replace_placeholders_dict(
        self, data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Replace placeholders in dictionary"""
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self._replace_placeholders_text(value, event_data)
            elif isinstance(value, dict):
                result[key] = self._replace_placeholders_dict(value, event_data)
            else:
                result[key] = value
        return result

    def _get_nested_value(
        self, data: Dict[str, Any], path: str, default: Any = None
    ) -> Any:
        """Get value from nested dictionary using dot notation"""
        keys = path.split(".")
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    def validate_parameters(self) -> bool:
        """Validate API call action parameters"""
        if not self.url:
            logger.error("API call action missing URL")
            return False
        if self.method not in ["GET", "POST", "PUT", "DELETE", "PATCH"]:
            logger.error(f"API call action invalid method: {self.method}")
            return False
        return True


@dataclass
class WriteFileAction(BaseAction):
    """Action to write data to a file"""

    file_path: str = ""
    content: str = ""
    append: bool = False

    def __post_init__(self):
        self.action_type = ActionType.WRITE_FILE

    async def execute(
        self, trigger_data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> bool:
        """Execute write file action"""
        try:
            # Replace placeholders
            file_path = self._replace_placeholders_text(self.file_path, event_data)
            content = self._replace_placeholders_text(self.content, event_data)

            # Ensure directory exists
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            # Write file
            mode = "a" if self.append else "w"
            with open(file_path, mode, encoding="utf-8") as f:
                f.write(content)

            logger.info(f"File written: {file_path}")
            return True

        except Exception as e:
            logger.error(f"Error executing write file action: {e}", exc_info=True)
            return False

    def _replace_placeholders_text(self, text: str, event_data: Dict[str, Any]) -> str:
        """Replace placeholders in text"""
        import re

        def replace_func(match):
            placeholder = match.group(1).strip()
            return str(self._get_nested_value(event_data, placeholder, match.group(0)))

        return re.sub(r"\{\{\s*([^}]+)\s*\}\}", replace_func, text)

    def _get_nested_value(
        self, data: Dict[str, Any], path: str, default: Any = None
    ) -> Any:
        """Get value from nested dictionary using dot notation"""
        keys = path.split(".")
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    def validate_parameters(self) -> bool:
        """Validate write file action parameters"""
        if not self.file_path:
            logger.error("Write file action missing file_path")
            return False
        return True


@dataclass
class ExecuteCommandAction(BaseAction):
    """Action to execute a system command"""

    command: str = ""
    working_directory: str = ""
    timeout_seconds: int = 30

    def __post_init__(self):
        self.action_type = ActionType.EXECUTE_COMMAND

    async def execute(
        self, trigger_data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> bool:
        """Execute command action"""
        try:
            # Replace placeholders
            command = self._replace_placeholders_text(self.command, event_data)
            working_dir = (
                self._replace_placeholders_text(self.working_directory, event_data)
                if self.working_directory
                else None
            )

            # Execute command
            result = subprocess.run(
                command,
                shell=True,
                cwd=working_dir,
                timeout=self.timeout_seconds,
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                logger.info(f"Command executed successfully: {command}")
                return True
            else:
                logger.error(f"Command failed: {command} -> {result.returncode}")
                logger.error(f"stderr: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out: {command}")
            return False
        except Exception as e:
            logger.error(f"Error executing command action: {e}", exc_info=True)
            return False

    def _replace_placeholders_text(self, text: str, event_data: Dict[str, Any]) -> str:
        """Replace placeholders in text"""
        import re

        def replace_func(match):
            placeholder = match.group(1).strip()
            return str(self._get_nested_value(event_data, placeholder, match.group(0)))

        return re.sub(r"\{\{\s*([^}]+)\s*\}\}", replace_func, text)

    def _get_nested_value(
        self, data: Dict[str, Any], path: str, default: Any = None
    ) -> Any:
        """Get value from nested dictionary using dot notation"""
        keys = path.split(".")
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    def validate_parameters(self) -> bool:
        """Validate execute command action parameters"""
        if not self.command:
            logger.error("Execute command action missing command")
            return False
        return True


@dataclass
class KeyPressAction(BaseAction):
    """Action to simulate keyboard and mouse input"""

    input_type: str = "key"  # "key", "mouse", "macro"
    action_mode: str = "press"  # "press", "hold", "release", "repeat"
    key_sequence: str = ""  # Key or sequence to press (e.g., "ctrl+c", "left_click")
    repeat_count: int = 1  # Number of times to repeat
    repeat_interval: float = 0.1  # Seconds between repeats
    hold_duration: float = 0.5  # Duration for hold actions
    macro_sequence: str = ""  # JSON string of macro steps

    def __post_init__(self):
        self.action_type = ActionType.KEY_PRESS

    async def execute(
        self, trigger_data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> bool:
        """Execute key press action"""
        try:
            # Import pynput here to avoid circular imports and handle missing dependency
            try:
                from pynput import keyboard, mouse  # noqa: F401
                from pynput.keyboard import Key, KeyCode  # noqa: F401
                from pynput.mouse import Button  # noqa: F401
            except ImportError as e:
                logger.error(f"pynput library not available for key press action: {e}")
                return False

            logger.info(
                f"Executing key press action: {self.input_type} - {self.action_mode} - {self.key_sequence}"
            )

            # Initialize controllers
            keyboard_controller = keyboard.Controller()
            mouse_controller = mouse.Controller()

            if self.input_type == "macro":
                return await self._execute_macro(
                    keyboard_controller, mouse_controller, event_data
                )
            elif self.input_type == "mouse":
                return await self._execute_mouse_action(mouse_controller)
            else:  # keyboard
                return await self._execute_keyboard_action(
                    keyboard_controller, event_data
                )

        except Exception as e:
            logger.error(f"Error executing key press action: {e}", exc_info=True)
            return False

    async def _execute_keyboard_action(
        self, controller, event_data: Dict[str, Any]
    ) -> bool:
        """Execute keyboard action"""
        try:
            # Replace placeholders in key sequence
            key_sequence = self._replace_placeholders(self.key_sequence, event_data)

            if self.action_mode == "repeat":
                for i in range(self.repeat_count):
                    await self._press_key_sequence(controller, key_sequence)
                    if i < self.repeat_count - 1:  # Don't wait after the last repeat
                        await asyncio.sleep(self.repeat_interval)
            elif self.action_mode == "hold":
                await self._hold_key_sequence(
                    controller, key_sequence, self.hold_duration
                )
            else:  # press or release
                await self._press_key_sequence(
                    controller, key_sequence, self.action_mode == "release"
                )

            return True
        except Exception as e:
            logger.error(f"Error executing keyboard action: {e}", exc_info=True)
            return False

    async def _execute_mouse_action(self, controller) -> bool:
        """Execute mouse action"""
        try:
            from pynput.mouse import Button

            # Parse mouse action
            if self.key_sequence.lower() in ["left_click", "left"]:
                button = Button.left
            elif self.key_sequence.lower() in ["right_click", "right"]:
                button = Button.right
            elif self.key_sequence.lower() in ["middle_click", "middle"]:
                button = Button.middle
            else:
                logger.warning(f"Unknown mouse button: {self.key_sequence}")
                return False

            if self.action_mode == "repeat":
                for i in range(self.repeat_count):
                    controller.click(button)
                    if i < self.repeat_count - 1:
                        await asyncio.sleep(self.repeat_interval)
            elif self.action_mode == "hold":
                controller.press(button)
                await asyncio.sleep(self.hold_duration)
                controller.release(button)
            elif self.action_mode == "press":
                controller.click(button)
            elif self.action_mode == "release":
                controller.release(button)

            return True
        except Exception as e:
            logger.error(f"Error executing mouse action: {e}", exc_info=True)
            return False

    async def _execute_macro(
        self, keyboard_controller, mouse_controller, event_data: Dict[str, Any]
    ) -> bool:
        """Execute macro sequence"""
        try:
            import json

            # Parse macro sequence
            macro_steps = json.loads(self.macro_sequence) if self.macro_sequence else []

            for step in macro_steps:
                step_type = step.get("type", "key")
                action = step.get("action", "press")
                target = step.get("target", "")
                delay = step.get("delay", 0.1)

                # Replace placeholders in target
                target = self._replace_placeholders(target, event_data)

                if step_type == "key":
                    await self._press_key_sequence(
                        keyboard_controller, target, action == "release"
                    )
                elif step_type == "mouse":
                    # Simple mouse click for macro
                    if target.lower() in ["left_click", "left"]:
                        mouse_controller.click(mouse_controller.Button.left)
                    elif target.lower() in ["right_click", "right"]:
                        mouse_controller.click(mouse_controller.Button.right)
                elif step_type == "delay":
                    await asyncio.sleep(float(target))

                if delay > 0:
                    await asyncio.sleep(delay)

            return True
        except Exception as e:
            logger.error(f"Error executing macro: {e}", exc_info=True)
            return False

    async def _press_key_sequence(
        self, controller, key_sequence: str, release_only: bool = False
    ):
        """Press a key sequence like 'ctrl+c' or 'alt+tab'"""
        from pynput.keyboard import Key, KeyCode  # noqa: F401

        # Parse key sequence
        keys = self._parse_key_sequence(key_sequence)

        if not release_only:
            # Press all keys
            for key in keys:
                controller.press(key)

        # Release all keys (in reverse order)
        for key in reversed(keys):
            controller.release(key)

    async def _hold_key_sequence(self, controller, key_sequence: str, duration: float):
        """Hold a key sequence for a specified duration"""
        from pynput.keyboard import Key, KeyCode  # noqa: F401

        keys = self._parse_key_sequence(key_sequence)

        # Press all keys
        for key in keys:
            controller.press(key)

        # Hold
        await asyncio.sleep(duration)

        # Release all keys
        for key in reversed(keys):
            controller.release(key)

    def _parse_key_sequence(self, key_sequence: str) -> list:
        """Parse key sequence string into pynput Key objects"""
        from pynput.keyboard import Key, KeyCode  # noqa: F401

        # Common key mappings
        key_map = {
            "ctrl": Key.ctrl,
            "control": Key.ctrl,
            "alt": Key.alt,
            "shift": Key.shift,
            "cmd": Key.cmd,
            "super": Key.cmd,
            "win": Key.cmd,
            "tab": Key.tab,
            "enter": Key.enter,
            "return": Key.enter,
            "space": Key.space,
            "esc": Key.esc,
            "escape": Key.esc,
            "backspace": Key.backspace,
            "delete": Key.delete,
            "up": Key.up,
            "down": Key.down,
            "left": Key.left,
            "right": Key.right,
            "home": Key.home,
            "end": Key.end,
            "page_up": Key.page_up,
            "page_down": Key.page_down,
            "f1": Key.f1,
            "f2": Key.f2,
            "f3": Key.f3,
            "f4": Key.f4,
            "f5": Key.f5,
            "f6": Key.f6,
            "f7": Key.f7,
            "f8": Key.f8,
            "f9": Key.f9,
            "f10": Key.f10,
            "f11": Key.f11,
            "f12": Key.f12,
        }

        keys = []
        for key_name in key_sequence.lower().split("+"):
            key_name = key_name.strip()
            if key_name in key_map:
                keys.append(key_map[key_name])
            elif len(key_name) == 1:
                # Single character
                keys.append(KeyCode.from_char(key_name))
            else:
                # Try to handle as character anyway
                try:
                    keys.append(KeyCode.from_char(key_name))
                except (ValueError, TypeError):
                    logger.warning(f"Unknown key: {key_name}")

        return keys

    def _replace_placeholders(self, text: str, event_data: Dict[str, Any]) -> str:
        """Replace placeholders in text with values from event_data"""
        if not text:
            return text

        import re

        def replace_placeholder(match):
            placeholder = match.group(1)
            # Get value from event data using dot notation
            keys = placeholder.split(".")
            value = event_data
            for key in keys:
                if isinstance(value, dict) and key in value:
                    value = value[key]
                else:
                    return match.group(0)  # Return original if not found
            return str(value)

        # Replace {{placeholder}} patterns
        return re.sub(r"\{\{([^}]+)\}\}", replace_placeholder, text)


@dataclass
class AudioChangeEntry:
    """Entry for tracking active audio changes in the global registry"""

    original_values: Dict[str, Any]  # Baseline values before any changes
    current_values: Dict[str, Any]  # Currently applied values
    remaining_duration: float  # Time left in seconds
    restoration_task: Optional[asyncio.Task] = None
    task_id: str = ""
    last_update: float = 0.0  # Timestamp of last change

    def update_remaining_duration(self, new_duration: float):
        """Update remaining duration (add to existing if stacking)"""
        import time

        self.remaining_duration += new_duration
        self.last_update = time.time()

    def reset_duration(self, new_duration: float):
        """Reset duration (for random actions)"""
        import time

        self.remaining_duration = new_duration
        self.last_update = time.time()


@dataclass
class AudioControlAction(BaseAction):
    """Action to control system audio (volume, microphone, etc.)"""

    control_type: str = "device"  # "device", "application"
    action_mode: str = "set"  # "set", "set_random", "increase", "decrease", "mute", "unmute", "toggle_mute"
    volume_level: float = 50.0  # Volume level (0-100)
    volume_step: float = 10.0  # Step for increase/decrease operations
    target_application: str = ""  # Application name for app-specific volume control
    target_device: str = ""  # Specific audio device identifier
    device_name: str = ""  # Specific audio device name (optional)
    duration: float = 0.0  # Duration in seconds (0 = permanent change)

    # Class variable to track active restoration tasks
    _active_restoration_tasks: Dict[str, asyncio.Task] = None

    # Global registry for tracking active audio changes by source
    _audio_change_registry: Dict[str, AudioChangeEntry] = None

    def __post_init__(self):
        self.action_type = ActionType.AUDIO_CONTROL
        if AudioControlAction._active_restoration_tasks is None:
            AudioControlAction._active_restoration_tasks = {}
        if AudioControlAction._audio_change_registry is None:
            AudioControlAction._audio_change_registry = {}

    @classmethod
    def cancel_all_restoration_tasks(cls):
        """Cancel all active restoration tasks"""
        if cls._active_restoration_tasks:
            for task_id, task in cls._active_restoration_tasks.items():
                if not task.done():
                    task.cancel()
                    logger.info(f"Cancelled restoration task: {task_id}")
            cls._active_restoration_tasks.clear()

    @classmethod
    def cleanup_audio_change_registry(cls):
        """Clean up the audio change registry"""
        if cls._audio_change_registry:
            # Cancel any active restoration tasks in the registry
            for source_key, entry in cls._audio_change_registry.items():
                if entry.restoration_task and not entry.restoration_task.done():
                    entry.restoration_task.cancel()
                    logger.info(f"Cancelled restoration task for {source_key}")
            cls._audio_change_registry.clear()

    def add_restoration_task(self, task_id: str, task: asyncio.Task):
        """Add a restoration task to the active tasks"""
        if AudioControlAction._active_restoration_tasks is None:
            AudioControlAction._active_restoration_tasks = {}
        AudioControlAction._active_restoration_tasks[task_id] = task

    def remove_restoration_task(self, task_id: str):
        """Remove a restoration task from the active tasks"""
        if AudioControlAction._active_restoration_tasks:
            AudioControlAction._active_restoration_tasks.pop(task_id, None)

    def get_audio_source_key(
        self, target_app: str, target_device: str, device_name: str
    ) -> str:
        """Generate a unique key for the audio source"""
        if self.control_type == "application":
            return f"app:{target_app}"
        else:  # device
            return f"device:{target_device or device_name or 'default'}"

    async def get_or_create_registry_entry(
        self, source_key: str, target_app: str, target_device: str, device_name: str
    ) -> AudioChangeEntry:
        """Get existing registry entry or create a new one"""
        if source_key not in AudioControlAction._audio_change_registry:
            # Create new entry with baseline values
            original_values = {}
            success = False

            if self.control_type == "device":
                success = await self._get_current_device_values(
                    target_device or device_name, original_values
                )
            elif self.control_type == "application":
                success = await self._get_current_app_values(
                    target_app, original_values
                )

            if success:
                entry = AudioChangeEntry(
                    original_values=original_values.copy(),
                    current_values=original_values.copy(),
                    remaining_duration=0.0,
                )
                AudioControlAction._audio_change_registry[source_key] = entry
                return entry
            else:
                logger.warning(f"Failed to get baseline values for {source_key}")
                return None

        return AudioControlAction._audio_change_registry[source_key]

    def update_registry_entry(
        self,
        source_key: str,
        new_values: Dict[str, Any],
        duration: float,
        is_random: bool = False,
    ):
        """Update registry entry with new values and duration"""
        entry = AudioControlAction._audio_change_registry.get(source_key)
        if not entry:
            return

        # Update current values
        entry.current_values.update(new_values)

        if is_random:
            # For random actions, reset the duration
            entry.reset_duration(duration)
        else:
            # For other actions, add to remaining duration
            entry.update_remaining_duration(duration)

    async def execute(
        self, trigger_data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> bool:
        """Execute audio control action"""
        try:
            logger.info(
                f"Executing audio control action: {self.control_type} - {self.action_mode}"
            )

            # Replace placeholders in target application and device
            target_app = self._replace_placeholders(self.target_application, event_data)
            target_device = self._replace_placeholders(self.target_device, event_data)
            device_name = self._replace_placeholders(self.device_name, event_data)

            # Generate source key for registry
            source_key = self.get_audio_source_key(
                target_app, target_device, device_name
            )

            # Handle random volume generation
            is_random = self.action_mode == "set_random"
            if is_random:
                import random

                self.volume_level = random.uniform(0, 100)
                logger.info(f"Random volume set to: {self.volume_level:.1f}%")

            # Handle duration-based changes with stacking
            if self.duration > 0:
                logger.info(f"Temporary audio change for {self.duration} seconds")
                return await self._execute_with_stacking(
                    target_app, target_device, device_name, source_key, is_random
                )
            else:
                # Permanent change - clear any existing duration tracking
                if source_key in AudioControlAction._audio_change_registry:
                    entry = AudioControlAction._audio_change_registry[source_key]
                    if entry.restoration_task and not entry.restoration_task.done():
                        entry.restoration_task.cancel()
                        logger.info(
                            f"Cancelled existing restoration task for {source_key}"
                        )
                    AudioControlAction._audio_change_registry.pop(source_key, None)

                # Apply permanent change
                if self.control_type == "device":
                    return await self._control_device(target_device or device_name)
                elif self.control_type == "application":
                    return await self._control_application_volume(target_app)
                else:
                    logger.error(f"Unknown audio control type: {self.control_type}")
                    return False

        except Exception as e:
            logger.error(f"Error executing audio control action: {e}", exc_info=True)
            return False

    async def _execute_with_stacking(
        self,
        target_app: str,
        target_device: str,
        device_name: str,
        source_key: str,
        is_random: bool,
    ) -> bool:
        """Execute audio control with duration stacking and random reset logic"""
        try:
            # Get or create registry entry
            entry = await self.get_or_create_registry_entry(
                source_key, target_app, target_device, device_name
            )
            if not entry:
                logger.error(f"Failed to create/get registry entry for {source_key}")
                return False

            # Calculate new values that would result from this action
            new_values = await self._calculate_new_values(
                target_app, target_device, device_name
            )
            if new_values is None:
                logger.error("Failed to calculate new audio values")
                return False

            # Handle random vs stacking logic
            if is_random:
                # For random actions: immediately apply new values and reset duration
                logger.info(
                    f"Random action: immediately applying new values for {source_key}"
                )

                # Cancel existing restoration task if any
                if entry.restoration_task and not entry.restoration_task.done():
                    entry.restoration_task.cancel()
                    logger.info(
                        f"Cancelled existing restoration task for random action on {source_key}"
                    )

                # Apply the new random values immediately
                success = await self._apply_calculated_values(
                    new_values, target_app, target_device, device_name
                )
                if not success:
                    logger.error("Failed to apply random audio values")
                    return False

                # Reset duration and schedule new restoration
                entry.reset_duration(self.duration)
                entry.current_values.update(new_values)

            else:
                # For non-random actions: stack duration, update values if needed
                logger.info(f"Stacking action: extending duration for {source_key}")

                # Check if we need to apply new values (if different from current)
                values_changed = self._values_differ(entry.current_values, new_values)

                if values_changed:
                    # Apply new values
                    success = await self._apply_calculated_values(
                        new_values, target_app, target_device, device_name
                    )
                    if not success:
                        logger.error("Failed to apply stacked audio values")
                        return False

                    entry.current_values.update(new_values)
                    logger.info(
                        f"Applied new values for stacked action on {source_key}"
                    )

                # Update duration (stacking logic)
                entry.update_remaining_duration(self.duration)

            # Schedule or update restoration task
            await self._schedule_restoration_task(
                entry, source_key, target_app, target_device, device_name
            )

            logger.info(
                f"Audio change applied for {source_key}, "
                f"remaining duration: {entry.remaining_duration:.1f}s"
            )
            return True

        except Exception as e:
            logger.error(f"Error executing stacking audio control: {e}", exc_info=True)
            return False

    async def _calculate_new_values(
        self, target_app: str, target_device: str, device_name: str
    ) -> Dict[str, Any]:
        """Calculate what the new values would be after applying this action"""
        try:
            # Get current values
            current_values = {}
            success = False

            if self.control_type == "device":
                success = await self._get_current_device_values(
                    target_device or device_name, current_values
                )
            elif self.control_type == "application":
                success = await self._get_current_app_values(target_app, current_values)

            if not success:
                return None

            # Calculate new values based on action mode
            new_values = current_values.copy()

            if self.action_mode == "set" or self.action_mode == "set_random":
                new_values["volume"] = self.volume_level
            elif self.action_mode == "increase":
                new_values["volume"] = min(
                    100.0, current_values.get("volume", 50) + self.volume_step
                )
            elif self.action_mode == "decrease":
                new_values["volume"] = max(
                    0.0, current_values.get("volume", 50) - self.volume_step
                )
            elif self.action_mode == "mute":
                new_values["muted"] = True
            elif self.action_mode == "unmute":
                new_values["muted"] = False
            elif self.action_mode == "toggle_mute":
                new_values["muted"] = not current_values.get("muted", False)

            return new_values

        except Exception as e:
            logger.error(f"Error calculating new values: {e}", exc_info=True)
            return None

    async def _apply_calculated_values(
        self,
        values: Dict[str, Any],
        target_app: str,
        target_device: str,
        device_name: str,
    ) -> bool:
        """Apply the calculated values to the audio system"""
        try:
            if self.control_type == "device":
                return await self._apply_device_values(
                    values, target_device or device_name
                )
            elif self.control_type == "application":
                return await self._apply_app_values(values, target_app)
            return False

        except Exception as e:
            logger.error(f"Error applying calculated values: {e}", exc_info=True)
            return False

    async def _apply_device_values(
        self, values: Dict[str, Any], device_identifier: str
    ) -> bool:
        """Apply values to a device"""
        try:
            import platform

            system = platform.system().lower()

            if system == "windows":
                return await self._apply_windows_device_values(
                    values, device_identifier
                )
            elif system == "darwin":
                return await self._apply_macos_device_values(values, device_identifier)
            elif system == "linux":
                return await self._apply_linux_device_values(values, device_identifier)
            return False

        except Exception as e:
            logger.error(f"Error applying device values: {e}", exc_info=True)
            return False

    async def _apply_app_values(self, values: Dict[str, Any], app_name: str) -> bool:
        """Apply values to an application"""
        try:
            import platform

            system = platform.system().lower()

            if system == "windows":
                return await self._apply_windows_app_values(values, app_name)
            return False

        except Exception as e:
            logger.error(f"Error applying app values: {e}", exc_info=True)
            return False

    def _values_differ(
        self, old_values: Dict[str, Any], new_values: Dict[str, Any]
    ) -> bool:
        """Check if values have changed"""
        for key in ["volume", "muted"]:
            if key in old_values and key in new_values:
                if old_values[key] != new_values[key]:
                    return True
        return False

    async def _schedule_restoration_task(
        self,
        entry: AudioChangeEntry,
        source_key: str,
        target_app: str,
        target_device: str,
        device_name: str,
    ):
        """Schedule or update the restoration task for this entry"""
        try:
            # Cancel existing task if any
            if entry.restoration_task and not entry.restoration_task.done():
                entry.restoration_task.cancel()

            # Create new task ID
            task_id = f"restore_{source_key}_{int(asyncio.get_event_loop().time())}"
            entry.task_id = task_id

            # Schedule restoration
            restoration_task = asyncio.create_task(
                self._restore_with_stacking(
                    entry, source_key, target_app, target_device, device_name, task_id
                )
            )
            entry.restoration_task = restoration_task
            self.add_restoration_task(task_id, restoration_task)

            logger.info(
                f"Scheduled restoration task {task_id} for {source_key} in {entry.remaining_duration}s"
            )

        except Exception as e:
            logger.error(f"Error scheduling restoration task: {e}", exc_info=True)

    async def _restore_with_stacking(
        self,
        entry: AudioChangeEntry,
        source_key: str,
        target_app: str,
        target_device: str,
        device_name: str,
        task_id: str,
    ):
        """Restore audio values with proper stacking cleanup"""
        try:
            # Wait for the duration
            await asyncio.sleep(entry.remaining_duration)

            logger.info(f"Restoring original audio values for {source_key}")

            # Restore original values
            if self.control_type == "device":
                success = await self._restore_device_values(
                    entry.original_values, target_device or device_name
                )
            elif self.control_type == "application":
                success = await self._restore_app_values(
                    entry.original_values, target_app
                )

            if success:
                logger.info(f"Successfully restored audio values for {source_key}")
                # Remove from registry
                AudioControlAction._audio_change_registry.pop(source_key, None)
            else:
                logger.error(f"Failed to restore audio values for {source_key}")

        except asyncio.CancelledError:
            logger.info(f"Restoration task {task_id} was cancelled")
        except Exception as e:
            logger.error(
                f"Error restoring audio values for {task_id}: {e}", exc_info=True
            )
        finally:
            # Always cleanup
            self.remove_restoration_task(task_id)
            # Remove from registry if this was the last task
            if source_key in AudioControlAction._audio_change_registry:
                registry_entry = AudioControlAction._audio_change_registry[source_key]
                if registry_entry.task_id == task_id:
                    AudioControlAction._audio_change_registry.pop(source_key, None)

    async def _execute_with_duration(
        self, target_app: str, target_device: str, device_name: str
    ) -> bool:
        """Execute audio control with duration and automatic restoration"""
        try:
            # Store original values before making changes
            original_values = await self._store_original_values(
                target_app, target_device, device_name
            )

            if original_values is None:
                logger.error("Failed to store original audio values")
                return False

            # Apply the change
            success = False
            if self.control_type == "device":
                success = await self._control_device(target_device or device_name)
            elif self.control_type == "application":
                success = await self._control_application_volume(target_app)

            if not success:
                logger.error("Failed to apply audio change")
                return False

            # Schedule restoration
            task_id = f"{self.action_id}_{int(trigger_data.get('triggered_at', 0))}"
            restoration_task = asyncio.create_task(
                self._restore_audio_values_with_cleanup(
                    original_values, target_app, target_device, device_name, task_id
                )
            )
            self.add_restoration_task(task_id, restoration_task)

            logger.info(
                f"Audio change applied, will restore in {self.duration} seconds"
            )
            return True

        except Exception as e:
            logger.error(
                f"Error executing duration-based audio control: {e}", exc_info=True
            )
            return False

    async def _store_original_values(
        self, target_app: str, target_device: str, device_name: str
    ) -> Dict[str, Any]:
        """Store original audio values before making changes"""
        try:
            original_values = {}

            if self.control_type == "device":
                # Store current device volume and mute state
                if await self._get_current_device_values(
                    target_device or device_name, original_values
                ):
                    return original_values
            elif self.control_type == "application":
                # Store current application volume and mute state
                if await self._get_current_app_values(target_app, original_values):
                    return original_values

            return None

        except Exception as e:
            logger.error(f"Error storing original audio values: {e}", exc_info=True)
            return None

    async def _restore_audio_values(
        self,
        original_values: Dict[str, Any],
        target_app: str,
        target_device: str,
        device_name: str,
    ):
        """Restore audio values after duration expires"""
        try:
            await asyncio.sleep(self.duration)

            logger.info("Restoring original audio values")

            if self.control_type == "device":
                await self._restore_device_values(
                    original_values, target_device or device_name
                )
            elif self.control_type == "application":
                await self._restore_app_values(original_values, target_app)

            logger.info("Audio values restored successfully")

        except Exception as e:
            logger.error(f"Error restoring audio values: {e}", exc_info=True)

    async def _restore_audio_values_with_cleanup(
        self,
        original_values: Dict[str, Any],
        target_app: str,
        target_device: str,
        device_name: str,
        task_id: str,
    ):
        """Restore audio values with cleanup tracking"""
        try:
            await asyncio.sleep(self.duration)

            logger.info(f"Restoring original audio values for task {task_id}")

            if self.control_type == "device":
                await self._restore_device_values(
                    original_values, target_device or device_name
                )
            elif self.control_type == "application":
                await self._restore_app_values(original_values, target_app)

            logger.info(f"Audio values restored successfully for task {task_id}")

        except asyncio.CancelledError:
            logger.info(f"Restoration task {task_id} was cancelled")
        except Exception as e:
            logger.error(
                f"Error restoring audio values for task {task_id}: {e}", exc_info=True
            )
        finally:
            # Always remove the task from tracking
            self.remove_restoration_task(task_id)

    async def _get_current_device_values(
        self, device_identifier: str, values_dict: Dict[str, Any]
    ) -> bool:
        """Get current device volume and mute values"""
        try:
            import platform

            system = platform.system().lower()

            if system == "windows":
                return await self._get_windows_device_values(
                    device_identifier, values_dict
                )
            elif system == "darwin":  # macOS
                return await self._get_macos_device_values(
                    device_identifier, values_dict
                )
            elif system == "linux":
                return await self._get_linux_device_values(
                    device_identifier, values_dict
                )
            else:
                logger.warning(f"Getting device values not supported on {system}")
                return False

        except Exception as e:
            logger.error(f"Error getting current device values: {e}", exc_info=True)
            return False

    async def _get_current_app_values(
        self, app_name: str, values_dict: Dict[str, Any]
    ) -> bool:
        """Get current application volume and mute values"""
        try:
            import platform

            system = platform.system().lower()

            if system == "windows":
                return await self._get_windows_app_values(app_name, values_dict)
            else:
                logger.warning(
                    f"Application-specific audio values not supported on {system}"
                )
                return False

        except Exception as e:
            logger.error(f"Error getting current app values: {e}", exc_info=True)
            return False

    async def _restore_device_values(
        self, original_values: Dict[str, Any], device_identifier: str
    ) -> bool:
        """Restore device volume and mute values"""
        try:
            import platform

            system = platform.system().lower()

            if system == "windows":
                return await self._restore_windows_device_values(
                    original_values, device_identifier
                )
            elif system == "darwin":  # macOS
                return await self._restore_macos_device_values(
                    original_values, device_identifier
                )
            elif system == "linux":
                return await self._restore_linux_device_values(
                    original_values, device_identifier
                )
            else:
                logger.warning(f"Restoring device values not supported on {system}")
                return False

        except Exception as e:
            logger.error(f"Error restoring device values: {e}", exc_info=True)
            return False

    async def _restore_app_values(
        self, original_values: Dict[str, Any], app_name: str
    ) -> bool:
        """Restore application volume and mute values"""
        try:
            import platform

            system = platform.system().lower()

            if system == "windows":
                return await self._restore_windows_app_values(original_values, app_name)
            else:
                logger.warning(f"Restoring app values not supported on {system}")
                return False

        except Exception as e:
            logger.error(f"Error restoring app values: {e}", exc_info=True)
            return False

    async def _get_windows_device_values(
        self, device_identifier: str, values_dict: Dict[str, Any]
    ) -> bool:
        """Get current Windows device values"""
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL

            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)

            values_dict["volume"] = volume.GetMasterVolumeLevelScalar() * 100.0
            values_dict["muted"] = volume.GetMute()
            return True

        except Exception as e:
            logger.error(f"Error getting Windows device values: {e}")
            return False

    async def _get_macos_device_values(
        self, device_identifier: str, values_dict: Dict[str, Any]
    ) -> bool:
        """Get current macOS device values"""
        try:
            import subprocess

            # Get current volume
            get_vol_cmd = "osascript -e 'output volume of (get volume settings)'"
            result = subprocess.run(
                get_vol_cmd, shell=True, capture_output=True, text=True
            )
            if result.returncode == 0:
                values_dict["volume"] = float(result.stdout.strip())
            else:
                values_dict["volume"] = 50.0  # fallback

            # Get current mute state
            get_mute_cmd = "osascript -e 'output muted of (get volume settings)'"
            result = subprocess.run(
                get_mute_cmd, shell=True, capture_output=True, text=True
            )
            values_dict["muted"] = result.stdout.strip().lower() == "true"

            return True

        except Exception as e:
            logger.error(f"Error getting macOS device values: {e}")
            return False

    async def _get_linux_device_values(
        self, device_identifier: str, values_dict: Dict[str, Any]
    ) -> bool:
        """Get current Linux device values"""
        try:
            import pulsectl

            with pulsectl.Pulse("get-device-values") as pulse:
                sinks = pulse.sink_list()
                if sinks:
                    sink = sinks[0]  # Use first sink
                    values_dict["volume"] = sink.volume.value_flat * 100.0
                    values_dict["muted"] = sink.mute
                    return True

            return False

        except Exception as e:
            logger.error(f"Error getting Linux device values: {e}")
            return False

    async def _get_windows_app_values(
        self, app_name: str, values_dict: Dict[str, Any]
    ) -> bool:
        """Get current Windows application values"""
        try:
            from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume

            sessions = AudioUtilities.GetAllSessions()

            for session in sessions:
                if (
                    session.Process
                    and session.Process.name().lower() == app_name.lower()
                ):
                    volume = session._ctl.QueryInterface(ISimpleAudioVolume)
                    values_dict["volume"] = volume.GetMasterVolume() * 100.0
                    values_dict["muted"] = volume.GetMute()
                    return True

            return False

        except Exception as e:
            logger.error(f"Error getting Windows app values: {e}")
            return False

    async def _restore_windows_device_values(
        self, original_values: Dict[str, Any], device_identifier: str
    ) -> bool:
        """Restore Windows device values"""
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL

            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)

            # Restore volume
            if "volume" in original_values:
                volume.SetMasterVolumeLevelScalar(
                    original_values["volume"] / 100.0, None
                )

            # Restore mute state
            if "muted" in original_values:
                volume.SetMute(original_values["muted"], None)

            return True

        except Exception as e:
            logger.error(f"Error restoring Windows device values: {e}")
            return False

    async def _restore_macos_device_values(
        self, original_values: Dict[str, Any], device_identifier: str
    ) -> bool:
        """Restore macOS device values"""
        try:
            import subprocess

            # Restore volume
            if "volume" in original_values:
                cmd = f"osascript -e 'set volume output volume {int(original_values['volume'])}'"
                subprocess.run(cmd, shell=True, check=True)

            # Restore mute state
            if "muted" in original_values:
                mute_state = "true" if original_values["muted"] else "false"
                cmd = f"osascript -e 'set volume output muted {mute_state}'"
                subprocess.run(cmd, shell=True, check=True)

            return True

        except Exception as e:
            logger.error(f"Error restoring macOS device values: {e}")
            return False

    async def _restore_linux_device_values(
        self, original_values: Dict[str, Any], device_identifier: str
    ) -> bool:
        """Restore Linux device values"""
        try:
            import pulsectl

            with pulsectl.Pulse("restore-device-values") as pulse:
                sinks = pulse.sink_list()
                if sinks:
                    sink = sinks[0]  # Use first sink

                    # Restore volume
                    if "volume" in original_values:
                        pulse.volume_set_all_chans(
                            sink, original_values["volume"] / 100.0
                        )

                    # Restore mute state
                    if "muted" in original_values:
                        pulse.mute(sink, original_values["muted"])

                    return True

            return False

        except Exception as e:
            logger.error(f"Error restoring Linux device values: {e}")
            return False

    async def _restore_windows_app_values(
        self, original_values: Dict[str, Any], app_name: str
    ) -> bool:
        """Restore Windows application values"""
        try:
            from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume

            sessions = AudioUtilities.GetAllSessions()

            for session in sessions:
                if (
                    session.Process
                    and session.Process.name().lower() == app_name.lower()
                ):
                    volume = session._ctl.QueryInterface(ISimpleAudioVolume)

                    # Restore volume
                    if "volume" in original_values:
                        volume.SetMasterVolume(original_values["volume"] / 100.0, None)

                    # Restore mute state
                    if "muted" in original_values:
                        volume.SetMute(original_values["muted"], None)

                    return True

            return False

        except Exception as e:
            logger.error(f"Error restoring Windows app values: {e}")
            return False

    async def _apply_windows_device_values(
        self, values: Dict[str, Any], device_identifier: str
    ) -> bool:
        """Apply values to a Windows device"""
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL

            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)

            # Apply volume
            if "volume" in values:
                volume.SetMasterVolumeLevelScalar(values["volume"] / 100.0, None)

            # Apply mute state
            if "muted" in values:
                volume.SetMute(values["muted"], None)

            return True

        except Exception as e:
            logger.error(f"Error applying Windows device values: {e}")
            return False

    async def _apply_macos_device_values(
        self, values: Dict[str, Any], device_identifier: str
    ) -> bool:
        """Apply values to a macOS device"""
        try:
            import subprocess

            # Apply volume
            if "volume" in values:
                cmd = f"osascript -e 'set volume output volume {int(values['volume'])}'"
                subprocess.run(cmd, shell=True, check=True)

            # Apply mute state
            if "muted" in values:
                mute_state = "true" if values["muted"] else "false"
                cmd = f"osascript -e 'set volume output muted {mute_state}'"
                subprocess.run(cmd, shell=True, check=True)

            return True

        except Exception as e:
            logger.error(f"Error applying macOS device values: {e}")
            return False

    async def _apply_linux_device_values(
        self, values: Dict[str, Any], device_identifier: str
    ) -> bool:
        """Apply values to a Linux device"""
        try:
            import pulsectl

            with pulsectl.Pulse("apply-device-values") as pulse:
                sinks = pulse.sink_list()
                if sinks:
                    sink = sinks[0]  # Use first sink

                    # Apply volume
                    if "volume" in values:
                        pulse.volume_set_all_chans(sink, values["volume"] / 100.0)

                    # Apply mute state
                    if "muted" in values:
                        pulse.mute(sink, values["muted"])

                    return True

            return False

        except Exception as e:
            logger.error(f"Error applying Linux device values: {e}")
            return False

    async def _apply_windows_app_values(
        self, values: Dict[str, Any], app_name: str
    ) -> bool:
        """Apply values to a Windows application"""
        try:
            from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume

            sessions = AudioUtilities.GetAllSessions()

            for session in sessions:
                if (
                    session.Process
                    and session.Process.name().lower() == app_name.lower()
                ):
                    volume = session._ctl.QueryInterface(ISimpleAudioVolume)

                    # Apply volume
                    if "volume" in values:
                        volume.SetMasterVolume(values["volume"] / 100.0, None)

                    # Apply mute state
                    if "muted" in values:
                        volume.SetMute(values["muted"], None)

                    return True

            return False

        except Exception as e:
            logger.error(f"Error applying Windows app values: {e}")
            return False

    def _replace_placeholders(self, text: str, event_data: Dict[str, Any]) -> str:
        """Replace placeholders in text with values from event_data"""
        if not text:
            return text

        import re

        def replace_placeholder(match):
            placeholder = match.group(1)
            # Get value from event data using dot notation
            keys = placeholder.split(".")
            value = event_data
            for key in keys:
                if isinstance(value, dict) and key in value:
                    value = value[key]
                else:
                    return match.group(0)  # Return original if not found
            return str(value)

        # Replace {{placeholder}} patterns
        return re.sub(r"\{\{([^}]+)\}\}", replace_placeholder, text)

    async def _control_device(self, device_identifier: str) -> bool:
        """Control a specific audio device"""
        try:
            import platform

            system = platform.system().lower()

            if system == "windows":
                return await self._control_windows_device(device_identifier)
            elif system == "darwin":  # macOS
                return await self._control_macos_device(device_identifier)
            elif system == "linux":
                return await self._control_linux_device(device_identifier)
            else:
                logger.error(
                    f"Unsupported operating system for device control: {system}"
                )
                return False

        except Exception as e:
            logger.error(f"Error controlling device: {e}", exc_info=True)
            return False

    async def _control_windows_device(self, device_identifier: str) -> bool:
        """Control Windows audio device"""
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL

            # For now, always use default device since specific device selection
            # requires more complex audio endpoint enumeration
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)

            if self.action_mode == "set":
                volume.SetMasterVolumeLevelScalar(self.volume_level / 100.0, None)
            elif self.action_mode == "set_random":
                volume.SetMasterVolumeLevelScalar(self.volume_level / 100.0, None)
            elif self.action_mode == "increase":
                current_volume = volume.GetMasterVolumeLevelScalar()
                new_volume = min(1.0, current_volume + (self.volume_step / 100.0))
                volume.SetMasterVolumeLevelScalar(new_volume, None)
            elif self.action_mode == "decrease":
                current_volume = volume.GetMasterVolumeLevelScalar()
                new_volume = max(0.0, current_volume - (self.volume_step / 100.0))
                volume.SetMasterVolumeLevelScalar(new_volume, None)
            elif self.action_mode == "mute":
                volume.SetMute(1, None)
            elif self.action_mode == "unmute":
                volume.SetMute(0, None)
            elif self.action_mode == "toggle_mute":
                current_mute = volume.GetMute()
                volume.SetMute(0 if current_mute else 1, None)

            return True

        except ImportError:
            logger.error("pycaw library not available for Windows device control")
            logger.warning(
                "Audio control may require administrator privileges on Windows"
            )
            return False
        except Exception as e:
            error_msg = str(e).lower()
            if (
                "access" in error_msg
                or "denied" in error_msg
                or "permission" in error_msg
            ):
                logger.warning(
                    "Audio control failed - may require administrator privileges"
                )
                logger.warning(
                    "Try running the application as administrator for full audio control"
                )
            else:
                logger.error(f"Error controlling Windows device: {e}", exc_info=True)
            return False

    async def _control_macos_device(self, device_identifier: str) -> bool:
        """Control macOS audio device"""
        try:
            import subprocess

            # For macOS, we'll use osascript to control audio
            # Note: macOS has limited support for device-specific control
            if self.action_mode == "set":
                cmd = (
                    f"osascript -e 'set volume output volume {int(self.volume_level)}'"
                )
                subprocess.run(cmd, shell=True, check=True)
            elif self.action_mode == "set_random":
                cmd = (
                    f"osascript -e 'set volume output volume {int(self.volume_level)}'"
                )
                subprocess.run(cmd, shell=True, check=True)
            elif self.action_mode == "increase":
                get_vol_cmd = "osascript -e 'output volume of (get volume settings)'"
                result = subprocess.run(
                    get_vol_cmd, shell=True, capture_output=True, text=True
                )
                current_vol = int(result.stdout.strip())
                new_vol = min(100, current_vol + int(self.volume_step))
                cmd = f"osascript -e 'set volume output volume {new_vol}'"
                subprocess.run(cmd, shell=True, check=True)
            elif self.action_mode == "decrease":
                get_vol_cmd = "osascript -e 'output volume of (get volume settings)'"
                result = subprocess.run(
                    get_vol_cmd, shell=True, capture_output=True, text=True
                )
                current_vol = int(result.stdout.strip())
                new_vol = max(0, current_vol - int(self.volume_step))
                cmd = f"osascript -e 'set volume output volume {new_vol}'"
                subprocess.run(cmd, shell=True, check=True)
            elif self.action_mode == "mute":
                cmd = "osascript -e 'set volume output muted true'"
                subprocess.run(cmd, shell=True, check=True)
            elif self.action_mode == "unmute":
                cmd = "osascript -e 'set volume output muted false'"
                subprocess.run(cmd, shell=True, check=True)
            elif self.action_mode == "toggle_mute":
                get_mute_cmd = "osascript -e 'output muted of (get volume settings)'"
                result = subprocess.run(
                    get_mute_cmd, shell=True, capture_output=True, text=True
                )
                is_muted = result.stdout.strip().lower() == "true"
                cmd = f"osascript -e 'set volume output muted {not is_muted}'"
                subprocess.run(cmd, shell=True, check=True)

            return True

        except Exception as e:
            error_msg = str(e).lower()
            if "permission" in error_msg or "denied" in error_msg:
                logger.warning(
                    "Audio control failed - microphone permissions may be required"
                )
                logger.warning(
                    "Check System Preferences > Security & Privacy > Microphone"
                )
            else:
                logger.error(f"Error controlling macOS device: {e}", exc_info=True)
            return False

    async def _control_linux_device(self, device_identifier: str) -> bool:
        """Control Linux audio device"""
        try:
            import pulsectl

            with pulsectl.Pulse("mycelian-device-control") as pulse:
                # Parse device identifier
                if device_identifier and device_identifier.startswith("sink:"):
                    try:
                        sink_index = int(device_identifier.split(":")[1])
                        sink = pulse.sink_list()[sink_index]
                    except (IndexError, ValueError):
                        # Default to first sink
                        sinks = pulse.sink_list()
                        sink = sinks[0] if sinks else None
                elif device_identifier and device_identifier.startswith("source:"):
                    try:
                        source_index = int(device_identifier.split(":")[1])
                        source = pulse.source_list()[source_index]
                        sink = source  # For input devices, we still control volume
                    except (IndexError, ValueError):
                        # Default to first sink
                        sinks = pulse.sink_list()
                        sink = sinks[0] if sinks else None
                else:
                    # Default to first sink
                    sinks = pulse.sink_list()
                    sink = sinks[0] if sinks else None

                if not sink:
                    logger.error("No audio device found")
                    return False

                if self.action_mode == "set":
                    pulse.volume_set_all_chans(sink, self.volume_level / 100.0)
                elif self.action_mode == "set_random":
                    pulse.volume_set_all_chans(sink, self.volume_level / 100.0)
                elif self.action_mode == "increase":
                    current_vol = sink.volume.value_flat
                    new_vol = min(1.0, current_vol + (self.volume_step / 100.0))
                    pulse.volume_set_all_chans(sink, new_vol)
                elif self.action_mode == "decrease":
                    current_vol = sink.volume.value_flat
                    new_vol = max(0.0, current_vol - (self.volume_step / 100.0))
                    pulse.volume_set_all_chans(sink, new_vol)
                elif self.action_mode == "mute":
                    pulse.mute(sink, True)
                elif self.action_mode == "unmute":
                    pulse.mute(sink, False)
                elif self.action_mode == "toggle_mute":
                    pulse.mute(sink, not sink.mute)

            return True

        except ImportError:
            logger.error("pulsectl library not available for Linux device control")
            logger.warning("Audio control may require user to be in 'audio' group")
            return False
        except Exception as e:
            error_msg = str(e).lower()
            if (
                "permission" in error_msg
                or "denied" in error_msg
                or "access" in error_msg
            ):
                logger.warning("Audio control failed - user may not be in audio group")
                logger.warning("Try: sudo usermod -a -G audio $USER")
            else:
                logger.error(f"Error controlling Linux device: {e}", exc_info=True)
            return False

    async def _control_system_volume(self) -> bool:
        """Control system volume"""
        try:
            import platform

            system = platform.system().lower()

            if system == "windows":
                return await self._control_windows_volume()
            elif system == "darwin":  # macOS
                return await self._control_macos_volume()
            elif system == "linux":
                return await self._control_linux_volume()
            else:
                logger.error(
                    f"Unsupported operating system for audio control: {system}"
                )
                return False

        except Exception as e:
            logger.error(f"Error controlling system volume: {e}", exc_info=True)
            return False

    async def _control_microphone(self) -> bool:
        """Control microphone mute/unmute"""
        try:
            import platform

            system = platform.system().lower()

            if system == "windows":
                return await self._control_windows_microphone()
            elif system == "darwin":  # macOS
                return await self._control_macos_microphone()
            elif system == "linux":
                return await self._control_linux_microphone()
            else:
                logger.error(
                    f"Unsupported operating system for microphone control: {system}"
                )
                return False

        except Exception as e:
            logger.error(f"Error controlling microphone: {e}", exc_info=True)
            return False

    async def _control_application_volume(self, app_name: str) -> bool:
        """Control application-specific volume"""
        try:
            import platform

            system = platform.system().lower()

            if system == "windows":
                return await self._control_windows_app_volume(app_name)
            else:
                logger.warning(f"Application volume control not supported on {system}")
                return False

        except Exception as e:
            logger.error(f"Error controlling application volume: {e}", exc_info=True)
            return False

    async def _control_windows_volume(self) -> bool:
        """Control Windows system volume using pycaw"""
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL

            # Get default audio device
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)

            if self.action_mode == "set":
                # Set volume to specific level (0.0 to 1.0)
                volume.SetMasterVolumeLevelScalar(self.volume_level / 100.0, None)
            elif self.action_mode == "set_random":
                # Set volume to random level (0.0 to 1.0)
                volume.SetMasterVolumeLevelScalar(self.volume_level / 100.0, None)
            elif self.action_mode == "increase":
                current_volume = volume.GetMasterVolumeLevelScalar()
                new_volume = min(1.0, current_volume + (self.volume_step / 100.0))
                volume.SetMasterVolumeLevelScalar(new_volume, None)
            elif self.action_mode == "decrease":
                current_volume = volume.GetMasterVolumeLevelScalar()
                new_volume = max(0.0, current_volume - (self.volume_step / 100.0))
                volume.SetMasterVolumeLevelScalar(new_volume, None)
            elif self.action_mode == "mute":
                volume.SetMute(1, None)
            elif self.action_mode == "unmute":
                volume.SetMute(0, None)
            elif self.action_mode == "toggle_mute":
                current_mute = volume.GetMute()
                volume.SetMute(0 if current_mute else 1, None)

            return True

        except ImportError:
            logger.error("pycaw library not available for Windows audio control")
            return False
        except Exception as e:
            logger.error(f"Error controlling Windows volume: {e}", exc_info=True)
            return False

    async def _control_windows_microphone(self) -> bool:
        """Control Windows microphone using pycaw"""
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL

            # Get default microphone device
            devices = AudioUtilities.GetMicrophone()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)

            if self.action_mode == "mute":
                volume.SetMute(1, None)
            elif self.action_mode == "unmute":
                volume.SetMute(0, None)
            elif self.action_mode == "toggle_mute":
                current_mute = volume.GetMute()
                volume.SetMute(0 if current_mute else 1, None)
            elif self.action_mode == "set":
                volume.SetMasterVolumeLevelScalar(self.volume_level / 100.0, None)
            elif self.action_mode == "set_random":
                volume.SetMasterVolumeLevelScalar(self.volume_level / 100.0, None)

            return True

        except ImportError:
            logger.error("pycaw library not available for Windows microphone control")
            return False
        except Exception as e:
            logger.error(f"Error controlling Windows microphone: {e}", exc_info=True)
            return False

    async def _control_windows_app_volume(self, app_name: str) -> bool:
        """Control Windows application volume using pycaw"""
        try:
            from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume

            # Get all audio sessions
            sessions = AudioUtilities.GetAllSessions()

            for session in sessions:
                if (
                    session.Process
                    and session.Process.name().lower() == app_name.lower()
                ):
                    volume = session._ctl.QueryInterface(ISimpleAudioVolume)

                    if self.action_mode == "set":
                        volume.SetMasterVolume(self.volume_level / 100.0, None)
                    elif self.action_mode == "set_random":
                        volume.SetMasterVolume(self.volume_level / 100.0, None)
                    elif self.action_mode == "mute":
                        volume.SetMute(1, None)
                    elif self.action_mode == "unmute":
                        volume.SetMute(0, None)
                    elif self.action_mode == "toggle_mute":
                        current_mute = volume.GetMute()
                        volume.SetMute(0 if current_mute else 1, None)

                    return True

            logger.warning(f"Application '{app_name}' not found in audio sessions")
            return False

        except ImportError:
            logger.error(
                "pycaw library not available for Windows application volume control"
            )
            return False
        except Exception as e:
            logger.error(
                f"Error controlling Windows application volume: {e}", exc_info=True
            )
            return False

    async def _control_macos_volume(self) -> bool:
        """Control macOS system volume using osascript"""
        try:
            import subprocess

            if self.action_mode == "set":
                # Set volume (0-100)
                cmd = (
                    f"osascript -e 'set volume output volume {int(self.volume_level)}'"
                )
                subprocess.run(cmd, shell=True, check=True)
            elif self.action_mode == "set_random":
                # Set random volume (0-100)
                cmd = (
                    f"osascript -e 'set volume output volume {int(self.volume_level)}'"
                )
                subprocess.run(cmd, shell=True, check=True)
            elif self.action_mode == "increase":
                # Get current volume and increase
                get_vol_cmd = "osascript -e 'output volume of (get volume settings)'"
                result = subprocess.run(
                    get_vol_cmd, shell=True, capture_output=True, text=True
                )
                current_vol = int(result.stdout.strip())
                new_vol = min(100, current_vol + int(self.volume_step))
                cmd = f"osascript -e 'set volume output volume {new_vol}'"
                subprocess.run(cmd, shell=True, check=True)
            elif self.action_mode == "decrease":
                # Get current volume and decrease
                get_vol_cmd = "osascript -e 'output volume of (get volume settings)'"
                result = subprocess.run(
                    get_vol_cmd, shell=True, capture_output=True, text=True
                )
                current_vol = int(result.stdout.strip())
                new_vol = max(0, current_vol - int(self.volume_step))
                cmd = f"osascript -e 'set volume output volume {new_vol}'"
                subprocess.run(cmd, shell=True, check=True)
            elif self.action_mode == "mute":
                cmd = "osascript -e 'set volume output muted true'"
                subprocess.run(cmd, shell=True, check=True)
            elif self.action_mode == "unmute":
                cmd = "osascript -e 'set volume output muted false'"
                subprocess.run(cmd, shell=True, check=True)
            elif self.action_mode == "toggle_mute":
                # Get current mute state and toggle
                get_mute_cmd = "osascript -e 'output muted of (get volume settings)'"
                result = subprocess.run(
                    get_mute_cmd, shell=True, capture_output=True, text=True
                )
                is_muted = result.stdout.strip().lower() == "true"
                cmd = f"osascript -e 'set volume output muted {not is_muted}'"
                subprocess.run(cmd, shell=True, check=True)

            return True

        except Exception as e:
            logger.error(f"Error controlling macOS volume: {e}", exc_info=True)
            return False

    async def _control_macos_microphone(self) -> bool:
        """Control macOS microphone using osascript"""
        try:
            import subprocess

            if self.action_mode in ["mute", "unmute", "toggle_mute"]:
                if self.action_mode == "mute":
                    cmd = "osascript -e 'set volume input volume 0'"
                elif self.action_mode == "unmute":
                    cmd = "osascript -e 'set volume input volume 50'"  # Set to 50% when unmuting
                elif self.action_mode == "toggle_mute":
                    # Get current input volume to determine if muted
                    get_vol_cmd = "osascript -e 'input volume of (get volume settings)'"
                    result = subprocess.run(
                        get_vol_cmd, shell=True, capture_output=True, text=True
                    )
                    current_vol = int(result.stdout.strip())
                    if current_vol == 0:
                        cmd = "osascript -e 'set volume input volume 50'"  # Unmute
                    else:
                        cmd = "osascript -e 'set volume input volume 0'"  # Mute

                subprocess.run(cmd, shell=True, check=True)
            elif self.action_mode == "set":
                cmd = f"osascript -e 'set volume input volume {int(self.volume_level)}'"
                subprocess.run(cmd, shell=True, check=True)
            elif self.action_mode == "set_random":
                cmd = f"osascript -e 'set volume input volume {int(self.volume_level)}'"
                subprocess.run(cmd, shell=True, check=True)

            return True

        except Exception as e:
            logger.error(f"Error controlling macOS microphone: {e}", exc_info=True)
            return False

    async def _control_linux_volume(self) -> bool:
        """Control Linux system volume using pulsectl"""
        try:
            import pulsectl

            with pulsectl.Pulse("mycelian-audio-control") as pulse:
                # Get default sink (speakers)
                sinks = pulse.sink_list()
                if not sinks:
                    logger.error("No audio sinks found")
                    return False

                default_sink = sinks[0]  # Use first sink as default

                if self.action_mode == "set":
                    # Set volume (0.0 to 1.0)
                    pulse.volume_set_all_chans(default_sink, self.volume_level / 100.0)
                elif self.action_mode == "set_random":
                    # Set random volume (0.0 to 1.0)
                    pulse.volume_set_all_chans(default_sink, self.volume_level / 100.0)
                elif self.action_mode == "increase":
                    current_vol = default_sink.volume.value_flat
                    new_vol = min(1.0, current_vol + (self.volume_step / 100.0))
                    pulse.volume_set_all_chans(default_sink, new_vol)
                elif self.action_mode == "decrease":
                    current_vol = default_sink.volume.value_flat
                    new_vol = max(0.0, current_vol - (self.volume_step / 100.0))
                    pulse.volume_set_all_chans(default_sink, new_vol)
                elif self.action_mode == "mute":
                    pulse.mute(default_sink, True)
                elif self.action_mode == "unmute":
                    pulse.mute(default_sink, False)
                elif self.action_mode == "toggle_mute":
                    pulse.mute(default_sink, not default_sink.mute)

            return True

        except ImportError:
            logger.error("pulsectl library not available for Linux audio control")
            return False
        except Exception as e:
            logger.error(f"Error controlling Linux volume: {e}", exc_info=True)
            return False

    async def _control_linux_microphone(self) -> bool:
        """Control Linux microphone using pulsectl"""
        try:
            import pulsectl

            with pulsectl.Pulse("mycelian-mic-control") as pulse:
                # Get default source (microphone)
                sources = pulse.source_list()
                if not sources:
                    logger.error("No audio sources found")
                    return False

                # Find the first non-monitor source (actual microphone)
                mic_source = None
                for source in sources:
                    if not source.name.endswith(".monitor"):
                        mic_source = source
                        break

                if not mic_source:
                    logger.error("No microphone source found")
                    return False

                if self.action_mode == "mute":
                    pulse.mute(mic_source, True)
                elif self.action_mode == "unmute":
                    pulse.mute(mic_source, False)
                elif self.action_mode == "toggle_mute":
                    pulse.mute(mic_source, not mic_source.mute)
                elif self.action_mode == "set":
                    pulse.volume_set_all_chans(mic_source, self.volume_level / 100.0)
                elif self.action_mode == "set_random":
                    pulse.volume_set_all_chans(mic_source, self.volume_level / 100.0)

            return True

        except ImportError:
            logger.error("pulsectl library not available for Linux microphone control")
            return False
        except Exception as e:
            logger.error(f"Error controlling Linux microphone: {e}", exc_info=True)
            return False

    def _replace_placeholders(self, text: str, event_data: Dict[str, Any]) -> str:
        """Replace placeholders in text with values from event_data"""
        if not text:
            return text

        import re

        def replace_placeholder(match):
            placeholder = match.group(1)
            # Get value from event data using dot notation
            keys = placeholder.split(".")
            value = event_data
            for key in keys:
                if isinstance(value, dict) and key in value:
                    value = value[key]
                else:
                    return match.group(0)  # Return original if not found
            return str(value)

        # Replace {{placeholder}} patterns
        return re.sub(r"\{\{([^}]+)\}\}", replace_placeholder, text)


@dataclass
class AddGreetingAction(BaseAction):
    """Action to add a new greeting for a user"""

    user_id: str = ""
    username: str = ""
    greeting_text: str = ""
    enabled: bool = True

    def __post_init__(self):
        self.action_type = ActionType.ADD_GREETING

    async def execute(
        self, trigger_data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> bool:
        """Execute add greeting action"""
        try:
            from . import chatbot_manager

            # Replace placeholders in parameters
            user_id = self._replace_placeholders_text(self.user_id, event_data)
            username = self._replace_placeholders_text(self.username, event_data)
            greeting_text = self._replace_placeholders_text(
                self.greeting_text, event_data
            )

            # Get chatbot manager
            manager = chatbot_manager.get_manager()

            # Add the greeting
            success, error_msg, greeting_id = manager.add_greeting(
                user_id=user_id,
                username=username,
                greeting_text=greeting_text,
                enabled=self.enabled,
            )

            if success:
                logger.info(f"Added greeting for user {username} (ID: {user_id})")
                return True
            else:
                logger.error(f"Failed to add greeting: {error_msg}")
                return False

        except Exception as e:
            logger.error(f"Error executing add greeting action: {e}", exc_info=True)
            return False

    def _replace_placeholders_text(self, text: str, event_data: Dict[str, Any]) -> str:
        """Replace placeholders in text with values from event_data"""
        import re

        def replace_func(match):
            placeholder = match.group(1).strip()
            return str(self._get_nested_value(event_data, placeholder, match.group(0)))

        return re.sub(r"\{\{\s*([^}]+)\s*\}\}", replace_func, text)

    def _get_nested_value(
        self, data: Dict[str, Any], path: str, default: Any = None
    ) -> Any:
        """Get value from nested dictionary using dot notation"""
        keys = path.split(".")
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    def validate_parameters(self) -> bool:
        """Validate add greeting action parameters"""
        if not self.user_id:
            logger.error("Add greeting action missing user_id")
            return False
        if not self.username:
            logger.error("Add greeting action missing username")
            return False
        if not self.greeting_text:
            logger.error("Add greeting action missing greeting_text")
            return False
        return True


@dataclass
class UpdateGreetingAction(BaseAction):
    """Action to update an existing greeting"""

    greeting_id: str = ""
    greeting_text: str = ""
    enabled: bool = None

    def __post_init__(self):
        self.action_type = ActionType.UPDATE_GREETING

    async def execute(
        self, trigger_data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> bool:
        """Execute update greeting action"""
        try:
            from . import chatbot_manager

            # Replace placeholders in parameters
            greeting_id = self._replace_placeholders_text(self.greeting_id, event_data)
            greeting_text = (
                self._replace_placeholders_text(self.greeting_text, event_data)
                if self.greeting_text
                else None
            )

            # Get chatbot manager
            manager = chatbot_manager.get_manager()

            # Update the greeting
            success = manager.update_greeting(
                greeting_id=greeting_id,
                greeting_text=greeting_text,
                enabled=self.enabled,
            )

            if success:
                logger.info(f"Updated greeting {greeting_id}")
                return True
            else:
                logger.error(f"Failed to update greeting {greeting_id}")
                return False

        except Exception as e:
            logger.error(f"Error executing update greeting action: {e}", exc_info=True)
            return False

    def _replace_placeholders_text(self, text: str, event_data: Dict[str, Any]) -> str:
        """Replace placeholders in text with values from event_data"""
        import re

        def replace_func(match):
            placeholder = match.group(1).strip()
            return str(self._get_nested_value(event_data, placeholder, match.group(0)))

        return re.sub(r"\{\{\s*([^}]+)\s*\}\}", replace_func, text)

    def _get_nested_value(
        self, data: Dict[str, Any], path: str, default: Any = None
    ) -> Any:
        """Get value from nested dictionary using dot notation"""
        keys = path.split(".")
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    def validate_parameters(self) -> bool:
        """Validate update greeting action parameters"""
        if not self.greeting_id:
            logger.error("Update greeting action missing greeting_id")
            return False
        # At least one field to update should be provided
        if self.greeting_text is None and self.enabled is None:
            logger.error(
                "Update greeting action needs at least greeting_text or enabled parameter"
            )
            return False
        return True


@dataclass
class SendGreetingAction(BaseAction):
    """Action to send a greeting to a user (even if already sent)"""

    user_id: str = ""
    username: str = ""
    force_send: bool = True  # Override normal greeting logic

    def __post_init__(self):
        self.action_type = ActionType.SEND_GREETING

    async def execute(
        self, trigger_data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> bool:
        """Execute send greeting action"""
        try:
            from . import chatbot_manager, twitch

            # Replace placeholders in parameters
            user_id = self._replace_placeholders_text(self.user_id, event_data)
            username = self._replace_placeholders_text(self.username, event_data)

            # Get chatbot manager
            manager = chatbot_manager.get_manager()

            # Process the greeting
            greeting_message = manager.process_greeting(
                user_id=user_id,
                username=username,
                current_time=trigger_data.get("triggered_at"),
            )

            if greeting_message:
                # Send the greeting via chat
                if twitch.twitch_api and twitch.twitch_api.is_connected:
                    # For now, just log what would be sent
                    logger.info(
                        f"Would send greeting to {username}: {greeting_message}"
                    )
                    # TODO: Implement actual chat message sending in twitch.py
                    return True
                else:
                    logger.error("Twitch API not available for sending greeting")
                    return False
            else:
                if self.force_send:
                    # Try to send default greeting even if user was already greeted
                    default_greeting = manager.get_default_greeting()
                    if default_greeting and manager.get_default_greeting_enabled():
                        greeting_message = default_greeting.replace(
                            "{username}", username
                        )

                        if twitch.twitch_api and twitch.twitch_api.is_connected:
                            logger.info(
                                f"Force sending default greeting to {username}: {greeting_message}"
                            )
                            # TODO: Implement actual chat message sending in twitch.py
                            return True
                        else:
                            logger.error(
                                "Twitch API not available for sending greeting"
                            )
                            return False

                logger.info(f"No greeting to send for user {username}")
                return True  # Not an error, just no greeting to send

        except Exception as e:
            logger.error(f"Error executing send greeting action: {e}", exc_info=True)
            return False

    def _replace_placeholders_text(self, text: str, event_data: Dict[str, Any]) -> str:
        """Replace placeholders in text with values from event_data"""
        import re

        def replace_func(match):
            placeholder = match.group(1).strip()
            return str(self._get_nested_value(event_data, placeholder, match.group(0)))

        return re.sub(r"\{\{\s*([^}]+)\s*\}\}", replace_func, text)

    def _get_nested_value(
        self, data: Dict[str, Any], path: str, default: Any = None
    ) -> Any:
        """Get value from nested dictionary using dot notation"""
        keys = path.split(".")
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    def validate_parameters(self) -> bool:
        """Validate send greeting action parameters"""
        if not self.user_id:
            logger.error("Send greeting action missing user_id")
            return False
        if not self.username:
            logger.error("Send greeting action missing username")
            return False
        return True


@dataclass
class GameHookAction(BaseAction):
    """Write to a supported game's memory (crowd control), via the game hooks service thread."""

    game_id: str = "ff7"
    operation: str = ""
    hook_arguments: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.action_type = ActionType.GAME_HOOK

    async def execute(
        self, trigger_data: Dict[str, Any], event_data: Dict[str, Any]
    ) -> bool:
        try:
            from .game_hooks_service import game_hooks_service

            cfut = game_hooks_service.enqueue_game_hook_write(
                self.game_id, self.operation, self.hook_arguments
            )
            ok, err_msg = await asyncio.wait_for(
                asyncio.wrap_future(cfut), timeout=10.0
            )
            if not ok:
                logger.warning(
                    "Game hook write failed game_id=%s op=%s err=%s",
                    self.game_id,
                    self.operation,
                    err_msg,
                )
            return bool(ok)
        except asyncio.TimeoutError:
            logger.error("Game hook write timed out: %s", self.operation)
            return False
        except Exception as e:
            logger.error("Game hook action error: %s", e, exc_info=True)
            return False


# Factory function for creating actions
def create_action(
    action_type: ActionType, action_id: str, name: str, **kwargs
) -> BaseAction:
    """Factory function to create action instances"""
    action_classes = {
        ActionType.TEMPLATE_CONTROL: TemplateControlAction,
        ActionType.WEBSOCKET_EMIT: WebSocketEmitAction,
        ActionType.TRIGGER_ALERT: TriggerAlertAction,
        ActionType.SEND_CHAT_MESSAGE: SendChatMessageAction,
        ActionType.ADD_GREETING: AddGreetingAction,
        ActionType.UPDATE_GREETING: UpdateGreetingAction,
        ActionType.SEND_GREETING: SendGreetingAction,
        ActionType.API_CALL: ApiCallAction,
        ActionType.WRITE_FILE: WriteFileAction,
        ActionType.EXECUTE_COMMAND: ExecuteCommandAction,
        ActionType.KEY_PRESS: KeyPressAction,
        ActionType.AUDIO_CONTROL: AudioControlAction,
        ActionType.GAME_HOOK: GameHookAction,
    }

    action_class = action_classes.get(action_type)
    if not action_class:
        raise ValueError(f"Unknown action type: {action_type}")

    # Remove action_type from kwargs to avoid duplicate parameter
    filtered_kwargs = {k: v for k, v in kwargs.items() if k != "action_type"}

    # Only pass parameters that are defined on the action dataclass to avoid __init__ errors
    allowed_field_names = {f.name for f in fields(action_class)}
    safe_kwargs = {k: v for k, v in filtered_kwargs.items() if k in allowed_field_names}

    # Log and ignore unexpected keys to aid debugging
    unexpected_keys = set(filtered_kwargs.keys()) - allowed_field_names
    if unexpected_keys:
        logger.debug(
            f"Ignoring unexpected parameters for {action_type.value}: {sorted(unexpected_keys)}"
        )

    return action_class(
        action_id=action_id, name=name, action_type=action_type, **safe_kwargs
    )
