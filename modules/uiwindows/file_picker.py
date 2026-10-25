# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Shared file and folder picker dialog.

Two scopes:

* ``"assets"`` (default): browsing stays inside the assets folder the overlay
  server serves (``path_utils.get_assets_path()``), and results carry the
  ``/assets/...`` web path overlays load. "Add files..." copies files from
  anywhere on the computer into the open folder.
* ``"system"``: anywhere on this computer (database files, key files), with
  home folders and drives as places.

The listing, path and naming helpers are plain functions so they work (and
can be tested) the same on Windows, macOS and Linux, from source or frozen.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import logging
import os
import re
import stat
import string
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from nicegui import ui

from ..notification_engine import notify
from ..ui_buttons import outline_button, primary_button
from .file_picker_styles import FILE_PICKER_CSS

logger = logging.getLogger(__name__)

IMAGE_EXTS = (".gif", ".png", ".jpg", ".jpeg", ".webp", ".apng", ".avif", ".bmp", ".svg")
AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".opus", ".weba")
VIDEO_EXTS = (".mp4", ".webm", ".mov", ".m4v", ".mkv", ".ogv")

_TYPE_NAMES = {
    ".gif": "GIF image", ".png": "PNG image", ".jpg": "JPEG image", ".jpeg": "JPEG image", ".webp": "WebP image",
    ".apng": "Animated PNG", ".avif": "AVIF image", ".bmp": "Bitmap image", ".svg": "SVG image",
    ".mp3": "MP3 audio", ".wav": "WAV audio", ".ogg": "OGG audio", ".m4a": "M4A audio", ".flac": "FLAC audio",
    ".aac": "AAC audio", ".opus": "Opus audio", ".weba": "WebM audio",
    ".mp4": "MP4 video", ".webm": "WebM video", ".mov": "QuickTime video", ".m4v": "M4V video", ".mkv": "MKV video", ".ogv": "OGG video",
    ".db": "SQLite database", ".sqlite": "SQLite database", ".json": "JSON file",
}

# Shown in folder rows: "12 items · 4 sounds".
_KIND_NOUNS = {"image": "images", "audio": "sounds", "video": "videos", "media": "media files"}

_SKIP_NAMES = {"desktop.ini", "thumbs.db", ".ds_store", "__pycache__", "$recycle.bin", "system volume information"}
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}

MAX_LIST_ENTRIES = 600  # rendered rows per folder; the filter narrows larger folders
_FOLDER_SCAN_LIMIT = 2000  # entries read per sub-folder when counting usable files
_FOLDER_COUNT_LIMIT = 150  # sub-folders that get a usable-file count


# ---------------------------------------------------------------------------
#  Pure helpers
# ---------------------------------------------------------------------------


@dataclass
class Entry:
    path: Path
    name: str
    is_dir: bool
    ext: str = ""
    size: int = 0
    mtime: float = 0.0
    usable: bool = False
    item_count: Optional[int] = None
    usable_count: Optional[int] = None


@dataclass
class PickResult:
    """What the user chose. ``web_*`` are set in the assets scope."""

    path: Path
    is_dir: bool
    web_path: str = ""
    web_dir: str = ""
    filename: str = ""


def assets_root() -> Path:
    from .. import path_utils

    return Path(path_utils.get_assets_path()).resolve()


def _real(path: Path) -> Path:
    try:
        return Path(os.path.realpath(path))
    except OSError:
        return path


def is_inside(path: Path, root: Path) -> bool:
    """True when ``path`` (symlinks resolved) is ``root`` or below it."""
    try:
        real, real_root = os.path.realpath(path), os.path.realpath(root)
        return os.path.commonpath([os.path.normcase(real), os.path.normcase(real_root)]) == os.path.normcase(real_root)
    except (OSError, ValueError):
        return False


def to_web_path(path: Path, root: Path, *, is_dir: bool = False) -> str:
    """``/assets/...`` for a path inside ``root`` (folders end with ``/``)."""
    rel = Path(os.path.relpath(_real(path), _real(root))).as_posix()
    web = "/assets/" if rel in (".", "") else "/assets/" + rel
    if is_dir and not web.endswith("/"):
        web += "/"
    return web


def from_web_path(value: str, root: Path) -> Optional[Path]:
    """Map ``/assets/x/y`` (or ``assets/x/y``) to a path under ``root``; None if it escapes."""
    text = (value or "").strip().replace("\\", "/")
    if text.startswith("/assets/") or text == "/assets":
        text = text[len("/assets"):]
    elif text.startswith("assets/"):
        text = text[len("assets"):]
    else:
        return None
    candidate = root.joinpath(*[p for p in text.split("/") if p and p != "."])
    return candidate if is_inside(candidate, root) else None


def nearest_existing_dir(path: Optional[Path], fallback: Path, *, root: Optional[Path] = None) -> Path:
    """``path`` or its closest existing parent folder (kept inside ``root`` when given)."""
    current = path
    while current is not None:
        if root is not None and not is_inside(current, root):
            break
        try:
            if current.is_dir():
                return current
        except OSError:
            pass
        if current.parent == current:
            break
        current = current.parent
    return fallback


