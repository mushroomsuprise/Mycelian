# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Help System Module

Provides comprehensive in-app help documentation with contextual help buttons,
searchable documentation, and an integrated help browser for Mycelian.
"""

from .help_manager import get_help_manager, HelpManager
from .help_browser import show_help_browser, HelpBrowser
from .contextual_help import help_button, inline_help

__all__ = [
    'get_help_manager',
    'HelpManager',
    'show_help_browser',
    'HelpBrowser',
    'help_button',
    'inline_help'
]