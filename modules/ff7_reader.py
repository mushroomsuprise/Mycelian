"""
FF7 PC (English Steam, ff7_en.exe) live memory reader for Mycelian game hooks.

Canonical virtual addresses below match the layout used by ff7-ultima
(https://github.com/maciej-trebacz/ff7-ultima): battle_char_base / battle_atb_base /
current_module appear in useFF7.ts shellcode and instant-ATB routines. Other
addresses follow the same 0x00400000 image-base convention and are rebased with:

    phys = module_base + (canon_addr - 0x00400000)

Payload schema v1 (hooks.ff7) is assembled in game_hooks_service.py.

This module is Windows-only; on other platforms snapshot() returns an error stub.
"""

from __future__ import annotations

import ctypes
import logging
import struct
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CANON_IMAGE_BASE = 0x00400000

# Addresses from ff7-lib.rs (https://github.com/maciej-trebacz/ff7-lib.rs)
# All addresses use the 0x00400000 canonical image base and are rebased at runtime.
ADDR_CURRENT_MODULE = 0x00CBF9DC
ADDR_BATTLE_CHAR_BASE = 0x009AB0DC
ADDR_BATTLE_ATB_BASE = 0x009A8B12
ADDR_BATTLE_CHAR_ARRAY = 0x009A8DB8
ADDR_ENEMY_OBJ_BASE = 0x009A8794
ADDR_ENEMY_DATA_BASE = 0x009A8E9C
ADDR_PARTY_MEMBER_IDS = 0x00DC0230
ADDR_PARTY_MEMBER_NAMES = 0x00DBFD9C
# Fixed savemap address in ff7_en.exe data segment (well-documented at
# https://ff7-mods.github.io/ff7-flat-wiki/FF7/Savemap — not behind a pointer).
ADDR_SAVEMAP_BASE = 0x00DBFD38

BATTLE_ACTOR_STRIDE = 104
BATTLE_ATB_STRIDE = 68
BATTLE_CHAR_ARRAY_STRIDE = 0x34
ENEMY_OBJ_STRIDE = 16
ENEMY_DATA_STRIDE = 184
PARTY_RECORD_STRIDE = 0x84
SAVEMAP_SIZE = 0x10F4

# In-save character records (Data Crystal savemap) — offset from start of savemap.
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

if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _OpenProcess = _kernel32.OpenProcess
    _ReadProcessMemory = _kernel32.ReadProcessMemory
    _CloseHandle = _kernel32.CloseHandle
    PROCESS_VM_READ = 0x0010
    PROCESS_QUERY_INFORMATION = 0x0400
else:
    _kernel32 = None


def _rebase(module_base: int, canon_addr: int) -> int:
    return module_base + (canon_addr - CANON_IMAGE_BASE)


def _decode_ff7_name(raw: bytes) -> str:
    """Decode an FF Text encoded name (offset +0x20 from ASCII)."""
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


@dataclass
class _ProcessHandle:
    pid: int
    handle: int
    module_base: int


