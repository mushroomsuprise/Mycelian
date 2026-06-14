"""
Shared layout helpers for themed form dialogs.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Generator, Optional

from nicegui import ui


@dataclass
class FormDialogSlots:
    """Body and footer regions inside an app_form_dialog shell."""

    dialog: Any
    body: Any
    footer: Any
    close: Callable[[], None]


@contextmanager
def app_form_dialog(
    title: str,
    *,
    width: str = "min-w-[32rem] max-w-[42rem] w-[90vw]",
    on_close: Optional[Callable[[], None]] = None,
) -> Generator[FormDialogSlots, None, None]:
    """
    Themed persistent dialog: header + body column + footer row.

    Usage:
        with app_form_dialog("Add Quote") as slots:
            with slots.body:
                ...
            with slots.footer:
                ui.button("Cancel", on_click=slots.close).props("flat")
    """
    dialog = ui.dialog().props("persistent")

    def close() -> None:
        dialog.close()
        if on_close:
            on_close()

    with dialog:
        with ui.card().classes(
            f"{width} content-card border border-theme-subtle"
        ):
            with ui.column().classes("w-full"):
                with ui.row().classes(
                    "w-full items-center justify-between p-4 border-b border-theme-subtle"
                ):
                    ui.label(title).classes(
                        "text-lg font-semibold text-theme-primary"
                    )
                    ui.button(icon="close", on_click=close).props(
                        "flat round"
                    ).classes("secondary-text")

                body = ui.column().classes("p-4 gap-4 w-full")
                footer = ui.row().classes(
                    "w-full items-center justify-end gap-2 px-4 pb-4"
                )

                yield FormDialogSlots(
                    dialog=dialog,
                    body=body,
                    footer=footer,
                    close=close,
                )
