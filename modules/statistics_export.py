#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024 Mycelian

Statistics export module for generating vibrant highlight images.
Uses Pillow to render a flashy stats recap card that can be shared on social media.
"""

import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .path_utils import get_data_path

logger = logging.getLogger(__name__)

# --- Colour palette (neon / cyberpunk inspired) ---
BG_TOP = (18, 10, 40)  # Deep purple
BG_BOTTOM = (8, 15, 50)  # Dark blue
CARD_BG = (30, 20, 60, 200)  # Semi-transparent purple
CARD_BORDER_GLOW = [
    (0, 255, 255),  # Cyan
    (255, 0, 200),  # Magenta / Hot pink
    (0, 255, 100),  # Lime green
    (255, 215, 0),  # Gold
    (255, 80, 180),  # Pink
    (100, 200, 255),  # Light blue
]
TEXT_PRIMARY = (255, 255, 255)
TEXT_SECONDARY = (180, 180, 220)
TEXT_ACCENT = (0, 255, 255)  # Cyan
TITLE_COLOR = (255, 255, 255)
SUBTITLE_COLOR = (200, 180, 255)
BRANDING_COLOR = (120, 100, 180)


def _load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Attempt to load a font from the assets directory, falling back to default."""
    fonts_dir = get_data_path(os.path.join("assets", "default_assets", "fonts"))
    try:
        path = os.path.join(fonts_dir, name)
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    except Exception:
        pass
    # Fallback
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _draw_gradient(img: Image.Image, top: Tuple[int, ...], bottom: Tuple[int, ...]):
    """Draw a vertical linear gradient on *img* (in-place)."""
    draw = ImageDraw.Draw(img)
    w, h = img.size
    for y in range(h):
        ratio = y / h
        r = int(top[0] + (bottom[0] - top[0]) * ratio)
        g = int(top[1] + (bottom[1] - top[1]) * ratio)
        b = int(top[2] + (bottom[2] - top[2]) * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b))


def _draw_glow_circle(
    img: Image.Image, centre: Tuple[int, int], radius: int, color: Tuple[int, ...]
):
    """Draw a soft radial glow on *img* (additive blend)."""
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    for i in range(radius, 0, -2):
        alpha = int(40 * (i / radius))
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
):
    """Draw a rounded rectangle with optional outline."""
    draw.rounded_rectangle(bbox, radius=radius, fill=fill, outline=outline, width=outline_width)


def _format_number(n: int) -> str:
    """Format a number with commas."""
    return f"{n:,}"


