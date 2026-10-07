#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Stage the installer payload shared by the Arch package and the macOS pkg.

The file list matches the uncommented ``[Files]`` entries in ``Mycelian.iss``.
Template JSON is copied only to ``templates/template_configs_temp``. The live
``templates/template_configs`` directory is never shipped, so an upgrade can
merge user values instead of overwriting them.

This directory must not contain ``__init__.py``. A ``packaging`` package on
the project path would shadow the third-party ``packaging`` dependency.
"""

import shutil
from pathlib import Path


# Stems of the HTML templates and matching template JSON shipped by Mycelian.iss.
# Commented-out templates in that script (combobar, follow, memecalc, salary,
# ttimer, and anything else not listed there) are intentionally omitted.
TEMPLATE_STEMS = (
    "activity_feed",
    "bitbar",
    "bitboss",
    "chat",
    "counter",
    "pausedalerts",
    "roulette",
    "source_controls",
    "subbar",
    "title",
    "alerts",
    "giveaway",
    "ff7",
    "factorio",
    "bitcounter",
    "spotify",
    "trophies",
)

# Directories copied in full on every install (Inno ``ignoreversion``).
ALWAYS_DIRS = (
    "sd_plugin",
    "factorio_mod",
    "assets/default_assets",
    "assets/ff7",
)

# Copied into installer_defaults/ and installed only when the destination
# path does not already exist (Inno ``onlyifdoesntexist``).
ONLY_IF_MISSING = (
    "themes",
    "assets/pausedalerts",
    "assets/bitboss",
)


def stage_install_payload(dest: Path, project_root: Path) -> None:
    """Copy installer data files into ``dest``.

    Does not copy the application binary or ``merge_template_configs``.
    The caller adds those for the OS it is packaging.
    """
    dest.mkdir(parents=True, exist_ok=True)
    _require_copy(project_root / "README.md", dest / "README.md")

    for relative in ALWAYS_DIRS:
        _require_copy(project_root / relative, dest / relative)

    templates = dest / "templates"
    for stem in TEMPLATE_STEMS:
        _require_copy(
            project_root / "templates" / f"{stem}.html",
            templates / f"{stem}.html",
        )
        _require_copy(
            project_root / "templates" / "template_configs" / f"{stem}.json",
            templates / "template_configs_temp" / f"{stem}.json",
        )

    _require_copy(
        project_root / "templates" / "_boilerplates",
        templates / "_boilerplates",
    )
    _require_copy(
        project_root / "templates" / "_spore" / "bitcounter.spore.json",
        templates / "_spore" / "bitcounter.spore.json",
    )

    defaults = dest / "installer_defaults"
    for relative in ONLY_IF_MISSING:
        _require_copy(project_root / relative, defaults / relative)


def _require_copy(src: Path, dest: Path) -> None:
    if not src.exists():
        raise FileNotFoundError(f"Installer payload file missing: {src}")
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
