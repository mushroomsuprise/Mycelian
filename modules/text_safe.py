"""Safe string helpers for logging and console output on Windows (cp1252)."""

from typing import Any


def safe_console_str(value: Any) -> str:
    """Avoid UnicodeEncodeError when printing or logging user/chat text."""
    if value is None:
        return ""
    return str(value).encode("ascii", "replace").decode("ascii")
