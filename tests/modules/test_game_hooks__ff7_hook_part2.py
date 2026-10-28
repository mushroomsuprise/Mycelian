# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/game_hooks/ff7_hook.py, part 2: FF7Hook attach/read helpers, battle-log text,
savemap and battle parsers, robust writes, FFNx trampoline, token parsers, name lookups, inventory
slot helpers and the simple write ops (status, menu words, game speed, battle speed, ATB, infinite
items). Memory is a fake byte store; nothing attaches to a real process.
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


# --------------------------------------------------------------------------- construction / attach


def test_init_uses_backend_when_no_memory(monkeypatch):
    sentinel = FakeMemory()
    monkeypatch.setattr(ff7, "get_memory_backend", lambda: sentinel)
    h = ff7.FF7Hook()
    assert h._mem is sentinel
    assert h._last_snapshot is None and h._battle_log_lines == [] and h._infinite_items_active is False


def test_consume_timed_schedules(hook):
    hook._post_success_timed.append(("op", {}, 3))
    assert hook.consume_timed_schedules() == [("op", {}, 3)]
    assert hook.consume_timed_schedules() == []


def test_proc_property(hook, mem):
    p = hook._proc
    assert (p.pid, p.handle, p.module_base) == (4242, 7, BASE)
    mem.attached = False
    assert hook._proc is None
    hook._mem = None
    assert hook._proc is None


def test_close_resets_state(hook, mem):
    hook._battle_ui_latched = True
    hook._battle_ui_off_ticks = 3
    hook._bl_name_cache = {}
    hook.close()
    assert mem.closed == 1 and hook._battle_ui_latched is False and hook._battle_ui_off_ticks == 0
    assert hook._bl_name_cache is None
    hook._mem = None
    hook.close()  # no memory: still resets


def test_attach_paths(hook, mem):
    assert hook._attach() == (True, None)
    assert mem.attach_calls[-1] == ("attach", ("ff7_en.exe", "ff7.exe"), BASE)
    mem.attach_result = (False, None)
    assert hook._attach() == (False, "FF7 process not found (ff7_en.exe / ff7.exe)")
    mem.attach_result = (False, "boom")
    assert hook._attach() == (False, "boom")
    mem.available = False
    assert hook._attach()[0] is False
    assert "Windows" in hook.ensure_attached()[1]
    hook._mem = None
    assert hook._attach()[0] is False and hook.ensure_attached()[0] is False


def test_ensure_attached_delegates(hook, mem):
    mem.attach_result = (True, None)
    assert hook.ensure_attached() == (True, None)
    assert mem.attach_calls[-1][0] == "ensure"


def test_read_write_shellcode_without_memory():
    h = ff7.FF7Hook(memory=FakeMemory())
    h._mem = None
    assert h._read(1, 1) is None
    assert h._write(1, b"x") is False
    assert h._run_remote_shellcode(b"\xc3", "x") is False


def test_party_stat_recalc_shellcode(hook, mem):
    hook._call_ff7_party_stat_recalc()
    sc, label = mem.shellcode[-1]
    assert label == "party stat recalc"
    assert sc == bytes([0xB8]) + struct.pack("<I", ff7.ADDR_PARTY_STAT_RECALC_FN) + bytes([0xFF, 0xD0, 0x33, 0xC0, 0xC3])
    mem.attached = False
    hook._call_ff7_party_stat_recalc()
    assert len(mem.shellcode) == 1


def test_call_game_fn_one_arg(hook, mem):
    assert hook._call_ff7_game_fn_one_arg(ff7.ADDR_PARTY_ADD_ITEM_FN, 0x1234) is True
    sc, label = mem.shellcode[-1]
    assert label == f"call fn {ff7.ADDR_PARTY_ADD_ITEM_FN:#x}"
    assert sc[:5] == bytes([0x68]) + struct.pack("<I", 0x1234)
    assert sc[5:10] == bytes([0xB8]) + struct.pack("<I", ff7.ADDR_PARTY_ADD_ITEM_FN)
    mem.shellcode_ok = False
    assert hook._call_ff7_game_fn_one_arg(1, 2) is False
    mem.attached = False
    assert hook._call_ff7_game_fn_one_arg(1, 2) is False


def test_typed_reads(hook, mem):
    mem.put(0x5000, struct.pack("<i", -2))
    assert hook._read_u8(0x5000) == 0xFE
    assert hook._read_u16(0x5000) == 0xFFFE
    assert hook._read_i16(0x5000) == -2
    assert hook._read_u32(0x5000) == 0xFFFFFFFE
    assert hook._read_i32(0x5000) == -2
    mem.fail_read.add(0x5000)
    assert [hook._read_u8(0x5000), hook._read_u16(0x5000), hook._read_i16(0x5000), hook._read_u32(0x5000),
            hook._read_i32(0x5000)] == [None] * 5


# --------------------------------------------------------------------------- kernel text tables


def test_read_kernel_name_string(hook, mem):
    mem.put(0x6000, ff7._encode_ff7_name("Fire") + b"\x00")
    assert hook._read_kernel_name_string(0x6000, 24) == "Fire"
    mem.put(0x6100, b"\xff\x21")
    assert hook._read_kernel_name_string(0x6100, 4) == ""
    mem.put(0x6200, bytes([0x21, 0x22]))  # no terminator inside max_len
    assert hook._read_kernel_name_string(0x6200, 2) == "AB"
    assert hook._read_kernel_name_string(0, 4) == ""
    mem.fail_read.add(0x6300)
    assert hook._read_kernel_name_string(0x6300, 4) == ""
    mem.attached = False
    assert hook._read_kernel_name_string(0x6000, 4) == ""


def test_kernel_section_vanilla(hook, mem):
    base = ff7.ADDR_KERNEL_TEXTS_BASE
    mem.put(base, struct.pack("<I", 1))  # vanilla: non-zero
    mem.put(ff7.ADDR_KERNEL_SECTION_OFFSETS + 2 * 8, struct.pack("<H", 0x100))
    table = base + 0x100
    mem.put(table, struct.pack("<HH", 4, 20))
    mem.put(table + 4, ff7._encode_ff7_name("Attack")[:6] + b"\xff")
    mem.put(table + 20, ff7._encode_ff7_name("Magic")[:5] + b"\xff")
    assert hook._read_kernel_section_strings(8, 2) == ["Attack", "Magic"]
    assert hook._read_kernel_section_strings(8, 0) == []


def test_kernel_section_vanilla_zero_table(hook, mem, monkeypatch):
    monkeypatch.setattr(ff7, "ADDR_KERNEL_TEXTS_BASE", 0)
    hook._read_kernel_section_strings  # table_addr = 0 + 0 -> empty
    mem.put(0, b"\x01")
    assert hook._read_kernel_section_strings(8, 2) == []


def test_kernel_section_ffnx(hook, mem):
    base = ff7.ADDR_KERNEL_TEXTS_BASE
    mem.put(base, struct.pack("<I", 0))
    kc = ff7.ADDR_KERNEL_READ_FN_CALL
    mem.put(kc, struct.pack("<I", 0x100))
    kread = 0x100 + kc + 4
    mem.put(kread + 0x1B, struct.pack("<I", 0x700000))
    mem.put(0x700000 + 4 * 9, struct.pack("<I", 0x710000))
    mem.put(0x710000, struct.pack("<H", 2))
    mem.put(0x710002, ff7._encode_ff7_name("Bolt")[:4] + b"\xff")
    assert hook._read_kernel_section_strings(9, 1) == ["Bolt"]
    # section table entry 0 -> nothing
    assert hook._read_kernel_section_strings(8, 1) == []
    # no table pointer
    mem.put(kread + 0x1B, struct.pack("<I", 0))
    assert hook._read_kernel_section_strings(9, 1) == []


def test_kernel_section_guards(hook, mem):
    mem.fail_read.add(ff7.ADDR_KERNEL_TEXTS_BASE)
    assert hook._read_kernel_section_strings(8, 2) == []
    mem.attached = False
    assert hook._read_kernel_section_strings(8, 2) == []


