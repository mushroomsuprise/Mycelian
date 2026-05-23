"""Stream Deck plugin install path and version helpers."""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import platform
import shutil
import stat
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from packaging.version import InvalidVersion, Version, parse as parse_version

from .notification_engine import nav_actions_settings, notify
from .path_utils import get_data_path

logger = logging.getLogger(__name__)

PLUGIN_BUNDLE_NAME = "com.mushroomsuprise.mycelian.sdPlugin"


def enqueue_streamdeck_connector_event(event_data: Dict[str, Any]) -> bool:
    """
    Schedule a connector event on the ConnectorProcessor asyncio loop.

    Stream Deck HTTP handlers run on the Flask/gevent thread; connector
    queue processing lives on a dedicated thread (see ConnectorManager).
    """
    from .connector_integration import get_integration
    from .obs_service import obs_service

    loop = obs_service._connector_loop
    if loop is None or not loop.is_running():
        logger.debug(
            "Stream Deck connector enqueue skipped: connector loop not ready"
        )
        return False
    try:
        asyncio.run_coroutine_threadsafe(
            get_integration().manager.add_event(event_data), loop
        )
        return True
    except Exception as e:
        logger.debug("Stream Deck connector enqueue failed: %s", e)
        return False


OUTDATED_NOTIFY_DEDUPE_KEY = "streamdeck:plugin_outdated"
OUTDATED_NOTIFY_COOLDOWN_SEC = 86400.0
_STREAMDECK_QUIT_HINT = (
    "Quit the Stream Deck app completely (macOS: Stream Deck → Quit Stream Deck), "
    "then click Reinstall Plugin again. Stream Deck locks plugin files while it is running."
)


class PluginInstallError(Exception):
    """Install failed; ``user_message`` is safe to show in the UI."""

    def __init__(self, message: str, *, user_message: Optional[str] = None) -> None:
        super().__init__(message)
        self.user_message = user_message or message


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


def resolve_streamdeck_plugins_dir(*, create: bool = False) -> Optional[Path]:
    """OS-specific Elgato Stream Deck plugins directory path."""
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
    if create:
        plugins_dir.mkdir(parents=True, exist_ok=True)
    return plugins_dir


def get_streamdeck_plugins_dir() -> Optional[Path]:
    """Stream Deck plugins directory if Elgato has created it."""
    plugins_dir = resolve_streamdeck_plugins_dir(create=False)
    if plugins_dir is None or not plugins_dir.exists():
        return None
    return plugins_dir


def _is_permission_denied(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError) and exc.errno in (errno.EACCES, errno.EPERM):
        return True
    return False


