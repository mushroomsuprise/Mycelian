# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Games Tracker: the PSN and Steam libraries, trophy/achievement progress and its changes.

Library syncs run on their own worker threads (``service``). The first sync of an account
is silent; later changes become :class:`~.models.GameEvent` objects that ``dispatch``
sends to Statistics, the chatbot, connectors and overlays.
"""

from __future__ import annotations

from .models import GameEvent, TrackedGame, TrackerSettings

__all__ = ["GameEvent", "TrackedGame", "TrackerSettings"]
