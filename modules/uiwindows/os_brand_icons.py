# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
# OS brand marks: colored variants when supported, flat grey when unsupported.
# Apple + Linux path data: Simple Icons v9.14.0 — CC0-1.0 — https://github.com/simple-icons/simple-icons
# Windows four-pane: Microsoft-style brand colors (public logo geometry).
# Linux path: _linux_svg_path.py (Tux outline).

from __future__ import annotations

from typing import Final, Tuple

from ._linux_svg_path import LINUX_SVG_PATH_D

# Flat / unsupported neutral (works on dark and light UIs)
_GREY_FLAT: Final = "#6e6e73"

_APPLE_PATH_D: Final = (
    "M12.152 6.896c-.948 0-2.415-1.078-3.96-1.04-2.04.027-3.91 1.183-4.961 3.014-2.117 3.675-.546 "
    "9.103 1.519 12.09 1.013 1.454 2.208 3.09 3.792 3.039 1.52-.065 2.09-.987 3.935-.987 1.831 0 "
    "2.35.987 3.96.948 1.637-.026 2.676-1.48 3.676-2.948 1.156-1.688 1.636-3.325 1.662-3.415-.039"
    "-.013-3.182-1.221-3.22-4.857-.026-3.04 2.48-4.494 2.597-4.559-1.429-2.09-3.623-2.324-4.39"
    "-2.376-2-.156-3.675 1.09-4.61 1.09zM15.53 3.83c.843-1.012 1.4-2.427 1.245-3.83-1.207.052"
    "-2.662.805-3.532 1.818-.78.896-1.454 2.338-1.273 3.714 1.338.104 2.715-.688 3.559-1.701"
)


def _svg_single_path(fill: str, path_d: str) -> str:
    return (
        '<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" '
        'width="1.25em" height="1.25em" aria-hidden="true">'
        f'<path fill="{fill}" d="{path_d}"/></svg>'
    )


# Microsoft Windows logo — four panes (brand colors)
_SVG_WINDOWS_COLOR: Final[str] = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 88 88" '
    'width="1.25em" height="1.25em" aria-hidden="true">'
    '<path fill="#F25022" d="M0 0h42v42H0z"/>'
    '<path fill="#7FBA00" d="M46 0h42v42H46z"/>'
    '<path fill="#00A4EF" d="M0 46h42v42H0z"/>'
    '<path fill="#FFB900" d="M46 46h42v42H46z"/>'
    "</svg>"
)

_SVG_WINDOWS_GREY: Final[str] = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 88 88" '
    'width="1.25em" height="1.25em" aria-hidden="true">'
    f'<path fill="{_GREY_FLAT}" d="M0 0h42v42H0z"/>'
    f'<path fill="{_GREY_FLAT}" d="M46 0h42v42H46z"/>'
    f'<path fill="{_GREY_FLAT}" d="M0 46h42v42H0z"/>'
    f'<path fill="{_GREY_FLAT}" d="M46 46h42v42H46z"/>'
    "</svg>"
)

# Apple mark — near-white on dark UIs (brand “lit”); grey when unsupported
_APPLE_LIT_FILL: Final = "#f5f5f7"
_SVG_APPLE_COLOR: Final[str] = _svg_single_path(_APPLE_LIT_FILL, _APPLE_PATH_D)
_SVG_APPLE_GREY: Final[str] = _svg_single_path(_GREY_FLAT, _APPLE_PATH_D)

# Tux — warm accent when “supported”; flat grey outline when not
_LINUX_LIT_FILL: Final = "#f5bf03"
_SVG_LINUX_COLOR: Final[str] = _svg_single_path(_LINUX_LIT_FILL, LINUX_SVG_PATH_D)
_SVG_LINUX_GREY: Final[str] = _svg_single_path(_GREY_FLAT, LINUX_SVG_PATH_D)

# (svg when supported, svg when unsupported, os_key, human label)
OS_BRAND_ROW: Final[Tuple[Tuple[str, str, str, str], ...]] = (
    (_SVG_WINDOWS_COLOR, _SVG_WINDOWS_GREY, "windows", "Windows"),
    (_SVG_LINUX_COLOR, _SVG_LINUX_GREY, "linux", "Linux"),
    (_SVG_APPLE_COLOR, _SVG_APPLE_GREY, "darwin", "macOS"),
)