def test_enemy_display_attack_names(hook, mem, monkeypatch):
    base = ff7.ADDR_ENEMY_ATTACK_NAMES
    mem.put(base, b"\xff")
    mem.put(base + 32, ff7._encode_ff7_name("Scorpion Tail", 24) + b"\xff")
    names = hook._read_enemy_display_attack_names_32()
    assert len(names) == 32 and names[0] == "" and names[1] == "Scorpion Tail"
    monkeypatch.setattr(hook, "_read_kernel_name_string", lambda a, n: (_ for _ in ()).throw(RuntimeError("x")))
    assert hook._read_enemy_display_attack_names_32() == [""]  # partial list up to the failure
    mem.attached = False
    assert hook._read_enemy_display_attack_names_32() == []


def test_ensure_battle_log_names(hook, mem, monkeypatch):
    calls = []
    monkeypatch.setattr(hook, "_read_kernel_section_strings", lambda sid, n: calls.append((sid, n)) or [f"s{sid}"])
    hook._ensure_battle_log_names()
    assert hook._bl_name_cache == {"command": ["s8"], "attack": ["s9"]}
    hook._ensure_battle_log_names()
    assert calls == [(8, 32), (9, 128)]


def test_ensure_battle_log_names_detached_and_error(hook, mem, monkeypatch):
    mem.attached = False
    hook._ensure_battle_log_names()
    assert hook._bl_name_cache == {"command": [], "attack": []}
    hook._bl_name_cache = None
    mem.attached = True

    def boom(sid, n):
        raise RuntimeError("bad")

    monkeypatch.setattr(hook, "_read_kernel_section_strings", boom)
    hook._ensure_battle_log_names()
    assert hook._bl_name_cache == {"command": [], "attack": []}


# --------------------------------------------------------------------------- battle command text


@pytest.fixture
def names_hook(hook, monkeypatch):
    monkeypatch.setattr(ff7, "_BATTLE_LOG_NAME_ASSETS_LOADED", True)  # keep the tables empty
    monkeypatch.setattr(ff7, "_BATTLE_LOG_CMD_EN", {})
    monkeypatch.setattr(ff7, "_BATTLE_LOG_ATK128_EN", {})
    cmds = [""] * 32
    cmds[1], cmds[0x0D] = "Attack", "Enemy Skill"
    atks = [""] * 128
    atks[0], atks[56], atks[72], atks[0x21] = "Cure", "Choco/Mog", "Frog Song", "Big Guard"
    hook._bl_name_cache = {"command": cmds, "attack": atks}
    return hook


def test_format_battle_command_text(names_hook, monkeypatch):
    h = names_hook
    monkeypatch.setattr(h, "_read_enemy_display_attack_names_32", lambda: ["Tail Laser"] + [""] * 31)
    assert h._format_battle_command_text(0x23, 0) == "Poison"
    assert h._format_battle_command_text(0x02, 0) == "Cure"
    assert h._format_battle_command_text(0x02, 3) == "Magic 3"
    assert h._format_battle_command_text(0x02, 60) == "Magic 60"
    assert h._format_battle_command_text(0x03, 0) == "Choco/Mog"
    assert h._format_battle_command_text(0x03, 5) == "Summon 5"
    assert h._format_battle_command_text(0x04, 0) == "Potion"
    assert h._format_battle_command_text(0x0D, 0) == "Enemy Skill: Frog Song"
    assert h._format_battle_command_text(0x0D, 1) == "Enemy Skill: E.Skill 1"
    assert h._format_battle_command_text(0x0D, 100) == "Enemy Skill: E.Skill 100"
    assert h._format_battle_command_text(0x20, 0) == "Tail Laser"
    assert h._format_battle_command_text(0x20, 0x21) == "Big Guard"
    assert h._format_battle_command_text(0x20, 0x45) == "Attack 0x45"
    assert h._format_battle_command_text(0x01, 0) == "Attack"
    assert h._format_battle_command_text(0x07, 0) == "cmd 0x07"


def test_target_label_from_mask():
    party = [{"party_slot": 0, "name": "Cloud"}, {"slot": 1, "name": "Tifa"}, {"party_slot": 2, "name": " "}]
    enemies = [{"slot": 4, "name": "Grunt"}, {"slot": 5, "name": "Grunt"}, {"slot": 6, "name": "Boss"},
               {"slot": 7, "name": None}]
    f = ff7.FF7Hook._target_label_from_mask
    assert f(0, party, enemies) == ""
    assert f(0b1, party, enemies) == "Cloud"
    assert f(0b11, party, enemies) == "All Allies"
    assert f(0b0100, party, enemies) == ""  # nameless ally
    assert f(0b110000, party, enemies) == "All Enemies"
    assert f(0b100000, party, enemies) == "Grunt B"
    assert f(0b1000000, party, enemies) == "Boss"
    assert f(0b10000000, party, enemies) == "?"
    assert f(0b1010001, party, enemies) == "Cloud and Grunt A and Boss"
    assert f(1 << 8 | 1 << 9, party, enemies) == ""  # bits 8+ ignored
    assert f(0b100000000000 | 0b10000000, party, [{"slot": 9, "name": "X"}]) == ""


def test_command_helpers():
    assert ff7.FF7Hook._command_has_target_mask(0x12) is False
    assert ff7.FF7Hook._command_has_target_mask(0x01) is True
    assert [ff7.FF7Hook._command_verb(c) for c in (2, 3, 4, 0x23, 1)] == ["cast", "summoned", "used", "took", "used"]


def test_command_deals_damage(hook):
    assert hook._command_deals_damage_battlelog(0x12, 0) is False
    assert hook._command_deals_damage_battlelog(0x05, 0) is False
    assert hook._command_deals_damage_battlelog(0x04, 0) is True
    assert hook._command_deals_damage_battlelog(0x01, 0) is True


def test_format_battle_log_line(names_hook, monkeypatch):
    h = names_hook
    party = [{"party_slot": 0, "name": "Cloud"}, {"party_slot": 1, "name": ""}]
    enemies = [{"slot": 4, "name": "Grunt"}, {"slot": 5, "name": "Grunt"}, {"slot": 6, "name": "Boss"}]
    assert h._format_battle_log_line(party, enemies, 0, 0xFF, 0, 0, 0, False, False) == ""
    assert h._format_battle_log_line(party, enemies, 0, 0x01, 0, 0b10000, 123, False, True) == \
        "Cloud used Attack on Grunt A, dealt 123 damage [crit]"
    assert h._format_battle_log_line(party, enemies, 5, 0x01, 0, 0b1, -5, False, False) == \
        "Grunt B used Attack on Cloud, dealt 5 damage"
    assert h._format_battle_log_line(party, enemies, 6, 0x01, 0, 0, 0, True, False) == "Boss used Attack, missed"
    assert h._format_battle_log_line(party, enemies, 1, 0x02, 0, 0b1, 0, False, False).startswith("Unknown cast Cure")
    assert h._format_battle_log_line(party, enemies, 9, 0x12, 0, 0b1, 50, False, False) == "Unknown used cmd 0x12"
    assert h._format_battle_log_line(party, enemies, 3, 0x05, 0, 0, None, False, False) == "Unknown used cmd 0x05"


def test_read_savemap(hook, mem):
    assert hook._read_savemap() == (None, SM_ADDR)  # all zero -> unreadable
    mem.put(SM_ADDR, b"\x01")
    data, addr = hook._read_savemap()
    assert addr == SM_ADDR and len(data) == ff7.SAVEMAP_SIZE and data[0] == 1
    mem.fail_read.add(SM_ADDR)
    assert hook._read_savemap() == (None, SM_ADDR)
    mem.attached = False
    assert hook._read_savemap() == (None, 0)


def test_current_module_byte(hook, mem):
    mem.put(ff7.ADDR_CURRENT_MODULE, b"\x02")
    assert hook._current_module_byte() == 2
    mem.attached = False
    assert hook._current_module_byte() is None


