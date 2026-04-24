"""
FF7 PC (English Steam, ff7_en.exe) live memory hook: reads, optional writes, menu colors.

Window gradient RGB is read from the in-RAM savemap (Data Crystal offsets 0x48–0x53).
Live menu rendering uses a separate BGR block (see DevChatter InteractiveSeven ``Addresses.MenuColorAll``).
Character record gear/materia offsets match Data Crystal / ff7-flat-wiki savemap layouts for PC English.
"""

from __future__ import annotations

import ctypes
import json
import logging
import math
import os
import random
import re
import struct
import sys
import time
import unicodedata
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

CANON_IMAGE_BASE = 0x00400000

ADDR_CURRENT_MODULE = 0x00CBF9DC
ADDR_BATTLE_CHAR_BASE = 0x009AB0DC
ADDR_BATTLE_ATB_BASE = 0x009A8B12
ADDR_BATTLE_CHAR_ARRAY = 0x009A8DB8
ADDR_ENEMY_OBJ_BASE = 0x009A8794
ADDR_ENEMY_DATA_BASE = 0x009A8E9C
ADDR_PARTY_MEMBER_IDS = 0x00DC0230
ADDR_PARTY_MEMBER_NAMES = 0x00DBFD9C
ADDR_SAVEMAP_BASE = 0x00DBFD38
ADDR_MENU_VISIBILITY = 0x00DC08F8
ADDR_MENU_LOCKS = 0x00DC08FA
ADDR_FFNX_TRAMPOLINE_CHECK = 0x0041B965
# World map movement speed byte — ff7-lib
ADDR_WORLD_SPEED_MULTIPLIER = 0x00DFC480
ADDR_FIELD_MENU_ACCESS_ENABLED = 0x00CC0DBC
ADDR_PARTY_STAT_RECALC_FN = 0x0061F739
# Vanilla tick interval storage: ff7-lib `read_memory_float` reads **f64** at these RVAs (not f32).
ADDR_FIELD_FPS = 0x00CFF890
ADDR_BATTLE_FPS = 0x009AB090
ADDR_WORLD_FPS = 0x00DE6938
ADDR_FPS_NOP_INIT_1 = 0x0060E434
ADDR_FPS_NOP_INIT_2 = 0x0074BD02
ADDR_FPS_NOP_INIT_3 = 0x0041B6D8

# start_battle support (ff7-lib addresses.rs + ff7-ultima startBattle flow)
ADDR_FIELD_OBJ_PTR = 0x00CBF9D8
ADDR_BATTLE_MODULE_FIELD = 0x00CBF6B8
ADDR_BATTLE_ID_WORLD = 0x00E3A88C
ADDR_WORLD_BATTLE_FLAG1 = 0x00E2BBC8
ADDR_WORLD_BATTLE_FLAG2 = 0x00969950
ADDR_WORLD_BATTLE_FLAG3 = 0x00E3A884
ADDR_WORLD_MODE = 0x00E045E4
ADDR_WORLD_BATTLE_FLAG4 = 0x00E045E4  # ff7-lib addresses.rs world_battle_flag4
# ff7-lib addresses.rs — battle context byte (read-only for overlay; do not write piecemeal)
ADDR_BATTLE_MODE = 0x009AAD64
ADDR_BATTLE_PARTY_ITEMS = 0x009AC354
# ff7-lib — current battle action queue (8 bytes) and battle state object pointer
ADDR_BATTLE_QUEUE = 0x009A9884
ADDR_BATTLE_OBJ_PTR = 0x0099CE0C
# Kernel text tables (ff7-lib addresses.rs) for battle log command names
ADDR_KERNEL_READ_FN_CALL = 0x00419458
ADDR_KERNEL_SECTION_OFFSETS = 0x009A7FC8
ADDR_KERNEL_TEXTS_BASE = 0x009A13C8
ADDR_ENEMY_ATTACK_NAMES = 0x009A9484

# Battle inventory: 320 slots × 6 bytes (ff7-ultima useFF7 addItem; U16 id, u8 qty @ +2, …)
BATTLE_PARTY_ITEMS_SLOT_STRIDE = 6
BATTLE_PARTY_ITEMS_NUM_SLOTS = 0x140  # 320 decimal
BATTLE_PARTY_ITEMS_BLOB_SIZE = BATTLE_PARTY_ITEMS_NUM_SLOTS * BATTLE_PARTY_ITEMS_SLOT_STRIDE

# Savemap config — ff7-flat-wiki Savemap (PC English RAM save crystal)
SAVE_OFF_BATTLE_SPEED = 0x10D8
SAVE_OFF_BATTLE_MSG_SPEED = 0x10D9
SAVE_OFF_GENERAL_CONFIG = 0x10DA  # packs sound/controller/cursor + ATB high bits
SAVEMAP_ATB_MASK = 0xC0  # bits 6–7: Active 0x00, Recommended 0x40, Wait 0x80

# ``current_module == 2`` can glitch during combat; latch ``battle_ui`` for overlay autohide.
_BATTLE_UI_OFF_POLL_TICKS = 2  # game_hooks_service polls ~250ms → ~4s sustained non-battle

# Live window palette + RAM mirror (InteractiveSeven Core/FinalFantasy/Addresses.cs).
# Display: 16 bytes, four corners × (B, G, R, 0x80). Order: upper-left, lower-left, upper-right, lower-right.
ADDR_MENU_COLOR_DISPLAY_BASE = 0x0091EFC8
# 12 bytes RGB: TL, BL, TR, BR (same corner order as GetDisplayBytes / GetSaveBytes in MenuColors.cs).
ADDR_MENU_COLOR_SAVE_MIRROR_BASE = 0x0091EFD8

REC_OFF_CHAR_ID = 0x00
REC_OFF_LEVEL = 0x01
REC_OFF_STR = 0x02
REC_OFF_VIT = 0x03
REC_OFF_MAG = 0x04
REC_OFF_SPR = 0x05
REC_OFF_DEX = 0x06
REC_OFF_LUK = 0x07
REC_OFF_STR_BONUS = 0x08
REC_OFF_VIT_BONUS = 0x09
REC_OFF_MAG_BONUS = 0x0A
REC_OFF_SPR_BONUS = 0x0B
REC_OFF_DEX_BONUS = 0x0C
REC_OFF_LUK_BONUS = 0x0D
REC_OFF_FIELD_STATUS = 0x1F
REC_OFF_NAME = 0x10
NAME_BYTES = 12

# FF7 party_add_item_fn / party_add_materia_fn — call via CreateRemoteThread.
# Addresses from ff7-lib/src/ff7/addresses.rs (canonical RVAs).
ADDR_PARTY_ADD_ITEM_FN = 0x006CBFFA
ADDR_PARTY_ADD_MATERIA_FN = 0x006CC0EA

# Inventory layout (Data Crystal savemap).
SAVE_OFF_INV_ITEMS = 0x04FC
SAVE_OFF_INV_MATERIA = 0x077C
INV_ITEM_SLOTS = 0x140
INV_MATERIA_SLOTS = 0xC8
INV_ITEM_MAX_ID = 0x13F
MAX_ITEM_QUANTITY = 99

# Unified item-ID space used by party_add_item_fn (single flat table 0..0x13F).
ITEM_ID_WEAPON_BASE = 0x80
ITEM_ID_ARMOR_BASE = 0x100
ITEM_ID_ACCESSORY_BASE = 0x120

# Enemy data record (184 bytes @ enemy_data_base + scene_id*184)
ENEMY_OFF_LEVEL = 0x20
ENEMY_OFF_SPEED = 0x21
ENEMY_OFF_LUCK = 0x22
ENEMY_OFF_EVADE = 0x23
ENEMY_OFF_STR = 0x24
ENEMY_OFF_DEF = 0x25
ENEMY_OFF_MAG = 0x26
ENEMY_OFF_MDEF = 0x27

# Battle-actor ally-only fields
BATTLE_OFF_LEVEL = 0x24

FF7_MENU_NAMES: List[str] = [
    "Item",
    "Magic",
    "Materia",
    "Equip",
    "Status",
    "Order",
    "Limit",
    "Config",
    "PHS",
    "Save",
]

# Savemap window gradient (Data Crystal savemap)
SAVE_OFF_WIN_UL = 0x0048
SAVE_OFF_WIN_UR = 0x004B
SAVE_OFF_WIN_LL = 0x004E
SAVE_OFF_WIN_LR = 0x0051

BATTLE_ACTOR_STRIDE = 104
BATTLE_ATB_STRIDE = 68
BATTLE_CHAR_ARRAY_STRIDE = 0x34
ENEMY_OBJ_STRIDE = 16
ENEMY_DATA_STRIDE = 184
PARTY_RECORD_STRIDE = 0x84
SAVEMAP_SIZE = 0x10F4

_CHAR_BLOCK = [
    0x0054,
    0x00D8,
    0x015C,
    0x01E0,
    0x0264,
    0x02E8,
    0x036C,
    0x03F0,
    0x0474,
]

SAVE_OFF_PARTY_SLOTS = 0x04F8
SAVE_OFF_GIL = 0x0B7C
SAVE_OFF_PLAYTIME_SEC = 0x0B80
# Savemap "Name of location" (FF text), 24 bytes — ff7-flat-wiki Savemap 0x0F0C
SAVE_OFF_LOCATION_NAME = 0x0F0C
SAVE_OFF_LOCATION_NAME_LEN = 24

# Character record (field) — offsets within 132-byte block
REC_OFF_HP = 0x2C
REC_OFF_MP = 0x30
REC_OFF_MAX_HP = 0x38
REC_OFF_MAX_MP = 0x3A
REC_OFF_WEAPON = 0x1C
REC_OFF_ARMOR = 0x1D
REC_OFF_ACCESSORY = 0x1E
REC_OFF_WEAPON_MATERIA = 0x40
REC_OFF_ARMOR_MATERIA = 0x60
REC_OFF_MATERIA_WEAPON = 0x40
REC_OFF_MATERIA_ARMOR = 0x60

# Built-in gear slot layouts (KERNEL-style slot-type bytes; see ff7-flat-wiki Weapon_data / Armor_data).


