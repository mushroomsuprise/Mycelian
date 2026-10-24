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

_MODIFIER_ORDER = ("ctrl", "alt", "shift", "cmd")
_MODIFIER_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "ctl": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "alt": "alt",
    "option": "alt",
    "opt": "alt",
    "alt_l": "alt",
    "alt_r": "alt",
    "alt_gr": "alt",
    "altgr": "alt",
    "shift": "shift",
    "shift_l": "shift",
    "shift_r": "shift",
    "cmd": "cmd",
    "command": "cmd",
    "meta": "cmd",
    "win": "cmd",
    "windows": "cmd",
    "super": "cmd",
    "os": "cmd",
    "cmd_l": "cmd",
    "cmd_r": "cmd",
}
_KEY_ALIASES = {
    "arrowup": "up",
    "arrowdown": "down",
    "arrowleft": "left",
    "arrowright": "right",
    "pageup": "page_up",
    "pagedown": "page_down",
    "pgup": "page_up",
    "pgdn": "page_down",
    "spacebar": "space",
    "escape": "esc",
    "return": "enter",
    "del": "delete",
    "ins": "insert",
    "+": "plus",
}
# Characters macOS produces for Option+<key> on a US layout, mapped back to the
# key. Used only when alt is part of the combo/press. Dead keys (Option+E, I, N,
# U, `) produce no character, so those combos cannot be told apart by text.
_MAC_OPTION_CHARS = {
    "å": "a", "∫": "b", "ç": "c", "∂": "d", "ƒ": "f", "©": "g", "˙": "h",
    "∆": "j", "˚": "k", "¬": "l", "µ": "m", "ø": "o", "π": "p", "œ": "q",
    "®": "r", "ß": "s", "†": "t", "√": "v", "∑": "w", "≈": "x", "¥": "y",
    "Ω": "z",
    "¡": "1", "™": "2", "£": "3", "¢": "4", "∞": "5", "§": "6", "¶": "7",
    "•": "8", "ª": "9", "º": "0", "–": "-", "≠": "=", "“": "[", "‘": "]",
    "«": "\\", "…": ";", "æ": "'", "≤": ",", "≥": ".", "÷": "/",
    # Option+Shift variants
    "Å": "a", "ı": "b", "Ç": "c", "Î": "d", "Ï": "f", "˝": "g", "Ó": "h",
    "Ô": "j", "Ò": "l", "Â": "m", "Ø": "o", "∏": "p", "Œ": "q",
    "‰": "r", "Í": "s", "ˇ": "t", "◊": "v", "„": "w", "˛": "x", "Á": "y",
    "¸": "z",
}
# Shifted US-layout symbols mapped back to their key (only when shift is held).
_SHIFTED_CHARS = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
    "*": "8", "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]",
    "|": "\\", ":": ";", '"': "'", "<": ",", ">": ".", "?": "/", "~": "`",
}


def base_key_for_char(char: str, modifiers) -> str:
    """Map a produced character back to its key given held modifiers.

    ``ƒ`` with alt -> ``f`` (macOS Option layer); ``!`` with shift -> ``1``.
    Letters are lowercased. Anything else is returned unchanged.
    """
    mods = set(modifiers or ())
    if "alt" in mods and char in _MAC_OPTION_CHARS:
        return _MAC_OPTION_CHARS[char]
    if "shift" in mods and char in _SHIFTED_CHARS:
        return _SHIFTED_CHARS[char]
    return char.lower() if len(char) == 1 else char


def normalize_hotkey_combo(combo: str) -> str:
    """Canonical text for a key combination: ``"Ctrl + Shift + F"`` -> ``"ctrl+shift+f"``.

    * lowercase; spaces around ``+`` dropped; a literal space key or
      ``spacebar`` -> ``space``; a trailing ``+`` (``"ctrl++"``) is the plus key,
      written ``plus`` (``"ctrl+plus"``) so the result splits cleanly on ``+``.
    * modifiers deduplicated and ordered ctrl, alt, shift, cmd;
      control->ctrl, option->alt, meta/win/command/super->cmd.
    * arrowup/down/left/right -> up/down/left/right, pageup/pagedown ->
      page_up/page_down, escape -> esc, return -> enter.
    * macOS Option characters (``ƒ``) map back to their key when alt is in the
      combo, and shifted symbols (``!``) when shift is.
    * the main key is the last non-modifier part; a combo of modifiers only
      keeps just the modifiers.
    """
    if combo is None:
        return ""
    text = str(combo)
    if not text:
        return ""
    raw_parts = text.split("+")
    parts = []
    for index, part in enumerate(raw_parts):
        stripped = part.strip()
        if stripped:
            parts.append(stripped)
        elif part:
            parts.append("space")  # a literal space typed as the key
        elif index == len(raw_parts) - 1 and index > 0 and raw_parts[index - 1] == "":
            parts.append("+")  # "ctrl++" or "+" alone: the plus key
    modifiers = set()
    key = ""
    for part in parts:
        low = part.lower()
        if low in _MODIFIER_ALIASES:
            modifiers.add(_MODIFIER_ALIASES[low])
            continue
        key = part
    if key:
        low = key.lower()
        key = _KEY_ALIASES.get(low, low if len(key) > 1 else key)
        if len(key) == 1:
            key = base_key_for_char(key, modifiers)
    ordered = [m for m in _MODIFIER_ORDER if m in modifiers]
    if key:
        ordered.append(key)
    return "+".join(ordered)