def test_parse_field_party(hook, loaded):
    sm = bytearray(loaded)
    sm[ff7.SAVE_OFF_PARTY_SLOTS + 2] = 0xFF
    members, ids = hook._parse_field_party(bytes(sm))
    assert ids == [0, 1, -1]
    cloud, barret, empty = members
    assert cloud["name"] == "Cloud" and cloud["level"] == 10 and cloud["hp"] == 100 and cloud["max_hp"] == 200
    assert cloud["limit"] == 50 and cloud["row"] == "FR" and cloud["weapon_name"] == "Buster Sword"
    assert barret["row"] == "BR" and barret["status_effect"] == "Sadness"
    assert empty["slot_empty"] is True and empty["weapon_id"] == -1 and empty["id"] == -1
    # max fallbacks
    members, _ = hook._parse_field_party(bytes(loaded))
    assert members[2]["max_hp"] == 80 and members[2]["max_mp"] == 5
    # short savemap: no slot ids, no members
    assert hook._parse_field_party(b"") == ([], [])


def test_parse_field_party_record_past_end(hook, monkeypatch):
    sm = bytearray(b"\x00" * (ff7.SAVE_OFF_PARTY_SLOTS + 3))
    monkeypatch.setattr(ff7, "_CHAR_BLOCK", [0x2000] * 9)
    members, ids = hook._parse_field_party(bytes(sm))
    assert ids == [0, 0, 0] and members == []


def test_parse_all_characters(hook, loaded):
    rows = hook._parse_all_characters(bytes(loaded))
    names = [r["name"] for r in rows]
    # unnamed records decode as "?" and are kept (non-empty)
    assert names[:3] == ["Cloud", "Barret", "Tifa"] and len(rows) == 9
    assert all(r["party_slot"] == -1 for r in rows)
    assert rows[2]["max_hp"] == 80 and rows[1]["row"] == "BR"
    assert hook._parse_all_characters(b"") == []


def test_parse_all_characters_skips(hook, monkeypatch):
    monkeypatch.setattr(ff7, "_decode_ff7_name", lambda raw: " ")
    sm = standard_savemap()
    assert hook._parse_all_characters(bytes(sm)) == []
    assert hook._parse_all_characters(bytes(sm[:0x100])) == []


# --------------------------------------------------------------------------- battle reads


def test_read_battle_common(hook, mem):
    battle_actor(mem, 1, status=0x10, hp=-5, max_hp=300, mp=7, max_mp=9, flags=3)
    mem.put(ff7.ADDR_BATTLE_ATB_BASE + 1 * ff7.BATTLE_ATB_STRIDE + 2, struct.pack("<H", 65535))
    raw, status, flags, hp, max_hp, mp, max_mp, atb = hook._read_battle_common(1)
    assert (status, flags, hp, max_hp, mp, max_mp, atb) == (0x10, 3, -5, 300, 7, 9, 1.0)
    mem.fail_read.add(ff7.ADDR_BATTLE_ATB_BASE + 1 * ff7.BATTLE_ATB_STRIDE + 2)
    assert hook._read_battle_common(1)[-1] == 0.0
    mem.fail_read.add(ff7.ADDR_BATTLE_CHAR_BASE + ff7.BATTLE_ACTOR_STRIDE)
    assert hook._read_battle_common(1) is None
    mem.attached = False
    assert hook._read_battle_common(0) is None


def test_read_battle_ally(hook, mem, loaded):
    battle_actor(mem, 0, status=0x20 | 0x1, hp=50, level=33, back=True)
    mem.put(ff7.ADDR_PARTY_MEMBER_IDS, bytes([0, 1, 0xFF]))
    mem.put(ff7.ADDR_PARTY_MEMBER_NAMES, ff7._encode_ff7_name("Cloud"))
    mem.put(ff7.ADDR_BATTLE_CHAR_ARRAY + 8, struct.pack("<H", 77))
    a = hook._read_battle_ally(0)
    assert a["name"] == "Cloud" and a["level"] == 33 and a["limit"] == 77 and a["row"] == "BR"
    assert a["status_effect"] == "Fury" and a["ailments"] == ["Death", "Fury"] and a["id"] == 0
    # level 0 in battle actor -> savemap level (reads savemap itself)
    battle_actor(mem, 1, level=0)
    mem.fail_read.add(ff7.ADDR_PARTY_MEMBER_NAMES + ff7.PARTY_RECORD_STRIDE)
    mem.fail_read.add(ff7.ADDR_BATTLE_CHAR_ARRAY + ff7.BATTLE_CHAR_ARRAY_STRIDE + 8)
    b = hook._read_battle_ally(1)
    assert b["level"] == 12 and b["name"] == "?" and b["limit"] == 0
    # passed savemap is used as-is
    b2 = hook._read_battle_ally(1, savemap=bytes(loaded))
    assert b2["level"] == 12
    # party id 0xFF -> unknown name, no savemap lookup
    battle_actor(mem, 2, level=0)
    c = hook._read_battle_ally(2)
    assert c["name"] == "?" and c["id"] == 0xFF and c["level"] == 0
    mem.fail_read.add(ff7.ADDR_PARTY_MEMBER_IDS + 2)
    assert hook._read_battle_ally(2)["id"] == -1
    mem.fail_read.add(ff7.ADDR_BATTLE_CHAR_BASE + 2 * ff7.BATTLE_ACTOR_STRIDE)
    assert hook._read_battle_ally(2) is None


def test_read_battle_enemy(hook, mem):
    assert hook._read_battle_enemy(3) is None
    mem.put(ff7.ADDR_ENEMY_OBJ_BASE, b"\x02")
    battle_actor(mem, 4, hp=-1, max_hp=500)
    data = ff7.ADDR_ENEMY_DATA_BASE + 2 * ff7.ENEMY_DATA_STRIDE
    mem.put(data, ff7._encode_ff7_name("Guard Scorpion", 24))
    mem.put(data + 0x20, b"\x0c")
    e = hook._read_battle_enemy(4)
    assert e["name"] == "Guard Scorpion" and e["level"] == 12 and e["hp"] == 0 and e["scene_id"] == 2
    mem.fail_read.add(data)
    mem.fail_read.add(data + 0x20)
    e = hook._read_battle_enemy(4)
    assert e["name"] == "?" and e["level"] == 0
    mem.fail_read.add(ff7.ADDR_BATTLE_CHAR_BASE + 4 * ff7.BATTLE_ACTOR_STRIDE)
    assert hook._read_battle_enemy(4) is None
    mem.put(ff7.ADDR_ENEMY_OBJ_BASE + ff7.ENEMY_OBJ_STRIDE, b"\xff")
    assert hook._read_battle_enemy(5) is None
    mem.fail_read.add(ff7.ADDR_ENEMY_OBJ_BASE + 2 * ff7.ENEMY_OBJ_STRIDE)
    assert hook._read_battle_enemy(6) is None
    mem.attached = False
    assert hook._read_battle_enemy(4) is None


def test_battle_actor_addr(hook):
    assert hook._battle_actor_addr(2) == ff7.ADDR_BATTLE_CHAR_BASE + 2 * ff7.BATTLE_ACTOR_STRIDE


def test_field_char_hp_helpers(hook, mem, loaded):
    sm = bytes(loaded)
    off = ff7._CHAR_BLOCK[1]
    assert hook._field_char_hp_addr(sm, 1) == SM_ADDR + off + ff7.REC_OFF_HP
    assert hook._field_char_hp_addr(sm, 3) is None
    assert hook._field_char_hp_addr(b"", 0) is None
    sm2 = bytearray(sm)
    sm2[ff7.SAVE_OFF_PARTY_SLOTS] = 0xFF
    assert hook._field_char_hp_addr(bytes(sm2), 0) is None
    assert hook._field_char_max_hp(bytes(sm2), 0) is None
    assert hook._field_char_max_hp(sm, 0) == 200
    assert hook._field_char_max_hp(sm, -1) is None
    assert hook._field_char_hp_u16(sm, 0) == 100
    assert hook._field_char_hp_u16(sm, 5) is None


def test_field_char_hp_record_past_end(hook, monkeypatch):
    sm = bytearray(ff7.SAVE_OFF_PARTY_SLOTS + 3)
    monkeypatch.setattr(ff7, "_CHAR_BLOCK", [0x2000] * 9)
    assert hook._field_char_hp_addr(bytes(sm), 0) is None
    assert hook._field_char_max_hp(bytes(sm), 0) is None


# --------------------------------------------------------------------------- float / double writes


