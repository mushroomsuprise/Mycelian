# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""In-app previews for alert media: play buttons, sound lengths, GIF timing and TTS samples.

The browser side is ``assets/default_assets/mycelian_media_preview.js``. Buttons carry what
to play in ``data-*`` attributes so a click plays straight away in the page (browsers only
allow sound and speech inside a click); Python keeps those attributes current.
"""

from __future__ import annotations

import json
import logging
import os
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from nicegui import context, ui

logger = logging.getLogger(__name__)

PREVIEW_JS_ASSET = "default_assets/mycelian_media_preview.js"

# The files a random sound folder can play: the same list the overlay asks the server for
# (web_engine ``get_audio_files``), so a preview never picks a file the overlay would skip.
RANDOM_SOUND_EXTS = (".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac")
_RANDOM_LIST_LIMIT = 2000

# Browsers show GIF frames with a delay of 10 ms or less for 100 ms.
_MIN_FRAME_MS = 20
_SHORT_FRAME_MS = 100


# ---------------------------------------------------------------------------
#  Page script + random-pick callbacks
# ---------------------------------------------------------------------------


_loaded_clients: "weakref.WeakSet[Any]" = weakref.WeakSet()


def _read_asset(relative: str) -> str:
    try:
        from ..path_utils import get_assets_path

        with open(get_assets_path(relative), "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception as exc:
        logger.error("Could not load %s: %s", relative, exc)
        return ""


def ensure_preview_script(client: Any = None) -> None:
    """Load the preview script into the page once."""
    if client is None:
        try:
            client = context.client
        except Exception:
            return
    if client in _loaded_clients:
        return
    _loaded_clients.add(client)
    script = _read_asset(PREVIEW_JS_ASSET)
    if script:
        client.run_javascript(script)


def preview_button(tooltip: str, *, icon: str = "play_arrow", on_pick: Optional[Callable[[str], None]] = None,
                   **data: Any) -> ui.button:
    """Round play/stop button; ``data`` becomes its ``data-*`` attributes (see set_button_data).

    ``on_pick(file_name)`` runs when a random-folder button picks a file to play.
    """
    ensure_preview_script()
    button = ui.button(icon=icon).props("flat round dense size=sm").classes("mpv-btn")
    button._props["data-icon"] = icon

    def picked(event: Any) -> None:
        if on_pick is not None and event.args:
            try:
                on_pick(str(event.args))
            except Exception:
                logger.debug("Preview pick handler failed", exc_info=True)

    set_button_data(button, **data)
    # Plays inside the click (browsers only allow sound there); a random pick is sent back with emit().
    button.on("click", picked, js_handler="(e) => { const f = window.MycelianPreview && window.MycelianPreview.toggle(e); if (f) emit(f); }")
    button.tooltip(tooltip)
    return button


def set_button_data(button: ui.button, **data: Any) -> None:
    """Set ``data-*`` attributes (underscores become dashes; lists are sent as JSON)."""
    changed = False
    for key, value in data.items():
        name = "data-" + key.replace("_", "-")
        text = json.dumps(value) if isinstance(value, (list, tuple)) else ("" if value is None else str(value))
        if button._props.get(name) != text:
            button._props[name] = text
            changed = True
    if changed:
        button.update()


async def audio_duration(url: str, client: Any = None) -> Optional[float]:
    """Length of an audio file in seconds, read by the page (None when unknown)."""
    if not url:
        return None
    try:
        client = client or context.client
        ensure_preview_script(client)
        value = await client.run_javascript(f"window.MycelianPreview.duration({json.dumps(url)})", timeout=10)
        seconds = float(value) if value is not None else 0.0
        return seconds if seconds > 0 else None
    except Exception:
        logger.debug("Could not read audio duration for %s", url, exc_info=True)
        return None


# ---------------------------------------------------------------------------
#  Files
# ---------------------------------------------------------------------------


def asset_file(directory: str, filename: str = "") -> Optional[Path]:
    """The file (or folder) an alert's ``/assets/...`` directory + file name points at, if it exists."""
    from .file_picker import assets_root, from_web_path

    directory = str(directory or "").strip()
    if not directory:
        return None
    root = assets_root()
    web = directory.rstrip("/\\") + ("/" + filename if filename else "")
    path = from_web_path(web, root)
    if path is None:
        return None
    if filename:
        return path if path.is_file() else None
    return path if path.is_dir() else None


def media_url(path: Optional[Path]) -> str:
    """URL the app serves an assets file at (for playback and duration)."""
    if path is None:
        return ""
    from .file_picker import _media_url, assets_root

    try:
        return _media_url(path, assets_root())
    except Exception:
        logger.debug("No media URL for %s", path, exc_info=True)
        return ""


def folder_base_url(folder: Optional[Path]) -> str:
    """URL prefix for files in an assets folder (file names are appended URL-encoded)."""
    if folder is None:
        return ""
    url = media_url(folder)
    if not url:
        return ""
    return url.split("?", 1)[0].rstrip("/") + "/"


