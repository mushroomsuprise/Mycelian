# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/game_hooks/ff7_hook.py, part 4: FF7Hook.execute_operation dispatch, FF7Hook.snapshot
in every state, and the Ff7GameHook wrapper (boss log, timed restores, connector override persistence and
replay, tick). Memory, database and template config are faked; nothing touches a real process or real data.
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


import types  # noqa: E402

MODULE = ff7.ADDR_CURRENT_MODULE


def set_module(mem, value):
    mem.put(MODULE, bytes([value]))


# --------------------------------------------------------------------------- execute_operation dispatch


class Rec:
    """Replace FF7Hook methods with recorders returning a fixed result."""

    def __init__(self, hook, monkeypatch):
        self.hook, self.mp, self.calls = hook, monkeypatch, []

    def __call__(self, name, result=(True, None)):
        def fn(*args):
            self.calls.append((name, args))
            return result

        self.mp.setattr(self.hook, name, fn)
        return fn


@pytest.fixture
def rec(hook, monkeypatch):
    return Rec(hook, monkeypatch)


def run(hook, op, **kw):
    return hook.execute_operation(op, kw)


def test_execute_requires_windows_and_attach(hook, mem):
    mem.available = False
    assert run(hook, "add_gil", amount=1) == (False, "FF7 writes require Windows")
    mem.available = True
    mem.attach_result = (False, "no process")
    assert run(hook, "add_gil", amount=1) == (False, "no process")
    mem.attach_result = (True, None)
    mem.attached = False
    assert run(hook, "add_gil", amount=1) == (False, "Not attached")
    hook._mem = None
    assert run(hook, "add_gil", amount=1) == (False, "FF7 writes require Windows")


def test_execute_unknown_and_input_ops(hook):
    assert run(hook, "warp") == (False, "Unknown operation: warp")
    for op in ("press_confirm", "press_cancel", "press_menu"):
        assert run(hook, op)[1].startswith("Input simulation")


def test_execute_gil(hook, rec):
    rec("_op_add_gil")
    rec("_op_remove_gil")
    assert run(hook, "add_gil", amount="25") == (True, None)
    assert run(hook, "remove_gil", amount=3) == (True, None)
    assert rec.calls == [("_op_add_gil", (25,)), ("_op_remove_gil", (3,))]
    assert run(hook, "add_gil", amount="x") == (False, "not an integer: x")
    assert run(hook, "remove_gil") == (False, "missing amount")


def test_execute_gil_none_amount(hook, monkeypatch):
    monkeypatch.setattr(hook, "_parse_gil_amount", lambda raw: (None, None))
    assert run(hook, "add_gil") == (False, "amount")
    assert run(hook, "remove_gil") == (False, "amount")


@pytest.mark.parametrize("op,mode", [("add_party_hp", "add"), ("remove_party_hp", "remove")])
def test_execute_party_hp(hook, rec, op, mode):
    rec("_party_hp_rw")
    assert run(hook, op, character="1", amount="50") == (True, None)
    assert run(hook, op, character=" ", slot="2", amount=5) == (True, None)
    assert rec.calls == [("_party_hp_rw", (1, mode, 50)), ("_party_hp_rw", (2, mode, 5))]
    assert run(hook, op, character="1", amount="0") == (False, "amount must be positive")
    assert run(hook, op, character="1", amount="x") == (False, "not an int: x")
    assert run(hook, op, amount=5) == (False, "empty character token")


def test_execute_party_hp_none_slot(hook, monkeypatch, rec):
    monkeypatch.setattr(hook, "_party_slot_from_token", lambda *a: (None, None))
    for op in ("add_party_hp", "remove_party_hp", "kill_party_member", "set_battle_status", "set_field_status_byte"):
        assert run(hook, op, character="x", amount=5)[1] == "character"


def test_execute_kill_ops(hook, rec):
    rec("_op_kill_party_member")
    rec("_op_kill_all_enemies")
    rec("_op_kill_enemy")
    hook._last_snapshot = {"enemies": [{"slot": 5, "name": "Grunt"}]}
    assert run(hook, "kill_party_member", character="0") == (True, None)
    assert run(hook, "kill_party_member") == (False, "empty character token")
    assert run(hook, "kill_all_enemies") == (True, None)
    assert run(hook, "kill_enemy", enemy="grunt") == (True, None)
    assert run(hook, "kill_enemy", enemy=" ") == (False, "empty enemy")
    assert run(hook, "kill_enemy", enemy="Dragon") == (False, "enemy not found: Dragon")
    assert [c[0] for c in rec.calls] == ["_op_kill_party_member", "_op_kill_all_enemies", "_op_kill_enemy"]
    assert rec.calls[2][1] == (5,)


def test_execute_damage_enemy(hook, rec, monkeypatch):
    rec("_op_damage_enemy")
    assert run(hook, "damage_enemy", enemy="2", amount="20000") == (True, None)
    assert run(hook, "damage_enemy", enemy_index=1, amount=3) == (True, None)
    assert rec.calls == [("_op_damage_enemy", (6, 9999)), ("_op_damage_enemy", (5, 3))]
    assert run(hook, "damage_enemy", amount=3) == (False, "empty enemy token")
    assert run(hook, "damage_enemy", enemy="1", amount="x") == (False, "not an int: x")
    monkeypatch.setattr(hook, "_parse_int_or_random", lambda *a, **k: (0, None))
    assert run(hook, "damage_enemy", enemy="1", amount=0) == (False, "amount must be positive")


def test_execute_enemy_none_slot(hook, monkeypatch, rec):
    monkeypatch.setattr(hook, "_enemy_slot_from_token", lambda t: (None, None))
    assert run(hook, "damage_enemy", enemy="x", amount=1) == (False, "enemy")
    assert run(hook, "kill_enemy", enemy="x") == (False, "enemy")