def test_float_reads_writes(hook, mem):
    assert hook._write_float(0x7000, 1.5) is True
    assert hook._read_float(0x7000) == 1.5
    mem.put(0x7100, struct.pack("<d", 2.25))
    assert hook._read_f64(0x7100) == 2.25
    mem.fail_read.update({0x7200})
    assert hook._read_float(0x7200) is None and hook._read_f64(0x7200) is None


def test_read_f64_short_read(hook, mem, monkeypatch):
    monkeypatch.setattr(hook, "_read", lambda a, n: b"\x00" * 4)
    assert hook._read_f64(0) is None


def test_write_double_robust_paths(hook, mem):
    assert hook._write_double_robust(0x8000, 3.5) is True
    assert mem.protect_writes == []
    # fast path writes but read-back disagrees -> protect write (first try works)
    mem.fail_read.add(0x8100)
    assert hook._write_double_robust(0x8100, 1.0) is True
    assert mem.protect_writes[-1][3] == ff7._PAGE_READWRITE
    # write fails; protect fails too
    mem.fail_write.add(0x8200)
    mem.fail_protect = True
    assert hook._write_double_robust(0x8200, 1.0) is False
    assert [p[3] for p in mem.protect_writes[-2:]] == [ff7._PAGE_READWRITE, ff7._PAGE_EXECUTE_READWRITE]


def test_write_float_robust_paths(hook, mem):
    assert hook._write_float_robust(0x9000, 2.0) is True
    mem.fail_read.add(0x9100)
    assert hook._write_float_robust(0x9100, 2.0) is True
    mem.fail_write.add(0x9200)
    mem.fail_protect = True
    assert hook._write_float_robust(0x9200, 2.0) is False


def test_win_protect_write(hook, mem):
    assert hook._win_protect_write(0x10, b"\x01", flush_icache=False, page_protect=4) is True
    assert mem.protect_writes[-1] == (0x10, b"\x01", False, 4)
    assert hook._win_protect_write(0x10, b"") is False
    hook._mem = None
    assert hook._win_protect_write(0x10, b"\x01") is False


# --------------------------------------------------------------------------- FFNx trampoline


def _ffnx_stub(mem, chk, *, second=True, short=False):
    rel = 0x1000
    mem.put(chk + 1, struct.pack("<i", rel))
    base = chk + 5 + rel
    code = bytearray(256)
    code[10:14] = bytes([0xF2, 0x0F, 0x10, 0x05])
    struct.pack_into("<i", code, 14, 0x100)
    if second:
        code[40:44] = bytes([0xF2, 0x0F, 0x10, 0x05])
        struct.pack_into("<i", code, 44, 0x200)
    mem.put(base, code)
    return base


def test_ffnx_targets(hook, mem):
    chk = ff7.ADDR_FFNX_TRAMPOLINE_CHECK
    base = _ffnx_stub(mem, chk)
    a30, a15 = hook._ffnx_trampoline_targets_fps_addrs(chk)
    assert a30 == base + 10 + 8 + 0x100 and a15 == base + 40 + 8 + 0x200


def test_ffnx_targets_failures(hook, mem, monkeypatch):
    chk = ff7.ADDR_FFNX_TRAMPOLINE_CHECK
    mem.fail_read.add(chk + 1)
    assert hook._ffnx_trampoline_targets_fps_addrs(chk) is None
    mem.fail_read.clear()
    base = _ffnx_stub(mem, chk, second=False)
    assert hook._ffnx_trampoline_targets_fps_addrs(chk) is None  # only one movsd
    mem.put(base + 10, b"\x00\x00\x00\x00")
    assert hook._ffnx_trampoline_targets_fps_addrs(chk) is None  # none
    mem.fail_read.add(base)
    assert hook._ffnx_trampoline_targets_fps_addrs(chk) is None  # code unreadable
    real = hook._read
    monkeypatch.setattr(hook, "_read", lambda a, n: b"\x00" * 10 if n == 256 else real(a, n))
    assert hook._ffnx_trampoline_targets_fps_addrs(chk) is None  # code too short
    mem.attached = False
    assert hook._ffnx_trampoline_targets_fps_addrs(chk) is None


def test_ffnx_fps_pair_plausible(hook, mem):
    assert hook._ffnx_fps_pair_plausible(1, 1) is False
    mem.put(0xA000, struct.pack("<d", 30.0))
    mem.put(0xA100, struct.pack("<d", 15.0))
    assert hook._ffnx_fps_pair_plausible(0xA000, 0xA100) is True
    mem.put(0xA100, struct.pack("<d", 9000.0))
    assert hook._ffnx_fps_pair_plausible(0xA000, 0xA100) is False
    mem.put(0xA100, struct.pack("<d", 0.1))
    assert hook._ffnx_fps_pair_plausible(0xA000, 0xA100) is False
    mem.put(0xA100, struct.pack("<d", math.inf))
    assert hook._ffnx_fps_pair_plausible(0xA000, 0xA100) is False
    mem.fail_read.add(0xA100)
    assert hook._ffnx_fps_pair_plausible(0xA000, 0xA100) is False


# --------------------------------------------------------------------------- name / token resolution


def test_char_id_from_savemap_name(hook, loaded):
    sm = bytes(loaded)
    assert hook._char_id_from_savemap_name(sm, "  barret ") == 1
    assert hook._char_id_from_savemap_name(sm, "") is None
    assert hook._char_id_from_savemap_name(sm, "Sephiroth") is None
    assert hook._char_id_from_savemap_name(sm[:0x60], "Cloud") is None


def test_party_slot_from_token(hook, mem, loaded, monkeypatch):
    sm = bytes(loaded)
    assert hook._party_slot_from_token(sm, "", False) == (None, "empty character token")
    assert hook._party_slot_from_token(sm, "2", False) == (2, None)
    assert hook._party_slot_from_token(sm, "Tifa", False) == (2, None)
    assert hook._party_slot_from_token(None, "Barret", False) == (1, None)  # reads savemap
    assert hook._party_slot_from_token(sm, "Nobody", False) == (None, "unknown character name: Nobody")
    sm2 = bytearray(sm)
    put_char(sm2, 5, name="Yuffie")
    assert hook._party_slot_from_token(bytes(sm2), "Yuffie", True) == (None, "Yuffie is not in the active party (field)")
    mem.fail_read.add(SM_ADDR)
    assert hook._party_slot_from_token(None, "Cloud", False) == (None, "savemap not readable")


def test_party_slot_from_random(hook, loaded, monkeypatch):
    monkeypatch.setattr(ff7.random, "choice", lambda seq: seq[-1])
    hook._last_snapshot = {"party": [{"name": "Cloud"}, {"name": "?"}, {"name": "Tifa"}, {"name": "X", "slot_empty": True},
                                     "junk"]}
    assert hook._party_slot_from_token(bytes(loaded), "random_character", False) == (2, None)
    hook._last_snapshot = {"party": []}
    assert hook._party_slot_from_token(bytes(loaded), "__random_party__", False) == (None, "no party members for random")
    hook._last_snapshot = None
    assert hook._party_slot_from_token(bytes(loaded), "__random_party__", False)[0] is None


def test_char_id_from_gear_token(hook, loaded):
    sm = bytes(loaded)
    assert hook._char_id_from_gear_character_token(sm, " ") == (None, "empty character token")
    assert hook._char_id_from_gear_character_token(sm, "8") == (8, None)
    assert hook._char_id_from_gear_character_token(sm, "9") == (None, "character index must be 0–8")
    assert hook._char_id_from_gear_character_token(sm, "Tifa") == (2, None)
    assert hook._char_id_from_gear_character_token(sm, "Zack") == (None, "unknown character name: Zack")


def test_parse_int_or_random(hook, monkeypatch):
    monkeypatch.setattr(ff7.random, "randint", lambda lo, hi: (lo, hi))
    p = hook._parse_int_or_random
    assert p(None) == (None, "missing numeric value")
    assert p(5) == (5, None)
    assert p(0, clamp_1_9999=True) == (1, None)
    assert p(20000, clamp_1_9999=True) == (9999, None)
    assert p("random: 9 - 3") == ((3, 9), None)
    assert p("random:0-20000", clamp_1_9999=True) == ((1, 9999), None)
    assert p("RANDOM", 50) == ((1, 50), None)
    assert p("random", 50000, clamp_1_9999=True) == ((1, 9999), None)
    assert p(" 42 ") == (42, None)
    assert p("0", clamp_1_9999=True) == (1, None)
    assert p("x") == (None, "not an int: x")