def _is_hidden(entry: os.DirEntry) -> bool:
    name = entry.name
    if name.startswith(".") or name.lower() in _SKIP_NAMES:
        return True
    if sys.platform == "win32":
        try:
            attrs = entry.stat(follow_symlinks=False).st_file_attributes  # type: ignore[attr-defined]
            return bool(attrs & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))  # type: ignore[attr-defined]
        except (OSError, AttributeError):
            return False
    return False


def natural_key(name: str):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", name)]


def count_usable(folder: Path, extensions: Sequence[str]) -> Tuple[int, int]:
    """(visible items, usable files) directly inside ``folder``; reads at most a few thousand entries."""
    items = usable = 0
    try:
        with os.scandir(folder) as it:
            for i, entry in enumerate(it):
                if i >= _FOLDER_SCAN_LIMIT:
                    break
                if _is_hidden(entry):
                    continue
                items += 1
                try:
                    if entry.is_file() and os.path.splitext(entry.name)[1].lower() in extensions:
                        usable += 1
                except OSError:
                    continue
    except OSError:
        pass
    return items, usable


def list_entries(
    folder: Path, extensions: Sequence[str], *, root: Optional[Path] = None, count_folders: bool = True
) -> Tuple[List[Entry], Optional[str]]:
    """Folders first, then files, naturally sorted. Returns (entries, error message)."""
    entries: List[Entry] = []
    try:
        with os.scandir(folder) as it:
            raw = list(it)
    except PermissionError:
        return [], "Mycelian doesn't have permission to open this folder."
    except FileNotFoundError:
        return [], "This folder no longer exists."
    except OSError as exc:
        return [], f"This folder can't be opened ({exc.strerror or exc})."
    for entry in raw:
        if _is_hidden(entry):
            continue
        path = Path(entry.path)
        if root is not None and not is_inside(path, root):
            continue  # symlink that points outside the assets folder
        try:
            is_dir = entry.is_dir()
            st = entry.stat()
        except OSError:
            continue
        if is_dir:
            entries.append(Entry(path=path, name=entry.name, is_dir=True, mtime=st.st_mtime))
        else:
            ext = os.path.splitext(entry.name)[1].lower()
            entries.append(Entry(path=path, name=entry.name, is_dir=False, ext=ext, size=st.st_size,
                                 mtime=st.st_mtime, usable=ext in extensions))
    entries.sort(key=lambda e: (not e.is_dir, natural_key(e.name)))
    if count_folders:
        for entry in [e for e in entries if e.is_dir][:_FOLDER_COUNT_LIMIT]:
            entry.item_count, entry.usable_count = count_usable(entry.path, extensions)
            entry.usable = entry.usable_count > 0
    return entries, None


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}".replace(".0 ", " ")
        value /= 1024
    return f"{size} B"


def format_time(ts: float) -> str:
    if not ts:
        return ""
    lt = time.localtime(ts)
    if time.strftime("%Y-%m-%d", lt) == time.strftime("%Y-%m-%d"):
        return "Today, " + time.strftime("%H:%M", lt)
    return time.strftime("%b %d, %Y", lt).replace(" 0", " ")


def type_name(ext: str) -> str:
    return _TYPE_NAMES.get(ext, (ext[1:].upper() + " file") if ext else "File")


def media_kind(ext: str) -> str:
    if ext in IMAGE_EXTS:
        return "image"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    return "file"


def file_icon(ext: str) -> str:
    return {"image": "image", "audio": "music_note", "video": "movie"}.get(
        media_kind(ext), "storage" if ext in (".db", ".sqlite") else "description"
    )


def safe_upload_name(name: str, existing: Iterable[str]) -> str:
    """A safe file name for an upload that does not clash with ``existing`` (case-insensitive)."""
    base = (name or "").replace("\\", "/").split("/")[-1]
    base = re.sub(r'[\x00-\x1f<>:"|?*]', "_", base).strip().rstrip(". ")
    if not base or base in (".", ".."):
        base = "file"
    stem, ext = os.path.splitext(base)
    if not stem:
        stem, ext = base, ""
    if stem.split(".")[0].lower() in _WINDOWS_RESERVED:
        stem = "_" + stem
    taken = {n.casefold() for n in existing}
    candidate, n = stem + ext, 2
    while candidate.casefold() in taken:
        candidate = f"{stem} ({n}){ext}"
        n += 1
    return candidate


def is_temp_bundle(path: Path) -> bool:
    """True when ``path`` lives in a frozen build's temporary unpack folder (lost on exit)."""
    meipass = getattr(sys, "_MEIPASS", None)
    return bool(getattr(sys, "frozen", False) and meipass and is_inside(path, Path(meipass)))


def assets_places(root: Path, extra: Sequence[Tuple[str, str, Path]] = ()) -> List[Tuple[str, str, Path]]:
    places = [
        ("folder_special", "Assets", root),
        ("inventory_2", "Default assets", root / "default_assets"),
        ("image", "Images", root / "default_assets" / "images"),
        ("music_note", "Sounds", root / "default_assets" / "sounds"),
        *extra,
    ]
    seen, out = set(), []
    for icon, label, path in places:
        key = os.path.normcase(str(path))
        if key in seen or not path.is_dir() or not is_inside(path, root):
            continue
        seen.add(key)
        out.append((icon, label, path))
    return out


