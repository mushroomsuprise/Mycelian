# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Install the bundled mycelian-stats Factorio mod into the user's mods folder."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..path_utils import get_data_path, get_resource_path
from .factorio_hook import process_exe_path, resolve_data_dir

logger = logging.getLogger(__name__)

MOD_NAME = "mycelian-stats"

# First line of factorio-current.log, e.g. "0.000 2026-10-29 19:39:30; Factorio 2.1.24 (build ...)"
_GAME_VERSION_RE = re.compile(r"Factorio (\d+)\.(\d+)\.\d+")


def bundled_mod_dir() -> Optional[Path]:
    candidates = [
        Path(get_data_path(os.path.join("factorio_mod", MOD_NAME))),
        Path(get_resource_path(os.path.join("factorio_mod", MOD_NAME))),
        Path(__file__).resolve().parents[2] / "factorio_mod" / MOD_NAME,
    ]
    for path in candidates:
        if (path / "info.json").is_file():
            return path
    return None


def _read_info_field(info_path: Path, key: str) -> str:
    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if isinstance(data, dict):
        return str(data.get(key) or "").strip()
    return ""


def read_info_version(info_path: Path) -> str:
    return _read_info_field(info_path, "version")


def read_info_factorio_version(info_path: Path) -> str:
    return _read_info_field(info_path, "factorio_version")


def _version_key(version: str) -> Tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return ()


def game_version(data_dir: Optional[Path]) -> str:
    """Major.minor of the Factorio that last ran with this user-data folder, or "".

    Read from the head of factorio-current.log, which the game rewrites on every launch.
    """
    if data_dir is None:
        return ""
    try:
        with open(Path(data_dir) / "factorio-current.log", encoding="utf-8", errors="replace") as log:
            head = "".join(log.readline() for _ in range(5))
    except OSError:
        return ""
    match = _GAME_VERSION_RE.search(head)
    return f"{match.group(1)}.{match.group(2)}" if match else ""


def target_factorio_version(bundled: str, game: str) -> str:
    """The ``factorio_version`` to install with.

    Factorio only loads a mod whose ``factorio_version`` matches its own major.minor
    (2.1 rejects a 2.0 mod), so a newer game gets its own version. An older game keeps
    the bundled one: the mod uses that version's API.
    """
    if game and bundled and _version_key(game) > _version_key(bundled):
        return game
    return bundled


def bundled_version() -> str:
    bundled = bundled_mod_dir()
    if bundled is None:
        return ""
    return read_info_version(bundled / "info.json")


def mods_dir_for(data_dir: Optional[str] = None) -> Optional[Path]:
    if not data_dir:
        data_dir = resolve_data_dir(exe_path=process_exe_path())
    if not data_dir:
        return None
    return Path(data_dir) / "mods"


def _installed_entries(mods_dir: Path) -> List[Path]:
    if not mods_dir.is_dir():
        return []
    matches: List[Path] = []
    for child in mods_dir.iterdir():
        name = child.name
        if name == MOD_NAME or name.startswith(MOD_NAME + "_"):
            matches.append(child)
    return matches


def installed_version(mods_dir: Optional[Path] = None) -> str:
    if mods_dir is None:
        mods_dir = mods_dir_for()
    if mods_dir is None:
        return ""
    best = ""
    for entry in _installed_entries(mods_dir):
        info = entry / "info.json" if entry.is_dir() else None
        if info and info.is_file():
            version = read_info_version(info)
            if version:
                best = version
                continue
        # Zip or folder named mycelian-stats_1.2.3
        stem = entry.name
        if stem.endswith(".zip"):
            stem = stem[:-4]
        prefix = MOD_NAME + "_"
        if stem.startswith(prefix):
            best = stem[len(prefix) :]
    return best


def installed_factorio_version(mods_dir: Optional[Path]) -> str:
    """``factorio_version`` of an installed mod folder ("" for a zip or when unknown)."""
    if mods_dir is None:
        return ""
    for entry in _installed_entries(mods_dir):
        info = entry / "info.json"
        if entry.is_dir() and info.is_file():
            found = read_info_factorio_version(info)
            if found:
                return found
    return ""


def _factorio_update(mods_dir: Optional[Path]) -> str:
    """The Factorio version to reinstall for when the installed copy targets another one."""
    if mods_dir is None:
        return ""
    bundled = bundled_mod_dir()
    if bundled is None:
        return ""
    target = target_factorio_version(
        read_info_factorio_version(bundled / "info.json"), game_version(mods_dir.parent)
    )
    have = installed_factorio_version(mods_dir)
    return target if target and have and have != target else ""


def _state_for(want: str, have: str, factorio_update: str = "") -> str:
    if not have:
        return "not_installed"
    if want and have != want:
        return "outdated"
    if factorio_update:
        return "outdated"
    return "current"


