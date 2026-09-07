#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Read OBS ExtraBrowserDocks and recover Mycelian custom browser docks.

OBS has no WebSocket request to refresh Custom Browser Docks (View → Docks).
Scene browser sources use PressInputPropertiesButton; docks do not appear in
get_input_list. Failed dock loads navigate to Chrome's error page
(``chrome-error://``). Cmd/Ctrl+R reloads that error document, not the
configured Mycelian URL, so it cannot recover an OBS-first start.

This module:

- Parses ExtraBrowserDocks from OBS ``user.ini`` / ``global.ini``.
- Matches only docks whose URL is a registered Mycelian overlay route.
- When OBS is not running, retargets those docks to a local boot HTML page so
  the next OBS-first start waits for Mycelian instead of failing.
- When OBS is already open on a failed dock, tells the user to restart OBS
  while Mycelian is running (the in-memory dock URL is re-read on launch).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger(__name__)

_OBS_CONFIG_FILENAMES = ("user.ini", "global.ini")
_OBS_PROCESS_STEMS = frozenset({"obs", "obs64", "obs32", "obsstudio", "obs-studio", "obs studio"})
_DOCK_BOOT_FILENAME = "obs_dock_boot.html"
_OBS_EXIT_FLUSH_SEC = 3.0
_dock_boot_context_lock = threading.Lock()
_dock_boot_port: Optional[int] = None
_dock_boot_routes: List[str] = []
_obs_exit_watcher_lock = threading.Lock()
_obs_exit_watcher_started = False

_DOCK_BOOT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Mycelian</title>
  <style>
    html, body { margin: 0; background: #141414; color: #c8c8c8;
      font: 13px/1.4 -apple-system, BlinkMacSystemFont, sans-serif; }
    p { padding: 16px; }
  </style>
</head>
<body>
  <p id="s">Waiting for Mycelian…</p>
  <script>
  (function () {
    var params = new URLSearchParams(location.search);
    var port = parseInt(params.get("port") || "", 10);
    var route = (params.get("route") || "").replace(/^\\/+/, "");
    var hash = (location.hash || "").replace(/^#/, "");
    if (hash) {
      var slash = hash.indexOf("/");
      if (slash > 0) {
        var hp = parseInt(hash.slice(0, slash), 10);
        var hr = hash.slice(slash + 1).replace(/^\\/+/, "");
        if (hp) port = hp;
        if (hr) route = hr;
      }
    }
    if (!port) port = 5000;
    var status = document.getElementById("s");
    if (!route) {
      if (status) status.textContent = "Missing Mycelian dock route.";
      return;
    }
    var target = "http://127.0.0.1:" + port + "/" + route;
    var pingUrl = "http://127.0.0.1:" + port + "/api/dock-boot.js?t=";
    function go() { location.replace(target); }
    function ping() {
      var s = document.createElement("script");
      s.src = pingUrl + Date.now();
      s.onload = go;
      s.onerror = function () {
        fetch(pingUrl + Date.now(), {mode: "no-cors", cache: "no-store"})
          .then(go)
          .catch(function () { setTimeout(ping, 1000); });
      };
      document.head.appendChild(s);
    }
    ping();
  })();
  </script>
</body>
</html>
"""


def unescape_obs_ini_value(value: str) -> str:
    """Undo libobs config_save escaping (``\\\\``, ``\\n``, ``\\r``)."""
    out: List[str] = []
    i = 0
    raw = str(value or "")
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            if nxt == "\\":
                out.append("\\")
                i += 2
                continue
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "r":
                out.append("\r")
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_extra_browser_docks(ini_text: str) -> List[Dict[str, str]]:
    """Return ExtraBrowserDocks entries from an OBS ini document."""
    docks: List[Dict[str, str]] = []
    for raw_line in str(ini_text or "").splitlines():
        stripped = raw_line.lstrip()
        if not stripped.startswith("ExtraBrowserDocks="):
            continue
        payload = unescape_obs_ini_value(stripped.split("=", 1)[1].strip())
        if not payload:
            continue
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            logger.debug("OBS ExtraBrowserDocks JSON could not be parsed")
            continue
        if not isinstance(parsed, list):
            continue
        for row in parsed:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            url = str(row.get("url") or "").strip()
            uuid = str(row.get("uuid") or "").strip()
            if not url:
                continue
            docks.append({"title": title, "url": url, "uuid": uuid})
    return docks


def escape_obs_ini_value(value: str) -> str:
    """libobs config_save escaping for a single-line ini value."""
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def mycelian_app_support_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Mycelian"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or ""
        if appdata:
            return Path(appdata) / "Mycelian"
        return Path.home() / "AppData" / "Roaming" / "Mycelian"
    xdg = os.environ.get("XDG_CONFIG_HOME") or ""
    if xdg:
        return Path(xdg) / "mycelian"
    return Path.home() / ".config" / "mycelian"


def dock_boot_html_path() -> Path:
    return mycelian_app_support_dir() / _DOCK_BOOT_FILENAME


def ensure_dock_boot_html() -> Path:
    """Write the OBS-first dock waiter page to a stable user-data path."""
    path = dock_boot_html_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_DOCK_BOOT_HTML, encoding="utf-8")
    return path


def dock_boot_file_url(overlay_port: int, route: str) -> str:
    path = ensure_dock_boot_html()
    route_s = str(route).strip().strip("/")
    port = int(overlay_port)
    # Hash survives CEF builds that drop file:// query strings.
    return f"{path.as_uri()}?port={port}&route={route_s}#{port}/{route_s}"


def is_dock_boot_file_url(url: Any) -> bool:
    raw = str(url or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    if parsed.scheme != "file":
        return False
    name = Path(unquote(parsed.path or "")).name.lower()
    return name == _DOCK_BOOT_FILENAME


def mycelian_dock_route_from_url(
    url: Any,
    overlay_port: Any,
    template_routes: Any,
) -> Optional[str]:
    """Template route for a Mycelian overlay URL or local dock-boot file URL."""
    from .obs_browser_source_match import overlay_template_route_from_url

    routes = [
        str(route).strip()
        for route in (template_routes or [])
        if str(route).strip()
    ]
    port: Optional[int] = None
    if overlay_port is not None and overlay_port != "":
        try:
            port = int(overlay_port)
        except (TypeError, ValueError):
            port = None
    raw = str(url or "").strip()
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    if parsed.scheme == "file":
        if not is_dock_boot_file_url(raw) or port is None:
            return None
        qs = parse_qs(parsed.query)
        route = str((qs.get("route") or [""])[0]).strip().strip("/")
        boot_port: Optional[int] = None
        try:
            boot_port = int(str((qs.get("port") or [""])[0]).strip())
        except (TypeError, ValueError):
            boot_port = None
        frag = str(parsed.fragment or "").strip()
        if frag and "/" in frag:
            frag_port_s, frag_route = frag.split("/", 1)
            try:
                frag_port = int(frag_port_s.strip())
            except (TypeError, ValueError):
                frag_port = None
            frag_route = frag_route.strip().strip("/")
            if frag_port is not None:
                boot_port = frag_port
            if frag_route:
                route = frag_route
        if boot_port is None or boot_port != port:
            return None
        return route if route in routes else None
    return overlay_template_route_from_url(raw, port, routes)


def set_dock_boot_context(overlay_port: Any, template_routes: Any) -> None:
    """Remember overlay port/routes so OBS-exit can retarget ExtraBrowserDocks."""
    global _dock_boot_port, _dock_boot_routes
    port: Optional[int] = None
    if overlay_port is not None and overlay_port != "":
        try:
            port = int(overlay_port)
        except (TypeError, ValueError):
            port = None
    routes = [
        str(route).strip()
        for route in (template_routes or [])
        if str(route).strip()
    ]
    with _dock_boot_context_lock:
        _dock_boot_port = port
        _dock_boot_routes = routes
    ensure_obs_exit_dock_boot_watcher()


def replace_extra_browser_docks_value(ini_text: str, docks: Sequence[Dict[str, str]]) -> str:
    """Replace the ExtraBrowserDocks= line with compact JSON (uuid/title/url only)."""
    payload = json.dumps(
        [
            {
                "title": str(row.get("title") or ""),
                "url": str(row.get("url") or ""),
                "uuid": str(row.get("uuid") or ""),
            }
            for row in docks
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    escaped = escape_obs_ini_value(payload)
    out: List[str] = []
    replaced = False
    for line in str(ini_text or "").splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("ExtraBrowserDocks="):
            indent = line[: len(line) - len(line.lstrip())]
            newline = "\n" if line.endswith("\n") else ""
            out.append(f"{indent}ExtraBrowserDocks={escaped}{newline}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        return str(ini_text or "")
    return "".join(out)


def retarget_mycelian_docks_to_boot(
    docks: Sequence[Dict[str, str]],
    overlay_port: int,
    template_routes: Any,
) -> List[Dict[str, str]]:
    """Point Mycelian http(s) docks at the local boot file; leave others unchanged."""
    updated: List[Dict[str, str]] = []
    changed = False
    for row in docks:
        item = {
            "title": str(row.get("title") or ""),
            "url": str(row.get("url") or ""),
            "uuid": str(row.get("uuid") or ""),
        }
        route = mycelian_dock_route_from_url(
            item["url"], overlay_port, template_routes
        )
        if route and not is_dock_boot_file_url(item["url"]):
            item["url"] = dock_boot_file_url(overlay_port, route)
            changed = True
        updated.append(item)
    return updated if changed else list(docks)


def migrate_extra_browser_docks_to_boot(
    overlay_port: Any,
    template_routes: Any,
    *,
    config_paths: Optional[Sequence[Path]] = None,
) -> int:
    """Rewrite Mycelian ExtraBrowserDocks to file:// boot URLs. OBS must not be running."""
    if obs_process_is_running():
        return 0
    port: Optional[int] = None
    if overlay_port is not None and overlay_port != "":
        try:
            port = int(overlay_port)
        except (TypeError, ValueError):
            port = None
    if port is None:
        return 0
    routes = [
        str(route).strip()
        for route in (template_routes or [])
        if str(route).strip()
    ]
    if not routes:
        return 0
    ensure_dock_boot_html()
    changed_files = 0
    paths = list(config_paths) if config_paths is not None else iter_obs_config_paths()
    for path in paths:
        try:
            if not path.is_file():
                continue
            original = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        docks = parse_extra_browser_docks(original)
        if not docks:
            continue
        updated = retarget_mycelian_docks_to_boot(docks, port, routes)
        if updated == docks:
            continue
        rewritten = replace_extra_browser_docks_value(original, updated)
        if rewritten == original:
            continue
        try:
            path.write_text(rewritten, encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not retarget OBS docks in %s: %s", path, exc)
            continue
        changed_files += 1
        logger.warning(
            "Retargeted Mycelian OBS docks in %s to a local boot page "
            "(OBS was not running)",
            path.name,
        )
    return changed_files


def schedule_dock_boot_migrate_if_obs_exits() -> None:
    """After OBS websocket drop, retarget docks once the OBS process is gone."""
    threading.Thread(
        target=_dock_boot_migrate_after_obs_exit,
        name="MycelianOBSDockBootMigrate",
        daemon=True,
    ).start()


def _dock_boot_context() -> tuple[Optional[int], List[str]]:
    with _dock_boot_context_lock:
        return _dock_boot_port, list(_dock_boot_routes)


def _try_migrate_dock_boot_now() -> int:
    port, routes = _dock_boot_context()
    if port is None or not routes:
        return 0
    try:
        return int(migrate_extra_browser_docks_to_boot(port, routes) or 0)
    except Exception as exc:
        logger.debug("OBS dock boot migrate failed: %s", exc)
        return 0


def ensure_obs_exit_dock_boot_watcher() -> None:
    """Retarget Mycelian docks to the boot page every time OBS quits."""
    global _obs_exit_watcher_started
    with _obs_exit_watcher_lock:
        if _obs_exit_watcher_started:
            return
        _obs_exit_watcher_started = True
    threading.Thread(
        target=_obs_exit_dock_boot_loop,
        name="MycelianOBSDockBootWatch",
        daemon=True,
    ).start()


def _obs_exit_dock_boot_loop() -> None:
    was_running = False
    try:
        was_running = obs_process_is_running()
    except Exception:
        was_running = False
    while True:
        try:
            time.sleep(1.5)
            running = obs_process_is_running()
            if was_running and not running:
                time.sleep(_OBS_EXIT_FLUSH_SEC)
                if not obs_process_is_running():
                    changed = _try_migrate_dock_boot_now()
                    if changed:
                        logger.warning(
                            "Retargeted Mycelian OBS docks to a local boot page "
                            "after OBS quit"
                        )
                running = obs_process_is_running()
            was_running = running
        except Exception:
            logger.debug("OBS dock boot watcher iteration failed", exc_info=True)
            time.sleep(2.0)


def _dock_boot_migrate_after_obs_exit() -> None:
    # OBS writes ExtraBrowserDocks on a clean quit; retry until that flush lands.
    for _ in range(3):
        time.sleep(2.0)
        if obs_process_is_running():
            return
        if _try_migrate_dock_boot_now():
            return


def iter_obs_config_paths() -> List[Path]:
    """Likely OBS Studio config files that may contain ExtraBrowserDocks."""
    home = Path.home()
    dirs: List[Path] = []
    if sys.platform == "darwin":
        dirs.append(home / "Library" / "Application Support" / "obs-studio")
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or ""
        if appdata:
            dirs.append(Path(appdata) / "obs-studio")
    else:
        dirs.append(home / ".config" / "obs-studio")
        dirs.append(
            home
            / ".var"
            / "app"
            / "com.obsproject.Studio"
            / "config"
            / "obs-studio"
        )
        dirs.append(
            home / "snap" / "obs-studio" / "current" / ".config" / "obs-studio"
        )
    paths: List[Path] = []
    seen: Set[str] = set()
    for folder in dirs:
        for name in _OBS_CONFIG_FILENAMES:
            path = folder / name
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
    return paths


def load_extra_browser_docks(
    *,
    ini_text: Optional[str] = None,
    config_paths: Optional[Sequence[Path]] = None,
) -> List[Dict[str, str]]:
    """Load ExtraBrowserDocks from *ini_text* or OBS config files on disk."""
    if ini_text is not None:
        return parse_extra_browser_docks(ini_text)
    found: List[Dict[str, str]] = []
    seen: Set[str] = set()
    paths = list(config_paths) if config_paths is not None else iter_obs_config_paths()
    for path in paths:
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        for dock in parse_extra_browser_docks(text):
            key = dock.get("uuid") or f"{dock.get('title')}|{dock.get('url')}"
            if key in seen:
                continue
            seen.add(key)
            found.append(dock)
    return found


def list_mycelian_extra_browser_docks(
    overlay_port: Any,
    template_routes: Any,
    *,
    ini_text: Optional[str] = None,
    config_paths: Optional[Sequence[Path]] = None,
) -> List[Dict[str, str]]:
    """Mycelian-only ExtraBrowserDocks (local overlay host + registered route)."""
    port: Optional[int] = None
    if overlay_port is not None and overlay_port != "":
        try:
            port = int(overlay_port)
        except (TypeError, ValueError):
            port = None
    routes = [
        str(route).strip()
        for route in (template_routes or [])
        if str(route).strip()
    ]
    matched: List[Dict[str, str]] = []
    for dock in load_extra_browser_docks(
        ini_text=ini_text, config_paths=config_paths
    ):
        route = mycelian_dock_route_from_url(dock.get("url"), port, routes)
        if not route:
            continue
        matched.append(
            {
                "title": dock.get("title") or "",
                "url": dock.get("url") or "",
                "uuid": dock.get("uuid") or "",
                "route": route,
            }
        )
    return matched


def _is_obs_main_process_name(name: str) -> bool:
    n = str(name or "").lower()
    if "helper" in n:
        return False
    stem = Path(name).stem.lower()
    if stem in _OBS_PROCESS_STEMS:
        return True
    compact = stem.replace(" ", "").replace("-", "")
    return compact in {"obs", "obsstudio", "obs64", "obs32"}


def obs_process_is_running() -> bool:
    """True when an OBS Studio main process appears in the process list."""
    try:
        import psutil
    except Exception:
        return False
    try:
        for proc in psutil.process_iter(["name"]):
            name = str(proc.info.get("name") or "")
            if _is_obs_main_process_name(name):
                return True
    except Exception:
        return False
    return False


def obs_appears_running() -> bool:
    """True if the OBS websocket is up or an OBS process is running."""
    try:
        from .obs_service import obs_service

        if obs_service.is_connected():
            return True
    except Exception:
        pass
    return obs_process_is_running()


def _pending_http_dock_titles(pending: Sequence[Dict[str, str]]) -> List[str]:
    titles: List[str] = []
    seen: Set[str] = set()
    for dock in pending:
        if is_dock_boot_file_url(dock.get("url")):
            continue
        title = str(dock.get("title") or dock.get("route") or "").strip()
        if not title:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        titles.append(title)
    return titles


def _notify_restart_obs_for_docks(titles: Sequence[str]) -> None:
    if not titles:
        return
    if len(titles) == 1:
        label = titles[0]
    elif len(titles) == 2:
        label = f"{titles[0]} and {titles[1]}"
    else:
        label = ", ".join(titles[:-1]) + f", and {titles[-1]}"
    message = (
        f"{label} could not load because OBS started before Mycelian. "
        "Keep Mycelian running and restart OBS. Cmd+R on that error page "
        "does not reload the Mycelian URL."
    )
    try:
        from .notification_engine import notify

        notify(
            message,
            type="warning",
            timeout=14.0,
            dedupe_key="obs:docks:restart-obs",
            dedupe_cooldown_sec=120.0,
        )
    except Exception as exc:
        logger.debug("OBS dock restart notify failed: %s", exc)


def wake_mycelian_browser_docks(
    pending: Sequence[Dict[str, str]],
) -> List[str]:
    """OBS cannot refresh custom docks over WebSocket, and Cmd/Ctrl+R cannot
    recover Chrome's error page. Tell the user to restart OBS instead.
    """
    pending_list = [dict(row) for row in pending if row.get("route")]
    titles = _pending_http_dock_titles(pending_list)
    if not titles:
        return []
    logger.warning(
        "OBS Mycelian docks are stuck on a failed load (%s). "
        "Cmd/Ctrl+R reloads Chrome's error page, not the Mycelian URL. "
        "Restart OBS while Mycelian is running. After OBS quits, those docks "
        "are pointed at a local boot page for the next OBS-first start.",
        titles,
    )
    _notify_restart_obs_for_docks(titles)
    return []


def pending_docks_missing_routes(
    docks: Iterable[Dict[str, str]],
    connected_routes: Iterable[str],
) -> List[Dict[str, str]]:
    """Docks whose overlay route has no Socket.IO hello yet."""
    connected = {str(route).strip().lower() for route in connected_routes if str(route).strip()}
    pending: List[Dict[str, str]] = []
    for dock in docks:
        route = str(dock.get("route") or "").strip()
        if not route:
            continue
        if route.lower() in connected:
            continue
        pending.append(dict(dock))
    return pending