def generate_highlights_image(
    highlights: Dict[str, Any],
    start_date: datetime,
    end_date: datetime,
    output_path: str,
    streamer_name: str = "Mycelian",
) -> bool:
    """Generate a vibrant statistics highlights image.

    Args:
        highlights: Dictionary returned by ``StatisticsManager.get_date_range_highlights``.
        start_date: Start of the date range.
        end_date: End of the date range.
        output_path: File path to save the PNG image to.
        streamer_name: Name to display in the branding footer.

    Returns:
        True if the image was successfully generated.
    """
    try:
        WIDTH, HEIGHT = 1920, 1080
        img = Image.new("RGBA", (WIDTH, HEIGHT))

        # --- Background gradient ---
        _draw_gradient(img, BG_TOP, BG_BOTTOM)

        # --- Glow accents (decorative background lights) ---
        _draw_glow_circle(img, (200, 200), 350, (120, 0, 255))
        _draw_glow_circle(img, (1700, 150), 300, (0, 200, 255))
        _draw_glow_circle(img, (960, 900), 400, (255, 0, 180))
        _draw_glow_circle(img, (400, 800), 250, (0, 255, 150))

        draw = ImageDraw.Draw(img)

        # --- Fonts ---
        font_title = _load_font("Anton-Regular.ttf", 72)
        font_subtitle = _load_font("Roboto-Bold.ttf", 32)
        font_date = _load_font("Roboto-Regular.ttf", 28)
        font_card_title = _load_font("Roboto-Bold.ttf", 22)
        font_card_value = _load_font("Anton-Regular.ttf", 52)
        font_card_label = _load_font("Roboto-Regular.ttf", 18)
        font_card_detail = _load_font("Roboto-Regular.ttf", 16)
        font_branding = _load_font("Roboto-Regular.ttf", 20)

        # --- Title ---
        title_text = "STREAM HIGHLIGHTS"
        title_bbox = draw.textbbox((0, 0), title_text, font=font_title)
        title_w = title_bbox[2] - title_bbox[0]
        title_x = (WIDTH - title_w) // 2
        title_y = 40

        # Title glow effect (draw text multiple times with blur)
        for offset in [3, 2, 1]:
            draw.text(
                (title_x, title_y),
                title_text,
                fill=(0, 255, 255, 80),
                font=font_title,
            )
        draw.text((title_x, title_y), title_text, fill=TITLE_COLOR, font=font_title)

        # --- Date range subtitle ---
        date_fmt = "%b %d, %Y"
        date_text = f"{start_date.strftime(date_fmt)}  -  {end_date.strftime(date_fmt)}"
        date_bbox = draw.textbbox((0, 0), date_text, font=font_date)
        date_w = date_bbox[2] - date_bbox[0]
        draw.text(
            ((WIDTH - date_w) // 2, title_y + 90),
            date_text,
            fill=SUBTITLE_COLOR,
            font=font_date,
        )

        # --- Stat cards grid ---
        # Build card data from highlights
        cards = []

        total_bits = highlights.get("total_bits", 0)
        top_bit_donor = highlights.get("top_bit_donor")
        cards.append(
            {
                "icon": "BITS",
                "value": _format_number(total_bits),
                "label": "Total Bits",
                "detail": f"Top Donor: {top_bit_donor['username']} ({_format_number(int(top_bit_donor['total']))})"
                if top_bit_donor
                else "No bit events",
                "color_idx": 0,
            }
        )

        total_subs = highlights.get("total_subs", 0)
        total_gift = highlights.get("total_gift_subs", 0)
        cards.append(
            {
                "icon": "SUBS",
                "value": _format_number(total_subs + total_gift),
                "label": "Total Subs",
                "detail": f"New/Resub: {_format_number(total_subs)}  |  Gifted: {_format_number(total_gift)}",
                "color_idx": 1,
            }
        )

        total_donations = highlights.get("total_donations", 0)
        cards.append(
            {
                "icon": "DONATIONS",
                "value": _format_number(total_donations),
                "label": "Donations",
                "detail": "",
                "color_idx": 2,
            }
        )

        top_gifter = highlights.get("top_gifter")
        most_active = highlights.get("most_active_user")
        cards.append(
            {
                "icon": "MVP",
                "value": most_active["username"] if most_active else "-",
                "label": "Most Active User",
                "detail": f"{_format_number(most_active['event_count'])} events"
                if most_active
                else "No events",
                "color_idx": 3,
            }
        )

        total_points = highlights.get("total_channel_points", 0)
        cards.append(
            {
                "icon": "POINTS",
                "value": _format_number(total_points),
                "label": "Channel Points",
                "detail": "",
                "color_idx": 4,
            }
        )

        total_follows = highlights.get("total_follows", 0)
        total_raids = highlights.get("total_raids", 0)
        unique_users = highlights.get("unique_users", 0)
        cards.append(
            {
                "icon": "COMMUNITY",
                "value": _format_number(unique_users),
                "label": "Unique Users",
                "detail": f"Follows: {_format_number(total_follows)}  |  Raids: {_format_number(total_raids)}",
                "color_idx": 5,
            }
        )

        # --- Draw cards in a 3x2 grid ---
        grid_cols = 3
        grid_rows = 2
        card_w = 520
        card_h = 280
        grid_x_start = (WIDTH - (grid_cols * card_w + (grid_cols - 1) * 30)) // 2
        grid_y_start = 190
        h_gap = 30
        v_gap = 30

        for idx, card in enumerate(cards):
            col = idx % grid_cols
            row = idx // grid_cols
            cx = grid_x_start + col * (card_w + h_gap)
            cy = grid_y_start + row * (card_h + v_gap)

            accent_color = CARD_BORDER_GLOW[card["color_idx"] % len(CARD_BORDER_GLOW)]

            # Card background with glow border
            _draw_rounded_rect(
                draw,
                (cx, cy, cx + card_w, cy + card_h),
                radius=20,
                fill=(25, 18, 55, 220),
                outline=accent_color,
                outline_width=3,
            )

            # Inner glow line at top
            _draw_rounded_rect(
                draw,
                (cx + 4, cy + 4, cx + card_w - 4, cy + 6),
                radius=2,
                fill=(*accent_color, 120),
            )

            # Card icon/header text
            draw.text(
                (cx + 30, cy + 20),
                card["icon"],
                fill=accent_color,
                font=font_card_title,
            )

            # Card value (large number or text)
            value_text = card["value"]
            # Truncate long usernames
            if len(value_text) > 16:
                value_text = value_text[:14] + ".."
            draw.text(
                (cx + 30, cy + 65),
                value_text,
                fill=TEXT_PRIMARY,
                font=font_card_value,
            )

            # Card label
            draw.text(
                (cx + 30, cy + 140),
                card["label"],
                fill=TEXT_SECONDARY,
                font=font_card_label,
            )

            # Card detail line
            if card["detail"]:
                draw.text(
                    (cx + 30, cy + 170),
                    card["detail"],
                    fill=(*accent_color, 200),
                    font=font_card_detail,
                )

            # Decorative corner dots
            dot_r = 4
            draw.ellipse(
                [cx + card_w - 30, cy + 20, cx + card_w - 30 + dot_r * 2, cy + 20 + dot_r * 2],
                fill=accent_color,
            )

        # --- Biggest bit donation callout (if exists) ---
        biggest_bit = highlights.get("biggest_bit_donation")
        if biggest_bit and biggest_bit.get("amount", 0) > 0:
            callout_y = grid_y_start + grid_rows * (card_h + v_gap) + 20
            callout_text = (
                f"Biggest Single Bit Cheer: {biggest_bit['username']} "
                f"with {_format_number(int(biggest_bit['amount']))} bits!"
            )
            callout_bbox = draw.textbbox((0, 0), callout_text, font=font_subtitle)
            callout_w = callout_bbox[2] - callout_bbox[0]
            _draw_rounded_rect(
                draw,
                ((WIDTH - callout_w) // 2 - 30, callout_y - 10,
                 (WIDTH + callout_w) // 2 + 30, callout_y + 50),
                radius=15,
                fill=(40, 20, 80, 180),
                outline=(255, 215, 0),
                outline_width=2,
            )
            draw.text(
                ((WIDTH - callout_w) // 2, callout_y),
                callout_text,
                fill=(255, 215, 0),
                font=font_subtitle,
            )

        # --- Top gifter callout (if exists) ---
        if top_gifter:
            gifter_y = grid_y_start + grid_rows * (card_h + v_gap) + 80
            gifter_text = (
                f"Top Gift Sub Giver: {top_gifter['username']} "
                f"with {_format_number(top_gifter['total'])} gift subs!"
            )
            gifter_bbox = draw.textbbox((0, 0), gifter_text, font=font_subtitle)
            gifter_w = gifter_bbox[2] - gifter_bbox[0]
            _draw_rounded_rect(
                draw,
                ((WIDTH - gifter_w) // 2 - 30, gifter_y - 10,
                 (WIDTH + gifter_w) // 2 + 30, gifter_y + 50),
                radius=15,
                fill=(40, 20, 80, 180),
                outline=(255, 0, 200),
                outline_width=2,
            )
            draw.text(
                ((WIDTH - gifter_w) // 2, gifter_y),
                gifter_text,
                fill=(255, 100, 220),
                font=font_subtitle,
            )

        # --- Branding footer ---
        branding_text = f"Powered by {streamer_name}  |  Mycelian Streaming Toolkit"
        branding_bbox = draw.textbbox((0, 0), branding_text, font=font_branding)
        branding_w = branding_bbox[2] - branding_bbox[0]
        draw.text(
            ((WIDTH - branding_w) // 2, HEIGHT - 50),
            branding_text,
            fill=BRANDING_COLOR,
            font=font_branding,
        )

        # --- Decorative scan lines (subtle) ---
        for y in range(0, HEIGHT, 4):
            draw.line([(0, y), (WIDTH, y)], fill=(0, 0, 0, 8), width=1)

        # --- Save ---
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
        img = img.convert("RGB")
        img.save(output_path, "PNG", quality=95)
        logger.info(f"Statistics highlights image saved to {output_path}")
        return True

    except Exception as e:
        logger.error(f"Error generating highlights image: {e}", exc_info=True)
        return False
