#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024 Mycelian

Statistics export module for generating highlights images from event-log data.
Uses Pillow; colors follow ThemeManager when available.
"""

from __future__ import annotations

import logging
import os
import re
from colorsys import hls_to_rgb, rgb_to_hls
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .path_utils import get_data_path
from .theme_manager import ThemeColors, get_theme_manager

logger = logging.getLogger(__name__)

_RGB_RE = re.compile(
    r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+)\s*)?\)",
    re.IGNORECASE,
)


def parse_css_color(s: str) -> Tuple[int, int, int, int]:
    """Parse ``rgb()`` / ``rgba()`` string to RGBA bytes for Pillow."""
    if not s or not isinstance(s, str):
        return (128, 128, 128, 255)
    m = _RGB_RE.search(s.strip())
    if not m:
        return (128, 128, 128, 255)
    r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
    a = 255
    if m.group(4) is not None:
        a = int(round(float(m.group(4)) * 255))
    return (r, g, b, a)


def _rgb_tuple(c: Tuple[int, int, int, int]) -> Tuple[int, int, int]:
    return (c[0], c[1], c[2])


def _accent_colors(theme: ThemeColors, n: int) -> List[Tuple[int, int, int]]:
    """Distinct accent hues derived from theme status / primary colors."""
    candidates = [
        parse_css_color(theme.primary),
        parse_css_color(theme.success),
        parse_css_color(theme.info),
        parse_css_color(theme.warning),
        parse_css_color(theme.error),
        parse_css_color(theme.border_accent),
    ]
    out: List[Tuple[int, int, int]] = []
    for i in range(n):
        if i < len(candidates):
            out.append(_rgb_tuple(candidates[i]))
            continue
        base = _rgb_tuple(parse_css_color(theme.primary))
        h, l, s = rgb_to_hls(base[0] / 255.0, base[1] / 255.0, base[2] / 255.0)
        h = (h + (i - len(candidates) + 1) * 0.12) % 1.0
        rr, gg, bb = hls_to_rgb(h, min(0.85, max(0.25, l)), min(0.9, max(0.35, s)))
        out.append(
            (int(rr * 255), int(gg * 255), int(bb * 255)),
        )
    return out


def _load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    fonts_dir = get_data_path(os.path.join("assets", "default_assets", "fonts"))
    try:
        path = os.path.join(fonts_dir, name)
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    except Exception:
        pass
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _draw_gradient(
    img: Image.Image, top: Tuple[int, int, int], bottom: Tuple[int, int, int]
) -> None:
    draw = ImageDraw.Draw(img)
    w, h = img.size
    for y in range(h):
        ratio = y / max(h - 1, 1)
        r = int(top[0] + (bottom[0] - top[0]) * ratio)
        g = int(top[1] + (bottom[1] - top[1]) * ratio)
        b = int(top[2] + (bottom[2] - top[2]) * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b))


def _draw_glow_circle(
    img: Image.Image, centre: Tuple[int, int], radius: int, color: Tuple[int, int, int]
) -> None:
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    for i in range(radius, 0, -3):
        alpha = int(28 * (i / radius))
        draw.ellipse(
            [centre[0] - i, centre[1] - i, centre[0] + i, centre[1] + i],
            fill=(*color, alpha),
        )
    img.paste(Image.alpha_composite(img.convert("RGBA"), glow))


def _draw_rounded_rect(
    draw: ImageDraw.ImageDraw,
    bbox: Tuple[int, int, int, int],
    radius: int,
    fill: Optional[Tuple[int, ...]] = None,
    outline: Optional[Tuple[int, ...]] = None,
    outline_width: int = 2,
) -> None:
    draw.rounded_rectangle(bbox, radius=radius, fill=fill, outline=outline, width=outline_width)


def _format_number(n: int) -> str:
    return f"{n:,}"


def _truncate_username(s: str, max_len: int = 14) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 2] + ".."


def _leaderboard_lines(
    entries: List[Dict[str, Any]], value_key: str
) -> List[Tuple[int, str, int]]:
    """Return (rank, display_line, rank) for up to 5 rows; value_key is ``total`` or ``count``."""
    lines: List[Tuple[int, str, int]] = []
    for i, e in enumerate(entries[:5], start=1):
        un = str(e.get("username") or "-")
        v = int(e.get(value_key, 0) or 0)
        lines.append(
            (i, f"#{i}  {_truncate_username(un)}  {_format_number(v)}", i),
        )
    return lines


def generate_highlights_image(
    highlights: Dict[str, Any],
    start_date: datetime,
    end_date: datetime,
    output_path: str,
    streamer_name: str = "Mycelian",
    theme: Optional[ThemeColors] = None,
) -> bool:
    """Generate a statistics highlights PNG from event-log aggregates.

    Args:
        highlights: Dictionary from ``StatisticsManager.get_date_range_highlights``.
        start_date: Start of the date range.
        end_date: End of the date range.
        output_path: File path to save the PNG image to.
        streamer_name: Name in the footer (``Powered by …``).
        theme: Optional ``ThemeColors``; defaults to ``ThemeManager`` current theme.

    Returns:
        True if the image was successfully generated.
    """
    print(
        "[highlights render] enter output_path=",
        output_path,
        "total_events=",
        highlights.get("total_events"),
        "fallback_partial=",
        highlights.get("_fallback_partial"),
    )
    try:
        if theme is None:
            tm = get_theme_manager()
            tm.load_themes_from_directory()
            theme = tm.get_theme()

        bg_top = _rgb_tuple(parse_css_color(theme.bg_base))
        bg_bottom = _rgb_tuple(parse_css_color(theme.bg_surface))
        text_primary = _rgb_tuple(parse_css_color(theme.text_primary))
        text_secondary = _rgb_tuple(parse_css_color(theme.text_secondary))
        text_muted = _rgb_tuple(parse_css_color(theme.text_muted))
        card_fill = _rgb_tuple(parse_css_color(theme.bg_elevated))
        primary_soft = _rgb_tuple(parse_css_color(theme.primary))

        WIDTH, HEIGHT = 1920, 1280
        img = Image.new("RGBA", (WIDTH, HEIGHT))
        _draw_gradient(img, bg_top, bg_bottom)

        _draw_glow_circle(img, (220, 220), 320, primary_soft)
        _draw_glow_circle(img, (1700, 200), 260, primary_soft)
        _draw_glow_circle(img, (960, 980), 360, primary_soft)

        draw = ImageDraw.Draw(img)

        font_title = _load_font("Anton-Regular.ttf", 52)
        font_date = _load_font("Roboto-Bold.ttf", 26)
        font_card_title = _load_font("Roboto-Bold.ttf", 18)
        font_card_value = _load_font("Anton-Regular.ttf", 42)
        font_card_label = _load_font("Roboto-Regular.ttf", 15)
        font_detail = _load_font("Roboto-Regular.ttf", 14)
        font_lb1 = _load_font("Roboto-Bold.ttf", 19)
        font_lb = _load_font("Roboto-Regular.ttf", 15)
        font_brand = _load_font("Roboto-Regular.ttf", 18)

        margin = 40
        title_text = "STREAM HIGHLIGHTS"
        title_bbox = draw.textbbox((0, 0), title_text, font=font_title)
        title_h = title_bbox[3] - title_bbox[1]
        title_y = margin
        draw.text((margin, title_y), title_text, fill=text_primary, font=font_title)

        date_fmt = "%b %d, %Y"
        date_text = f"{start_date.strftime(date_fmt)}  –  {end_date.strftime(date_fmt)}"
        date_bbox = draw.textbbox((0, 0), date_text, font=font_date)
        date_w = date_bbox[2] - date_bbox[0]
        date_h = date_bbox[3] - date_bbox[1]
        date_x = WIDTH - margin - date_w
        date_y = title_y + (title_h - date_h) // 2
        draw.text((date_x, date_y), date_text, fill=text_secondary, font=font_date)

        accents = _accent_colors(theme, 12)
        lifetime_fb = bool(highlights.get("_lifetime_fallback"))
        community_users_label = (
            "Unique users (lifetime aggregates)"
            if lifetime_fb
            else "Unique users (event log)"
        )

        community_detail_lines: List[str] = [
            f"Follows: {_format_number(int(highlights.get('total_follows', 0) or 0))}",
            f"Raids: {_format_number(int(highlights.get('total_raids', 0) or 0))}",
            f"Watch streak alerts: {_format_number(int(highlights.get('total_watch_streak_alerts', 0) or 0))}",
        ]
        _bw = highlights.get("biggest_watch_streak")
        if _bw and int(_bw.get("amount", 0) or 0) > 0:
            community_detail_lines.append(
                f"Top streak (period): {int(_bw.get('amount', 0) or 0)} — {_bw.get('username') or '?'}"
            )

        def card_spec(
            key: str,
            title: str,
            value: str,
            label: str,
            detail_lines: List[str],
            entries: List[Dict[str, Any]],
            value_key: str,
            accent_idx: int,
        ) -> Dict[str, Any]:
            return {
                "key": key,
                "title": title,
                "value": value,
                "label": label,
                "detail_lines": detail_lines,
                "entries": entries or [],
                "value_key": value_key,
                "accent_idx": accent_idx,
            }

        cards: List[Dict[str, Any]] = [
            card_spec(
                "bits",
                "BITS",
                _format_number(int(highlights.get("total_bits", 0) or 0)),
                "Total bits",
                [],
                highlights.get("top_bits") or [],
                "total",
                0,
            ),
            card_spec(
                "points",
                "POINTS",
                _format_number(int(highlights.get("total_channel_points", 0) or 0)),
                "Channel points redeemed",
                [],
                highlights.get("top_channel_points") or [],
                "total",
                1,
            ),
            card_spec(
                "new_subs",
                "NEW SUBS",
                _format_number(int(highlights.get("total_new_subs", 0) or 0)),
                "New subscriptions",
                [],
                highlights.get("top_new_subs") or [],
                "count",
                2,
            ),
            card_spec(
                "resubs",
                "RESUBS",
                _format_number(int(highlights.get("total_resubs", 0) or 0)),
                "Resubscriptions",
                [],
                highlights.get("top_resubs") or [],
                "count",
                3,
            ),
            card_spec(
                "gift_subs",
                "GIFT SUBS",
                _format_number(int(highlights.get("total_gift_subs", 0) or 0)),
                "Gifted subs (total)",
                [],
                highlights.get("top_gift_subs") or [],
                "total",
                4,
            ),
            card_spec(
                "donations",
                "DONATIONS",
                _format_number(int(highlights.get("total_donations", 0) or 0)),
                "Donation alerts",
                [],
                highlights.get("top_donations") or [],
                "count",
                5,
            ),
            card_spec(
                "chat",
                "CHAT",
                _format_number(int(highlights.get("total_chat_messages", 0) or 0)),
                "Chat messages",
                [],
                highlights.get("top_chat_messages") or [],
                "count",
                6,
            ),
            card_spec(
                "giveaways",
                "GIVEAWAYS",
                _format_number(int(highlights.get("total_giveaway_entries", 0) or 0)),
                "Giveaway pool entries",
                [
                    f"Wins: {_format_number(int(highlights.get('total_giveaway_wins', 0) or 0))}",
                    f"Rounds completed: {_format_number(int(highlights.get('total_giveaway_rounds', 0) or 0))}",
                ],
                highlights.get("top_giveaway_entries") or [],
                "count",
                7,
            ),
            card_spec(
                "community",
                "COMMUNITY",
                _format_number(int(highlights.get("unique_users", 0) or 0)),
                community_users_label,
                community_detail_lines,
                [],
                "count",
                8,
            ),
        ]

        grid_cols = 3
        grid_rows = 3
        header_bottom = title_y + title_h + 36
        footer_h = 44
        usable_h = HEIGHT - header_bottom - footer_h - margin
        h_gap = 22
        v_gap = 22
        card_w = (WIDTH - 2 * margin - (grid_cols - 1) * h_gap) // grid_cols
        card_h = (usable_h - (grid_rows - 1) * v_gap) // grid_rows
        grid_y_start = header_bottom

        for idx, card in enumerate(cards):
            col = idx % grid_cols
            row = idx // grid_cols
            cx = margin + col * (card_w + h_gap)
            cy = grid_y_start + row * (card_h + v_gap)
            accent = accents[card["accent_idx"] % len(accents)]

            _draw_rounded_rect(
                draw,
                (cx, cy, cx + card_w, cy + card_h),
                radius=16,
                fill=(*card_fill, 245),
                outline=accent,
                outline_width=2,
            )

            pad = 14
            draw.text((cx + pad, cy + pad), card["title"], fill=accent, font=font_card_title)

            value_text = card["value"]
            if len(value_text) > 18:
                value_text = value_text[:16] + ".."
            draw.text(
                (cx + pad, cy + pad + 28),
                value_text,
                fill=text_primary,
                font=font_card_value,
            )

            ly = cy + pad + 82
            draw.text((cx + pad, ly), card["label"], fill=text_secondary, font=font_card_label)
            ly += 22
            for dl in card["detail_lines"]:
                draw.text((cx + pad, ly), dl, fill=text_muted, font=font_detail)
                ly += 18

            ly += 6
            vk = card["value_key"]
            lb = _leaderboard_lines(card["entries"], vk)

            if card["key"] == "giveaways":
                left_x = cx + pad
                right_x = cx + card_w // 2 + 10
                draw.text((left_x, ly), "Top entrants", fill=text_muted, font=font_detail)
                draw.text((right_x, ly), "Top winners", fill=text_muted, font=font_detail)
                ly += 20
                ent_lines = _leaderboard_lines(
                    highlights.get("top_giveaway_entries") or [], "count"
                )
                win_lines = _leaderboard_lines(
                    highlights.get("top_giveaway_wins") or [], "count"
                )
                for i in range(5):
                    row_y = ly + i * 22
                    if i < len(ent_lines):
                        rank, line, _ = ent_lines[i]
                        fnt = font_lb1 if rank == 1 else font_lb
                        draw.text(
                            (left_x, row_y),
                            line,
                            fill=text_primary if rank == 1 else text_secondary,
                            font=fnt,
                        )
                    if i < len(win_lines):
                        rank, line, _ = win_lines[i]
                        fnt = font_lb1 if rank == 1 else font_lb
                        draw.text(
                            (right_x, row_y),
                            line,
                            fill=text_primary if rank == 1 else text_secondary,
                            font=fnt,
                        )
            elif card["key"] == "community":
                left_x = cx + pad
                right_x = cx + card_w // 2 + 10
                draw.text((left_x, ly), "Top followers", fill=text_muted, font=font_detail)
                draw.text((right_x, ly), "Top raiders", fill=text_muted, font=font_detail)
                ly += 20
                fol_lines = _leaderboard_lines(
                    highlights.get("top_follows") or [], "count"
                )
                raid_lines = _leaderboard_lines(
                    highlights.get("top_raids") or [], "count"
                )
                for i in range(5):
                    row_y = ly + i * 22
                    if i < len(fol_lines):
                        rank, line, _ = fol_lines[i]
                        fnt = font_lb1 if rank == 1 else font_lb
                        draw.text(
                            (left_x, row_y),
                            line,
                            fill=text_primary if rank == 1 else text_secondary,
                            font=fnt,
                        )
                    if i < len(raid_lines):
                        rank, line, _ = raid_lines[i]
                        fnt = font_lb1 if rank == 1 else font_lb
                        draw.text(
                            (right_x, row_y),
                            line,
                            fill=text_primary if rank == 1 else text_secondary,
                            font=fnt,
                        )
            else:
                for rank, line, _ in lb:
                    fnt = font_lb1 if rank == 1 else font_lb
                    draw.text(
                        (cx + pad, ly),
                        line,
                        fill=text_primary if rank == 1 else text_secondary,
                        font=fnt,
                    )
                    ly += 24 if rank == 1 else 20

        branding = "Powered by Mycelian"
        bb = draw.textbbox((0, 0), branding, font=font_brand)
        bw = bb[2] - bb[0]
        draw.text(
            ((WIDTH - bw) // 2, HEIGHT - margin - (bb[3] - bb[1])),
            branding,
            fill=text_muted,
            font=font_brand,
        )

        os.makedirs(
            os.path.dirname(output_path) if os.path.dirname(output_path) else ".",
            exist_ok=True,
        )
        img = img.convert("RGB")
        img.save(output_path, "PNG", quality=95)
        logger.info("Statistics highlights image saved to %s", output_path)
        print("[highlights render] saved OK ->", output_path)
        return True

    except Exception as e:
        logger.error("Error generating highlights image: %s", e, exc_info=True)
        print("[highlights render] FAILED:", repr(e))
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
