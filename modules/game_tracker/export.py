# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Games Tracker export: the shareable library recap / game spotlight image.

Drawn with the same palette, fonts and card style as the Statistics highlights image
(``statistics_export``), so colors follow the active theme. Covers come from the local
cover cache; a game without one gets an initials tile. Nothing here touches the network.
"""

from __future__ import annotations

import colorsys
import hashlib
import logging
import math
import os
import re
import time
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from ..statistics_export import BOLD, DISPLAY, REGULAR, Canvas, Palette, _compact
from . import covers
from .models import PSN, STEAM, GameEvent, TrackedGame, grade_counts

logger = logging.getLogger(__name__)

LAYOUTS = {"wide": (1920, 1080), "portrait": (1080, 1350), "square": (1080, 1080)}
LAYOUT_LABELS = (("wide", "Wide 16:9", "crop_16_9"), ("portrait", "Portrait 4:5", "crop_portrait"), ("square", "Square 1:1", "crop_square"))
TEMPLATES = (("library", "Library recap", "grid_view"), ("game", "Game spotlight", "stars"))
LIBRARY_SECTIONS = (("tiles", "Headline tiles"), ("grid", "Game grid"), ("cabinet", "Trophy cabinet"), ("unlocks", "Recent unlocks"), ("footer", "Footer"))
GAME_SECTIONS = (("progress", "Progress"), ("breakdown", "Grade breakdown"), ("groups", "Trophy groups"), ("unlocks", "Recent unlocks"), ("footer", "Footer"))
TILES = (("games", "Games"), ("plat", "Platinums"), ("perfect", "Perfect games"), ("trophies", "Trophies"), ("ach", "Achievements"), ("avg", "Avg completion"))
PERIODS = (("all", "All time"), ("year", "This year"), ("30d", "Last 30 days"), ("7d", "Last 7 days"))
PICKS = (("recent", "Recently played"), ("progress", "Most complete"), ("done", "Completed only"), ("picked", "Hand-picked"))
COUNTS = (4, 6, 8, 12)
PLATFORM_CHOICES = ("all", PSN, STEAM)

GRID_LABELS = {"recent": "Recently played", "progress": "Most complete", "done": "Completed", "picked": "Favourites"}
_TILE_PLATFORM = {"games": "all", "plat": PSN, "perfect": STEAM, "trophies": PSN, "ach": STEAM, "avg": "all"}
GRADE_RGB = {"platinum": (143, 180, 224), "gold": (224, 177, 63), "silver": (169, 180, 191), "bronze": (194, 124, 70)}
BRAND_RGB = {PSN: (0, 112, 204), STEAM: (27, 40, 56)}
WHITE = (255, 255, 255)
_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _choice(value: Any, allowed: Sequence[str], default: str) -> str:
    value = str(value or "").strip()
    return value if value in allowed else default


def _str_list(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(v) for v in value if isinstance(v, (str, int)) and str(v)]


@dataclass
class ExportOptions:
    template: str = "library"
    game_key: str = ""  # "psn:NPWR..._00" or "steam:<appid>"
    layout: str = "wide"
    title: str = ""
    platform: str = "all"
    period: str = "all"
    hidden_sections: List[str] = field(default_factory=list)
    hidden_tiles: List[str] = field(default_factory=list)
    pick: str = "recent"
    count: int = 8
    picked: List[str] = field(default_factory=list)
    show_pct: bool = True
    show_badges: bool = True
    cover_background: bool = False

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "ExportOptions":
        raw = raw if isinstance(raw, dict) else {}
        known = {f.name for f in fields(cls)}
        o = cls()
        data = {k: v for k, v in raw.items() if k in known}
        o.template = _choice(data.get("template"), [t[0] for t in TEMPLATES], "library")
        o.game_key = str(data.get("game_key") or "").strip()[:64]
        o.layout = _choice(data.get("layout"), list(LAYOUTS), "wide")
        o.title = str(data.get("title") or "").strip()[:80].strip()
        o.platform = _choice(data.get("platform"), PLATFORM_CHOICES, "all")
        o.period = _choice(data.get("period"), [p[0] for p in PERIODS], "all")
        sections = {k for k, _ in LIBRARY_SECTIONS} | {k for k, _ in GAME_SECTIONS}
        o.hidden_sections = [s for s in _str_list(data.get("hidden_sections")) if s in sections]
        o.hidden_tiles = [t for t in _str_list(data.get("hidden_tiles")) if t in dict(TILES)]
        o.pick = _choice(data.get("pick"), [p[0] for p in PICKS], "recent")
        try:
            count = int(data.get("count", 8))
        except (TypeError, ValueError):
            count = 8
        o.count = count if count in COUNTS else 8
        o.picked = _str_list(data.get("picked"))[:12]
        for flag in ("show_pct", "show_badges", "cover_background"):
            if flag in data:
                setattr(o, flag, bool(data[flag]))
        return o

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def default_channel_name() -> str:
    """Connected Twitch display name, else the stored streamer name, else Mycelian."""
    try:
        from .. import twitch

        name = str(twitch.get_twitch_connection_status().get("user_name") or "").strip()
        if name and name not in ("N/A", "None", "Unknown"):
            return name
    except Exception:
        pass
    try:
        from ..dataobjects import state_manager

        settings = state_manager.get_app_settings()
        return (getattr(settings, "streamer_name", "") or "Mycelian").strip() or "Mycelian"
    except Exception:
        return "Mycelian"


def _since(period: str, now: Optional[float] = None) -> Optional[float]:
    now = time.time() if now is None else now
    if period == "year":
        return datetime(datetime.fromtimestamp(now).year, 1, 1).timestamp()
    if period == "30d":
        return now - 30 * 86400
    if period == "7d":
        return now - 7 * 86400
    return None


def _ago(ts: float, now: Optional[float] = None) -> str:
    now = time.time() if now is None else now
    d = max(0.0, now - float(ts or 0))
    if d < 3600:
        return f"{max(1, int(d // 60))}m ago"
    if d < 86400:
        return f"{int(d // 3600)}h ago"
    if d < 2 * 86400:
        return "Yesterday"
    if d < 7 * 86400:
        return f"{int(d // 86400)}d ago"
    if d < 60 * 86400:
        return f"{int(d // (7 * 86400))}w ago"
    return datetime.fromtimestamp(ts).strftime("%b %Y")


def _initials(title: str) -> str:
    words = [w for w in re.split(r"[\s:\-–—]+", str(title or "")) if w[:1].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def _key_color(key: str) -> Tuple[int, int, int]:
    h = int(hashlib.md5(key.encode("utf-8")).hexdigest()[:6], 16) / 0xFFFFFF
    r, g, b = colorsys.hsv_to_rgb(h, 0.45, 0.42)
    return int(r * 255), int(g * 255), int(b * 255)


def _load_cover(game: TrackedGame) -> Optional[Image.Image]:
    path = covers.cached_cover(game.platform, game.game_id)
    if not path:
        return None
    try:
        with Image.open(path) as im:
            return im.convert("RGB")
    except Exception as e:
        logger.debug("Games Tracker export: unreadable cover for %s: %s", game.key, e)
        return None


def fit_cover(img: Image.Image, size: Tuple[int, int]) -> Image.Image:
    """The whole cover scaled to fit ``size`` (never stretched or cropped), over a blurred, dimmed
    copy of itself that fills the rest. A cover that already has the box's shape just fills it."""
    w, h = size
    src_ratio, box_ratio = img.width / max(1, img.height), w / max(1, h)
    if abs(src_ratio - box_ratio) / box_ratio < 0.04:
        return img.resize((w, h), Image.LANCZOS)
    back = ImageOps.fit(img, (max(1, w // 4), max(1, h // 4)), Image.BILINEAR)
    back = back.filter(ImageFilter.GaussianBlur(max(2, min(w, h) // 40))).resize((w, h), Image.BILINEAR)
    back = Image.blend(back, Image.new("RGB", (w, h), (0, 0, 0)), 0.45)
    scale = min(w / img.width, h / img.height)
    fg = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    back.paste(fg, ((w - fg.width) // 2, (h - fg.height) // 2))
    return back


def _rounded_paste(cv: Canvas, img: Image.Image, box, radius: int) -> None:
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    fitted = fit_cover(img, (w, h))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    cv.img.paste(fitted, (x0, y0), mask)
    cv.draw = ImageDraw.Draw(cv.img)


def _cover(cv: Canvas, box, game: TrackedGame, radius: int, initials_size: int) -> None:
    img = _load_cover(game)
    if img is not None:
        _rounded_paste(cv, img, box, radius)
        return
    cv.draw.rounded_rectangle(box, radius=radius, fill=_key_color(game.key))
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    cv.text((cx, cy), _initials(game.display_title), DISPLAY(initials_size), WHITE, anchor="mm")


_PATH_TOKEN_RE = re.compile(r"[MmLlHhVvCcSsZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_CURVE_STEPS = 16


def svg_path_subpaths(d: str) -> List[List[Tuple[float, float]]]:
    """Flatten an SVG path (M L H V C S Z, absolute and relative) into point lists, one per subpath."""
    tokens = _PATH_TOKEN_RE.findall(d or "")
    subpaths: List[List[Tuple[float, float]]] = []
    pts: List[Tuple[float, float]] = []
    x = y = sx = sy = 0.0
    ctrl: Optional[Tuple[float, float]] = None  # last cubic control point, for S
    cmd = ""
    i = 0

    def nums(n: int) -> List[float]:
        nonlocal i
        out = [float(t) for t in tokens[i:i + n]]
        i += n
        return out

    def cubic(p1, p2, p3) -> None:
        x0, y0 = x, y
        for k in range(1, _CURVE_STEPS + 1):
            t = k / _CURVE_STEPS
            mt = 1 - t
            pts.append((mt ** 3 * x0 + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t ** 3 * p3[0],
                        mt ** 3 * y0 + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t ** 3 * p3[1]))

    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                if pts:
                    subpaths.append(pts)
                pts, x, y, ctrl = [], sx, sy, None
                continue
        elif not cmd or cmd in "Zz":
            i += 1  # stray number
            continue
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            dx, dy = nums(2)
            if pts:
                subpaths.append(pts)
            x, y = (x + dx, y + dy) if rel else (dx, dy)
            sx, sy, pts, ctrl = x, y, [(x, y)], None
            cmd = "l" if rel else "L"  # extra pairs after M are line-tos
        elif c == "L":
            dx, dy = nums(2)
            x, y = (x + dx, y + dy) if rel else (dx, dy)
            pts.append((x, y))
            ctrl = None
        elif c == "H":
            (v,) = nums(1)
            x = x + v if rel else v
            pts.append((x, y))
            ctrl = None
        elif c == "V":
            (v,) = nums(1)
            y = y + v if rel else v
            pts.append((x, y))
            ctrl = None
        elif c == "C":
            a = nums(6)
            if rel:
                a = [a[0] + x, a[1] + y, a[2] + x, a[3] + y, a[4] + x, a[5] + y]
            cubic((a[0], a[1]), (a[2], a[3]), (a[4], a[5]))
            ctrl, (x, y) = (a[2], a[3]), (a[4], a[5])
        elif c == "S":
            a = nums(4)
            if rel:
                a = [a[0] + x, a[1] + y, a[2] + x, a[3] + y]
            p1 = (2 * x - ctrl[0], 2 * y - ctrl[1]) if ctrl else (x, y)
            cubic(p1, (a[0], a[1]), (a[2], a[3]))
            ctrl, (x, y) = (a[0], a[1]), (a[2], a[3])
    if pts:
        subpaths.append(pts)
    return [p for p in subpaths if len(p) >= 3]


_mark_cache: Dict[Tuple[str, int], Optional[Image.Image]] = {}


def brand_mark(platform: str, size: int) -> Optional[Image.Image]:
    """The real PlayStation / Steam logo (from the app's brand SVGs) as an RGBA image ``size`` px square."""
    key = (platform, int(size))
    if key in _mark_cache:
        return _mark_cache[key]
    mark = None
    try:
        from ..uiwindows.service_brand_icons import SERVICE_BRAND_SVG

        svg = SERVICE_BRAND_SVG.get("psn" if platform == PSN else "steam", "")
        d = re.search(r'\sd="([^"]+)"', svg)
        fill = re.search(r'fill="#([0-9A-Fa-f]{6})"', svg)
        box = re.search(r'viewBox="[\d.\s-]+?\s([\d.]+)\s([\d.]+)"', svg)
        if d and fill:
            view = float(box.group(1)) if box else 24.0
            big = max(8, int(size)) * 4  # draw large, shrink for smooth edges
            scale = big / view
            mask = Image.new("L", (big, big), 0)
            for sub in svg_path_subpaths(d.group(1)):
                layer = Image.new("L", (big, big), 0)
                ImageDraw.Draw(layer).polygon([(px * scale, py * scale) for px, py in sub], fill=255)
                # Even-odd fill: a subpath inside another one (Steam's rings) cuts a hole.
                mask = ImageChops.logical_xor(mask.convert("1"), layer.convert("1")).convert("L")
            rgb = tuple(int(fill.group(1)[j:j + 2], 16) for j in (0, 2, 4))
            # Shrink only the coverage mask, then paint the exact brand colour through it.
            mark = Image.new("RGBA", (int(size), int(size)), (*rgb, 0))
            mark.putalpha(mask.resize((int(size), int(size)), Image.LANCZOS))
    except Exception as e:
        logger.debug("Games Tracker export: brand mark unavailable for %s: %s", platform, e)
    _mark_cache[key] = mark
    return mark


def _badge(cv: Canvas, box, platform: str) -> None:
    """A small tile with the platform's real logo (a plain tile if the logo can't be drawn)."""
    x0, y0, x1, y1 = box
    s = int(x1 - x0)
    cv.draw.rounded_rectangle(box, radius=max(4, int(s * 0.26)), fill=cv.pal.raised,
                              outline=_mix_rgb(cv.pal.raised, cv.pal.text, 0.18), width=1)
    mark = brand_mark(platform, max(8, int(s * 0.66)))
    if mark is not None:
        ox = int(x0 + (s - mark.width) / 2)
        oy = int(y0 + (int(y1 - y0) - mark.height) / 2)
        cv.img.paste(mark, (ox, oy), mark)
        cv.draw = ImageDraw.Draw(cv.img)
    else:
        cv.draw.rounded_rectangle(box, radius=max(4, int(s * 0.26)), fill=BRAND_RGB.get(platform, (60, 60, 70)))


def _mix_rgb(a, b, t: float):
    return tuple(int(a[k] + (b[k] - a[k]) * t) for k in range(3))


def _bar(cv: Canvas, box, pct: float, color) -> None:
    x0, y0, x1, y1 = box
    r = int((y1 - y0) / 2)
    cv.draw.rounded_rectangle(box, radius=r, fill=cv.pal.track)
    pct = max(0.0, min(100.0, float(pct or 0)))
    if pct > 0:
        cv.draw.rounded_rectangle((x0, y0, x0 + max(y1 - y0, (x1 - x0) * pct / 100), y1), radius=r, fill=color)


def _section_title(cv: Canvas, xy, text: str) -> None:
    cv.text(xy, text, BOLD(22), cv.pal.text)


def _fill_background(cv: Canvas, game: Optional[TrackedGame]) -> None:
    if game is None:
        return
    img = _load_cover(game)
    if img is None:
        return
    bg = ImageOps.fit(img, (cv.w, cv.h), Image.LANCZOS).filter(ImageFilter.GaussianBlur(60))
    cv.img = Image.blend(cv.img, bg, 0.35)
    cv.draw = ImageDraw.Draw(cv.img)


# ---------------------------------------------------------------------------
# library recap
# ---------------------------------------------------------------------------


def _filter_platform(games: List[TrackedGame], platform: str) -> List[TrackedGame]:
    return [g for g in games if not g.hidden and (platform == "all" or g.platform == platform)]


def _pick_games(games: List[TrackedGame], o: ExportOptions) -> List[TrackedGame]:
    if o.pick == "progress":
        pool = sorted(games, key=lambda g: (-g.progress, -g.last_played))
    elif o.pick == "done":
        pool = sorted((g for g in games if g.is_complete), key=lambda g: -g.last_played)
    elif o.pick == "picked":
        by_key = {g.key: g for g in games}
        pool = [by_key[k] for k in o.picked if k in by_key]
    else:
        pool = sorted(games, key=lambda g: -g.last_played)
    cap = o.count if o.layout == "wide" else min(o.count, 8)
    return pool[:cap]


def _tile_specs(store, games: List[TrackedGame], o: ExportOptions) -> List[Tuple[str, str, str]]:
    from .service import summarize

    s = summarize(games)
    since = _since(o.period)
    deltas: Dict[str, str] = {}
    if since is not None:
        psn_counts = store.event_counts(since, platform=PSN) if o.platform in ("all", PSN) else {}
        steam_counts = store.event_counts(since, platform=STEAM) if o.platform in ("all", STEAM) else {}
        started = psn_counts.get("started", 0) + steam_counts.get("started", 0)
        deltas = {
            "games": f"+{started}",
            "plat": f"+{psn_counts.get('grade:platinum', 0)}",
            "perfect": f"+{steam_counts.get('completed', 0)}",
            "trophies": f"+{_compact(psn_counts.get('unlock', 0))}",
            "ach": f"+{_compact(steam_counts.get('unlock', 0))}",
        }
    values = {
        "games": _compact(s["games"]),
        "plat": _compact(s["platinums"]),
        "perfect": _compact(s["perfect"]),
        "trophies": _compact(s["trophies"]),
        "ach": _compact(s["achievements"]),
        "avg": f"{s['avg_completion']}%",
    }
    hidden = set(o.hidden_tiles)
    out = []
    for key, label in TILES:
        if key in hidden:
            continue
        plat = _TILE_PLATFORM[key]
        if o.platform != "all" and plat not in ("all", o.platform):
            continue
        out.append((label, values[key], deltas.get(key, "")))
    if o.layout == "square":
        out = out[:3]
    return out


def _tile(cv: Canvas, box, label: str, value: str, delta: str, value_size: int) -> None:
    x0, y0, x1, y1 = box
    p = cv.pal
    cv.card(box, radius=22)
    cv.draw.rounded_rectangle((x0 + 22, y0 + 22, x0 + 62, y0 + 27), radius=3, fill=p.primary)
    dw = 0
    if delta:
        dw = cv.text_w(delta, BOLD(19))
        cv.text((x1 - 22, y0 + 42), delta, BOLD(19), p.up, anchor="ra")
    cv.text((x0 + 22, y0 + 40), label, REGULAR(21), p.text2, max_w=int(x1 - x0 - 56 - dw))
    cv.text((x0 + 22, y1 - 20), value, DISPLAY(value_size), p.text, anchor="ls", max_w=int(x1 - x0 - 40))


def _header(cv: Canvas, box, kicker: str, title: str, right1: str, right2: str, compact: bool) -> int:
    """Draws the header; returns its bottom y."""
    x0, y0, x1, _ = box
    p = cv.pal
    cv.tracked((x0, y0), kicker, BOLD(20 if not compact else 18), p.primary, 3)
    if compact:
        cv.text((x0, y0 + 30), title, DISPLAY(72), p.text, max_w=x1 - x0)
        cv.text((x0, y0 + 124), right1, BOLD(26), p.text2, max_w=x1 - x0)
        return y0 + 164
    rw = max(cv.text_w(right1, BOLD(32)), cv.text_w(right2, REGULAR(22)))
    cv.text((x0, y0 + 30), title, DISPLAY(84), p.text, max_w=x1 - x0 - rw - 60)
    cv.text((x1, y0 + 46), right1, BOLD(32), p.text, anchor="ra")
    cv.text((x1, y0 + 90), right2, REGULAR(22), p.muted, anchor="ra")
    return y0 + 150


def _grid_card(cv: Canvas, box, games: List[TrackedGame], o: ExportOptions, cols: int) -> None:
    x0, y0, x1, y1 = box
    p = cv.pal
    cv.card(box, radius=22)
    _section_title(cv, (x0 + 22, y0 + 20), GRID_LABELS.get(o.pick, "Games"))
    if not games:
        cv.text(((x0 + x1) / 2, (y0 + y1) / 2), "No games match these options", REGULAR(22), p.muted, anchor="mm")
        return
    pad, gap = 22, 18
    top = y0 + 62
    inner_w = x1 - x0 - 2 * pad
    cell_w = (inner_w - gap * (cols - 1)) / cols
    rows = math.ceil(len(games) / cols)
    text_h = 30 + 18 + (24 if o.show_pct else 0)
    avail = y1 - pad - top - gap * (rows - 1)
    cover_h = max(30.0, min(cell_w * 9 / 16, avail / rows - text_h))
    for i, g in enumerate(games):
        r, c = divmod(i, cols)
        cx = x0 + pad + c * (cell_w + gap)
        cy = top + r * (cover_h + text_h + gap)
        cbox = (cx, cy, cx + cell_w, cy + cover_h)
        _cover(cv, cbox, g, 14, max(18, int(cover_h * 0.34)))
        if o.show_badges:
            s = max(20, min(34, int(cover_h * 0.24)))
            _badge(cv, (cbox[2] - s - 10, cbox[3] - s - 10, cbox[2] - 10, cbox[3] - 10), g.platform)
        if g.is_complete:
            s = max(20, min(32, int(cover_h * 0.22)))
            cv.draw.ellipse((cx + 10, cy + 10, cx + 10 + s, cy + 10 + s), fill=p.up)
            ox, oy = cx + 10, cy + 10
            cv.draw.line([(ox + s * 0.28, oy + s * 0.52), (ox + s * 0.44, oy + s * 0.68), (ox + s * 0.74, oy + s * 0.34)],
                         fill=WHITE, width=max(2, int(s * 0.12)))
        ty = cy + cover_h + 8
        cv.text((cx, ty), g.display_title, BOLD(19), p.text, max_w=int(cell_w))
        _bar(cv, (cx, ty + 30, cx + cell_w, ty + 38), g.progress, p.up if g.is_complete else p.primary)
        if o.show_pct:
            cv.text((cx, ty + 44), f"{g.progress}%", BOLD(16), p.muted)


_STACK_MIN_SEG = 6
_STACK_GAP = 3


def _stack_segments(x_start: float, x_end: float, grades: Dict[str, int]) -> List[Tuple[float, float, str]]:
    """(x0, x1, grade) for the stacked grade bar. Every grade you have gets at least a sliver, so a
    single platinum among thousands of bronzes still shows; the rest of the width is shared by count."""
    present = [(g, grades.get(g, 0)) for g in ("platinum", "gold", "silver", "bronze") if grades.get(g, 0) > 0]
    if not present:
        return []
    usable = (x_end - x_start) - _STACK_GAP * (len(present) - 1)
    if usable < _STACK_MIN_SEG * len(present):
        return []
    total = sum(n for _g, n in present)
    spare = usable - _STACK_MIN_SEG * len(present)
    out, x = [], float(x_start)
    for i, (grade, n) in enumerate(present):
        seg = _STACK_MIN_SEG + spare * n / total
        x_b = x_end if i == len(present) - 1 else x + seg
        out.append((x, max(x, x_b - 1), grade))
        x = x_b + _STACK_GAP
    return out


def _cabinet_card(cv: Canvas, box, games: List[TrackedGame], platform: str) -> None:
    from .service import summarize

    x0, y0, x1, y1 = box
    p = cv.pal
    cv.card(box, radius=22)
    _section_title(cv, (x0 + 22, y0 + 20), "Trophy cabinet")
    s = summarize(games)
    y = y0 + 68
    if platform != STEAM:
        grades = s["trophies_by_grade"]
        cw = (x1 - x0 - 44) / 4
        for i, grade in enumerate(("platinum", "gold", "silver", "bronze")):
            gx = x0 + 22 + i * cw
            cv.draw.ellipse((gx, y, gx + 16, y + 16), fill=GRADE_RGB[grade])
            cv.text((gx, y + 26), _compact(grades[grade]), DISPLAY(44), p.text, max_w=int(cw - 8))
            cv.text((gx, y + 80), grade.capitalize(), REGULAR(18), p.muted)
        y += 116
        for x_a, x_b, grade in _stack_segments(x0 + 22, x1 - 22, grades) if y + 12 < y1 - 16 else ():
            cv.draw.rectangle((x_a, y, x_b, y + 12), fill=GRADE_RGB[grade])
        if sum(grades.values()) and y + 12 < y1 - 16:
            y += 30
    if platform != PSN and y + 30 < y1:
        _badge(cv, (x0 + 22, y, x0 + 50, y + 28), STEAM)
        perfect = s["perfect"]
        text = f"{_compact(s['achievements'])} achievements · {perfect} perfect game{'' if perfect == 1 else 's'}"
        cv.text((x0 + 62, y + 2), text, REGULAR(20), p.text2, max_w=int(x1 - x0 - 84))


def _unlocks_card(cv: Canvas, box, events: List[GameEvent], with_game: bool) -> None:
    x0, y0, x1, y1 = box
    p = cv.pal
    cv.card(box, radius=22)
    _section_title(cv, (x0 + 22, y0 + 20), "Recent unlocks")
    y = y0 + 66
    row_h = 58 if with_game else 44
    if not events:
        cv.text((x0 + 22, y), "Nothing unlocked yet", REGULAR(19), p.muted)
        return
    for i, e in enumerate(events):
        if y + row_h > y1 - 12:
            break
        if i:
            cv.draw.line((x0 + 22, y - 6, x1 - 22, y - 6), fill=p.hairline, width=1)
        cy = y + row_h / 2 - 6
        if e.platform == PSN and e.unlock_grade in GRADE_RGB:
            cv.draw.ellipse((x0 + 22, cy - 9, x0 + 40, cy + 9), fill=GRADE_RGB[e.unlock_grade])
        else:
            cv.draw.ellipse((x0 + 22, cy - 9, x0 + 40, cy + 9), outline=p.muted, width=3)
        when = _ago(e.ts)
        ww = cv.text_w(when, REGULAR(16))
        cv.text((x1 - 22, y + 4), when, REGULAR(16), p.muted, anchor="ra")
        name = e.unlock_name or ("Trophy" if e.platform == PSN else "Achievement")
        cv.text((x0 + 54, y), name, BOLD(19), p.text, max_w=int(x1 - x0 - 90 - ww))
        if with_game:
            cv.text((x0 + 54, y + 26), e.game_title, REGULAR(16), p.muted, max_w=int(x1 - x0 - 90 - ww))
        y += row_h


def _footer(cv: Canvas, box, left: str) -> None:
    x0, y0, x1, _ = box
    note = "Made with Mycelian"
    cv.text((x1, y0), note, REGULAR(19), cv.pal.muted, anchor="ra")
    cv.text((x0, y0), left, REGULAR(19), cv.pal.muted, max_w=int(x1 - x0 - cv.text_w(note, REGULAR(19)) - 40))


def _platform_text(o: ExportOptions) -> str:
    return {"all": "PlayStation + Steam", PSN: "PlayStation", STEAM: "Steam"}[o.platform]


def _render_library(cv: Canvas, store, games_all: List[TrackedGame], o: ExportOptions, channel: str) -> None:
    W, H = cv.w, cv.h
    L = o.layout
    hidden = set(o.hidden_sections)
    games = _filter_platform(games_all, o.platform)
    grid_games = _pick_games(games, o)
    if o.cover_background and grid_games:
        _fill_background(cv, grid_games[0])
    m = 64 if L == "wide" else 56
    cv.glow((W - 180, 80), 420 if L == "wide" else 360, cv.pal.primary, 60)
    cv.glow((120, H - 60), 380 if L == "wide" else 320, cv.pal.primary, 34)
    period = dict(PERIODS)[o.period]
    title = o.title or channel
    y = _header(cv, (m, 56 if L == "wide" else 52, W - m, 0), "GAMES TRACKER", title, period,
                f"{len(games):,} games · {_platform_text(o)}", L != "wide")
    gap = 22
    y += 26 if L == "wide" else 16
    bottom = H - 88 if "footer" not in hidden else H - m

    tiles = _tile_specs(store, games, o) if "tiles" not in hidden else []
    if tiles:
        per_row = 3 if L == "portrait" else len(tiles)
        tile_h = 150
        rows = math.ceil(len(tiles) / per_row)
        for r in range(rows):
            row = tiles[r * per_row:(r + 1) * per_row]
            tw = (W - 2 * m - gap * (len(row) - 1)) / len(row)
            for i, (label, value, delta) in enumerate(row):
                tx = m + i * (tw + gap)
                _tile(cv, (tx, y, tx + tw, y + tile_h), label, value, delta, 56 if L == "portrait" or len(row) > 4 else 64)
            y += tile_h + gap

    show_grid = "grid" not in hidden
    show_cab = "cabinet" not in hidden and L != "square"
    show_unl = "unlocks" not in hidden and L != "square"
    platform = None if o.platform == "all" else o.platform
    events = store.recent_events(5 if L == "wide" else 3, platform=platform, kinds=("unlock",)) if show_unl else []
    cols = 6 if (L == "wide" and len(grid_games) > 8) else 4

    if L == "wide":
        side = show_cab or show_unl
        if show_grid and side:
            split = m + (W - 2 * m) * 0.64
            _grid_card(cv, (m, y, split - gap / 2, bottom), grid_games, o, cols)
            _side_stack(cv, (split + gap / 2, y, W - m, bottom), games, events, show_cab, show_unl, o.platform, gap, vertical=True)
        elif show_grid:
            _grid_card(cv, (m, y, W - m, bottom), grid_games, o, cols)
        elif side:
            _side_stack(cv, (m, y, W - m, bottom), games, events, show_cab, show_unl, o.platform, gap, vertical=False)
    else:
        if show_grid:
            rows = max(1, math.ceil(len(grid_games) / cols))
            want = 62 + rows * ((W - 2 * m - 44 - 18 * (cols - 1)) / cols * 9 / 16 + 72 + 18) + 4
            grid_bottom = bottom if not (show_cab or show_unl) else min(bottom - 260, y + want)
            _grid_card(cv, (m, y, W - m, grid_bottom), grid_games, o, cols)
            y = grid_bottom + gap
        if (show_cab or show_unl) and bottom - y > 120:
            _side_stack(cv, (m, y, W - m, bottom), games, events, show_cab, show_unl, o.platform, gap, vertical=False)

    if "footer" not in hidden:
        psn_n = sum(1 for g in games if g.platform == PSN)
        steam_n = sum(1 for g in games if g.platform == STEAM)
        parts = []
        if o.platform in ("all", PSN):
            parts.append(f"{psn_n:,} PlayStation")
        if o.platform in ("all", STEAM):
            parts.append(f"{steam_n:,} Steam")
        _footer(cv, (m, H - 60, W - m, H - 30), " · ".join(parts) + " games")


def _side_stack(cv: Canvas, box, games, events, show_cab: bool, show_unl: bool, platform: str, gap: int, vertical: bool) -> None:
    x0, y0, x1, y1 = box
    if show_cab and show_unl:
        if vertical:
            mid = y0 + (y1 - y0) * 0.44
            _cabinet_card(cv, (x0, y0, x1, mid - gap / 2), games, platform)
            _unlocks_card(cv, (x0, mid + gap / 2, x1, y1), events, True)
        else:
            mid = (x0 + x1) / 2
            _cabinet_card(cv, (x0, y0, mid - gap / 2, y1), games, platform)
            _unlocks_card(cv, (mid + gap / 2, y0, x1, y1), events, True)
    elif show_cab:
        _cabinet_card(cv, box, games, platform)
    elif show_unl:
        _unlocks_card(cv, box, events, True)


# ---------------------------------------------------------------------------
# game spotlight
# ---------------------------------------------------------------------------


def _wrap(cv: Canvas, text: str, font, max_w: int, max_lines: int) -> List[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if cv.text_w(trial, font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = cv.fit(lines[-1] + "…", font, max_w)
    return [cv.fit(line, font, max_w) for line in lines]


def _render_game(cv: Canvas, store, game: TrackedGame, o: ExportOptions) -> None:
    W, H = cv.w, cv.h
    L = o.layout
    p = cv.pal
    hidden = set(o.hidden_sections)
    if o.cover_background:
        _fill_background(cv, game)
    m = 64 if L == "wide" else 56
    cv.glow((W - 180, 80), 420 if L == "wide" else 360, p.primary, 60)
    cv.glow((120, H - 60), 380 if L == "wide" else 320, p.primary, 34)
    bottom = H - 88 if "footer" not in hidden else H - m
    top = 56 if L == "wide" else 52

    if L == "portrait":
        cover_box = (m, top, W - m, top + 440)
        ix0, iy, ix1 = m, top + 440 + 30, W - m
    else:
        cw = 600 if L == "wide" else 360
        cover_box = (m, top, m + cw, bottom if L == "wide" else top + cw * 1.3)
        ix0, iy, ix1 = m + cw + 44, top, W - m
    _cover(cv, cover_box, game, 26, 160 if L != "square" else 110)
    if o.show_badges:
        s = 60
        _badge(cv, (cover_box[2] - s - 18, cover_box[3] - s - 18, cover_box[2] - 18, cover_box[3] - 18), game.platform)

    psn = game.platform == PSN
    kicker = ("PLAYSTATION · " + (game.platform_label or "PS")) if psn else "STEAM"
    if game.is_complete:
        kicker += " · PLATINUM" if psn and game.has_platinum else " · PERFECT" if not psn else " · COMPLETE"
    cv.tracked((ix0, iy), kicker, BOLD(20), p.primary, 3)
    iy += 34
    title_font = DISPLAY(96 if L != "square" else 60)
    for line in _wrap(cv, o.title or game.display_title, title_font, int(ix1 - ix0), 2):
        cv.text((ix0, iy), line, title_font, p.text)
        iy += int(title_font.size * 1.12)
    iy += 12

    if "progress" not in hidden and iy + 120 < bottom:
        big = DISPLAY(88 if L != "square" else 64)
        pct = f"{game.progress}%"
        cv.text((ix0, iy), pct, big, p.text)
        label = f"{game.earned_total} of {game.defined_total} " + ("trophies" if psn else "achievements")
        cv.text((ix0 + cv.text_w(pct, big) + 18, iy + big.size * 0.55), label, REGULAR(24), p.text2, max_w=int(ix1 - ix0 - cv.text_w(pct, big) - 18))
        iy += int(big.size * 1.3)
        _bar(cv, (ix0, iy, ix1, iy + 14), game.progress, p.up if game.is_complete else p.primary)
        iy += 36

    if "breakdown" not in hidden and iy + 110 < bottom:
        if psn:
            earned, defined = grade_counts(game.earned), grade_counts(game.defined)
            boxes = [(g.capitalize(), f"{earned[g]}", f" / {defined[g]}", GRADE_RGB[g]) for g in ("platinum", "gold", "silver", "bronze")]
        else:
            events = store.recent_events(50, platform=STEAM, game_id=game.game_id, kinds=("unlock",))
            rates = [e.unlock_rarity for e in events if e.unlock_rarity is not None]
            hours = game.playtime_min / 60
            boxes = [
                ("Achievements", f"{game.earned_total}", f" / {game.defined_total}", None),
                ("Playtime", f"{hours:,.0f} h" if hours >= 1 else f"{game.playtime_min} min", "", None),
                ("Rarest unlock", f"{min(rates):.1f}%" if rates else "—", "", None),
            ]
        per_row = 2 if L == "square" else len(boxes)
        bh = 104 if L != "square" else 92
        rows = math.ceil(len(boxes) / per_row)
        bw = (ix1 - ix0 - 16 * (per_row - 1)) / per_row
        for i, (label, value, small, dot) in enumerate(boxes):
            r, c = divmod(i, per_row)
            bx, by = ix0 + c * (bw + 16), iy + r * (bh + 12)
            if by + bh > bottom:
                break
            cv.card((bx, by, bx + bw, by + bh), radius=18)
            tx = bx + 20
            if dot:
                cv.draw.ellipse((tx, by + 20, tx + 16, by + 36), fill=dot)
                tx += 26
            cv.text((tx, by + 16), label, REGULAR(18), p.text2, max_w=int(bw - 40))
            vfont = DISPLAY(52 if L != "square" else 38)
            cv.text((bx + 20, by + bh - 14), value, vfont, p.text, anchor="ls")
            if small:
                cv.text((bx + 20 + cv.text_w(value, vfont), by + bh - 18), small, REGULAR(22), p.muted, anchor="ls")
        iy += rows * (bh + 12) + 14

    groups = game.extra.get("trophy_groups") if isinstance(game.extra.get("trophy_groups"), list) else []
    if psn and groups and "groups" not in hidden and L != "square" and iy + 110 < bottom:
        gh = min(bottom - iy, 40 + 40 * len(groups))
        if "unlocks" not in hidden:
            gh = min(gh, (bottom - iy) * 0.45)
        cv.card((ix0, iy, ix1, iy + gh), radius=22)
        gy = iy + 20
        for grp in groups:
            if gy + 30 > iy + gh - 10:
                break
            name = str(grp.get("group_name") or grp.get("trophy_group_id") or "Trophies")
            n = sum(grade_counts(grp.get("defined_trophies")).values())
            right = f"{n} trophies"
            cv.text((ix1 - 22, gy), right, REGULAR(20), p.muted, anchor="ra")
            cv.text((ix0 + 22, gy), name, REGULAR(20), p.text, max_w=int(ix1 - ix0 - 64 - cv.text_w(right, REGULAR(20))))
            gy += 40
        iy += gh + 18

    if "unlocks" not in hidden and bottom - iy > 120:
        events = store.recent_events(3 if L != "square" else 2, platform=game.platform, game_id=game.game_id, kinds=("unlock",))
        _unlocks_card(cv, (ix0, iy, ix1, bottom), events, False)

    if "footer" not in hidden:
        played = f"Last played {_ago(game.last_played)}" if game.last_played else ""
        _footer(cv, (m, H - 60, W - m, H - 30), played)


# ---------------------------------------------------------------------------
# public
# ---------------------------------------------------------------------------


def _find_game(store, key: str, games: List[TrackedGame]) -> Optional[TrackedGame]:
    if not key:
        visible = [g for g in games if not g.hidden]
        return max(visible, key=lambda g: g.last_played, default=None)
    platform, _, game_id = key.partition(":")
    if platform not in (PSN, STEAM) or not game_id:
        return None
    return store.get_game(platform, game_id)


def render_games_image(
    options: ExportOptions,
    *,
    store=None,
    theme=None,
    channel_name: Optional[str] = None,
) -> Optional[Image.Image]:
    """Render the export image; None when there is nothing to draw (empty library, unknown game)."""
    o = options if isinstance(options, ExportOptions) else ExportOptions.from_dict(options)
    if store is None:
        from .service import get_service

        store = get_service().store
    if theme is None:
        from ..theme_manager import get_theme_manager

        tm = get_theme_manager()
        tm.load_themes_from_directory()
        theme = tm.get_theme()
    games = store.list_games()
    if not any(not g.hidden for g in games):
        return None
    cv = Canvas(LAYOUTS[o.layout], Palette.from_theme(theme))
    if o.template == "game":
        game = _find_game(store, o.game_key, games)
        if game is None:
            return None
        _render_game(cv, store, game, o)
    else:
        if not _filter_platform([g for g in games if not g.hidden], o.platform):
            return None  # nothing on the chosen platform
        _render_library(cv, store, games, o, channel_name or default_channel_name())
    return cv.img


def export_filename(options: ExportOptions, when: Optional[datetime] = None) -> str:
    o = options
    if o.template == "game" and o.game_key:
        slug = _SLUG_RE.sub("_", o.game_key.replace(":", "_")).strip("_") or "game"
    else:
        slug = "library"
    suffix = "" if o.layout == "wide" else f"_{o.layout}"
    return f"games_{slug}_{(when or datetime.now()):%Y%m%d}{suffix}.png"


def save_export_image(image: Image.Image, options: ExportOptions) -> str:
    """Save as PNG under data/exports; returns the path."""
    from ..path_utils import get_data_path

    export_dir = get_data_path("exports")
    os.makedirs(export_dir, exist_ok=True)
    path = os.path.join(export_dir, export_filename(options))
    image.convert("RGB").save(path, "PNG")
    return path
