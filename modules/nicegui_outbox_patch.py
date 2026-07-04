#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Runtime guard for NiceGUI's per-client outbox loop.

NiceGUI stores pending element updates in a ``weakref.WeakValueDictionary``. The
outbox loop iterates that dict to build the update payload, but a weakref
finalizer (or an update enqueued from another thread) can shrink the dict
mid-iteration, raising ``RuntimeError: dictionary changed size during iteration``
(seen in logs as ``nicegui - ERROR - dictionary changed size during iteration``).

In the pinned version the loop already catches the exception, so it is not fatal,
but it drops that batch of UI updates and spams the log. This patch reimplements
``Outbox.loop`` to iterate a snapshot taken with the garbage collector briefly
disabled, eliminating the race. It is gated to tested NiceGUI versions and is fully
best-effort: any problem applying it leaves NiceGUI untouched.
"""

import asyncio
import gc
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_PATCH_ATTR = "__mycelian_outbox_snapshot_patched__"
_MIN_NICEGUI_VERSION = (3, 12, 1)


def _parse_nicegui_version(version: Optional[str]) -> Optional[Tuple[int, int, int]]:
    if not version:
        return None
    parts = version.split(".")
    try:
        nums = tuple(int(p) for p in parts[:3])
        while len(nums) < 3:
            nums = (*nums, 0)
        return nums  # type: ignore[return-value]
    except ValueError:
        return None


def _nicegui_version_supported(version: Optional[str]) -> bool:
    parsed = _parse_nicegui_version(version)
    if parsed is None:
        return False
    major, minor, patch = parsed
    if major != 3:
        return False
    return (minor, patch) >= (_MIN_NICEGUI_VERSION[1], _MIN_NICEGUI_VERSION[2])


def ensure_outbox_snapshot_patch() -> None:
    """Patch ``Outbox.loop`` to snapshot updates before iterating; no-op on failure."""
    try:
        import nicegui

        version = getattr(nicegui, "__version__", None)
        if not _nicegui_version_supported(version):
            logger.warning(
                "Skipping NiceGUI outbox patch: version %s is outside supported "
                "range (>= %s)",
                version,
                ".".join(str(p) for p in _MIN_NICEGUI_VERSION),
            )
            return

        from nicegui import core
        from nicegui import outbox as outbox_mod
        from nicegui.dependencies import JsComponent

        outbox_cls = outbox_mod.Outbox
        if getattr(outbox_cls, _PATCH_ATTR, False):
            return

        deleted_cls = outbox_mod.Deleted
        deleted_sentinel = outbox_mod.deleted

        async def loop(self) -> None:
            """Snapshot-safe reimplementation of NiceGUI's Outbox.loop."""
            self._enqueue_event = asyncio.Event()
            self._enqueue_event.set()

            while not self._should_stop:
                try:
                    if not self._enqueue_event.is_set():
                        try:
                            await asyncio.wait_for(
                                self._enqueue_event.wait(), timeout=1.0
                            )
                        except (TimeoutError, asyncio.TimeoutError):
                            continue

                    client = self.client
                    if not client or not client.has_socket_connection:
                        await asyncio.sleep(0.1)
                        continue

                    self._enqueue_event.clear()

                    coros = []
                    if self.updates:
                        # Take a stable snapshot. Disabling GC during the copy
                        # prevents weakref finalizers from shrinking the dict
                        # mid-iteration (the source of the RuntimeError).
                        gc_was_enabled = gc.isenabled()
                        gc.disable()
                        try:
                            update_items = list(self.updates.items())
                        finally:
                            if gc_was_enabled:
                                gc.enable()

                        data = {
                            element_id: None
                            if element is deleted_sentinel
                            else element._to_dict()  # noqa: SLF001
                            for element_id, element in update_items
                        }
                        js_components = [
                            component
                            for _element_id, element in update_items
                            if not isinstance(element, deleted_cls)
                            and isinstance(
                                (component := element.component), JsComponent
                            )
                            and component.name not in self._loaded_components
                        ]
                        if js_components:
                            coros.append(
                                self._emit(
                                    (
                                        client.id,
                                        "load_js_components",
                                        {
                                            "components": [
                                                {"key": c.key, "tag": c.tag}
                                                for c in js_components
                                            ],
                                        },
                                    )
                                )
                            )
                            self._loaded_components.update(
                                c.name for c in js_components
                            )
                        coros.append(self._emit((client.id, "update", data)))
                        self.updates.clear()

                    if self.messages:
                        for message in self.messages:
                            coros.append(self._emit(message))
                        self.messages.clear()

                    for coro in coros:
                        try:
                            await coro
                        except Exception as e:
                            core.app.handle_exception(e)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    core.app.handle_exception(e)
                    await asyncio.sleep(0.1)

        outbox_cls.loop = loop
        setattr(outbox_cls, _PATCH_ATTR, True)
        logger.warning(
            "Patched NiceGUI %s Outbox.loop with snapshot-safe update iteration",
            version,
        )
    except Exception as e:
        logger.warning("Could not apply NiceGUI outbox snapshot patch: %s", e)
