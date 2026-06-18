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
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class HotkeyListener:
    """Global hotkey listener for connector system"""

    def __init__(self):
        self.is_running = False
        self.listener_thread = None
        self.hotkey_mappings = {}  # key_combination -> list of connector_ids
        self._lock = threading.RLock()

    def start(self):
        """Start the hotkey listener"""
        if self.is_running:
            logger.warning("Hotkey listener is already running")
            return

        self.is_running = True
        logger.info("Starting hotkey listener")

        # Start listener in background thread
        self.listener_thread = threading.Thread(target=self._run_listener, daemon=True)
        self.listener_thread.start()

    def stop(self):
        """Stop the hotkey listener"""
        if not self.is_running:
            return

        self.is_running = False
        logger.info("Stopping hotkey listener")

        if self.listener_thread:
            self.listener_thread.join(timeout=2.0)

    def register_hotkey(
        self, connector_id: str, key_combination: str, is_global: bool = True
    ):
        """Register a hotkey for a connector"""
        try:
            with self._lock:
                if key_combination not in self.hotkey_mappings:
                    self.hotkey_mappings[key_combination] = []

                if connector_id not in self.hotkey_mappings[key_combination]:
                    self.hotkey_mappings[key_combination].append(connector_id)
                    logger.info(
                        f"Registered hotkey '{key_combination}' for connector {connector_id}"
                    )

        except Exception as e:
            logger.error(f"Error registering hotkey: {e}", exc_info=True)

    def unregister_hotkey(self, connector_id: str, key_combination: str = None):
        """Unregister a hotkey for a connector"""
        try:
            with self._lock:
                if key_combination:
                    # Unregister specific hotkey
                    if key_combination in self.hotkey_mappings:
                        if connector_id in self.hotkey_mappings[key_combination]:
                            self.hotkey_mappings[key_combination].remove(connector_id)
                            logger.info(
                                f"Unregistered hotkey '{key_combination}' for connector {connector_id}"
                            )

                            # Clean up empty mappings
                            if not self.hotkey_mappings[key_combination]:
                                del self.hotkey_mappings[key_combination]
                else:
                    # Unregister all hotkeys for this connector
                    keys_to_remove = []
                    for key_combo, connector_ids in self.hotkey_mappings.items():
                        if connector_id in connector_ids:
                            connector_ids.remove(connector_id)
                            logger.info(
                                f"Unregistered hotkey '{key_combo}' for connector {connector_id}"
                            )

                            if not connector_ids:
                                keys_to_remove.append(key_combo)

                    # Clean up empty mappings
                    for key_combo in keys_to_remove:
                        del self.hotkey_mappings[key_combo]

        except Exception as e:
            logger.error(f"Error unregistering hotkey: {e}", exc_info=True)

    def _run_listener(self):
        """Run the hotkey listener in a separate thread"""
        try:
            # Try to use pynput for cross-platform hotkey support
            try:
                from pynput import keyboard

                self._run_pynput_listener()
            except ImportError:
                logger.warning("pynput not available, falling back to keyboard library")
                try:
                    import keyboard

                    self._run_keyboard_listener()
                except ImportError:
                    logger.error(
                        "Neither pynput nor keyboard library available for hotkey listening"
                    )
                    return

        except Exception as e:
            logger.error(f"Error in hotkey listener: {e}", exc_info=True)
        finally:
            self.is_running = False

    def _run_pynput_listener(self):
        """Run hotkey listener using pynput"""
        try:
            from pynput import keyboard
            from pynput.keyboard import Key, KeyCode

            # Create hotkey combinations
            hotkeys = []
            for key_combination in self.hotkey_mappings.keys():
                try:
                    keys = self._parse_key_combination(key_combination)
                    if keys:
                        hotkey = keyboard.HotKey(keys, self._on_hotkey_pressed)
                        hotkeys.append((hotkey, key_combination))
                except Exception as e:
                    logger.warning(
                        f"Failed to parse hotkey combination '{key_combination}': {e}"
                    )

            if not hotkeys:
                logger.info("No valid hotkey combinations to listen for")
                return

            # Start listener
            with keyboard.Listener(
                on_press=self._on_key_press, on_release=self._on_key_release
            ) as listener:
                # Keep track of active hotkeys
                active_hotkeys = {combo: hotkey for hotkey, combo in hotkeys}

                while self.is_running:
                    time.sleep(0.1)

                listener.stop()

        except Exception as e:
            logger.error(f"Error in pynput listener: {e}", exc_info=True)

    def _run_keyboard_listener(self):
        """Run hotkey listener using keyboard library"""
        try:
            import keyboard

            # Register hotkeys
            registered_hotkeys = []
            for key_combination in self.hotkey_mappings.keys():
                try:
                    # The keyboard library uses different syntax
                    keyboard.add_hotkey(
                        key_combination, self._on_hotkey_pressed, args=[key_combination]
                    )
                    registered_hotkeys.append(key_combination)
                    logger.info(f"Registered hotkey: {key_combination}")
                except Exception as e:
                    logger.warning(
                        f"Failed to register hotkey '{key_combination}': {e}"
                    )

            if not registered_hotkeys:
                logger.info("No valid hotkey combinations registered")
                return

            # Keep the listener running
            while self.is_running:
                time.sleep(0.1)

            # Clean up hotkeys
            for key_combination in registered_hotkeys:
                try:
                    keyboard.remove_hotkey(key_combination)
                except:
                    pass

        except Exception as e:
            logger.error(f"Error in keyboard listener: {e}", exc_info=True)

    def _parse_key_combination(self, key_combination: str) -> List:
        """Parse key combination string into pynput Key objects"""
        try:
            from pynput.keyboard import Key, KeyCode

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
            for key_name in key_combination.lower().split("+"):
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
                    except:
                        logger.warning(f"Unknown key: {key_name}")
                        return []

            return keys

        except Exception as e:
            logger.error(f"Error parsing key combination '{key_combination}': {e}")
            return []

    def _on_hotkey_pressed(self, key_combination: str = None):
        """Handle hotkey press event"""
        try:
            # Get the key combination that was pressed
            pressed_combo = key_combination
            if not pressed_combo and hasattr(self, "_current_hotkey"):
                pressed_combo = self._current_hotkey

            if not pressed_combo:
                return

            logger.debug(f"Hotkey pressed: {pressed_combo}")

            # Trigger connectors
            connector_ids = self.hotkey_mappings.get(pressed_combo, [])
            for connector_id in connector_ids:
                try:
                    # Send hotkey event to connector system
                    from .connector_integration import get_integration

                    integration = get_integration()

                    # Create event data for the hotkey
                    event_data = {
                        "event_type": "hotkey",
                        "key_code": pressed_combo.split("+")[
                            -1
                        ],  # Last key is the main key
                        "modifiers": [
                            mod.strip() for mod in pressed_combo.split("+")[:-1]
                        ],
                        "is_global": True,  # Assume global for now
                        "timestamp": time.time(),
                        "source": "hotkey",
                    }

                    # Send to connector system asynchronously
                    asyncio.run_coroutine_threadsafe(
                        integration.manager.add_event(event_data),
                        asyncio.get_event_loop(),
                    )

                except Exception as e:
                    logger.error(
                        f"Error triggering connector {connector_id}: {e}", exc_info=True
                    )

        except Exception as e:
            logger.error(f"Error handling hotkey press: {e}", exc_info=True)

    def _on_key_press(self, key):
        """Handle individual key press (for pynput)"""
        # This is called for each key press, we could implement custom hotkey detection here
        # For now, let pynput handle the hotkey combinations
        pass

    def _on_key_release(self, key):
        """Handle individual key release (for pynput)"""
        pass

    def get_registered_hotkeys(self) -> Dict[str, List[str]]:
        """Get all registered hotkeys"""
        with self._lock:
            return self.hotkey_mappings.copy()


# Global hotkey listener instance
hotkey_listener = HotkeyListener()


def initialize():
    """Initialize the hotkey listener system"""
    global hotkey_listener

    try:
        logger.info("Initializing hotkey listener")
        hotkey_listener.start()
        logger.info("Hotkey listener initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing hotkey listener: {e}", exc_info=True)


def cleanup():
    """Cleanup the hotkey listener system"""
    global hotkey_listener

    try:
        logger.info("Cleaning up hotkey listener")
        hotkey_listener.stop()
        logger.info("Hotkey listener cleaned up successfully")
    except Exception as e:
        logger.error(f"Error cleaning up hotkey listener: {e}", exc_info=True)


def get_listener() -> HotkeyListener:
    """Get the global hotkey listener instance"""
    return hotkey_listener