def random_sound_files(folder: Optional[Path]) -> List[str]:
    """Sound files a random folder can play, sorted like the overlay's list."""
    if folder is None:
        return []
    names: List[str] = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if len(names) >= _RANDOM_LIST_LIMIT:
                    break
                if entry.is_file() and os.path.splitext(entry.name.lower())[1] in RANDOM_SOUND_EXTS:
                    names.append(entry.name)
    except OSError:
        return []
    return sorted(names)


@dataclass
class AnimationTiming:
    seconds: float
    frames: int
    fps: float
    loops: Optional[int]  # 0 = forever, None = plays once


_timing_cache: Dict[tuple, Optional[AnimationTiming]] = {}


def animation_timing(path: Optional[Path]) -> Optional[AnimationTiming]:
    """Play length of an animated GIF / WebP / APNG, timed the way browsers show it."""
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _timing_cache:
        if len(_timing_cache) > 256:
            _timing_cache.clear()
        _timing_cache[key] = _read_animation_timing(path)
    return _timing_cache[key]


def _read_animation_timing(path: Path) -> Optional[AnimationTiming]:
    try:
        from PIL import Image

        with Image.open(path) as img:
            frames = int(getattr(img, "n_frames", 1) or 1)
            if frames < 2:
                return None
            total_ms = 0
            for index in range(frames):
                img.seek(index)
                delay = int(img.info.get("duration") or 0)
                total_ms += _SHORT_FRAME_MS if delay < _MIN_FRAME_MS else delay
            loop = img.info.get("loop")
    except Exception:
        logger.debug("Could not read animation timing for %s", path, exc_info=True)
        return None
    seconds = total_ms / 1000.0
    return AnimationTiming(seconds=seconds, frames=frames, fps=frames / seconds if seconds else 0.0,
                           loops=int(loop) if loop is not None else None)


def format_seconds(seconds: Optional[float]) -> str:
    """"3.4 s" under a minute, "1:05" from a minute up."""
    if seconds is None or seconds <= 0:
        return ""
    if seconds < 60:
        text = f"{seconds:.1f}".rstrip("0").rstrip(".")
        return f"{text} s"
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}:{secs:02d}"


def describe_animation(timing: AnimationTiming, *, frames: bool = True) -> str:
    """Detail line: "36 frames · 15 fps · loops forever" (``frames=False`` leaves the count out)."""
    fps = f"{timing.fps:.1f}".rstrip("0").rstrip(".")
    loops = {0: "loops forever", None: "plays once"}.get(timing.loops, f"loops {timing.loops} times")
    return " · ".join(p for p in (f"{timing.frames} frames" if frames else "", f"{fps} fps", loops) if p)


# ---------------------------------------------------------------------------
#  Text-to-speech
# ---------------------------------------------------------------------------

# "Alert message" is what the viewer typed with the event; previews speak one of these.
MOCK_TTS_MESSAGES: Dict[str, List[str]] = {
    "_": [
        "Love the stream, keep it up!",
        "Hype hype hype!",
        "Greetings from the other side of the world.",
        "That last play was incredible.",
        "Happy to support, have a great stream!",
    ],
    "bits": ["Take my bits, that clutch deserved it!", "Cheering for the comeback!"],
    "subs": ["Happy to be part of the community!", "Six months already, time flies!"],
    "giftsubs": ["Spreading the love, enjoy the subs everyone!", "Welcome to the club, new subs!"],
    "donations": ["Thanks for all the laughs, have a coffee on me.", "For the snack fund!"],
    "points": ["Hydrate!", "Do the victory dance!"],
    "raids": ["We come in peace! Raid party has arrived."],
    "streaks": ["Haven't missed a stream all month!"],
}


def mock_tts_messages(alert_type: str) -> List[str]:
    return MOCK_TTS_MESSAGES.get(alert_type, []) + MOCK_TTS_MESSAGES["_"]


def tts_voice_settings() -> Dict[str, float]:
    """Rate, pitch and volume the alerts overlay speaks with (its template settings)."""
    values = {"rate": 1.0, "pitch": 1.0, "volume": 1.0}
    try:
        from ..template_config_parser import _config_element_value, get_shared_parser

        config = get_shared_parser().load_config("alerts", include_dynamic_controls=False, create=False)
        for key, element_id in (("rate", "TtsRate"), ("pitch", "TtsPitch"), ("volume", "TtsVolume")):
            value = _config_element_value(config, element_id)
            if value not in (None, ""):
                values[key] = float(value)
    except Exception:
        logger.debug("Could not read the alerts TTS settings", exc_info=True)
    return values


def tts_preview_texts(alert_type: str, source: str, custom_message: str) -> List[str]:
    """What a TTS preview may say: the custom message, or sample viewer messages."""
    if source == "custom_message":
        text = str(custom_message or "").strip()
        return [text] if text else []
    return mock_tts_messages(alert_type)

