# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Help System Module

Import submodules directly (e.g. ``from modules.help_system.contextual_help import help_button``)
so first paint does not load help_content or the help browser.
"""

__all__ = [
    "get_help_manager",
    "HelpManager",
    "show_help_browser",
    "HelpBrowser",
    "help_button",
    "inline_help",
]


def __getattr__(name: str):
    if name in ("get_help_manager", "HelpManager"):
        from .help_manager import HelpManager, get_help_manager

        return get_help_manager if name == "get_help_manager" else HelpManager
    if name in ("show_help_browser", "HelpBrowser"):
        from .help_browser import HelpBrowser, show_help_browser

        return show_help_browser if name == "show_help_browser" else HelpBrowser
    if name in ("help_button", "inline_help"):
        from .contextual_help import help_button, inline_help

        return help_button if name == "help_button" else inline_help
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
