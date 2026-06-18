# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Game hook protocol and UI metadata."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, Tuple


@dataclass(frozen=True)
class HookUiMetadata:
    hook_id: str
    title: str
    supported_platforms: frozenset[str]


def runtime_os_key() -> str:
    p = sys.platform
    if p == "win32":
        return "windows"
    if p == "darwin":
        return "darwin"
    if p.startswith("linux"):
        return "linux"
    return "other"


class GameHook(Protocol):
    hook_id: str
    ui: HookUiMetadata
    process_names: Tuple[str, ...]

    def is_platform_supported(self) -> bool:
        """True when the current OS is listed in ``ui.supported_platforms``."""
        ...

    def is_process_running(self) -> bool:
        ...

    def tick(self) -> Dict[str, Any]:
        """Poll game state; return payload slice for ``hooks[hook_id]``."""
        ...

    def execute_operation(
        self, op: str, kwargs: Dict[str, Any]
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, str]]]:
        ...

    def close(self) -> None:
        ...

    def handle_command(self, action: str, data: Dict[str, Any]) -> None:
        ...

    def on_config_reloaded(self) -> None:
        ...

    def idle_snapshot(self, *, disabled: bool = False) -> Dict[str, Any]:
        ...

    def set_write_enqueue(self, enqueue: Any) -> None:
        """Worker provides ``enqueue(op, kwargs)`` for timed restore replay."""
        ...

    def on_attached(self) -> None:
        """Called when process attach transitions to attached (timed job resume, etc.)."""
        ...

    def on_detached(self) -> None:
        """Called when attach transitions to detached (pause timed jobs, etc.)."""
        ...

    def drain_timed_jobs(self, enqueue_write: Any) -> None:
        """Fire due timed restore operations via ``enqueue_write(op, kwargs)``."""
        ...