def test_execute_rename(hook, rec):
    rec("_op_rename_character")
    run(hook, "rename_character", current_name="Cloud", new_name="Zack")
    run(hook, "rename_character")
    assert rec.calls == [("_op_rename_character", ("Cloud", "Zack")), ("_op_rename_character", ("", ""))]


def test_execute_set_battle_status(hook, rec):
    rec("_op_set_battle_status")
    assert run(hook, "set_battle_status", character="0", status_effect="Near Death", mode="toggle") == (True, None)
    assert run(hook, "set_battle_status", slot="1", status_effect="Dual") == (True, None)
    assert run(hook, "set_battle_status", character="2", status_effect="Fury") == (True, None)
    assert rec.calls == [("_op_set_battle_status", (0, 0x2, "toggle")),
                         ("_op_set_battle_status", (1, 0x00080000 | 0x08000000, "on")),
                         ("_op_set_battle_status", (2, 0x20, "on"))]
    assert run(hook, "set_battle_status", character="0", status_effect="Itchy") == (False, "unknown status: Itchy")
    assert run(hook, "set_battle_status", status_effect="Fury") == (False, "empty character token")


def test_execute_field_status_byte(hook, rec):
    rec("_op_set_field_status_byte")
    assert run(hook, "set_field_status_byte", character="1", value="16") == (True, None)
    assert run(hook, "set_field_status_byte", character="1") == (True, None)
    assert rec.calls == [("_op_set_field_status_byte", (1, 16)), ("_op_set_field_status_byte", (1, 0))]
    assert run(hook, "set_field_status_byte")[0] is False


def test_execute_set_character_gear(hook, rec):
    rec("_op_set_character_gear_with_inventory")
    assert run(hook, "set_character_gear", character="Cloud", gear="Mythril Saber") == (True, None)
    assert run(hook, "set_character_gear", slot="0", gear_kind="Armor", gear="Iron Bangle") == (True, None)
    assert rec.calls == [("_op_set_character_gear_with_inventory", ("Cloud", "weapon", 1)),
                         ("_op_set_character_gear_with_inventory", ("0", "armor", 1))]
    assert run(hook, "set_character_gear", gear="Nope") == (False, "unknown weapon: Nope")


def test_execute_set_character_gear_none(hook, monkeypatch, rec):
    monkeypatch.setattr(hook, "_resolve_gear_token", lambda k, t: (None, None))
    assert run(hook, "set_character_gear", gear="x") == (False, "gear")


def test_execute_levels_and_stats(hook, rec):
    rec("_op_set_party_level")
    rec("_op_set_enemy_level")
    rec("_op_set_party_stat")
    rec("_op_set_enemy_stat")
    run(hook, "set_party_level", character="Cloud", level="50")
    run(hook, "set_enemy_level", enemy=" Grunt ", level=7)
    run(hook, "set_party_stat", slot="1", stat="str", amount="9")
    run(hook, "set_enemy_stat", enemy="Grunt", stat="def", amount=4)
    assert rec.calls == [("_op_set_party_level", ("Cloud", 50)), ("_op_set_enemy_level", ("Grunt", 7)),
                         ("_op_set_party_stat", ("1", "str", 9)), ("_op_set_enemy_stat", ("Grunt", "def", 4))]
    assert run(hook, "set_party_level", level="x") == (False, "not an int: x")
    assert run(hook, "set_enemy_level") == (False, "missing numeric value")
    assert run(hook, "set_party_stat", amount="x") == (False, "not an int: x")
    assert run(hook, "set_enemy_stat") == (False, "missing numeric value")


def test_execute_none_numeric_fallbacks(hook, monkeypatch, rec):
    monkeypatch.setattr(hook, "_parse_int_or_random", lambda *a, **k: (None, None))
    assert run(hook, "add_party_hp", character="0", amount=1) == (False, "amount must be positive")
    assert run(hook, "set_party_level") == (False, "level")
    assert run(hook, "set_enemy_level") == (False, "level")
    assert run(hook, "set_party_stat") == (False, "amount")
    assert run(hook, "set_enemy_stat") == (False, "amount")
    assert run(hook, "equip_materia") == (False, "slot")
    assert run(hook, "start_battle") == (False, "battle_id")
    assert run(hook, "add_item") == (False, "quantity")
    assert run(hook, "add_materia", materia_level="3") == (False, "materia_level")


def test_execute_menu_colors_materia_battle_items(hook, rec):
    for n in ("_op_set_menu_colors", "_op_equip_materia", "_op_start_battle", "_op_add_item", "_op_add_materia",
              "_op_add_gear", "_op_query_inventory"):
        rec(n)
    run(hook, "set_menu_colors", color="red")
    run(hook, "equip_materia", character="Cloud", slot="12", materia="Fire")
    run(hook, "equip_materia", character="Tifa", slot=-3, gear_kind="armor")
    run(hook, "start_battle", battle_id="300")
    run(hook, "add_item", item="Potion", quantity="4")
    run(hook, "add_materia", materia="Fire")
    run(hook, "add_materia", materia="Fire", materia_level=" ")
    run(hook, "add_materia", materia="Fire", materia_level="2000")
    run(hook, "add_gear", gear="Earring")
    run(hook, "query_inventory", item_name="Potion")
    assert rec.calls == [
        ("_op_set_menu_colors", ("all", "red")),
        ("_op_equip_materia", ("Cloud", "weapon", 7, "Fire")),
        ("_op_equip_materia", ("Tifa", "armor", 0, "")),
        ("_op_start_battle", (300,)),
        ("_op_add_item", ("Potion", 4)),
        ("_op_add_materia", ("Fire", 1)),
        ("_op_add_materia", ("Fire", 1)),
        ("_op_add_materia", ("Fire", 2000)),
        ("_op_add_gear", ("Earring",)),
        ("_op_query_inventory", ("Potion",)),
    ]
    assert run(hook, "equip_materia", slot="x") == (False, "not an int: x")
    assert run(hook, "start_battle", battle_id="x") == (False, "not an int: x")
    assert run(hook, "add_item", quantity="x") == (False, "not an int: x")
    assert run(hook, "add_materia", materia_level="x") == (False, "not an int: x")


