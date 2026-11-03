# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Send Games Tracker changes to Statistics, the chatbot, connectors and overlays.

Every sink is isolated: one failing never stops the others or the sync that found the
change. Only changes after the silent first import ever get here.
"""

from __future__ import annotations

import logging
from typing import Iterable, List

from .models import GameEvent, TrackerSettings

logger = logging.getLogger(__name__)

# GameEvent.kind -> chatbot / connector event type
EVENT_TYPES = {
    "unlock": "game_unlock",
    "completed": "game_completed",
    "started": "game_started",
    "milestone": "game_milestone",
}
CONNECTOR_SOURCE = "game_tracker"


def _to_statistics(event: GameEvent) -> None:
    from ..statistics_manager import get_statistics_manager

    recorder = getattr(get_statistics_manager(), "record_game_event", None)
    if recorder is not None:
        recorder(event)


def _to_chatbot(event: GameEvent) -> None:
    from ..chatbot import dispatch_process_event_result
    from ..chatbot_core import EventType
    from ..chatbot_manager import get_manager as get_chatbot_manager

    event_type = EventType(EVENT_TYPES[event.kind])
    result = get_chatbot_manager().process_event(event_type, event.fields_for_events())
    dispatch_process_event_result(result, default_targets=["twitch"])


def _to_connectors(event: GameEvent) -> None:
    from ..connector_integration import emit_event

    emit_event(CONNECTOR_SOURCE, EVENT_TYPES[event.kind], event.fields_for_events())


def _to_overlays(event: GameEvent) -> None:
    from .. import web_engine

    instance = getattr(web_engine, "web_engine_instance", None)
    if instance:
        instance.safe_emit("game_tracker_event", event.fields_for_events())


def dispatch(events: Iterable[GameEvent], settings: TrackerSettings, store=None) -> List[GameEvent]:
    """Fan the changes out and log them in the tracker's history. Returns the events sent."""
    sent = [e for e in events if e.kind in EVENT_TYPES]
    if not sent:
        return []
    if store is not None:
        try:
            store.record_events(sent)
        except Exception:
            logger.warning("Games Tracker: couldn't save change history", exc_info=True)
    sinks = [("overlays", _to_overlays)]
    if settings.record_stats:
        sinks.append(("statistics", _to_statistics))
    if settings.send_events:
        sinks.extend([("chatbot", _to_chatbot), ("connectors", _to_connectors)])
    for event in sent:
        for name, sink in sinks:
            try:
                sink(event)
            except Exception:
                logger.debug("Games Tracker: %s didn't take %s for %s", name, event.kind, event.game_id, exc_info=True)
    logger.info("Games Tracker: %d change(s) sent", len(sent))
    return sent
