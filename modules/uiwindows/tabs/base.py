from __future__ import annotations

from typing import Protocol


class TabBase(Protocol):
    """Protocol for a settings tab component.

    Each tab is responsible for:
    - Building its own UI within the provided container
    - Buffering its own data and tracking a dirty flag
    - Saving/discarding changes independently
    - Managing any timers/resources on enter/exit
    """

    name: str
    dirty: bool

    def build(self, parent_container) -> None:
        """Build the tab UI inside parent_container."""

    def on_enter(self) -> None:
        """Called when the tab becomes active."""

    def on_exit(self) -> None:
        """Called when the tab is no longer active."""

    def save(self) -> None:
        """Persist buffered changes to the appropriate manager(s)."""

    def discard(self) -> None:
        """Reset buffer and UI from persisted state; clear dirty state."""