def _ff7_gear_asset_dir() -> Path:
    """assets/ff7: repo root in dev; PyInstaller extract dir when frozen; else next to the exe."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / "assets" / "ff7"
            if bundled.is_dir():
                return bundled
        return Path(os.path.dirname(sys.executable)) / "assets" / "ff7"
    return Path(__file__).resolve().parents[2] / "assets" / "ff7"


_GEAR_ASSET_DIR = _ff7_gear_asset_dir()
_DEFAULT_MATERIA_SLOT_TYPES: List[int] = [0x05] * 8
_WEAPON_MATERIA_SLOT_TYPES: Dict[str, List[int]] = {}
_ARMOR_MATERIA_SLOT_TYPES: Dict[str, List[int]] = {}
_MATERIA_NAMES_EN: Dict[str, str] = {}
_MATERIA_ORB_BY_ID: Dict[str, str] = {}
_WEAPON_NAMES_EN: Dict[str, str] = {}
_ARMOR_NAMES_EN: Dict[str, str] = {}
_ACCESSORY_NAMES_EN: Dict[str, str] = {}
_ITEM_NAMES_EN: Dict[str, str] = {}
# Optional overrides for battle log: kernel section 8=commands(32), 9=attack names(128) — see ff7-lib.
_BATTLE_LOG_CMD_EN: Dict[str, str] = {}
_BATTLE_LOG_ATK128_EN: Dict[str, str] = {}
_BATTLE_LOG_NAME_ASSETS_LOADED = False
_GEAR_LAYOUT_ASSETS_LOADED = False

_EQUIP_ALLOW: Dict[str, Any] = {}
_EQUIP_ALLOW_LOADED = False


def _load_equip_allowlists() -> None:
    """Optional per-character weapon/armor/accessory id allowlists (null = any)."""
    global _EQUIP_ALLOW_LOADED, _EQUIP_ALLOW
    if _EQUIP_ALLOW_LOADED:
        return
    _EQUIP_ALLOW_LOADED = True
    path = _GEAR_ASSET_DIR / "equip_allowlists.json"
    if not path.is_file():
        _EQUIP_ALLOW = {}
        return
    try:
        _EQUIP_ALLOW = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("equip_allowlists.json unreadable: %s", e)
        _EQUIP_ALLOW = {}


def _allowed_ids_for_char_gear(char_id: int, kind: str) -> Optional[Set[int]]:
    """None = no restriction (any id in valid range)."""
    _load_equip_allowlists()
    entry = _EQUIP_ALLOW.get(str(char_id))
    if not isinstance(entry, dict):
        return None
    raw = entry.get(kind)
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    if len(raw) == 0:
        return set()
    out: Set[int] = set()
    for x in raw:
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            continue
    return out if out else set()


def _load_battle_log_name_assets() -> None:
    """Load optional battle_log_names_en.json: {"commands": {"0": "…"}, "attacks_128": {"0": "…"}."""
    global _BATTLE_LOG_NAME_ASSETS_LOADED, _BATTLE_LOG_CMD_EN, _BATTLE_LOG_ATK128_EN
    if _BATTLE_LOG_NAME_ASSETS_LOADED:
        return
    _BATTLE_LOG_NAME_ASSETS_LOADED = True
    path = _GEAR_ASSET_DIR / "battle_log_names_en.json"
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("battle_log_names_en.json unreadable: %s", e)
        return
    if not isinstance(raw, dict):
        return
    c = raw.get("commands")
    a = raw.get("attacks_128")
    if isinstance(c, dict):
        _BATTLE_LOG_CMD_EN.clear()
        for k, v in c.items():
            if v is not None and str(v).strip():
                _BATTLE_LOG_CMD_EN[str(k)] = str(v).strip()
    if isinstance(a, dict):
        _BATTLE_LOG_ATK128_EN.clear()
        for k, v in a.items():
            if v is not None and str(v).strip():
                _BATTLE_LOG_ATK128_EN[str(k)] = str(v).strip()


def _load_ff7_gear_layout_assets() -> None:
    """Load weapon/armor slot-type maps, materia names, orb colors, and English gear names from JSON."""
    global _GEAR_LAYOUT_ASSETS_LOADED
    if _GEAR_LAYOUT_ASSETS_LOADED:
        return
    _GEAR_LAYOUT_ASSETS_LOADED = True
    try:
        wpath = _GEAR_ASSET_DIR / "weapon_materia_slot_types.json"
        apath = _GEAR_ASSET_DIR / "armor_materia_slot_types.json"
        mpath = _GEAR_ASSET_DIR / "materia_names_en.json"
        opath = _GEAR_ASSET_DIR / "materia_orb_by_id.json"
        wnpath = _GEAR_ASSET_DIR / "weapon_names_en.json"
        anpath = _GEAR_ASSET_DIR / "armor_names_en.json"
        acpath = _GEAR_ASSET_DIR / "accessory_names_en.json"
        ipath = _GEAR_ASSET_DIR / "item_names_en.json"
        if wpath.is_file():
            _WEAPON_MATERIA_SLOT_TYPES.clear()
            _WEAPON_MATERIA_SLOT_TYPES.update(json.loads(wpath.read_text(encoding="utf-8")))
        if apath.is_file():
            _ARMOR_MATERIA_SLOT_TYPES.clear()
            _ARMOR_MATERIA_SLOT_TYPES.update(json.loads(apath.read_text(encoding="utf-8")))
        if mpath.is_file():
            _MATERIA_NAMES_EN.clear()
            _MATERIA_NAMES_EN.update(json.loads(mpath.read_text(encoding="utf-8")))
        if opath.is_file():
            _MATERIA_ORB_BY_ID.clear()
            _MATERIA_ORB_BY_ID.update(json.loads(opath.read_text(encoding="utf-8")))
        if wnpath.is_file():
            _WEAPON_NAMES_EN.clear()
            _WEAPON_NAMES_EN.update(json.loads(wnpath.read_text(encoding="utf-8")))
        if anpath.is_file():
            _ARMOR_NAMES_EN.clear()
            _ARMOR_NAMES_EN.update(json.loads(anpath.read_text(encoding="utf-8")))
        if acpath.is_file():
            _ACCESSORY_NAMES_EN.clear()
            _ACCESSORY_NAMES_EN.update(json.loads(acpath.read_text(encoding="utf-8")))
        if ipath.is_file():
            _ITEM_NAMES_EN.clear()
            _ITEM_NAMES_EN.update(json.loads(ipath.read_text(encoding="utf-8")))
        _load_battle_log_name_assets()
    except OSError as e:
        logger.warning("FF7 layout/materia name assets unreadable: %s", e)


def _weapon_materia_slot_types(weapon_id: int) -> List[int]:
    _load_ff7_gear_layout_assets()
    if weapon_id < 0 or weapon_id > 127:
        return list(_DEFAULT_MATERIA_SLOT_TYPES)
    return list(
        _WEAPON_MATERIA_SLOT_TYPES.get(str(weapon_id), _DEFAULT_MATERIA_SLOT_TYPES)
    )


def _armor_materia_slot_types(armor_id: int) -> List[int]:
    _load_ff7_gear_layout_assets()
    if armor_id < 0 or armor_id > 31:
        return list(_DEFAULT_MATERIA_SLOT_TYPES)
    return list(
        _ARMOR_MATERIA_SLOT_TYPES.get(str(armor_id), _DEFAULT_MATERIA_SLOT_TYPES)
    )


BATTLE_OFF_STATUS = 0x00
STATUS_DEAD = 1

BATTLE_OFF_HP = 0x2C
BATTLE_OFF_MAX_HP = 0x30
BATTLE_OFF_MP = 0x28
BATTLE_OFF_MAX_MP = 0x2A

_MAX_GIL = 999_999_999

# Battle status bits (ff7-flat-wiki / Battle Mechanics FAQ); dword at actor+0x00
_STATUS_AILMENT_BITS: List[Tuple[int, str]] = [
    (0x00000001, "Death"),
    (0x00000002, "NearDeath"),
    (0x00000004, "Sleep"),
    (0x00000008, "Poison"),
    (0x00000010, "Sadness"),
    (0x00000020, "Fury"),
    (0x00000040, "Confusion"),
    (0x00000080, "Silence"),
    (0x00000100, "Haste"),
    (0x00000200, "Slow"),
    (0x00000400, "Stop"),
    (0x00000800, "Frog"),
    (0x00001000, "Small"),
    (0x00002000, "SlowNumb"),
    (0x00004000, "Petrify"),
    (0x00008000, "Regen"),
    (0x00010000, "Barrier"),
    (0x00020000, "MBarrier"),
    (0x00040000, "Reflect"),
    (0x00080000, "Dual"),
    (0x00100000, "Shield"),
    (0x00200000, "DeathSentence"),
    (0x00400000, "Manipulate"),
    (0x00800000, "Berserk"),
    (0x01000000, "Peerless"),
    (0x02000000, "Paralysis"),
    (0x04000000, "Darkness"),
    (0x08000000, "DualDrain"),
    (0x10000000, "DeathForce"),
    (0x20000000, "Resist"),
    (0x40000000, "LuckyGirl"),
    (0x80000000, "Imprisoned"),
]

_STATUS_NAME_TO_MASK: Dict[str, int] = {}
for mask, name in _STATUS_AILMENT_BITS:
    _STATUS_NAME_TO_MASK[name.lower()] = mask
    _STATUS_NAME_TO_MASK[name.lower().replace(" ", "")] = mask

# Valid orb image stems (assets/ff7/*_materia.png); materia_orb_by_id.json uses these strings.
_VALID_MATERIA_ORB_STEMS = frozenset(
    {
        "materia_green",
        "materia_yellow",
        "materia_red",
        "materia_blue",
        "materia_purple",
    }
)

_CURRENT_MODULE_NAMES: Dict[int, str] = {
    0: "None",
    1: "Field",
    2: "Battle",
    3: "World Map",
    5: "Menu",
    6: "Highway",
    7: "Chocobo race",
    8: "Snowboard",
    9: "Fort Condor",
    10: "Submarine",
    11: "Jet",
    12: "Change disc",
    14: "Snowboard 2",
    17: "Victory", 
    19: "Quit",
    20: "Start",
    23: "Battle swirl",
    25: "Ending",
    26: "Game Over",
    27: "Intro",
    28: "Credits",
}


def _ailments_from_status(status: int) -> List[str]:
    st = status & 0xFFFFFFFF
    out: List[str] = []
    for mask, label in _STATUS_AILMENT_BITS:
        if st & mask:
            out.append(label)
    return out


def _materia_orb_stem(materia_id: int) -> str:
    if materia_id < 0 or materia_id == 0xFF:
        return ""
    _load_ff7_gear_layout_assets()
    stem = _MATERIA_ORB_BY_ID.get(str(materia_id), "materia_green")
    if stem not in _VALID_MATERIA_ORB_STEMS:
        return "materia_green"
    return stem


def _gear_display_name(table: Dict[str, str], idx: int, hi: int) -> str:
    if idx < 0 or idx > hi or idx == 0xFF:
        return ""
    return (table.get(str(idx), "") or "").strip()


def _battle_log_cmd_display(cmd_id: int, cmds_ram: List[str]) -> str:
    _load_battle_log_name_assets()
    i = int(cmd_id) & 0xFF
    s = _BATTLE_LOG_CMD_EN.get(str(i), "").strip()
    if s:
        return s
    if 0 <= i < len(cmds_ram) and (cmds_ram[i] or "").strip():
        return (cmds_ram[i] or "").strip()
    return ""


def _battle_log_atk_display(idx: int, atks_ram: List[str]) -> str:
    _load_battle_log_name_assets()
    j = int(idx)
    s = _BATTLE_LOG_ATK128_EN.get(str(j), "").strip()
    if s:
        return s
    if 0 <= j < len(atks_ram) and (atks_ram[j] or "").strip():
        return (atks_ram[j] or "").strip()
    return ""


def _ff7_unified_item_name(item_id: int) -> str:
    """
    English name for the unified 0x000..0x13F item id space: consumables, then
    weapons, armor, accessories (same as ``party_add_item_fn`` / kernel item table).
    """
    _load_ff7_gear_layout_assets()
    i = int(item_id) & 0xFFFF
    if i < 0 or i > INV_ITEM_MAX_ID:
        return f"Item {i}"
    if i < ITEM_ID_WEAPON_BASE:
        s = (_ITEM_NAMES_EN.get(str(i), "") or "").strip()
        return s or f"Item {i}"
    if i < ITEM_ID_ARMOR_BASE:
        s = (
            _WEAPON_NAMES_EN.get(str(i - ITEM_ID_WEAPON_BASE), "") or ""
        ).strip()
        return s or f"Item {i}"
    if i < ITEM_ID_ACCESSORY_BASE:
        s = (
            _ARMOR_NAMES_EN.get(str(i - ITEM_ID_ARMOR_BASE), "") or ""
        ).strip()
        return s or f"Item {i}"
    s = (
        _ACCESSORY_NAMES_EN.get(str(i - ITEM_ID_ACCESSORY_BASE), "") or ""
    ).strip()
    return s or f"Item {i}"


def _parse_materia_block(rec: bytes, base: int, n_slots: int = 8) -> List[Dict[str, Any]]:
    _load_ff7_gear_layout_assets()
    slots: List[Dict[str, Any]] = []
    for i in range(n_slots):
        o = base + i * 4
        if o + 4 > len(rec):
            break
        mid = int(rec[o])
        ap = int(rec[o + 1] | (rec[o + 2] << 8) | (rec[o + 3] << 16))
        slots.append(
            {
                "id": mid,
                "ap": ap,
                "empty": mid == 0xFF,
                "orb": _materia_orb_stem(mid) if mid != 0xFF else "",
                "name": (
                    ""
                    if mid == 0xFF
                    else _MATERIA_NAMES_EN.get(str(mid), "")
                ),
            }
        )
    return slots


def _char_gear_materia(rec: bytes) -> Dict[str, Any]:
    """Equipment + materia from one 132-byte character record (Data Crystal layout)."""
    if len(rec) < 0x84:
        return {
            "weapon_id": -1,
            "armor_id": -1,
            "accessory_id": -1,
            "weapon_name": "",
            "armor_name": "",
            "accessory_name": "",
            "materia_weapon": [],
            "materia_armor": [],
            "materia_weapon_slot_types": list(_DEFAULT_MATERIA_SLOT_TYPES),
            "materia_armor_slot_types": list(_DEFAULT_MATERIA_SLOT_TYPES),
        }
    w, a, acc_raw = int(rec[REC_OFF_WEAPON]), int(rec[REC_OFF_ARMOR]), int(rec[REC_OFF_ACCESSORY])
    acc = -1 if acc_raw == 0xFF else acc_raw
    mw = _parse_materia_block(rec, REC_OFF_MATERIA_WEAPON, 8)
    ma = _parse_materia_block(rec, REC_OFF_MATERIA_ARMOR, 8)
    _load_ff7_gear_layout_assets()
    return {
        "weapon_id": w,
        "armor_id": a,
        "accessory_id": acc,
        "weapon_name": _gear_display_name(_WEAPON_NAMES_EN, w, 127),
        "armor_name": _gear_display_name(_ARMOR_NAMES_EN, a, 31),
        "accessory_name": _gear_display_name(_ACCESSORY_NAMES_EN, acc, 31),
        "materia_weapon": mw,
        "materia_armor": ma,
        "materia_weapon_slot_types": _weapon_materia_slot_types(w),
        "materia_armor_slot_types": _armor_materia_slot_types(a),
    }


def _field_name_from_savemap(savemap: bytes) -> str:
    if len(savemap) < SAVE_OFF_LOCATION_NAME + SAVE_OFF_LOCATION_NAME_LEN:
        return ""
    raw = savemap[SAVE_OFF_LOCATION_NAME : SAVE_OFF_LOCATION_NAME + SAVE_OFF_LOCATION_NAME_LEN]
    return _decode_ff7_name(raw)


def _current_module_label(module_byte: int) -> str:
    return _CURRENT_MODULE_NAMES.get(int(module_byte), f"Module {int(module_byte)}")


def _party_gear_from_savemap(savemap: bytes, party_slot: int) -> Dict[str, Any]:
    """Party slot 0–2 → character record gear/materia, or empty dict if slot empty."""
    if party_slot < 0 or party_slot > 2:
        return {}
    if len(savemap) <= SAVE_OFF_PARTY_SLOTS + party_slot:
        return {}
    cid = savemap[SAVE_OFF_PARTY_SLOTS + party_slot]
    if cid >= len(_CHAR_BLOCK) or cid == 0xFF:
        return {}
    off = _CHAR_BLOCK[cid]
    if off + 0x84 > len(savemap):
        return {}
    rec = savemap[off : off + 0x84]
    return _char_gear_materia(rec)


def _empty_gear_materia() -> Dict[str, Any]:
    return {
        "weapon_id": -1,
        "armor_id": -1,
        "accessory_id": -1,
        "weapon_name": "",
        "armor_name": "",
        "accessory_name": "",
        "materia_weapon": [],
        "materia_armor": [],
        "materia_weapon_slot_types": list(_DEFAULT_MATERIA_SLOT_TYPES),
        "materia_armor_slot_types": list(_DEFAULT_MATERIA_SLOT_TYPES),
    }


def _party_row_party_slot(row: Dict[str, Any]) -> int:
    ps = row.get("party_slot", row.get("slot", -1))
    try:
        i = int(ps)
    except (TypeError, ValueError):
        return -1
    return i if i in (0, 1, 2) else -1


def _count_equipped_materia_in_row(row: Dict[str, Any]) -> int:
    n = 0
    for key in ("materia_weapon", "materia_armor"):
        for s in row.get(key) or []:
            if not isinstance(s, dict) or s.get("empty"):
                continue
            try:
                mid_i = int(s.get("id", 0xFF))
            except (TypeError, ValueError):
                continue
            if mid_i != 0xFF and mid_i >= 0:
                n += 1
    return n


def _savemap_party_slot_level(savemap: bytes, party_slot: int) -> int:
    if party_slot < 0 or party_slot > 2 or len(savemap) <= SAVE_OFF_PARTY_SLOTS + party_slot:
        return 0
    cid = savemap[SAVE_OFF_PARTY_SLOTS + party_slot]
    if cid >= len(_CHAR_BLOCK) or cid == 0xFF:
        return 0
    off = _CHAR_BLOCK[cid]
    if off + 0x02 > len(savemap):
        return 0
    return int(savemap[off + 0x01])


def _savemap_party_slot_empty(savemap: bytes, party_slot: int) -> bool:
    if party_slot < 0 or party_slot > 2 or len(savemap) <= SAVE_OFF_PARTY_SLOTS + party_slot:
        return True
    cid = savemap[SAVE_OFF_PARTY_SLOTS + party_slot]
    return cid >= len(_CHAR_BLOCK) or cid == 0xFF


def _decode_savemap_atb_mode(byte_val: int) -> str:
    """ATB setting from savemap 0x10DA (bits 6–7 per ff7-flat-wiki)."""
    b = int(byte_val) & SAVEMAP_ATB_MASK
    if b == 0x80:
        return "wait"
    if b == 0x40:
        return "recommended"
    return "active"


def _encode_savemap_atb_mode(mode: str) -> int:
    m = (mode or "").strip().lower()
    if m in ("wait", "w"):
        return 0x80
    if m in ("recommended", "recommend", "mid", "m"):
        return 0x40
    return 0x00  # active


def _records_party_aggregates(
    party: List[Dict[str, Any]], savemap: Optional[bytes]
) -> Tuple[int, int]:
    """Average level over occupied party slots only; materia on active trio savemap slots only."""
    by_slot: Dict[int, Dict[str, Any]] = {}
    for row in party:
        ps = _party_row_party_slot(row)
        if ps >= 0:
            by_slot[ps] = row
    levels: List[int] = []
    materia_total = 0
    for ps in range(3):
        if savemap and _savemap_party_slot_empty(savemap, ps):
            continue
        if ps in by_slot:
            row = by_slot[ps]
            lv = int(row.get("level", 0) or 0)
            if lv > 0:
                levels.append(lv)
            elif savemap:
                slv = _savemap_party_slot_level(savemap, ps)
                if slv > 0:
                    levels.append(slv)
            materia_total += _count_equipped_materia_in_row(row)
        elif savemap:
            slv = _savemap_party_slot_level(savemap, ps)
            if slv > 0:
                levels.append(slv)
            gear = _party_gear_from_savemap(savemap, ps)
            if gear:
                materia_total += _count_equipped_materia_in_row(gear)
    avg = round(sum(levels) / len(levels)) if levels else 0
    return avg, materia_total


if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _OpenProcess = _kernel32.OpenProcess
    _ReadProcessMemory = _kernel32.ReadProcessMemory
    _WriteProcessMemory = _kernel32.WriteProcessMemory
    _CloseHandle = _kernel32.CloseHandle
    PROCESS_VM_READ = 0x0010
    PROCESS_VM_WRITE = 0x0020
    PROCESS_VM_OPERATION = 0x0008
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_CREATE_THREAD = 0x0002
    _PROCESS_ACCESS = (
        PROCESS_VM_READ
        | PROCESS_VM_WRITE
        | PROCESS_VM_OPERATION
        | PROCESS_QUERY_INFORMATION
        | PROCESS_CREATE_THREAD
    )
else:
    _kernel32 = None

_PAGE_READWRITE = 0x04
_PAGE_EXECUTE_READWRITE = 0x40
_KERNEL32_EXTRA = None
_VirtualProtect = None
_VirtualProtectEx = None
_FlushInstructionCache = None
_GetCurrentProcess = None
_VirtualAllocEx = None
_VirtualFreeEx = None
_CreateRemoteThread = None
_WaitForSingleObject = None
if sys.platform == "win32" and _kernel32 is not None:
    _KERNEL32_EXTRA = _kernel32
    _VirtualProtect = _kernel32.VirtualProtect
    _VirtualProtect.argtypes = [
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _VirtualProtect.restype = wintypes.BOOL
    _VirtualProtectEx = _kernel32.VirtualProtectEx
    _VirtualProtectEx.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _VirtualProtectEx.restype = wintypes.BOOL
    _FlushInstructionCache = _kernel32.FlushInstructionCache
    _FlushInstructionCache.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        ctypes.c_size_t,
    ]
    _FlushInstructionCache.restype = wintypes.BOOL
    _GetCurrentProcess = _kernel32.GetCurrentProcess
    _GetCurrentProcess.argtypes = []
    _GetCurrentProcess.restype = wintypes.HANDLE

    _VirtualAllocEx = _kernel32.VirtualAllocEx
    _VirtualAllocEx.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _VirtualAllocEx.restype = wintypes.LPVOID

    _VirtualFreeEx = _kernel32.VirtualFreeEx
    _VirtualFreeEx.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.DWORD,
    ]
    _VirtualFreeEx.restype = wintypes.BOOL

    _CreateRemoteThread = _kernel32.CreateRemoteThread
    _CreateRemoteThread.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _CreateRemoteThread.restype = wintypes.HANDLE

    _WaitForSingleObject = _kernel32.WaitForSingleObject
    _WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _WaitForSingleObject.restype = wintypes.DWORD


def _rebase(module_base: int, canon_addr: int) -> int:
    return module_base + (canon_addr - CANON_IMAGE_BASE)


def _decode_ff7_name(raw: bytes) -> str:
    out: List[str] = []
    for b in raw:
        if b == 0xFF:
            break
        if b == 0x00:
            out.append(" ")
            continue
        ch = b + 0x20
        if 0x20 <= ch < 0x7F:
            out.append(chr(ch))
    return "".join(out).strip() or "?"


def _encode_ff7_name(text: str, out_len: int = NAME_BYTES) -> bytes:
    """FF7 menu font bytes (inverse of _decode_ff7_name), padded with 0xFF."""
    raw = bytearray([0xFF] * out_len)
    i = 0
    for ch in (text or "").strip():
        if i >= out_len:
            break
        if ch == " ":
            raw[i] = 0x00
        else:
            o = ord(ch)
            if 0x20 <= o < 0x7F:
                raw[i] = o - 0x20
            else:
                raw[i] = min(0xFE, max(0, o - 0x20))
        i += 1
    return bytes(raw)


def _norm_party_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _sanitize_enemy_token(s: str) -> str:
    """Strip chat/invisible junk (e.g. U+034F) and normalize spaces for enemy lookup."""
    if not s:
        return ""
    out: List[str] = []
    for ch in s.strip():
        if unicodedata.category(ch) in ("Cf", "Mn", "Me"):
            continue
        out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


# Party stat name -> (savemap record offset, byte width, is_signed).
# HP/MP/maxHP/maxMP are u16, stats are u8. Bonuses are aliased with "_bonus" suffix.
_PARTY_STAT_OFFSETS: Dict[str, Tuple[int, int]] = {
    "str": (REC_OFF_STR, 1),
    "strength": (REC_OFF_STR, 1),
    "vit": (REC_OFF_VIT, 1),
    "vitality": (REC_OFF_VIT, 1),
    "mag": (REC_OFF_MAG, 1),
    "magic": (REC_OFF_MAG, 1),
    "spr": (REC_OFF_SPR, 1),
    "spirit": (REC_OFF_SPR, 1),
    "dex": (REC_OFF_DEX, 1),
    "dexterity": (REC_OFF_DEX, 1),
    "luk": (REC_OFF_LUK, 1),
    "luck": (REC_OFF_LUK, 1),
    "str_bonus": (REC_OFF_STR_BONUS, 1),
    "vit_bonus": (REC_OFF_VIT_BONUS, 1),
    "mag_bonus": (REC_OFF_MAG_BONUS, 1),
    "spr_bonus": (REC_OFF_SPR_BONUS, 1),
    "dex_bonus": (REC_OFF_DEX_BONUS, 1),
    "luk_bonus": (REC_OFF_LUK_BONUS, 1),
    "hp": (0x2C, 2),
    "mp": (0x30, 2),
    "max_hp": (0x38, 2),
    "max_mp": (0x3A, 2),
    "maxhp": (0x38, 2),
    "maxmp": (0x3A, 2),
}

# Enemy stat name -> (offset in enemy_data_base record, width). Defense/MDef
# are stored as raw byte but the game reads value*2, so the caller halves the
# requested amount before writing (see _op_set_enemy_stat).
_ENEMY_STAT_OFFSETS: Dict[str, Tuple[int, int]] = {
    "level": (ENEMY_OFF_LEVEL, 1),
    "speed": (ENEMY_OFF_SPEED, 1),
    "luck": (ENEMY_OFF_LUCK, 1),
    "evade": (ENEMY_OFF_EVADE, 1),
    "str": (ENEMY_OFF_STR, 1),
    "strength": (ENEMY_OFF_STR, 1),
    "def": (ENEMY_OFF_DEF, 1),
    "defense": (ENEMY_OFF_DEF, 1),
    "mag": (ENEMY_OFF_MAG, 1),
    "magic": (ENEMY_OFF_MAG, 1),
    "mdef": (ENEMY_OFF_MDEF, 1),
    "magic_defense": (ENEMY_OFF_MDEF, 1),
    "magicdefense": (ENEMY_OFF_MDEF, 1),
}


# Materia AP thresholds for levels 1-5 (and Master) from FF7 kernel data.
_MATERIA_LEVEL_AP: Dict[int, int] = {
    1: 0,
    2: 2000,
    3: 18000,
    4: 35000,
    5: 81000,
    6: 0xFFFFFF,
}


# Named colors → RGB, accepted by _op_set_menu_colors.
_NAMED_COLORS: Dict[str, Tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "orange": (255, 128, 0),
    "purple": (128, 0, 255),
    "pink": (255, 128, 192),
    "brown": (128, 64, 0),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "navy": (0, 0, 128),
    "teal": (0, 128, 128),
    "lime": (128, 255, 0),
    "crimson": (192, 0, 64),
    "gold": (255, 192, 0),
    "silver": (192, 192, 192),
    # FF7 menu defaults (roughly the blue gradient)
    "ff7blue": (16, 48, 160),
    "default": (16, 48, 160),
}


# Menu corner token → list of (savemap_offset, corner_label) affected.
_MENU_CORNERS: Dict[str, List[str]] = {
    "upper_left": ["ul"],
    "upper right": ["ur"],
    "upper_right": ["ur"],
    "ul": ["ul"],
    "ur": ["ur"],
    "ll": ["ll"],
    "lr": ["lr"],
    "top_left": ["ul"],
    "top_right": ["ur"],
    "top-left": ["ul"],
    "top-right": ["ur"],
    "bottom_left": ["ll"],
    "bottom_right": ["lr"],
    "lower_left": ["ll"],
    "lower_right": ["lr"],
    "top": ["ul", "ur"],
    "bottom": ["ll", "lr"],
    "left": ["ul", "ll"],
    "right": ["ur", "lr"],
    "all": ["ul", "ur", "ll", "lr"],
    "corners": ["ul", "ur", "ll", "lr"],
}

_MENU_CORNER_OFFSETS: Dict[str, int] = {
    "ul": SAVE_OFF_WIN_UL,
    "ur": SAVE_OFF_WIN_UR,
    "ll": SAVE_OFF_WIN_LL,
    "lr": SAVE_OFF_WIN_LR,
}

# Byte offset within ADDR_MENU_COLOR_DISPLAY_BASE (16-byte BGR+0x80 layout).
_MENU_CORNER_DISPLAY_OFF: Dict[str, int] = {
    "ul": 0,
    "ll": 4,
    "ur": 8,
    "lr": 12,
}

# Byte offset within ADDR_MENU_COLOR_SAVE_MIRROR_BASE (12-byte RGB sequential layout).
_MENU_CORNER_I7_MIRROR_OFF: Dict[str, int] = {
    "ul": 0,
    "ll": 3,
    "ur": 6,
    "lr": 9,
}


def _menu_display_quad(rgb: Tuple[int, int, int]) -> bytes:
    """BGR + 0x80 padding per InteractiveSeven ``MenuColors.GetDisplayBytes``."""
    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
    return bytes([b & 0xFF, g & 0xFF, r & 0xFF, 0x80])


def _parse_menu_color(token: str) -> Optional[Tuple[int, int, int]]:
    """Parse a color string: named color, ``#RRGGBB``, ``rgb(r,g,b)`` or ``r,g,b``."""
    if token is None:
        return None
    s = str(token).strip().lower()
    if not s:
        return None
    if s in _NAMED_COLORS:
        return _NAMED_COLORS[s]
    m = re.match(r"^#?([0-9a-f]{6})$", s)
    if m:
        h = m.group(1)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    m = re.match(r"^#?([0-9a-f]{3})$", s)
    if m:
        h = m.group(1)
        return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16))
    m = re.match(
        r"^rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$", s
    )
    if m:
        return tuple(max(0, min(255, int(v))) for v in m.groups())  # type: ignore[return-value]
    m = re.match(r"^(\d+)\s*,\s*(\d+)\s*,\s*(\d+)$", s)
    if m:
        return tuple(max(0, min(255, int(v))) for v in m.groups())  # type: ignore[return-value]
    return None


def _parse_menu_corners(token: str) -> Optional[List[str]]:
    if token is None:
        return None
    s = re.sub(r"\s+", "_", str(token).strip().lower())
    return list(_MENU_CORNERS.get(s, []))


def _rgb_tuple(savemap: bytes, off: int) -> Optional[Tuple[int, int, int]]:
    if len(savemap) < off + 3:
        return None
    return (int(savemap[off]), int(savemap[off + 1]), int(savemap[off + 2]))