def test_execute_equip_materia_empty_character_is_rejected(hook, rec):
    rec("_op_equip_materia")
    # Connector config with no character: {"character": "", "slot": "3", ...}
    run(hook, "equip_materia", character="", slot="3", materia="Fire")
    assert rec.calls == [] or rec.calls[0][1][0] == ""


def test_execute_restores(hook, rec):
    for n in ("_op_restore_menu_words", "_op_restore_battle_speed", "_op_restore_battle_atb_mode",
              "_op_restore_infinite_items", "_op_restore_game_speed", "_finish_field_battle_start"):
        rec(n)
    run(hook, "restore_menu_words", visibility_u16=1)
    run(hook, "restore_battle_speed", prev_byte="7")
    run(hook, "restore_battle_atb_mode", prev_config_byte=64)
    run(hook, "restore_infinite_items")
    run(hook, "restore_game_speed")
    run(hook, "_finish_field_battle_start", field_obj=1)
    assert rec.calls == [("_op_restore_menu_words", ({"visibility_u16": 1},)), ("_op_restore_battle_speed", (7,)),
                         ("_op_restore_battle_atb_mode", (64,)), ("_op_restore_infinite_items", ()),
                         ("_op_restore_game_speed", ()), ("_finish_field_battle_start", ({"field_obj": 1},))]
    assert run(hook, "restore_battle_speed") == (False, "prev_byte")
    assert run(hook, "restore_battle_atb_mode") == (False, "prev_config_byte")


def test_execute_battle_speed_atb_items(hook, rec, monkeypatch):
    rec("_op_set_battle_speed_byte")
    rec("_op_set_battle_atb_mode")
    rec("_op_set_infinite_items")
    run(hook, "set_battle_speed", speed="fast", duration_sec="5")
    run(hook, "set_battle_speed", speed=3, duration_sec=None)
    run(hook, "set_battle_atb_mode", mode="wait", duration_sec=2)
    run(hook, "set_battle_atb_mode")
    run(hook, "set_infinite_items")
    run(hook, "set_infinite_items", enabled="off", duration_sec="none")
    run(hook, "set_infinite_items", enabled="YES", duration_sec="12")
    run(hook, "set_infinite_items", enabled="1", duration_sec="soon")
    run(hook, "set_infinite_items", enabled="1", duration_sec=[1])
    assert rec.calls == [
        ("_op_set_battle_speed_byte", (40, 5)), ("_op_set_battle_speed_byte", (3, 0)),
        ("_op_set_battle_atb_mode", ("wait", 2)), ("_op_set_battle_atb_mode", ("", 0)),
        ("_op_set_infinite_items", (True, 0)), ("_op_set_infinite_items", (False, 0)),
        ("_op_set_infinite_items", (True, 12)), ("_op_set_infinite_items", (True, 0)),
        ("_op_set_infinite_items", (True, 0)),
    ]
    assert run(hook, "set_battle_speed") == (False, "missing speed")
    monkeypatch.setattr(hook, "_parse_battle_speed_token", lambda r: (None, None))
    assert run(hook, "set_battle_speed") == (False, "speed")


def test_execute_field_menu_world_speed_game_speed(hook, rec, monkeypatch):
    rec("_op_field_menu_access")
    rec("_op_world_speed_multiplier")
    rec("_op_set_game_speed")
    run(hook, "set_field_menu_access", enabled="no")
    run(hook, "set_field_menu_access")
    run(hook, "set_world_speed_multiplier", multiplier="4")
    run(hook, "set_world_speed_multiplier")
    run(hook, "set_game_speed", speed="20", duration_sec="3")
    run(hook, "set_game_speed", speed=0.1)
    assert rec.calls == [("_op_field_menu_access", (False,)), ("_op_field_menu_access", (True,)),
                         ("_op_world_speed_multiplier", (4,)), ("_op_world_speed_multiplier", (1,)),
                         ("_op_set_game_speed", (8.0, 3)), ("_op_set_game_speed", (0.25, 0))]
    assert run(hook, "set_game_speed", speed="x") == (False, "not a float: x")
    monkeypatch.setattr(hook, "_parse_float_or_random", lambda r: (None, None))
    assert run(hook, "set_game_speed") == (False, "speed")


@pytest.mark.parametrize("op,arg,addr_attr", [("set_menu_visibility", "enabled", "ADDR_MENU_VISIBILITY"),
                                              ("set_menu_lock", "locked", "ADDR_MENU_LOCKS")])
def test_execute_menu_single_word(hook, mem, op, arg, addr_attr):
    addr = getattr(ff7, addr_attr)
    mem.put(ff7.ADDR_MENU_VISIBILITY, struct.pack("<H", 0x00F0))
    mem.put(ff7.ADDR_MENU_LOCKS, struct.pack("<H", 0x000F))
    before = mem.get(addr, 2)
    assert run(hook, op, menu_name="Item", **{arg: "1"}, duration_sec=30) == (True, None)
    assert struct.unpack("<H", mem.get(addr, 2))[0] & 1 == 1
    assert hook._post_success_timed == [("restore_menu_words", {"visibility_u16": 0xF0, "locks_u16": 0x0F}, 30)]
    hook._post_success_timed.clear()
    assert run(hook, op, menu_name="Item", **{arg: "0"}) == (True, None)
    assert struct.unpack("<H", mem.get(addr, 2))[0] & 1 == 0
    assert hook._post_success_timed == []
    assert run(hook, op, menu_name="Bogus") == (False, "unknown menu: Bogus")
    mem.fail_read.add(ff7.ADDR_MENU_LOCKS)
    assert run(hook, op, menu_name="Item", duration_sec=5) == (False, "read menu words failed")
    assert before is not None


