#!/usr/bin/env python3
"""
NiceGUI tab for Spore Studio.

The actual editor is a static HTML/JS bundle hosted by Mycelian's web
engine at ``/_spore_studio_editor`` (see :mod:`modules.web_engine`). The
NiceGUI tab is just a thin host: it shows a placeholder when the web
engine is offline, and an iframe pointing at the editor URL when the
server is ready.
"""

from .editor_view import create_spore_studio_tab

__all__ = ["create_spore_studio_tab"]
