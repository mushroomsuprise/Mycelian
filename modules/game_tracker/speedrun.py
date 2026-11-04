# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Speedrun info for tracked games, read from LiveSplit split files (.lss).

The split file belongs to LiveSplit and may be open and saved by it at any time, so Mycelian
only ever reads it, and never holds it in a way LiveSplit could notice:

* :func:`read_shared` opens it read-only with every share flag (other programs can read,
  write, rename and delete it meanwhile), reads it into memory in one go and closes it at once.
* :class:`SpeedrunWatcher` notices changes with ``os.stat`` only (no file handle at all), waits
  until the size and time stop changing, then reads; a read that overlapped a save is dropped
  and tried again on the next check.

:func:`parse_lss` turns the XML into the attempt count, finished runs and personal best.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

LSS_EXTENSIONS = (".lss",)
MAX_FILE_BYTES = 64 * 1024 * 1024  # split files with long segment histories run to a few MB
POLL_SEC = 2.0
TIMINGS = ("auto", "real", "game")
TIMING_LABELS = {"auto": "Automatic", "real": "Real time", "game": "Game time"}

# .NET TimeSpan text: [-][d.]hh:mm:ss[.fffffff]
_TIMESPAN = re.compile(r"^\s*(-)?(?:(\d+)\.)?(\d+):(\d{1,2}):(\d{1,2})(?:\.(\d+))?\s*$")


class LssError(ValueError):
    """The file isn't a LiveSplit split file (or was caught half-written)."""


@dataclass
class LssStats:
    game_name: str = ""
    category: str = ""
    attempts: int = 0  # every run started (LiveSplit's attempt counter)
    finished: int = 0  # attempts that reached the last split
    pb_real_ms: Optional[int] = None
    pb_game_ms: Optional[int] = None
    segments: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_timespan(text: Optional[str]) -> Optional[int]:
    """Milliseconds from a LiveSplit time ("01:23:45.6780000", "1.02:03:04"); None when blank or odd."""
    m = _TIMESPAN.match(text or "")
    if not m:
        return None
    sign, days, h, mnt, s, frac = m.groups()
    ms = ((int(days or 0) * 24 + int(h)) * 3600 + int(mnt) * 60 + int(s)) * 1000
    ms += int((frac or "0")[:3].ljust(3, "0"))
    return -ms if sign else ms