def test_execute_menu_single_word_none_error(hook, monkeypatch):
    monkeypatch.setattr(hook, "_op_set_menu_u16", lambda *a: (False, None))
    assert run(hook, "set_menu_visibility", menu_name="Item") == (False, "menu visibility")
    assert run(hook, "set_menu_lock", menu_name="Item") == (False, "menu lock")


def test_execute_menu_row_access(hook, mem, monkeypatch):
    mem.put(ff7.ADDR_MENU_VISIBILITY, struct.pack("<H", 0x0000))
    mem.put(ff7.ADDR_MENU_LOCKS, struct.pack("<H", 0xFFFF))
    assert run(hook, "set_menu_row_access", menu_name="Save", duration_sec=10) == (True, None)
    assert mem.get(ff7.ADDR_MENU_VISIBILITY, 2) == struct.pack("<H", 1 << 9)
    assert mem.get(ff7.ADDR_MENU_LOCKS, 2) == struct.pack("<H", 0xFFFF & ~(1 << 9))
    assert hook._post_success_timed == [("restore_menu_words", {"visibility_u16": 0, "locks_u16": 0xFFFF}, 10)]
    hook._post_success_timed.clear()
    assert run(hook, "set_menu_row_access", menu_name="Save", access="block") == (True, None)
    assert mem.get(ff7.ADDR_MENU_VISIBILITY, 2) == b"\x00\x00"
    assert hook._post_success_timed == []
    assert run(hook, "set_menu_row_access", menu_name="Nope") == (False, "unknown menu: Nope")
    mem.fail_write.add(ff7.ADDR_MENU_LOCKS)
    assert run(hook, "set_menu_row_access", menu_name="Save") == (False, "write menu word failed")
    mem.fail_read.add(ff7.ADDR_MENU_VISIBILITY)
    assert run(hook, "set_menu_row_access", menu_name="Save", duration_sec=1) == (False, "read menu words failed")
    monkeypatch.setattr(hook, "_op_set_menu_u16", lambda *a: (False, None))
    assert run(hook, "set_menu_row_access", menu_name="Save") == (False, "menu visibility")
    seq = iter([(True, None), (False, None)])
    monkeypatch.setattr(hook, "_op_set_menu_u16", lambda *a: next(seq))
    assert run(hook, "set_menu_row_access", menu_name="Save") == (False, "menu lock")


# --------------------------------------------------------------------------- snapshot


EMPTY_KEYS = {"hook", "attached", "error", "battle", "battle_ui", "current_module", "party", "characters", "enemies",
              "gil", "playtime_seconds", "playtime_text", "avg_party_level", "equipped_materia_count",
              "battle_atb_mode", "battle_speed_byte", "battle_mode_ram", "recent_item", "battle_log",
              "inventory_query", "debug"}


def test_snapshot_unsupported(hook, mem):
    mem.available = False
    s = hook.snapshot()
    assert set(s) == EMPTY_KEYS and s["attached"] is False and s["debug"]["stage"] == "unsupported_os"
    hook._mem = None
    assert hook.snapshot()["error"] == "FF7 hook requires Windows"


def test_snapshot_attach_failed(hook, mem):
    hook._battle_ui_latched = True
    mem.attach_result = (False, "nope")
    s = hook.snapshot()
    assert s["error"] == "nope" and s["debug"] == {"stage": "attach_failed", "message": "nope"}
    assert hook._battle_ui_latched is False
    mem.attach_result = (True, None)
    mem.attached = False
    s = hook.snapshot()
    assert s["error"] == "Not attached" and s["debug"]["message"] == "not attached"


def test_snapshot_read_module_failed_closes(hook, mem):
    mem.fail_read.add(MODULE)
    s = hook.snapshot()
    assert s["error"] == "Read failed (access denied?)" and s["debug"]["stage"] == "read_current_module_failed"
    assert s["debug"]["pid"] == 4242 and mem.closed == 1


def test_snapshot_field(hook, mem, loaded):
    set_module(mem, 1)
    sm = bytearray(loaded)
    struct.pack_into("<I", sm, ff7.SAVE_OFF_PLAYTIME_SEC, 3 * 3600 + 25 * 60 + 7)
    sm[ff7.SAVE_OFF_LOCATION_NAME: ff7.SAVE_OFF_LOCATION_NAME + 12] = ff7._encode_ff7_name("Sector 7")
    sm[ff7.SAVE_OFF_GENERAL_CONFIG] = 0x80
    sm[ff7.SAVE_OFF_BATTLE_SPEED] = 77
    sm[ff7.SAVE_OFF_WIN_UL: ff7.SAVE_OFF_WIN_UL + 3] = b"\x01\x02\x03"
    mem.put(SM_ADDR, sm)
    s = hook.snapshot()
    assert s["attached"] is True and s["battle"] is False and s["current_module_name"] == "Field"
    assert s["gil"] == 1000 and s["playtime_text"] == "03:25:07" and s["field_name"] == "Sector 7"
    assert [p["name"] for p in s["party"]] == ["Cloud", "Barret", "Tifa"]
    assert len(s["characters"]) == 9 and s["avg_party_level"] == 11
    assert s["battle_atb_mode"] == "wait" and s["battle_speed_byte"] == 77 and s["battle_mode_ram"] is None
    assert s["menu_theme"]["bg_ul"] == "#000000"  # live display palette (all zero) wins over savemap
    assert s["debug"]["menu_theme_source"] == "live" and s["debug"]["party_slot_ids"] == [0, 1, 2]
    assert s["inventory_query"] is None and hook._last_snapshot is s