def _chmod_writable(path: str) -> None:
    try:
        mode = os.stat(path).st_mode
        os.chmod(path, mode | stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    except OSError:
        pass


def _rmtree_onerror(func: Callable[..., None], path: str, exc_info) -> None:
    """Retry tree removal after clearing read-only flags (Windows / locked files)."""
    exc = exc_info[1]
    if exc is not None and _is_permission_denied(exc):
        _chmod_writable(path)
        try:
            func(path)
            return
        except Exception:
            pass
    raise exc  # type: ignore[misc]


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    shutil.rmtree(path, onerror=_rmtree_onerror)


def _remove_plugin_directory(plugin_dir: Path) -> None:
    """
    Remove an existing .sdPlugin bundle.

    Stream Deck often locks ``bin/plugin.js`` while running; try rmtree with
    chmod retry, then rename-aside so a fresh copy can land in place.
    """
    if not plugin_dir.exists():
        return

    try:
        _remove_tree(plugin_dir)
        return
    except (PermissionError, OSError) as e:
        if not _is_permission_denied(e):
            raise
        logger.warning(
            "Could not delete Stream Deck plugin folder (likely in use): %s",
            plugin_dir,
            exc_info=True,
        )

    backup = plugin_dir.parent / f"{plugin_dir.name}.mycelian-old-{int(time.time())}"
    try:
        plugin_dir.rename(backup)
    except (PermissionError, OSError) as e:
        raise PluginInstallError(
            f"Could not replace locked plugin directory: {plugin_dir}",
            user_message=_STREAMDECK_QUIT_HINT,
        ) from e

    try:
        _remove_tree(backup)
    except (PermissionError, OSError):
        logger.info(
            "Previous plugin copy left at %s (remove manually after quitting Stream Deck)",
            backup,
        )


def _cleanup_stale_plugin_backups(plugins_dir: Path) -> None:
    prefix = f"{PLUGIN_BUNDLE_NAME}.mycelian-old-"
    for entry in plugins_dir.iterdir():
        if entry.is_dir() and entry.name.startswith(prefix):
            try:
                _remove_tree(entry)
            except (PermissionError, OSError):
                pass


@dataclass(frozen=True)
class PluginInstallResult:
    destination: Path
    replaced_existing: bool


def install_bundled_plugin() -> PluginInstallResult:
    """
    Copy the bundled Mycelian Stream Deck plugin into Elgato's plugins folder.

    Raises PluginInstallError on failure.
    """
    source_dir = get_bundled_plugin_dir()
    if not source_dir.is_dir():
        raise PluginInstallError(
            f"Bundled plugin missing: {source_dir}",
            user_message=(
                "Plugin source files not found. Reinstall Mycelian or restore the "
                "sd_plugin folder."
            ),
        )

    plugins_dir = resolve_streamdeck_plugins_dir(create=True)
    if plugins_dir is None:
        raise PluginInstallError(
            "Stream Deck plugins path is not supported on this OS",
            user_message="Stream Deck plugin install is only supported on macOS and Windows.",
        )

    destination_dir = plugins_dir / PLUGIN_BUNDLE_NAME
    replaced_existing = destination_dir.exists()

    _cleanup_stale_plugin_backups(plugins_dir)
    if replaced_existing:
        _remove_plugin_directory(destination_dir)

    try:
        shutil.copytree(source_dir, destination_dir)
    except (PermissionError, OSError) as e:
        if _is_permission_denied(e):
            raise PluginInstallError(
                f"Permission denied copying plugin to {destination_dir}",
                user_message=_STREAMDECK_QUIT_HINT,
            ) from e
        raise PluginInstallError(
            f"Failed to copy plugin: {e}",
            user_message=f"Could not copy plugin files: {e}",
        ) from e

    return PluginInstallResult(
        destination=destination_dir,
        replaced_existing=replaced_existing,
    )


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


@dataclass(frozen=True)
class PluginStatusDisplay:
    """Structured labels for the App Settings Stream Deck section."""

    status_text: str
    installed_version: Optional[str] = None
    new_version_available: Optional[str] = None


def get_plugin_status_display(status: PluginStatus) -> PluginStatusDisplay:
    if status.state == PluginInstallState.UNAVAILABLE:
        return PluginStatusDisplay(status_text="Stream Deck not detected")
    if status.state == PluginInstallState.NOT_INSTALLED:
        return PluginStatusDisplay(status_text="Not installed")
    installed = status.installed_version or status.bundled_version
    if status.state == PluginInstallState.OUTDATED:
        return PluginStatusDisplay(
            status_text="Installed",
            installed_version=installed,
            new_version_available=status.bundled_version,
        )
    if status.state == PluginInstallState.UP_TO_DATE:
        return PluginStatusDisplay(
            status_text="Installed",
            installed_version=installed,
        )
    return PluginStatusDisplay(
        status_text="Installed",
        installed_version=installed,
    )


def format_plugin_status_text(status: PluginStatus) -> str:
    """Single-line summary (legacy); prefer get_plugin_status_display for UI."""
    display = get_plugin_status_display(status)
    parts = [display.status_text]
    if display.installed_version:
        parts.append(f"v{display.installed_version}")
    if display.new_version_available:
        parts.append(f"(update available: v{display.new_version_available})")
    return " · ".join(parts)


def get_install_button_label(status: PluginStatus) -> str:
    if status.state == PluginInstallState.NOT_INSTALLED:
        return "Install Plugin"
    if status.is_installed or status.state == PluginInstallState.OUTDATED:
        return "Reinstall Plugin"
    return "Install Plugin"


def maybe_notify_streamdeck_plugin_outdated() -> bool:
    """
    Warn when the installed plugin is older than bundled.

    Returns True if a notification was shown (not deduped or skipped).
    """
    status = get_plugin_status()
    if status.state != PluginInstallState.OUTDATED:
        return False
    bundled = status.bundled_version or "?"
    entry_id = notify(
        f"A new Stream Deck plugin version (v{bundled}) is available. "
        "Reinstall from Settings → App Settings, then restart Stream Deck. "
        "Click this in the notification center to open App Settings.",
        type="warning",
        dedupe_key=OUTDATED_NOTIFY_DEDUPE_KEY,
        dedupe_cooldown_sec=OUTDATED_NOTIFY_COOLDOWN_SEC,
        actions=nav_actions_settings("App Settings"),
        timeout=12,
    )
    if entry_id:
        logger.info(
            "Stream Deck plugin update notification shown (installed=%s, bundled=%s)",
            status.installed_version,
            status.bundled_version,
        )
    return entry_id is not None
