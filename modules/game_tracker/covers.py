# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Game cover art cached on disk (data/cache/game_covers) with a time to live.

Covers come from the platforms (PSN trophy list icon, Steam store header). Downloads run
on one background thread and never block the caller; a cover that fails is retried after
the shorter "missing" TTL. Each file is a JPEG at most ``MAX_WIDTH`` wide.
"""

from __future__ import annotations

import io
import json
import logging
import os
import queue
import re
import threading
import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

COVER_DIR = os.path.join("data", "cache", "game_covers")
INDEX_FILE = "index.json"
MAX_WIDTH = 600
DOWNLOAD_TIMEOUT_SEC = 15
MAX_BYTES = 8 * 1024 * 1024
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]")

DEFAULT_TTL_SEC = 30 * 24 * 3600
DEFAULT_MISSING_TTL_SEC = 6 * 3600

_lock = threading.Lock()
_index: Dict[str, Tuple[str, float, bool]] = {}  # key -> (source url, fetched at, ok)
_loaded_from: Optional[str] = None
_pending: set = set()
_queue: "queue.Queue[Tuple[str, str, str, str]]" = queue.Queue()
_worker: Optional[threading.Thread] = None
_ttl_sec = DEFAULT_TTL_SEC
_missing_ttl_sec = DEFAULT_MISSING_TTL_SEC
_listeners: List[Callable[[str], None]] = []
_fetcher: Optional[Callable[[str], bytes]] = None  # tests swap this
# platform -> fn(game_id) giving another URL to try when the usual one fails (Steam's newer
# games keep their art at hashed addresses only the store API knows).
_fallbacks: Dict[str, Callable[[str], Optional[str]]] = {}


def cover_dir() -> str:
    from ..path_utils import get_data_path

    return get_data_path(COVER_DIR)


def cover_key(platform: str, game_id: str) -> str:
    return f"{platform}_{_SAFE_ID_RE.sub('_', str(game_id))}"


def cover_path(platform: str, game_id: str) -> str:
    return os.path.join(cover_dir(), cover_key(platform, game_id) + ".jpg")


def set_ttl(days: float, missing_hours: float) -> None:
    global _ttl_sec, _missing_ttl_sec
    _ttl_sec = max(1, float(days)) * 24 * 3600
    _missing_ttl_sec = max(1, float(missing_hours)) * 3600


def set_fallback(platform: str, fn: Optional[Callable[[str], Optional[str]]]) -> None:
    if fn is None:
        _fallbacks.pop(platform, None)
    else:
        _fallbacks[platform] = fn


def add_listener(fn: Callable[[str], None]) -> None:
    """Called with the cover key after each successful download (from the fetch thread)."""
    if fn not in _listeners:
        _listeners.append(fn)


def _load_locked() -> None:
    global _loaded_from
    directory = cover_dir()
    if _loaded_from == directory:
        return
    _loaded_from = directory
    _index.clear()
    try:
        with open(os.path.join(directory, INDEX_FILE), "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return
    except Exception as e:
        logger.debug("Games Tracker: couldn't read the cover index: %s", e)
        return
    if isinstance(raw, dict):
        for key, entry in raw.items():
            try:
                _index[str(key)] = (str(entry[0] or ""), float(entry[1]), bool(entry[2]))
            except (TypeError, ValueError, IndexError):
                continue


def _save_index() -> None:
    with _lock:
        payload = {k: [u, t, ok] for k, (u, t, ok) in _index.items()}
    directory = cover_dir()
    path = os.path.join(directory, INDEX_FILE)
    tmp = path + ".tmp"
    try:
        os.makedirs(directory, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception as e:
        logger.debug("Games Tracker: couldn't save the cover index: %s", e)


def _fresh(entry: Tuple[str, float, bool], url: str, now: float) -> bool:
    src, fetched, ok = entry
    if src != url:
        return False
    return now - fetched < (_ttl_sec if ok else _missing_ttl_sec)


def cached_cover(platform: str, game_id: str) -> Optional[str]:
    """Path of the cached cover, or None (expired covers still count until replaced)."""
    path = cover_path(platform, game_id)
    return path if os.path.isfile(path) else None


def cover_bytes(platform: str, game_id: str) -> Optional[bytes]:
    path = cached_cover(platform, game_id)
    if not path:
        return None
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def cover_info(platform: str, game_id: str) -> Dict[str, object]:
    """{cached, fetched_at, expires_at, ok} for the drawer's details."""
    key = cover_key(platform, game_id)
    with _lock:
        _load_locked()
        entry = _index.get(key)
    if not entry:
        return {"cached": False, "fetched_at": 0.0, "expires_at": 0.0, "ok": False}
    _src, fetched, ok = entry
    return {
        "cached": ok and os.path.isfile(cover_path(platform, game_id)),
        "fetched_at": fetched,
        "expires_at": fetched + (_ttl_sec if ok else _missing_ttl_sec),
        "ok": ok,
    }