def test_snapshot_menu_theme_from_savemap(hook, mem, loaded, monkeypatch):
    set_module(mem, 1)
    sm = bytearray(loaded)
    sm[ff7.SAVE_OFF_WIN_UL: ff7.SAVE_OFF_WIN_UL + 3] = b"\x10\x20\x30"
    mem.put(SM_ADDR, sm)
    mem.fail_read.add(ff7.ADDR_MENU_COLOR_DISPLAY_BASE)
    hook._last_inventory_query = {"item_name": "Potion"}
    s = hook.snapshot()
    assert s["menu_theme"]["bg_ul"] == "#102030" and s["debug"]["menu_theme_source"] == "savemap"
    assert s["inventory_query"] == {"item_name": "Potion"}


def test_snapshot_no_savemap(hook, mem):
    set_module(mem, 1)
    mem.fail_read.add(ff7.ADDR_MENU_COLOR_DISPLAY_BASE)
    s = hook.snapshot()
    assert s["party"] == [] and s["gil"] == 0 and "menu_theme" not in s
    assert s["debug"]["menu_theme_source"] == "none" and s["debug"]["savemap_read_bytes"] == 0
    assert "party_slot_ids" not in s["debug"] and s["battle_atb_mode"] is None


def test_snapshot_short_savemap_struct_error(hook, mem, monkeypatch):
    set_module(mem, 1)
    monkeypatch.setattr(hook, "_read_savemap", lambda: (b"\x01" * 0x100, SM_ADDR))
    s = hook.snapshot()
    assert s["gil"] == 0 and s["debug"]["savemap_read_bytes"] == 0x100


def test_snapshot_battle(hook, mem, loaded):
    set_module(mem, 2)
    mem.put(ff7.ADDR_PARTY_MEMBER_IDS, bytes([0, 0xFF, 0xFF]))
    mem.put(ff7.ADDR_PARTY_MEMBER_NAMES, ff7._encode_ff7_name("Cloud"))
    battle_actor(mem, 0, hp=90, max_hp=200, level=10)
    battle_actor(mem, 1, hp=0, max_hp=0)          # nameless, no HP -> dropped
    for s in range(6):
        mem.put(ff7.ADDR_ENEMY_OBJ_BASE + s * ff7.ENEMY_OBJ_STRIDE, b"\xff")
    mem.put(ff7.ADDR_ENEMY_OBJ_BASE, b"\x00")
    battle_actor(mem, 4, hp=50, max_hp=50)
    mem.put(ff7.ADDR_ENEMY_DATA_BASE, ff7._encode_ff7_name("Grunt", 24))
    mem.put(ff7.ADDR_BATTLE_MODE, b"\x07")
    hook._bl_name_cache = {"command": [], "attack": []}
    s = hook.snapshot()
    assert s["battle"] is True and s["battle_ui"] is True and s["battle_mode_ram"] == 7
    assert [p["name"] for p in s["party"]] == ["Cloud"]
    assert s["party"][0]["weapon_name"] == "Buster Sword"  # gear merged from savemap
    assert [e["name"] for e in s["enemies"]] == ["Grunt"]
    assert s["debug"]["enemy_count"] == 1
    # back to field: battle UI stays latched for one poll, then clears
    set_module(mem, 1)
    assert hook.snapshot()["battle_ui"] is True
    assert hook.snapshot()["battle_ui"] is False


def test_snapshot_battle_without_allies(hook, mem, loaded):
    set_module(mem, 2)
    for slot in range(3):
        mem.fail_read.add(ff7.ADDR_BATTLE_CHAR_BASE + slot * ff7.BATTLE_ACTOR_STRIDE)
    for s in range(6):
        mem.put(ff7.ADDR_ENEMY_OBJ_BASE + s * ff7.ENEMY_OBJ_STRIDE, b"\xff")
    hook._bl_name_cache = {"command": [], "attack": []}
    s = hook.snapshot()
    assert [p["name"] for p in s["party"]] == ["Cloud", "Barret", "Tifa"]  # falls back to field party


def test_snapshot_battle_allies_without_savemap(hook, mem):
    set_module(mem, 2)
    mem.put(ff7.ADDR_PARTY_MEMBER_IDS, bytes([0xFF, 0xFF, 0xFF]))
    battle_actor(mem, 0, hp=5, max_hp=5)
    for s in range(6):
        mem.put(ff7.ADDR_ENEMY_OBJ_BASE + s * ff7.ENEMY_OBJ_STRIDE, b"\xff")
    hook._bl_name_cache = {"command": [], "attack": []}
    s = hook.snapshot()
    assert len(s["party"]) == 1 and "weapon_name" not in s["party"][0]


# --------------------------------------------------------------------------- Ff7GameHook


class FakeDB:
    def __init__(self):
        self.store = {}
        self.fail_reads = False
        self.raise_on = None
        self.writes = []

    def get_data_checked(self, path):
        if self.raise_on == "read":
            raise RuntimeError("db exploded")
        if self.fail_reads:
            return {}, False
        return self.store.get(path), True

    def get_data(self, path):
        if self.raise_on == "read":
            raise RuntimeError("db exploded")
        return self.store.get(path)

    def set_data(self, path, data):
        if self.raise_on == "write":
            raise RuntimeError("db exploded")
        self.writes.append((path, data))
        self.store[path] = data
        return True