class FF7Reader:
    """Attach to ff7_en.exe / ff7.exe and read a minimal snapshot."""

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
        h = _OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
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

    def _parse_field_party(self, savemap: bytes) -> Tuple[List[Dict[str, Any]], List[int]]:
        party_ids: List[int] = []
        for i in range(3):
            if len(savemap) > SAVE_OFF_PARTY_SLOTS + i:
                cid = savemap[SAVE_OFF_PARTY_SLOTS + i]
                party_ids.append(cid if cid < 0xFF else -1)
        members: List[Dict[str, Any]] = []
        for cid in party_ids:
            if cid < 0 or cid >= len(_CHAR_BLOCK):
                members.append(
                    {
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
                )
                continue
            off = _CHAR_BLOCK[cid]
            if off + 0x84 > len(savemap):
                continue
            rec = savemap[off : off + 0x84]
            name = _decode_ff7_name(rec[0x10:0x1C])
            level = rec[0x01]
            limit_bar = rec[0x0F]
            hp = struct.unpack_from("<H", rec, 0x2C)[0]
            max_hp = struct.unpack_from("<H", rec, 0x38)[0]
            mp = struct.unpack_from("<H", rec, 0x30)[0]
            max_mp = struct.unpack_from("<H", rec, 0x3A)[0]
            members.append(
                {
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
            )
        return members, party_ids

    def _read_battle_common(self, slot: int) -> Optional[Tuple[bytes, int, int, int, int, int, float]]:
        """Read fields shared by allies and enemies from battle_char_base / battle_atb_base."""
        if not self._proc:
            return None
        base = _rebase(self._proc.module_base, ADDR_BATTLE_CHAR_BASE) + slot * BATTLE_ACTOR_STRIDE
        raw = self._read(base, BATTLE_ACTOR_STRIDE)
        if not raw:
            return None
        status = struct.unpack_from("<I", raw, 0x00)[0]
        flags = raw[0x05] if len(raw) > 5 else 0
        hp = struct.unpack_from("<i", raw, 0x2C)[0]
        max_hp = struct.unpack_from("<i", raw, 0x30)[0]
        mp = struct.unpack_from("<h", raw, 0x28)[0]
        max_mp = struct.unpack_from("<h", raw, 0x2A)[0]
        atb_addr = _rebase(self._proc.module_base, ADDR_BATTLE_ATB_BASE) + slot * BATTLE_ATB_STRIDE + 0x02
        atb_raw = self._read_u16(atb_addr)
        atb = (atb_raw or 0) / 65535.0 if atb_raw is not None else 0.0
        return (raw, int(status), int(flags), int(hp), int(max_hp), int(mp), int(max_mp), float(atb))

    def _read_battle_ally(self, slot: int) -> Optional[Dict[str, Any]]:
        """Read a party member's live battle state (slots 0-2)."""
        common = self._read_battle_common(slot)
        if common is None:
            return None
        raw, status, flags, hp, max_hp, mp, max_mp, atb = common

        party_id_addr = _rebase(self._proc.module_base, ADDR_PARTY_MEMBER_IDS) + slot
        party_id = self._read_u8(party_id_addr)
        if party_id is not None and party_id < 9:
            name_addr = _rebase(self._proc.module_base, ADDR_PARTY_MEMBER_NAMES) + party_id * PARTY_RECORD_STRIDE
            name_raw = self._read(name_addr, 12)
            name = _decode_ff7_name(name_raw) if name_raw else "?"
        else:
            name = "?"

        limit_addr = _rebase(self._proc.module_base, ADDR_BATTLE_CHAR_ARRAY) + slot * BATTLE_CHAR_ARRAY_STRIDE + 0x08
        limit_raw = self._read_u16(limit_addr)
        limit = int(limit_raw) if limit_raw is not None else 0

        level_off = raw[0x24] if len(raw) > 0x24 else 0
        return {
            "slot": slot,
            "status": status,
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
        """Read an enemy's live battle state (slots 4-9)."""
        enemy_idx = slot - 4
        if not self._proc or enemy_idx < 0:
            return None

        scene_idx_addr = _rebase(self._proc.module_base, ADDR_ENEMY_OBJ_BASE) + enemy_idx * ENEMY_OBJ_STRIDE
        scene_idx = self._read_u8(scene_idx_addr)
        if scene_idx is None or scene_idx == 0xFF:
            return None

        common = self._read_battle_common(slot)
        if common is None:
            return None
        raw, status, flags, hp, max_hp, mp, max_mp, atb = common

        name_addr = _rebase(self._proc.module_base, ADDR_ENEMY_DATA_BASE) + scene_idx * ENEMY_DATA_STRIDE
        name_raw = self._read(name_addr, 24)
        name = _decode_ff7_name(name_raw) if name_raw else "?"

        level_addr = name_addr + 0x20
        level = self._read_u8(level_addr) or 0

        return {
            "slot": slot,
            "status": status,
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

    def snapshot(self) -> Dict[str, Any]:
        """
        Return a dict suitable for hooks.ff7 in game_hook_payload v1.
        """
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
                "debug": dbg_fail,
            }

        battle = cur_mod == 2
        savemap, savemap_addr = self._read_savemap()
        gil = 0
        play_sec = 0
        party: List[Dict[str, Any]] = []
        enemies: List[Dict[str, Any]] = []

        if savemap:
            try:
                gil = struct.unpack_from("<I", savemap, SAVE_OFF_GIL)[0]
                play_sec = struct.unpack_from("<I", savemap, SAVE_OFF_PLAYTIME_SEC)[0]
            except struct.error:
                pass
            party, _ = self._parse_field_party(savemap)

        if battle:
            allies: List[Dict[str, Any]] = []
            for slot in range(3):
                a = self._read_battle_ally(slot)
                if a and (a["name"] not in ("", "?") or a["hp"] or a["max_hp"]):
                    allies.append(a)
            if allies:
                party = allies
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

        return {
            "hook": "ff7",
            "attached": True,
            "error": None,
            "battle": battle,
            "current_module": int(cur_mod),
            "party": party,
            "enemies": enemies,
            "gil": int(gil),
            "playtime_seconds": int(play_sec),
            "playtime_text": play_text,
            "debug": dbg,
        }