def request_covers(items: Iterable[Tuple[str, str, str]]) -> int:
    """Queue (platform, game_id, url) covers that are missing or stale. Returns how many were queued."""
    now = time.time()
    queued = 0
    with _lock:
        _load_locked()
        for platform, game_id, url in items:
            url = str(url or "").strip()
            if not url.startswith("https://"):
                continue
            key = cover_key(platform, game_id)
            if key in _pending:
                continue
            entry = _index.get(key)
            if entry and _fresh(entry, url, now) and (not entry[2] or os.path.isfile(cover_path(platform, game_id))):
                continue
            _pending.add(key)
            _queue.put((key, url, platform, str(game_id)))
            queued += 1
    if queued:
        _ensure_worker()
    return queued


def _ensure_worker() -> None:
    global _worker
    with _lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_run, name="game-tracker-covers", daemon=True)
        _worker.start()


def _download(url: str) -> bytes:
    if _fetcher is not None:
        return _fetcher(url)
    import requests

    with requests.get(url, timeout=DOWNLOAD_TIMEOUT_SEC, stream=True) as r:
        r.raise_for_status()
        data = bytearray()
        for chunk in r.iter_content(65536):
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise ValueError("cover image too large")
        return bytes(data)


def _to_jpeg(raw: bytes) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as im:
        im = im.convert("RGB")
        if im.width > MAX_WIDTH:
            im = im.resize((MAX_WIDTH, max(1, round(im.height * MAX_WIDTH / im.width))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def _fetch_jpeg(url: str, platform: str, game_id: str, key: str) -> bytes:
    try:
        return _to_jpeg(_download(url))
    except Exception as first:
        fallback = _fallbacks.get(platform)
        alt = None
        if fallback is not None:
            try:
                alt = fallback(game_id)
            except Exception as e:
                logger.debug("Games Tracker: cover fallback lookup failed for %s: %s", key, e)
        if not alt or alt == url or not str(alt).startswith("https://"):
            raise first
        return _to_jpeg(_download(alt))


def _store_one(key: str, url: str, platform: str = "", game_id: str = "") -> bool:
    try:
        data = _fetch_jpeg(url, platform, game_id, key)
        directory = cover_dir()
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, key + ".jpg")
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        ok = True
    except Exception as e:
        logger.debug("Games Tracker: cover download failed for %s: %s", key, e)
        ok = False
    with _lock:
        _index[key] = (url, time.time(), ok)
    return ok


def _run() -> None:
    saved_at = time.monotonic()
    while True:
        try:
            key, url, platform, game_id = _queue.get(timeout=5)
        except queue.Empty:
            break
        ok = _store_one(key, url, platform, game_id)
        if _queue.empty() or time.monotonic() - saved_at > 5:
            _save_index()
            saved_at = time.monotonic()
        if ok:
            for fn in list(_listeners):
                try:
                    fn(key)
                except Exception:
                    logger.debug("Games Tracker: cover listener failed", exc_info=True)
        with _lock:
            _pending.discard(key)


def wait_idle(timeout: float = 10.0) -> bool:
    """Block until queued downloads finish (tests)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with _lock:
            busy = bool(_pending)
        if not busy:
            return True
        time.sleep(0.02)
    return False


def forget_failures() -> int:
    """Drop remembered failures so those covers are tried again on the next request (Sync now)."""
    with _lock:
        _load_locked()
        failed = [k for k, (_u, _t, ok) in _index.items() if not ok]
        for k in failed:
            del _index[k]
    if failed:
        _save_index()
    return len(failed)


def clear_covers() -> int:
    """Delete every cached cover. Returns how many files were removed."""
    directory = cover_dir()
    removed = 0
    with _lock:
        _load_locked()
        _index.clear()
        try:
            names = os.listdir(directory)
        except FileNotFoundError:
            names = []
        for name in names:
            if name.endswith(".jpg") or name == INDEX_FILE:
                try:
                    os.remove(os.path.join(directory, name))
                    removed += name.endswith(".jpg")
                except OSError:
                    pass
    return removed


def cover_count() -> int:
    try:
        return sum(1 for n in os.listdir(cover_dir()) if n.endswith(".jpg"))
    except FileNotFoundError:
        return 0