def test_parse_gil_amount(hook, monkeypatch):
    monkeypatch.setattr(ff7.random, "randint", lambda lo, hi: (lo, hi))
    p = hook._parse_gil_amount
    assert p(None) == (None, "missing amount")
    assert p(True) == (None, "invalid amount")
    assert p(0) == (None, "amount must be positive")
    assert p(5) == (5, None)
    assert p(2_000_000_000) == (ff7._MAX_GIL, None)
    assert p(2.9) == (2, None)
    assert p(0.5) == (None, "amount must be positive")
    assert p(5e9) == (ff7._MAX_GIL, None)
    assert p("random:10-1") == ((1, 10), None)
    assert p("random:0-0") == ((1, 1), None)
    assert p("random:2000000000-5") == ((5, ff7._MAX_GIL), None)
    assert p("random") == ((1, 999_999), None)
    assert p("abc") == (None, "not an integer: abc")
    assert p("-4") == (None, "amount must be positive")
    assert p(" 77 ") == (77, None)
    assert p("9999999999") == (ff7._MAX_GIL, None)


def test_parse_battle_speed_token(hook):
    p = hook._parse_battle_speed_token
    assert p(None) == (None, "missing speed")
    assert p("  ") == (None, "missing speed")
    assert p("Fast") == (40, None)
    assert p("slowest") == (255, None)
    assert p("300") == (255, None)
    assert p("-5") == (0, None)
    assert p("12.7") == (12, None)
    assert p("warp") == (None, "bad battle speed: warp")


def test_parse_float_or_random(hook, monkeypatch):
    monkeypatch.setattr(ff7.random, "uniform", lambda lo, hi: (lo, hi))
    p = hook._parse_float_or_random
    assert p(None) == (None, "missing speed")
    assert p(2) == (2.0, None)
    assert p(0.5) == (0.5, None)
    assert p("random:4-1.5") == ((1.5, 4.0), None)
    assert p(" 1.25 ") == (1.25, None)
    assert p("x") == (None, "not a float: x")


def test_menu_bit_index(hook):
    assert hook._menu_bit_index(" materia ") == (2, None)
    assert hook._menu_bit_index("Save") == (9, None)
    assert hook._menu_bit_index("Bestiary") == (None, "unknown menu: Bestiary")
    assert hook._menu_bit_index(None)[0] is None


def test_gear_name_to_id(hook):
    assert hook._gear_name_to_id("weapon", "buster sword") == (0, None)
    assert hook._gear_name_to_id("armor", " Iron Bangle ") == (1, None)
    assert hook._gear_name_to_id("accessory", "Earring") == (2, None)
    assert hook._gear_name_to_id("weapon", "Nope") == (None, "unknown weapon: Nope")
    assert hook._gear_name_to_id("armor", "Nope") == (None, "unknown armor: Nope")
    assert hook._gear_name_to_id("accessory", "Nope") == (None, "unknown accessory: Nope")
    assert hook._gear_name_to_id("weapon", "") == (None, "empty gear name")
    assert hook._gear_name_to_id("hat", "Earring") == (None, "gear_kind must be weapon|armor|accessory")


def test_gear_name_to_item_id(hook):
    assert hook._gear_name_to_item_id("Buster Sword") == (0x80, None)
    assert hook._gear_name_to_item_id("Iron Bangle") == (0x101, None)
    assert hook._gear_name_to_item_id("earring") == (0x122, None)
    assert hook._gear_name_to_item_id("") == (None, "empty gear name")
    assert hook._gear_name_to_item_id("Potion") == (None, "unknown gear: Potion")


def test_item_and_materia_name_to_id(hook, monkeypatch):
    assert hook._item_name_to_id("potion") == (0, None)
    assert hook._item_name_to_id("Buster Sword") == (0x80, None)
    assert hook._item_name_to_id("") == (None, "empty item name")
    monkeypatch.setitem(ff7._ITEM_NAMES_EN, "200", "Weird Item")
    assert hook._item_name_to_id("Weird Item") == (None, "unknown gear: Weird Item")
    assert hook._materia_name_to_id(" mp plus") == (0, None)
    assert hook._materia_name_to_id("") == (None, "empty materia name")
    assert hook._materia_name_to_id("Nope") == (None, "unknown materia: Nope")


def test_resolve_gear_token(hook, monkeypatch):
    monkeypatch.setattr(ff7.random, "choice", lambda seq: seq[0])
    assert hook._resolve_gear_token("weapon", "random") == (0, None)
    assert hook._resolve_gear_token("armor", "random:armor") == (0, None)
    assert hook._resolve_gear_token("accessory", "random_accessory") == (0, None)
    assert hook._resolve_gear_token("weapon", "Mythril Saber") == (1, None)
    monkeypatch.setattr(ff7, "_WEAPON_NAMES_EN", {"0": ""})
    assert hook._resolve_gear_token("weapon", "random") == (None, "no gear pool")


def test_enemy_slot_from_token(hook, monkeypatch):
    monkeypatch.setattr(ff7.random, "choice", lambda seq: seq[0])
    hook._last_snapshot = {"enemies": [
        {"slot": 5, "name": "Grunt", "scene_id": 1},
        {"slot": 4, "name": "Grunt", "scene_id": 1},
        {"slot": 6, "name": "Guard Scorpion", "scene_id": 0},
    ]}
    f = hook._enemy_slot_from_token
    assert f("") == (None, "empty enemy token")
    assert f("͏") == (None, "empty enemy token")
    assert f("random_enemy") == (5, None)
    assert f("Grunt A") == (4, None)
    assert f("grunt b") == (5, None)
    assert f("Grunt C") == (None, "enemy suffix out of range: Grunt C")
    assert f("Ghost A") == (None, "enemy not found: Ghost A")
    assert f("3") == (7, None)
    assert f("9") == (None, "enemy index must be 0–5")
    assert f("scorp") == (6, None)
    assert f("Dragon") == (None, "enemy not found: Dragon")


def test_enemy_slot_random_and_suffix_bad_slots(hook, monkeypatch):
    monkeypatch.setattr(ff7.random, "choice", lambda seq: seq[0])
    hook._last_snapshot = {"enemies": [{"slot": 2, "name": "Odd"}]}
    assert hook._enemy_slot_from_token("__random_enemy__") == (None, "bad enemy slot")
    assert hook._enemy_slot_from_token("Odd A") == (None, "bad enemy slot")
    hook._last_snapshot = None
    assert hook._enemy_slot_from_token("random_enemy") == (None, "no enemies for random")


# --------------------------------------------------------------------------- rename / gear (no inventory)


def test_op_rename_character(hook, mem, loaded):
    assert hook._op_rename_character("Cloud", "Zack") == (True, None)
    off = ff7._CHAR_BLOCK[0]
    assert mem.get(SM_ADDR + off + ff7.REC_OFF_NAME, 12) == ff7._encode_ff7_name("Zack")
    assert hook._op_rename_character("Nobody", "x") == (False, "Character not found")
    mem.fail_write.add(SM_ADDR + ff7._CHAR_BLOCK[1] + ff7.REC_OFF_NAME)
    assert hook._op_rename_character("Barret", "x") == (False, "Write name failed")
    mem.fail_read.add(SM_ADDR)
    assert hook._op_rename_character("Cloud", "x") == (False, "Savemap not readable")


@pytest.fixture
def no_allowlist(monkeypatch):
    monkeypatch.setattr(ff7, "_EQUIP_ALLOW", {})
    monkeypatch.setattr(ff7, "_EQUIP_ALLOW_LOADED", True)


