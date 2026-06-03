"""System font family discovery for the app font setting."""

from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Set

logger = logging.getLogger(__name__)

SYSTEM_DEFAULT_LABEL = "System default"
_DEFAULT_STACK = "'Inter', -apple-system, BlinkMacSystemFont, sans-serif"

_cached_families: Optional[List[str]] = None

_FONT_EXTENSIONS = frozenset({".ttf", ".otf", ".ttc", ".woff", ".woff2"})


def _normalize_family_name(raw: str) -> str:
    name = raw.strip()
    if not name:
        return ""
    # Drop style suffixes from filenames like "Arial-Bold"
    name = re.sub(r"[-_](Bold|Italic|Regular|Light|Medium|Black).*$", "", name, flags=re.I)
    return name.strip()


def _families_from_paths(paths: List[Path]) -> Set[str]:
    found: Set[str] = set()
    for base in paths:
        if not base.is_dir():
            continue
        try:
            for path in base.rglob("*"):
                if path.suffix.lower() not in _FONT_EXTENSIONS:
                    continue
                family = _normalize_family_name(path.stem)
                if family and not family.startswith("."):
                    found.add(family)
        except OSError as exc:
            logger.debug("Font scan skipped for %s: %s", base, exc)
    return found


def _families_fc_list() -> Set[str]:
    try:
        out = subprocess.run(
            ["fc-list", ":family"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return set()
    families: Set[str] = set()
    for line in (out.stdout or "").splitlines():
        # "Family Name,Family Name:style=Regular"
        head = line.split(":", 1)[0].strip()
        for part in head.split(","):
            part = part.strip()
            if part:
                families.add(part)
    return families


def _font_directories() -> List[Path]:
    system = platform.system()
    if system == "Darwin":
        home = Path.home()
        return [
            Path("/System/Library/Fonts"),
            Path("/Library/Fonts"),
            home / "Library/Fonts",
        ]
    if system == "Windows":
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        return [windir / "Fonts"]
    return [
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".fonts",
        Path.home() / ".local/share/fonts",
    ]


def get_available_font_families(*, refresh: bool = False) -> List[str]:
    """Return sorted font family names with System default first."""
    global _cached_families
    if _cached_families is not None and not refresh:
        return list(_cached_families)

    families: Set[str] = set()
    families |= _families_from_paths(_font_directories())
    if platform.system() == "Linux":
        families |= _families_fc_list()

    sorted_names = sorted(families, key=lambda s: s.lower())
    _cached_families = [SYSTEM_DEFAULT_LABEL] + sorted_names
    return list(_cached_families)


def resolve_font_css(family: Optional[str]) -> str:
    """CSS font-family value for AppSettings.ui_font_family."""
    if not family or family == SYSTEM_DEFAULT_LABEL:
        return _DEFAULT_STACK
    safe = str(family).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{safe}', -apple-system, BlinkMacSystemFont, sans-serif"
