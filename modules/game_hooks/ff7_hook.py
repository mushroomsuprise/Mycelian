"""
FF7 PC (English Steam, ff7_en.exe) live memory hook: reads, optional writes, menu colors.

Canonical virtual addresses match ff7-ultima / ff7-lib.rs; rebase with:
    phys = module_base + (canon_addr - 0x00400000)

Window gradient RGB is read from the in-RAM savemap (Data Crystal offsets 0x48–0x53).
Character record gear/materia offsets match Data Crystal / ff7-flat-wiki savemap layouts for PC English.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
_GEAR_LAYOUT_ASSETS_LOADED = False


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


# Battle actor status dword at offset 0 (matches ff7-ultima statuses.Dead)
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

# Matches ff7-ultima GameModule (src/types.ts); unlisted bytes fall through to _current_module_label.
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
    17: "Victory",  # post-battle / fanfare (not in ff7-ultima GameModule enum gap 15–18)
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


def _records_party_aggregates(
    party: List[Dict[str, Any]], savemap: Optional[bytes]
) -> Tuple[int, int]:
    """Avg level (round(sum/3), empty slots 0) and total equipped materia for party slots 0–2."""
    by_slot: Dict[int, Dict[str, Any]] = {}
    for row in party:
        ps = _party_row_party_slot(row)
        if ps >= 0:
            by_slot[ps] = row
    levels: List[int] = []
    materia_total = 0
    for ps in range(3):
        if ps in by_slot:
            row = by_slot[ps]
            levels.append(int(row.get("level", 0) or 0))
            materia_total += _count_equipped_materia_in_row(row)
        elif savemap:
            levels.append(_savemap_party_slot_level(savemap, ps))
            gear = _party_gear_from_savemap(savemap, ps)
            if gear:
                materia_total += _count_equipped_materia_in_row(gear)
        else:
            levels.append(0)
    avg = round(sum(levels) / 3) if levels else 0
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
    _PROCESS_ACCESS = (
        PROCESS_VM_READ
        | PROCESS_VM_WRITE
        | PROCESS_VM_OPERATION
        | PROCESS_QUERY_INFORMATION
    )
else:
    _kernel32 = None


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


def menu_theme_from_savemap(savemap: bytes) -> Optional[Dict[str, str]]:
    """Build CSS-friendly colors from savemap window RGB corners."""
    ul = _rgb_tuple(savemap, SAVE_OFF_WIN_UL)
    ur = _rgb_tuple(savemap, SAVE_OFF_WIN_UR)
    ll = _rgb_tuple(savemap, SAVE_OFF_WIN_LL)
    lr = _rgb_tuple(savemap, SAVE_OFF_WIN_LR)
    if not all((ul, ur, ll, lr)):
        return None
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


# Human-readable catalog for Help + Connectors UI
FF7_CONNECTOR_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "add_gil",
        "label": "Add gil",
        "description": "Increases party gil in the active save (clamped).",
        "args": [{"name": "amount", "type": "positive_int", "label": "Amount"}],
    },
    {
        "id": "remove_gil",
        "label": "Remove gil",
        "description": "Decreases party gil (not below zero).",
        "args": [{"name": "amount", "type": "positive_int", "label": "Amount"}],
    },
    {
        "id": "add_party_hp",
        "label": "Add HP to party slot",
        "description": "In battle: live actor HP. On field: savemap character for that party slot.",
        "args": [
            {"name": "slot", "type": "slot_0_2", "label": "Party slot (0–2)"},
            {"name": "amount", "type": "positive_int", "label": "HP to add"},
        ],
    },
    {
        "id": "remove_party_hp",
        "label": "Remove HP from party slot",
        "description": "Same scope as Add HP; HP will not go below zero.",
        "args": [
            {"name": "slot", "type": "slot_0_2", "label": "Party slot (0–2)"},
            {"name": "amount", "type": "positive_int", "label": "HP to remove"},
        ],
    },
    {
        "id": "set_party_hp",
        "label": "Set party slot HP",
        "description": "Sets HP to a value (clamped to max HP when known).",
        "args": [
            {"name": "slot", "type": "slot_0_2", "label": "Party slot (0–2)"},
            {"name": "value", "type": "non_negative_int", "label": "Target HP"},
        ],
    },
    {
        "id": "kill_party_member",
        "label": "KO party slot",
        "description": "Battle only: sets Death status and HP to 0 (matches ff7-ultima). Field KO not supported.",
        "args": [{"name": "slot", "type": "slot_0_2", "label": "Party slot (0–2)"}],
    },
    {
        "id": "kill_all_enemies",
        "label": "Kill all enemies",
        "description": "Battle only: sets Death status and HP to 0 for each active enemy (matches ff7-ultima).",
        "args": [],
    },
    {
        "id": "damage_enemy",
        "label": "Damage enemy",
        "description": "Enemy index 0–5 maps to battle slots 4–9 (battle only).",
        "args": [
            {"name": "enemy_index", "type": "enemy_0_5", "label": "Enemy index (0–5)"},
            {"name": "amount", "type": "positive_int", "label": "Damage"},
        ],
    },
]


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

    def _read_battle_ally(self, slot: int) -> Optional[Dict[str, Any]]:
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
            "level": int(level_off) if level_off else 0,
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

    def _op_damage_enemy(self, enemy_index: int, amount: int) -> Tuple[bool, Optional[str]]:
        if amount <= 0:
            return False, "amount must be positive"
        if self._current_module_byte() != 2:
            return False, "Not in battle"
        if enemy_index < 0 or enemy_index > 5:
            return False, "enemy_index must be 0–5"
        slot = 4 + enemy_index
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

    def execute_operation(
        self, op: str, kwargs: Dict[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        """Run a crowd-control style operation. Always returns (ok, error_message)."""
        if sys.platform != "win32" or _kernel32 is None:
            return False, "FF7 writes require Windows"
        ok, err = self.ensure_attached()
        if not ok or not self._proc:
            return False, err or "Not attached"

        if op == "add_gil":
            return self._op_add_gil(int(kwargs.get("amount", 0)))
        if op == "remove_gil":
            return self._op_remove_gil(int(kwargs.get("amount", 0)))
        if op == "add_party_hp":
            amt = int(kwargs.get("amount", 0))
            if amt <= 0:
                return False, "amount must be positive"
            return self._party_hp_rw(int(kwargs.get("slot", -1)), "add", amt)
        if op == "remove_party_hp":
            amt = int(kwargs.get("amount", 0))
            if amt <= 0:
                return False, "amount must be positive"
            return self._party_hp_rw(
                int(kwargs.get("slot", -1)), "remove", amt
            )
        if op == "set_party_hp":
            return self._party_hp_rw(
                int(kwargs.get("slot", -1)), "set", int(kwargs.get("value", 0))
            )
        if op == "kill_party_member":
            return self._op_kill_party_member(int(kwargs.get("slot", -1)))
        if op == "kill_all_enemies":
            return self._op_kill_all_enemies()
        if op == "damage_enemy":
            return self._op_damage_enemy(
                int(kwargs.get("enemy_index", -1)), int(kwargs.get("amount", 0))
            )
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
                "current_module": 0,
                "party": [],
                "enemies": [],
                "gil": 0,
                "playtime_seconds": 0,
                "playtime_text": "--:--:--",
                "avg_party_level": 0,
                "equipped_materia_count": 0,
                "debug": {"stage": "unsupported_os", "platform": sys.platform},
            }

        ok, err = self.ensure_attached()
        if not ok or not self._proc:
            return {
                "hook": "ff7",
                "attached": False,
                "error": err or "Not attached",
                "battle": False,
                "current_module": 0,
                "party": [],
                "enemies": [],
                "gil": 0,
                "playtime_seconds": 0,
                "playtime_text": "--:--:--",
                "avg_party_level": 0,
                "equipped_materia_count": 0,
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
                "current_module": 0,
                "party": [],
                "enemies": [],
                "gil": 0,
                "playtime_seconds": 0,
                "playtime_text": "--:--:--",
                "avg_party_level": 0,
                "equipped_materia_count": 0,
                "debug": dbg_fail,
            }

        battle = cur_mod == 2
        savemap, savemap_addr = self._read_savemap()
        gil = 0
        play_sec = 0
        party: List[Dict[str, Any]] = []
        enemies: List[Dict[str, Any]] = []
        menu_theme: Optional[Dict[str, str]] = None

        field_name = ""
        if savemap:
            try:
                gil = struct.unpack_from("<I", savemap, SAVE_OFF_GIL)[0]
                play_sec = struct.unpack_from("<I", savemap, SAVE_OFF_PLAYTIME_SEC)[0]
            except struct.error:
                pass
            party, _ = self._parse_field_party(savemap)
            menu_theme = menu_theme_from_savemap(savemap)
            field_name = _field_name_from_savemap(savemap)

        if battle:
            allies: List[Dict[str, Any]] = []
            for slot in range(3):
                a = self._read_battle_ally(slot)
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

        out: Dict[str, Any] = {
            "hook": "ff7",
            "attached": True,
            "error": None,
            "battle": battle,
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
            "debug": dbg,
        }
        if menu_theme:
            out["menu_theme"] = menu_theme
        return out


def ff7_connector_config_to_hook_kwargs(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Map connector UI config (arg_* keys) to execute_operation kwargs."""
    op = str(cfg.get("operation", ""))
    if op in ("add_gil", "remove_gil"):
        return {"amount": max(0, int(cfg.get("arg_amount") or 0))}
    if op in ("add_party_hp", "remove_party_hp"):
        return {
            "slot": int(cfg.get("arg_slot") or 0),
            "amount": max(0, int(cfg.get("arg_amount") or 0)),
        }
    if op == "set_party_hp":
        return {
            "slot": int(cfg.get("arg_slot") or 0),
            "value": max(0, int(cfg.get("arg_value") or 0)),
        }
    if op == "kill_party_member":
        return {"slot": int(cfg.get("arg_slot") or 0)}
    if op == "damage_enemy":
        return {
            "enemy_index": int(cfg.get("arg_enemy_index") or 0),
            "amount": max(0, int(cfg.get("arg_amount") or 0)),
        }
    if op == "kill_all_enemies":
        return {}
    return {}


# Backwards compatibility
FF7Reader = FF7Hook