def _windows_drives() -> List[str]:
    lister = getattr(os, "listdrives", None)
    if lister is not None:
        try:
            return list(lister())
        except OSError:
            pass
    return [f"{letter}:\\" for letter in string.ascii_uppercase if os.path.exists(f"{letter}:\\")]


def system_places() -> List[Tuple[str, str, Path]]:
    """Home folders, the Mycelian folder and drives/volumes for this OS."""
    from .. import path_utils

    home = Path.home()
    places: List[Tuple[str, str, Path]] = [("home", "Home", home)]
    for icon, name in (("desktop_windows", "Desktop"), ("description", "Documents"), ("download", "Downloads")):
        places.append((icon, name, home / name))
    places.append(("eco", "Mycelian folder", Path(path_utils.get_data_path(""))))
    if sys.platform == "win32":
        for drive in _windows_drives():
            places.append(("save", drive.rstrip("\\"), Path(drive)))
    else:
        places.append(("computer", "Computer", Path("/")))
        if sys.platform == "darwin":
            volumes = [Path("/Volumes") / n for n in _safe_listdir(Path("/Volumes"))]
        else:
            user = os.environ.get("USER", "")
            volumes = [base / n for base in (Path("/media") / user, Path("/run/media") / user, Path("/mnt"))
                       for n in _safe_listdir(base)]
        for vol in volumes:
            if vol.is_dir() and not vol.is_symlink():
                places.append(("usb", vol.name, vol))
    seen, out = set(), []
    for icon, label, path in places:
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        try:
            if not path.is_dir():
                continue
        except OSError:
            continue
        seen.add(key)
        out.append((icon, label, path))
    return out


def _safe_listdir(path: Path) -> List[str]:
    try:
        return sorted(n for n in os.listdir(path) if not n.startswith("."))
    except OSError:
        return []


def breadcrumbs(folder: Path, *, root: Optional[Path]) -> List[Tuple[str, Path]]:
    """(label, path) segments from the scope's top to ``folder``."""
    if root is not None:
        parts = [("assets", root)]
        rel = Path(os.path.relpath(_real(folder), _real(root)))
        current = root
        for part in rel.parts:
            if part in (".", ""):
                continue
            current = current / part
            parts.append((part, current))
        return parts
    home = Path.home()
    if is_inside(folder, home):
        parts, current = [("Home", home)], home
        rel = Path(os.path.relpath(_real(folder), _real(home)))
    else:
        anchor = Path(folder.anchor or "/")
        parts, current = [(anchor.drive or str(anchor), anchor)], anchor
        rel = folder.relative_to(anchor) if folder.is_absolute() else folder
    for part in rel.parts:
        if part in (".", ""):
            continue
        current = current / part
        parts.append((part, current))
    return parts


