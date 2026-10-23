#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024-2026 Mycelian

Statistics export: renders the shareable "stream highlights" image.

Input is the highlights dict from ``StatisticsManager.get_highlights_for_export``
plus optional ``extra`` data from the Statistics tab (daily activity series and
period-over-period deltas). Colors come from the active theme. Two layouts:
``wide`` (1920×1080) and ``portrait`` (1080×1350, sized for social feeds).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .path_utils import get_data_path
from .theme_manager import ThemeColors, get_theme_manager

logger = logging.getLogger(__name__)

RGB = Tuple[int, int, int]

_RGB_RE = re.compile(
    r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+)\s*)?\)",
    re.IGNORECASE,
)

LAYOUTS = {"wide": (1920, 1080), "portrait": (1080, 1350)}


def parse_css_color(s: str) -> Tuple[int, int, int, int]:
    """Parse ``rgb()`` / ``rgba()`` / ``#rrggbb`` to RGBA bytes for Pillow."""
    if not s or not isinstance(s, str):
        return (128, 128, 128, 255)
    s = s.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", s):
        return (int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16), 255)
    m = _RGB_RE.search(s)
    if not m:
        return (128, 128, 128, 255)
    a = 255 if m.group(4) is None else int(round(float(m.group(4)) * 255))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), a)


def _rgb(s: str) -> RGB:
    c = parse_css_color(s)
    return (c[0], c[1], c[2])


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))  # type: ignore[return-value]


def _over(fg: Tuple[int, int, int, int], bg: RGB) -> RGB:
    """Flatten a translucent theme color onto a background."""
    return _mix(bg, fg[:3], fg[3] / 255)


@dataclass
class Palette:
    bg: RGB
    surface: RGB
    raised: RGB
    text: RGB
    text2: RGB
    muted: RGB
    primary: RGB
    hairline: RGB
    track: RGB
    up: RGB
    down: RGB

    @classmethod
    def from_theme(cls, theme: ThemeColors) -> "Palette":
        bg = _rgb(theme.bg_base)
        surface = _over(parse_css_color(theme.bg_surface), bg)
        text = _rgb(theme.text_primary)
        return cls(
            bg=bg,
            surface=surface,
            raised=_over(parse_css_color(theme.bg_elevated), bg),
            text=text,
            text2=_over(parse_css_color(theme.text_secondary), surface),
            muted=_over(parse_css_color(theme.text_muted), surface),
            primary=_rgb(theme.primary),
            hairline=_mix(surface, text, 0.10),
            track=_mix(surface, text, 0.06),
            up=_rgb(theme.success),
            down=_rgb(theme.error),
        )