def _blend_rgb(
    a: Tuple[int, int, int], b: Tuple[int, int, int]
) -> Tuple[int, int, int]:
    return (
        min(255, (int(a[0]) + int(b[0])) // 2),
        min(255, (int(a[1]) + int(b[1])) // 2),
        min(255, (int(a[2]) + int(b[2])) // 2),
    )


def _hex_rgb(t: Tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % t


def _darken(t: Tuple[int, int, int], factor: float) -> Tuple[int, int, int]:
    return (
        max(0, min(255, int(t[0] * factor))),
        max(0, min(255, int(t[1] * factor))),
        max(0, min(255, int(t[2] * factor))),
    )


def _menu_theme_dict_from_rgbs(
    ul: Tuple[int, int, int],
    ur: Tuple[int, int, int],
    ll: Tuple[int, int, int],
    lr: Tuple[int, int, int],
) -> Dict[str, str]:
    """Build the ``menu_theme`` JSON/CSS dict from four corner RGB tuples."""
    top = _blend_rgb(ul, ur)
    bot = _blend_rgb(ll, lr)
    avg = (
        (ul[0] + ur[0] + ll[0] + lr[0]) // 4,
        (ul[1] + ur[1] + ll[1] + lr[1]) // 4,
        (ul[2] + ur[2] + ll[2] + lr[2]) // 4,
    )
    border_outer = tuple(min(255, c + 50) for c in avg)
    border_mid = tuple(min(255, c + 25) for c in avg)
    border_inner = _darken(bot, 0.45)
    return {
        "bg_ul": _hex_rgb(ul),
        "bg_ur": _hex_rgb(ur),
        "bg_ll": _hex_rgb(ll),
        "bg_lr": _hex_rgb(lr),
        "bg_center": _hex_rgb(avg),
        "bg_top": _hex_rgb(top),
        "bg_bot": _hex_rgb(bot),
        "border_outer": _hex_rgb(border_outer),
        "border_mid": _hex_rgb(border_mid),
        "border_inner": _hex_rgb(border_inner),
    }


def menu_theme_from_savemap(savemap: bytes) -> Optional[Dict[str, str]]:
    """Build CSS-friendly colors from savemap window RGB corners."""
    ul = _rgb_tuple(savemap, SAVE_OFF_WIN_UL)
    ur = _rgb_tuple(savemap, SAVE_OFF_WIN_UR)
    ll = _rgb_tuple(savemap, SAVE_OFF_WIN_LL)
    lr = _rgb_tuple(savemap, SAVE_OFF_WIN_LR)
    if not all((ul, ur, ll, lr)):
        return None
    return _menu_theme_dict_from_rgbs(ul, ur, ll, lr)


def menu_theme_from_live_display(raw: bytes) -> Optional[Dict[str, str]]:
    """Build ``menu_theme`` from the 16-byte live palette (``ADDR_MENU_COLOR_DISPLAY_BASE``).

    Layout matches InteractiveSeven ``MenuColors.GetDisplayBytes``: four corners ×
    (B, G, R, 0x80); corner order upper-left, lower-left, upper-right, lower-right.
    """
    if raw is None or len(raw) < 16:
        return None

    def bgr_quad(i: int) -> Tuple[int, int, int]:
        b, g, r = int(raw[i]), int(raw[i + 1]), int(raw[i + 2])
        return (r, g, b)

    ul = bgr_quad(0)
    ll = bgr_quad(4)
    ur = bgr_quad(8)
    lr = bgr_quad(12)
    return _menu_theme_dict_from_rgbs(ul, ur, ll, lr)


# Human-readable catalog for Help + Connectors UI (filter internal via catalog_entry_is_public).
# Optional per-arg ``hint_tags`` scopes inline placeholder hints in the Connectors UI; see
# ``game_hook_placeholder_lines`` in ``modules/uiwindows/connectors.py``.
FF7_CONNECTOR_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "add_gil",
        "label": "Add gil",
        "description": "Increases party gil in the active save (clamped).",
        "args": [
            {
                "name": "amount",
                "type": "ff7_text",
                "label": "Amount",
                "hint_tags": ("numeric", "random_range", "hooks_gil"),
            }
        ],
    },
    {
        "id": "remove_gil",
        "label": "Remove gil",
        "description": "Decreases party gil (not below zero).",
        "args": [
            {
                "name": "amount",
                "type": "ff7_text",
                "label": "Amount",
                "hint_tags": ("numeric", "random_range", "hooks_gil"),
            }
        ],
    },
    {
        "id": "add_party_hp",
        "label": "Add HP to character",
        "description": "Battle or field: party member by name, slot 0–2, or {random_character} (set in connector args). Amount: number or random:min-max.",
        "args": [
            {
                "name": "character",
                "type": "ff7_text",
                "label": "Character",
                "hint_tags": ("character",),
            },
            {
                "name": "amount",
                "type": "ff7_text",
                "label": "HP to add",
                "hint_tags": ("numeric", "random_range"),
            },
        ],
    },
    {
        "id": "remove_party_hp",
        "label": "Remove HP from character",
        "description": "Same as Add HP; HP not below zero.",
        "args": [
            {
                "name": "character",
                "type": "ff7_text",
                "label": "Character",
                "hint_tags": ("character",),
            },
            {
                "name": "amount",
                "type": "ff7_text",
                "label": "HP to remove",
                "hint_tags": ("numeric", "random_range"),
            },
        ],
    },
    {
        "id": "kill_party_member",
        "label": "KO party member",
        "description": "Battle only: Death + 0 HP. Target by name, slot 0–2, or {random_character}.",
        "args": [
            {
                "name": "character",
                "type": "ff7_text",
                "label": "Character",
                "hint_tags": ("character",),
            }
        ],
    },
    {
        "id": "kill_all_enemies",
        "label": "Kill all enemies",
        "description": "Battle only: sets Death status and HP to 0 for each active enemy."
    },
    {
        "id": "kill_enemy",
        "label": "Kill enemy",
        "description": "Battle only: enemy by substring name, slot index 0–5, or {random_enemy}. When several foes share a name (overlay shows Name A, Name B), use that form.",
        "args": [
            {
                "name": "enemy",
                "type": "ff7_text",
                "label": "Enemy",
                "hint_tags": ("enemy",),
            }
        ],
    },
    {
        "id": "damage_enemy",
        "label": "Damage enemy",
        "description": "Battle only: enemy by substring name, index 0–5, or {random_enemy}. Duplicates: use Name A / Name B as on the overlay. Damage 1–9999 or random:min-max (clamped).",
        "args": [
            {
                "name": "enemy",
                "type": "ff7_text",
                "label": "Enemy",
                "hint_tags": ("enemy",),
            },
            {
                "name": "amount",
                "type": "ff7_text",
                "label": "Damage",
                "hint_tags": ("damage",),
            },
        ],
    },
    {
        "id": "rename_character",
        "label": "Rename character",
        "description": "Field savemap: find record by current display name, write new FF7 name (12 chars).",
        "args": [
            {
                "name": "current_name",
                "type": "ff7_text",
                "label": "Current name",
                "hint_tags": ("character",),
            },
            {
                "name": "new_name",
                "type": "ff7_text",
                "label": "New name",
                "hint_tags": ("character",),
            },
        ],
    },
    {
        "id": "set_battle_status",
        "label": "Inflict status effects",
        "description": "Battle only: status flags (Fury, Sadness, Haste, …).",
        "args": [
            {
                "name": "character",
                "type": "ff7_text",
                "label": "Character",
                "hint_tags": ("character",),
            },
            {
                "name": "status_effect",
                "type": "ff7_text",
                "label": "Status (e.g. Fury)",
            },
            {
                "name": "mode",
                "type": "ff7_text",
                "label": "Mode",
                "control": "select",
                "options": {
                    "on": "On",
                    "off": "Off",
                    "toggle": "Toggle",
                },
            },
        ],
    },
    {
        "id": "set_character_gear",
        "label": "Change character gear",
        "description": "Field: equip slot + item. Use {random_weapon}, {random_armor}, or {random_accessory} in connector args when needed.",
        "args": [
            {
                "name": "character",
                "type": "ff7_text",
                "label": "Character",
                "hint_tags": ("character",),
            },
            {
                "name": "gear_kind",
                "type": "ff7_text",
                "label": "Slot (weapon / armor / accessory)",
                "hint_tags": ("message", "gear"),
            },
            {
                "name": "gear",
                "type": "ff7_text",
                "label": "Item",
                "hint_tags": ("gear",),
            },
        ],
    },
    {
        "id": "set_menu_row_access",
        "label": "Toggle Menu Access",
        "description": "Show and unlock a main-menu row, or hide and lock it. Optional duration_sec reverts visibility+locks.",
        "args": [
            {
                "name": "menu_name",
                "type": "ff7_text",
                "label": "Menu row",
            },
            {
                "name": "access",
                "type": "ff7_text",
                "label": "Access",
                "control": "select",
                "options": {
                    "allow": "Allow (visible + unlocked)",
                    "block": "Block (hidden + locked)",
                },
            },
            {
                "name": "duration_sec",
                "type": "non_negative_int",
                "label": "Auto-revert after (seconds, 0 = keep)",
                "hint_tags": ("numeric",),
            },
        ],
    },
    {
        "id": "set_menu_visibility",
        "label": "Set menu row visibility",
        "description": "Show or hide one main-menu row. Optional duration_sec restores prior visibility+lock words.",
        "args": [
            {"name": "menu_name", "type": "ff7_text", "label": "Menu row"},
            {
                "name": "enabled",
                "type": "ff7_text",
                "label": "Visible",
                "control": "select",
                "options": {"1": "Show", "0": "Hide"},
            },
            {
                "name": "duration_sec",
                "type": "non_negative_int",
                "label": "Auto-revert after (seconds, 0 = keep)",
                "hint_tags": ("numeric",),
            },
        ],
    },
    {
        "id": "set_menu_lock",
        "label": "Set menu row lock",
        "description": "Lock or unlock one main-menu row. Optional duration_sec restores prior visibility+lock words.",
        "args": [
            {"name": "menu_name", "type": "ff7_text", "label": "Menu row"},
            {
                "name": "locked",
                "type": "ff7_text",
                "label": "Locked",
                "control": "select",
                "options": {"1": "Lock", "0": "Unlock"},
            },
            {
                "name": "duration_sec",
                "type": "non_negative_int",
                "label": "Auto-revert after (seconds, 0 = keep)",
                "hint_tags": ("numeric",),
            },
        ],
    },
    {
        "id": "set_game_speed",
        "label": "Game speed",
        "description": "0.25×–8× (0.25 steps). Vanilla tick f64; FFNx literal f64. duration_sec 0 keeps until restore or exit.",
        "args": [
            {
                "name": "speed",
                "type": "ff7_text",
                "label": "Speed multiplier",
                "control": "select",
                "options": {},  # filled at runtime in UI from ff7_game_speed_select_options()
            },
            {
                "name": "duration_sec",
                "type": "non_negative_int",
                "label": "Auto-restore after (seconds, 0 = keep)",
                "hint_tags": ("numeric",),
            },
        ],
    },
    {
        "id": "set_battle_speed",
        "label": "Battle speed (savemap)",
        "description": "Config-menu battle speed (ATB charge rate), savemap 0x10D8: 0=fastest … 255=slowest. Names: fastest/fast/normal/slow/slowest or 0–255.",
        "args": [
            {
                "name": "speed",
                "type": "ff7_text",
                "label": "Speed (byte, name, or 0–255)",
            },
            {
                "name": "duration_sec",
                "type": "non_negative_int",
                "label": "Auto-restore after (seconds, 0 = keep)",
                "hint_tags": ("numeric",),
            },
        ],
    },
    {
        "id": "set_battle_atb_mode",
        "label": "Battle ATB mode (savemap)",
        "description": "Active, Wait, or Recommended — savemap general byte 0x10DA bits 6–7 (ff7-flat-wiki).",
        "args": [
            {
                "name": "mode",
                "type": "ff7_text",
                "label": "Mode (active / wait / recommended)",
            },
            {
                "name": "duration_sec",
                "type": "non_negative_int",
                "label": "Auto-restore after (seconds, 0 = keep)",
                "hint_tags": ("numeric",),
            },
        ],
    },
    {
        "id": "set_infinite_items",
        "label": "Battle infinite items (experimental)",
        "description": "When enabled, tops up battle item quantities (RAM at ff7-lib battle_party_items). duration_sec 0 or None = until disabled. Text 'none' for duration = permanent timer side.",
        "args": [
            {
                "name": "enabled",
                "type": "ff7_text",
                "label": "Enabled (1/0)",
            },
            {
                "name": "duration_sec",
                "type": "ff7_text",
                "label": "Seconds until auto-off (0 / none / empty = keep)",
                "hint_tags": ("numeric",),
            },
        ],
    },
    {
        "id": "set_party_level",
        "label": "Set party member level",
        "description": "Savemap level (1-99); mirrored into the battle actor and followed by a stat recalc.",
        "args": [
            {
                "name": "character",
                "type": "ff7_text",
                "label": "Character",
                "hint_tags": ("character",),
            },
            {
                "name": "level",
                "type": "ff7_text",
                "label": "Level (1-99)",
                "hint_tags": ("numeric", "random_range"),
            },
        ],
    },
    {
        "id": "set_enemy_level",
        "label": "Set enemy level",
        "description": "Battle only: writes the enemy data record level byte (affects derived stats on next action).",
        "args": [
            {
                "name": "enemy",
                "type": "ff7_text",
                "label": "Enemy",
                "hint_tags": ("enemy",),
            },
            {
                "name": "level",
                "type": "ff7_text",
                "label": "Level (1-99)",
                "hint_tags": ("numeric", "random_range"),
            },
        ],
    },
    {
        "id": "set_party_stat",
        "label": "Set party member stat",
        "description": "Savemap stat write + battle-actor mirror for hp/mp/max_hp/max_mp.",
        "args": [
            {
                "name": "character",
                "type": "ff7_text",
                "label": "Character",
                "hint_tags": ("character",),
            },
            {
                "name": "stat",
                "type": "ff7_text",
                "label": "Stat (str / vit / mag / spr / dex / luk / hp / mp / max_hp / max_mp)",
                "hint_tags": ("message",),
            },
            {
                "name": "amount",
                "type": "ff7_text",
                "label": "Amount",
                "hint_tags": ("numeric", "random_range"),
            },
        ],
    },
    {
        "id": "set_enemy_stat",
        "label": "Set enemy stat",
        "description": "Writes enemy_data_base (level/speed/luck/evade/str/def/mag/mdef) or battle-actor (hp/mp/max_hp/max_mp).",
        "args": [
            {
                "name": "enemy",
                "type": "ff7_text",
                "label": "Enemy",
                "hint_tags": ("enemy",),
            },
            {
                "name": "stat",
                "type": "ff7_text",
                "label": "Stat (level / speed / luck / evade / str / def / mag / mdef / hp / max_hp / mp / max_mp)",
                "hint_tags": ("message",),
            },
            {
                "name": "amount",
                "type": "ff7_text",
                "label": "Amount",
                "hint_tags": ("numeric", "random_range"),
            },
        ],
    },
    {
        "id": "set_menu_colors",
        "label": "Change menu colors",
        "description": "Writes RGB to menu corner(s). Accepts named colors (red/blue/…), #RRGGBB, rgb(r,g,b) or r,g,b.",
        "args": [
            {
                "name": "target",
                "type": "ff7_text",
                "label": "Target (all, upper_left, upper_right, lower_left, lower_right, top, bottom, left, right)",
                "hint_tags": ("message",),
            },
            {
                "name": "color",
                "type": "ff7_text",
                "label": "Color (name / #RRGGBB / r,g,b)",
            },
        ],
    },
    {
        "id": "equip_materia",
        "label": "Equip / unequip materia",
        "description": "Swaps inventory materia into a weapon/armor slot. Use 'none' to unequip; any displaced materia returns to inventory.",
        "args": [
            {
                "name": "character",
                "type": "ff7_text",
                "label": "Character",
                "hint_tags": ("character",),
            },
            {
                "name": "gear_kind",
                "type": "ff7_text",
                "label": "Gear (weapon or armor)",
                "hint_tags": ("message",),
            },
            {
                "name": "slot",
                "type": "ff7_text",
                "label": "Slot (0-7)",
                "hint_tags": ("message", "numeric", "random_range"),
            },
            {
                "name": "materia",
                "type": "ff7_text",
                "label": "Materia ('none' to unequip)",
            },
        ],
    },
    {
        "id": "start_battle",
        "label": "Start battle",
        "description": "Starts the given battle_id. Queued until Field/World if currently in Menu or Victory.",
        "args": [
            {
                "name": "battle_id",
                "type": "ff7_text",
                "label": "Battle ID",
                "hint_tags": ("numeric", "random_range"),
            },
        ],
    },
    {
        "id": "add_item",
        "label": "Add item",
        "description": "Calls party_add_item_fn to add quantity copies of an item by name.",
        "args": [
            {
                "name": "item",
                "type": "ff7_text",
                "label": "Item name",
            },
            {
                "name": "quantity",
                "type": "ff7_text",
                "label": "Quantity (1-99)",
                "hint_tags": ("numeric", "random_range"),
            },
        ],
    },
    {
        "id": "add_materia",
        "label": "Add materia",
        "description": "Calls party_add_materia_fn to place a materia (at the chosen level) into inventory.",
        "args": [
            {
                "name": "materia",
                "type": "ff7_text",
                "label": "Materia name",
            },
            {
                "name": "materia_level",
                "type": "ff7_text",
                "label": "Level 1-5, 6 for Master, or raw AP (e.g. 2000)",
                "hint_tags": ("message", "numeric", "random_range"),
            },
        ],
    },
    {
        "id": "add_gear",
        "label": "Add gear",
        "description": "Adds a weapon, armor, or accessory to inventory by name (single copy).",
        "args": [
            {
                "name": "gear",
                "type": "ff7_text",
                "label": "Gear name (weapon / armor / accessory)",
                "hint_tags": ("gear",),
            },
        ],
    },
    {
        "id": "restore_menu_words",
        "label": "Restore menu u16 words (internal)",
        "description": "Restores menu visibility/lock masks captured before a timed connector change.",
        "args": [],
        "internal": True,
    },
    {
        "id": "restore_battle_speed",
        "label": "Restore battle speed byte (internal)",
        "description": "Writes previous savemap battle speed byte.",
        "args": [{"name": "prev_byte", "type": "non_negative_int", "label": "Previous byte"}],
        "internal": True,
    },
    {
        "id": "restore_battle_atb_mode",
        "label": "Restore ATB config byte (internal)",
        "description": "Writes previous savemap 0x10DA byte.",
        "args": [
            {"name": "prev_config_byte", "type": "non_negative_int", "label": "Previous byte"}
        ],
        "internal": True,
    },
    {
        "id": "restore_infinite_items",
        "label": "Disable infinite battle items (internal)",
        "description": "Restores battle_party_items RAM snapshot.",
        "args": [],
        "internal": True,
    },
    {
        "id": "restore_game_speed",
        "label": "Restore game speed (internal)",
        "description": "Restores FPS saved by Game speed. Used by timers; not shown in the operation list.",
        "args": [],
        "internal": True,
    },
]


def catalog_entry_is_public(entry: Dict[str, Any]) -> bool:
    return not entry.get("internal")


FF7_PRESET_SPEED_MULTIPLIERS = frozenset({0.25, 0.5, 1.0, 2.0, 5.0})

FF7_SETSPEED_TICK_NUMERATOR: float = 10000000.0


def ffnx_fps_literal_f64(speed: float) -> Tuple[float, float]:
    """FFNx branch: f64 literals at movsd targets (field/world = 30×, battle = 15×)."""
    s = float(speed)
    return 30.0 * s, 15.0 * s


def vanilla_tick_interval_f64(default_fps: float, speed: float) -> float:
    """Vanilla branch: tick interval f64 at field_fps / battle_fps / world_fps sites."""
    return FF7_SETSPEED_TICK_NUMERATOR / (float(default_fps) * float(speed))


def ff7_game_speed_select_options() -> Dict[str, str]:
    """Granular 0.25×–8× (0.25 step). """
    out: Dict[str, str] = {}
    for i in range(1, 33):
        n = round(i * 0.25, 2)
        label = str(n).rstrip("0").rstrip(".") if n % 1 else str(int(n))
        tag = "" if n in FF7_PRESET_SPEED_MULTIPLIERS else ""
        out[str(n)] = f"{label}×{tag}"
    return out


@dataclass
class _ProcessHandle:
    pid: int
    handle: int
    module_base: int


class FF7Hook:
    """Attach to ff7_en.exe / ff7.exe: read snapshots and optional memory writes."""

    _exe_names = ("ff7_en.exe", "ff7.exe")

    def __init__(self) -> None:
        self._proc: Optional[_ProcessHandle] = None
        self._last_snapshot: Optional[Dict[str, Any]] = None
        self._speed_backup: Optional[Dict[str, Any]] = None
        self._pending_battle_id: Optional[int] = None
        self._post_success_timed: List[Tuple[str, Dict[str, Any], int]] = []
        self._battle_speed_restore_byte: Optional[int] = None
        self._infinite_items_active = False
        self._infinite_items_backup: Optional[bytes] = None
        self._infinite_items_backup_len = 0
        self._prev_inventory_sig: Optional[bytes] = None
        self._prev_gil_for_items: Optional[int] = None
        self._last_item_gain: Optional[Dict[str, Any]] = None
        self._battle_log_lines: List[str] = []
        self._prev_battle_status: Dict[str, int] = {}
        self._battle_log_seen: Set[Tuple[int, int]] = set()
        self._bl_name_cache: Optional[Dict[str, Any]] = None
        self._was_in_battle = False
        self._battle_ui_latched: bool = False
        self._battle_ui_off_ticks: int = 0

    def consume_timed_schedules(self) -> List[Tuple[str, Dict[str, Any], int]]:
        """Pop (restore_op, kwargs, delay_sec) entries queued by the last successful write."""
        out = list(self._post_success_timed)
        self._post_success_timed.clear()
        return out

    def close(self) -> None:
        if sys.platform != "win32" or _kernel32 is None:
            self._proc = None
            return
        if self._proc and self._proc.handle:
            try:
                _CloseHandle(self._proc.handle)
            except Exception:
                pass
        self._proc = None
        self._battle_ui_latched = False
        self._battle_ui_off_ticks = 0
        self._bl_name_cache = None

    def _find_pid(self) -> Optional[int]:
        try:
            import psutil
        except Exception:
            return None
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if name in self._exe_names:
                    return int(proc.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None

    def _module_base(self, pid: int) -> Optional[int]:
        try:
            import psutil

            p = psutil.Process(pid)
            for m in p.memory_maps(grouped=False):
                path = (m.path or "").replace("\\", "/").lower()
                for ex in self._exe_names:
                    if path.endswith("/" + ex):
                        addr = (m.addr or "").split("-")[0].strip()
                        return int(addr, 16)
        except Exception as e:
            logger.debug("FF7 module base: %s", e)
        return None

    def _attach(self) -> Tuple[bool, Optional[str]]:
        if sys.platform != "win32" or _kernel32 is None:
            return False, "FF7 memory hook is only supported on Windows"
        pid = self._find_pid()
        if pid is None:
            return False, "FF7 process not found (ff7_en.exe / ff7.exe)"
        base = self._module_base(pid)
        if base is None:
            return False, "Could not resolve FF7 module base address"
        h = _OpenProcess(_PROCESS_ACCESS, False, pid)
        if not h:
            err = ctypes.get_last_error()
            return False, f"OpenProcess failed (pid={pid}, err={err})"
        self.close()
        self._proc = _ProcessHandle(pid=pid, handle=int(h), module_base=base)
        return True, None

    def ensure_attached(self) -> Tuple[bool, Optional[str]]:
        if self._proc:
            try:
                import psutil

                if not psutil.pid_exists(self._proc.pid):
                    self.close()
            except Exception:
                self.close()
        if not self._proc:
            return self._attach()
        return True, None

    def _read(self, addr: int, size: int) -> Optional[bytes]:
        if not self._proc or addr <= 0 or size <= 0:
            return None
        buf = ctypes.create_string_buffer(size)
        read = ctypes.c_size_t(0)
        ok = _ReadProcessMemory(
            self._proc.handle,
            ctypes.c_void_p(addr),
            buf,
            size,
            ctypes.byref(read),
        )
        if not ok or read.value != size:
            return None
        return buf.raw

    def _write(self, addr: int, data: bytes) -> bool:
        if (
            sys.platform != "win32"
            or _kernel32 is None
            or not self._proc
            or addr <= 0
            or not data
        ):
            return False
        written = ctypes.c_size_t(0)
        ok = _WriteProcessMemory(
            self._proc.handle,
            ctypes.c_void_p(addr),
            data,
            len(data),
            ctypes.byref(written),
        )
        return bool(ok) and written.value == len(data)

    def _run_remote_shellcode(self, sc: bytes, label: str) -> bool:
        """Allocate, write, and run ``sc`` as a remote thread in the FF7 process.

        Shared plumbing for :meth:`_call_ff7_party_stat_recalc` and
        :meth:`_call_ff7_game_fn_one_arg`.
        """
        if (
            sys.platform != "win32"
            or _kernel32 is None
            or not self._proc
            or _VirtualAllocEx is None
            or _VirtualFreeEx is None
            or _CreateRemoteThread is None
            or _WaitForSingleObject is None
        ):
            return False
        MEM_COMMIT = 0x1000
        MEM_RESERVE = 0x2000
        MEM_RELEASE = 0x8000
        remote = None
        try:
            remote = _VirtualAllocEx(
                self._proc.handle,
                None,
                len(sc),
                MEM_COMMIT | MEM_RESERVE,
                _PAGE_EXECUTE_READWRITE,
            )
            if not remote:
                logger.debug("%s: VirtualAllocEx failed", label)
                return False
            ra = int(ctypes.cast(remote, ctypes.c_void_p).value or 0)
            if not ra or not self._write(ra, sc):
                logger.debug("%s: shellcode write failed", label)
                return False
            tid = wintypes.DWORD(0)
            h_thread = _CreateRemoteThread(
                self._proc.handle,
                None,
                0,
                ctypes.c_void_p(ra),
                None,
                0,
                ctypes.byref(tid),
            )
            if not h_thread:
                logger.debug("%s: CreateRemoteThread failed", label)
                return False
            try:
                w = _WaitForSingleObject(h_thread, 5000)
                if w != 0:
                    logger.debug("%s: WaitForSingleObject=%s", label, w)
                    return False
            finally:
                _CloseHandle(h_thread)
            return True
        except Exception as e:
            logger.debug("%s: %s", label, e)
            return False
        finally:
            if remote:
                try:
                    _VirtualFreeEx(self._proc.handle, remote, 0, MEM_RELEASE)
                except Exception:
                    pass

    def _call_ff7_party_stat_recalc(self) -> None:
        """Best-effort: post-equip refresh via remote thread stub."""
        if not self._proc:
            return
        target = _rebase(self._proc.module_base, ADDR_PARTY_STAT_RECALC_FN)
        sc = (
            bytes([0xB8])
            + struct.pack("<I", target & 0xFFFFFFFF)
            + bytes([0xFF, 0xD0, 0x33, 0xC0, 0xC3])
        )
        self._run_remote_shellcode(sc, "party stat recalc")

    def _call_ff7_game_fn_one_arg(self, fn_rva: int, arg_u32: int) -> bool:
        """Call a ``stdcall/cdecl`` FF7 function that takes a single u32 arg.

        Used for ``party_add_item_fn`` and ``party_add_materia_fn``. The caller
        cleans the stack with ``add esp, 4`` so this is cdecl-safe.
        """
        if not self._proc:
            return False
        target = _rebase(self._proc.module_base, fn_rva) & 0xFFFFFFFF
        arg = int(arg_u32) & 0xFFFFFFFF
        # push imm32 ; mov eax, target ; call eax ; add esp, 4 ; xor eax,eax ; ret
        sc = (
            bytes([0x68])
            + struct.pack("<I", arg)
            + bytes([0xB8])
            + struct.pack("<I", target)
            + bytes([0xFF, 0xD0, 0x83, 0xC4, 0x04, 0x33, 0xC0, 0xC3])
        )
        return self._run_remote_shellcode(sc, f"call fn {fn_rva:#x}")

    def _read_u8(self, addr: int) -> Optional[int]:
        d = self._read(addr, 1)
        return d[0] if d else None

    def _read_u16(self, addr: int) -> Optional[int]:
        d = self._read(addr, 2)
        return struct.unpack("<H", d)[0] if d else None

    def _read_i16(self, addr: int) -> Optional[int]:
        d = self._read(addr, 2)
        return struct.unpack("<h", d)[0] if d else None

    def _read_u32(self, addr: int) -> Optional[int]:
        d = self._read(addr, 4)
        return struct.unpack("<I", d)[0] if d else None

    def _read_i32(self, addr: int) -> Optional[int]:
        d = self._read(addr, 4)
        return struct.unpack("<i", d)[0] if d else None

    def _read_kernel_name_string(self, addr: int, max_len: int) -> str:
        """FF7 kernel text: bytes until 0xFF, same encoding as _decode_ff7_name."""
        if not self._proc or addr <= 0:
            return ""
        raw = self._read(addr, max_len)
        if not raw:
            return ""
        for i, b in enumerate(raw):
            if b == 0xFF:
                raw = raw[:i]
                break
        return _decode_ff7_name(bytes(raw)) if raw else ""

    def _read_kernel_section_strings(self, section_id: int, count: int) -> List[str]:
        """Port of ff7-lib read_kernel_section (command/attack name tables)."""
        if not self._proc or count <= 0:
            return []
        mb = self._proc.module_base
        base = _rebase(mb, ADDR_KERNEL_TEXTS_BASE)
        # FFNx sets this dword to 0; vanilla is non-zero. Do not use "or 1" — 0 is valid (ff7-lib).
        ffnx_check = self._read_u32(base)
        if ffnx_check is None:
            return []
        if ffnx_check == 0:
            kc = _rebase(mb, ADDR_KERNEL_READ_FN_CALL)
            w = self._read_u32(kc) or 0
            kread = int(w) + kc + 4
            tbl = self._read_u32(kread + 0x1B) or 0
            if tbl == 0:
                return []
            table_addr = int(self._read_u32(tbl + 4 * int(section_id)) or 0)
        else:
            off = int(self._read_u16(_rebase(mb, ADDR_KERNEL_SECTION_OFFSETS) + 2 * int(section_id)) or 0)
            table_addr = int(base) + off
        if table_addr == 0:
            return []
        out: List[str] = []
        for i in range(int(count)):
            rel = int(self._read_u16(table_addr + i * 2) or 0)
            saddr = int(table_addr) + rel
            out.append(self._read_kernel_name_string(saddr, 24))
        return out

    def _read_enemy_display_attack_names_32(self) -> List[str]:
        """
        ff7-lib read_enemy_attack_names: 32 display slots at ADDR_ENEMY_ATTACK_NAMES.
        These are overwritten during battle; do not cache for multi-second periods.
        """
        if not self._proc:
            return []
        out: List[str] = []
        try:
            base = _rebase(self._proc.module_base, ADDR_ENEMY_ATTACK_NAMES)
            for i in range(32):
                b0 = self._read_u8(base + i * 32)
                if b0 is not None and b0 == 0xFF:
                    out.append("")
                else:
                    out.append(self._read_kernel_name_string(base + i * 32, 32))
        except Exception as e:
            logger.debug("enemy display names 32: %s", e)
        return out

    def _ensure_battle_log_names(self) -> None:
        if self._bl_name_cache is not None:
            return
        cache: Dict[str, Any] = {"command": [], "attack": []}
        if not self._proc:
            self._bl_name_cache = cache
            return
        try:
            cache["command"] = self._read_kernel_section_strings(8, 32)
            cache["attack"] = self._read_kernel_section_strings(9, 128)
        except Exception as e:
            logger.debug("battle log name cache: %s", e)
        self._bl_name_cache = cache

    def _format_battle_command_text(self, command_id: int, parameter: int) -> str:
        _ensure = self._ensure_battle_log_names
        _ensure()
        c = self._bl_name_cache or {}
        cmds: List[str] = list(c.get("command", []))
        atks: List[str] = list(c.get("attack", []))
        cid = int(command_id) & 0xFF
        base_name = _battle_log_cmd_display(cid, cmds) or f"cmd 0x{cid:02X}"
        if command_id == 0x23:
            return "Poison"
        if command_id == 0x02 and 0 <= parameter < 56:
            t = _battle_log_atk_display(int(parameter), atks)
            return t or f"Magic {parameter}"
        if command_id == 0x02:
            return f"Magic {parameter}"
        if command_id == 0x03:
            t = _battle_log_atk_display(int(parameter) + 56, atks)
            return t or f"Summon {parameter}"
        if command_id == 0x04:
            return _ff7_unified_item_name(int(parameter) & 0xFFFF)
        if command_id == 0x0D and 0 <= (parameter + 72) < 128:
            sub = _battle_log_atk_display(int(parameter) + 72, atks) or f"E.Skill {parameter}"
            return f"{base_name}: {sub}"
        if command_id == 0x0D:
            return f"{base_name}: E.Skill {parameter}"
        if command_id == 0x20:
            # 0-31: rotating display buffer at enemy_attack_names (ff7-lib) — read fresh; never
            # use a cached copy (stale names from a prior enemy/animation look like wrong attacks).
            # >=32: many enemies use the global 128 attack-name table (same as kernel section 9), e.g. 0x20+.
            p = int(parameter) & 0xFFFF
            e32 = self._read_enemy_display_attack_names_32()
            if p < 32 and p < len(e32) and (e32[p] or "").strip():
                return (e32[p] or "").strip()
            t = _battle_log_atk_display(p & 0x7F, atks)
            if t:
                return t
            return f"Attack 0x{p:X}"
        return (base_name or f"0x{command_id:02X}").strip() or f"0x{command_id:02X}"

    @staticmethod
    def _target_label_from_mask(
        tmask: int, party: List[Dict[str, Any]], enemies: List[Dict[str, Any]]
    ) -> str:
        """Match ff7-ultima BattleLogRow: bits 0–3 allies, 4–7 enemies (8 targets)."""
        names: List[str] = []
        has_tg = False
        all_allies = True
        all_foes = True
        for i in range(8):
            if (tmask & (1 << i)) == 0:
                continue
            has_tg = True
            if i < 4:
                all_foes = False
                row = next(
                    (r for r in party if int(r.get("party_slot", r.get("slot", -1))) == i),
                    None,
                )
                if row and (row.get("name") or "").strip():
                    names.append(str(row.get("name")).strip())
            else:
                all_allies = False
                ei = i - 4
                row = next(
                    (e for e in enemies if int(e.get("slot", -1)) == ei + 4),
                    None,
                )
                if not row:
                    continue
                base = (str(row.get("name") or "?")).strip() or "?"
                sames = [e for e in enemies if (e.get("name") or "").strip() == (row.get("name") or "").strip()]
                if len(sames) > 1:
                    sames = sorted(sames, key=lambda x: int(x.get("slot", 0)))
                    try:
                        pos = sames.index(row)
                    except ValueError:
                        pos = 0
                    names.append(f"{base} {chr(65 + (pos % 26))}")
                else:
                    names.append(base)
        if not has_tg or not names:
            return ""
        if len(names) == 1:
            return names[0]
        if has_tg and all_allies and not all_foes:
            return "All Allies"
        if has_tg and all_foes and not all_allies:
            return "All Enemies"
        return " and ".join(names)

    @staticmethod
    def _command_has_target_mask(command_id: int) -> bool:
        return int(command_id) not in (0x12, 0x13)

    @staticmethod
    def _command_verb(command_id: int) -> str:
        cid = int(command_id) & 0xFF
        if cid == 0x02:
            return "cast"
        if cid == 0x03:
            return "summoned"
        if cid == 0x04:
            return "used"
        if cid == 0x23:
            return "took"
        return "used"

    def _format_battle_log_line(
        self,
        party: List[Dict[str, Any]],
        enemies: List[Dict[str, Any]],
        attacker: int,
        command_id: int,
        parameter: int,
        target_mask: int,
        damage: int,
        miss: bool,
        crit: bool,
    ) -> str:
        aid = int(attacker) & 0xFF
        if int(command_id) & 0xFF == 0xFF:
            return ""
        a_name: Optional[str] = None
        if aid < 4:
            pr = next(
                (r for r in party if int(r.get("party_slot", r.get("slot", -1))) == aid),
                None,
            )
            a_name = (str(pr.get("name")).strip() if pr else None) or None
        else:
            ei = aid - 4
            row = next(
                (e for e in enemies if int(e.get("slot", -1)) == ei + 4),
                None,
            )
            if row:
                base = (str(row.get("name") or "?")).strip() or "?"
                sames = [
                    e
                    for e in enemies
                    if (e.get("name") or "").strip() == (row.get("name") or "").strip()
                ]
                if len(sames) > 1:
                    sames = sorted(sames, key=lambda x: int(x.get("slot", 0)))
                    try:
                        pos = sames.index(row)
                    except ValueError:
                        pos = 0
                    a_name = f"{base} {chr(65 + (pos % 26))}"
                else:
                    a_name = base
        cmd_t = self._format_battle_command_text(int(command_id) & 0xFF, int(parameter) & 0xFFFF)
        has_tg = self._command_has_target_mask(int(command_id) & 0xFF)
        tg = (
            self._target_label_from_mask(int(target_mask) & 0xFFFF, party, enemies)
            if has_tg
            else ""
        )
        vrb = self._command_verb(int(command_id) & 0xFF)
        pre = f"{a_name} {vrb} {cmd_t}" if a_name else f"Unknown {vrb} {cmd_t}"
        if has_tg and tg:
            pre += f" on {tg}"
        dam = int(damage) if damage is not None else 0
        if not self._command_deals_damage_battlelog(int(command_id) & 0xFF, int(parameter) & 0xFFFF):
            return pre
        if miss:
            return f"{pre}, missed"
        suf = f", dealt {abs(dam)} damage"
        if crit:
            suf += " [crit]"
        return pre + suf

    def _command_deals_damage_battlelog(self, command_id: int, parameter: int) -> bool:
        if int(command_id) in (0x12, 0x13, 0x05, 0x06, 0x0B):
            return False
        if int(command_id) == 0x04:
            return True
        return True

    def _read_savemap(self) -> Tuple[Optional[bytes], int]:
        if not self._proc:
            return None, 0
        addr = _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE)
        data = self._read(addr, SAVEMAP_SIZE)
        if data and data == b"\x00" * len(data):
            return None, addr
        return data, addr

    def _current_module_byte(self) -> Optional[int]:
        if not self._proc:
            return None
        mod = _rebase(self._proc.module_base, ADDR_CURRENT_MODULE)
        return self._read_u8(mod)

    def _parse_field_party(
        self, savemap: bytes
    ) -> Tuple[List[Dict[str, Any]], List[int]]:
        party_ids: List[int] = []
        for i in range(3):
            if len(savemap) > SAVE_OFF_PARTY_SLOTS + i:
                cid = savemap[SAVE_OFF_PARTY_SLOTS + i]
                party_ids.append(cid if cid < 0xFF else -1)
        members: List[Dict[str, Any]] = []
        for party_slot, cid in enumerate(party_ids):
            if cid < 0 or cid >= len(_CHAR_BLOCK):
                row = {
                    "party_slot": party_slot,
                    "id": cid,
                    "name": "",
                    "level": 0,
                    "hp": 0,
                    "max_hp": 0,
                    "mp": 0,
                    "max_mp": 0,
                    "limit": 0,
                    "atb": 0.0,
                    "slot_empty": True,
                }
                row.update(_empty_gear_materia())
                members.append(row)
                continue
            off = _CHAR_BLOCK[cid]
            if off + 0x84 > len(savemap):
                continue
            rec = savemap[off : off + 0x84]
            name = _decode_ff7_name(rec[0x10:0x1C])
            level = rec[0x01]
            limit_bar = rec[0x0F]
            hp = struct.unpack_from("<H", rec, REC_OFF_HP)[0]
            max_hp = struct.unpack_from("<H", rec, REC_OFF_MAX_HP)[0]
            mp = struct.unpack_from("<H", rec, REC_OFF_MP)[0]
            max_mp = struct.unpack_from("<H", rec, REC_OFF_MAX_MP)[0]
            row = {
                "party_slot": party_slot,
                "id": cid,
                "name": name,
                "level": int(level),
                "hp": int(hp),
                "max_hp": int(max_hp) if max_hp else int(hp),
                "mp": int(mp),
                "max_mp": int(max_mp) if max_mp else int(mp),
                "limit": int(limit_bar),
                "atb": 0.0,
                "slot_empty": False,
            }
            row.update(_char_gear_materia(rec))
            members.append(row)
        return members, party_ids

    def _read_battle_common(
        self, slot: int
    ) -> Optional[Tuple[bytes, int, int, int, int, int, int, float]]:
        if not self._proc:
            return None
        base = (
            _rebase(self._proc.module_base, ADDR_BATTLE_CHAR_BASE)
            + slot * BATTLE_ACTOR_STRIDE
        )
        raw = self._read(base, BATTLE_ACTOR_STRIDE)
        if not raw:
            return None
        status = struct.unpack_from("<I", raw, 0x00)[0]
        flags = raw[0x05] if len(raw) > 5 else 0
        hp = struct.unpack_from("<i", raw, BATTLE_OFF_HP)[0]
        max_hp = struct.unpack_from("<i", raw, BATTLE_OFF_MAX_HP)[0]
        mp = struct.unpack_from("<h", raw, BATTLE_OFF_MP)[0]
        max_mp = struct.unpack_from("<h", raw, BATTLE_OFF_MAX_MP)[0]
        atb_addr = (
            _rebase(self._proc.module_base, ADDR_BATTLE_ATB_BASE)
            + slot * BATTLE_ATB_STRIDE
            + 0x02
        )
        atb_raw = self._read_u16(atb_addr)
        atb = (atb_raw or 0) / 65535.0 if atb_raw is not None else 0.0
        return (
            raw,
            int(status),
            int(flags),
            int(hp),
            int(max_hp),
            int(mp),
            int(max_mp),
            float(atb),
        )

    def _read_battle_ally(
        self, slot: int, savemap: Optional[bytes] = None
    ) -> Optional[Dict[str, Any]]:
        common = self._read_battle_common(slot)
        if common is None:
            return None
        raw, status, flags, hp, max_hp, mp, max_mp, atb = common

        party_id_addr = _rebase(self._proc.module_base, ADDR_PARTY_MEMBER_IDS) + slot
        party_id = self._read_u8(party_id_addr)
        if party_id is not None and party_id < 9:
            name_addr = (
                _rebase(self._proc.module_base, ADDR_PARTY_MEMBER_NAMES)
                + party_id * PARTY_RECORD_STRIDE
            )
            name_raw = self._read(name_addr, 12)
            name = _decode_ff7_name(name_raw) if name_raw else "?"
        else:
            name = "?"

        limit_addr = (
            _rebase(self._proc.module_base, ADDR_BATTLE_CHAR_ARRAY)
            + slot * BATTLE_CHAR_ARRAY_STRIDE
            + 0x08
        )
        limit_raw = self._read_u16(limit_addr)
        limit = int(limit_raw) if limit_raw is not None else 0

        level_off = raw[0x24] if len(raw) > 0x24 else 0
        level = int(level_off) if level_off else 0
        if level <= 0 and party_id is not None and party_id < len(_CHAR_BLOCK):
            sm = savemap
            if sm is None:
                sm, _ = self._read_savemap()
            if sm and len(sm) > _CHAR_BLOCK[party_id] + REC_OFF_LEVEL:
                cid = party_id
                off = _CHAR_BLOCK[cid]
                level = int(sm[off + REC_OFF_LEVEL])
        ailments = _ailments_from_status(status)
        return {
            "party_slot": slot,
            "slot": slot,
            "status": status,
            "status_raw": status,
            "ailments": ailments,
            "flags": flags,
            "hp": max(hp, 0),
            "max_hp": max(max_hp, 0),
            "mp": max(mp, 0),
            "max_mp": max(max_mp, 0),
            "atb": atb,
            "limit": limit,
            "name": name,
            "scene_id": 0,
            "level": int(level),
            "slot_empty": False,
        }

    def _read_battle_enemy(self, slot: int) -> Optional[Dict[str, Any]]:
        enemy_idx = slot - 4
        if not self._proc or enemy_idx < 0:
            return None

        scene_idx_addr = (
            _rebase(self._proc.module_base, ADDR_ENEMY_OBJ_BASE)
            + enemy_idx * ENEMY_OBJ_STRIDE
        )
        scene_idx = self._read_u8(scene_idx_addr)
        if scene_idx is None or scene_idx == 0xFF:
            return None

        common = self._read_battle_common(slot)
        if common is None:
            return None
        raw, status, flags, hp, max_hp, mp, max_mp, atb = common

        name_addr = (
            _rebase(self._proc.module_base, ADDR_ENEMY_DATA_BASE)
            + scene_idx * ENEMY_DATA_STRIDE
        )
        name_raw = self._read(name_addr, 24)
        name = _decode_ff7_name(name_raw) if name_raw else "?"

        level_addr = name_addr + 0x20
        level = self._read_u8(level_addr) or 0
        ailments = _ailments_from_status(status)

        return {
            "slot": slot,
            "status": status,
            "status_raw": status,
            "ailments": ailments,
            "flags": flags,
            "hp": max(hp, 0),
            "max_hp": max(max_hp, 0),
            "mp": max(mp, 0),
            "max_mp": max(max_mp, 0),
            "atb": atb,
            "limit": 0,
            "name": name,
            "scene_id": int(scene_idx),
            "level": int(level),
            "slot_empty": False,
        }

    def _battle_actor_addr(self, slot: int) -> int:
        assert self._proc
        return _rebase(self._proc.module_base, ADDR_BATTLE_CHAR_BASE) + slot * (
            BATTLE_ACTOR_STRIDE
        )

    def _field_char_hp_addr(self, savemap: bytes, party_slot: int) -> Optional[int]:
        if party_slot < 0 or party_slot > 2 or len(savemap) <= SAVE_OFF_PARTY_SLOTS + party_slot:
            return None
        cid = savemap[SAVE_OFF_PARTY_SLOTS + party_slot]
        if cid >= len(_CHAR_BLOCK) or cid == 0xFF:
            return None
        off = _CHAR_BLOCK[cid]
        if off + REC_OFF_HP + 2 > len(savemap):
            return None
        return _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE) + off + REC_OFF_HP

    def _field_char_max_hp(self, savemap: bytes, party_slot: int) -> Optional[int]:
        if party_slot < 0 or party_slot > 2:
            return None
        cid = savemap[SAVE_OFF_PARTY_SLOTS + party_slot]
        if cid >= len(_CHAR_BLOCK) or cid == 0xFF:
            return None
        off = _CHAR_BLOCK[cid]
        if off + REC_OFF_MAX_HP + 2 > len(savemap):
            return None
        return struct.unpack_from("<H", savemap, off + REC_OFF_MAX_HP)[0]

    def _field_char_hp_u16(self, savemap: bytes, party_slot: int) -> Optional[int]:
        addr = self._field_char_hp_addr(savemap, party_slot)
        if addr is None:
            return None
        return self._read_u16(addr)

    def _read_float(self, addr: int) -> Optional[float]:
        d = self._read(addr, 4)
        if not d:
            return None
        return struct.unpack("<f", d)[0]

    def _read_f64(self, addr: int) -> Optional[float]:
        d = self._read(addr, 8)
        if not d or len(d) != 8:
            return None
        return struct.unpack("<d", d)[0]

    def _write_float(self, addr: int, val: float) -> bool:
        return self._write(addr, struct.pack("<f", float(val)))

    def _write_double_robust(self, addr: int, val: float) -> bool:
        fv = float(val)
        payload = struct.pack("<d", fv)
        if self._write(addr, payload):
            got = self._read_f64(addr)
            if got is not None and abs(got - fv) <= max(1e-4, abs(fv) * 1e-9):
                return True
        for prot in (_PAGE_READWRITE, _PAGE_EXECUTE_READWRITE):
            if self._win_protect_write(
                addr, payload, flush_icache=False, page_protect=prot
            ):
                return True
        return False

    def _write_float_robust(self, addr: int, val: float) -> bool:
        """Write a 32-bit float: prefer a verified fast path, then PAGE_READWRITE, then RXW."""
        fv = float(val)
        payload = struct.pack("<f", fv)
        if self._write(addr, payload):
            got = self._read_float(addr)
            if got is not None and abs(got - fv) <= max(1e-4, abs(fv) * 1e-6):
                return True
        for prot in (_PAGE_READWRITE, _PAGE_EXECUTE_READWRITE):
            if self._win_protect_write(
                addr, payload, flush_icache=False, page_protect=prot
            ):
                return True
        return False

    def _win_protect_write(
        self,
        addr: int,
        data: bytes,
        *,
        flush_icache: bool = True,
        page_protect: int = _PAGE_EXECUTE_READWRITE,
    ) -> bool:
        """Write to memory in the **FF7** process via VirtualProtectEx (not the local process)."""
        if not self._proc or not data:
            return False
        h = self._proc.handle
        page = addr & ~0xFFF
        end = addr + len(data)
        page_end = (end + 0xFFF) & ~0xFFF
        size = page_end - page
        if _VirtualProtectEx is None:
            return self._write(addr, data)
        old = wintypes.DWORD(0)
        if not _VirtualProtectEx(
            h,
            ctypes.c_void_p(page),
            ctypes.c_size_t(size),
            page_protect,
            ctypes.byref(old),
        ):
            return False
        try:
            ok = self._write(addr, data)
        finally:
            _junk = wintypes.DWORD(0)
            _VirtualProtectEx(
                h,
                ctypes.c_void_p(page),
                ctypes.c_size_t(size),
                old.value,
                ctypes.byref(_junk),
            )
        if ok and flush_icache and _FlushInstructionCache:
            _FlushInstructionCache(h, ctypes.c_void_p(page), ctypes.c_size_t(size))
        return ok

    def _ffnx_trampoline_targets_fps_addrs(self, chk: int) -> Optional[Tuple[int, int]]:
        """Resolve (addr_fps30, addr_fps15) from FFNx hook stub at ``chk``.

        Caller must verify the byte at ``chk`` is ``0xE9`` (near jmp) before calling.
        """
        if not self._proc:
            return None
        rel = self._read_i32(chk + 1)
        if rel is None:
            return None
        base = chk + 5 + rel
        code = self._read(base, 256)
        if not code or len(code) < 64:
            return None
        pat = bytes([0xF2, 0x0F, 0x10, 0x05])
        fps30 = -1
        for i in range(len(code) - 8):
            if code[i : i + 4] == pat:
                fps30 = i
                break
        if fps30 < 0:
            return None
        rest = code[fps30 + 1 :]
        fps15rel = -1
        for i in range(len(rest) - 8):
            if rest[i : i + 4] == pat:
                fps15rel = i
                break
        if fps15rel < 0:
            return None
        fps15 = fps30 + 1 + fps15rel

        def rip(off: int) -> Optional[int]:
            if off + 8 > len(code):
                return None
            disp = struct.unpack_from("<i", code, off + 4)[0]
            return base + off + 8 + disp

        a30 = rip(fps30)
        a15 = rip(fps15)
        if a30 is None or a15 is None:
            return None
        return a30, a15

    def _ffnx_fps_pair_plausible(self, a30: int, a15: int) -> bool:
        """Diagnostic only: True if floats at resolved addresses look like literal FPS, not tick ticks.

        Do not use to choose vanilla vs FFNx branch — under FFNx, operands may still read as
        large tick-style values before writes"""
        if a30 == a15:
            return False
        v30 = self._read_f64(a30)
        v15 = self._read_f64(a15)
        if v30 is None or v15 is None:
            return False
        if not math.isfinite(v30) or not math.isfinite(v15):
            return False
        if v30 > 5000.0 or v15 > 5000.0:
            return False
        if v30 < 0.125 or v15 < 0.125:
            return False
        return True

    def _char_id_from_savemap_name(self, savemap: bytes, name: str) -> Optional[int]:
        want = _norm_party_name(name)
        if not want:
            return None
        for cid in range(9):
            off = _CHAR_BLOCK[cid]
            if off + REC_OFF_NAME + NAME_BYTES > len(savemap):
                continue
            raw = savemap[off + REC_OFF_NAME : off + REC_OFF_NAME + NAME_BYTES]
            if _norm_party_name(_decode_ff7_name(raw)) == want:
                return cid
        return None

    def _party_slot_from_token(
        self, savemap: Optional[bytes], token: str, battle: bool
    ) -> Tuple[Optional[int], Optional[str]]:
        t = (token or "").strip()
        if not t:
            return None, "empty character token"
        tl = t.lower()
        if tl in ("__random_party__", "random_character"):
            snap = self._last_snapshot or {}
            party = snap.get("party") or []
            names = [
                str(r.get("name", ""))
                for r in party
                if isinstance(r, dict) and not r.get("slot_empty")
            ]
            names = [n for n in names if n and n != "?"]
            if not names:
                return None, "no party members for random"
            pick = random.choice(names)
            return self._party_slot_from_token(savemap, pick, battle)
        if t.isdigit() and int(t) in (0, 1, 2):
            return int(t), None
        if savemap is None:
            data, _ = self._read_savemap()
            savemap = data
        if not savemap:
            return None, "savemap not readable"
        cid = self._char_id_from_savemap_name(savemap, t)
        if cid is None:
            return None, f"unknown character name: {t}"
        for ps in range(3):
            if savemap[SAVE_OFF_PARTY_SLOTS + ps] == cid:
                return ps, None
        return None, f"{t} is not in the active party (field)"

    def _char_id_from_gear_character_token(
        self, savemap: bytes, token: str
    ) -> Tuple[Optional[int], Optional[str]]:
        """Roster index 0–8 English name; does not require party membership."""
        t = (token or "").strip()
        if not t:
            return None, "empty character token"
        if t.isdigit():
            n = int(t)
            if 0 <= n <= 8:
                return n, None
            return None, "character index must be 0–8"
        cid = self._char_id_from_savemap_name(savemap, t)
        if cid is None:
            return None, f"unknown character name: {t}"
        return cid, None

    def _parse_int_or_random(
        self,
        raw: Any,
        default_max: int = 9999,
        *,
        clamp_1_9999: bool = False,
    ) -> Tuple[Optional[int], Optional[str]]:
        if raw is None:
            return None, "missing numeric value"
        if isinstance(raw, int):
            v = int(raw)
            if clamp_1_9999:
                v = max(1, min(9999, v))
            return v, None
        s = str(raw).strip()
        m = re.match(r"^random\s*:\s*(\d+)\s*-\s*(\d+)\s*$", s, re.I)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo > hi:
                lo, hi = hi, lo
            if clamp_1_9999:
                lo = max(1, min(9999, lo))
                hi = max(1, min(9999, hi))
                if lo > hi:
                    lo, hi = hi, lo
            return random.randint(lo, hi), None
        if s.lower() == "random":
            lo, hi = 1, min(9999, default_max) if clamp_1_9999 else (1, default_max)
            return random.randint(lo, hi), None
        try:
            v = int(s)
            if clamp_1_9999:
                v = max(1, min(9999, v))
            return v, None
        except ValueError:
            return None, f"not an int: {s}"

    def _parse_gil_amount(self, raw: Any) -> Tuple[Optional[int], Optional[str]]:
        """Parse gil add/remove amount after connector placeholder substitution."""
        if raw is None:
            return None, "missing amount"
        if isinstance(raw, bool):
            return None, "invalid amount"
        if isinstance(raw, int):
            v = int(raw)
            if v <= 0:
                return None, "amount must be positive"
            return min(v, _MAX_GIL), None
        if isinstance(raw, float):
            v = int(raw)
            if v <= 0:
                return None, "amount must be positive"
            return min(v, _MAX_GIL), None
        s = str(raw).strip()
        m = re.match(r"^random\s*:\s*(\d+)\s*-\s*(\d+)\s*$", s, re.I)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo > hi:
                lo, hi = hi, lo
            lo = max(1, min(_MAX_GIL, lo))
            hi = max(1, min(_MAX_GIL, hi))
            if lo > hi:
                lo, hi = hi, lo
            return random.randint(lo, hi), None
        if s.lower() == "random":
            return random.randint(1, min(999_999, _MAX_GIL)), None
        try:
            v = int(s)
        except ValueError:
            return None, f"not an integer: {s}"
        if v <= 0:
            return None, "amount must be positive"
        return min(v, _MAX_GIL), None

    def _parse_battle_speed_token(self, raw: Any) -> Tuple[Optional[int], Optional[str]]:
        if raw is None or str(raw).strip() == "":
            return None, "missing speed"
        s = str(raw).strip().lower()
        named = {
            "fastest": 0,
            "fast": 40,
            "normal": 128,
            "slow": 220,
            "slowest": 255,
        }
        if s in named:
            return named[s], None
        try:
            v = int(float(s))
            return max(0, min(255, v)), None
        except (TypeError, ValueError):
            return None, f"bad battle speed: {raw}"

    def _parse_float_or_random(self, raw: Any) -> Tuple[Optional[float], Optional[str]]:
        if raw is None:
            return None, "missing speed"
        if isinstance(raw, (int, float)):
            return float(raw), None
        s = str(raw).strip()
        m = re.match(r"^random\s*:\s*([0-9.]+)\s*-\s*([0-9.]+)\s*$", s, re.I)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            if lo > hi:
                lo, hi = hi, lo
            return random.uniform(lo, hi), None
        try:
            return float(s), None
        except ValueError:
            return None, f"not a float: {s}"

    def _menu_bit_index(self, menu_name: str) -> Tuple[Optional[int], Optional[str]]:
        want = (menu_name or "").strip().lower()
        for i, m in enumerate(FF7_MENU_NAMES):
            if m.lower() == want:
                return i, None
        return None, f"unknown menu: {menu_name}"

    def _gear_name_to_id(self, kind: str, gear_name: str) -> Tuple[Optional[int], Optional[str]]:
        _load_ff7_gear_layout_assets()
        clean = _sanitize_enemy_token(str(gear_name or ""))
        want = clean.strip().lower()
        if not want:
            return None, "empty gear name"
        if kind == "weapon":
            for sid, nm in _WEAPON_NAMES_EN.items():
                if nm and nm.strip().lower() == want:
                    return int(sid), None
            return None, f"unknown weapon: {clean}"
        if kind == "armor":
            for sid, nm in _ARMOR_NAMES_EN.items():
                if nm and nm.strip().lower() == want:
                    return int(sid), None
            return None, f"unknown armor: {clean}"
        if kind == "accessory":
            for sid, nm in _ACCESSORY_NAMES_EN.items():
                if nm and nm.strip().lower() == want:
                    return int(sid), None
            return None, f"unknown accessory: {clean}"
        return None, "gear_kind must be weapon|armor|accessory"

    def _gear_name_to_item_id(
        self, gear_name: str
    ) -> Tuple[Optional[int], Optional[str]]:
        """Resolve a weapon/armor/accessory name to the unified inventory item ID.

        Weapons occupy 0x80..0xFF, armor 0x100..0x11F, accessories 0x120..0x13F.
        Matches are case/whitespace insensitive.
        """
        _load_ff7_gear_layout_assets()
        clean = _sanitize_enemy_token(str(gear_name or "")).strip().lower()
        if not clean:
            return None, "empty gear name"
        for sid, nm in _WEAPON_NAMES_EN.items():
            if nm and nm.strip().lower() == clean:
                return ITEM_ID_WEAPON_BASE + int(sid), None
        for sid, nm in _ARMOR_NAMES_EN.items():
            if nm and nm.strip().lower() == clean:
                return ITEM_ID_ARMOR_BASE + int(sid), None
        for sid, nm in _ACCESSORY_NAMES_EN.items():
            if nm and nm.strip().lower() == clean:
                return ITEM_ID_ACCESSORY_BASE + int(sid), None
        return None, f"unknown gear: {gear_name}"

    def _item_name_to_id(
        self, item_name: str
    ) -> Tuple[Optional[int], Optional[str]]:
        """Resolve a name across items, weapons, armor, accessories."""
        _load_ff7_gear_layout_assets()
        clean = _sanitize_enemy_token(str(item_name or "")).strip().lower()
        if not clean:
            return None, "empty item name"
        for sid, nm in _ITEM_NAMES_EN.items():
            if nm and nm.strip().lower() == clean:
                iid = int(sid)
                if 0 <= iid < ITEM_ID_WEAPON_BASE:
                    return iid, None
        return self._gear_name_to_item_id(item_name)

    def _materia_name_to_id(
        self, materia_name: str
    ) -> Tuple[Optional[int], Optional[str]]:
        _load_ff7_gear_layout_assets()
        clean = _sanitize_enemy_token(str(materia_name or "")).strip().lower()
        if not clean:
            return None, "empty materia name"
        for sid, nm in _MATERIA_NAMES_EN.items():
            if nm and nm.strip().lower() == clean:
                return int(sid), None
        return None, f"unknown materia: {materia_name}"

    def _resolve_gear_token(self, kind: str, token: str) -> Tuple[Optional[int], Optional[str]]:
        tl = (token or "").strip().lower()
        if tl in (f"random:{kind}", "random") or tl == f"random_{kind}":
            pool: List[int] = []
            _load_ff7_gear_layout_assets()
            if kind == "weapon":
                pool = [int(k) for k in _WEAPON_NAMES_EN if _WEAPON_NAMES_EN[k]]
            elif kind == "armor":
                pool = [int(k) for k in _ARMOR_NAMES_EN if _ARMOR_NAMES_EN[k]]
            else:
                pool = [int(k) for k in _ACCESSORY_NAMES_EN if _ACCESSORY_NAMES_EN[k]]
            if not pool:
                return None, "no gear pool"
            return random.choice(pool), None
        return self._gear_name_to_id(kind, token)

    def _enemy_slot_from_token(self, token: str) -> Tuple[Optional[int], Optional[str]]:
        t = _sanitize_enemy_token(token or "")
        if not t:
            return None, "empty enemy token"
        tl = t.lower()
        if tl in ("__random_enemy__", "random_enemy"):
            snap = self._last_snapshot or {}
            en = snap.get("enemies") or []
            if not en:
                return None, "no enemies for random"
            row = random.choice(en)
            sl = int(row.get("slot", -1))
            if sl < 4 or sl > 9:
                return None, "bad enemy slot"
            return sl, None
        parts = t.rsplit(None, 1)
        if (
            len(parts) == 2
            and len(parts[1]) == 1
            and parts[1].isalpha()
            and parts[0].strip()
        ):
            base_raw, letter = parts[0], parts[1]
            idx = ord(letter.upper()) - ord("A")
            want_base = _norm_party_name(base_raw)
            snap = self._last_snapshot or {}
            pool = [
                row
                for row in (snap.get("enemies") or [])
                if _norm_party_name(str(row.get("name", ""))) == want_base
            ]
            pool.sort(
                key=lambda r: (int(r.get("scene_id", 0)), int(r.get("slot", 0)))
            )
            if not pool:
                return None, f"enemy not found: {t}"
            if idx < 0 or idx >= len(pool):
                return None, f"enemy suffix out of range: {t}"
            sl = int(pool[idx].get("slot", -1))
            if sl < 4 or sl > 9:
                return None, "bad enemy slot"
            return sl, None
        if t.isdigit():
            ei = int(t)
            if 0 <= ei <= 5:
                return 4 + ei, None
            return None, "enemy index must be 0–5"
        want = _norm_party_name(t)
        snap = self._last_snapshot or {}
        for row in snap.get("enemies") or []:
            nm = _norm_party_name(str(row.get("name", "")))
            if want and want in nm:
                return int(row["slot"]), None
        return None, f"enemy not found: {t}"

    def _op_rename_character(self, current_name: str, new_name: str) -> Tuple[bool, Optional[str]]:
        data, _ = self._read_savemap()
        if not data:
            return False, "Savemap not readable"
        cid = self._char_id_from_savemap_name(data, current_name)
        if cid is None:
            return False, "Character not found"
        off = _CHAR_BLOCK[cid]
        addr = _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE) + off + REC_OFF_NAME
        enc = _encode_ff7_name(new_name)
        if not self._write(addr, enc):
            return False, "Write name failed"
        return True, None

    def _op_set_character_gear(
        self, character: str, gear_kind: str, gear_id: int
    ) -> Tuple[bool, Optional[str]]:
        data, _ = self._read_savemap()
        if not data:
            return False, "Savemap not readable"
        cid, err = self._char_id_from_gear_character_token(data, character)
        if err or cid is None:
            return False, err or "character"
        if cid >= len(_CHAR_BLOCK):
            return False, "bad character id"
        gk = gear_kind.strip().lower()
        allow = _allowed_ids_for_char_gear(int(cid), gk)
        if allow is not None and int(gear_id) not in allow:
            return False, "gear not allowed for this character (equip_allowlists.json)"
        off = _CHAR_BLOCK[cid]
        kind = gk
        if kind == "weapon":
            o = REC_OFF_WEAPON
            if gear_id < 0 or gear_id > 127:
                return False, "weapon id out of range"
        elif kind == "armor":
            o = REC_OFF_ARMOR
            if gear_id < 0 or gear_id > 31:
                return False, "armor id out of range"
        elif kind == "accessory":
            o = REC_OFF_ACCESSORY
            if gear_id < 0 or gear_id > 31:
                return False, "accessory id out of range"
            b = gear_id if gear_id >= 0 else 0xFF
            addr = _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE) + off + o
            if not self._write(addr, bytes([b])):
                return False, "write failed"
            self._call_ff7_party_stat_recalc()
            return True, None
        else:
            return False, "gear_kind must be weapon|armor|accessory"
        addr = _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE) + off + o
        if not self._write(addr, bytes([int(gear_id) & 0xFF])):
            return False, "write failed"
        self._call_ff7_party_stat_recalc()
        return True, None

    # ------------------------------------------------------------------
    # Inventory helpers
    # ------------------------------------------------------------------
    def _battle_ally_slot_for_cid(self, cid: int) -> Optional[int]:
        """Return the 0..2 battle-ally slot currently holding character ``cid``."""
        if not self._proc:
            return None
        base = _rebase(self._proc.module_base, ADDR_PARTY_MEMBER_IDS)
        for s in range(3):
            b = self._read_u8(base + s)
            if b is not None and int(b) == int(cid):
                return s
        return None

    def _inv_items_addr(self) -> int:
        assert self._proc
        return _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE) + SAVE_OFF_INV_ITEMS

    def _inv_materia_addr(self) -> int:
        assert self._proc
        return _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE) + SAVE_OFF_INV_MATERIA

    def _inv_read_item_slot(self, idx: int) -> Optional[Tuple[int, int]]:
        """Return (item_id, quantity) at slot ``idx``; None if empty.

        Record is 2 bytes: ``item_id = bits 0..8``, ``quantity = bits 9..15``.
        Empty slot is ``0xFFFF``.
        """
        if idx < 0 or idx >= INV_ITEM_SLOTS:
            return None
        raw = self._read(self._inv_items_addr() + idx * 2, 2)
        if not raw or len(raw) < 2:
            return None
        val = struct.unpack("<H", raw)[0]
        if val == 0xFFFF:
            return None
        return (val & 0x1FF, (val >> 9) & 0x7F)

    def _inv_write_item_slot(self, idx: int, item_id: Optional[int], qty: int) -> bool:
        if idx < 0 or idx >= INV_ITEM_SLOTS:
            return False
        if item_id is None or qty <= 0:
            val = 0xFFFF
        else:
            val = (int(item_id) & 0x1FF) | ((int(qty) & 0x7F) << 9)
        return self._write(
            self._inv_items_addr() + idx * 2, struct.pack("<H", val)
        )

    def _inv_find_item_slot(self, item_id: int) -> Optional[int]:
        for i in range(INV_ITEM_SLOTS):
            rec = self._inv_read_item_slot(i)
            if rec and rec[0] == item_id:
                return i
        return None

    def _inv_first_empty_item_slot(self) -> Optional[int]:
        for i in range(INV_ITEM_SLOTS):
            if self._inv_read_item_slot(i) is None:
                return i
        return None

    def _inv_add_item(self, item_id: int, qty: int = 1) -> bool:
        qty = max(1, min(MAX_ITEM_QUANTITY, int(qty)))
        existing = self._inv_find_item_slot(int(item_id))
        if existing is not None:
            rec = self._inv_read_item_slot(existing)
            if rec is None:
                return False
            new_qty = min(MAX_ITEM_QUANTITY, rec[1] + qty)
            return self._inv_write_item_slot(existing, item_id, new_qty)
        empty = self._inv_first_empty_item_slot()
        if empty is None:
            return False
        return self._inv_write_item_slot(empty, item_id, qty)

    def _inv_remove_item(self, item_id: int, qty: int = 1) -> bool:
        slot = self._inv_find_item_slot(int(item_id))
        if slot is None:
            return False
        rec = self._inv_read_item_slot(slot)
        if rec is None:
            return False
        remaining = rec[1] - int(qty)
        if remaining <= 0:
            return self._inv_write_item_slot(slot, None, 0)
        return self._inv_write_item_slot(slot, item_id, remaining)

    def _inv_read_materia_slot(
        self, idx: int
    ) -> Optional[Tuple[int, int]]:
        """Return (materia_id, ap) for inventory slot ``idx``; None if empty."""
        if idx < 0 or idx >= INV_MATERIA_SLOTS:
            return None
        raw = self._read(self._inv_materia_addr() + idx * 4, 4)
        if not raw or len(raw) < 4:
            return None
        mid = raw[0]
        if mid == 0xFF:
            return None
        ap = raw[1] | (raw[2] << 8) | (raw[3] << 16)
        return (int(mid), int(ap))

    def _inv_write_materia_slot(
        self, idx: int, mid: Optional[int], ap: int
    ) -> bool:
        if idx < 0 or idx >= INV_MATERIA_SLOTS:
            return False
        if mid is None:
            data = bytes([0xFF, 0xFF, 0xFF, 0xFF])
        else:
            a = int(ap) & 0xFFFFFF
            data = bytes(
                [int(mid) & 0xFF, a & 0xFF, (a >> 8) & 0xFF, (a >> 16) & 0xFF]
            )
        return self._write(self._inv_materia_addr() + idx * 4, data)

    def _inv_first_empty_materia_slot(self) -> Optional[int]:
        for i in range(INV_MATERIA_SLOTS):
            if self._inv_read_materia_slot(i) is None:
                return i
        return None

    def _inv_add_materia(self, mid: int, ap: int = 0) -> bool:
        empty = self._inv_first_empty_materia_slot()
        if empty is None:
            return False
        return self._inv_write_materia_slot(empty, int(mid), int(ap))

    def _inv_find_materia_slot(
        self, mid: int, ap: Optional[int] = None
    ) -> Optional[int]:
        """Find the first inventory slot with matching materia id (and optional exact AP)."""
        for i in range(INV_MATERIA_SLOTS):
            rec = self._inv_read_materia_slot(i)
            if rec is None:
                continue
            if rec[0] == int(mid) and (ap is None or rec[1] == int(ap)):
                return i
        return None

    def _inv_remove_materia(
        self, mid: int, ap: Optional[int] = None
    ) -> Tuple[bool, int]:
        """Remove one materia of ``mid`` (optionally matching ``ap``).

        Returns ``(ok, ap_removed)`` — ``ap_removed`` is the AP of the slot
        that was consumed (useful when the caller wants to preserve it).
        """
        slot = self._inv_find_materia_slot(mid, ap)
        if slot is None:
            return False, 0
        rec = self._inv_read_materia_slot(slot) or (0, 0)
        if not self._inv_write_materia_slot(slot, None, 0):
            return False, 0
        return True, rec[1]

    def _op_set_battle_status(
        self, party_slot: int, mask: int, mode: str
    ) -> Tuple[bool, Optional[str]]:
        if self._current_module_byte() != 2:
            return False, "Not in battle"
        if party_slot < 0 or party_slot > 2:
            return False, "bad party slot"
        actor = self._battle_actor_addr(party_slot)
        cur = self._read_u32(actor + BATTLE_OFF_STATUS)
        if cur is None:
            return False, "read status failed"
        m = int(mask) & 0xFFFFFFFF
        md = (mode or "on").strip().lower()
        if md == "toggle":
            new_st = cur ^ m
        elif md in ("off", "clear"):
            new_st = cur & (~m & 0xFFFFFFFF)
        else:
            new_st = cur | m
        if not self._write(actor + BATTLE_OFF_STATUS, struct.pack("<I", new_st)):
            return False, "write status failed"
        return True, None

    def _op_set_field_status_byte(
        self, party_slot: int, value: int
    ) -> Tuple[bool, Optional[str]]:
        data, _ = self._read_savemap()
        if not data:
            return False, "Savemap not readable"
        if party_slot < 0 or party_slot > 2:
            return False, "bad party slot"
        cid = data[SAVE_OFF_PARTY_SLOTS + party_slot]
        if cid >= len(_CHAR_BLOCK) or cid == 0xFF:
            return False, "empty slot"
        off = _CHAR_BLOCK[cid]
        addr = _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE) + off + REC_OFF_FIELD_STATUS
        b = max(0, min(255, int(value)))
        if not self._write(addr, bytes([b])):
            return False, "write failed"
        return True, None

    def _op_set_menu_u16(
        self, canon_addr: int, menu_name: str, bit_on: bool, xor_mode: bool
    ) -> Tuple[bool, Optional[str]]:
        bi, err = self._menu_bit_index(menu_name)
        if err or bi is None:
            return False, err or "menu"
        addr = _rebase(self._proc.module_base, canon_addr)
        cur = self._read_u16(addr)
        if cur is None:
            return False, "read menu word failed"
        bit = 1 << bi
        if xor_mode:
            new_v = cur ^ bit
        elif bit_on:
            new_v = cur | bit
        else:
            new_v = cur & (~bit & 0xFFFF)
        if not self._write(addr, struct.pack("<H", new_v & 0xFFFF)):
            return False, "write menu word failed"
        return True, None

    def _op_field_menu_access(self, enabled: bool) -> Tuple[bool, Optional[str]]:
        addr = _rebase(self._proc.module_base, ADDR_FIELD_MENU_ACCESS_ENABLED)
        b = 0 if enabled else 1
        if not self._write(addr, bytes([b & 0xFF])):
            return False, "write field menu access failed"
        return True, None

    def _op_world_speed_multiplier(self, mult: int) -> Tuple[bool, Optional[str]]:
        addr = _rebase(self._proc.module_base, ADDR_WORLD_SPEED_MULTIPLIER)
        m = max(1, min(255, int(mult)))
        if not self._write(addr, bytes([m])):
            return False, "write world speed failed"
        return True, None

    def _op_set_game_speed(self, speed: float, duration_sec: int) -> Tuple[bool, Optional[str]]:
        assert self._proc
        sp = max(0.25, min(8.0, float(speed)))
        mb = self._proc.module_base
        chk = _rebase(mb, ADDR_FFNX_TRAMPOLINE_CHECK)
        b0 = self._read_u8(chk)
        backup: Dict[str, Any] = {
            "speed": sp,
            "duration_sec": int(duration_sec),
        }

        if b0 == 0xE9:
            pair = self._ffnx_trampoline_targets_fps_addrs(chk)
            if not pair:
                return (
                    False,
                    "FFNx hook present (0xE9 at ffnx_check) but movsd FPS pattern not found",
                )
            a30, a15 = pair
            looks_literal = self._ffnx_fps_pair_plausible(a30, a15)
            if not looks_literal:
                w = (
                    "resolved floats look like vanilla tick storage — "
                    "writing FFNx-style anyway"
                )
                logger.warning("set_game_speed: %s", w)
            f30 = self._read_f64(a30)
            f15 = self._read_f64(a15)
            t30, t15 = ffnx_fps_literal_f64(sp)
            p30 = struct.pack("<d", float(t30)).hex()
            p15 = struct.pack("<d", float(t15)).hex()
            backup["ffnx"] = {"a30": a30, "a15": a15, "f30": f30, "f15": f15}
            backup["mode"] = "ffnx"
            ok30 = self._write_double_robust(a30, t30)
            af30 = self._read_f64(a30) if ok30 else None
            if not ok30:
                return False, "write FFNx field/world fps float failed"
            ok15 = self._write_double_robust(a15, t15)
            af15 = self._read_f64(a15) if ok15 else None
            if not ok15:
                return False, "write FFNx battle fps float failed"
            logger.debug(
                "set_game_speed: FFNx path a30=%s a15=%s sp=%s",
                hex(a30),
                hex(a15),
                sp,
            )
        else:
            fps_specs = [
                (ADDR_FIELD_FPS, 30.0),
                (ADDR_BATTLE_FPS, 15.0),
                (ADDR_WORLD_FPS, 30.0),
            ]
            fps_backup: List[Tuple[int, Optional[float]]] = []
            for canon, default_fps in fps_specs:
                addr = _rebase(self._proc.module_base, canon)
                prev = self._read_f64(addr)
                fps_backup.append((addr, prev))
                new_f = vanilla_tick_interval_f64(default_fps, sp)
                ph = struct.pack("<d", float(new_f)).hex()
                if not self._write_double_robust(addr, new_f):
                    return False, f"write vanilla FPS failed (0x{canon:08X})"
            nop_canons = (
                ADDR_FPS_NOP_INIT_1,
                ADDR_FPS_NOP_INIT_2,
                ADDR_FPS_NOP_INIT_3,
            )
            nop_backup: List[Tuple[int, bytes]] = []
            nop_patch = b"\x90" * 6
            for canon in nop_canons:
                addr = _rebase(self._proc.module_base, canon)
                orig = self._read(addr, 6)
                if orig is None or len(orig) != 6:
                    return False, f"read FPS NOP site failed (0x{canon:08X})"
                nop_backup.append((addr, orig))
                ok_nop = self._win_protect_write(addr, nop_patch, flush_icache=True)
                if not ok_nop:
                    return False, f"write FPS NOP failed (0x{canon:08X})"
            backup["vanilla"] = {"fps": fps_backup, "nops": nop_backup}
            backup["mode"] = "vanilla"
            logger.debug(
                "set_game_speed: vanilla path field/battle/world FPS + NOPs sp=%s",
                sp,
            )
        # set_game_speed: field/battle/world FPS + NOPs only (no world_speed_multiplier).
        self._speed_backup = backup
        if int(duration_sec) > 0:
            self._post_success_timed.append(
                ("restore_game_speed", {}, int(duration_sec))
            )
        return True, None

    def _op_restore_game_speed(self) -> Tuple[bool, Optional[str]]:
        b = self._speed_backup
        if not b or not self._proc:
            return False, "no speed backup"
        mode = b.get("mode")
        if mode == "ffnx" or "ffnx" in b:
            e = b.get("ffnx") or {}
            if e.get("f30") is not None and e.get("a30") is not None:
                self._write_double_robust(int(e["a30"]), float(e["f30"]))
            if e.get("f15") is not None and e.get("a15") is not None:
                self._write_double_robust(int(e["a15"]), float(e["f15"]))
        elif mode == "vanilla" or "vanilla" in b:
            van = b.get("vanilla") or {}
            for addr, prev in van.get("fps") or []:
                if prev is not None:
                    self._write_double_robust(int(addr), float(prev))
            for addr, orig in van.get("nops") or []:
                if orig is not None:
                    self._win_protect_write(int(addr), bytes(orig), flush_icache=True)
        self._speed_backup = None
        return True, None

    def _op_restore_menu_words(self, kwargs: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        if not self._proc:
            return False, "Not attached"
        v = kwargs.get("visibility_u16")
        lk = kwargs.get("locks_u16")
        if v is not None:
            addr = _rebase(self._proc.module_base, ADDR_MENU_VISIBILITY)
            if not self._write(addr, struct.pack("<H", int(v) & 0xFFFF)):
                return False, "restore menu visibility failed"
        if lk is not None:
            addr = _rebase(self._proc.module_base, ADDR_MENU_LOCKS)
            if not self._write(addr, struct.pack("<H", int(lk) & 0xFFFF)):
                return False, "restore menu locks failed"
        return True, None

    def _op_set_battle_speed_byte(
        self, speed_byte: int, duration_sec: int
    ) -> Tuple[bool, Optional[str]]:
        data, _ = self._read_savemap()
        if not data or len(data) <= SAVE_OFF_BATTLE_SPEED:
            return False, "Savemap not readable"
        prev = int(data[SAVE_OFF_BATTLE_SPEED])
        b = max(0, min(255, int(speed_byte)))
        addr = _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE) + SAVE_OFF_BATTLE_SPEED
        if not self._write(addr, bytes([b])):
            return False, "write battle speed failed"
        self._battle_speed_restore_byte = prev
        if int(duration_sec) > 0:
            self._post_success_timed.append(
                (
                    "restore_battle_speed",
                    {"prev_byte": int(prev)},
                    int(duration_sec),
                )
            )
        return True, None

    def _op_restore_battle_speed(self, prev_byte: int) -> Tuple[bool, Optional[str]]:
        data, _ = self._read_savemap()
        if not data or len(data) <= SAVE_OFF_BATTLE_SPEED:
            return False, "Savemap not readable"
        addr = _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE) + SAVE_OFF_BATTLE_SPEED
        b = max(0, min(255, int(prev_byte)))
        if not self._write(addr, bytes([b])):
            return False, "restore battle speed failed"
        self._battle_speed_restore_byte = None
        return True, None

    def _op_set_battle_atb_mode(
        self, mode: str, duration_sec: int
    ) -> Tuple[bool, Optional[str]]:
        data, _ = self._read_savemap()
        if not data or len(data) <= SAVE_OFF_GENERAL_CONFIG:
            return False, "Savemap not readable"
        prev = int(data[SAVE_OFF_GENERAL_CONFIG])
        new_bits = _encode_savemap_atb_mode(mode)
        merged = (prev & ~SAVEMAP_ATB_MASK) | (new_bits & SAVEMAP_ATB_MASK)
        addr = _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE) + SAVE_OFF_GENERAL_CONFIG
        if not self._write(addr, bytes([merged & 0xFF])):
            return False, "write ATB config failed"
        # Do not patch ADDR_BATTLE_MODE here: that byte packs more than ATB style;
        # partial writes have been observed to end battles incorrectly. Savemap 0x10DA
        # is the supported source for config; the game applies it when appropriate.
        if int(duration_sec) > 0:
            self._post_success_timed.append(
                (
                    "restore_battle_atb_mode",
                    {"prev_config_byte": int(prev)},
                    int(duration_sec),
                )
            )
        return True, None

    def _op_restore_battle_atb_mode(self, prev_config_byte: int) -> Tuple[bool, Optional[str]]:
        data, _ = self._read_savemap()
        if not data or len(data) <= SAVE_OFF_GENERAL_CONFIG:
            return False, "Savemap not readable"
        addr = _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE) + SAVE_OFF_GENERAL_CONFIG
        if not self._write(addr, bytes([int(prev_config_byte) & 0xFF])):
            return False, "restore ATB config failed"
        return True, None

    def _op_set_infinite_items(self, enable: bool, duration_sec: int) -> Tuple[bool, Optional[str]]:
        if not self._proc:
            return False, "Not attached"
        base = _rebase(self._proc.module_base, ADDR_BATTLE_PARTY_ITEMS)
        n = BATTLE_PARTY_ITEMS_BLOB_SIZE
        if enable:
            raw = self._read(base, n)
            if raw is None or len(raw) < n:
                return False, "read battle items failed"
            self._infinite_items_backup = bytes(raw)
            self._infinite_items_backup_len = n
            self._infinite_items_active = True
            if int(duration_sec) > 0:
                self._post_success_timed.append(
                    ("restore_infinite_items", {}, int(duration_sec))
                )
            return True, None
        self._infinite_items_active = False
        if self._infinite_items_backup and self._infinite_items_backup_len > 0:
            blob = self._infinite_items_backup[: self._infinite_items_backup_len]
            if not self._write(base, blob):
                return False, "restore battle items blob failed"
        self._infinite_items_backup = None
        self._infinite_items_backup_len = 0
        return True, None

    def _op_restore_infinite_items(self) -> Tuple[bool, Optional[str]]:
        return self._op_set_infinite_items(False, 0)

    def _maybe_top_up_battle_party_items(self) -> None:
        if not self._infinite_items_active or not self._proc:
            return
        if self._current_module_byte() != 2:
            return
        base = _rebase(self._proc.module_base, ADDR_BATTLE_PARTY_ITEMS)
        for i in range(BATTLE_PARTY_ITEMS_NUM_SLOTS):
            o = i * BATTLE_PARTY_ITEMS_SLOT_STRIDE
            raw = self._read(base + o, 4)
            if not raw or len(raw) < 4:
                break
            iid = int(struct.unpack_from("<H", raw, 0)[0]) & 0xFFFF
            qty = int(raw[2]) & 0xFF
            if iid != 0xFFFF and 0 < iid and 0 < qty < 99:
                if not self._write(base + o + 2, bytes([99])):
                    break

    def _tick_recent_item_detection(self, savemap: Optional[bytes], gil: int) -> None:
        _load_ff7_gear_layout_assets()
        if not savemap or len(savemap) < SAVE_OFF_INV_ITEMS + INV_ITEM_SLOTS * 2:
            return
        inv_slice = bytes(
            savemap[SAVE_OFF_INV_ITEMS : SAVE_OFF_INV_ITEMS + INV_ITEM_SLOTS * 2]
        )
        prev = self._prev_inventory_sig
        prev_gil = self._prev_gil_for_items
        if (
            prev is not None
            and len(prev) == len(inv_slice)
            and prev_gil is not None
            and gil >= prev_gil
        ):
            for idx in range(INV_ITEM_SLOTS):
                o = idx * 2
                cur = struct.unpack_from("<H", inv_slice, o)[0]
                pr = struct.unpack_from("<H", prev, o)[0]
                if cur == 0xFFFF and pr == 0xFFFF:
                    continue
                if cur == 0xFFFF:
                    continue
                ci, cq = cur & 0x1FF, (cur >> 9) & 0x7F
                if pr == 0xFFFF and cq > 0:
                    self._last_item_gain = {
                        "name": _ff7_unified_item_name(int(ci)),
                        "delta": int(cq),
                        "item_id": int(ci),
                    }
                    break
                if pr != 0xFFFF:
                    pi, pq = pr & 0x1FF, (pr >> 9) & 0x7F
                    if ci == pi and cq > pq:
                        self._last_item_gain = {
                            "name": _ff7_unified_item_name(int(ci)),
                            "delta": int(cq - pq),
                            "item_id": int(ci),
                        }
                        break
        self._prev_inventory_sig = inv_slice
        self._prev_gil_for_items = int(gil)

    def _tick_battle_status_log(
        self, party: List[Dict[str, Any]], enemies: List[Dict[str, Any]]
    ) -> None:
        for row in party:
            if row.get("slot_empty"):
                continue
            ps = int(row.get("party_slot", row.get("slot", -1)))
            st = int(row.get("status", 0) or 0)
            key = f"a{ps}"
            prev = self._prev_battle_status.get(key)
            if prev is not None and int(prev) != st:
                nm = (str(row.get("name") or "?")).strip() or "?"
                for mask, sname in _STATUS_AILMENT_BITS:
                    had = (int(prev) & int(mask)) != 0
                    has = (st & int(mask)) != 0
                    if had == has:
                        continue
                    if has and not had:
                        if sname == "Death":
                            self._battle_log_lines.append(f"{nm} died")
                        else:
                            self._battle_log_lines.append(f"{nm} — inflicted with {sname}")
                    elif had and not has:
                        self._battle_log_lines.append(f"{nm} — cleared of {sname}")
            self._prev_battle_status[key] = st
        for row in enemies:
            sl = int(row.get("slot", -1))
            st = int(row.get("status", 0) or 0)
            key = f"e{sl}"
            prev = self._prev_battle_status.get(key)
            if prev is not None and int(prev) != st:
                nm = (str(row.get("name") or "?")).strip() or "?"
                for mask, sname in _STATUS_AILMENT_BITS:
                    had = (int(prev) & int(mask)) != 0
                    has = (st & int(mask)) != 0
                    if had == has:
                        continue
                    if has and not had:
                        if sname == "Death":
                            self._battle_log_lines.append(f"{nm} died")
                        else:
                            self._battle_log_lines.append(f"{nm} — inflicted with {sname}")
                    elif had and not has:
                        self._battle_log_lines.append(f"{nm} — cleared of {sname}")
            self._prev_battle_status[key] = st
        if len(self._battle_log_lines) > 200:
            self._battle_log_lines = self._battle_log_lines[-200:]

    def _tick_battle_log(
        self,
        battle: bool,
        party: List[Dict[str, Any]],
        enemies: List[Dict[str, Any]],
        battle_ui: bool,
    ) -> None:
        if battle and not self._was_in_battle:
            self._battle_log_lines.clear()
            self._battle_log_seen.clear()
            self._prev_battle_status.clear()
        if not battle:
            if not battle_ui:
                self._prev_battle_status.clear()
                self._was_in_battle = False
            return
        if not self._proc:
            return
        self._ensure_battle_log_names()
        bq = self._read(
            _rebase(self._proc.module_base, ADDR_BATTLE_QUEUE), 8
        )
        if bq and len(bq) == 8 and not all(b == 0 for b in bq):
            priority = int(bq[0])
            qpos = int(bq[1])
            att = int(bq[2])
            cmd_id = int(bq[3]) & 0xFF
            param = int(struct.unpack_from("<H", bq, 4)[0]) & 0xFFFF
            tmask = int(struct.unpack_from("<H", bq, 6)[0]) & 0xFFFF
            if cmd_id != 0xFF and (priority, qpos) not in self._battle_log_seen:
                self._battle_log_seen.add((priority, qpos))
                dam, miss, crit = 0, False, False
                pptr = _rebase(self._proc.module_base, ADDR_BATTLE_OBJ_PTR)
                bop = self._read_u32(pptr) or 0
                if 0x00010000 < bop < 0x7FFF0000:
                    blob = self._read(bop, 0x264)
                    if blob and len(blob) > 0x220:
                        dam = int(struct.unpack_from("<i", blob, 0x214)[0])
                        miss = bool(blob[0x218] & 1)
                        crit = bool(blob[0x220] & 2)
                line = self._format_battle_log_line(
                    party, enemies, att, cmd_id, param, tmask, dam, miss, crit
                )
                if line:
                    self._battle_log_lines.append(line)
        self._tick_battle_status_log(party, enemies)
        if len(self._battle_log_lines) > 200:
            self._battle_log_lines = self._battle_log_lines[-200:]
        self._was_in_battle = True

    def _op_add_gil(self, amount: int) -> Tuple[bool, Optional[str]]:
        if amount <= 0:
            return False, "amount must be positive"
        data, _ = self._read_savemap()
        if not data or len(data) < SAVE_OFF_GIL + 4:
            return False, "Savemap not readable"
        gil = struct.unpack_from("<I", data, SAVE_OFF_GIL)[0]
        new_gil = min(int(gil) + int(amount), _MAX_GIL)
        addr = _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE) + SAVE_OFF_GIL
        if not self._write(addr, struct.pack("<I", new_gil)):
            return False, "WriteProcessMemory failed (gil)"
        return True, None

    def _op_remove_gil(self, amount: int) -> Tuple[bool, Optional[str]]:
        if amount <= 0:
            return False, "amount must be positive"
        data, _ = self._read_savemap()
        if not data or len(data) < SAVE_OFF_GIL + 4:
            return False, "Savemap not readable"
        gil = struct.unpack_from("<I", data, SAVE_OFF_GIL)[0]
        new_gil = max(0, int(gil) - int(amount))
        addr = _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE) + SAVE_OFF_GIL
        if not self._write(addr, struct.pack("<I", new_gil)):
            return False, "WriteProcessMemory failed (gil)"
        return True, None

    def _op_battle_party_hp_write(
        self, slot: int, new_hp: int
    ) -> Tuple[bool, Optional[str]]:
        cur = self._current_module_byte()
        if cur != 2:
            return False, "Not in battle (party HP live write requires battle)"
        actor = self._battle_actor_addr(slot)
        hp_addr = actor + BATTLE_OFF_HP
        mx = self._read_i32(actor + BATTLE_OFF_MAX_HP)
        if mx is None:
            return False, "Read max HP failed"
        clamped = max(0, min(int(new_hp), int(mx)))
        if clamped > 0:
            st_raw = self._read_i32(actor + BATTLE_OFF_STATUS)
            if st_raw is None:
                return False, "Read battle status failed"
            st_u32 = st_raw & 0xFFFFFFFF
            cleared = st_u32 & ~STATUS_DEAD
            if cleared != st_u32 and not self._write(
                actor + BATTLE_OFF_STATUS, struct.pack("<I", cleared)
            ):
                return False, "WriteProcessMemory failed (battle status)"
        if not self._write(hp_addr, struct.pack("<i", clamped)):
            return False, "WriteProcessMemory failed (battle HP)"
        return True, None

    def _op_field_party_hp_write(
        self, savemap: bytes, slot: int, new_hp: int
    ) -> Tuple[bool, Optional[str]]:
        mx = self._field_char_max_hp(savemap, slot)
        if mx is None:
            return False, "Invalid party slot or empty slot (field)"
        addr = self._field_char_hp_addr(savemap, slot)
        if addr is None:
            return False, "Could not resolve field HP address"
        clamped = max(0, min(int(new_hp), int(mx)))
        if not self._write(addr, struct.pack("<H", clamped)):
            return False, "WriteProcessMemory failed (field HP)"
        return True, None

    def _party_hp_rw(
        self, slot: int, mode: str, amount_or_value: int
    ) -> Tuple[bool, Optional[str]]:
        if slot < 0 or slot > 2:
            return False, "slot must be 0–2"
        cur = self._current_module_byte()
        if cur == 2:
            actor = self._battle_actor_addr(slot)
            hp = self._read_i32(actor + BATTLE_OFF_HP)
            mx = self._read_i32(actor + BATTLE_OFF_MAX_HP)
            if hp is None or mx is None:
                return False, "Read battle HP failed"
            if mode == "add":
                new_hp = min(int(mx), int(hp) + int(amount_or_value))
            elif mode == "remove":
                new_hp = max(0, int(hp) - int(amount_or_value))
            else:
                new_hp = max(0, min(int(mx), int(amount_or_value)))
            return self._op_battle_party_hp_write(slot, new_hp)
        data, _ = self._read_savemap()
        if not data:
            return False, "Savemap not readable"
        cur_u16 = self._field_char_hp_u16(data, slot)
        if cur_u16 is None:
            return False, "Read field HP failed"
        mx = self._field_char_max_hp(data, slot)
        if mx is None:
            return False, "Field max HP unavailable"
        if mode == "add":
            new_hp = min(int(mx), int(cur_u16) + int(amount_or_value))
        elif mode == "remove":
            new_hp = max(0, int(cur_u16) - int(amount_or_value))
        else:
            new_hp = max(0, min(int(mx), int(amount_or_value)))
        return self._op_field_party_hp_write(data, slot, new_hp)

    def _op_kill_party_member(self, slot: int) -> Tuple[bool, Optional[str]]:
        if self._current_module_byte() != 2:
            return False, "KO party member is only supported in battle"
        actor = self._battle_actor_addr(slot)
        if not self._write(
            actor + BATTLE_OFF_STATUS, struct.pack("<I", STATUS_DEAD)
        ):
            return False, "WriteProcessMemory failed (battle status Dead)"
        return self._op_battle_party_hp_write(slot, 0)

    def _op_kill_all_enemies(self) -> Tuple[bool, Optional[str]]:
        if self._current_module_byte() != 2:
            return False, "Not in battle"
        for slot in range(4, 10):
            e = self._read_battle_enemy(slot)
            if not e:
                continue
            actor = self._battle_actor_addr(slot)
            if not self._write(
                actor + BATTLE_OFF_STATUS, struct.pack("<I", STATUS_DEAD)
            ):
                return False, f"Write failed enemy status slot {slot}"
            if not self._write(actor + BATTLE_OFF_HP, struct.pack("<i", 0)):
                return False, f"Write failed enemy HP slot {slot}"
        return True, None

    def _op_damage_enemy(self, battle_slot: int, amount: int) -> Tuple[bool, Optional[str]]:
        if amount <= 0:
            return False, "amount must be positive"
        if self._current_module_byte() != 2:
            return False, "Not in battle"
        slot = int(battle_slot)
        if slot < 4 or slot > 9:
            return False, "enemy battle slot must be 4–9"
        e = self._read_battle_enemy(slot)
        if not e:
            return False, "No enemy in that slot"
        hp = self._read_i32(self._battle_actor_addr(slot) + BATTLE_OFF_HP)
        if hp is None:
            return False, "Read enemy HP failed"
        new_hp = max(0, int(hp) - int(amount))
        actor = self._battle_actor_addr(slot)
        if new_hp == 0 and not self._write(
            actor + BATTLE_OFF_STATUS, struct.pack("<I", STATUS_DEAD)
        ):
            return False, "Write enemy Death status failed"
        if not self._write(actor + BATTLE_OFF_HP, struct.pack("<i", new_hp)):
            return False, "Write enemy HP failed"
        return True, None

    def _op_kill_enemy(self, battle_slot: int) -> Tuple[bool, Optional[str]]:
        if self._current_module_byte() != 2:
            return False, "Not in battle"
        slot = int(battle_slot)
        if slot < 4 or slot > 9:
            return False, "enemy battle slot must be 4–9"
        e = self._read_battle_enemy(slot)
        if not e:
            return False, "No enemy in that slot"
        actor = self._battle_actor_addr(slot)
        if not self._write(actor + BATTLE_OFF_STATUS, struct.pack("<I", STATUS_DEAD)):
            return False, "Write enemy Death status failed"
        if not self._write(actor + BATTLE_OFF_HP, struct.pack("<i", 0)):
            return False, "Write enemy HP failed"
        return True, None

    # ------------------------------------------------------------------
    # Level / stat edits (party + enemy)
    # ------------------------------------------------------------------
    def _op_set_party_level(
        self, character: str, level: int
    ) -> Tuple[bool, Optional[str]]:
        data, _ = self._read_savemap()
        if not data:
            return False, "Savemap not readable"
        cid, err = self._char_id_from_gear_character_token(data, character)
        if err or cid is None:
            return False, err or "character"
        if cid >= len(_CHAR_BLOCK):
            return False, "bad character id"
        lvl = max(1, min(99, int(level)))
        base = _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE) + _CHAR_BLOCK[cid]
        if not self._write(base + REC_OFF_LEVEL, bytes([lvl])):
            return False, "write level failed"
        if self._current_module_byte() == 2:
            slot = self._battle_ally_slot_for_cid(int(cid))
            if slot is not None:
                self._write(
                    self._battle_actor_addr(slot) + BATTLE_OFF_LEVEL,
                    bytes([lvl]),
                )
        self._call_ff7_party_stat_recalc()
        return True, None

    def _op_set_enemy_level(
        self, enemy: str, level: int
    ) -> Tuple[bool, Optional[str]]:
        sl, err = self._enemy_slot_from_token(enemy)
        if err or sl is None:
            return False, err or "enemy"
        snap = self._last_snapshot or {}
        scene_id: Optional[int] = None
        for row in snap.get("enemies") or []:
            if int(row.get("slot", -1)) == int(sl):
                scene_id = int(row.get("scene_id", -1))
                break
        if scene_id is None or scene_id < 0:
            return False, "no scene_id for enemy"
        lvl = max(1, min(99, int(level)))
        base = (
            _rebase(self._proc.module_base, ADDR_ENEMY_DATA_BASE)
            + scene_id * ENEMY_DATA_STRIDE
        )
        if not self._write(base + ENEMY_OFF_LEVEL, bytes([lvl])):
            return False, "write enemy level failed"
        return True, None

    def _op_set_party_stat(
        self, character: str, stat: str, amount: int
    ) -> Tuple[bool, Optional[str]]:
        key = re.sub(r"[\s\-]+", "_", str(stat or "").strip().lower())
        info = _PARTY_STAT_OFFSETS.get(key)
        if info is None:
            return False, f"unknown party stat: {stat}"
        off, width = info
        data, _ = self._read_savemap()
        if not data:
            return False, "Savemap not readable"
        cid, err = self._char_id_from_gear_character_token(data, character)
        if err or cid is None:
            return False, err or "character"
        if cid >= len(_CHAR_BLOCK):
            return False, "bad character id"
        base = _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE) + _CHAR_BLOCK[cid]
        if width == 1:
            v = max(0, min(255, int(amount)))
            payload = bytes([v])
        else:
            v = max(0, min(9999, int(amount)))
            payload = struct.pack("<H", v)
        if not self._write(base + off, payload):
            return False, "write stat failed"
        if self._current_module_byte() == 2 and key in (
            "hp",
            "mp",
            "max_hp",
            "max_mp",
            "maxhp",
            "maxmp",
        ):
            slot = self._battle_ally_slot_for_cid(int(cid))
            if slot is not None:
                actor = self._battle_actor_addr(slot)
                mirror = {
                    "hp": BATTLE_OFF_HP,
                    "mp": BATTLE_OFF_MP,
                    "max_hp": BATTLE_OFF_MAX_HP,
                    "maxhp": BATTLE_OFF_MAX_HP,
                    "max_mp": BATTLE_OFF_MAX_MP,
                    "maxmp": BATTLE_OFF_MAX_MP,
                }
                moff = mirror[key]
                self._write(
                    actor + moff,
                    struct.pack("<I" if moff == BATTLE_OFF_HP or moff == BATTLE_OFF_MAX_HP else "<H", v),
                )
        self._call_ff7_party_stat_recalc()
        return True, None

    def _op_set_enemy_stat(
        self, enemy: str, stat: str, amount: int
    ) -> Tuple[bool, Optional[str]]:
        key = re.sub(r"[\s\-]+", "_", str(stat or "").strip().lower())
        sl, err = self._enemy_slot_from_token(enemy)
        if err or sl is None:
            return False, err or "enemy"
        snap = self._last_snapshot or {}
        scene_id: Optional[int] = None
        for row in snap.get("enemies") or []:
            if int(row.get("slot", -1)) == int(sl):
                scene_id = int(row.get("scene_id", -1))
                break
        # Live battle-actor stats (hp/mp/max_hp/max_mp) go to the actor directly
        if key in ("hp", "mp", "max_hp", "maxhp", "max_mp", "maxmp"):
            actor = self._battle_actor_addr(int(sl))
            v = max(0, min(65535, int(amount)))
            if key == "hp":
                ok = self._write(actor + BATTLE_OFF_HP, struct.pack("<I", v))
            elif key in ("max_hp", "maxhp"):
                ok = self._write(actor + BATTLE_OFF_MAX_HP, struct.pack("<I", v))
            elif key == "mp":
                ok = self._write(actor + BATTLE_OFF_MP, struct.pack("<H", v))
            else:
                ok = self._write(actor + BATTLE_OFF_MAX_MP, struct.pack("<H", v))
            return (True, None) if ok else (False, "write failed")
        info = _ENEMY_STAT_OFFSETS.get(key)
        if info is None:
            return False, f"unknown enemy stat: {stat}"
        off, _width = info
        if scene_id is None or scene_id < 0:
            return False, "no scene_id for enemy"
        base = (
            _rebase(self._proc.module_base, ADDR_ENEMY_DATA_BASE)
            + scene_id * ENEMY_DATA_STRIDE
        )
        raw_amount = int(amount)
        # Def/MDef are stored as value*2 in the record; scale down to keep UX natural.
        if key in ("def", "defense", "mdef", "magic_defense", "magicdefense"):
            raw_amount = max(0, raw_amount // 2)
        v = max(0, min(255, raw_amount))
        if not self._write(base + off, bytes([v])):
            return False, "write enemy stat failed"
        return True, None

    # ------------------------------------------------------------------
    # Menu colors
    # ------------------------------------------------------------------
    def _op_set_menu_colors(
        self, target: str, color: str
    ) -> Tuple[bool, Optional[str]]:
        corners = _parse_menu_corners(target)
        if not corners:
            return False, f"unknown menu target: {target}"
        rgb = _parse_menu_color(color)
        if rgb is None:
            return False, f"unknown color: {color}"
        if not self._proc:
            return False, "Not attached"
        sm_base = _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE)
        probe = self._read(sm_base + SAVE_OFF_GIL, 1)
        if probe is None or len(probe) < 1:
            logger.warning(
                "set_menu_colors: savemap unreachable (module_base=0x%x, sm_base=0x%x)",
                self._proc.module_base,
                sm_base,
            )
            return False, "savemap unreachable (process attach or rebase is wrong)"
        mb = self._proc.module_base
        disp_base = _rebase(mb, ADDR_MENU_COLOR_DISPLAY_BASE)
        mir_base = _rebase(mb, ADDR_MENU_COLOR_SAVE_MIRROR_BASE)
        payload_rgb = bytes(rgb)
        disp_payload = _menu_display_quad(rgb)
        written_addrs: List[str] = []
        for c in corners:
            sm_off = _MENU_CORNER_OFFSETS.get(c)
            d_off = _MENU_CORNER_DISPLAY_OFF.get(c)
            m_off = _MENU_CORNER_I7_MIRROR_OFF.get(c)
            if sm_off is None or d_off is None or m_off is None:
                continue
            # 1) Savemap (Data Crystal layout) — persists for saves / our HTML reader.
            sm_addr = sm_base + sm_off
            ok = self._win_protect_write(
                sm_addr,
                payload_rgb,
                flush_icache=False,
                page_protect=_PAGE_READWRITE,
            )
            if not ok:
                logger.warning(
                    "set_menu_colors: savemap write failed corner=%s addr=0x%x",
                    c,
                    sm_addr,
                )
                return False, f"savemap write failed for corner {c}"
            back_sm = self._read(sm_addr, 3)
            if back_sm != payload_rgb:
                logger.warning(
                    "set_menu_colors: savemap read-back mismatch corner=%s expected=%r got=%r",
                    c,
                    payload_rgb,
                    back_sm,
                )
                return False, f"menu color savemap read-back failed for corner {c}"
            written_addrs.append(f"savemap:{c}=0x{sm_addr:x}")

            # 2) Live display palette (InteractiveSeven MenuColorAll) — what the game draws.
            d_addr = disp_base + d_off
            ok = self._win_protect_write(
                d_addr,
                disp_payload,
                flush_icache=False,
                page_protect=_PAGE_READWRITE,
            )
            if not ok:
                logger.warning(
                    "set_menu_colors: display write failed corner=%s addr=0x%x",
                    c,
                    d_addr,
                )
                return False, f"display palette write failed for corner {c}"
            back_d = self._read(d_addr, 4)
            if back_d != disp_payload:
                logger.warning(
                    "set_menu_colors: display read-back mismatch corner=%s expected=%r got=%r",
                    c,
                    disp_payload,
                    back_d,
                )
                return False, f"menu display read-back failed for corner {c}"
            written_addrs.append(f"display:{c}=0x{d_addr:x}")

            # 3) RAM mirror (InteractiveSeven MenuColorAllSave) — I7 writes after display.
            m_addr = mir_base + m_off
            ok = self._win_protect_write(
                m_addr,
                payload_rgb,
                flush_icache=False,
                page_protect=_PAGE_READWRITE,
            )
            if not ok:
                logger.warning(
                    "set_menu_colors: mirror write failed corner=%s addr=0x%x",
                    c,
                    m_addr,
                )
                return False, f"palette mirror write failed for corner {c}"
            back_m = self._read(m_addr, 3)
            if back_m != payload_rgb:
                logger.warning(
                    "set_menu_colors: mirror read-back mismatch corner=%s expected=%r got=%r",
                    c,
                    payload_rgb,
                    back_m,
                )
                return False, f"menu mirror read-back failed for corner {c}"
            written_addrs.append(f"mirror:{c}=0x{m_addr:x}")

        logger.info(
            "set_menu_colors: module_base=0x%x target=%r corners=%s rgb=%s sites=%s",
            self._proc.module_base,
            target,
            corners,
            rgb,
            written_addrs,
        )
        return True, None

    # ------------------------------------------------------------------
    # Materia equip / unequip
    # ------------------------------------------------------------------
    def _op_equip_materia(
        self, character: str, gear_kind: str, slot: int, materia_name: str
    ) -> Tuple[bool, Optional[str]]:
        data, _ = self._read_savemap()
        if not data:
            return False, "Savemap not readable"
        cid, err = self._char_id_from_gear_character_token(data, character)
        if err or cid is None:
            return False, err or "character"
        if cid >= len(_CHAR_BLOCK):
            return False, "bad character id"
        gk = str(gear_kind or "").strip().lower()
        if gk == "weapon":
            slot_base = REC_OFF_WEAPON_MATERIA
        elif gk == "armor":
            slot_base = REC_OFF_ARMOR_MATERIA
        else:
            return False, "gear_kind must be weapon|armor"
        s = int(slot)
        if s < 0 or s >= 8:
            return False, "materia slot must be 0-7"
        slot_addr = (
            _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE)
            + _CHAR_BLOCK[cid]
            + slot_base
            + s * 4
        )
        cur_raw = self._read(slot_addr, 4)
        if cur_raw is None or len(cur_raw) < 4:
            return False, "read current materia failed"
        cur_id = cur_raw[0]
        cur_ap = cur_raw[1] | (cur_raw[2] << 8) | (cur_raw[3] << 16)

        token = str(materia_name or "").strip().lower()
        unequip_only = token in ("", "none", "empty", "unequip", "-", "null")

        # Return previously-equipped materia (if any) to inventory first.
        if cur_id != 0xFF:
            if not self._inv_add_materia(int(cur_id), int(cur_ap)):
                return False, "inventory full (materia)"

        if unequip_only:
            if not self._write(
                slot_addr, bytes([0xFF, 0xFF, 0xFF, 0xFF])
            ):
                return False, "write materia slot failed"
            self._call_ff7_party_stat_recalc()
            return True, None

        mid, merr = self._materia_name_to_id(materia_name)
        if merr or mid is None:
            return False, merr or "materia name"
        inv_slot = self._inv_find_materia_slot(int(mid))
        if inv_slot is None:
            return False, f"materia not in inventory: {materia_name}"
        rec = self._inv_read_materia_slot(inv_slot) or (0, 0)
        new_ap = int(rec[1])
        if not self._inv_write_materia_slot(inv_slot, None, 0):
            return False, "remove from inventory failed"
        payload = bytes(
            [
                int(mid) & 0xFF,
                new_ap & 0xFF,
                (new_ap >> 8) & 0xFF,
                (new_ap >> 16) & 0xFF,
            ]
        )
        if not self._write(slot_addr, payload):
            return False, "write materia slot failed"
        self._call_ff7_party_stat_recalc()
        return True, None

    # ------------------------------------------------------------------
    # Inventory-aware gear swap
    # ------------------------------------------------------------------
    def _op_set_character_gear_with_inventory(
        self, character: str, gear_kind: str, gear_id: int
    ) -> Tuple[bool, Optional[str]]:
        """Equip by swapping with the inventory item list.

        - Validates the requested gear exists in the inventory.
        - Removes one copy from inventory.
        - Reads current equipped id and adds it back to inventory.
        - Writes the new gear id into the character record.
        """
        data, _ = self._read_savemap()
        if not data:
            return False, "Savemap not readable"
        cid, err = self._char_id_from_gear_character_token(data, character)
        if err or cid is None:
            return False, err or "character"
        if cid >= len(_CHAR_BLOCK):
            return False, "bad character id"
        gk = str(gear_kind or "").strip().lower()
        if gk == "weapon":
            rec_off = REC_OFF_WEAPON
            base_id = ITEM_ID_WEAPON_BASE
            id_max = 127
        elif gk == "armor":
            rec_off = REC_OFF_ARMOR
            base_id = ITEM_ID_ARMOR_BASE
            id_max = 31
        elif gk == "accessory":
            rec_off = REC_OFF_ACCESSORY
            base_id = ITEM_ID_ACCESSORY_BASE
            id_max = 31
        else:
            return False, "gear_kind must be weapon|armor|accessory"
        if gear_id < 0 or gear_id > id_max:
            return False, f"{gk} id out of range"
        allow = _allowed_ids_for_char_gear(int(cid), gk)
        if allow is not None and int(gear_id) not in allow:
            return False, "gear not allowed for this character (equip_allowlists.json)"
        new_item_id = base_id + int(gear_id)
        if self._inv_find_item_slot(new_item_id) is None:
            return False, "gear not in inventory"
        # Remove requested gear from inventory first, then read+return old gear.
        if not self._inv_remove_item(new_item_id, 1):
            return False, "remove from inventory failed"
        rec_addr = (
            _rebase(self._proc.module_base, ADDR_SAVEMAP_BASE)
            + _CHAR_BLOCK[cid]
            + rec_off
        )
        cur = self._read_u8(rec_addr)
        if cur is not None and cur != 0xFF:
            self._inv_add_item(base_id + int(cur), 1)
        if not self._write(rec_addr, bytes([int(gear_id) & 0xFF])):
            return False, "write gear failed"
        self._call_ff7_party_stat_recalc()
        return True, None

    # ------------------------------------------------------------------
    # Start battle (with queued behaviour)
    # ------------------------------------------------------------------
    def consume_pending_battle(self) -> Optional[int]:
        """Service-loop helper: pops the pending battle id and returns it."""
        pid = self._pending_battle_id
        self._pending_battle_id = None
        return pid

    def _start_battle_now(self, battle_id: int) -> Tuple[bool, Optional[str]]:
        """Trigger a battle assuming current module is Field (1) or World (3).

        Field/world write sequence matches ff7-ultima ``startBattle`` (useFF7.ts).
        """
        if not self._proc:
            return False, "Not attached"
        mod = self._current_module_byte()
        bid = int(battle_id) & 0xFFFF
        base = self._proc.module_base
        if mod == 1:
            field_obj = self._read_u32(_rebase(base, ADDR_FIELD_OBJ_PTR))
            if not field_obj:
                return False, "field object pointer missing"
            if not self._write(field_obj + 1, bytes([2])):
                return False, "write field game module (battle) failed"
            if not self._write(field_obj + 2, struct.pack("<H", bid)):
                return False, "write field battle id failed"
            if not self._write(field_obj + 38, struct.pack("<H", 0)):
                return False, "write field battle result reset failed"
            if not self._write(
                _rebase(base, ADDR_BATTLE_MODULE_FIELD),
                bytes([1]),
            ):
                return False, "write battle_module_field failed"
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if self._current_module_byte() == 2:
                    self._write(field_obj + 1, bytes([0]))
                    break
                time.sleep(0.05)
            return True, None
        if mod == 3:
            if not self._write(
                _rebase(base, ADDR_BATTLE_ID_WORLD), struct.pack("<I", bid)
            ):
                return False, "write world battle id failed"
            if not self._write(
                _rebase(base, ADDR_WORLD_BATTLE_FLAG1), struct.pack("<I", 0)
            ):
                return False, "write world_battle_flag1 failed"
            if not self._write(
                _rebase(base, ADDR_WORLD_BATTLE_FLAG2), struct.pack("<I", 0)
            ):
                return False, "write world_battle_flag2 failed"
            if not self._write(
                _rebase(base, ADDR_WORLD_BATTLE_FLAG3), struct.pack("<I", 1)
            ):
                return False, "write world_battle_flag3 failed"
            if not self._write(
                _rebase(base, ADDR_WORLD_BATTLE_FLAG4), struct.pack("<I", 3)
            ):
                return False, "write world_battle_flag4 failed"
            return True, None
        return False, f"cannot start battle in module {mod}"

    def _op_start_battle(self, battle_id: int) -> Tuple[bool, Optional[str]]:
        mod = self._current_module_byte()
        bid = int(battle_id) & 0xFFFF
        if mod in (1, 3):
            return self._start_battle_now(bid)
        self._pending_battle_id = bid
        return True, None

    # ------------------------------------------------------------------
    # Add item / materia / gear (via FF7's own functions)
    # ------------------------------------------------------------------
    def _op_add_item(
        self, item_name: str, quantity: int
    ) -> Tuple[bool, Optional[str]]:
        iid, err = self._item_name_to_id(item_name)
        if err or iid is None:
            return False, err or "item"
        qty = max(1, min(MAX_ITEM_QUANTITY, int(quantity)))
        encoded = (int(iid) & 0x1FF) | ((qty & 0x7F) << 9)
        ok = self._call_ff7_game_fn_one_arg(ADDR_PARTY_ADD_ITEM_FN, encoded)
        return (True, None) if ok else (False, "party_add_item_fn failed")

    def _op_add_materia(
        self, materia_name: str, materia_level: int
    ) -> Tuple[bool, Optional[str]]:
        mid, err = self._materia_name_to_id(materia_name)
        if err or mid is None:
            return False, err or "materia"
        raw_level = int(materia_level or 0)
        if raw_level in _MATERIA_LEVEL_AP:
            ap = _MATERIA_LEVEL_AP[raw_level]
        else:
            ap = max(0, min(0xFFFFFF, raw_level))
        encoded = (int(mid) & 0xFF) | ((ap & 0xFFFFFF) << 8)
        ok = self._call_ff7_game_fn_one_arg(ADDR_PARTY_ADD_MATERIA_FN, encoded)
        return (True, None) if ok else (False, "party_add_materia_fn failed")

    def _op_add_gear(self, gear_name: str) -> Tuple[bool, Optional[str]]:
        iid, err = self._gear_name_to_item_id(gear_name)
        if err or iid is None:
            return False, err or "gear"
        encoded = (int(iid) & 0x1FF) | (1 << 9)
        ok = self._call_ff7_game_fn_one_arg(ADDR_PARTY_ADD_ITEM_FN, encoded)
        return (True, None) if ok else (False, "party_add_item_fn failed")

    def execute_operation(
        self, op: str, kwargs: Dict[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        """Run a crowd-control style operation. Always returns (ok, error_message)."""
        if sys.platform != "win32" or _kernel32 is None:
            return False, "FF7 writes require Windows"
        ok, err = self.ensure_attached()
        if not ok or not self._proc:
            return False, err or "Not attached"

        def _tok_char() -> str:
            c = kwargs.get("character")
            if c is not None and str(c).strip() != "":
                return str(c).strip()
            s = kwargs.get("slot")
            if s is not None and str(s).strip() != "":
                return str(s).strip()
            return ""

        if op == "add_gil":
            amt, e = self._parse_gil_amount(kwargs.get("amount"))
            if e or amt is None:
                return False, e or "amount"
            return self._op_add_gil(int(amt))
        if op == "remove_gil":
            amt, e = self._parse_gil_amount(kwargs.get("amount"))
            if e or amt is None:
                return False, e or "amount"
            return self._op_remove_gil(int(amt))
        if op == "add_party_hp":
            amt, e = self._parse_int_or_random(kwargs.get("amount"), 9999)
            if e or amt is None or amt <= 0:
                return False, e or "amount must be positive"
            ps, err = self._party_slot_from_token(None, _tok_char(), False)
            if err or ps is None:
                return False, err or "character"
            return self._party_hp_rw(int(ps), "add", int(amt))
        if op == "remove_party_hp":
            amt, e = self._parse_int_or_random(kwargs.get("amount"), 9999)
            if e or amt is None or amt <= 0:
                return False, e or "amount must be positive"
            ps, err = self._party_slot_from_token(None, _tok_char(), False)
            if err or ps is None:
                return False, err or "character"
            return self._party_hp_rw(int(ps), "remove", int(amt))
        if op == "kill_party_member":
            ps, err = self._party_slot_from_token(None, _tok_char(), False)
            if err or ps is None:
                return False, err or "character"
            return self._op_kill_party_member(int(ps))
        if op == "kill_all_enemies":
            return self._op_kill_all_enemies()
        if op == "damage_enemy":
            amt, e = self._parse_int_or_random(
                kwargs.get("amount"), 9999, clamp_1_9999=True
            )
            if e or amt is None or amt <= 0:
                return False, e or "amount must be positive"
            if kwargs.get("enemy") is not None and str(kwargs.get("enemy")).strip() != "":
                en = str(kwargs.get("enemy")).strip()
            elif "enemy_index" in kwargs:
                en = str(int(kwargs["enemy_index"]))
            else:
                en = ""
            sl, err = self._enemy_slot_from_token(en)
            if err or sl is None:
                return False, err or "enemy"
            return self._op_damage_enemy(int(sl), int(amt))
        if op == "kill_enemy":
            en = str(kwargs.get("enemy", "")).strip()
            if not en:
                return False, "empty enemy"
            sl, err = self._enemy_slot_from_token(en)
            if err or sl is None:
                return False, err or "enemy"
            return self._op_kill_enemy(int(sl))
        if op == "rename_character":
            return self._op_rename_character(
                str(kwargs.get("current_name", "")),
                str(kwargs.get("new_name", "")),
            )
        if op == "set_battle_status":
            ps, err = self._party_slot_from_token(None, _tok_char(), True)
            if err or ps is None:
                return False, err or "character"
            st = str(kwargs.get("status_effect", "")).strip()
            key = re.sub(r"[^a-z0-9]", "", st.lower())
            mask = _STATUS_NAME_TO_MASK.get(key) or _STATUS_NAME_TO_MASK.get(st.lower())
            if mask is None:
                return False, f"unknown status: {st}"
            if st.lower() == "dual":
                mask |= 0x08000000
            return self._op_set_battle_status(
                int(ps), int(mask), str(kwargs.get("mode", "on"))
            )
        if op == "set_field_status_byte":
            ps, err = self._party_slot_from_token(None, _tok_char(), False)
            if err or ps is None:
                return False, err or "character"
            return self._op_set_field_status_byte(int(ps), int(kwargs.get("value", 0)))
        if op == "set_character_gear":
            raw_kind = str(kwargs.get("gear_kind", "")).strip().lower()
            if not raw_kind:
                raw_kind = "weapon"
            gid, ge = self._resolve_gear_token(
                raw_kind,
                str(kwargs.get("gear", "")),
            )
            if ge or gid is None:
                return False, ge or "gear"
            return self._op_set_character_gear_with_inventory(
                _tok_char(),
                raw_kind,
                int(gid),
            )
        if op == "set_party_level":
            lvl, e = self._parse_int_or_random(kwargs.get("level"), 99)
            if e or lvl is None:
                return False, e or "level"
            return self._op_set_party_level(_tok_char(), int(lvl))
        if op == "set_enemy_level":
            lvl, e = self._parse_int_or_random(kwargs.get("level"), 99)
            if e or lvl is None:
                return False, e or "level"
            en = str(kwargs.get("enemy", "")).strip()
            return self._op_set_enemy_level(en, int(lvl))
        if op == "set_party_stat":
            amt, e = self._parse_int_or_random(kwargs.get("amount"), 9999)
            if e or amt is None:
                return False, e or "amount"
            return self._op_set_party_stat(
                _tok_char(),
                str(kwargs.get("stat", "")),
                int(amt),
            )
        if op == "set_enemy_stat":
            amt, e = self._parse_int_or_random(kwargs.get("amount"), 9999)
            if e or amt is None:
                return False, e or "amount"
            en = str(kwargs.get("enemy", "")).strip()
            return self._op_set_enemy_stat(
                en, str(kwargs.get("stat", "")), int(amt)
            )
        if op == "set_menu_colors":
            return self._op_set_menu_colors(
                str(kwargs.get("target", "all")),
                str(kwargs.get("color", "")),
            )
        if op == "equip_materia":
            slv, se = self._parse_int_or_random(kwargs.get("slot", 0), 7)
            if se or slv is None:
                return False, se or "slot"
            slot = max(0, min(7, int(slv)))
            return self._op_equip_materia(
                _tok_char(),
                str(kwargs.get("gear_kind", "weapon")),
                slot,
                str(kwargs.get("materia", "")),
            )
        if op == "start_battle":
            bid, e = self._parse_int_or_random(kwargs.get("battle_id"), 1024)
            if e or bid is None:
                return False, e or "battle_id"
            return self._op_start_battle(int(bid))
        if op == "add_item":
            qty, e = self._parse_int_or_random(kwargs.get("quantity"), 99)
            if e or qty is None:
                return False, e or "quantity"
            return self._op_add_item(
                str(kwargs.get("item", "")), int(qty)
            )
        if op == "add_materia":
            raw_ml = kwargs.get("materia_level")
            if raw_ml is None or str(raw_ml).strip() == "":
                lvl = 1
            else:
                lv, le = self._parse_int_or_random(raw_ml, 6)
                if le or lv is None:
                    return False, le or "materia_level"
                lvl = max(0, min(0xFFFFFF, int(lv)))
            return self._op_add_materia(str(kwargs.get("materia", "")), int(lvl))
        if op == "add_gear":
            return self._op_add_gear(str(kwargs.get("gear", "")))
        if op == "restore_menu_words":
            return self._op_restore_menu_words(dict(kwargs))
        if op == "restore_battle_speed":
            pb = kwargs.get("prev_byte")
            if pb is None:
                return False, "prev_byte"
            return self._op_restore_battle_speed(int(pb))
        if op == "restore_battle_atb_mode":
            pb = kwargs.get("prev_config_byte")
            if pb is None:
                return False, "prev_config_byte"
            return self._op_restore_battle_atb_mode(int(pb))
        if op == "restore_infinite_items":
            return self._op_restore_infinite_items()
        if op == "set_battle_speed":
            sb, se = self._parse_battle_speed_token(kwargs.get("speed"))
            if se or sb is None:
                return False, se or "speed"
            ds = int(kwargs.get("duration_sec", 0) or 0)
            return self._op_set_battle_speed_byte(int(sb), ds)
        if op == "set_battle_atb_mode":
            ds = int(kwargs.get("duration_sec", 0) or 0)
            return self._op_set_battle_atb_mode(str(kwargs.get("mode", "")), ds)
        if op == "set_infinite_items":
            en = str(kwargs.get("enabled", "1")).strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            ds = int(kwargs.get("duration_sec", 0) or 0)
            raw_dur = kwargs.get("duration_sec")
            if raw_dur is not None and str(raw_dur).strip().lower() in (
                "none",
                "null",
                "inf",
                "infinite",
            ):
                ds = 0
            return self._op_set_infinite_items(en, ds)
        if op == "set_menu_row_access":
            acc = str(kwargs.get("access", "allow")).strip().lower()
            allow = acc in ("allow", "on", "true", "yes", "1")
            name = str(kwargs.get("menu_name", ""))
            dur = int(kwargs.get("duration_sec", 0) or 0)
            v0 = l0 = None
            if dur > 0:
                va = _rebase(self._proc.module_base, ADDR_MENU_VISIBILITY)
                la = _rebase(self._proc.module_base, ADDR_MENU_LOCKS)
                v0 = self._read_u16(va)
                l0 = self._read_u16(la)
                if v0 is None or l0 is None:
                    return False, "read menu words failed"
            ok_vis, err_vis = self._op_set_menu_u16(
                ADDR_MENU_VISIBILITY, name, allow, False
            )
            if not ok_vis:
                return False, err_vis or "menu visibility"
            ok_lk, err_lk = self._op_set_menu_u16(
                ADDR_MENU_LOCKS, name, not allow, False
            )
            if not ok_lk:
                return False, err_lk or "menu lock"
            if dur > 0 and v0 is not None and l0 is not None:
                self._post_success_timed.append(
                    (
                        "restore_menu_words",
                        {"visibility_u16": int(v0), "locks_u16": int(l0)},
                        dur,
                    )
                )
            return True, None
        if op == "set_menu_visibility":
            en = str(kwargs.get("enabled", "1")).strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            dur = int(kwargs.get("duration_sec", 0) or 0)
            v0 = l0 = None
            if dur > 0:
                va = _rebase(self._proc.module_base, ADDR_MENU_VISIBILITY)
                la = _rebase(self._proc.module_base, ADDR_MENU_LOCKS)
                v0 = self._read_u16(va)
                l0 = self._read_u16(la)
                if v0 is None or l0 is None:
                    return False, "read menu words failed"
            ok, err = self._op_set_menu_u16(
                ADDR_MENU_VISIBILITY, str(kwargs.get("menu_name", "")), en, False
            )
            if not ok:
                return False, err or "menu visibility"
            if dur > 0 and v0 is not None and l0 is not None:
                self._post_success_timed.append(
                    (
                        "restore_menu_words",
                        {"visibility_u16": int(v0), "locks_u16": int(l0)},
                        dur,
                    )
                )
            return True, None
        if op == "set_menu_lock":
            lk = str(kwargs.get("locked", "1")).strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            dur = int(kwargs.get("duration_sec", 0) or 0)
            v0 = l0 = None
            if dur > 0:
                va = _rebase(self._proc.module_base, ADDR_MENU_VISIBILITY)
                la = _rebase(self._proc.module_base, ADDR_MENU_LOCKS)
                v0 = self._read_u16(va)
                l0 = self._read_u16(la)
                if v0 is None or l0 is None:
                    return False, "read menu words failed"
            ok, err = self._op_set_menu_u16(
                ADDR_MENU_LOCKS, str(kwargs.get("menu_name", "")), lk, False
            )
            if not ok:
                return False, err or "menu lock"
            if dur > 0 and v0 is not None and l0 is not None:
                self._post_success_timed.append(
                    (
                        "restore_menu_words",
                        {"visibility_u16": int(v0), "locks_u16": int(l0)},
                        dur,
                    )
                )
            return True, None
        if op == "set_field_menu_access":
            en = str(kwargs.get("enabled", "1")).strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            return self._op_field_menu_access(en)
        if op == "set_world_speed_multiplier":
            return self._op_world_speed_multiplier(int(kwargs.get("multiplier", 1)))
        if op == "set_game_speed":
            sp, e = self._parse_float_or_random(kwargs.get("speed"))
            if e or sp is None:
                return False, e or "speed"
            sp = max(0.25, min(8.0, float(sp)))
            return self._op_set_game_speed(
                float(sp), int(kwargs.get("duration_sec", 0))
            )
        if op == "restore_game_speed":
            return self._op_restore_game_speed()
        if op in ("press_confirm", "press_cancel", "press_menu"):
            return False, "Input simulation is not implemented (addresses not wired)"

        return False, f"Unknown operation: {op}"

    def snapshot(self) -> Dict[str, Any]:
        if sys.platform != "win32" or _kernel32 is None:
            return {
                "hook": "ff7",
                "attached": False,
                "error": "FF7 hook requires Windows",
                "battle": False,
                "battle_ui": False,
                "current_module": 0,
                "party": [],
                "enemies": [],
                "gil": 0,
                "playtime_seconds": 0,
                "playtime_text": "--:--:--",
                "avg_party_level": 0,
                "equipped_materia_count": 0,
                "battle_atb_mode": None,
                "battle_speed_byte": None,
                "battle_mode_ram": None,
                "recent_item": None,
                "battle_log": [],
                "debug": {"stage": "unsupported_os", "platform": sys.platform},
            }

        ok, err = self.ensure_attached()
        if not ok or not self._proc:
            self._battle_ui_latched = False
            self._battle_ui_off_ticks = 0
            return {
                "hook": "ff7",
                "attached": False,
                "error": err or "Not attached",
                "battle": False,
                "battle_ui": False,
                "current_module": 0,
                "party": [],
                "enemies": [],
                "gil": 0,
                "playtime_seconds": 0,
                "playtime_text": "--:--:--",
                "avg_party_level": 0,
                "equipped_materia_count": 0,
                "battle_atb_mode": None,
                "battle_speed_byte": None,
                "battle_mode_ram": None,
                "recent_item": None,
                "battle_log": [],
                "debug": {"stage": "attach_failed", "message": err or "not attached"},
            }

        mod = _rebase(self._proc.module_base, ADDR_CURRENT_MODULE)
        cur_mod = self._read_u8(mod)
        if cur_mod is None:
            dbg_fail = {
                "stage": "read_current_module_failed",
                "current_module_addr_hex": hex(mod),
                "module_base_hex": hex(self._proc.module_base),
                "pid": self._proc.pid,
            }
            self.close()
            return {
                "hook": "ff7",
                "attached": False,
                "error": "Read failed (access denied?)",
                "battle": False,
                "battle_ui": False,
                "current_module": 0,
                "party": [],
                "enemies": [],
                "gil": 0,
                "playtime_seconds": 0,
                "playtime_text": "--:--:--",
                "avg_party_level": 0,
                "equipped_materia_count": 0,
                "battle_atb_mode": None,
                "battle_speed_byte": None,
                "battle_mode_ram": None,
                "recent_item": None,
                "battle_log": [],
                "debug": dbg_fail,
            }

        battle = cur_mod == 2
        if battle:
            self._battle_ui_off_ticks = 0
            self._battle_ui_latched = True
        else:
            self._battle_ui_off_ticks += 1
            if self._battle_ui_off_ticks >= _BATTLE_UI_OFF_POLL_TICKS:
                self._battle_ui_latched = False
        battle_ui = self._battle_ui_latched
        savemap, savemap_addr = self._read_savemap()
        gil = 0
        play_sec = 0
        party: List[Dict[str, Any]] = []
        enemies: List[Dict[str, Any]] = []
        menu_theme: Optional[Dict[str, str]] = None

        field_name = ""
        disp_addr = _rebase(self._proc.module_base, ADDR_MENU_COLOR_DISPLAY_BASE)
        disp_raw = self._read(disp_addr, 16)
        if disp_raw and len(disp_raw) == 16:
            menu_theme = menu_theme_from_live_display(disp_raw)
        if savemap:
            try:
                gil = struct.unpack_from("<I", savemap, SAVE_OFF_GIL)[0]
                play_sec = struct.unpack_from("<I", savemap, SAVE_OFF_PLAYTIME_SEC)[0]
            except struct.error:
                pass
            party, _ = self._parse_field_party(savemap)
            if menu_theme is None:
                menu_theme = menu_theme_from_savemap(savemap)
            field_name = _field_name_from_savemap(savemap)

        if battle:
            allies: List[Dict[str, Any]] = []
            for slot in range(3):
                a = self._read_battle_ally(slot, savemap)
                if a and (a["name"] not in ("", "?") or a["hp"] or a["max_hp"]):
                    allies.append(a)
            if allies:
                party = allies
                if savemap:
                    for row in party:
                        ps = int(row.get("party_slot", row.get("slot", -1)))
                        if ps in (0, 1, 2):
                            row.update(_party_gear_from_savemap(savemap, ps))
            for slot in range(4, 10):
                e = self._read_battle_enemy(slot)
                if not e:
                    continue
                if not (e.get("name") or e.get("hp") or e.get("max_hp")):
                    continue
                enemies.append(e)

        self._maybe_top_up_battle_party_items()
        self._tick_recent_item_detection(savemap, int(gil))
        self._tick_battle_log(battle, party, enemies, battle_ui)

        h = play_sec // 3600
        m = (play_sec % 3600) // 60
        s = play_sec % 60
        play_text = f"{h:02d}:{m:02d}:{s:02d}"

        dbg: Dict[str, Any] = {
            "stage": "ok",
            "pid": self._proc.pid,
            "module_base_hex": hex(self._proc.module_base),
            "savemap_addr_hex": hex(savemap_addr) if savemap_addr else None,
            "savemap_read_bytes": len(savemap) if savemap else 0,
            "menu_theme_source": (
                "live"
                if disp_raw and len(disp_raw) == 16 and menu_theme
                else ("savemap" if menu_theme and savemap else "none")
            ),
            "menu_color_display_addr_hex": hex(disp_addr),
            "current_module_addr_hex": hex(mod),
            "current_module_byte": int(cur_mod),
            "battle_char_base_hex": hex(
                _rebase(self._proc.module_base, ADDR_BATTLE_CHAR_BASE)
            ),
            "battle_atb_base_hex": hex(
                _rebase(self._proc.module_base, ADDR_BATTLE_ATB_BASE)
            ),
            "party_count": len(party),
            "enemy_count": len(enemies),
        }
        if savemap and len(savemap) >= SAVE_OFF_PARTY_SLOTS + 3:
            dbg["party_slot_ids"] = [
                int(savemap[SAVE_OFF_PARTY_SLOTS + i]) for i in range(3)
            ]

        avg_lv, mat_total = _records_party_aggregates(party, savemap)

        battle_atb: Optional[str] = None
        battle_speed_b: Optional[int] = None
        battle_mode_ram: Optional[int] = None
        if savemap and len(savemap) > SAVE_OFF_GENERAL_CONFIG:
            battle_atb = _decode_savemap_atb_mode(savemap[SAVE_OFF_GENERAL_CONFIG])
        if savemap and len(savemap) > SAVE_OFF_BATTLE_SPEED:
            battle_speed_b = int(savemap[SAVE_OFF_BATTLE_SPEED])
        if battle and self._proc:
            bm = _rebase(self._proc.module_base, ADDR_BATTLE_MODE)
            battle_mode_ram = self._read_u8(bm)

        out: Dict[str, Any] = {
            "hook": "ff7",
            "attached": True,
            "error": None,
            "battle": battle,
            "battle_ui": battle_ui,
            "current_module": int(cur_mod),
            "current_module_name": _current_module_label(int(cur_mod)),
            "field_name": field_name,
            "party": party,
            "enemies": enemies,
            "gil": int(gil),
            "playtime_seconds": int(play_sec),
            "playtime_text": play_text,
            "avg_party_level": int(avg_lv),
            "equipped_materia_count": int(mat_total),
            "battle_atb_mode": battle_atb,
            "battle_speed_byte": battle_speed_b,
            "battle_mode_ram": battle_mode_ram,
            "recent_item": self._last_item_gain,
            "battle_log": list(self._battle_log_lines),
            "debug": dbg,
        }
        if menu_theme:
            out["menu_theme"] = menu_theme
        self._last_snapshot = out
        return out


def ff7_connector_config_to_hook_kwargs(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Map connector UI config (arg_* keys) to execute_operation kwargs."""
    op = str(cfg.get("operation", ""))

    def _txt(key: str, default: str = "") -> str:
        v = cfg.get(key)
        if v is None:
            return default
        return str(v).strip()

    def _has_placeholder(s: str) -> bool:
        return "{" in s

    if op in ("add_gil", "remove_gil"):
        raw_amt = _txt("arg_amount", "0")
        if (
            not _has_placeholder(raw_amt)
            and not re.match(r"^random\s*:", raw_amt, re.I)
            and raw_amt.lower() != "random"
        ):
            try:
                raw_amt = str(max(0, int(raw_amt or 0)))
            except ValueError:
                pass
        return {"amount": raw_amt}
    if op in ("add_party_hp", "remove_party_hp"):
        raw_amt = _txt("arg_amount", "0")
        if (
            not _has_placeholder(raw_amt)
            and not re.match(r"^random\s*:", raw_amt, re.I)
            and raw_amt.lower() != "random"
        ):
            try:
                raw_amt = str(max(0, int(raw_amt or 0)))
            except ValueError:
                pass
        return {"character": _txt("arg_character"), "amount": raw_amt}
    if op == "kill_party_member":
        return {"character": _txt("arg_character")}
    if op == "damage_enemy":
        raw_amt = _txt("arg_amount", "0")
        if (
            not _has_placeholder(raw_amt)
            and not re.match(r"^random\s*:", raw_amt, re.I)
            and raw_amt.lower() != "random"
        ):
            try:
                raw_amt = str(max(1, min(9999, int(raw_amt or 0))))
            except ValueError:
                pass
        return {"enemy": _txt("arg_enemy"), "amount": raw_amt}
    if op == "kill_enemy":
        return {"enemy": _txt("arg_enemy")}
    if op == "kill_all_enemies":
        return {}
    if op == "rename_character":
        return {
            "current_name": _txt("arg_current_name"),
            "new_name": _txt("arg_new_name"),
        }
    if op == "set_battle_status":
        return {
            "character": _txt("arg_character"),
            "status_effect": _txt("arg_status_effect"),
            "mode": _txt("arg_mode", "on"),
        }
    if op == "set_field_status_byte":
        return {
            "character": _txt("arg_character"),
            "value": max(0, int(cfg.get("arg_value") or 0)),
        }
    if op == "set_character_gear":
        gk = _txt("arg_gear_kind")
        if not (gk or "").strip():
            gk = "weapon"
        return {
            "character": _txt("arg_character"),
            "gear_kind": gk,
            "gear": _txt("arg_gear"),
        }
    if op == "set_menu_row_access":
        return {
            "menu_name": _txt("arg_menu_name"),
            "access": _txt("arg_access", "allow"),
            "duration_sec": max(0, int(cfg.get("arg_duration_sec") or 0)),
        }
    if op == "set_menu_visibility":
        return {
            "menu_name": _txt("arg_menu_name"),
            "enabled": _txt("arg_enabled", "1"),
            "duration_sec": max(0, int(cfg.get("arg_duration_sec") or 0)),
        }
    if op == "set_menu_lock":
        return {
            "menu_name": _txt("arg_menu_name"),
            "locked": _txt("arg_locked", "1"),
            "duration_sec": max(0, int(cfg.get("arg_duration_sec") or 0)),
        }
    if op == "set_field_menu_access":
        return {"enabled": _txt("arg_enabled", "1")}
    if op == "set_world_speed_multiplier":
        return {"multiplier": max(1, int(cfg.get("arg_multiplier") or 1))}
    if op == "set_game_speed":
        raw_sp = _txt("arg_speed", "1.0")
        if (
            not _has_placeholder(raw_sp)
            and not re.match(r"^random\s*:", raw_sp, re.I)
        ):
            try:
                raw_sp = str(max(0.25, min(8.0, float(raw_sp))))
            except ValueError:
                pass
        return {
            "speed": raw_sp,
            "duration_sec": max(0, int(cfg.get("arg_duration_sec") or 0)),
        }
    if op == "restore_game_speed":
        return {}
    if op == "set_battle_speed":
        raw_sp = _txt("arg_speed", "128")
        if not _has_placeholder(raw_sp) and not re.match(r"^random\s*:", raw_sp, re.I):
            try:
                raw_sp = str(max(0, min(255, int(float(raw_sp)))))
            except ValueError:
                pass
        return {
            "speed": raw_sp,
            "duration_sec": max(0, int(cfg.get("arg_duration_sec") or 0)),
        }
    if op == "set_battle_atb_mode":
        return {
            "mode": _txt("arg_mode", "active"),
            "duration_sec": max(0, int(cfg.get("arg_duration_sec") or 0)),
        }
    if op == "set_infinite_items":
        raw_d = _txt("arg_duration_sec", "0")
        return {"enabled": _txt("arg_enabled", "1"), "duration_sec": raw_d}
    if op in ("set_party_level", "set_enemy_level"):
        raw_lv = _txt("arg_level", "1")
        if (
            not _has_placeholder(raw_lv)
            and not re.match(r"^random\s*:", raw_lv, re.I)
            and raw_lv.lower() != "random"
        ):
            try:
                raw_lv = str(max(1, min(99, int(raw_lv or 1))))
            except ValueError:
                pass
        if op == "set_party_level":
            return {"character": _txt("arg_character"), "level": raw_lv}
        return {"enemy": _txt("arg_enemy"), "level": raw_lv}
    if op in ("set_party_stat", "set_enemy_stat"):
        raw_amt = _txt("arg_amount", "0")
        if (
            not _has_placeholder(raw_amt)
            and not re.match(r"^random\s*:", raw_amt, re.I)
            and raw_amt.lower() != "random"
        ):
            try:
                raw_amt = str(max(0, int(raw_amt or 0)))
            except ValueError:
                pass
        if op == "set_party_stat":
            return {
                "character": _txt("arg_character"),
                "stat": _txt("arg_stat"),
                "amount": raw_amt,
            }
        return {
            "enemy": _txt("arg_enemy"),
            "stat": _txt("arg_stat"),
            "amount": raw_amt,
        }
    if op == "set_menu_colors":
        return {
            "target": _txt("arg_target", "all"),
            "color": _txt("arg_color"),
        }
    if op == "equip_materia":
        return {
            "character": _txt("arg_character"),
            "gear_kind": _txt("arg_gear_kind", "weapon"),
            "slot": _txt("arg_slot", "0"),
            "materia": _txt("arg_materia"),
        }
    if op == "start_battle":
        raw_bid = _txt("arg_battle_id", "0")
        if (
            not _has_placeholder(raw_bid)
            and not re.match(r"^random\s*:", raw_bid, re.I)
            and raw_bid.lower() != "random"
        ):
            try:
                raw_bid = str(max(0, min(0xFFFF, int(raw_bid or 0))))
            except ValueError:
                pass
        return {"battle_id": raw_bid}
    if op == "add_item":
        raw_qty = _txt("arg_quantity", "1")
        if (
            not _has_placeholder(raw_qty)
            and not re.match(r"^random\s*:", raw_qty, re.I)
            and raw_qty.lower() != "random"
        ):
            try:
                raw_qty = str(max(1, min(99, int(raw_qty or 1))))
            except ValueError:
                pass
        return {"item": _txt("arg_item"), "quantity": raw_qty}
    if op == "add_materia":
        raw_lvl = _txt("arg_materia_level", "1")
        if (
            not _has_placeholder(raw_lvl)
            and not re.match(r"^random\s*:", raw_lvl, re.I)
            and raw_lvl.lower() != "random"
        ):
            try:
                raw_lvl = str(max(0, min(0xFFFFFF, int(raw_lvl or 1))))
            except ValueError:
                pass
        return {"materia": _txt("arg_materia"), "materia_level": raw_lvl}
    if op == "add_gear":
        return {"gear": _txt("arg_gear")}
    return {}


FF7_CONNECTOR_PERSIST_ALLOWLIST = frozenset(
    {
        "set_game_speed",
        "set_battle_speed",
        "set_battle_atb_mode",
        "set_menu_row_access",
        "set_menu_visibility",
        "set_menu_lock",
        "set_field_menu_access",
        "set_world_speed_multiplier",
        "set_menu_colors",
        "set_infinite_items",
    }
)


# Backwards compatibility
FF7Reader = FF7Hook