def image_dimensions(path: Path) -> str:
    try:
        from PIL import Image

        with Image.open(path) as img:
            width, height = img.size
            frames = getattr(img, "n_frames", 1)
        text = f"{width} × {height}"
        return text + (f" · {frames} frames" if frames and frames > 1 else "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
#  Media preview route (assets scope)
# ---------------------------------------------------------------------------

_MEDIA_ROUTE = "/_mycelian_picker_assets"
_media_route_root: Optional[str] = None


def _media_url(path: Path, root: Path) -> str:
    """URL NiceGUI serves an assets file at (for thumbnails, previews and playback)."""
    global _media_route_root
    key = str(_real(root))
    if _media_route_root != key:
        try:
            from nicegui import app

            app.add_media_files(_MEDIA_ROUTE, key)
            _media_route_root = key
        except Exception:
            logger.debug("Could not register picker media route", exc_info=True)
            return ""
    from urllib.parse import quote

    rel = Path(os.path.relpath(_real(path), key)).as_posix()
    return f"{_MEDIA_ROUTE}/{quote(rel)}?v={int(path.stat().st_mtime) if path.exists() else 0}"


# ---------------------------------------------------------------------------
#  Dialog
# ---------------------------------------------------------------------------


@dataclass
class _PickerConfig:
    title: str
    subtitle: str
    icon: str
    extensions: Tuple[str, ...]
    mode: str
    scope: str
    kind: str
    on_pick: Callable[[PickResult], None]
    places_extra: Sequence[Tuple[str, str, Path]] = field(default_factory=tuple)
    confirm_label: str = ""
    within: Optional[Path] = None  # results must be inside this folder (e.g. assets/alerts)
    within_hint: str = ""


def _icon(name: str, cls: str = "") -> str:
    return f'<span class="fpk-icon {cls}">{name}</span>'


class _FilePicker:
    def __init__(self, config: _PickerConfig, start: Optional[str]):
        self.cfg = config
        self.root: Optional[Path] = assets_root() if config.scope == "assets" else None
        self.folder_mode = config.mode == "folder"
        fallback = self.root or Path.home()
        start_path: Optional[Path] = None
        if start:
            if self.root is not None:
                start_path = from_web_path(start, self.root)
                if start_path is None:
                    candidate = Path(start).expanduser()
                    start_path = candidate if is_inside(candidate, self.root) else None
            else:
                start_path = Path(start).expanduser()
        if start_path is None and config.within is not None and config.within.is_dir():
            start_path = config.within
        if start_path is not None and start_path.is_file():
            self.preselect = start_path
            start_path = start_path.parent
        else:
            self.preselect = None
        self.folder = nearest_existing_dir(start_path, fallback, root=self.root)
        self.selected: Optional[Entry] = None
        self.entries: List[Entry] = []
        self.query = ""
        self.only_usable = not self.folder_mode
        self.view = "grid" if config.kind == "image" else "list"
        self._upload_batch = _new_upload_batch()

    # ----- building -----
    def open(self) -> None:
        from ..help_system.help_render import inject_page_stylesheet, promote_color_importance, scope_selectors

        inject_page_stylesheet("mycelian-file-picker", promote_color_importance(scope_selectors(FILE_PICKER_CSS, ".fpk")))
        with ui.dialog().props("content-class=mycelian-wide-file-dialog") as self.dialog, ui.card().classes("fpk gap-0"):
            self._build_head()
            with ui.element("div").classes("fpk-main"):
                self.side = ui.element("div").classes("fpk-side")
                with ui.element("div").classes("fpk-center"):
                    self._build_toolbar()
                    self.body = ui.element("div").classes("fpk-body")
                self.preview = ui.element("div").classes("fpk-pv")
            self._build_footer()
        self._render_places()
        self._load_folder(self.folder, select=self.preselect)
        self.dialog.open()

    def _build_head(self) -> None:
        with ui.element("div").classes("fpk-head"):
            ui.html(_icon(self.cfg.icon), sanitize=False).classes("fpk-hic")
            with ui.element("div"):
                ui.label(self.cfg.title).classes("fpk-h")
                ui.label(self.cfg.subtitle).classes("fpk-s")
            ui.element("div").classes("fpk-sp")
            ui.button(icon="close", on_click=lambda: self.dialog.close()).props("flat round dense").tooltip("Close")

    def _build_toolbar(self) -> None:
        with ui.element("div").classes("fpk-tool"):
            self.up_btn = ui.button(icon="arrow_upward", on_click=self._go_up).props("flat round dense size=sm")
            self.up_btn.tooltip("Up one folder")
            self.crumbs = ui.element("div").classes("fpk-crumbs")
            self.filter = ui.input(placeholder="Filter").props("dense outlined clearable").classes("fpk-filter")
            with self.filter.add_slot("prepend"):
                ui.icon("search").classes("text-base")
            self.filter.on_value_change(self._on_filter)
            with ui.element("div").classes("fpk-seg"):
                self.view_btns = {}
                for value, icon, tip in (("grid", "grid_view", "Grid"), ("list", "view_list", "List")):
                    btn = ui.button(icon=icon, on_click=lambda _e, v=value: self._set_view(v)).props("flat dense size=sm")
                    btn.tooltip(tip)
                    self.view_btns[value] = btn
            self._sync_view_buttons()
        with ui.element("div").classes("fpk-tool2"):
            if self.folder_mode:
                self.only_box = ui.checkbox("Show files", value=False, on_change=self._on_only).props("dense")
            else:
                self.only_box = ui.checkbox("Only usable files", value=True, on_change=self._on_only).props("dense")
            self.count_label = ui.label("")
            ui.element("div").classes("fpk-sp")
            if self.root is not None:
                self.upload = ui.upload(multiple=True, auto_upload=True, on_upload=self._on_upload).props(
                    f'accept="{",".join(self.cfg.extensions)}"'
                ).classes("fpk-upload")
                self.add_btn = outline_button("Add files…", self._pick_uploads, icon="upload").props("dense").classes("px-2")
                with self.add_btn:
                    self.add_tip = ui.tooltip("Copy files from your computer into this folder")
            else:
                self.upload = None
                self.add_btn = None
                self.add_tip = None

    def _build_footer(self) -> None:
        with ui.element("div").classes("fpk-foot"):
            self.fills = ui.html("", sanitize=False).classes("fpk-fills")
            outline_button("Cancel", lambda: self.dialog.close()).props("dense").classes("px-3")
            label = self.cfg.confirm_label or ("Use this folder" if self.folder_mode else "Use this file")
            self.confirm_btn = primary_button(label, self._confirm, icon="check").props("dense").classes("px-4")

    # ----- places / crumbs -----
    def _places(self) -> List[Tuple[str, str, Path]]:
        if self.root is not None:
            return assets_places(self.root, self.cfg.places_extra)
        return system_places()

    def _render_places(self) -> None:
        self.side.clear()
        best = None
        places = self._places()
        for icon, label, path in places:
            if is_inside(self.folder, path) and (best is None or len(str(path)) > len(str(best))):
                best = path
        with self.side:
            ui.label("Places").classes("fpk-side-h")
            for icon, label, path in places:
                active = best is not None and os.path.normcase(str(path)) == os.path.normcase(str(best))
                place = ui.element("div").on("click", lambda _e, p=path: self._load_folder(p))
                place.classes("fpk-place is-active" if active else "fpk-place")
                with place:
                    ui.html(f"{_icon(icon)}<span>{html_lib.escape(label)}</span>", sanitize=False).classes("contents")
            if self.root is not None:
                ui.html(
                    f'{_icon("info")}<span>Overlays can only load files inside the assets folder.</span>', sanitize=False
                ).classes("fpk-side-note")

    def _render_crumbs(self) -> None:
        self.crumbs.clear()
        parts = breadcrumbs(self.folder, root=self.root)
        with self.crumbs:
            for i, (label, path) in enumerate(parts):
                if i:
                    ui.html('<span class="fpk-crumb-sep">›</span>', sanitize=False).classes("contents")
                last = i == len(parts) - 1
                lead = _icon("folder_special" if self.root is not None else "home") if i == 0 else ""
                crumb = ui.element("div")
                if not last:
                    crumb.on("click", lambda _e, p=path: self._load_folder(p))
                crumb.classes("fpk-crumb is-last" if last else "fpk-crumb")
                with crumb:
                    ui.html(f"{lead}<span>{html_lib.escape(label)}</span>", sanitize=False).classes("contents")
        top = self.root if self.root is not None else Path(self.folder.anchor or "/")
        at_top = os.path.normcase(str(_real(self.folder))) == os.path.normcase(str(_real(top)))
        if at_top:
            self.up_btn.props("disable")
        else:
            self.up_btn.props(remove="disable")

    # ----- navigation -----
    def _go_up(self) -> None:
        parent = self.folder.parent
        if self.root is not None and not is_inside(parent, self.root):
            return
        self._load_folder(parent, select=self.folder if self.folder_mode else None)

    def _load_folder(self, folder: Path, *, select: Optional[Path] = None) -> None:
        if self.root is not None and not is_inside(folder, self.root):
            notify(
                "",
                type="warning",
                source="files",
                title="The picker only shows files inside the assets folder",
                hint="Move the file into Mycelian's assets folder, then pick it from there.",
            )
            return
        self.folder = folder
        self.entries, error = list_entries(folder, self.cfg.extensions, root=self.root)
        self.error = error
        self.selected = None
        if select is not None:
            key = os.path.normcase(str(select))
            self.selected = next((e for e in self.entries if os.path.normcase(str(e.path)) == key), None)
        self._render_places()
        self._render_crumbs()
        self._render_list()
        self._render_preview()
        self._render_fills()
        self._update_add_button()

    def _update_add_button(self) -> None:
        if self.add_btn is None:
            return
        reason = ""
        if is_temp_bundle(self.folder):
            reason = "This build keeps its assets in a temporary folder, so added files would be lost when Mycelian closes."
        elif not os.access(self.folder, os.W_OK):
            reason = "Mycelian can't write to this folder."
        if reason:
            self.add_btn.props("disable")
        else:
            self.add_btn.props(remove="disable")
        if self.add_tip is not None:
            self.add_tip.set_text(reason or "Copy files from your computer into this folder")

    # ----- list -----
    def _visible_entries(self) -> List[Entry]:
        query = self.query.casefold()
        out = []
        for entry in self.entries:
            if query and query not in entry.name.casefold():
                continue
            if not entry.is_dir:
                if self.folder_mode and not self.only_box.value:
                    continue
                if not self.folder_mode and self.only_usable and not entry.usable:
                    continue
            out.append(entry)
        return out

    def _render_list(self) -> None:
        files = [e for e in self.entries if not e.is_dir]
        usable = sum(1 for e in files if e.usable)
        self.count_label.set_text(f"{len(files)} file{'s' if len(files) != 1 else ''} · {usable} usable")
        self.body.clear()
        visible = self._visible_entries()
        with self.body:
            if self.error:
                ui.label(self.error).classes("fpk-empty")
                return
            if not visible:
                if self.query:
                    message = "Nothing here matches the filter."
                elif self.folder_mode:
                    message = "No folders here."
                elif files and self.only_usable:
                    message = "No usable files here. Untick “Only usable files” to see everything."
                else:
                    message = "This folder is empty." + (" Use Add files… to copy files here." if self.root is not None else "")
                ui.label(message).classes("fpk-empty")
                return
            container = ui.element("div").classes("fpk-grid" if self.view == "grid" else "fpk-list")
            with container:
                for entry in visible[:MAX_LIST_ENTRIES]:
                    self._render_entry(entry)
            if len(visible) > MAX_LIST_ENTRIES:
                ui.label(f"Showing the first {MAX_LIST_ENTRIES} of {len(visible)}. Use the filter to find others.").classes("fpk-more")

    def _folder_meta(self, entry: Entry) -> str:
        if entry.item_count is None:
            return ""
        noun = _KIND_NOUNS.get(self.cfg.kind, "usable files")
        items = f"{entry.item_count} item{'s' if entry.item_count != 1 else ''}"
        return f"{items} · {entry.usable_count} {noun}" if entry.usable_count else f"{items} · no {noun}"

    def _render_entry(self, entry: Entry) -> None:
        selected = self.selected is not None and entry.path == self.selected.path
        allowed = self._allowed(entry.path)
        dim = (not entry.is_dir and (self.folder_mode or not entry.usable or not allowed)) or (
            entry.is_dir and self.folder_mode and (not allowed or (entry.item_count is not None and not entry.usable))
        )
        classes = ("fpk-card" if self.view == "grid" else "fpk-row") + (" is-selected" if selected else "") + (" is-dim" if dim else "")
        el = ui.element("div")
        el.on("click", lambda _e, en=entry: self._on_click(en))
        el.on("dblclick", lambda _e, en=entry: self._on_dblclick(en))
        el.classes(classes)
        name = html_lib.escape(entry.name)
        if entry.is_dir:
            meta = html_lib.escape(self._folder_meta(entry))
            if self.view == "grid":
                inner = f'<div class="fpk-thumb is-folder">{_icon("folder")}</div><div class="fpk-name">{name}</div><div class="fpk-meta">{meta}</div>'
            else:
                ok = f'<span class="fpk-ok">{_icon("check_circle")}Can use</span>' if self.folder_mode and entry.usable and allowed else ""
                inner = (f'<span class="fpk-ricon is-folder">{_icon("folder")}</span><span class="fpk-rname">{name}</span>{ok}'
                         f'<span class="fpk-rmeta">{meta}</span><span class="fpk-icon fpk-chev">chevron_right</span>')
        else:
            ok = entry.usable and not self.folder_mode
            badge = f'<span class="fpk-type{" is-ok" if ok else ""}">{html_lib.escape(entry.ext[1:].upper() or "FILE")}</span>'
            size = format_size(entry.size)
            if self.view == "grid":
                url = _media_url(entry.path, self.root) if self.root is not None and entry.usable and entry.ext in IMAGE_EXTS else ""
                if url:
                    thumb = f'<div class="fpk-thumb has-img"><img loading="lazy" src="{html_lib.escape(url, quote=True)}" alt=""></div>'
                else:
                    thumb = f'<div class="fpk-thumb{" is-ok" if ok else ""}">{_icon(file_icon(entry.ext))}</div>'
                inner = f'{thumb}<div class="fpk-name">{name}</div><div class="fpk-meta">{badge}{size}</div>'
            else:
                inner = (f'<span class="fpk-ricon{" is-ok" if ok else ""}">{_icon(file_icon(entry.ext))}</span>'
                         f'<span class="fpk-rname">{name}</span>')
                tail = f'{badge}<span class="fpk-rmeta">{size}</span>'
                with el:
                    ui.html(inner, sanitize=False).classes("contents")
                    if ok and self.root is not None and entry.ext in AUDIO_EXTS:
                        play = ui.element("span").on("click.stop", lambda _e, en=entry: self._play(en))
                        play.classes("fpk-play")
                        play.tooltip("Play")
                        with play:
                            ui.html(_icon("play_arrow"), sanitize=False).classes("contents")
                    ui.html(tail, sanitize=False).classes("contents")
                if not entry.usable and not self.folder_mode:
                    el.tooltip(f"Not usable here. Supported: {', '.join(e[1:].upper() for e in self.cfg.extensions)}")
                return
            if not entry.usable and not self.folder_mode:
                el.tooltip(f"Not usable here. Supported: {', '.join(e[1:].upper() for e in self.cfg.extensions)}")
        with el:
            ui.html(inner, sanitize=False).classes("contents")

    # ----- interaction -----
    def _on_click(self, entry: Entry) -> None:
        if entry.is_dir and not self.folder_mode:
            self._load_folder(entry.path)
            return
        if not entry.is_dir and self.folder_mode:
            return
        self.selected = entry
        self._render_list()
        self._render_preview()
        self._render_fills()

    def _play(self, entry: Entry) -> None:
        self.selected = entry
        self._render_list()
        self._render_preview(autoplay=True)
        self._render_fills()

    def _on_dblclick(self, entry: Entry) -> None:
        if entry.is_dir:
            self._load_folder(entry.path)
        elif not self.folder_mode and entry.usable:
            self.selected = entry
            self._confirm()

    def _on_filter(self, e) -> None:
        self.query = str(e.value or "")
        self._render_list()

    def _set_view(self, view: str) -> None:
        self.view = view
        self._sync_view_buttons()
        self._render_list()

    def _sync_view_buttons(self) -> None:
        for value, btn in self.view_btns.items():
            if value == self.view:
                btn.classes(add="is-on")
            else:
                btn.classes(remove="is-on")

    def _on_only(self, e) -> None:
        if not self.folder_mode:
            self.only_usable = bool(e.value)
        self._render_list()

    # ----- preview / fills -----
    def _render_preview(self, autoplay: bool = False) -> None:
        self.preview.clear()
        entry = self.selected
        with self.preview:
            if entry is None:
                target = self.folder
                items, usable = count_usable(target, self.cfg.extensions)
                ui.html(f'<div class="fpk-pv-media is-folder">{_icon("folder_open")}</div>', sanitize=False).classes("contents")
                ui.label(target.name or str(target)).classes("fpk-pv-name")
                noun = _KIND_NOUNS.get(self.cfg.kind, "usable files")
                self._rows([("Items", str(items)), (noun.capitalize(), str(usable))])
                hint = ("Pick a folder below, or use this one." if self.folder_mode
                        else "Click a file to preview it. Double-click to use it right away.")
                ui.label(hint).classes("fpk-pv-hint")
                return
            if entry.is_dir:
                self._preview_folder(entry)
                return
            url = _media_url(entry.path, self.root) if self.root is not None and entry.usable else ""
            kind = media_kind(entry.ext)
            safe = html_lib.escape(url, quote=True)
            if url and kind == "image":
                ui.html(f'<div class="fpk-pv-media has-img"><img src="{safe}" alt=""></div>', sanitize=False).classes("contents")
            elif url and kind == "video":
                ui.html(f'<div class="fpk-pv-media"><video src="{safe}" muted loop autoplay playsinline></video></div>',
                        sanitize=False).classes("contents")
            else:
                cls = "is-ok" if entry.usable else ""
                ui.html(f'<div class="fpk-pv-media {cls}">{_icon("graphic_eq" if kind == "audio" else file_icon(entry.ext))}</div>',
                        sanitize=False).classes("contents")
            ui.label(entry.name).classes("fpk-pv-name")
            if url and kind == "audio":
                audio = ui.audio(url, controls=True, autoplay=autoplay)
                audio.props("preload=metadata")
            rows = [("Type", type_name(entry.ext)), ("Size", format_size(entry.size))]
            if kind == "image":
                dims = image_dimensions(entry.path)
                if dims:
                    rows.append(("Dimensions", dims))
            rows.append(("Modified", format_time(entry.mtime)))
            if not entry.usable:
                rows.append(("Usable here", "No"))
            self._rows(rows)

    def _preview_folder(self, entry: Entry) -> None:
        ui.html(f'<div class="fpk-pv-media is-folder">{_icon("folder")}</div>', sanitize=False).classes("contents")
        ui.label(entry.name).classes("fpk-pv-name")
        items, _entries_err = list_entries(entry.path, self.cfg.extensions, root=self.root, count_folders=False)
        usable = [e for e in items if not e.is_dir and e.usable]
        noun = _KIND_NOUNS.get(self.cfg.kind, "Usable files")
        self._rows([(noun.capitalize(), str(len(usable))), ("Other items", str(len(items) - len(usable)))])
        if usable:
            lines = "".join(
                f'<div>{_icon(file_icon(e.ext))}<span>{html_lib.escape(e.name)}</span></div>' for e in usable[:6]
            )
            more = f'<div class="fpk-pv-more">+ {len(usable) - 6} more</div>' if len(usable) > 6 else ""
            ui.html(lines + more, sanitize=False).classes("fpk-pv-list")

    @staticmethod
    def _rows(rows: Sequence[Tuple[str, str]]) -> None:
        ui.html(
            "".join(f"<div><span>{html_lib.escape(k)}</span><b>{html_lib.escape(v)}</b></div>" for k, v in rows if v),
            sanitize=False,
        ).classes("fpk-pv-rows")

    def _allowed(self, path: Path) -> bool:
        within = self.cfg.within
        return within is None or is_inside(path, within)

    def _result(self) -> Optional[PickResult]:
        entry = self.selected
        if self.folder_mode:
            path = entry.path if entry is not None and entry.is_dir else self.folder
            is_dir = True
        else:
            if entry is None or entry.is_dir or not entry.usable:
                return None
            path, is_dir = entry.path, False
        if not self._allowed(path):
            return None
        result = PickResult(path=path, is_dir=is_dir, filename="" if is_dir else path.name)
        if self.root is not None:
            result.web_path = to_web_path(path, self.root, is_dir=is_dir)
            result.web_dir = result.web_path if is_dir else to_web_path(path.parent, self.root, is_dir=True)
        return result

    def _render_fills(self) -> None:
        result = self._result()
        if result is None:
            hint = "Choose a folder to continue." if self.folder_mode else "Choose a file to continue."
            if self.cfg.within is not None and self.cfg.within_hint:
                hint = self.cfg.within_hint
            self.fills.set_content(f'<span class="fpk-fill-none">{html_lib.escape(hint)}</span>')
            self.confirm_btn.props("disable")
            return
        self.confirm_btn.props(remove="disable")
        if self.root is None:
            pairs = [("Path", str(result.path))]
        elif result.is_dir:
            pairs = [("Folder", result.web_path)]
        else:
            pairs = [("Folder", result.web_dir), ("File", result.filename)]
        chips = "".join(
            f'<span class="fpk-fill"><span>{html_lib.escape(k)}</span><code title="{html_lib.escape(v, quote=True)}">'
            f"{html_lib.escape(v)}</code></span>" for k, v in pairs
        )
        self.fills.set_content(f'<span class="fpk-fill-label">{_icon("subdirectory_arrow_right")}Fills in</span>{chips}')

    def _confirm(self) -> None:
        result = self._result()
        if result is None:
            notify(
                "",
                type="warning",
                source="files",
                title="Choose a folder first" if self.folder_mode else "Choose a file first",
                hint="Click an item in the list, then click the confirm button.",
            )
            return
        try:
            self.cfg.on_pick(result)
        except Exception as exc:
            logger.error("File picker callback failed", exc_info=True)
            notify(
                "The field wasn't changed.",
                type="error",
                source="files",
                title=f"Couldn't use {result.path.name}",
                hint="Try another file, or close the picker and try again.",
                details=f"{result.path}\n{type(exc).__name__}: {exc}",
            )
            return
        self.dialog.close()

    # ----- add files -----
    def _pick_uploads(self) -> None:
        if self.upload is not None:
            self.upload.run_method("pickFiles")

    async def _on_upload(self, e) -> None:
        upload = getattr(e, "file", None)
        name = getattr(upload, "name", None) or getattr(e, "name", "")
        ext = os.path.splitext(name)[1].lower()
        if ext not in self.cfg.extensions:
            self._note_upload("rejected", name)
            return
        folder = self.folder
        if self.root is None or not is_inside(folder, self.root) or is_temp_bundle(folder) or not os.access(folder, os.W_OK):
            self._note_upload("blocked", name)
            return
        target = folder / safe_upload_name(name, _safe_listdir_all(folder))
        try:
            if upload is not None and hasattr(upload, "save"):
                await upload.save(target)
            else:  # older NiceGUI event shape
                target.write_bytes(e.content.read())
        except Exception as exc:
            logger.error("Could not add %s to %s", name, folder, exc_info=True)
            self._note_upload("failed", f"{name}: {type(exc).__name__}: {exc}")
            return
        self._upload_batch["folder"] = folder.name
        self._note_upload("added", target.name)
        self._load_folder(folder, select=target)
        if self.upload is not None:
            self.upload.reset()


    # One upload of several files fires one event per file: sum them up in one notice each.
    def _note_upload(self, kind: str, item: str) -> None:
        batch = self._upload_batch
        batch[kind].append(item)
        timer = batch.get("timer")
        if timer is not None:
            timer.cancel()
        try:
            batch["timer"] = asyncio.get_running_loop().call_later(
                _UPLOAD_SUMMARY_DELAY_SEC, self._flush_upload_notices
            )
        except RuntimeError:
            self._flush_upload_notices()

    def _flush_upload_notices(self) -> None:
        batch, self._upload_batch = self._upload_batch, _new_upload_batch()
        added, rejected, blocked, failed = (batch[k] for k in ("added", "rejected", "blocked", "failed"))
        if added:
            where = f" to {batch['folder']}" if batch.get("folder") else ""
            title = f"Added {added[0]}" if len(added) == 1 else f"Added {len(added)} files{where}"
            notify("", type="success", source="files", title=title)
        if rejected:
            exts = ", ".join(self.cfg.extensions)
            notify(
                _short_list(rejected),
                type="warning",
                source="files",
                title="1 file wasn't added" if len(rejected) == 1 else f"{len(rejected)} files weren't added",
                hint=f"Only these file types can be used here: {exts}.",
            )
        if blocked:
            notify(
                _short_list(blocked),
                type="warning",
                source="files",
                title="Files can't be added to this folder",
                hint="Open one of your own folders inside the assets folder (not a built-in one), then add the files again.",
            )
        if failed:
            notify(
                "",
                type="error",
                source="files",
                title="Couldn't add 1 file" if len(failed) == 1 else f"Couldn't add {len(failed)} files",
                hint="Check free disk space and that you can write to the assets folder, then try again.",
                details="\n".join(failed),
            )


_UPLOAD_SUMMARY_DELAY_SEC = 0.8


def _new_upload_batch() -> dict:
    return {"added": [], "rejected": [], "blocked": [], "failed": [], "folder": "", "timer": None}


def _short_list(names: List[str], limit: int = 3) -> str:
    shown = ", ".join(names[:limit])
    return shown + (f" and {len(names) - limit} more" if len(names) > limit else "")


def _safe_listdir_all(path: Path) -> List[str]:
    try:
        return os.listdir(path)
    except OSError:
        return []


def open_file_picker(
    *,
    title: str,
    subtitle: str,
    extensions: Sequence[str],
    on_pick: Callable[[PickResult], None],
    mode: str = "file",
    scope: str = "assets",
    kind: str = "file",
    icon: str = "",
    start: Optional[str] = None,
    places: Sequence[Tuple[str, str, Path]] = (),
    confirm_label: str = "",
    within: Optional[Path] = None,
    within_hint: str = "",
) -> None:
    """Open the picker.

    ``extensions`` are the usable file types (lower-case, with the dot). ``start`` is a
    current field value: an ``/assets/...`` path in the assets scope, a file system path
    otherwise. ``places`` adds (icon, label, path) shortcuts in the assets scope.
    ``within`` limits what can be chosen to one folder (``within_hint`` says why).
    """
    config = _PickerConfig(
        title=title,
        subtitle=subtitle,
        icon=icon or ("folder" if mode == "folder" else {"image": "image", "audio": "music_note", "video": "movie"}.get(kind, "description")),
        extensions=tuple(e.lower() for e in extensions),
        mode=mode,
        scope=scope,
        kind=kind,
        on_pick=on_pick,
        places_extra=tuple(places),
        confirm_label=confirm_label,
        within=within,
        within_hint=within_hint,
    )
    _FilePicker(config, start).open()