def test_op_set_character_gear(hook, mem, loaded, no_allowlist):
    off = ff7._CHAR_BLOCK[0]
    assert hook._op_set_character_gear("Cloud", "weapon", 5) == (True, None)
    assert mem.get(SM_ADDR + off + ff7.REC_OFF_WEAPON, 1) == b"\x05"
    assert mem.shellcode[-1][1] == "party stat recalc"
    assert hook._op_set_character_gear("0", " ARMOR ", 3) == (True, None)
    assert mem.get(SM_ADDR + off + ff7.REC_OFF_ARMOR, 1) == b"\x03"
    assert hook._op_set_character_gear("0", "accessory", 4) == (True, None)
    assert mem.get(SM_ADDR + off + ff7.REC_OFF_ACCESSORY, 1) == b"\x04"
    assert hook._op_set_character_gear("0", "weapon", 200) == (False, "weapon id out of range")
    assert hook._op_set_character_gear("0", "armor", 40) == (False, "armor id out of range")
    assert hook._op_set_character_gear("0", "accessory", -1) == (False, "accessory id out of range")
    assert hook._op_set_character_gear("0", "hat", 1) == (False, "gear_kind must be weapon|armor|accessory")
    assert hook._op_set_character_gear("Zed", "weapon", 1) == (False, "unknown character name: Zed")
    mem.fail_all_writes = True
    assert hook._op_set_character_gear("0", "weapon", 1) == (False, "write failed")
    assert hook._op_set_character_gear("0", "accessory", 1) == (False, "write failed")
    mem.fail_read.add(SM_ADDR)
    assert hook._op_set_character_gear("0", "weapon", 1) == (False, "Savemap not readable")


def test_op_set_character_gear_allowlist(hook, mem, loaded, monkeypatch):
    monkeypatch.setattr(ff7, "_EQUIP_ALLOW", {"0": {"weapon": [1, 2]}})
    monkeypatch.setattr(ff7, "_EQUIP_ALLOW_LOADED", True)
    assert hook._op_set_character_gear("0", "weapon", 9)[1].startswith("gear not allowed")
    assert hook._op_set_character_gear("0", "weapon", 2) == (True, None)


def test_op_set_character_gear_bad_cid(hook, mem, loaded, monkeypatch, no_allowlist):
    monkeypatch.setattr(hook, "_char_id_from_gear_character_token", lambda sm, t: (12, None))
    assert hook._op_set_character_gear("x", "weapon", 1) == (False, "bad character id")
    monkeypatch.setattr(hook, "_char_id_from_gear_character_token", lambda sm, t: (None, None))
    assert hook._op_set_character_gear("x", "weapon", 1) == (False, "character")


# --------------------------------------------------------------------------- inventory helpers


INV_ITEMS = SM_ADDR + ff7.SAVE_OFF_INV_ITEMS
INV_MAT = SM_ADDR + ff7.SAVE_OFF_INV_MATERIA


def test_battle_ally_slot_for_cid(hook, mem):
    mem.put(ff7.ADDR_PARTY_MEMBER_IDS, bytes([3, 5, 7]))
    assert hook._battle_ally_slot_for_cid(5) == 1
    assert hook._battle_ally_slot_for_cid(9) is None
    mem.attached = False
    assert hook._battle_ally_slot_for_cid(5) is None


def test_inventory_addresses(hook):
    assert hook._inv_items_addr() == INV_ITEMS
    assert hook._inv_materia_addr() == INV_MAT


def test_inv_item_slots(hook, mem, loaded):
    assert hook._inv_read_item_slot(-1) is None
    assert hook._inv_read_item_slot(ff7.INV_ITEM_SLOTS) is None
    assert hook._inv_read_item_slot(0) is None  # empty 0xFFFF
    assert hook._inv_write_item_slot(0, 5, 3) is True
    assert mem.get(INV_ITEMS, 2) == struct.pack("<H", 5 | (3 << 9))
    assert hook._inv_read_item_slot(0) == (5, 3)
    assert hook._inv_write_item_slot(0, None, 3) is True
    assert hook._inv_read_item_slot(0) is None
    assert hook._inv_write_item_slot(0, 5, 0) is True
    assert mem.get(INV_ITEMS, 2) == b"\xff\xff"
    assert hook._inv_write_item_slot(-1, 5, 1) is False
    assert hook._inv_write_item_slot(ff7.INV_ITEM_SLOTS, 5, 1) is False
    mem.fail_read.add(INV_ITEMS + 2)
    assert hook._inv_read_item_slot(1) is None


def test_inv_find_add_remove_items(hook, mem, loaded):
    assert hook._inv_find_item_slot(5) is None
    assert hook._inv_first_empty_item_slot() == 0
    assert hook._inv_add_item(5, 200) is True  # clamped to 99
    assert hook._inv_read_item_slot(0) == (5, 99)
    hook._inv_write_item_slot(0, 5, 90)
    assert hook._inv_add_item(5, 20) is True
    assert hook._inv_read_item_slot(0) == (5, 99)
    assert hook._inv_add_item(6, 0) is True  # clamped to 1, new slot
    assert hook._inv_read_item_slot(1) == (6, 1)
    assert hook._inv_remove_item(5, 10) is True
    assert hook._inv_read_item_slot(0) == (5, 89)
    assert hook._inv_remove_item(6, 1) is True
    assert hook._inv_read_item_slot(1) is None
    assert hook._inv_remove_item(77, 1) is False


def test_inv_add_item_full(hook, mem, loaded):
    for i in range(ff7.INV_ITEM_SLOTS):
        mem.put(INV_ITEMS + i * 2, struct.pack("<H", (i % 0x100) | (1 << 9)))
    assert hook._inv_first_empty_item_slot() is None
    assert hook._inv_add_item(0x13F, 1) is False


def test_inv_add_remove_item_reread_fails(hook, mem, loaded, monkeypatch):
    monkeypatch.setattr(hook, "_inv_find_item_slot", lambda iid: 3)
    monkeypatch.setattr(hook, "_inv_read_item_slot", lambda idx: None)
    assert hook._inv_add_item(5, 1) is False
    assert hook._inv_remove_item(5, 1) is False


def test_inv_materia_slots(hook, mem, loaded):
    assert hook._inv_read_materia_slot(-1) is None
    assert hook._inv_read_materia_slot(ff7.INV_MATERIA_SLOTS) is None
    assert hook._inv_read_materia_slot(0) is None
    assert hook._inv_write_materia_slot(0, 7, 0x123456) is True
    assert hook._inv_read_materia_slot(0) == (7, 0x123456)
    assert hook._inv_write_materia_slot(0, None, 0) is True
    assert mem.get(INV_MAT, 4) == b"\xff" * 4
    assert hook._inv_write_materia_slot(500, 1, 0) is False
    mem.fail_read.add(INV_MAT + 4)
    assert hook._inv_read_materia_slot(1) is None


def test_inv_materia_add_find_remove(hook, mem, loaded):
    assert hook._inv_add_materia(3, 10) is True
    assert hook._inv_add_materia(3, 20) is True
    assert hook._inv_find_materia_slot(3) == 0
    assert hook._inv_find_materia_slot(3, 20) == 1
    assert hook._inv_find_materia_slot(4) is None
    assert hook._inv_remove_materia(3, 20) == (True, 20)
    assert hook._inv_remove_materia(9) == (False, 0)
    mem.fail_write.add(INV_MAT)
    assert hook._inv_remove_materia(3) == (False, 0)


def test_inv_materia_full(hook, mem, loaded):
    for i in range(ff7.INV_MATERIA_SLOTS):
        mem.put(INV_MAT + i * 4, b"\x01\x00\x00\x00")
    assert hook._inv_first_empty_materia_slot() is None
    assert hook._inv_add_materia(1) is False


# --------------------------------------------------------------------------- status / menu ops


def test_op_set_battle_status(hook, mem):
    mem.put(ff7.ADDR_CURRENT_MODULE, b"\x01")
    assert hook._op_set_battle_status(0, 0x20, "on") == (False, "Not in battle")
    mem.put(ff7.ADDR_CURRENT_MODULE, b"\x02")
    assert hook._op_set_battle_status(3, 0x20, "on") == (False, "bad party slot")
    actor = ff7.ADDR_BATTLE_CHAR_BASE
    mem.put(actor, struct.pack("<I", 0x10))
    assert hook._op_set_battle_status(0, 0x20, None) == (True, None)
    assert mem.get(actor, 4) == struct.pack("<I", 0x30)
    hook._op_set_battle_status(0, 0x10, "toggle")
    assert mem.get(actor, 4) == struct.pack("<I", 0x20)
    hook._op_set_battle_status(0, 0x20, "clear")
    assert mem.get(actor, 4) == struct.pack("<I", 0)
    mem.fail_write.add(actor)
    assert hook._op_set_battle_status(0, 1, "off") == (False, "write status failed")
    mem.fail_read.add(actor)
    assert hook._op_set_battle_status(0, 1, "on") == (False, "read status failed")


