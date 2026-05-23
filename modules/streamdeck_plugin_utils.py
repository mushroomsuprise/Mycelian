"""Stream Deck plugin install path and version helpers."""

from __future__ import annotations

import json
import logging
import os
import platform
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from packaging.version import InvalidVersion, Version, parse as parse_version

from .notification_engine import nav_actions_settings, notify
from .path_utils import get_data_path

logger = logging.getLogger(__name__)

PLUGIN_BUNDLE_NAME = "com.mushroomsuprise.mycelian.sdPlugin"
OUTDATED_NOTIFY_DEDUPE_KEY = "streamdeck:plugin_outdated"
OUTDATED_NOTIFY_COOLDOWN_SEC = 86400.0


class PluginInstallState(str, Enum):
    UNAVAILABLE = "unavailable"
    NOT_INSTALLED = "not_installed"
    UP_TO_DATE = "up_to_date"
    OUTDATED = "outdated"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PluginStatus:
    state: PluginInstallState
    bundled_version: Optional[str] = None
    installed_version: Optional[str] = None

    @property
    def is_installed(self) -> bool:
        return self.state in (
            PluginInstallState.UP_TO_DATE,
            PluginInstallState.OUTDATED,
            PluginInstallState.UNKNOWN,
        )


def get_streamdeck_plugins_dir() -> Optional[Path]:
    """OS-specific Elgato Stream Deck plugins directory, if it exists."""
    system = platform.system()
    if system == "Darwin":
        plugins_dir = (
            Path.home()
            / "Library"
            / "Application Support"
            / "com.elgato.StreamDeck"
            / "Plugins"
        )
    elif system == "Windows":
        appdata = os.path.expandvars("%appdata%")
        plugins_dir = Path(appdata) / "Elgato" / "StreamDeck" / "Plugins"
    else:
        return None
    return plugins_dir if plugins_dir.exists() else None


def get_bundled_plugin_dir() -> Path:
    return Path(get_data_path("sd_plugin")) / PLUGIN_BUNDLE_NAME


def get_installed_plugin_dir() -> Optional[Path]:
    plugins_dir = get_streamdeck_plugins_dir()
    if not plugins_dir:
        return None
    plugin_dir = plugins_dir / PLUGIN_BUNDLE_NAME
    if not plugin_dir.is_dir():
        return None
    return plugin_dir


def read_plugin_version(manifest_path: Path) -> Optional[str]:
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        version = data.get("Version")
        if version is None:
            return None
        text = str(version).strip()
        return text or None
    except Exception as e:
        logger.debug("Could not read Stream Deck plugin version from %s: %s", manifest_path, e)
        return None


def compare_plugin_versions(bundled: str, installed: str) -> int:
    """
    Compare installed vs bundled versions.

    Returns:
        -1 if installed < bundled (outdated)
         0 if equal
         1 if installed > bundled
    Raises InvalidVersion if either string cannot be parsed.
    """
    installed_v: Version = parse_version(installed)
    bundled_v: Version = parse_version(bundled)
    if installed_v < bundled_v:
        return -1
    if installed_v > bundled_v:
        return 1
    return 0


def get_plugin_status() -> PluginStatus:
    bundled_dir = get_bundled_plugin_dir()
    bundled_manifest = bundled_dir / "manifest.json"
    bundled_version = (
        read_plugin_version(bundled_manifest) if bundled_manifest.is_file() else None
    )

    plugins_dir = get_streamdeck_plugins_dir()
    if plugins_dir is None:
        return PluginStatus(
            state=PluginInstallState.UNAVAILABLE,
            bundled_version=bundled_version,
        )

    installed_dir = get_installed_plugin_dir()
    if installed_dir is None:
        return PluginStatus(
            state=PluginInstallState.NOT_INSTALLED,
            bundled_version=bundled_version,
        )

    installed_manifest = installed_dir / "manifest.json"
    if not installed_manifest.is_file():
        return PluginStatus(
            state=PluginInstallState.NOT_INSTALLED,
            bundled_version=bundled_version,
        )

    installed_version = read_plugin_version(installed_manifest)
    if not bundled_version or not installed_version:
        return PluginStatus(
            state=PluginInstallState.UNKNOWN,
            bundled_version=bundled_version,
            installed_version=installed_version,
        )

    try:
        cmp = compare_plugin_versions(bundled_version, installed_version)
    except InvalidVersion:
        return PluginStatus(
            state=PluginInstallState.UNKNOWN,
            bundled_version=bundled_version,
            installed_version=installed_version,
        )

    if cmp < 0:
        return PluginStatus(
            state=PluginInstallState.OUTDATED,
            bundled_version=bundled_version,
            installed_version=installed_version,
        )
    return PluginStatus(
        state=PluginInstallState.UP_TO_DATE,
        bundled_version=bundled_version,
        installed_version=installed_version,
    )


def format_plugin_status_text(status: PluginStatus) -> str:
    if status.state == PluginInstallState.UNAVAILABLE:
        return "Stream Deck not detected"
    if status.state == PluginInstallState.NOT_INSTALLED:
        return "Not installed"
    if status.state == PluginInstallState.OUTDATED:
        installed = status.installed_version or "?"
        bundled = status.bundled_version or "?"
        return f"Out of date — installed v{installed}, bundled v{bundled}"
    if status.state == PluginInstallState.UP_TO_DATE:
        version = status.installed_version or status.bundled_version or "?"
        return f"Up to date (v{version})"
    installed = status.installed_version or "?"
    return f"Installed (v{installed}, version check inconclusive)"


def get_install_button_label(status: PluginStatus) -> str:
    if status.state == PluginInstallState.NOT_INSTALLED:
        return "Install Plugin"
    if status.is_installed or status.state == PluginInstallState.OUTDATED:
        return "Reinstall Plugin"
    return "Install Plugin"


def maybe_notify_streamdeck_plugin_outdated() -> None:
    """Warn once per dedupe window when the installed plugin is older than bundled."""
    status = get_plugin_status()
    if status.state != PluginInstallState.OUTDATED:
        return
    notify(
        "Stream Deck plugin is out of date. Reinstall it from App Settings "
        "(Settings → App Settings), then restart Stream Deck. "
        "Open the notification center to jump there.",
        type="warning",
        dedupe_key=OUTDATED_NOTIFY_DEDUPE_KEY,
        dedupe_cooldown_sec=OUTDATED_NOTIFY_COOLDOWN_SEC,
        actions=nav_actions_settings("App Settings"),
        timeout=12,
    )