_FONT_CACHE: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    key = (name, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    font = None
    path = os.path.join(get_data_path(os.path.join("assets", "default_assets", "fonts")), name)
    for candidate in (path, "Arial.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            font = ImageFont.truetype(candidate, size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def DISPLAY(size: int):
    return _font("Anton-Regular.ttf", size)


def BOLD(size: int):
    return _font("Roboto-Bold.ttf", size)


def REGULAR(size: int):
    return _font("Roboto-Regular.ttf", size)


def _format_number(n: float) -> str:
    return f"{int(round(n or 0)):,}"


def _compact(n: float) -> str:
    n = float(n or 0)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if abs(n) >= 100_000:
        return f"{n / 1_000:.0f}K"
    return f"{int(round(n)):,}"


class Canvas:
    def __init__(self, size: Tuple[int, int], pal: Palette):
        self.w, self.h = size
        self.pal = pal
        self.img = Image.new("RGB", size, pal.bg)
        self.draw = ImageDraw.Draw(self.img)

    # --- primitives ---

    def text_w(self, text: str, font) -> int:
        b = self.draw.textbbox((0, 0), text, font=font)
        return b[2] - b[0]

    def fit(self, text: str, font, max_w: int) -> str:
        if self.text_w(text, font) <= max_w:
            return text
        while text and self.text_w(text + "…", font) > max_w:
            text = text[:-1]
        return (text + "…") if text else ""

    def text(self, xy, text: str, font, fill: RGB, anchor: str = "la", max_w: Optional[int] = None) -> None:
        if max_w is not None:
            text = self.fit(text, font, max_w)
        self.draw.text(xy, text, font=font, fill=fill, anchor=anchor)

    def tracked(self, xy, text: str, font, fill: RGB, spacing: float = 2.0) -> int:
        """Letter-spaced caps for kickers. Returns the drawn width."""
        x, y = xy
        for ch in text:
            self.draw.text((x, y), ch, font=font, fill=fill)
            x += self.text_w(ch, font) + spacing
        return int(x - xy[0])

    def card(self, box, radius: int = 22, fill: Optional[RGB] = None, outline: Optional[RGB] = None) -> None:
        self.draw.rounded_rectangle(box, radius=radius, fill=fill or self.pal.surface, outline=outline, width=1 if outline else 0)

    def glow(self, centre: Tuple[int, int], radius: int, color: RGB, strength: int = 70) -> None:
        layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).ellipse(
            [centre[0] - radius, centre[1] - radius, centre[0] + radius, centre[1] + radius], fill=(*color, strength)
        )
        layer = layer.filter(ImageFilter.GaussianBlur(radius // 2))
        self.img.paste(Image.alpha_composite(self.img.convert("RGBA"), layer).convert("RGB"))
        self.draw = ImageDraw.Draw(self.img)

    def delta(self, xy, pct: Optional[float], font, label: str = "") -> None:
        """▲/▼ triangle + percent. Shape and sign carry the direction, not only color."""
        if pct is None:
            return
        x, y = xy
        size = font.size * 0.55
        if abs(pct) < 0.5:
            self.text((x, y), "±0%" + label, font, self.pal.muted)
            return
        up = pct > 0
        color = self.pal.up if up else self.pal.down
        cy = y + font.size * 0.55
        if up:
            pts = [(x, cy + size / 2), (x + size, cy + size / 2), (x + size / 2, cy - size / 2)]
        else:
            pts = [(x, cy - size / 2), (x + size, cy - size / 2), (x + size / 2, cy + size / 2)]
        self.draw.polygon(pts, fill=color)
        txt = f"{abs(pct):.0f}%" if abs(pct) < 1000 else "999%+"
        self.text((x + size + 8, y), txt, font, color)
        if label:
            self.text((x + size + 14 + self.text_w(txt, font), y), label, font, self.pal.muted)

    def avatar(self, centre: Tuple[int, int], r: int, name: str) -> None:
        x, y = centre
        self.draw.ellipse([x - r, y - r, x + r, y + r], fill=_mix(self.pal.surface, self.pal.primary, 0.28), outline=self.pal.primary, width=3)
        self.text((x, y + 2), (name.strip()[:1] or "?").upper(), DISPLAY(int(r * 1.05)), self.pal.text, anchor="mm")


# ---------------------------------------------------------------------------
# blocks
# ---------------------------------------------------------------------------

def _header(cv: Canvas, box, streamer: str, start: datetime, end: datetime, compact_layout: bool) -> None:
    x0, y0, x1, _y1 = box
    p = cv.pal
    cv.tracked((x0, y0), "STREAM HIGHLIGHTS", BOLD(20 if not compact_layout else 18), p.primary, 3)
    name_font = DISPLAY(84 if not compact_layout else 72)
    date_txt = f"{start:%b %d, %Y}" if start.date() == end.date() else f"{start:%b %d} – {end:%b %d, %Y}"
    days = (end.date() - start.date()).days + 1
    if compact_layout:
        cv.text((x0, y0 + 30), streamer, name_font, p.text, max_w=x1 - x0)
        cv.text((x0, y0 + 128), date_txt + f"   ·   {days} day{'s' if days != 1 else ''}", BOLD(26), p.text2)
    else:
        date_w = cv.text_w(date_txt, BOLD(32))
        cv.text((x0, y0 + 30), streamer, name_font, p.text, max_w=x1 - x0 - date_w - 60)
        cv.text((x1, y0 + 46), date_txt, BOLD(32), p.text, anchor="ra")
        cv.text((x1, y0 + 90), f"{days} day{'s' if days != 1 else ''} of streaming", REGULAR(22), p.muted, anchor="ra")


def _kpi(cv: Canvas, box, label: str, value: float, pct: Optional[float], value_size: int) -> None:
    x0, y0, x1, y1 = box
    p = cv.pal
    cv.card(box, radius=22)
    cv.draw.rounded_rectangle((x0 + 22, y0 + 22, x0 + 62, y0 + 27), radius=3, fill=p.primary)
    delta_w = 0
    if pct is not None:
        txt = "±0%" if abs(pct) < 0.5 else (f"{abs(pct):.0f}%" if abs(pct) < 1000 else "999%+")
        delta_w = cv.text_w(txt, BOLD(19)) + (0 if abs(pct) < 0.5 else 19)
        cv.delta((x1 - 22 - delta_w, y0 + 42), pct, BOLD(19))
    cv.text((x0 + 22, y0 + 40), label, REGULAR(21), p.text2, max_w=x1 - x0 - 56 - delta_w)
    cv.text((x0 + 22, y1 - 22), _compact(value), DISPLAY(value_size), p.text, anchor="ls", max_w=x1 - x0 - 40)


def _activity_chart(cv: Canvas, box, labels: Sequence[str], values: Sequence[float], bucket: str) -> None:
    x0, y0, x1, y1 = box
    p = cv.pal
    cv.card(box, radius=22)
    per = {"hour": "per hour", "day": "per day", "week": "per week"}.get(bucket, "per day")
    cv.text((x0 + 28, y0 + 24), "Activity", BOLD(26), p.text)
    total = sum(values)
    cv.text((x0 + 28 + cv.text_w("Activity", BOLD(26)) + 14, y0 + 31), f"events {per}", REGULAR(19), p.muted)
    if not values or total <= 0:
        cv.text(((x0 + x1) // 2, (y0 + y1) // 2), "No daily breakdown for this range", REGULAR(22), p.muted, anchor="mm")
        return

    px0, py0, px1, py1 = x0 + 28, y0 + 112, x1 - 28, y1 - 52
    peak = max(values)
    n = len(values)
    slot = (px1 - px0) / n
    bar_w = max(2, min(24, slot * 0.68))
    for frac in (0.5, 1.0):
        gy = py1 - (py1 - py0) * frac
        cv.draw.line([(px0, gy), (px1, gy)], fill=p.hairline, width=1)

    peak_i = max(range(n), key=lambda i: values[i])
    for i, v in enumerate(values):
        if v <= 0:
            continue
        cx = px0 + slot * (i + 0.5)
        h = max(3, (py1 - py0) * v / peak)
        color = p.primary if i == peak_i else _mix(p.surface, p.primary, 0.62)
        r = int(min(4, bar_w / 2))
        cv.draw.rounded_rectangle((cx - bar_w / 2, py1 - h, cx + bar_w / 2, py1), radius=r, fill=color, corners=(True, True, False, False))
    cv.draw.line([(px0, py1), (px1, py1)], fill=_mix(p.surface, p.text, 0.25), width=2)
    # Sparse x labels: first, middle, last (plus peak, if it does not collide).
    idx = sorted({0, n // 2, n - 1})
    for i in idx:
        anchor = "la" if i == 0 else ("ra" if i == n - 1 else "ma")
        x = px0 if i == 0 else (px1 if i == n - 1 else px0 + slot * (i + 0.5))
        cv.text((x, py1 + 14), labels[i], REGULAR(17), p.muted, anchor=anchor)
    pcx = px0 + slot * (peak_i + 0.5)
    label = f"Peak {_compact(peak)} · {labels[peak_i]}"
    lw = cv.text_w(label, BOLD(17))
    lx = min(max(pcx - lw / 2, px0), px1 - lw)
    ly = y0 + 74
    cv.draw.rounded_rectangle((lx - 10, ly - 4, lx + lw + 10, ly + 24), radius=12, fill=p.raised)
    cv.text((lx, ly), label, BOLD(17), p.text)


def _spotlight(cv: Canvas, box, h: Dict[str, Any]) -> None:
    x0, y0, x1, y1 = box
    p = cv.pal
    cv.card(box, radius=22)
    mvp = h.get("most_active_user") or {}
    cv.tracked((x0 + 28, y0 + 26), "MVP", BOLD(18), p.primary, 3)
    if mvp:
        cv.avatar((x0 + 70, y0 + 106), 40, str(mvp.get("username") or "?"))
        cv.text((x0 + 128, y0 + 74), str(mvp.get("username") or "?"), BOLD(32), p.text, max_w=x1 - x0 - 150)
        cv.text((x0 + 128, y0 + 116), f"{_format_number(mvp.get('event_count', 0))} interactions", REGULAR(20), p.text2)
    else:
        cv.text((x0 + 28, y0 + 80), "No standout viewer yet", REGULAR(22), p.muted)

    rows: List[Tuple[str, str, str]] = []
    big = h.get("biggest_bit_donation") or {}
    if big.get("amount"):
        rows.append(("Biggest cheer", str(big.get("username") or "?"), f"{_format_number(big['amount'])} bits"))
    gift = (h.get("top_gift_subs") or [None])[0]
    if gift and gift.get("total"):
        rows.append(("Top gifter", str(gift.get("username") or "?"), f"{_format_number(gift['total'])} subs"))
    streak = h.get("biggest_watch_streak") or {}
    if streak.get("amount"):
        rows.append(("Longest streak", str(streak.get("username") or "?"), f"{_format_number(streak['amount'])} streams"))
    chatter = h.get("top_chatter") or {}
    if chatter.get("event_count") and len(rows) < 3:
        rows.append(("Top chatter", str(chatter.get("username") or "?"), f"{_format_number(chatter['event_count'])} msgs"))

    ry = y0 + 160
    row_h = max(34, min(54, (y1 - ry - 12) // max(1, min(3, len(rows))) if rows else 54))
    for kicker, name, value in rows[:3]:
        if ry + row_h > y1 - 8:
            break
        cv.draw.line([(x0 + 28, ry), (x1 - 28, ry)], fill=p.hairline, width=1)
        ty = ry + (row_h - 22) // 2
        cv.text((x0 + 28, ty + 1), kicker, REGULAR(18), p.muted)
        vw = cv.text_w(value, BOLD(19))
        cv.text((x1 - 28, ty), value, BOLD(19), p.text2, anchor="ra")
        name_x = x0 + 28 + 150
        cv.text((name_x, ty), name, BOLD(19), p.text, max_w=x1 - 28 - vw - 18 - name_x)
        ry += row_h


def _mvp_strip(cv: Canvas, box, h: Dict[str, Any]) -> None:
    """One-line MVP card for the portrait layout."""
    x0, y0, x1, y1 = box
    p = cv.pal
    cv.card(box, radius=22)
    mvp = h.get("most_active_user") or {}
    if not mvp:
        cv.text((x0 + 28, (y0 + y1) // 2), "No standout viewer yet", REGULAR(22), p.muted, anchor="lm")
        return
    cy = (y0 + y1) // 2
    cv.avatar((x0 + 62, cy), 30, str(mvp.get("username") or "?"))
    cv.tracked((x0 + 110, y0 + 20), "MVP", BOLD(16), p.primary, 3)
    value = f"{_format_number(mvp.get('event_count', 0))} interactions"
    vw = cv.text_w(value, REGULAR(20))
    cv.text((x0 + 110, y0 + 44), str(mvp.get("username") or "?"), BOLD(28), p.text, max_w=x1 - x0 - 170 - vw)
    cv.text((x1 - 28, cy), value, REGULAR(20), p.text2, anchor="rm")


def _leaderboard(cv: Canvas, box, title: str, entries: Sequence[Dict[str, Any]], key: str, rows: int = 5) -> None:
    x0, y0, x1, y1 = box
    p = cv.pal
    cv.card(box, radius=22)
    cv.text((x0 + 24, y0 + 22), title, BOLD(22), p.text, max_w=x1 - x0 - 48)
    entries = [e for e in entries if int(e.get(key, 0) or 0) > 0][:rows]
    if not entries:
        cv.text((x0 + 24, y0 + 70), "Nobody yet", REGULAR(19), p.muted)
        return
    peak = max(int(e.get(key, 0) or 0) for e in entries) or 1
    top = y0 + 66
    row_h = min(44, (y1 - top - 14) / rows)
    for i, e in enumerate(entries):
        ry = top + i * row_h
        v = int(e.get(key, 0) or 0)
        name = str(e.get("username") or "?")
        rank_color = p.primary if i == 0 else p.muted
        cv.text((x0 + 24, ry), f"{i + 1}", BOLD(19), rank_color)
        vt = _compact(v)
        vw = cv.text_w(vt, BOLD(19))
        cv.text((x1 - 24, ry), vt, BOLD(19), p.text2, anchor="ra")
        cv.text((x0 + 52, ry), name, BOLD(19) if i == 0 else REGULAR(19), p.text, max_w=x1 - 24 - vw - 16 - (x0 + 52))
        bar_y = ry + 26
        bar_x0, bar_x1 = x0 + 52, x1 - 24
        if row_h >= 32:
            cv.draw.rounded_rectangle((bar_x0, bar_y, bar_x1, bar_y + 4), radius=2, fill=p.track)
            cv.draw.rounded_rectangle(
                (bar_x0, bar_y, bar_x0 + max(6, (bar_x1 - bar_x0) * v / peak), bar_y + 4),
                radius=3,
                fill=p.primary if i == 0 else _mix(p.surface, p.primary, 0.55),
            )


def _footer(cv: Canvas, box, h: Dict[str, Any], source: str) -> None:
    x0, y0, x1, _ = box
    p = cv.pal
    parts = [
        ("Follows", h.get("total_follows")),
        ("Raids", h.get("total_raids")),
        ("Channel points", h.get("total_channel_points")),
        ("Donations", h.get("total_donations")),
        ("Giveaway entries", h.get("total_giveaway_entries")),
    ]
    note = ("Lifetime totals · " if source == "lifetime" else "") + "Made with Mycelian"
    limit = x1 - cv.text_w(note, REGULAR(19)) - 40
    x = x0
    for label, val in parts:
        if not val:
            continue
        v = _compact(val)
        width = cv.text_w(v, BOLD(20)) + 8 + cv.text_w(label, REGULAR(19))
        if x + width > limit:
            break
        cv.text((x, y0), v, BOLD(20), p.text)
        cv.text((x + cv.text_w(v, BOLD(20)) + 8, y0 + 1), label, REGULAR(19), p.muted)
        x += width + 28
    cv.text((x1, y0 + 1), note, REGULAR(19), p.muted, anchor="ra")


# ---------------------------------------------------------------------------
# layouts
# ---------------------------------------------------------------------------

def _kpi_specs(h: Dict[str, Any], deltas: Dict[str, Optional[float]]):
    return [
        ("Active viewers", float(h.get("unique_users", 0) or 0), deltas.get("viewers")),
        ("Chat messages", float(h.get("total_chat_messages", 0) or 0), deltas.get("chat")),
        ("Bits cheered", float(h.get("total_bits", 0) or 0), deltas.get("bits")),
        ("Subs", float(h.get("total_subs", (h.get("total_new_subs", 0) or 0) + (h.get("total_resubs", 0) or 0)) or 0), deltas.get("subs")),
        ("Gifted subs", float(h.get("total_gift_subs", 0) or 0), deltas.get("gifts")),
    ]


def _leader_specs(h: Dict[str, Any]):
    return [
        ("Top chatters", h.get("top_chat_messages") or [], "count"),
        ("Top cheerers", h.get("top_bits") or [], "total"),
        ("Top gifters", h.get("top_gift_subs") or [], "total"),
        ("Channel points", h.get("top_channel_points") or [], "total"),
    ]


def _render_wide(cv: Canvas, h, start, end, streamer, extra, source) -> None:
    m = 64
    W, H = cv.w, cv.h
    cv.glow((W - 180, 80), 420, cv.pal.primary, 60)
    cv.glow((120, H - 60), 380, cv.pal.primary, 34)
    _header(cv, (m, 56, W - m, 200), streamer, start, end, False)

    deltas = extra.get("deltas") or {}
    kpis = _kpi_specs(h, deltas)
    gap = 22
    ky0, ky1 = 232, 392
    kw = (W - 2 * m - gap * (len(kpis) - 1)) / len(kpis)
    for i, (label, value, pct) in enumerate(kpis):
        kx = m + i * (kw + gap)
        _kpi(cv, (kx, ky0, kx + kw, ky1), label, value, pct, 72)

    cy0, cy1 = ky1 + gap, 712
    split = m + (W - 2 * m) * 0.64
    _activity_chart(cv, (m, cy0, split - gap / 2, cy1), extra.get("activity_labels") or [], extra.get("activity_values") or [], extra.get("activity_bucket", "day"))
    _spotlight(cv, (split + gap / 2, cy0, W - m, cy1), h)

    ly0, ly1 = cy1 + gap, H - 92
    boards = _leader_specs(h)
    lw = (W - 2 * m - gap * 3) / 4
    for i, (title, entries, key) in enumerate(boards):
        lx = m + i * (lw + gap)
        _leaderboard(cv, (lx, ly0, lx + lw, ly1), title, entries, key, rows=5)
    _footer(cv, (m, H - 62, W - m, H - 30), h, source)


def _render_portrait(cv: Canvas, h, start, end, streamer, extra, source) -> None:
    m = 56
    W, H = cv.w, cv.h
    cv.glow((W - 120, 60), 360, cv.pal.primary, 60)
    cv.glow((80, H - 80), 320, cv.pal.primary, 30)
    _header(cv, (m, 52, W - m, 220), streamer, start, end, True)

    deltas = extra.get("deltas") or {}
    kpis = _kpi_specs(h, deltas)
    gap = 18
    # 2 + 3 grid: the two headline numbers get the wider tiles.
    y = 236
    row_h = 150
    w2 = (W - 2 * m - gap) / 2
    for i, (label, value, pct) in enumerate(kpis[:2]):
        x = m + i * (w2 + gap)
        _kpi(cv, (x, y, x + w2, y + row_h), label, value, pct, 62)
    y += row_h + gap
    w3 = (W - 2 * m - gap * 2) / 3
    for i, (label, value, pct) in enumerate(kpis[2:]):
        x = m + i * (w3 + gap)
        _kpi(cv, (x, y, x + w3, y + row_h), label, value, pct, 52)
    y += row_h + gap

    _activity_chart(cv, (m, y, W - m, y + 300), extra.get("activity_labels") or [], extra.get("activity_values") or [], extra.get("activity_bucket", "day"))
    y += 300 + gap

    _mvp_strip(cv, (m, y, W - m, y + 92), h)
    y += 92 + gap

    boards = _leader_specs(h)[:2]
    bw = (W - 2 * m - gap) / 2
    board_h = H - 96 - y
    for i, (title, entries, key) in enumerate(boards):
        x = m + i * (bw + gap)
        _leaderboard(cv, (x, y, x + bw, y + board_h), title, entries, key, rows=5)
    _footer(cv, (m, H - 62, W - m, H - 30), h, source)


def render_highlights_image(
    highlights: Dict[str, Any],
    start_date: datetime,
    end_date: datetime,
    *,
    streamer_name: str = "Mycelian",
    theme: Optional[ThemeColors] = None,
    layout: str = "wide",
    extra: Optional[Dict[str, Any]] = None,
    source: str = "event_log",
) -> Image.Image:
    """Render the highlights card and return it as a PIL image."""
    if theme is None:
        tm = get_theme_manager()
        tm.load_themes_from_directory()
        theme = tm.get_theme()
    size = LAYOUTS.get(layout, LAYOUTS["wide"])
    cv = Canvas(size, Palette.from_theme(theme))
    render = _render_portrait if layout == "portrait" else _render_wide
    render(cv, highlights, start_date, end_date, streamer_name or "Mycelian", extra or {}, source)
    return cv.img


def generate_highlights_image(
    highlights: Dict[str, Any],
    start_date: datetime,
    end_date: datetime,
    output_path: str,
    streamer_name: str = "Mycelian",
    theme: Optional[ThemeColors] = None,
    layout: str = "wide",
    extra: Optional[Dict[str, Any]] = None,
    source: str = "event_log",
) -> bool:
    """Render the highlights card to ``output_path`` (PNG). Returns True on success."""
    try:
        img = render_highlights_image(
            highlights,
            start_date,
            end_date,
            streamer_name=streamer_name,
            theme=theme,
            layout=layout,
            extra=extra,
            source=source,
        )
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        img.save(output_path, "PNG")
        logger.info("Statistics highlights image saved to %s", output_path)
        return True
    except Exception as e:
        logger.error("Error generating highlights image: %s", e, exc_info=True)
        try:
            from .notification_engine import nav_actions_settings, notify_critical

            notify_critical(
                "Could not export statistics highlights image. Check logs.",
                dedupe_key="stats:highlights_export",
                actions=nav_actions_settings("Statistics"),
            )
        except Exception:
            pass
        return False