def test_op_set_field_status_byte(hook, mem, loaded):
    off = ff7._CHAR_BLOCK[1]
    assert hook._op_set_field_status_byte(1, 999) == (True, None)
    assert mem.get(SM_ADDR + off + ff7.REC_OFF_FIELD_STATUS, 1) == b"\xff"
    assert hook._op_set_field_status_byte(3, 1) == (False, "bad party slot")
    mem.put(SM_ADDR + ff7.SAVE_OFF_PARTY_SLOTS + 2, b"\xff")
    assert hook._op_set_field_status_byte(2, 1) == (False, "empty slot")
    mem.fail_all_writes = True
    assert hook._op_set_field_status_byte(0, 1) == (False, "write failed")
    mem.fail_read.add(SM_ADDR)
    assert hook._op_set_field_status_byte(0, 1) == (False, "Savemap not readable")


def test_op_set_menu_u16(hook, mem):
    addr = ff7.ADDR_MENU_VISIBILITY
    mem.put(addr, struct.pack("<H", 0b101))
    assert hook._op_set_menu_u16(addr, "Magic", True, False) == (True, None)
    assert mem.get(addr, 2) == struct.pack("<H", 0b111)
    hook._op_set_menu_u16(addr, "Item", False, False)
    assert mem.get(addr, 2) == struct.pack("<H", 0b110)
    hook._op_set_menu_u16(addr, "Item", False, True)
    assert mem.get(addr, 2) == struct.pack("<H", 0b111)
    assert hook._op_set_menu_u16(addr, "Nope", True, False) == (False, "unknown menu: Nope")
    mem.fail_write.add(addr)
    assert hook._op_set_menu_u16(addr, "Item", True, False) == (False, "write menu word failed")
    mem.fail_read.add(addr)
    assert hook._op_set_menu_u16(addr, "Item", True, False) == (False, "read menu word failed")


def test_op_set_menu_u16_index_none(hook, monkeypatch):
    monkeypatch.setattr(hook, "_menu_bit_index", lambda n: (None, None))
    assert hook._op_set_menu_u16(0, "x", True, False) == (False, "menu")


def test_op_field_menu_access_and_world_speed(hook, mem):
    assert hook._op_field_menu_access(True) == (True, None)
    assert mem.get(ff7.ADDR_FIELD_MENU_ACCESS_ENABLED, 1) == b"\x00"
    hook._op_field_menu_access(False)
    assert mem.get(ff7.ADDR_FIELD_MENU_ACCESS_ENABLED, 1) == b"\x01"
    assert hook._op_world_speed_multiplier(999) == (True, None)
    assert mem.get(ff7.ADDR_WORLD_SPEED_MULTIPLIER, 1) == b"\xff"
    hook._op_world_speed_multiplier(0)
    assert mem.get(ff7.ADDR_WORLD_SPEED_MULTIPLIER, 1) == b"\x01"
    mem.fail_all_writes = True
    assert hook._op_field_menu_access(True) == (False, "write field menu access failed")
    assert hook._op_world_speed_multiplier(2) == (False, "write world speed failed")


# --------------------------------------------------------------------------- game speed


def test_set_game_speed_vanilla(hook, mem):
    for canon in (ff7.ADDR_FIELD_FPS, ff7.ADDR_BATTLE_FPS, ff7.ADDR_WORLD_FPS):
        mem.put(canon, struct.pack("<d", 333333.0))
    for canon in (ff7.ADDR_FPS_NOP_INIT_1, ff7.ADDR_FPS_NOP_INIT_2, ff7.ADDR_FPS_NOP_INIT_3):
        mem.put(canon, b"\x01\x02\x03\x04\x05\x06")
    assert hook._op_set_game_speed(2.0, 10) == (True, None)
    assert hook._read_f64(ff7.ADDR_FIELD_FPS) == pytest.approx(ff7.vanilla_tick_interval_f64(30.0, 2.0))
    assert hook._read_f64(ff7.ADDR_BATTLE_FPS) == pytest.approx(ff7.vanilla_tick_interval_f64(15.0, 2.0))
    assert mem.get(ff7.ADDR_FPS_NOP_INIT_2, 6) == b"\x90" * 6
    b = hook._speed_backup
    assert b["mode"] == "vanilla" and b["speed"] == 2.0 and b["duration_sec"] == 10
    assert b["vanilla"]["fps"][0] == (ff7.ADDR_FIELD_FPS, 333333.0)
    assert b["vanilla"]["nops"][0] == (ff7.ADDR_FPS_NOP_INIT_1, b"\x01\x02\x03\x04\x05\x06")
    assert hook._post_success_timed == [("restore_game_speed", {}, 10)]
    # restore
    assert hook._op_restore_game_speed() == (True, None)
    assert hook._read_f64(ff7.ADDR_FIELD_FPS) == 333333.0
    assert mem.get(ff7.ADDR_FPS_NOP_INIT_1, 6) == b"\x01\x02\x03\x04\x05\x06"
    assert hook._speed_backup is None


def test_set_game_speed_clamps_and_no_timer(hook, mem):
    assert hook._op_set_game_speed(100.0, 0) == (True, None)
    assert hook._speed_backup["speed"] == 8.0
    assert hook._post_success_timed == []
    hook._op_set_game_speed(0.0, 0)
    assert hook._speed_backup["speed"] == 0.25


def test_set_game_speed_vanilla_failures(hook, mem):
    mem.fail_all_writes = True
    mem.fail_protect = True
    ok, err = hook._op_set_game_speed(2.0, 0)
    assert not ok and err == f"write vanilla FPS failed (0x{ff7.ADDR_FIELD_FPS:08X})"
    mem.fail_all_writes = False
    mem.fail_protect = False
    mem.fail_read.add(ff7.ADDR_FPS_NOP_INIT_1)
    ok, err = hook._op_set_game_speed(2.0, 0)
    assert err == f"read FPS NOP site failed (0x{ff7.ADDR_FPS_NOP_INIT_1:08X})"
    mem.fail_read.clear()
    mem.fail_protect = True
    mem.fail_all_writes = False
    # fps writes succeed through the fast path; NOP protect write fails
    ok, err = hook._op_set_game_speed(2.0, 0)
    assert err == f"write FPS NOP failed (0x{ff7.ADDR_FPS_NOP_INIT_1:08X})"


def _setup_ffnx(mem, *, f30=30.0, f15=15.0):
    chk = ff7.ADDR_FFNX_TRAMPOLINE_CHECK
    mem.put(chk, b"\xe9")
    base = _ffnx_stub(mem, chk)
    a30 = base + 10 + 8 + 0x100
    a15 = base + 40 + 8 + 0x200
    mem.put(a30, struct.pack("<d", f30))
    mem.put(a15, struct.pack("<d", f15))
    return a30, a15


def test_set_game_speed_ffnx(hook, mem, caplog):
    a30, a15 = _setup_ffnx(mem)
    assert hook._op_set_game_speed(2.0, 0) == (True, None)
    assert hook._read_f64(a30) == 60.0 and hook._read_f64(a15) == 30.0
    b = hook._speed_backup
    assert b["mode"] == "ffnx" and b["ffnx"] == {"a30": a30, "a15": a15, "f30": 30.0, "f15": 15.0}
    assert "vanilla tick storage" not in caplog.text
    assert hook._op_restore_game_speed() == (True, None)
    assert hook._read_f64(a30) == 30.0 and hook._read_f64(a15) == 15.0


def test_set_game_speed_ffnx_implausible_warns(hook, mem, caplog):
    _setup_ffnx(mem, f30=333333.0)
    assert hook._op_set_game_speed(1.0, 0)[0] is True
    assert "vanilla tick storage" in caplog.text