def format_time(ms: Optional[int]) -> str:
    """LiveSplit-style: 1:23:45.67, 23:45.67 or 45.67 (empty for no time)."""
    if ms is None:
        return ""
    neg = ms < 0
    ms = abs(int(ms))
    cs = (ms // 10) % 100
    secs = ms // 1000
    h, m, s = secs // 3600, (secs // 60) % 60, secs % 60
    if h:
        out = f"{h}:{m:02d}:{s:02d}.{cs:02d}"
    elif m:
        out = f"{m}:{s:02d}.{cs:02d}"
    else:
        out = f"{s}.{cs:02d}"
    return ("-" if neg else "") + out


def _text(el: Optional[ET.Element], tag: str) -> str:
    child = el.find(tag) if el is not None else None
    return (child.text or "").strip() if child is not None else ""


def parse_lss(data: bytes) -> LssStats:
    """Stats from a .lss file's bytes. Raises :class:`LssError` for anything that isn't a whole split file."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise LssError(f"Not readable as a split file ({e})") from None
    if root.tag != "Run":
        raise LssError("Not a LiveSplit split file")
    stats = LssStats(game_name=_text(root, "GameName"), category=_text(root, "CategoryName"))
    try:
        stats.attempts = max(0, int(_text(root, "AttemptCount") or 0))
    except ValueError:
        stats.attempts = 0
    history = root.find("AttemptHistory")
    best_real: Optional[int] = None
    best_game: Optional[int] = None
    for attempt in history.findall("Attempt") if history is not None else []:
        real = parse_timespan(_text(attempt, "RealTime"))
        game = parse_timespan(_text(attempt, "GameTime"))
        if real is None and game is None:
            continue  # reset before the last split
        stats.finished += 1
        if real is not None and (best_real is None or real < best_real):
            best_real = real
        if game is not None and (best_game is None or game < best_game):
            best_game = game
    stats.attempts = max(stats.attempts, stats.finished)
    segments = root.find("Segments")
    seg_list = segments.findall("Segment") if segments is not None else []
    stats.segments = len(seg_list)
    if seg_list:
        # The personal best is the last split's "Personal Best" time (it survives cleared history).
        for split in seg_list[-1].findall("SplitTimes/SplitTime"):
            if split.get("name") == "Personal Best":
                stats.pb_real_ms = parse_timespan(_text(split, "RealTime"))
                stats.pb_game_ms = parse_timespan(_text(split, "GameTime"))
                break
    if stats.pb_real_ms is None:
        stats.pb_real_ms = best_real
    if stats.pb_game_ms is None:
        stats.pb_game_ms = best_game
    return stats


# ----- reading without getting in LiveSplit's way -----


class FileBusy(LssError):
    """Another program (LiveSplit) is writing or opening the file right now: try again later."""


def read_shared(path: str, max_bytes: int = MAX_FILE_BYTES) -> bytes:
    """The whole file, read-only, without ever making another program's open, save, rename or delete fail.

    On Windows (:func:`_read_windows`) the file is opened with every share flag under a read-handle
    oplock. If LiveSplit opens it while this read is running, even for exclusive writing (which a share
    flag alone can't allow), Windows holds that open for the few milliseconds it takes to close here,
    instead of failing it with a sharing violation; this read then stops (:class:`FileBusy`) and is
    tried again later. Elsewhere, opening a file never locks it.
    """
    if os.name == "nt":
        data = _read_windows(str(path), max_bytes)
    else:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        with os.fdopen(fd, "rb", closefd=True) as fh:
            data = fh.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise LssError(f"The file is over {max_bytes // (1024 * 1024)} MB")
    return data


# ----- Windows: shared, oplocked, overlapped read (ctypes) -----

_GENERIC_READ = 0x80000000
_SHARE_ALL = 0x1 | 0x2 | 0x4  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
_OPEN_EXISTING = 3
_FILE_FLAGS = 0x80 | 0x40000000 | 0x08000000  # NORMAL | OVERLAPPED | SEQUENTIAL_SCAN
_FSCTL_REQUEST_OPLOCK = 0x00090240
_OPLOCK_READ_HANDLE = 0x1 | 0x2  # OPLOCK_LEVEL_CACHE_READ | OPLOCK_LEVEL_CACHE_HANDLE
_ERROR_IO_PENDING = 997
_ERROR_HANDLE_EOF = 38
_ERROR_OPLOCK_NOT_GRANTED = 300
_ERROR_SHARING_VIOLATION = 32
_WAIT_OBJECT_0 = 0
_READ_CHUNK = 1024 * 1024
_win = None


def _kernel32():
    """kernel32 with the signatures used here (64-bit safe handles)."""
    global _win
    if _win is not None:
        return _win
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p), ("Offset", wintypes.DWORD),
                    ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]

    class OPLOCK_IN(ctypes.Structure):  # REQUEST_OPLOCK_INPUT_BUFFER
        _fields_ = [("StructureVersion", wintypes.USHORT), ("StructureLength", wintypes.USHORT),
                    ("RequestedOplockLevel", wintypes.ULONG), ("Flags", wintypes.ULONG)]

    class OPLOCK_OUT(ctypes.Structure):  # REQUEST_OPLOCK_OUTPUT_BUFFER
        _fields_ = [("StructureVersion", wintypes.USHORT), ("StructureLength", wintypes.USHORT),
                    ("OriginalOplockLevel", wintypes.ULONG), ("NewOplockLevel", wintypes.ULONG),
                    ("Flags", wintypes.ULONG), ("AccessMode", wintypes.DWORD), ("ShareMode", wintypes.USHORT)]

    sig = {
        "CreateFileW": ([wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                         wintypes.DWORD, wintypes.HANDLE], wintypes.HANDLE),
        "CreateEventW": ([wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
        "DeviceIoControl": ([wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID,
                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(OVERLAPPED)], wintypes.BOOL),
        "ReadFile": ([wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                      ctypes.POINTER(OVERLAPPED)], wintypes.BOOL),
        "GetOverlappedResult": ([wintypes.HANDLE, ctypes.POINTER(OVERLAPPED), ctypes.POINTER(wintypes.DWORD),
                                 wintypes.BOOL], wintypes.BOOL),
        "CancelIoEx": ([wintypes.HANDLE, ctypes.POINTER(OVERLAPPED)], wintypes.BOOL),
        "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
    }
    for name, (args, res) in sig.items():
        fn = getattr(k, name)
        fn.argtypes, fn.restype = args, res
    _win = (ctypes, wintypes, k, OVERLAPPED, OPLOCK_IN, OPLOCK_OUT)
    return _win


class _OplockedFile:
    """An open, read-only, fully shared handle that holds a read-handle oplock while it's open.

    ``broken()`` turns true when another program opens the file in a way the oplock protects
    against (a write, or an open that would otherwise fail with a sharing violation). That open
    waits until this handle is closed, so close quickly once ``broken()`` is true.
    """

    def __init__(self, path: str):
        ctypes, wintypes, k, OVERLAPPED, OPLOCK_IN, OPLOCK_OUT = _kernel32()
        self._c, self._k = ctypes, k
        self.handle = k.CreateFileW(path, _GENERIC_READ, _SHARE_ALL, None, _OPEN_EXISTING, _FILE_FLAGS, None)
        if self.handle in (None, wintypes.HANDLE(-1).value):
            err = ctypes.get_last_error()
            if err == _ERROR_SHARING_VIOLATION:
                raise FileBusy("LiveSplit has the file open for saving")
            raise OSError(err, ctypes.FormatError(err).strip(), path)
        self.locked = False
        self._event = k.CreateEventW(None, True, False, None)
        self._ov = OVERLAPPED(hEvent=self._event)
        self._in = OPLOCK_IN(1, ctypes.sizeof(OPLOCK_IN), _OPLOCK_READ_HANDLE, 0x1)  # REQUEST
        self._out = OPLOCK_OUT()
        ok = k.DeviceIoControl(self.handle, _FSCTL_REQUEST_OPLOCK, ctypes.byref(self._in), ctypes.sizeof(self._in),
                               ctypes.byref(self._out), ctypes.sizeof(self._out), None, ctypes.byref(self._ov))
        err = ctypes.get_last_error()
        if not ok and err == _ERROR_IO_PENDING:
            self.locked = True  # granted: the request stays pending until the oplock breaks
        elif ok or err == _ERROR_OPLOCK_NOT_GRANTED:
            self.close()  # someone has it open for writing right now
            raise FileBusy("LiveSplit is saving the file")
        else:
            logger.debug("Games Tracker: no oplock for %s (error %s); reading without one", path, err)

    def broken(self) -> bool:
        return self.locked and self._k.WaitForSingleObject(self._event, 0) == _WAIT_OBJECT_0

    def read_all(self, limit: int) -> bytes:
        ctypes, wintypes, k, OVERLAPPED, _i, _o = _kernel32()
        event = k.CreateEventW(None, True, False, None)
        try:
            chunks, offset = [], 0
            buf = ctypes.create_string_buffer(_READ_CHUNK)
            while offset <= limit:
                if self.broken():
                    raise FileBusy("LiveSplit opened the file while it was read")
                ov = OVERLAPPED(Offset=offset & 0xFFFFFFFF, OffsetHigh=offset >> 32, hEvent=event)
                got = wintypes.DWORD(0)
                if not k.ReadFile(self.handle, buf, _READ_CHUNK, None, ctypes.byref(ov)):
                    err = ctypes.get_last_error()
                    if err == _ERROR_HANDLE_EOF:
                        break
                    if err != _ERROR_IO_PENDING:
                        raise OSError(err, ctypes.FormatError(err).strip())
                if not k.GetOverlappedResult(self.handle, ctypes.byref(ov), ctypes.byref(got), True):
                    err = ctypes.get_last_error()
                    if err == _ERROR_HANDLE_EOF:
                        break
                    raise OSError(err, ctypes.FormatError(err).strip())
                if got.value == 0:
                    break
                chunks.append(buf.raw[: got.value])
                offset += got.value
            if self.broken():
                raise FileBusy("LiveSplit opened the file while it was read")
            return b"".join(chunks)
        finally:
            k.CloseHandle(event)

    def close(self) -> None:
        k, ctypes = self._k, self._c
        if self.handle is None:
            return
        if self.locked:
            # Release the oplock and wait for its request to finish before its buffers go away.
            k.CancelIoEx(self.handle, ctypes.byref(self._ov))
            done = ctypes.c_ulong(0)
            k.GetOverlappedResult(self.handle, ctypes.byref(self._ov), ctypes.byref(done), True)
            self.locked = False
        k.CloseHandle(self.handle)
        k.CloseHandle(self._event)
        self.handle = None

    def __enter__(self) -> "_OplockedFile":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _read_windows(path: str, max_bytes: int) -> bytes:
    with _OplockedFile(path) as fh:
        return fh.read_all(max_bytes)


def file_signature(path: str) -> Optional[Tuple[int, int]]:
    """(modified time in ns, size) from ``os.stat``, which never opens the file; None when it's gone."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def read_stats(path: str) -> Tuple[LssStats, Tuple[int, int]]:
    """Read and parse a split file once it's whole. Raises OSError / LssError.

    A save that lands while reading changes the signature: that read is thrown away (LssError)
    and the watcher tries again on its next check.
    """
    before = file_signature(path)
    if before is None:
        raise FileNotFoundError(path)
    data = read_shared(path)
    after = file_signature(path)
    if after != before:
        raise LssError("The file changed while it was read")
    return parse_lss(data), before


# ----- per-game link -----


@dataclass
class SpeedrunLink:
    """One split file of a tracked game (a game can have one per category) and what was last read from it."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    platform: str = ""
    game_id: str = ""
    path: str = ""
    category: str = ""  # the user's label ("Any%"); filled from the file's own category when left blank
    order: int = 0
    timing: str = "auto"  # auto (real time, else game time) / real / game
    stats: Dict[str, Any] = field(default_factory=dict)  # LssStats.to_dict() of the last good read
    signature: List[int] = field(default_factory=list)  # [mtime_ns, size] of that read
    read_at: float = 0.0
    error: str = ""  # why the latest read failed ("" when it worked)

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.game_id}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "SpeedrunLink":
        known = {f.name for f in fields(cls)}
        link = cls(**{k: v for k, v in (raw or {}).items() if k in known})
        link.id = str(link.id or uuid.uuid4().hex[:10])
        link.category = str(link.category or "").strip()
        link.order = int(link.order or 0)
        link.timing = link.timing if link.timing in TIMINGS else "auto"
        link.stats = dict(link.stats or {}) if isinstance(link.stats, dict) else {}
        link.signature = [int(x) for x in (link.signature or [])][:2]
        return link


def pb_ms(link: SpeedrunLink) -> Tuple[Optional[int], str]:
    """(personal best in ms, "real" / "game") for the link's timing choice."""
    real, game = link.stats.get("pb_real_ms"), link.stats.get("pb_game_ms")
    if link.timing == "game":
        return game, "game"
    if link.timing == "real":
        return real, "real"
    return (real, "real") if real is not None else (game, "game")


def category_label(link: SpeedrunLink) -> str:
    """The user's category, else the one in the file, else the file's name."""
    stem = os.path.splitext(os.path.basename(link.path))[0]
    return link.category or str(link.stats.get("category") or "").strip() or stem


def sorted_links(links: Iterable[SpeedrunLink]) -> List[SpeedrunLink]:
    return sorted(links, key=lambda x: (x.order, category_label(x).casefold()))


def speedruns_public(links: Iterable[SpeedrunLink]) -> List[Dict[str, Any]]:
    """One row per split file, in the game's order."""
    return [row for row in (speedrun_public(x) for x in sorted_links(links)) if row is not None]


def speedrun_public(link: Optional[SpeedrunLink]) -> Optional[Dict[str, Any]]:
    """JSON for overlays, the API and the tab (None for an empty link)."""
    if link is None or not link.path:
        return None
    best, timing = pb_ms(link)
    s = link.stats
    return {
        "id": link.id,
        "category": category_label(link),
        "category_set": bool(link.category),
        "order": link.order,
        "file": link.path,
        "file_name": os.path.basename(link.path),
        "timing": timing,
        "timing_choice": link.timing,
        "game_name": s.get("game_name", ""),
        "file_category": s.get("category", ""),
        "attempts": int(s.get("attempts") or 0),
        "finished": int(s.get("finished") or 0),
        "pb_ms": best,
        "pb": format_time(best),
        "read": bool(s),
        "read_at": link.read_at,
        "error": link.error,
    }


def refresh_link(link: SpeedrunLink, now: Optional[float] = None) -> bool:
    """Read the link's file if it changed since the last good read. Returns True when the link changed."""
    sig = file_signature(link.path)
    if sig is None:
        if link.error != "File not found":
            link.error = "File not found"
            return True
        return False
    if list(sig) == link.signature and not link.error:
        return False
    try:
        stats, sig = read_stats(link.path)
    except FileBusy:
        return False  # LiveSplit is saving: leave everything as it was and read on the next check
    except (OSError, LssError) as e:
        message = str(e) if isinstance(e, LssError) else (e.strerror or str(e))
        changed = link.error != message
        link.error = message
        return changed
    link.stats = stats.to_dict()
    link.signature = list(sig)
    link.read_at = time.time() if now is None else now
    link.error = ""
    return True


class SpeedrunWatcher:
    """Checks every linked split file every few seconds (``os.stat`` only) and reads the ones that changed.

    A file is read once its signature has held still for one check, so a save in progress is left
    alone. ``on_change(keys)`` gets the game keys whose speedrun info changed.
    """

    def __init__(self, load: Callable[[], List[SpeedrunLink]], save: Callable[[SpeedrunLink], None],
                 on_change: Callable[[List[str]], None], poll_sec: float = POLL_SEC):
        self._load = load
        self._save = save
        self._on_change = on_change
        self._poll = poll_sec
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._seen: Dict[str, Optional[Tuple[int, int]]] = {}  # link id -> signature at the previous check
        self._failed: Dict[str, Optional[Tuple[int, int]]] = {}  # link id -> signature a read failed on (retried once it changes)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="game-tracker-speedrun", daemon=True)
        self._thread.start()

    def stop(self, join_timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
        self._thread = None

    def poke(self) -> None:
        """Check now (a file was just linked)."""
        self._wake.set()

    def check_once(self) -> List[str]:
        changed: List[str] = []
        for link in self._load():
            sig = file_signature(link.path)
            previous = self._seen.get(link.id)
            self._seen[link.id] = sig
            unchanged_since_read = sig is not None and list(sig) == link.signature and not link.error
            if unchanged_since_read:
                continue
            if sig is not None and previous != sig and link.signature:
                continue  # still changing (or just changed): read it once it holds still
            if link.error and link.id in self._failed and self._failed[link.id] == sig:
                continue  # failed on exactly this version already
            if refresh_link(link):
                self._save(link)
                changed.append(link.key)
            # Remember a failure only when the file held still (a read that overlapped a save is retried).
            if link.error and file_signature(link.path) == sig:
                self._failed[link.id] = sig
            else:
                self._failed.pop(link.id, None)
        return changed

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                changed = self.check_once()
                if changed:
                    self._on_change(changed)
            except Exception:
                logger.debug("Games Tracker: speedrun check failed", exc_info=True)
            self._wake.wait(self._poll)
            self._wake.clear()