@pytest.fixture
def env(monkeypatch):
    from modules import database_manager as dbm
    import modules.template_config_parser as tcp

    db = FakeDB()
    for name in ("get_data_checked", "get_data", "set_data"):
        monkeypatch.setattr(dbm.database_manager, name, getattr(db, name))
    cfg = {"elements": [{"id": "BossMatchSubstrings", "value": "reno\nboss"}]}
    parser = types.SimpleNamespace(load_config=lambda name, copy_result=True: cfg, fail=False)

    def get_parser():
        if parser.fail:
            raise RuntimeError("no config")
        return parser

    monkeypatch.setattr(tcp, "get_shared_parser", get_parser)
    monkeypatch.setattr(ff7, "get_memory_backend", lambda: None)
    clock = {"t": 1000.0, "wall": 5000.0}
    monkeypatch.setattr(ff7.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(ff7.time, "time", lambda: clock["wall"])
    return types.SimpleNamespace(db=db, cfg=cfg, parser=parser, clock=clock)


def make_game(env, mem=None):
    g = ff7.Ff7GameHook()
    g._core = ff7.FF7Hook(memory=mem or FakeMemory())
    return g


BOSS = ff7._FF7_BOSS_LOG_PATH
OVR = ff7._FF7_CONNECTOR_OVERRIDES_PATH


def test_game_init_loads_boss_log(env):
    env.db.store[BOSS] = {"names": ["Reno"], "last": "Reno"}
    g = make_game(env)
    assert g._boss_tracker.bosses_dict() == {"names": ["Reno"], "last": "Reno", "count": 1}
    assert g._boss_log_unread is False and g.hook_id == "ff7"


@pytest.mark.parametrize("stored", [None, "x", {"names": "x"}, {"names": ["A"], "last": 5}])
def test_game_init_boss_log_shapes(env, stored):
    env.db.store[BOSS] = stored
    g = make_game(env)
    names = g._boss_tracker.bosses_dict()["names"]
    assert names == (["A"] if isinstance(stored, dict) and stored.get("names") == ["A"] else [])


def test_game_boss_log_unread_and_error(env, caplog):
    env.db.fail_reads = True
    g = make_game(env)
    assert g._boss_log_unread is True
    env.db.fail_reads = False
    env.db.raise_on = "read"
    g._load_boss_log()
    assert "boss log load failed" in caplog.text


def test_stored_boss_log():
    f = ff7.Ff7GameHook._stored_boss_log
    assert f(None) == ([], "")
    assert f({"names": "x"}) == ([], "")
    assert f({"names": ["A"], "last": 3}) == (["A"], "")
    assert f({"names": ["A"], "last": "A"}) == (["A"], "A")


def test_persist_boss_log_merge_and_errors(env, caplog):
    env.db.fail_reads = True
    g = make_game(env)
    g._boss_tracker.restore(["New"], "New")
    g._persist_boss_log()
    assert env.db.writes == []
    env.db.fail_reads = False
    env.db.store[BOSS] = {"names": ["Old"], "last": "Old"}
    g._persist_boss_log()
    assert env.db.writes[-1][1]["names"] == ["New", "Old"] and g._boss_log_unread is False
    env.db.raise_on = "write"
    g._persist_boss_log()
    assert "persist failed" in caplog.text


def test_game_platform_and_process(env, monkeypatch):
    g = make_game(env)
    for plat, ok in (("win32", True), ("darwin", False)):
        monkeypatch.setattr(sys, "platform", plat)
        assert g.is_platform_supported() is ok
    mem = g._core._mem
    monkeypatch.setitem(sys.modules, "psutil", types.SimpleNamespace(pid_exists=lambda pid: pid == 4242))
    assert g.is_process_running() is True
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert g.is_process_running() is False
    mem.attached = False
    monkeypatch.setattr(ff7, "is_process_running", lambda names: names == ("ff7_en.exe", "ff7.exe"))
    assert g.is_process_running() is True


def test_game_process_attached_without_pid(env, monkeypatch):
    g = make_game(env)
    monkeypatch.setattr(FakeMemory, "pid", property(lambda self: 0))
    monkeypatch.setattr(ff7, "is_process_running", lambda names: False)
    assert g.is_process_running() is False


def test_game_config_reload(env, caplog):
    g = make_game(env)
    env.cfg["elements"] = [{"id": "BossMatchSubstrings", "value": "Grunt"}]
    g.on_config_reloaded()
    assert g._boss_tracker._substr == frozenset({"grunt"})
    env.parser.fail = True
    g.on_config_reloaded()
    assert "reload failed" in caplog.text


def test_game_handle_command(env):
    env.db.store[BOSS] = {"names": ["Reno"]}
    g = make_game(env)
    g.handle_command("other", {})
    assert env.db.writes == []
    g._boss_log_unread = True
    g.handle_command("clear_bosses", {})
    assert env.db.writes[-1] == (BOSS, {"names": [], "last": "", "count": 0})


def test_game_idle_snapshot(env, monkeypatch):
    g = make_game(env)
    d = g.idle_snapshot(disabled=True)
    assert d["disabled"] is True and d["debug"]["stage"] == "ff7_disabled_in_settings" and "bosses" in d
    monkeypatch.setattr(sys, "platform", "darwin")
    assert g.idle_snapshot()["error"] == "FF7 hook requires Windows"
    monkeypatch.setattr(sys, "platform", "win32")
    assert g.idle_snapshot()["debug"] == {"stage": "process_not_running"}


def test_game_close(env):
    g = make_game(env)
    g._last_attached = True
    g._timed_jobs.append((1, "op", {}))
    g._paused_timed_jobs.append((1, "op", {}))
    g.close()
    assert g._last_attached is None and g._timed_jobs == [] and g._paused_timed_jobs == []
    assert g._core._mem.closed == 1


def test_timed_jobs_lifecycle(env):
    g = make_game(env)
    g._schedule_timed_job(0, "never", {})
    g._schedule_timed_job(10, "restore_a", {"x": 1})
    g._schedule_timed_job(30, "restore_b", {})
    assert [j[1] for j in g._timed_jobs] == ["restore_a", "restore_b"]
    fired = []
    g.drain_timed_jobs(lambda op, kw: fired.append((op, kw)))
    assert fired == []
    env.clock["t"] += 15
    g.drain_timed_jobs(lambda op, kw: fired.append((op, kw)))
    assert fired == [("restore_a", {"x": 1})] and len(g._timed_jobs) == 1
    # detach pauses the remaining 15s; reattach later resumes from then
    g.on_detached()
    assert g._timed_jobs == [] and g._paused_timed_jobs == [(15.0, "restore_b", {})]
    g.on_detached()  # nothing running: no-op
    env.clock["t"] += 100
    g._resume_timed_jobs()
    assert g._timed_jobs == [(1115.0 + 15.0, "restore_b", {})] and g._paused_timed_jobs == []
    g._resume_timed_jobs()  # nothing paused
    g._paused_timed_jobs = [(0.0, "expired", {})]
    g._resume_timed_jobs()
    assert len(g._timed_jobs) == 1
    g._timed_jobs = []
    g.drain_timed_jobs(lambda *a: fired.append(a))  # empty: nothing


def test_on_attached_resumes_and_replays(env, monkeypatch):
    g = make_game(env)
    calls = []
    monkeypatch.setattr(g, "_resume_timed_jobs", lambda: calls.append("resume"))
    monkeypatch.setattr(g, "_replay_persisted_overrides", lambda: calls.append("replay"))
    g.on_attached()
    assert calls == ["resume", "replay"]
    enq = object()
    g.set_write_enqueue(enq)
    assert g._enqueue_write is enq


def test_persist_connector_override(env):
    g = make_game(env)
    g._persist_connector_override("add_gil", {"amount": 1})
    assert env.db.writes == []
    g._persist_connector_override("set_game_speed", {"speed": "2", "duration_sec": "30"})
    data = env.db.store[OVR]
    assert data["version"] == 1 and data["entries"] == [
        {"key": "set_game_speed", "op": "set_game_speed", "kwargs": {"speed": "2", "duration_sec": "30"},
         "expires_at_ms": 5000 * 1000 + 30000}]
    g._persist_connector_override("set_game_speed", {"speed": "3", "duration_sec": "bad"})
    g._persist_connector_override("set_menu_lock", {"menu_name": "Save", "obj": object()})
    entries = env.db.store[OVR]["entries"]
    assert [e["key"] for e in entries] == ["set_game_speed", "set_menu_lock:save"]
    assert entries[0]["kwargs"]["speed"] == "3" and entries[0]["expires_at_ms"] is None
    assert entries[1]["kwargs"]["obj"].startswith("<object")


def test_persist_connector_override_bad_stored_and_errors(env, caplog):
    g = make_game(env)
    env.db.store[OVR] = {"entries": "bad", "extra": 1}
    g._persist_connector_override("set_menu_colors", {"color": "red"})
    assert env.db.store[OVR]["extra"] == 1 and len(env.db.store[OVR]["entries"]) == 1
    env.db.store[OVR] = {"entries": ["junk"]}
    g._persist_connector_override("set_menu_colors", {"color": "blue"})
    assert env.db.store[OVR]["entries"][0] == "junk"
    env.db.raise_on = "read"
    g._persist_connector_override("set_menu_colors", {})
    assert "connector persist failed" in caplog.text


def test_replay_persisted_overrides(env):
    g = make_game(env)
    g._replay_persisted_overrides()  # no enqueue: nothing
    sent = []
    g.set_write_enqueue(lambda op, kw: sent.append((op, kw)))
    g._replay_persisted_overrides()  # nothing stored
    env.db.store[OVR] = {"entries": "x"}
    g._replay_persisted_overrides()
    assert sent == [] and env.db.writes == []
    now = 5000 * 1000
    env.db.store[OVR] = {"entries": [
        {"key": "set_game_speed", "op": "set_game_speed", "kwargs": {"speed": "2"}, "expires_at_ms": None},
        {"key": "a", "op": "set_battle_speed", "kwargs": {"speed": "1"}, "expires_at_ms": now + 9500},
        {"key": "b", "op": "set_menu_lock", "kwargs": {}, "expires_at_ms": now - 1},
        {"key": "c", "op": "set_menu_lock", "kwargs": {}, "expires_at_ms": "soon"},
        {"key": "d", "op": "set_menu_lock", "kwargs": {}, "expires_at_ms": now + 10},
        "junk",
    ]}
    g._pending_override_rows = {"set_game_speed": {"key": "set_game_speed", "op": "set_game_speed",
                                                   "kwargs": {"speed": "4"}, "expires_at_ms": None}}
    g._replay_persisted_overrides()
    assert sent == [("set_battle_speed", {"speed": "1", "duration_sec": 9}),
                    ("set_menu_lock", {"duration_sec": 1}),
                    ("set_game_speed", {"speed": "4"})]
    assert [e["key"] for e in env.db.store[OVR]["entries"]] == ["a", "d", "set_game_speed"]
    assert g._pending_override_rows == {}


def test_replay_persisted_overrides_error(env, caplog):
    g = make_game(env)
    g.set_write_enqueue(lambda op, kw: None)
    env.db.raise_on = "read"
    g._replay_persisted_overrides()
    assert "replay failed" in caplog.text


def test_game_execute_operation(env, monkeypatch):
    g = make_game(env)
    core = g._core

    def fake_exec(op, kw):
        if op == "set_battle_speed":
            core._post_success_timed.append(("restore_battle_speed", {"prev_byte": 1}, 20))
            return True, None
        if op == "query_inventory":
            core._last_inventory_query = {"item_name": "Potion", "quantity": 2, "kind": "unified_item"}
            return True, None
        core._post_success_timed.append(("x", {}, 5))
        return False, "nope"

    monkeypatch.setattr(core, "execute_operation", fake_exec)
    assert g.execute_operation("set_battle_speed", {"speed": "1"}) == (True, None, None)
    assert g._timed_jobs == [(1020.0, "restore_battle_speed", {"prev_byte": 1})]
    assert env.db.store[OVR]["entries"][0]["op"] == "set_battle_speed"
    ok, err, out = g.execute_operation("query_inventory", {"item_name": "Potion"})
    assert ok and out == {"item_name": "Potion", "quantity": "2", "resolved_name": "", "kind": "unified_item",
                          "error": ""}
    assert g._last_inventory_query_ui["quantity"] == 2
    assert g.execute_operation("add_gil", {}) == (False, "nope", None)
    assert core._post_success_timed == [] and len(g._timed_jobs) == 1
    core._last_inventory_query = None
    monkeypatch.setattr(core, "execute_operation", lambda op, kw: (False,))
    assert g.execute_operation("query_inventory", {}) == (False, None, None)


def test_game_tick_unsupported(env, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert make_game(env).tick()["debug"]["stage"] == "unsupported_os"


@pytest.fixture
def win(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")


def test_game_tick_attach_transitions_and_bosses(env, win, monkeypatch):
    g = make_game(env)
    snaps = iter([
        {"attached": True, "battle": True, "enemies": [{"slot": 4, "hp": 10, "name": "Reno"}]},
        {"attached": True, "battle": True, "enemies": [{"slot": 4, "hp": 0, "name": "Reno"}]},
        {"attached": False},
    ])
    monkeypatch.setattr(g._core, "snapshot", lambda: next(snaps))
    events = []
    monkeypatch.setattr(g, "on_attached", lambda: events.append("att"))
    monkeypatch.setattr(g, "on_detached", lambda: events.append("det"))
    g._last_inventory_query_ui = {"item_name": "Potion"}
    s1 = g.tick()
    assert s1["inventory_query"] == {"item_name": "Potion"} and s1["bosses"]["count"] == 0
    s2 = g.tick()
    assert s2["bosses"]["names"] == ["Reno"] and env.db.writes[-1][0] == BOSS
    g.tick()
    assert events == ["att", "det"] and g._last_attached is False


def test_game_tick_pending_battle(env, win, monkeypatch):
    g = make_game(env)
    core = g._core
    state = {"mod": 1}
    monkeypatch.setattr(core, "snapshot", lambda: {"attached": True, "current_module": state["mod"]})
    monkeypatch.setattr(g, "on_attached", lambda: None)
    results = iter([(True, None), (False, "busy")])

    def start(bid):
        core._post_success_timed.append(("_finish_field_battle_start", {"field_obj": 1}, 0.05))
        return next(results)

    monkeypatch.setattr(core, "_start_battle_now", start)
    core._pending_battle_id = 7
    g.tick()
    assert core._pending_battle_id is None and g._timed_jobs[0][1] == "_finish_field_battle_start"
    core._pending_battle_id = 8
    g.tick()
    assert core._pending_battle_id == 8  # failed: requeued
    state["mod"] = 5
    g.tick()
    assert core._pending_battle_id == 8  # still waiting for field/world
    g.tick()  # nothing pending path is covered via consume returning None next
    monkeypatch.setattr(core, "consume_pending_battle", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert g.tick()["attached"] is True


def test_game_tick_snapshot_exception(env, win, monkeypatch):
    g = make_game(env)
    monkeypatch.setattr(g._core, "snapshot", lambda: (_ for _ in ()).throw(ValueError("bad read")))
    s = g.tick()
    assert s["error"] == "bad read" and s["debug"] == {"stage": "snapshot_exception", "message": "bad read"}
    assert "bosses" in s


def test_snapshot_skips_nameless_zero_hp_enemy(hook, mem, loaded):
    set_module(mem, 2)
    for slot in range(3):
        mem.fail_read.add(ff7.ADDR_BATTLE_CHAR_BASE + slot * ff7.BATTLE_ACTOR_STRIDE)
    for s in range(6):
        mem.put(ff7.ADDR_ENEMY_OBJ_BASE + s * ff7.ENEMY_OBJ_STRIDE, b"\xff")
    mem.put(ff7.ADDR_ENEMY_OBJ_BASE + ff7.ENEMY_OBJ_STRIDE, b"\x01")  # slot 5: blank name, no HP
    mem.put(ff7.ADDR_ENEMY_DATA_BASE + ff7.ENEMY_DATA_STRIDE, b"\xff")
    battle_actor(mem, 5, hp=0, max_hp=0)
    hook._bl_name_cache = {"command": [], "attack": []}
    assert hook.snapshot()["enemies"] == []


def test_persist_connector_override_unreadable_keeps_pending(env):
    g = make_game(env)
    env.db.store[OVR] = {"version": 1, "entries": [{"key": "stored", "op": "set_menu_colors", "kwargs": {}}]}
    env.db.fail_reads = True
    g._persist_connector_override("set_game_speed", {"speed": "2"})
    assert env.db.writes == [] and list(g._pending_override_rows) == ["set_game_speed"]
    env.db.fail_reads = False
    g._persist_connector_override("set_battle_speed", {"speed": "1"})
    keys = [e["key"] for e in env.db.store[OVR]["entries"]]
    assert keys == ["stored", "set_game_speed", "set_battle_speed"] and g._pending_override_rows == {}