def test_set_game_speed_ffnx_failures(hook, mem):
    chk = ff7.ADDR_FFNX_TRAMPOLINE_CHECK
    mem.put(chk, b"\xe9")
    ok, err = hook._op_set_game_speed(1.0, 0)
    assert not ok and "movsd FPS pattern not found" in err
    a30, a15 = _setup_ffnx(mem)
    mem.fail_write.add(a30)
    mem.fail_protect = True
    assert hook._op_set_game_speed(1.0, 0) == (False, "write FFNx field/world fps float failed")
    mem.fail_write = {a15}
    assert hook._op_set_game_speed(1.0, 0) == (False, "write FFNx battle fps float failed")


def test_restore_game_speed_edge_cases(hook, mem):
    assert hook._op_restore_game_speed() == (False, "no speed backup")
    hook._speed_backup = {"ffnx": {"a30": None, "f30": None}}
    assert hook._op_restore_game_speed() == (True, None)
    hook._speed_backup = {"vanilla": {"fps": [(0x100, None)], "nops": [(0x200, None)]}}
    assert hook._op_restore_game_speed() == (True, None)
    assert mem.writes == [] and mem.protect_writes == []
    hook._speed_backup = {"mode": "other"}
    assert hook._op_restore_game_speed() == (True, None)
    hook._speed_backup = {"mode": "ffnx"}
    mem.attached = False
    assert hook._op_restore_game_speed() == (False, "no speed backup")


# --------------------------------------------------------------------------- menu restore / battle speed / ATB


def test_restore_menu_words(hook, mem):
    assert hook._op_restore_menu_words({"visibility_u16": 0x1FFFF, "locks_u16": 3}) == (True, None)
    assert mem.get(ff7.ADDR_MENU_VISIBILITY, 2) == b"\xff\xff"
    assert mem.get(ff7.ADDR_MENU_LOCKS, 2) == b"\x03\x00"
    assert hook._op_restore_menu_words({}) == (True, None)
    mem.fail_write.add(ff7.ADDR_MENU_LOCKS)
    assert hook._op_restore_menu_words({"locks_u16": 1}) == (False, "restore menu locks failed")
    mem.fail_write.add(ff7.ADDR_MENU_VISIBILITY)
    assert hook._op_restore_menu_words({"visibility_u16": 1}) == (False, "restore menu visibility failed")
    mem.attached = False
    assert hook._op_restore_menu_words({}) == (False, "Not attached")


def test_battle_speed_set_and_restore(hook, mem, loaded):
    addr = SM_ADDR + ff7.SAVE_OFF_BATTLE_SPEED
    mem.put(addr, b"\x80")
    assert hook._op_set_battle_speed_byte(400, 30) == (True, None)
    assert mem.get(addr, 1) == b"\xff"
    assert hook._battle_speed_restore_byte == 0x80
    assert hook._post_success_timed == [("restore_battle_speed", {"prev_byte": 0x80}, 30)]
    assert hook._op_restore_battle_speed(-5) == (True, None)
    assert mem.get(addr, 1) == b"\x00" and hook._battle_speed_restore_byte is None
    hook._post_success_timed.clear()
    hook._op_set_battle_speed_byte(5, 0)
    assert hook._post_success_timed == []
    mem.fail_write.add(addr)
    assert hook._op_set_battle_speed_byte(5, 0) == (False, "write battle speed failed")
    assert hook._op_restore_battle_speed(5) == (False, "restore battle speed failed")
    mem.fail_read.add(SM_ADDR)
    assert hook._op_set_battle_speed_byte(5, 0) == (False, "Savemap not readable")
    assert hook._op_restore_battle_speed(5) == (False, "Savemap not readable")


def test_battle_atb_set_and_restore(hook, mem, loaded):
    addr = SM_ADDR + ff7.SAVE_OFF_GENERAL_CONFIG
    mem.put(addr, b"\x15")
    assert hook._op_set_battle_atb_mode("wait", 12) == (True, None)
    assert mem.get(addr, 1) == bytes([0x95])
    assert hook._post_success_timed == [("restore_battle_atb_mode", {"prev_config_byte": 0x15}, 12)]
    hook._op_set_battle_atb_mode("recommended", 0)
    assert mem.get(addr, 1) == bytes([0x55])
    assert hook._op_restore_battle_atb_mode(0x115) == (True, None)
    assert mem.get(addr, 1) == b"\x15"
    mem.fail_write.add(addr)
    assert hook._op_set_battle_atb_mode("active", 0) == (False, "write ATB config failed")
    assert hook._op_restore_battle_atb_mode(1) == (False, "restore ATB config failed")
    mem.fail_read.add(SM_ADDR)
    assert hook._op_set_battle_atb_mode("active", 0) == (False, "Savemap not readable")
    assert hook._op_restore_battle_atb_mode(1) == (False, "Savemap not readable")


# --------------------------------------------------------------------------- infinite items


ITEMS = ff7.ADDR_BATTLE_PARTY_ITEMS


def test_infinite_items_enable_restore(hook, mem):
    mem.put(ITEMS, b"\x05\x00\x03")
    assert hook._op_set_infinite_items(True, 20) == (True, None)
    assert hook._infinite_items_active is True
    assert hook._infinite_items_backup_len == ff7.BATTLE_PARTY_ITEMS_BLOB_SIZE
    assert hook._post_success_timed == [("restore_infinite_items", {}, 20)]
    mem.put(ITEMS + 2, b"\x63")
    assert hook._op_restore_infinite_items() == (True, None)
    assert mem.get(ITEMS, 3) == b"\x05\x00\x03"
    assert hook._infinite_items_active is False and hook._infinite_items_backup is None


def test_infinite_items_failures(hook, mem):
    mem.fail_read.add(ITEMS)
    assert hook._op_set_infinite_items(True, 0) == (False, "read battle items failed")
    mem.fail_read.clear()
    hook._op_set_infinite_items(True, 0)
    assert hook._post_success_timed == []
    mem.fail_write.add(ITEMS)
    assert hook._op_set_infinite_items(False, 0) == (False, "restore battle items blob failed")
    # disabling without a backup just turns it off
    hook._infinite_items_backup = None
    assert hook._op_set_infinite_items(False, 0) == (True, None)
    mem.attached = False
    assert hook._op_set_infinite_items(True, 0) == (False, "Not attached")


def test_top_up_battle_party_items(hook, mem, monkeypatch):
    hook._maybe_top_up_battle_party_items()  # inactive: nothing
    hook._infinite_items_active = True
    mem.put(ff7.ADDR_CURRENT_MODULE, b"\x01")
    hook._maybe_top_up_battle_party_items()
    assert mem.writes == []
    mem.put(ff7.ADDR_CURRENT_MODULE, b"\x02")
    stride = ff7.BATTLE_PARTY_ITEMS_SLOT_STRIDE
    mem.put(ITEMS, struct.pack("<H", 5) + b"\x03")                 # topped up
    mem.put(ITEMS + stride, struct.pack("<H", 0xFFFF) + b"\x03")   # empty slot
    mem.put(ITEMS + 2 * stride, struct.pack("<H", 0) + b"\x03")    # id 0 ignored
    mem.put(ITEMS + 3 * stride, struct.pack("<H", 7) + b"\x63")    # already 99
    mem.put(ITEMS + 4 * stride, struct.pack("<H", 8) + b"\x00")    # qty 0 ignored
    mem.fail_read.add(ITEMS + 6 * stride)                          # stops scan
    hook._maybe_top_up_battle_party_items()
    assert mem.writes == [(ITEMS + 2, b"\x63")]


def test_top_up_stops_on_write_failure(hook, mem):
    hook._infinite_items_active = True
    mem.put(ff7.ADDR_CURRENT_MODULE, b"\x02")
    stride = ff7.BATTLE_PARTY_ITEMS_SLOT_STRIDE
    mem.put(ITEMS, struct.pack("<H", 5) + b"\x03")
    mem.put(ITEMS + stride, struct.pack("<H", 6) + b"\x03")
    mem.fail_write.add(ITEMS + 2)
    hook._maybe_top_up_battle_party_items()
    assert mem.writes == []
    mem.attached = False
    hook._maybe_top_up_battle_party_items()