def _lookup(mods_dir: Optional[Path]) -> Tuple[str, str, str, str]:
    """(state, installed, bundled, factorio_update) for one resolved mods folder."""
    want = bundled_version()
    have = installed_version(mods_dir)
    factorio = _factorio_update(mods_dir)
    return _state_for(want, have, factorio), have, want, factorio


def detect_state(data_dir: Optional[str] = None) -> str:
    """Return not_installed | outdated | current."""
    return _lookup(mods_dir_for(data_dir))[0]


def install_status() -> Tuple[str, str, str]:
    """Return (state, detail text, tone) from one bundled/installed lookup.

    Same values as ``detect_state()`` plus ``version_label()``, without resolving the
    mods folder (a process scan) three times.
    """
    state, have, want, factorio = _lookup(mods_dir_for())
    detail, tone = version_label(installed=have, bundled=want, state=state, factorio_update=factorio)
    return state, detail, tone


def version_label(
    installed: Optional[str] = None,
    bundled: Optional[str] = None,
    state: Optional[str] = None,
    factorio_update: Optional[str] = None,
) -> Tuple[str, str]:
    """Return (detail text, tone) for the Game Hooks Install/Update row."""
    have = (installed if installed is not None else installed_version()).strip()
    want = (bundled if bundled is not None else bundled_version()).strip()
    status = state if state is not None else detect_state()
    if factorio_update is None:
        factorio_update = _factorio_update(mods_dir_for())
    if status == "not_installed" or not have:
        extra = f" · bundled {want}" if want else ""
        return f"not installed{extra}", "muted"
    if status == "outdated":
        if want and have != want:
            hint = f"  ·  new version {want}"
        elif factorio_update:
            hint = f"  ·  update for Factorio {factorio_update}"
        else:
            hint = ""
        return f"v{have}{hint}", "warning"
    return f"v{have}", "ok"


def merge_mod_list(
    data: Any,
    name: str = MOD_NAME,
    enabled: bool = True,
) -> Dict[str, Any]:
    """Insert or enable ``name`` in a Factorio mod-list.json object."""
    if not isinstance(data, dict):
        data = {}
    mods = data.get("mods")
    if not isinstance(mods, list):
        mods = []
        data["mods"] = mods
    for entry in mods:
        if isinstance(entry, dict) and str(entry.get("name") or "") == name:
            entry["enabled"] = bool(enabled)
            return data
    mods.append({"name": name, "enabled": bool(enabled)})
    return data


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(path))


def _enable_in_mod_list(mods_dir: Path) -> None:
    list_path = mods_dir / "mod-list.json"
    existing: Any = {}
    if list_path.is_file():
        try:
            existing = json.loads(list_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Could not parse Factorio mod-list.json: %s", e)
            existing = {}
    merged = merge_mod_list(existing, MOD_NAME, True)
    _atomic_write_json(list_path, merged)


def install_mod(data_dir: Optional[str] = None) -> Tuple[bool, str]:
    """Copy the bundled mod into ``<data_dir>/mods/mycelian-stats_<version>/``."""
    bundled = bundled_mod_dir()
    if bundled is None:
        return False, "Bundled mycelian-stats mod is missing from this Mycelian install"
    version = read_info_version(bundled / "info.json") or "1.0.0"
    if data_dir is None:
        data_dir = resolve_data_dir(exe_path=process_exe_path())
    if not data_dir:
        return False, "Factorio user-data folder not found"
    mods_dir = Path(data_dir) / "mods"
    try:
        mods_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"Could not create mods folder: {e}"

    dest = mods_dir / f"{MOD_NAME}_{version}"
    try:
        for old in _installed_entries(mods_dir):
            if old.resolve() == dest.resolve():
                continue
            if old.is_dir():
                shutil.rmtree(old)
            else:
                old.unlink()
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(bundled, dest)
        _match_game_version(dest, Path(data_dir))
        _enable_in_mod_list(mods_dir)
    except OSError as e:
        logger.warning("Factorio mod install failed: %s", e, exc_info=True)
        return False, f"Could not install mod: {e}"
    return (
        True,
        "Installed mycelian-stats "
        f"{version}. Restart Factorio (quit fully if it is running) so the mod loads.",
    )


def _match_game_version(mod_dir: Path, data_dir: Path) -> None:
    """Point the installed copy's ``factorio_version`` at a newer game so it loads."""
    info_path = mod_dir / "info.json"
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(info, dict):
        return
    bundled = str(info.get("factorio_version") or "").strip()
    target = target_factorio_version(bundled, game_version(data_dir))
    if target == bundled:
        return
    info["factorio_version"] = target
    _atomic_write_json(info_path, info)
    logger.info("Factorio mod: factorio_version %s -> %s to match the game", bundled, target)