class HotkeyListener:
    """Global hotkey listener for connector system"""

    def __init__(self):
        self.is_running = False
        self.listener_thread = None
        self.hotkey_mappings = {}  # key_combination -> list of connector_ids
        self._lock = threading.RLock()
        self._hotkeys_dirty = True
        self._wake = threading.Event()
        self._pynput_hotkeys = []

    def start(self):
        """Start the hotkey listener"""
        if self.is_running:
            logger.warning("Hotkey listener is already running")
            return

        self.is_running = True
        self._wake.clear()
        logger.info("Starting hotkey listener")

        # Start listener in background thread
        self.listener_thread = threading.Thread(target=self._run_listener, daemon=True)
        self.listener_thread.start()

    def stop(self):
        """Stop the hotkey listener"""
        if not self.is_running:
            return

        self.is_running = False
        self._wake.set()
        logger.info("Stopping hotkey listener")

        if self.listener_thread:
            self.listener_thread.join(timeout=2.0)

    def register_hotkey(
        self, connector_id: str, key_combination: str, is_global: bool = True
    ):
        """Register a hotkey for a connector.

        The combination is stored normalized (:func:`normalize_hotkey_combo`), so
        connectors that share a combination share one mapping entry and one
        press produces one connector event. ``is_global`` is accepted for
        backward compatibility and ignored (hotkeys are always global).
        """
        try:
            key_combination = normalize_hotkey_combo(key_combination)
            if not key_combination:
                return
            with self._lock:
                if key_combination not in self.hotkey_mappings:
                    self.hotkey_mappings[key_combination] = []

                if connector_id not in self.hotkey_mappings[key_combination]:
                    self.hotkey_mappings[key_combination].append(connector_id)
                    logger.info(
                        f"Registered hotkey '{key_combination}' for connector {connector_id}"
                    )
                self._hotkeys_dirty = True
                self._wake.set()

        except Exception as e:
            logger.error(f"Error registering hotkey: {e}", exc_info=True)

    def unregister_hotkey(self, connector_id: str, key_combination: str = None):
        """Unregister a hotkey for a connector"""
        try:
            if key_combination:
                key_combination = normalize_hotkey_combo(key_combination)
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
                self._hotkeys_dirty = True
                self._wake.set()

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

            listener_holder = {"listener": None}
            held_modifiers = set()
            key_enum = getattr(keyboard, "Key", None)
            modifier_names = {}
            for attr in ("alt", "shift", "ctrl", "cmd"):
                member = getattr(key_enum, attr, None) if key_enum is not None else None
                if member is not None:
                    modifier_names[member] = attr

            def _canonical(key):
                listener = listener_holder["listener"]
                ck = listener.canonical(key) if listener else key
                char = getattr(ck, "char", None)
                if isinstance(char, str) and len(char) == 1:
                    # macOS Option / Shift layers report the produced character
                    # (alt+f -> "ƒ"); map it back so "alt+f" still matches.
                    base = base_key_for_char(char, held_modifiers)
                    if base != char:
                        try:
                            ck = keyboard.KeyCode.from_char(base)
                        except Exception:
                            pass  # keep the produced character
                return ck

            def _rebuild_hotkeys() -> None:
                with self._lock:
                    if not self._hotkeys_dirty:
                        combos = list(self.hotkey_mappings.keys())
                    else:
                        combos = list(self.hotkey_mappings.keys())
                        self._hotkeys_dirty = False
                hotkeys = []
                for key_combination in combos:
                    try:
                        keys = self._parse_key_combination(key_combination)
                        if keys:
                            hotkey = keyboard.HotKey(
                                keys,
                                lambda combo=key_combination: self._on_hotkey_pressed(
                                    combo
                                ),
                            )
                            hotkeys.append(hotkey)
                    except Exception as e:
                        logger.warning(
                            f"Failed to parse hotkey combination '{key_combination}': {e}"
                        )
                self._pynput_hotkeys = hotkeys

            def on_press(key):
                if self._hotkeys_dirty:
                    _rebuild_hotkeys()
                ck = _canonical(key)
                try:
                    name = modifier_names.get(ck)
                except TypeError:
                    name = None
                if name:
                    held_modifiers.add(name)
                for hotkey in list(self._pynput_hotkeys):
                    try:
                        hotkey.press(ck)
                    except Exception:
                        pass

            def on_release(key):
                ck = _canonical(key)
                try:
                    name = modifier_names.get(ck)
                except TypeError:
                    name = None
                if name:
                    held_modifiers.discard(name)
                for hotkey in list(self._pynput_hotkeys):
                    try:
                        hotkey.release(ck)
                    except Exception:
                        pass

            _rebuild_hotkeys()
            listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            listener_holder["listener"] = listener
            listener.start()
            try:
                while self.is_running:
                    if self._hotkeys_dirty:
                        _rebuild_hotkeys()
                    # Every mutation and stop() sets _wake; dirty is re-checked after
                    # clear(), so no change can be missed without a polling timeout.
                    self._wake.wait()
                    self._wake.clear()
            finally:
                listener.stop()

        except Exception as e:
            logger.error(f"Error in pynput listener: {e}", exc_info=True)

    def _run_keyboard_listener(self):
        """Run hotkey listener using keyboard library"""
        try:
            import keyboard

            registered_hotkeys: List[str] = []
            last_combos = None

            while self.is_running:
                with self._lock:
                    combos = list(self.hotkey_mappings.keys())
                if combos != last_combos:
                    for key_combination in registered_hotkeys:
                        try:
                            keyboard.remove_hotkey(key_combination)
                        except Exception:
                            pass
                    registered_hotkeys = []
                    for key_combination in combos:
                        try:
                            keyboard.add_hotkey(
                                _keyboard_lib_combo(key_combination),
                                self._on_hotkey_pressed,
                                args=[key_combination],
                            )
                            registered_hotkeys.append(key_combination)
                            logger.info(f"Registered hotkey: {key_combination}")
                        except Exception as e:
                            logger.warning(
                                f"Failed to register hotkey '{key_combination}': {e}"
                            )
                    last_combos = list(combos)
                self._wake.wait()
                self._wake.clear()

            for key_combination in registered_hotkeys:
                try:
                    keyboard.remove_hotkey(key_combination)
                except Exception:
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
                "insert": getattr(Key, "insert", None),
                "caps_lock": Key.caps_lock,
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

            key_map.update({f"f{n}": getattr(Key, f"f{n}") for n in range(13, 21) if hasattr(Key, f"f{n}")})
            key_map = {k: v for k, v in key_map.items() if v is not None}

            keys = []
            for key_name in normalize_hotkey_combo(key_combination).split("+"):
                key_name = key_name.strip()
                if key_name == "plus":
                    key_name = "+"
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

            combo = normalize_hotkey_combo(pressed_combo)
            with self._lock:
                connector_ids = list(self.hotkey_mappings.get(combo, []))
            if not connector_ids:
                return
            # One event per press: every hotkey connector checks it against its
            # own combination, so shared combos must not be sent once per connector.
            event_data = build_hotkey_event(combo)
            try:
                from .connector_integration import get_integration
                from .connector_manager import get_connector_loop

                integration = get_integration()
                loop = get_connector_loop()
                if loop is None or not loop.is_running() or loop.is_closed():
                    logger.warning("Connector loop not ready, dropping hotkey event")
                    return
                asyncio.run_coroutine_threadsafe(
                    integration.manager.add_event(event_data),
                    loop,
                )
            except Exception as e:
                logger.error(
                    f"Error sending hotkey event for {combo} "
                    f"(connectors {connector_ids}): {e}",
                    exc_info=True,
                )

        except Exception as e:
            logger.error(f"Error handling hotkey press: {e}", exc_info=True)

    def _on_key_press(self, key):
        """Handle individual key press (for pynput)"""
        pass

    def _on_key_release(self, key):
        """Handle individual key release (for pynput)"""
        pass

    def get_registered_hotkeys(self) -> Dict[str, List[str]]:
        """Get all registered hotkeys"""
        with self._lock:
            return self.hotkey_mappings.copy()


def build_hotkey_event(combo: str) -> Dict[str, Any]:
    """Connector event for one press of *combo* (normalized)."""
    combo = normalize_hotkey_combo(combo)
    parts = combo.split("+") if combo else [""]
    return {
        "event_type": "hotkey",
        "combo": combo,
        "key_code": parts[-1],
        "modifiers": parts[:-1],
        "is_global": True,
        "timestamp": time.time(),
        "source": "hotkey",
    }


def _keyboard_lib_combo(combo: str) -> str:
    """Spell a normalized combo the way the ``keyboard`` package expects."""
    import sys

    names = {
        "page_up": "page up",
        "page_down": "page down",
        "caps_lock": "caps lock",
        "cmd": "command" if sys.platform == "darwin" else "windows",
    }
    return "+".join(names.get(p, p) for p in normalize_hotkey_combo(combo).split("+"))


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
