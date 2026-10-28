# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/game_hooks/ff7_hook.py, part 3: FF7Hook tick helpers (recent item gain,
battle status/command log) and the heavier write ops (gil, HP, KO/damage, level and stat edits, menu
colors, materia equip, inventory gear swap, start battle, add item/materia/gear, inventory query).
Memory is a fake byte store; nothing attaches to a real process.
"""

from __future__ import annotations

import math
import struct
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.game_hooks import ff7_hook as ff7  # noqa: E402

BASE = ff7.CANON_IMAGE_BASE  # module base == canonical base, so _rebase is the identity


# --------------------------------------------------------------------------- fake process memory


class FakeMemory:
    """ProcessMemory stand-in: sparse byte store with failure switches and call logs."""

    def __init__(self) -> None:
        self.mem: Dict[int, int] = {}
        self.available = True
        self.attached = True
        self.attach_result: Tuple[bool, Optional[str]] = (True, None)
        self.fail_read: set = set()
        self.fail_write: set = set()
        self.fail_all_writes = False
        self.fail_protect = False
        self.protect_stores = True
        self.writes: List[Tuple[int, bytes]] = []
        self.protect_writes: List[Tuple[int, bytes, bool, int]] = []
        self.shellcode: List[Tuple[bytes, str]] = []
        self.shellcode_ok = True
        self.attach_calls: List = []
        self.closed = 0

    pid = property(lambda self: 4242)
    handle = property(lambda self: 7)
    module_base = property(lambda self: BASE)

    def is_available(self):
        return self.available

    def is_attached(self):
        return self.attached

    def attach(self, names, *, canon_image_base=BASE):
        self.attach_calls.append(("attach", names, canon_image_base))
        return self.attach_result

    def ensure_attached(self, names, *, canon_image_base=BASE):
        self.attach_calls.append(("ensure", names, canon_image_base))
        return self.attach_result

    def close(self):
        self.closed += 1

    def read(self, addr, size):
        if addr in self.fail_read:
            return None
        return bytes(self.mem.get(addr + i, 0) for i in range(size))

    def write(self, addr, data):
        if self.fail_all_writes or addr in self.fail_write:
            return False
        self.writes.append((addr, bytes(data)))
        self.put(addr, data)
        return True

    def protect_write(self, addr, data, *, flush_icache=True, page_protect=0x40):
        self.protect_writes.append((addr, bytes(data), flush_icache, page_protect))
        if self.fail_protect:
            return False
        if self.protect_stores:
            self.put(addr, data)
        return True

    def run_remote_shellcode(self, sc, label):
        self.shellcode.append((bytes(sc), label))
        return self.shellcode_ok

    def put(self, addr, data):
        for i, b in enumerate(bytes(data)):
            self.mem[addr + i] = b

    def get(self, addr, n):
        return bytes(self.mem.get(addr + i, 0) for i in range(n))


# --------------------------------------------------------------------------- savemap builders


SM_ADDR = ff7.ADDR_SAVEMAP_BASE


def blank_savemap() -> bytearray:
    sm = bytearray(ff7.SAVEMAP_SIZE)
    sm[ff7.SAVE_OFF_INV_ITEMS: ff7.SAVE_OFF_INV_ITEMS + ff7.INV_ITEM_SLOTS * 2] = b"\xff" * (ff7.INV_ITEM_SLOTS * 2)
    sm[ff7.SAVE_OFF_INV_MATERIA: ff7.SAVE_OFF_INV_MATERIA + ff7.INV_MATERIA_SLOTS * 4] = b"\xff" * (ff7.INV_MATERIA_SLOTS * 4)
    for off in ff7._CHAR_BLOCK:
        sm[off + 0x40: off + 0x80] = b"\xff" * 0x40
        sm[off + ff7.REC_OFF_ACCESSORY] = 0xFF
        sm[off + ff7.REC_OFF_ROW] = ff7.ROW_ORDER_FRONT
    sm[ff7.SAVE_OFF_PARTY_SLOTS: ff7.SAVE_OFF_PARTY_SLOTS + 3] = b"\xff\xff\xff"
    sm[ff7.SAVE_OFF_GIL: ff7.SAVE_OFF_GIL + 4] = struct.pack("<I", 1000)
    return sm


def put_char(sm, cid, *, name="Cloud", level=7, hp=100, max_hp=200, mp=10, max_mp=20, limit=50, weapon=0, armor=0,
             acc=0xFF, field_status=0, row=ff7.ROW_ORDER_FRONT):
    off = ff7._CHAR_BLOCK[cid]
    sm[off + ff7.REC_OFF_LEVEL] = level
    sm[off + 0x0F] = limit
    sm[off + ff7.REC_OFF_NAME: off + ff7.REC_OFF_NAME + 12] = ff7._encode_ff7_name(name)
    struct.pack_into("<H", sm, off + ff7.REC_OFF_HP, hp)
    struct.pack_into("<H", sm, off + ff7.REC_OFF_MAX_HP, max_hp)
    struct.pack_into("<H", sm, off + ff7.REC_OFF_MP, mp)
    struct.pack_into("<H", sm, off + ff7.REC_OFF_MAX_MP, max_mp)
    sm[off + ff7.REC_OFF_WEAPON] = weapon
    sm[off + ff7.REC_OFF_ARMOR] = armor
    sm[off + ff7.REC_OFF_ACCESSORY] = acc
    sm[off + ff7.REC_OFF_FIELD_STATUS] = field_status
    sm[off + ff7.REC_OFF_ROW] = row
    return off


def standard_savemap() -> bytearray:
    sm = blank_savemap()
    put_char(sm, 0, name="Cloud", level=10)
    put_char(sm, 1, name="Barret", level=12, row=ff7.ROW_ORDER_BACK, field_status=0x10)
    put_char(sm, 2, name="Tifa", level=11, max_hp=0, max_mp=0, hp=80, mp=5)
    sm[ff7.SAVE_OFF_PARTY_SLOTS: ff7.SAVE_OFF_PARTY_SLOTS + 3] = bytes([0, 1, 2])
    return sm


@pytest.fixture
def mem():
    return FakeMemory()


@pytest.fixture
def hook(mem):
    return ff7.FF7Hook(memory=mem)


@pytest.fixture
def loaded(mem, hook):
    """Hook with the standard savemap in fake memory."""
    sm = standard_savemap()
    mem.put(SM_ADDR, sm)
    return sm


def battle_actor(mem, slot, *, status=0, hp=100, max_hp=200, mp=10, max_mp=20, level=0, flags=0, back=False):
    raw = bytearray(ff7.BATTLE_ACTOR_STRIDE)
    struct.pack_into("<I", raw, 0, status)
    raw[4] = 0x40 if back else 0
    raw[5] = flags
    struct.pack_into("<i", raw, ff7.BATTLE_OFF_HP, hp)
    struct.pack_into("<i", raw, ff7.BATTLE_OFF_MAX_HP, max_hp)
    struct.pack_into("<h", raw, ff7.BATTLE_OFF_MP, mp)
    struct.pack_into("<h", raw, ff7.BATTLE_OFF_MAX_MP, max_mp)
    raw[0x24] = level
    mem.put(ff7.ADDR_BATTLE_CHAR_BASE + slot * ff7.BATTLE_ACTOR_STRIDE, raw)


INV_ITEMS = SM_ADDR + ff7.SAVE_OFF_INV_ITEMS
INV_MAT = SM_ADDR + ff7.SAVE_OFF_INV_MATERIA
MODULE = ff7.ADDR_CURRENT_MODULE


def set_module(mem, value):
    mem.put(MODULE, bytes([value]))


def inv_item(sm, idx, item_id, qty):
    struct.pack_into("<H", sm, ff7.SAVE_OFF_INV_ITEMS + idx * 2, (item_id & 0x1FF) | ((qty & 0x7F) << 9))


def inv_materia(sm, idx, mid, ap=0):
    sm[ff7.SAVE_OFF_INV_MATERIA + idx * 4: ff7.SAVE_OFF_INV_MATERIA + idx * 4 + 4] = bytes([mid]) + ap.to_bytes(3, "little")


def char_materia(sm, cid, base, slot, mid, ap=0):
    off = ff7._CHAR_BLOCK[cid] + base + slot * 4
    sm[off: off + 4] = bytes([mid]) + ap.to_bytes(3, "little")


# --------------------------------------------------------------------------- recent item detection


def test_recent_item_ignores_missing_or_short_savemap(hook):
    hook._tick_recent_item_detection(None)
    hook._tick_recent_item_detection(b"\x00" * 100)
    assert hook._prev_inventory_sig is None


def test_recent_item_first_tick_records_baseline(hook):
    sm = standard_savemap()
    hook._tick_recent_item_detection(bytes(sm))
    assert hook._last_item_gain is None
    assert hook._prev_inventory_sig is not None and hook._prev_materia_inv_sig is not None
    assert hook._prev_equipped_unified_counts == {0x80: 3, 0x100: 3}


def test_recent_item_gain_new_and_existing(hook):
    sm = standard_savemap()
    inv_item(sm, 0, 1, 2)  # Hi-Potion x2
    hook._tick_recent_item_detection(bytes(sm))
    inv_item(sm, 0, 1, 3)
    inv_item(sm, 1, 0, 1)  # new Potion stack (delta 1) vs Hi-Potion +1: tie -> new stack wins
    hook._tick_recent_item_detection(bytes(sm))
    assert hook._last_item_gain == {"kind": "item", "name": "Potion", "delta": 1, "item_id": 0, "materia_id": None}
    # unchanged savemap: early return, gain kept
    hook._tick_recent_item_detection(bytes(sm))
    assert hook._last_item_gain["name"] == "Potion"
    inv_item(sm, 0, 1, 9)  # +6 Hi-Potion
    hook._tick_recent_item_detection(bytes(sm))
    assert hook._last_item_gain["name"] == "Hi-Potion" and hook._last_item_gain["delta"] == 6


def test_recent_item_lower_id_wins_tie(hook):
    sm = standard_savemap()
    hook._tick_recent_item_detection(bytes(sm))
    inv_item(sm, 0, 3, 1)
    inv_item(sm, 1, 2, 1)
    hook._tick_recent_item_detection(bytes(sm))
    assert hook._last_item_gain["item_id"] == 2


def test_recent_materia_gain(hook):
    sm = standard_savemap()
    hook._tick_recent_item_detection(bytes(sm))
    inv_materia(sm, 0, 0)  # MP Plus
    hook._tick_recent_item_detection(bytes(sm))
    assert hook._last_item_gain == {"kind": "materia", "name": "MP Plus", "delta": 1, "item_id": None, "materia_id": 0}


def test_recent_item_vs_materia(hook):
    sm = standard_savemap()
    hook._tick_recent_item_detection(bytes(sm))
    inv_item(sm, 0, 0, 2)
    inv_materia(sm, 0, 0)
    hook._tick_recent_item_detection(bytes(sm))
    assert hook._last_item_gain["kind"] == "item"
    inv_item(sm, 0, 0, 3)
    inv_materia(sm, 1, 1)
    inv_materia(sm, 2, 1)
    hook._tick_recent_item_detection(bytes(sm))
    assert hook._last_item_gain == {"kind": "materia", "name": "HP Plus", "delta": 2, "item_id": None,
                                    "materia_id": 1}


def test_recent_item_unequip_not_reported(hook):
    sm = standard_savemap()
    hook._tick_recent_item_detection(bytes(sm))
    # Cloud unequips Buster Sword: weapon byte cleared, sword lands in inventory
    sm[ff7._CHAR_BLOCK[0] + ff7.REC_OFF_WEAPON] = 0xFF
    inv_item(sm, 0, 0x80, 1)
    hook._tick_recent_item_detection(bytes(sm))
    assert hook._last_item_gain is None


def test_recent_materia_unequip_not_reported(hook):
    sm = standard_savemap()
    char_materia(sm, 0, 0x40, 0, 5)
    hook._tick_recent_item_detection(bytes(sm))
    char_materia(sm, 0, 0x40, 0, 0xFF)
    inv_materia(sm, 0, 5)
    hook._tick_recent_item_detection(bytes(sm))
    assert hook._last_item_gain is None
    # a loss is never reported either
    inv_materia(sm, 0, 0xFF)
    inv_item(sm, 3, 9, 0)
    hook._tick_recent_item_detection(bytes(sm))
    assert hook._last_item_gain is None


def test_recent_item_party_change_rechecks(hook):
    sm = standard_savemap()
    hook._tick_recent_item_detection(bytes(sm))
    sm[ff7.SAVE_OFF_PARTY_SLOTS + 2] = 0xFF  # party change only: re-evaluated, nothing gained
    hook._tick_recent_item_detection(bytes(sm))
    assert hook._last_item_gain is None
    assert hook._prev_party_slots_sig == bytes([0, 1, 0xFF])


# --------------------------------------------------------------------------- battle status log


def test_battle_status_log_transitions(hook):
    party = [{"party_slot": 0, "name": "Cloud", "status": 0}, {"slot": 1, "slot_empty": True}]
    enemies = [{"slot": 4, "name": "", "status": 0}]
    hook._tick_battle_status_log(party, enemies)
    assert hook._battle_log_lines == []
    party[0]["status"] = 0x1 | 0x20
    enemies[0]["status"] = 0x8
    hook._tick_battle_status_log(party, enemies)
    assert hook._battle_log_lines == ["Cloud died", "Cloud — inflicted with Fury", "? — inflicted with Poison"]
    party[0]["status"] = 0x20
    enemies[0]["status"] = 0x1
    hook._tick_battle_status_log(party, enemies)
    assert hook._battle_log_lines[3:] == ["Cloud — cleared of Death", "? died", "? — cleared of Poison"]
    assert hook._prev_battle_status == {"a0": 0x20, "e4": 0x1}


def test_battle_status_log_trims(hook):
    hook._battle_log_lines = ["x"] * 250
    hook._tick_battle_status_log([], [])
    assert len(hook._battle_log_lines) == 200


# --------------------------------------------------------------------------- battle command log


@pytest.fixture
def blog(hook, mem, monkeypatch):
    monkeypatch.setattr(ff7, "_BATTLE_LOG_CMD_EN", {})
    monkeypatch.setattr(ff7, "_BATTLE_LOG_ATK128_EN", {})
    cmds = [""] * 32
    cmds[1] = "Attack"
    hook._bl_name_cache = {"command": cmds, "attack": [""] * 128}
    return hook


def queue(mem, prio, qpos, att, cmd, param=0, tmask=0):
    mem.put(ff7.ADDR_BATTLE_QUEUE, bytes([prio, qpos, att, cmd]) + struct.pack("<HH", param, tmask))


def battle_obj(mem, dmg, miss=False, crit=False, ptr=0x02000000):
    mem.put(ff7.ADDR_BATTLE_OBJ_PTR, struct.pack("<I", ptr))
    blob = bytearray(0x264)
    struct.pack_into("<i", blob, 0x214, dmg)
    blob[0x218] = 1 if miss else 0
    blob[0x220] = 2 if crit else 0
    mem.put(ptr, blob)


PARTY = [{"party_slot": 0, "name": "Cloud", "status": 0}]
ENEMIES = [{"slot": 4, "name": "Grunt", "status": 0}]


def test_battle_log_records_queue_entry(blog, mem):
    queue(mem, 1, 2, 0, 0x01, 0, 0b10000)
    battle_obj(mem, 42, crit=True)
    blog._tick_battle_log(True, PARTY, ENEMIES, True)
    assert blog._battle_log_lines == ["Cloud used Attack on Grunt, dealt 42 damage [crit]"]
    assert blog._was_in_battle is True
    blog._tick_battle_log(True, PARTY, ENEMIES, True)  # same entry: deduped
    assert len(blog._battle_log_lines) == 1
    queue(mem, 1, 3, 4, 0x01, 0, 0b1)
    battle_obj(mem, 0, miss=True)
    blog._tick_battle_log(True, PARTY, ENEMIES, True)
    assert blog._battle_log_lines[-1] == "Grunt used Attack on Cloud, missed"


def test_battle_log_bad_battle_object_pointer(blog, mem):
    queue(mem, 1, 2, 0, 0x01, 0, 0b10000)
    battle_obj(mem, 99, ptr=0x10)  # out of the plausible range -> 0 damage
    blog._tick_battle_log(True, PARTY, ENEMIES, True)
    assert blog._battle_log_lines == ["Cloud used Attack on Grunt, dealt 0 damage"]


def test_battle_log_short_blob(blog, mem, monkeypatch):
    queue(mem, 1, 2, 0, 0x01, 0, 0)
    mem.put(ff7.ADDR_BATTLE_OBJ_PTR, struct.pack("<I", 0x02000000))
    real = blog._read
    monkeypatch.setattr(blog, "_read", lambda a, n: b"\x00" * 16 if n == 0x264 else real(a, n))
    blog._tick_battle_log(True, PARTY, ENEMIES, True)
    assert blog._battle_log_lines == ["Cloud used Attack, dealt 0 damage"]


def test_battle_log_ignores_empty_and_ff(blog, mem, monkeypatch):
    blog._tick_battle_log(True, PARTY, ENEMIES, True)  # zero queue
    queue(mem, 1, 1, 0, 0xFF)
    blog._tick_battle_log(True, PARTY, ENEMIES, True)
    queue(mem, 1, 2, 0, 0x01)
    monkeypatch.setattr(blog, "_format_battle_log_line", lambda *a: "")
    blog._tick_battle_log(True, PARTY, ENEMIES, True)
    assert blog._battle_log_lines == []
    mem.fail_read.add(ff7.ADDR_BATTLE_QUEUE)
    blog._tick_battle_log(True, PARTY, ENEMIES, True)


def test_battle_log_resets_on_new_battle(blog, mem):
    blog._battle_log_lines = ["old"]
    blog._battle_log_seen = {(1, 1)}
    blog._battle_log_seen_order.append((1, 1))
    blog._prev_battle_status = {"a0": 5}
    blog._tick_battle_log(True, [], [], True)
    assert blog._battle_log_lines == [] and blog._battle_log_seen == set() and blog._prev_battle_status == {}


def test_battle_log_outside_battle(blog, mem):
    blog._was_in_battle = True
    blog._prev_battle_status = {"a0": 1}
    blog._tick_battle_log(False, [], [], True)  # UI still latched: keep state
    assert blog._was_in_battle is True and blog._prev_battle_status == {"a0": 1}
    blog._tick_battle_log(False, [], [], False)
    assert blog._was_in_battle is False and blog._prev_battle_status == {}


def test_battle_log_detached(blog, mem):
    mem.attached = False
    blog._tick_battle_log(True, [], [], True)
    assert blog._was_in_battle is False


def test_battle_log_seen_eviction_and_trim(blog, mem):
    for i in range(256):
        key = (i // 256 + 9, i % 256)
        blog._battle_log_seen.add(key)
        blog._battle_log_seen_order.append(key)
    blog._was_in_battle = True
    blog._battle_log_lines = ["x"] * 205
    queue(mem, 1, 2, 0, 0x01)
    blog._tick_battle_log(True, PARTY, ENEMIES, True)
    assert (9, 0) not in blog._battle_log_seen and (1, 2) in blog._battle_log_seen
    assert len(blog._battle_log_seen_order) == 256
    assert len(blog._battle_log_lines) == 200


def test_battle_log_trim_after_status(blog, mem, monkeypatch):
    blog._was_in_battle = True
    monkeypatch.setattr(blog, "_tick_battle_status_log", lambda p, e: blog._battle_log_lines.extend(["y"] * 300))
    blog._tick_battle_log(True, [], [], True)
    assert len(blog._battle_log_lines) == 200


# --------------------------------------------------------------------------- gil


def gil(mem):
    return struct.unpack("<I", mem.get(SM_ADDR + ff7.SAVE_OFF_GIL, 4))[0]


def test_add_remove_gil(hook, mem, loaded):
    assert hook._op_add_gil(0) == (False, "amount must be positive")
    assert hook._op_remove_gil(-1) == (False, "amount must be positive")
    assert hook._op_add_gil(500) == (True, None) and gil(mem) == 1500
    assert hook._op_add_gil(ff7._MAX_GIL) == (True, None) and gil(mem) == ff7._MAX_GIL
    assert hook._op_remove_gil(ff7._MAX_GIL + 5) == (True, None) and gil(mem) == 0
    mem.fail_write.add(SM_ADDR + ff7.SAVE_OFF_GIL)
    assert hook._op_add_gil(1) == (False, "WriteProcessMemory failed (gil)")
    assert hook._op_remove_gil(1) == (False, "WriteProcessMemory failed (gil)")
    mem.fail_read.add(SM_ADDR)
    assert hook._op_add_gil(1) == (False, "Savemap not readable")
    assert hook._op_remove_gil(1) == (False, "Savemap not readable")


def test_gil_short_savemap(hook, monkeypatch):
    monkeypatch.setattr(hook, "_read_savemap", lambda: (b"\x01" * 10, SM_ADDR))
    assert hook._op_add_gil(1) == (False, "Savemap not readable")
    assert hook._op_remove_gil(1) == (False, "Savemap not readable")


# --------------------------------------------------------------------------- party HP


def actor_i32(mem, slot, off):
    return struct.unpack("<i", mem.get(ff7.ADDR_BATTLE_CHAR_BASE + slot * ff7.BATTLE_ACTOR_STRIDE + off, 4))[0]


def test_battle_party_hp_write(hook, mem):
    set_module(mem, 1)
    assert hook._op_battle_party_hp_write(0, 10)[0] is False
    set_module(mem, 2)
    battle_actor(mem, 0, status=0x1 | 0x20, hp=0, max_hp=300)
    assert hook._op_battle_party_hp_write(0, 999) == (True, None)
    assert actor_i32(mem, 0, ff7.BATTLE_OFF_HP) == 300
    assert actor_i32(mem, 0, 0) == 0x20  # Death cleared on revive
    assert hook._op_battle_party_hp_write(0, -4) == (True, None)
    assert actor_i32(mem, 0, ff7.BATTLE_OFF_HP) == 0
    n = len(mem.writes)
    hook._op_battle_party_hp_write(0, 5)  # status already clear: no status write
    assert len(mem.writes) == n + 1


def test_battle_party_hp_write_failures(hook, mem):
    set_module(mem, 2)
    base = ff7.ADDR_BATTLE_CHAR_BASE
    battle_actor(mem, 0, status=1, max_hp=50)
    mem.fail_write.add(base)
    assert hook._op_battle_party_hp_write(0, 5) == (False, "WriteProcessMemory failed (battle status)")
    mem.fail_write = {base + ff7.BATTLE_OFF_HP}
    assert hook._op_battle_party_hp_write(0, 0) == (False, "WriteProcessMemory failed (battle HP)")
    mem.fail_read.add(base)
    assert hook._op_battle_party_hp_write(0, 5) == (False, "Read battle status failed")
    mem.fail_read.add(base + ff7.BATTLE_OFF_MAX_HP)
    assert hook._op_battle_party_hp_write(0, 5) == (False, "Read max HP failed")


def field_hp(mem, cid):
    return struct.unpack("<H", mem.get(SM_ADDR + ff7._CHAR_BLOCK[cid] + ff7.REC_OFF_HP, 2))[0]


def test_field_party_hp_write(hook, mem, loaded):
    sm = bytes(loaded)
    assert hook._op_field_party_hp_write(sm, 0, 5000) == (True, None)
    assert field_hp(mem, 0) == 200
    assert hook._op_field_party_hp_write(sm, 4, 1) == (False, "Invalid party slot or empty slot (field)")
    mem.fail_all_writes = True
    assert hook._op_field_party_hp_write(sm, 0, 5) == (False, "WriteProcessMemory failed (field HP)")


def test_field_party_hp_write_no_address(hook, monkeypatch, loaded):
    monkeypatch.setattr(hook, "_field_char_hp_addr", lambda sm, s: None)
    assert hook._op_field_party_hp_write(bytes(loaded), 0, 1) == (False, "Could not resolve field HP address")


def test_party_hp_rw_battle(hook, mem):
    assert hook._party_hp_rw(3, "add", 1) == (False, "slot must be 0–2")
    set_module(mem, 2)
    battle_actor(mem, 1, hp=100, max_hp=150)
    assert hook._party_hp_rw(1, "add", 80) == (True, None)
    assert actor_i32(mem, 1, ff7.BATTLE_OFF_HP) == 150
    assert hook._party_hp_rw(1, "remove", 500) == (True, None)
    assert actor_i32(mem, 1, ff7.BATTLE_OFF_HP) == 0
    assert hook._party_hp_rw(1, "set", 70) == (True, None)
    assert actor_i32(mem, 1, ff7.BATTLE_OFF_HP) == 70
    mem.fail_read.add(ff7.ADDR_BATTLE_CHAR_BASE + ff7.BATTLE_ACTOR_STRIDE + ff7.BATTLE_OFF_HP)
    assert hook._party_hp_rw(1, "add", 1) == (False, "Read battle HP failed")


def test_party_hp_rw_field(hook, mem, loaded):
    set_module(mem, 1)
    assert hook._party_hp_rw(0, "add", 500) == (True, None)
    assert field_hp(mem, 0) == 200
    assert hook._party_hp_rw(0, "remove", 30) == (True, None)
    assert field_hp(mem, 0) == 170
    assert hook._party_hp_rw(0, "set", 999) == (True, None)
    assert field_hp(mem, 0) == 200
    mem.put(SM_ADDR + ff7.SAVE_OFF_PARTY_SLOTS + 2, b"\xff")
    assert hook._party_hp_rw(2, "add", 1) == (False, "Read field HP failed")
    mem.fail_read.add(SM_ADDR)
    assert hook._party_hp_rw(0, "add", 1) == (False, "Savemap not readable")


def test_party_hp_rw_field_no_max(hook, mem, loaded, monkeypatch):
    set_module(mem, 1)
    monkeypatch.setattr(hook, "_field_char_max_hp", lambda sm, s: None)
    assert hook._party_hp_rw(0, "add", 1) == (False, "Field max HP unavailable")


# --------------------------------------------------------------------------- KO / enemies


def enemy_present(mem, slot, scene=0, hp=100, name="Grunt"):
    mem.put(ff7.ADDR_ENEMY_OBJ_BASE + (slot - 4) * ff7.ENEMY_OBJ_STRIDE, bytes([scene]))
    battle_actor(mem, slot, hp=hp, max_hp=hp)
    mem.put(ff7.ADDR_ENEMY_DATA_BASE + scene * ff7.ENEMY_DATA_STRIDE, ff7._encode_ff7_name(name, 24))


def no_enemies(mem):
    for s in range(6):
        mem.put(ff7.ADDR_ENEMY_OBJ_BASE + s * ff7.ENEMY_OBJ_STRIDE, b"\xff")


def test_kill_party_member(hook, mem):
    set_module(mem, 1)
    assert hook._op_kill_party_member(0)[0] is False
    set_module(mem, 2)
    battle_actor(mem, 0, hp=50, max_hp=50, status=0x20)
    assert hook._op_kill_party_member(0) == (True, None)
    assert actor_i32(mem, 0, 0) == ff7.STATUS_DEAD and actor_i32(mem, 0, ff7.BATTLE_OFF_HP) == 0
    mem.fail_write.add(ff7.ADDR_BATTLE_CHAR_BASE)
    assert hook._op_kill_party_member(0) == (False, "WriteProcessMemory failed (battle status Dead)")


def test_kill_all_enemies(hook, mem):
    set_module(mem, 1)
    assert hook._op_kill_all_enemies() == (False, "Not in battle")
    set_module(mem, 2)
    no_enemies(mem)
    enemy_present(mem, 4)
    enemy_present(mem, 6, scene=1)
    assert hook._op_kill_all_enemies() == (True, None)
    for s in (4, 6):
        assert actor_i32(mem, s, 0) == 1 and actor_i32(mem, s, ff7.BATTLE_OFF_HP) == 0
    a6 = ff7.ADDR_BATTLE_CHAR_BASE + 6 * ff7.BATTLE_ACTOR_STRIDE
    mem.fail_write.add(a6 + ff7.BATTLE_OFF_HP)
    assert hook._op_kill_all_enemies() == (False, "Write failed enemy HP slot 6")
    mem.fail_write.add(a6)
    assert hook._op_kill_all_enemies() == (False, "Write failed enemy status slot 6")


def test_damage_enemy(hook, mem):
    assert hook._op_damage_enemy(4, 0) == (False, "amount must be positive")
    set_module(mem, 1)
    assert hook._op_damage_enemy(4, 5) == (False, "Not in battle")
    set_module(mem, 2)
    assert hook._op_damage_enemy(3, 5) == (False, "enemy battle slot must be 4–9")
    no_enemies(mem)
    assert hook._op_damage_enemy(4, 5) == (False, "No enemy in that slot")
    enemy_present(mem, 4, hp=100)
    assert hook._op_damage_enemy(4, 30) == (True, None)
    assert actor_i32(mem, 4, ff7.BATTLE_OFF_HP) == 70 and actor_i32(mem, 4, 0) == 0
    assert hook._op_damage_enemy(4, 500) == (True, None)
    assert actor_i32(mem, 4, ff7.BATTLE_OFF_HP) == 0 and actor_i32(mem, 4, 0) == 1


def test_damage_enemy_failures(hook, mem, monkeypatch):
    set_module(mem, 2)
    no_enemies(mem)
    enemy_present(mem, 5, hp=10)
    a5 = ff7.ADDR_BATTLE_CHAR_BASE + 5 * ff7.BATTLE_ACTOR_STRIDE
    mem.fail_write.add(a5)
    assert hook._op_damage_enemy(5, 50) == (False, "Write enemy Death status failed")
    mem.fail_write = {a5 + ff7.BATTLE_OFF_HP}
    assert hook._op_damage_enemy(5, 1) == (False, "Write enemy HP failed")
    real = hook._read_i32
    monkeypatch.setattr(hook, "_read_i32", lambda a: None if a == a5 + ff7.BATTLE_OFF_HP else real(a))
    assert hook._op_damage_enemy(5, 1) == (False, "Read enemy HP failed")


def test_kill_enemy(hook, mem):
    set_module(mem, 1)
    assert hook._op_kill_enemy(4) == (False, "Not in battle")
    set_module(mem, 2)
    assert hook._op_kill_enemy(10) == (False, "enemy battle slot must be 4–9")
    no_enemies(mem)
    assert hook._op_kill_enemy(4) == (False, "No enemy in that slot")
    enemy_present(mem, 4)
    assert hook._op_kill_enemy(4) == (True, None)
    assert actor_i32(mem, 4, 0) == 1 and actor_i32(mem, 4, ff7.BATTLE_OFF_HP) == 0
    a4 = ff7.ADDR_BATTLE_CHAR_BASE + 4 * ff7.BATTLE_ACTOR_STRIDE
    mem.fail_write.add(a4 + ff7.BATTLE_OFF_HP)
    assert hook._op_kill_enemy(4) == (False, "Write enemy HP failed")
    mem.fail_write.add(a4)
    assert hook._op_kill_enemy(4) == (False, "Write enemy Death status failed")


# --------------------------------------------------------------------------- levels and stats


def test_set_party_level_field_and_battle(hook, mem, loaded):
    set_module(mem, 1)
    off = ff7._CHAR_BLOCK[1]
    assert hook._op_set_party_level("Barret", 150) == (True, None)
    assert mem.get(SM_ADDR + off + ff7.REC_OFF_LEVEL, 1) == bytes([99])
    assert mem.shellcode[-1][1] == "party stat recalc"
    set_module(mem, 2)
    mem.put(ff7.ADDR_PARTY_MEMBER_IDS, bytes([0, 1, 2]))
    assert hook._op_set_party_level("1", 0) == (True, None)
    assert mem.get(SM_ADDR + off + ff7.REC_OFF_LEVEL, 1) == bytes([1])
    assert mem.get(ff7.ADDR_BATTLE_CHAR_BASE + ff7.BATTLE_ACTOR_STRIDE + ff7.BATTLE_OFF_LEVEL, 1) == bytes([1])
    # in battle but character not in the battle party: no mirror
    n = len(mem.writes)
    assert hook._op_set_party_level("5", 3) == (True, None)
    assert len(mem.writes) == n + 1


def test_set_party_level_failures(hook, mem, loaded, monkeypatch):
    assert hook._op_set_party_level("Zed", 5) == (False, "unknown character name: Zed")
    mem.fail_write.add(SM_ADDR + ff7._CHAR_BLOCK[0] + ff7.REC_OFF_LEVEL)
    assert hook._op_set_party_level("0", 5) == (False, "write level failed")
    monkeypatch.setattr(hook, "_char_id_from_gear_character_token", lambda sm, t: (9, None))
    assert hook._op_set_party_level("x", 5) == (False, "bad character id")
    monkeypatch.setattr(hook, "_char_id_from_gear_character_token", lambda sm, t: (None, None))
    assert hook._op_set_party_level("x", 5) == (False, "character")
    mem.fail_read.add(SM_ADDR)
    assert hook._op_set_party_level("0", 5) == (False, "Savemap not readable")


def test_set_enemy_level(hook, mem):
    hook._last_snapshot = {"enemies": [{"slot": 4, "name": "Grunt", "scene_id": 2}, {"slot": 5, "name": "Ghost"}]}
    assert hook._op_set_enemy_level("Grunt", 200) == (True, None)
    assert mem.get(ff7.ADDR_ENEMY_DATA_BASE + 2 * ff7.ENEMY_DATA_STRIDE + ff7.ENEMY_OFF_LEVEL, 1) == bytes([99])
    assert hook._op_set_enemy_level("Ghost", 5) == (False, "no scene_id for enemy")
    assert hook._op_set_enemy_level("3", 5) == (False, "no scene_id for enemy")
    assert hook._op_set_enemy_level("", 5) == (False, "empty enemy token")
    mem.fail_all_writes = True
    assert hook._op_set_enemy_level("Grunt", 5) == (False, "write enemy level failed")


def test_set_enemy_level_no_snapshot(hook, monkeypatch):
    monkeypatch.setattr(hook, "_enemy_slot_from_token", lambda t: (4, None))
    assert hook._op_set_enemy_level("x", 1) == (False, "no scene_id for enemy")
    monkeypatch.setattr(hook, "_enemy_slot_from_token", lambda t: (None, None))
    assert hook._op_set_enemy_level("x", 1) == (False, "enemy")


def test_set_party_stat_bytes_and_words(hook, mem, loaded):
    set_module(mem, 1)
    off = SM_ADDR + ff7._CHAR_BLOCK[0]
    assert hook._op_set_party_stat("Cloud", "Strength", 300) == (True, None)
    assert mem.get(off + ff7.REC_OFF_STR, 1) == b"\xff"
    assert hook._op_set_party_stat("0", "luk-bonus", -5) == (True, None)
    assert mem.get(off + ff7.REC_OFF_LUK_BONUS, 1) == b"\x00"
    assert hook._op_set_party_stat("0", "Max HP", 20000) == (True, None)
    assert mem.get(off + 0x38, 2) == struct.pack("<H", 9999)
    assert hook._op_set_party_stat("0", "charm", 1) == (False, "unknown party stat: charm")
    assert mem.shellcode and mem.shellcode[-1][1] == "party stat recalc"


def test_set_party_stat_battle_mirror(hook, mem, loaded):
    set_module(mem, 2)
    mem.put(ff7.ADDR_PARTY_MEMBER_IDS, bytes([2, 0, 1]))
    actor = ff7.ADDR_BATTLE_CHAR_BASE + 1 * ff7.BATTLE_ACTOR_STRIDE
    for stat, moff, fmt, val in (("hp", ff7.BATTLE_OFF_HP, "<I", 150), ("maxhp", ff7.BATTLE_OFF_MAX_HP, "<I", 400),
                                 ("mp", ff7.BATTLE_OFF_MP, "<H", 40), ("max_mp", ff7.BATTLE_OFF_MAX_MP, "<H", 80)):
        assert hook._op_set_party_stat("Cloud", stat, val) == (True, None)
        assert mem.get(actor + moff, struct.calcsize(fmt)) == struct.pack(fmt, val)
    # not in the battle party: savemap only
    n = len(mem.writes)
    hook._op_set_party_stat("4", "hp", 1)
    assert len(mem.writes) == n + 1
    # byte stat in battle: no mirror
    n = len(mem.writes)
    hook._op_set_party_stat("0", "str", 1)
    assert len(mem.writes) == n + 1


def test_set_party_stat_failures(hook, mem, loaded, monkeypatch):
    assert hook._op_set_party_stat("Zed", "str", 1) == (False, "unknown character name: Zed")
    mem.fail_all_writes = True
    assert hook._op_set_party_stat("0", "str", 1) == (False, "write stat failed")
    monkeypatch.setattr(hook, "_char_id_from_gear_character_token", lambda sm, t: (9, None))
    assert hook._op_set_party_stat("x", "str", 1) == (False, "bad character id")
    monkeypatch.setattr(hook, "_char_id_from_gear_character_token", lambda sm, t: (None, None))
    assert hook._op_set_party_stat("x", "str", 1) == (False, "character")
    mem.fail_read.add(SM_ADDR)
    assert hook._op_set_party_stat("0", "str", 1) == (False, "Savemap not readable")


def test_set_enemy_stat_actor_values(hook, mem):
    hook._last_snapshot = {"enemies": [{"slot": 4, "name": "Grunt", "scene_id": 1}]}
    actor = ff7.ADDR_BATTLE_CHAR_BASE + 4 * ff7.BATTLE_ACTOR_STRIDE
    assert hook._op_set_enemy_stat("Grunt", "HP", 70000) == (True, None)
    assert mem.get(actor + ff7.BATTLE_OFF_HP, 4) == struct.pack("<I", 65535)
    hook._op_set_enemy_stat("Grunt", "max hp", 10)
    assert mem.get(actor + ff7.BATTLE_OFF_MAX_HP, 4) == struct.pack("<I", 10)
    hook._op_set_enemy_stat("Grunt", "mp", 5)
    assert mem.get(actor + ff7.BATTLE_OFF_MP, 2) == struct.pack("<H", 5)
    hook._op_set_enemy_stat("Grunt", "maxmp", 6)
    assert mem.get(actor + ff7.BATTLE_OFF_MAX_MP, 2) == struct.pack("<H", 6)
    mem.fail_all_writes = True
    assert hook._op_set_enemy_stat("Grunt", "hp", 1) == (False, "write failed")


def test_set_enemy_stat_record_values(hook, mem):
    hook._last_snapshot = {"enemies": [{"slot": 4, "name": "Grunt", "scene_id": 1}, {"slot": 5, "name": "Ghost"}]}
    rec = ff7.ADDR_ENEMY_DATA_BASE + 1 * ff7.ENEMY_DATA_STRIDE
    assert hook._op_set_enemy_stat("Grunt", "defense", 101) == (True, None)
    assert mem.get(rec + ff7.ENEMY_OFF_DEF, 1) == bytes([50])
    hook._op_set_enemy_stat("Grunt", "magic-defense", -10)
    assert mem.get(rec + ff7.ENEMY_OFF_MDEF, 1) == b"\x00"
    hook._op_set_enemy_stat("Grunt", "speed", 999)
    assert mem.get(rec + ff7.ENEMY_OFF_SPEED, 1) == b"\xff"
    assert hook._op_set_enemy_stat("Grunt", "charm", 1) == (False, "unknown enemy stat: charm")
    assert hook._op_set_enemy_stat("Ghost", "luck", 1) == (False, "no scene_id for enemy")
    assert hook._op_set_enemy_stat("Nobody", "luck", 1) == (False, "enemy not found: Nobody")
    mem.fail_all_writes = True
    assert hook._op_set_enemy_stat("Grunt", "luck", 1) == (False, "write enemy stat failed")


def test_set_enemy_stat_no_snapshot(hook, monkeypatch):
    monkeypatch.setattr(hook, "_enemy_slot_from_token", lambda t: (4, None))
    assert hook._op_set_enemy_stat("x", "luck", 1) == (False, "no scene_id for enemy")
    monkeypatch.setattr(hook, "_enemy_slot_from_token", lambda t: (None, None))
    assert hook._op_set_enemy_stat("x", "luck", 1) == (False, "enemy")


# --------------------------------------------------------------------------- menu colors


def test_set_menu_colors_all_sites(hook, mem, loaded):
    assert hook._op_set_menu_colors("top", "#102030") == (True, None)
    sm_ul = SM_ADDR + ff7.SAVE_OFF_WIN_UL
    assert mem.get(sm_ul, 3) == bytes([0x10, 0x20, 0x30])
    assert mem.get(SM_ADDR + ff7.SAVE_OFF_WIN_UR, 3) == bytes([0x10, 0x20, 0x30])
    assert mem.get(ff7.ADDR_MENU_COLOR_DISPLAY_BASE + 8, 4) == bytes([0x30, 0x20, 0x10, 0x80])
    assert mem.get(ff7.ADDR_MENU_COLOR_SAVE_MIRROR_BASE + 6, 3) == bytes([0x10, 0x20, 0x30])
    assert mem.get(SM_ADDR + ff7.SAVE_OFF_WIN_LL, 3) == b"\x00\x00\x00"  # untouched
    assert all(p[2] is False and p[3] == ff7._PAGE_READWRITE for p in mem.protect_writes)
    assert len(mem.protect_writes) == 6


def test_set_menu_colors_validation(hook, mem, loaded):
    assert hook._op_set_menu_colors("middle", "red") == (False, "unknown menu target: middle")
    assert hook._op_set_menu_colors("all", "plaid") == (False, "unknown color: plaid")
    mem.fail_read.add(SM_ADDR + ff7.SAVE_OFF_GIL)
    assert hook._op_set_menu_colors("all", "red")[1].startswith("savemap unreachable")
    mem.attached = False
    assert hook._op_set_menu_colors("all", "red") == (False, "Not attached")


def test_set_menu_colors_unknown_corner_skipped(hook, mem, loaded, monkeypatch):
    monkeypatch.setitem(ff7._MENU_CORNERS, "weird", ["zz", "ul"])
    assert hook._op_set_menu_colors("weird", "red") == (True, None)
    assert len(mem.protect_writes) == 3


@pytest.mark.parametrize("site,err", [(0, "savemap write failed for corner ul"),
                                      (1, "display palette write failed for corner ul"),
                                      (2, "palette mirror write failed for corner ul")])
def test_set_menu_colors_write_failures(hook, mem, loaded, monkeypatch, site, err):
    calls = {"n": 0}
    real = mem.protect_write

    def pw(addr, data, **kw):
        n = calls["n"]
        calls["n"] += 1
        if n == site:
            return False
        return real(addr, data, **kw)

    monkeypatch.setattr(mem, "protect_write", pw)
    assert hook._op_set_menu_colors("ul", "red") == (False, err)


@pytest.mark.parametrize("site,err", [(0, "menu color savemap read-back failed for corner ul"),
                                      (1, "menu display read-back failed for corner ul"),
                                      (2, "menu mirror read-back failed for corner ul")])
def test_set_menu_colors_readback_failures(hook, mem, loaded, site, err):
    targets = [SM_ADDR + ff7.SAVE_OFF_WIN_UL, ff7.ADDR_MENU_COLOR_DISPLAY_BASE, ff7.ADDR_MENU_COLOR_SAVE_MIRROR_BASE]
    mem.fail_read.add(targets[site])
    assert hook._op_set_menu_colors("ul", "red") == (False, err)


# --------------------------------------------------------------------------- materia equip


def slot_bytes(mem, cid, base, slot):
    return mem.get(SM_ADDR + ff7._CHAR_BLOCK[cid] + base + slot * 4, 4)


def test_equip_materia_swap(hook, mem, loaded):
    sm = bytearray(loaded)
    char_materia(sm, 0, 0x40, 1, 2, ap=500)   # currently Speed Plus in slot 1
    inv_materia(sm, 0, 0, ap=1234)            # MP Plus in inventory
    mem.put(SM_ADDR, sm)
    assert hook._op_equip_materia("Cloud", "Weapon", 1, "MP Plus") == (True, None)
    assert slot_bytes(mem, 0, 0x40, 1) == bytes([0]) + (1234).to_bytes(3, "little")
    assert hook._inv_read_materia_slot(0) is None
    assert hook._inv_read_materia_slot(1) == (2, 500)  # displaced materia back in inventory
    assert mem.shellcode[-1][1] == "party stat recalc"


def test_equip_materia_into_empty_armor_slot(hook, mem, loaded):
    sm = bytearray(loaded)
    inv_materia(sm, 3, 1, ap=7)
    mem.put(SM_ADDR, sm)
    assert hook._op_equip_materia("0", "armor", 0, "HP Plus") == (True, None)
    assert slot_bytes(mem, 0, 0x60, 0) == bytes([1, 7, 0, 0])
    assert hook._inv_first_empty_materia_slot() == 0


def test_equip_materia_unequip(hook, mem, loaded):
    sm = bytearray(loaded)
    char_materia(sm, 0, 0x40, 0, 2, ap=9)
    mem.put(SM_ADDR, sm)
    for tok in ("none", ""):
        assert hook._op_equip_materia("0", "weapon", 0, tok) == (True, None)
        assert slot_bytes(mem, 0, 0x40, 0) == b"\xff" * 4
    assert hook._inv_read_materia_slot(0) == (2, 9)
    assert hook._inv_read_materia_slot(1) is None  # second unequip of an empty slot adds nothing


def test_equip_materia_validation(hook, mem, loaded, monkeypatch):
    assert hook._op_equip_materia("Zed", "weapon", 0, "x") == (False, "unknown character name: Zed")
    assert hook._op_equip_materia("0", "hat", 0, "x") == (False, "gear_kind must be weapon|armor")
    assert hook._op_equip_materia("0", "weapon", 8, "x") == (False, "materia slot must be 0-7")
    assert hook._op_equip_materia("0", "weapon", 0, "Nope") == (False, "unknown materia: Nope")
    assert hook._op_equip_materia("0", "weapon", 0, "MP Plus") == (False, "materia not in inventory: MP Plus")
    mem.fail_read.add(SM_ADDR + ff7._CHAR_BLOCK[0] + 0x40)
    assert hook._op_equip_materia("0", "weapon", 0, "MP Plus") == (False, "read current materia failed")
    monkeypatch.setattr(hook, "_char_id_from_gear_character_token", lambda sm, t: (9, None))
    assert hook._op_equip_materia("x", "weapon", 0, "x") == (False, "bad character id")
    monkeypatch.setattr(hook, "_char_id_from_gear_character_token", lambda sm, t: (None, None))
    assert hook._op_equip_materia("x", "weapon", 0, "x") == (False, "character")
    mem.fail_read.add(SM_ADDR)
    assert hook._op_equip_materia("0", "weapon", 0, "x") == (False, "Savemap not readable")


def test_equip_materia_inventory_full_and_write_failures(hook, mem, loaded, monkeypatch):
    sm = bytearray(loaded)
    char_materia(sm, 0, 0x40, 0, 2)
    for i in range(ff7.INV_MATERIA_SLOTS):
        inv_materia(sm, i, 0)
    mem.put(SM_ADDR, sm)
    assert hook._op_equip_materia("0", "weapon", 0, "none") == (False, "inventory full (materia)")
    assert hook._op_equip_materia("0", "weapon", 0, "MP Plus") == (False, "inventory full (materia)")
    slot_addr = SM_ADDR + ff7._CHAR_BLOCK[0] + 0x40
    char_materia(sm, 0, 0x40, 0, 0xFF)
    inv_materia(sm, 1, 0xFF)
    mem.put(SM_ADDR, sm)
    mem.fail_write.add(slot_addr)
    assert hook._op_equip_materia("0", "weapon", 0, "none") == (False, "write materia slot failed")
    assert hook._op_equip_materia("0", "weapon", 0, "MP Plus") == (False, "write materia slot failed")
    found = hook._inv_find_materia_slot(0)
    mem.fail_write.add(INV_MAT + found * 4)
    assert hook._op_equip_materia("0", "weapon", 0, "MP Plus") == (False, "remove from inventory failed")


# --------------------------------------------------------------------------- gear swap with inventory


@pytest.fixture
def no_allowlist(monkeypatch):
    monkeypatch.setattr(ff7, "_EQUIP_ALLOW", {})
    monkeypatch.setattr(ff7, "_EQUIP_ALLOW_LOADED", True)


def test_gear_swap_weapon(hook, mem, loaded, no_allowlist):
    sm = bytearray(loaded)
    inv_item(sm, 0, 0x81, 1)  # Mythril Saber
    mem.put(SM_ADDR, sm)
    assert hook._op_set_character_gear_with_inventory("Cloud", "weapon", 1) == (True, None)
    assert mem.get(SM_ADDR + ff7._CHAR_BLOCK[0] + ff7.REC_OFF_WEAPON, 1) == b"\x01"
    assert hook._inv_read_item_slot(0) == (0x80, 1)  # Buster Sword returned
    assert mem.shellcode[-1][1] == "party stat recalc"


@pytest.mark.parametrize("kind,rec_off,base_id,old", [("armor", ff7.REC_OFF_ARMOR, 0x100, 0x100),
                                                      ("accessory", ff7.REC_OFF_ACCESSORY, 0x120, None)])
def test_gear_swap_armor_accessory(hook, mem, loaded, no_allowlist, kind, rec_off, base_id, old):
    sm = bytearray(loaded)
    inv_item(sm, 0, base_id + 2, 2)
    mem.put(SM_ADDR, sm)
    assert hook._op_set_character_gear_with_inventory("0", kind, 2) == (True, None)
    assert mem.get(SM_ADDR + ff7._CHAR_BLOCK[0] + rec_off, 1) == b"\x02"
    assert hook._inv_read_item_slot(0) == (base_id + 2, 1)
    assert (hook._inv_read_item_slot(1) or (None,))[0] == old


def test_gear_swap_old_byte_unmappable(hook, mem, loaded, no_allowlist):
    sm = bytearray(loaded)
    sm[ff7._CHAR_BLOCK[0] + ff7.REC_OFF_ARMOR] = 0x40  # not a valid armor byte
    inv_item(sm, 0, 0x101, 1)
    mem.put(SM_ADDR, sm)
    assert hook._op_set_character_gear_with_inventory("0", "armor", 1) == (True, None)
    assert hook._inv_first_empty_item_slot() == 0


def test_gear_swap_validation(hook, mem, loaded, monkeypatch, no_allowlist):
    f = hook._op_set_character_gear_with_inventory
    assert f("Zed", "weapon", 1) == (False, "unknown character name: Zed")
    assert f("0", "hat", 1) == (False, "gear_kind must be weapon|armor|accessory")
    assert f("0", "weapon", 128) == (False, "weapon id out of range")
    assert f("0", "armor", -1) == (False, "armor id out of range")
    assert f("0", "weapon", 3) == (False, "gear not in inventory")
    monkeypatch.setattr(ff7, "_EQUIP_ALLOW", {"0": {"weapon": [5]}})
    assert f("0", "weapon", 3)[1].startswith("gear not allowed")
    monkeypatch.setattr(ff7, "_EQUIP_ALLOW", {})
    monkeypatch.setattr(hook, "_char_id_from_gear_character_token", lambda sm, t: (9, None))
    assert f("x", "weapon", 1) == (False, "bad character id")
    monkeypatch.setattr(hook, "_char_id_from_gear_character_token", lambda sm, t: (None, "nope"))
    assert f("x", "weapon", 1) == (False, "nope")
    mem.fail_read.add(SM_ADDR)
    assert f("0", "weapon", 1) == (False, "Savemap not readable")


def test_gear_swap_write_failures(hook, mem, loaded, monkeypatch, no_allowlist):
    sm = bytearray(loaded)
    inv_item(sm, 0, 0x81, 3)
    mem.put(SM_ADDR, sm)
    rec = SM_ADDR + ff7._CHAR_BLOCK[0] + ff7.REC_OFF_WEAPON
    mem.fail_write.add(rec)
    assert hook._op_set_character_gear_with_inventory("0", "weapon", 1) == (False, "write gear failed")
    monkeypatch.setattr(hook, "_inv_remove_item", lambda iid, q: False)
    assert hook._op_set_character_gear_with_inventory("0", "weapon", 1) == (False, "remove from inventory failed")


@pytest.mark.xfail(
    strict=True,
    reason="BUG: _op_set_character_gear_with_inventory ignores _inv_add_item's result; with a full "
    "inventory the previously equipped gear is silently destroyed and the op still reports success",
)
def test_gear_swap_full_inventory_keeps_old_gear(hook, mem, loaded, no_allowlist):
    sm = bytearray(loaded)
    for i in range(ff7.INV_ITEM_SLOTS):
        inv_item(sm, i, 0x90 + (i % 0x40) if i else 0x81, 5)  # every slot used; Mythril Saber x5 in slot 0
    mem.put(SM_ADDR, sm)
    ok, _ = hook._op_set_character_gear_with_inventory("Cloud", "weapon", 1)
    # Either refuse the swap, or keep the Buster Sword somewhere: it must not vanish.
    buster_in_inv = any((hook._inv_read_item_slot(i) or (None,))[0] == 0x80 for i in range(ff7.INV_ITEM_SLOTS))
    equipped = mem.get(SM_ADDR + ff7._CHAR_BLOCK[0] + ff7.REC_OFF_WEAPON, 1)[0]
    assert (not ok and equipped == 0) or buster_in_inv


# --------------------------------------------------------------------------- start battle


def test_consume_pending_battle(hook):
    hook._pending_battle_id = 12
    assert hook.consume_pending_battle() == 12
    assert hook.consume_pending_battle() is None


def test_start_battle_field(hook, mem, monkeypatch):
    monkeypatch.setattr(ff7.time, "monotonic", lambda: 100.0)
    set_module(mem, 1)
    field_obj = 0x03000000
    mem.put(ff7.ADDR_FIELD_OBJ_PTR, struct.pack("<I", field_obj))
    assert hook._start_battle_now(0x10123) == (True, None)
    assert mem.get(field_obj + 1, 1) == b"\x02"
    assert mem.get(field_obj + 2, 2) == struct.pack("<H", 0x0123)
    assert mem.get(field_obj + 38, 2) == b"\x00\x00"
    assert mem.get(ff7.ADDR_BATTLE_MODULE_FIELD, 1) == b"\x01"
    assert hook._post_success_timed == [("_finish_field_battle_start", {"field_obj": field_obj, "deadline": 101.0}, 0.05)]


@pytest.mark.parametrize("fail_off,err", [(1, "write field game module (battle) failed"),
                                          (2, "write field battle id failed"),
                                          (38, "write field battle result reset failed"),
                                          (None, "write battle_module_field failed")])
def test_start_battle_field_failures(hook, mem, fail_off, err):
    set_module(mem, 1)
    field_obj = 0x03000000
    mem.put(ff7.ADDR_FIELD_OBJ_PTR, struct.pack("<I", field_obj))
    mem.fail_write.add(field_obj + fail_off if fail_off is not None else ff7.ADDR_BATTLE_MODULE_FIELD)
    assert hook._start_battle_now(5) == (False, err)


def test_start_battle_field_no_object(hook, mem):
    set_module(mem, 1)
    assert hook._start_battle_now(5) == (False, "field object pointer missing")


def test_start_battle_world(hook, mem):
    set_module(mem, 3)
    assert hook._start_battle_now(77) == (True, None)
    assert mem.get(ff7.ADDR_BATTLE_ID_WORLD, 4) == struct.pack("<I", 77)
    assert mem.get(ff7.ADDR_WORLD_BATTLE_FLAG3, 4) == struct.pack("<I", 1)
    assert mem.get(ff7.ADDR_WORLD_BATTLE_FLAG4, 4) == struct.pack("<I", 3)


@pytest.mark.parametrize("addr,err", [(ff7.ADDR_BATTLE_ID_WORLD, "write world battle id failed"),
                                      (ff7.ADDR_WORLD_BATTLE_FLAG1, "write world_battle_flag1 failed"),
                                      (ff7.ADDR_WORLD_BATTLE_FLAG2, "write world_battle_flag2 failed"),
                                      (ff7.ADDR_WORLD_BATTLE_FLAG3, "write world_battle_flag3 failed"),
                                      (ff7.ADDR_WORLD_BATTLE_FLAG4, "write world_battle_flag4 failed")])
def test_start_battle_world_failures(hook, mem, addr, err):
    set_module(mem, 3)
    mem.fail_write.add(addr)
    assert hook._start_battle_now(1) == (False, err)


def test_start_battle_other_module_and_detached(hook, mem):
    set_module(mem, 5)
    assert hook._start_battle_now(1) == (False, "cannot start battle in module 5")
    mem.attached = False
    assert hook._start_battle_now(1) == (False, "Not attached")


def test_finish_field_battle_start(hook, mem, monkeypatch):
    clock = {"t": 10.0}
    monkeypatch.setattr(ff7.time, "monotonic", lambda: clock["t"])
    assert hook._finish_field_battle_start({}) == (True, None)
    set_module(mem, 2)
    assert hook._finish_field_battle_start({"field_obj": 0x3000, "deadline": 11.0}) == (True, None)
    assert mem.get(0x3001, 1) == b"\x00"
    set_module(mem, 1)
    assert hook._finish_field_battle_start({"field_obj": 0x3000, "deadline": 11.0}) == (True, None)
    assert hook._post_success_timed == [("_finish_field_battle_start", {"field_obj": 0x3000, "deadline": 11.0}, 0.05)]
    clock["t"] = 12.0
    hook._post_success_timed.clear()
    hook._finish_field_battle_start({"field_obj": 0x3000, "deadline": 11.0})
    assert hook._post_success_timed == []
    mem.attached = False
    assert hook._finish_field_battle_start({"field_obj": 0x3000}) == (True, None)


def test_op_start_battle(hook, mem, monkeypatch):
    calls = []
    monkeypatch.setattr(hook, "_start_battle_now", lambda bid: calls.append(bid) or (True, None))
    set_module(mem, 3)
    assert hook._op_start_battle(0x10005) == (True, None)
    assert calls == [5]
    set_module(mem, 5)
    assert hook._op_start_battle(9) == (True, None)
    assert hook._pending_battle_id == 9 and calls == [5]


# --------------------------------------------------------------------------- add item / materia / gear


def test_add_item(hook, mem):
    assert hook._op_add_item("Potion", 500) == (True, None)
    sc, label = mem.shellcode[-1]
    assert sc[1:5] == struct.pack("<I", 0 | (99 << 9)) and label == f"call fn {ff7.ADDR_PARTY_ADD_ITEM_FN:#x}"
    hook._op_add_item("Buster Sword", 0)
    assert mem.shellcode[-1][0][1:5] == struct.pack("<I", 0x80 | (1 << 9))
    assert hook._op_add_item("Nothing", 1) == (False, "unknown gear: Nothing")
    mem.shellcode_ok = False
    assert hook._op_add_item("Potion", 1) == (False, "party_add_item_fn failed")


def test_add_item_none_id(hook, monkeypatch):
    monkeypatch.setattr(hook, "_item_name_to_id", lambda n: (None, None))
    assert hook._op_add_item("x", 1) == (False, "item")


def test_add_materia(hook, mem):
    assert hook._op_add_materia("MP Plus", 2) == (True, None)
    assert mem.shellcode[-1][0][1:5] == struct.pack("<I", 0 | (2000 << 8))
    hook._op_add_materia("HP Plus", 6)
    assert mem.shellcode[-1][0][1:5] == struct.pack("<I", 1 | (0xFFFFFF << 8))
    hook._op_add_materia("HP Plus", 2500)
    assert mem.shellcode[-1][0][1:5] == struct.pack("<I", 1 | (2500 << 8))
    hook._op_add_materia("HP Plus", None)
    assert mem.shellcode[-1][0][1:5] == struct.pack("<I", 1)
    assert hook._op_add_materia("Nope", 1) == (False, "unknown materia: Nope")
    mem.shellcode_ok = False
    assert hook._op_add_materia("MP Plus", 1) == (False, "party_add_materia_fn failed")


def test_add_materia_none_id(hook, monkeypatch):
    monkeypatch.setattr(hook, "_materia_name_to_id", lambda n: (None, None))
    assert hook._op_add_materia("x", 1) == (False, "materia")


def test_add_gear(hook, mem, monkeypatch):
    assert hook._op_add_gear("Earring") == (True, None)
    assert mem.shellcode[-1][0][1:5] == struct.pack("<I", 0x122 | (1 << 9))
    assert hook._op_add_gear("Potion") == (False, "unknown gear: Potion")
    mem.shellcode_ok = False
    assert hook._op_add_gear("Earring") == (False, "party_add_item_fn failed")
    monkeypatch.setattr(hook, "_gear_name_to_item_id", lambda n: (None, None))
    assert hook._op_add_gear("x") == (False, "gear")


# --------------------------------------------------------------------------- inventory query


def test_query_inventory_items(hook, mem, loaded):
    sm = bytearray(loaded)
    inv_item(sm, 0, 0x80, 2)   # two Buster Swords in the bag
    inv_item(sm, 5, 0x80, 1)
    mem.put(SM_ADDR, sm)
    assert hook._op_query_inventory(" Buster Sword ") == (True, None)
    q = hook._last_inventory_query
    # 3 in inventory + 3 equipped (each party member's weapon byte 0 = Buster Sword)
    assert q == {"item_name": "Buster Sword", "resolved_name": "Buster Sword", "kind": "unified_item",
                 "item_id": 0x80, "materia_id": None, "quantity": 6, "error": None}


def test_query_inventory_materia(hook, mem, loaded):
    sm = bytearray(loaded)
    inv_materia(sm, 0, 1)
    char_materia(sm, 1, 0x60, 2, 1)
    mem.put(SM_ADDR, sm)
    assert hook._op_query_inventory("hp plus") == (True, None)
    q = hook._last_inventory_query
    assert q["kind"] == "materia" and q["materia_id"] == 1 and q["quantity"] == 2 and q["resolved_name"] == "HP Plus"


def test_query_inventory_errors(hook, mem, loaded, monkeypatch):
    assert hook._op_query_inventory("  ") == (False, "empty item name")
    assert hook._last_inventory_query["error"] == "empty item name"
    assert hook._op_query_inventory("Unobtainium") == (False, "unknown gear: Unobtainium")
    assert hook._last_inventory_query["quantity"] is None
    monkeypatch.setattr(hook, "_item_name_to_id", lambda n: (None, None))
    monkeypatch.setattr(hook, "_materia_name_to_id", lambda n: (None, "  "))
    assert hook._op_query_inventory("x") == (False, "unknown item: x")
    mem.fail_read.add(SM_ADDR)
    assert hook._op_query_inventory("Potion") == (False, "Savemap not readable")
    assert hook._last_inventory_query["error"] == "Savemap not readable"


def test_gear_swap_accessory_returns_old_accessory(hook, mem, loaded, no_allowlist):
    sm = bytearray(loaded)
    sm[ff7._CHAR_BLOCK[0] + ff7.REC_OFF_ACCESSORY] = 3
    inv_item(sm, 0, 0x125, 1)
    mem.put(SM_ADDR, sm)
    assert hook._op_set_character_gear_with_inventory("0", "accessory", 5) == (True, None)
    assert hook._inv_read_item_slot(0) == (0x123, 1)
