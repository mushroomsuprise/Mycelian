# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Activity Feed event colours: the theme fields, their fallbacks and the shipped palettes.

Each event kind has one theme field (event_follow ...) and one CSS variable
(--color-event-follow ...). A theme without a value falls back to the built-in palette of
the theme with the same name, then to the status colour the feed used before these existed.
"""

from typing import Any, Dict, List, Tuple

# (key, label, icon, group, status fallback). The key names the field, the CSS variable and
# the feed's event kind ("donation" is a tip, "other" is any alert without its own kind).
EVENT_KINDS: Tuple[Tuple[str, str, str, str, str], ...] = (
    ("follow", "Follow", "favorite", "Twitch", "primary"),
    ("sub", "Sub", "star", "Twitch", "info"),
    ("resub", "Resub", "autorenew", "Twitch", "info"),
    ("giftsub", "Gift sub", "redeem", "Twitch", "success"),
    ("bits", "Bits", "diamond", "Twitch", "warning"),
    ("points", "Channel points", "toll", "Twitch", "error"),
    ("raid", "Raid", "groups", "Twitch", "error"),
    ("streak", "Watch streak", "local_fire_department", "Twitch", "warning"),
    ("hype", "Hype Train", "rocket_launch", "Twitch", "primary"),
    ("modiversary", "Modiversary", "shield", "Twitch", "info"),
    ("donation", "Tip", "payments", "Tips", "success"),
    ("member", "Member", "workspace_premium", "YouTube", "info"),
    ("milestone", "Milestone", "military_tech", "YouTube", "info"),
    ("giftmember", "Gift membership", "card_giftcard", "YouTube", "success"),
    ("superchat", "Super Chat", "chat", "YouTube", "success"),
    ("supersticker", "Super Sticker", "sticky_note_2", "YouTube", "warning"),
    ("other", "Other alerts", "notifications", "Other", "primary"),
)

EVENT_COLOR_FIELDS: Tuple[str, ...] = tuple(f"event_{key}" for key, *_ in EVENT_KINDS)
EVENT_FALLBACK: Dict[str, str] = {key: status for key, _l, _i, _g, status in EVENT_KINDS}

# Palettes shipped with the built-in themes. Dark keeps the colours the feed used before
# event colours existed; the others follow each theme's own accents (3:1 or better on
# its background). Installed theme files are never overwritten by an update, so a built-in
# theme missing these values takes them from here.
BUILTIN_EVENT_COLORS: Dict[str, Dict[str, str]] = {
    "arctic": {"follow": "rgb(53, 115, 181)", "sub": "rgb(102, 94, 226)", "resub": "rgb(5, 130, 164)", "giftsub": "rgb(14, 151, 67)", "bits": "rgb(163, 118, 15)", "points": "rgb(195, 56, 115)", "raid": "rgb(202, 58, 53)", "streak": "rgb(185, 94, 8)", "hype": "rgb(155, 82, 140)", "modiversary": "rgb(145, 78, 207)", "donation": "rgb(18, 144, 140)", "member": "rgb(102, 94, 226)", "milestone": "rgb(5, 130, 164)", "giftmember": "rgb(14, 151, 67)", "superchat": "rgb(18, 144, 140)", "supersticker": "rgb(163, 118, 15)", "other": "rgb(53, 115, 181)"},
    "crimson_night": {"follow": "rgb(235, 81, 70)", "sub": "rgb(88, 162, 253)", "resub": "rgb(8, 179, 211)", "giftsub": "rgb(38, 192, 93)", "bits": "rgb(210, 161, 15)", "points": "rgb(228, 107, 200)", "raid": "rgb(249, 100, 143)", "streak": "rgb(245, 128, 11)", "hype": "rgb(187, 96, 217)", "modiversary": "rgb(171, 137, 246)", "donation": "rgb(26, 184, 180)", "member": "rgb(88, 162, 253)", "milestone": "rgb(8, 179, 211)", "giftmember": "rgb(38, 192, 93)", "superchat": "rgb(26, 184, 180)", "supersticker": "rgb(210, 161, 15)", "other": "rgb(235, 81, 70)"},
    "cyberpunk": {"follow": "rgb(254, 110, 175)", "sub": "rgb(21, 199, 252)", "resub": "rgb(18, 211, 167)", "giftsub": "rgb(8, 229, 122)", "bits": "rgb(227, 190, 9)", "points": "rgb(246, 104, 221)", "raid": "rgb(255, 120, 122)", "streak": "rgb(254, 152, 60)", "hype": "rgb(219, 119, 252)", "modiversary": "rgb(151, 178, 254)", "donation": "rgb(12, 219, 219)", "member": "rgb(21, 199, 252)", "milestone": "rgb(18, 211, 167)", "giftmember": "rgb(8, 229, 122)", "superchat": "rgb(12, 219, 219)", "supersticker": "rgb(227, 190, 9)", "other": "rgb(254, 110, 175)"},
    "dark": {"follow": "rgb(115, 0, 255)", "sub": "rgb(59, 130, 246)", "resub": "rgb(59, 130, 246)", "giftsub": "rgb(34, 197, 94)", "bits": "rgb(234, 179, 8)", "points": "rgb(239, 68, 68)", "raid": "rgb(239, 68, 68)", "streak": "rgb(234, 179, 8)", "hype": "rgb(115, 0, 255)", "modiversary": "rgb(59, 130, 246)", "donation": "rgb(34, 197, 94)", "member": "rgb(59, 130, 246)", "milestone": "rgb(59, 130, 246)", "giftmember": "rgb(34, 197, 94)", "superchat": "rgb(34, 197, 94)", "supersticker": "rgb(234, 179, 8)", "other": "rgb(115, 0, 255)"},
    "emerald_dark": {"follow": "rgb(14, 188, 131)", "sub": "rgb(84, 149, 255)", "resub": "rgb(17, 168, 205)", "giftsub": "rgb(14, 194, 173)", "bits": "rgb(212, 162, 2)", "points": "rgb(241, 90, 154)", "raid": "rgb(250, 92, 87)", "streak": "rgb(240, 127, 9)", "hype": "rgb(218, 117, 198)", "modiversary": "rgb(173, 119, 244)", "donation": "rgb(23, 189, 204)", "member": "rgb(84, 149, 255)", "milestone": "rgb(17, 168, 205)", "giftmember": "rgb(14, 194, 173)", "superchat": "rgb(23, 189, 204)", "supersticker": "rgb(212, 162, 2)", "other": "rgb(14, 188, 131)"},
    "forest_glade": {"follow": "rgb(23, 136, 74)", "sub": "rgb(51, 108, 230)", "resub": "rgb(18, 130, 163)", "giftsub": "rgb(90, 144, 9)", "bits": "rgb(164, 119, 11)", "points": "rgb(196, 55, 116)", "raid": "rgb(203, 57, 53)", "streak": "rgb(186, 94, 5)", "hype": "rgb(161, 80, 145)", "modiversary": "rgb(145, 78, 208)", "donation": "rgb(14, 145, 141)", "member": "rgb(51, 108, 230)", "milestone": "rgb(18, 130, 163)", "giftmember": "rgb(90, 144, 9)", "superchat": "rgb(14, 145, 141)", "supersticker": "rgb(164, 119, 11)", "other": "rgb(23, 136, 74)"},
    "graphite": {"follow": "rgb(13, 171, 157)", "sub": "rgb(39, 186, 248)", "resub": "rgb(112, 175, 254)", "giftsub": "rgb(72, 207, 120)", "bits": "rgb(220, 179, 18)", "points": "rgb(243, 120, 174)", "raid": "rgb(253, 121, 120)", "streak": "rgb(251, 145, 51)", "hype": "rgb(191, 120, 176)", "modiversary": "rgb(148, 165, 253)", "donation": "rgb(17, 199, 225)", "member": "rgb(39, 186, 248)", "milestone": "rgb(112, 175, 254)", "giftmember": "rgb(72, 207, 120)", "superchat": "rgb(17, 199, 225)", "supersticker": "rgb(220, 179, 18)", "other": "rgb(13, 171, 157)"},
    "high_contrast": {"follow": "rgb(96, 152, 255)", "sub": "rgb(46, 192, 253)", "resub": "rgb(28, 198, 225)", "giftsub": "rgb(17, 216, 112)", "bits": "rgb(223, 187, 13)", "points": "rgb(254, 111, 172)", "raid": "rgb(255, 121, 116)", "streak": "rgb(255, 149, 42)", "hype": "rgb(232, 89, 208)", "modiversary": "rgb(180, 161, 254)", "donation": "rgb(22, 207, 205)", "member": "rgb(46, 192, 253)", "milestone": "rgb(28, 198, 225)", "giftmember": "rgb(17, 216, 112)", "superchat": "rgb(22, 207, 205)", "supersticker": "rgb(223, 187, 13)", "other": "rgb(96, 152, 255)"},
    "lavender_mist": {"follow": "rgb(137, 91, 207)", "sub": "rgb(53, 112, 239)", "resub": "rgb(1, 135, 171)", "giftsub": "rgb(2, 157, 68)", "bits": "rgb(181, 123, 9)", "points": "rgb(212, 53, 122)", "raid": "rgb(220, 56, 50)", "streak": "rgb(201, 98, 6)", "hype": "rgb(181, 72, 157)", "modiversary": "rgb(0, 128, 202)", "donation": "rgb(14, 150, 146)", "member": "rgb(53, 112, 239)", "milestone": "rgb(1, 135, 171)", "giftmember": "rgb(2, 157, 68)", "superchat": "rgb(14, 150, 146)", "supersticker": "rgb(181, 123, 9)", "other": "rgb(137, 91, 207)"},
    "light": {"follow": "rgb(117, 67, 243)", "sub": "rgb(48, 109, 241)", "resub": "rgb(0, 133, 168)", "giftsub": "rgb(14, 155, 69)", "bits": "rgb(178, 122, 7)", "points": "rgb(213, 45, 120)", "raid": "rgb(220, 48, 45)", "streak": "rgb(199, 96, 4)", "hype": "rgb(180, 22, 185)", "modiversary": "rgb(150, 76, 217)", "donation": "rgb(14, 148, 144)", "member": "rgb(48, 109, 241)", "milestone": "rgb(0, 133, 168)", "giftmember": "rgb(14, 155, 69)", "superchat": "rgb(14, 148, 144)", "supersticker": "rgb(178, 122, 7)", "other": "rgb(117, 67, 243)"},
    "midnight_blue": {"follow": "rgb(87, 142, 254)", "sub": "rgb(20, 177, 248)", "resub": "rgb(18, 190, 175)", "giftsub": "rgb(5, 202, 142)", "bits": "rgb(224, 169, 18)", "points": "rgb(242, 115, 171)", "raid": "rgb(252, 116, 116)", "streak": "rgb(250, 138, 51)", "hype": "rgb(216, 91, 194)", "modiversary": "rgb(175, 142, 249)", "donation": "rgb(26, 193, 209)", "member": "rgb(20, 177, 248)", "milestone": "rgb(18, 190, 175)", "giftmember": "rgb(5, 202, 142)", "superchat": "rgb(26, 193, 209)", "supersticker": "rgb(224, 169, 18)", "other": "rgb(87, 142, 254)"},
    "mint": {"follow": "rgb(14, 127, 119)", "sub": "rgb(43, 108, 203)", "resub": "rgb(12, 119, 172)", "giftsub": "rgb(23, 139, 60)", "bits": "rgb(160, 110, 14)", "points": "rgb(186, 57, 123)", "raid": "rgb(196, 56, 67)", "streak": "rgb(186, 83, 0)", "hype": "rgb(150, 84, 136)", "modiversary": "rgb(128, 83, 189)", "donation": "rgb(1, 131, 145)", "member": "rgb(43, 108, 203)", "milestone": "rgb(12, 119, 172)", "giftmember": "rgb(23, 139, 60)", "superchat": "rgb(1, 131, 145)", "supersticker": "rgb(160, 110, 14)", "other": "rgb(14, 127, 119)"},
    "mocha": {"follow": "rgb(224, 102, 63)", "sub": "rgb(104, 168, 226)", "resub": "rgb(58, 180, 198)", "giftsub": "rgb(123, 187, 93)", "bits": "rgb(215, 160, 44)", "points": "rgb(222, 113, 187)", "raid": "rgb(239, 109, 137)", "streak": "rgb(186, 165, 14)", "hype": "rgb(183, 109, 209)", "modiversary": "rgb(160, 151, 225)", "donation": "rgb(13, 193, 168)", "member": "rgb(104, 168, 226)", "milestone": "rgb(58, 180, 198)", "giftmember": "rgb(123, 187, 93)", "superchat": "rgb(13, 193, 168)", "supersticker": "rgb(215, 160, 44)", "other": "rgb(224, 102, 63)"},
    "monokai": {"follow": "rgb(249, 38, 114)", "sub": "rgb(102, 217, 239)", "resub": "rgb(120, 220, 232)", "giftsub": "rgb(166, 226, 46)", "bits": "rgb(230, 219, 116)", "points": "rgb(253, 151, 31)", "raid": "rgb(255, 97, 136)", "streak": "rgb(253, 151, 31)", "hype": "rgb(174, 129, 255)", "modiversary": "rgb(171, 157, 242)", "donation": "rgb(169, 220, 118)", "member": "rgb(102, 217, 239)", "milestone": "rgb(120, 220, 232)", "giftmember": "rgb(166, 226, 46)", "superchat": "rgb(169, 220, 118)", "supersticker": "rgb(230, 219, 116)", "other": "rgb(249, 38, 114)"},
    "nord_dark": {"follow": "rgb(180, 142, 173)", "sub": "rgb(129, 161, 193)", "resub": "rgb(136, 192, 208)", "giftsub": "rgb(163, 190, 140)", "bits": "rgb(235, 203, 139)", "points": "rgb(208, 135, 112)", "raid": "rgb(191, 97, 106)", "streak": "rgb(208, 135, 112)", "hype": "rgb(180, 142, 173)", "modiversary": "rgb(94, 129, 172)", "donation": "rgb(143, 188, 187)", "member": "rgb(129, 161, 193)", "milestone": "rgb(136, 192, 208)", "giftmember": "rgb(163, 190, 140)", "superchat": "rgb(143, 188, 187)", "supersticker": "rgb(235, 203, 139)", "other": "rgb(180, 142, 173)"},
    "ocean_breeze": {"follow": "rgb(13, 128, 197)", "sub": "rgb(54, 113, 238)", "resub": "rgb(15, 135, 170)", "giftsub": "rgb(13, 157, 70)", "bits": "rgb(181, 123, 3)", "points": "rgb(212, 55, 122)", "raid": "rgb(219, 58, 52)", "streak": "rgb(202, 97, 2)", "hype": "rgb(173, 84, 156)", "modiversary": "rgb(151, 81, 215)", "donation": "rgb(6, 150, 146)", "member": "rgb(54, 113, 238)", "milestone": "rgb(15, 135, 170)", "giftmember": "rgb(13, 157, 70)", "superchat": "rgb(6, 150, 146)", "supersticker": "rgb(181, 123, 3)", "other": "rgb(13, 128, 197)"},
    "ocean_depths": {"follow": "rgb(21, 187, 210)", "sub": "rgb(3, 184, 248)", "resub": "rgb(105, 172, 255)", "giftsub": "rgb(51, 196, 99)", "bits": "rgb(216, 165, 7)", "points": "rgb(245, 95, 158)", "raid": "rgb(254, 97, 92)", "streak": "rgb(245, 130, 14)", "hype": "rgb(221, 130, 202)", "modiversary": "rgb(144, 161, 254)", "donation": "rgb(17, 190, 185)", "member": "rgb(3, 184, 248)", "milestone": "rgb(105, 172, 255)", "giftmember": "rgb(51, 196, 99)", "superchat": "rgb(17, 190, 185)", "supersticker": "rgb(216, 165, 7)", "other": "rgb(21, 187, 210)"},
    "parchment": {"follow": "rgb(17, 112, 173)", "sub": "rgb(83, 96, 187)", "resub": "rgb(14, 118, 142)", "giftsub": "rgb(63, 128, 30)", "bits": "rgb(161, 99, 8)", "points": "rgb(178, 54, 102)", "raid": "rgb(184, 57, 46)", "streak": "rgb(176, 78, 3)", "hype": "rgb(149, 75, 135)", "modiversary": "rgb(118, 83, 176)", "donation": "rgb(3, 127, 111)", "member": "rgb(83, 96, 187)", "milestone": "rgb(14, 118, 142)", "giftmember": "rgb(63, 128, 30)", "superchat": "rgb(3, 127, 111)", "supersticker": "rgb(161, 99, 8)", "other": "rgb(17, 112, 173)"},
    "rose_garden": {"follow": "rgb(204, 62, 100)", "sub": "rgb(53, 112, 240)", "resub": "rgb(18, 135, 170)", "giftsub": "rgb(15, 157, 70)", "bits": "rgb(181, 124, 6)", "points": "rgb(200, 61, 160)", "raid": "rgb(220, 56, 50)", "streak": "rgb(202, 98, 4)", "hype": "rgb(163, 80, 191)", "modiversary": "rgb(120, 93, 234)", "donation": "rgb(11, 150, 146)", "member": "rgb(53, 112, 240)", "milestone": "rgb(18, 135, 170)", "giftmember": "rgb(15, 157, 70)", "superchat": "rgb(11, 150, 146)", "supersticker": "rgb(181, 124, 6)", "other": "rgb(204, 62, 100)"},
    "sunset_dark": {"follow": "rgb(231, 150, 18)", "sub": "rgb(91, 152, 253)", "resub": "rgb(3, 171, 210)", "giftsub": "rgb(40, 196, 95)", "bits": "rgb(197, 180, 20)", "points": "rgb(246, 91, 157)", "raid": "rgb(253, 95, 90)", "streak": "rgb(248, 130, 14)", "hype": "rgb(234, 124, 213)", "modiversary": "rgb(176, 121, 249)", "donation": "rgb(24, 189, 184)", "member": "rgb(91, 152, 253)", "milestone": "rgb(3, 171, 210)", "giftmember": "rgb(40, 196, 95)", "superchat": "rgb(24, 189, 184)", "supersticker": "rgb(197, 180, 20)", "other": "rgb(231, 150, 18)"},
    "sunset_glow": {"follow": "rgb(208, 91, 2)", "sub": "rgb(53, 111, 236)", "resub": "rgb(12, 134, 168)", "giftsub": "rgb(8, 156, 68)", "bits": "rgb(172, 116, 12)", "points": "rgb(201, 57, 119)", "raid": "rgb(208, 59, 54)", "streak": "rgb(146, 124, 6)", "hype": "rgb(191, 84, 171)", "modiversary": "rgb(149, 80, 213)", "donation": "rgb(3, 149, 145)", "member": "rgb(53, 111, 236)", "milestone": "rgb(12, 134, 168)", "giftmember": "rgb(8, 156, 68)", "superchat": "rgb(3, 149, 145)", "supersticker": "rgb(172, 116, 12)", "other": "rgb(208, 91, 2)"},
}


def event_groups() -> List[Tuple[str, List[Tuple[str, str, str]]]]:
    """[(group, [(field, label, icon)])] in display order."""
    groups: Dict[str, List[Tuple[str, str, str]]] = {}
    for key, label, icon, group, _status in EVENT_KINDS:
        groups.setdefault(group, []).append((f"event_{key}", label, icon))
    return list(groups.items())


def fill_builtin_event_colors(theme: Any) -> Any:
    """Give a built-in theme its shipped event colours where its file has none."""
    palette = BUILTIN_EVENT_COLORS.get(str(getattr(theme, "name", "") or ""))
    if not palette:
        return theme
    for key, value in palette.items():
        field = f"event_{key}"
        if not getattr(theme, field, ""):
            setattr(theme, field, value)
    return theme


def event_color(theme: Any, key: str) -> str:
    """The colour one event kind draws in: the theme's, else its status fallback."""
    value = getattr(theme, f"event_{key}", "") or ""
    if value:
        return value
    return getattr(theme, EVENT_FALLBACK.get(key, "primary"), "") or ""


def event_css_variables(theme: Any) -> str:
    """--color-event-* declarations for :root."""
    return "\n".join(f"    --color-event-{key}: {event_color(theme, key)};" for key, *_ in EVENT_KINDS)
