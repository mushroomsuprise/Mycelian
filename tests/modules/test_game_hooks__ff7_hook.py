# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/game_hooks/ff7_hook.py, part 1: module-level helpers.

Asset loaders, name/byte codecs, savemap and gear parsers, menu colors, catalog helpers,
game-speed math and the connector config -> kwargs mapping. The FF7Hook class is covered in
the _part2/_part3 files and the Ff7GameHook wrapper in _part4.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.game_hooks import ff7_hook as ff7  # noqa: E402


SM = ff7.SAVEMAP_SIZE


# --------------------------------------------------------------------------- savemap builders


def blank_savemap() -> bytearray:
    sm = bytearray(SM)
    sm[ff7.SAVE_OFF_INV_ITEMS: ff7.SAVE_OFF_INV_ITEMS + ff7.INV_ITEM_SLOTS * 2] = b"\xff" * (ff7.INV_ITEM_SLOTS * 2)
    sm[ff7.SAVE_OFF_INV_MATERIA: ff7.SAVE_OFF_INV_MATERIA + ff7.INV_MATERIA_SLOTS * 4] = b"\xff" * (ff7.INV_MATERIA_SLOTS * 4)
    for off in ff7._CHAR_BLOCK:
        sm[off + ff7.REC_OFF_MATERIA_WEAPON: off + ff7.REC_OFF_MATERIA_WEAPON + 64] = b"\xff" * 64
        sm[off + ff7.REC_OFF_ACCESSORY] = 0xFF
    sm[ff7.SAVE_OFF_PARTY_SLOTS: ff7.SAVE_OFF_PARTY_SLOTS + 3] = b"\xff\xff\xff"
    return sm


def put_char(sm, cid, *, name="Cloud", level=7, weapon=0, armor=0, acc=0xFF, materia_w=(), materia_a=(),
             row=ff7.ROW_ORDER_FRONT):
    off = ff7._CHAR_BLOCK[cid]
    sm[off + ff7.REC_OFF_LEVEL] = level
    sm[off + ff7.REC_OFF_NAME: off + ff7.REC_OFF_NAME + 12] = ff7._encode_ff7_name(name)
    sm[off + ff7.REC_OFF_WEAPON] = weapon
    sm[off + ff7.REC_OFF_ARMOR] = armor
    sm[off + ff7.REC_OFF_ACCESSORY] = acc
    sm[off + ff7.REC_OFF_ROW] = row
    for i, (mid, ap) in enumerate(materia_w):
        sm[off + 0x40 + i * 4: off + 0x44 + i * 4] = bytes([mid]) + ap.to_bytes(3, "little")
    for i, (mid, ap) in enumerate(materia_a):
        sm[off + 0x60 + i * 4: off + 0x64 + i * 4] = bytes([mid]) + ap.to_bytes(3, "little")
    return off


def set_party(sm, ids):
    for i, cid in enumerate(ids):
        sm[ff7.SAVE_OFF_PARTY_SLOTS + i] = cid


# --------------------------------------------------------------------------- asset directory + loaders


def test_asset_dir_dev_layout():
    assert ff7._ff7_gear_asset_dir() == ROOT / "assets" / "ff7"


def test_asset_dir_frozen_variants(monkeypatch, tmp_path):
    monkeypatch.setattr(ff7.sys, "frozen", True, raising=False)
    monkeypatch.setattr(ff7.sys, "executable", str(tmp_path / "app" / "Mycelian.exe"))
    monkeypatch.setattr(ff7.sys, "_MEIPASS", str(tmp_path / "mei"), raising=False)
    # _MEIPASS without assets -> next to the exe
    assert ff7._ff7_gear_asset_dir() == tmp_path / "app" / "assets" / "ff7"
    (tmp_path / "mei" / "assets" / "ff7").mkdir(parents=True)
    assert ff7._ff7_gear_asset_dir() == tmp_path / "mei" / "assets" / "ff7"
    monkeypatch.delattr(ff7.sys, "_MEIPASS")
    assert ff7._ff7_gear_asset_dir() == tmp_path / "app" / "assets" / "ff7"


@pytest.fixture
def fresh_assets(monkeypatch, tmp_path):
    """Point the asset loaders at an empty tmp folder with fresh tables (restored after)."""
    monkeypatch.setattr(ff7, "_GEAR_ASSET_DIR", tmp_path)
    for name in ("_WEAPON_MATERIA_SLOT_TYPES", "_ARMOR_MATERIA_SLOT_TYPES", "_MATERIA_NAMES_EN", "_MATERIA_ORB_BY_ID",
                 "_WEAPON_NAMES_EN", "_ARMOR_NAMES_EN", "_ACCESSORY_NAMES_EN", "_ITEM_NAMES_EN",
                 "_BATTLE_LOG_CMD_EN", "_BATTLE_LOG_ATK128_EN", "_EQUIP_ALLOW"):
        monkeypatch.setattr(ff7, name, {})
    for flag in ("_GEAR_LAYOUT_ASSETS_LOADED", "_BATTLE_LOG_NAME_ASSETS_LOADED", "_EQUIP_ALLOW_LOADED"):
        monkeypatch.setattr(ff7, flag, False)
    return tmp_path


def _write(dir_: Path, name: str, data) -> None:
    (dir_ / name).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def test_equip_allowlists_missing_file(fresh_assets):
    ff7._load_equip_allowlists()
    assert ff7._EQUIP_ALLOW == {} and ff7._EQUIP_ALLOW_LOADED is True
    assert ff7._allowed_ids_for_char_gear(0, "weapon") is None


def test_equip_allowlists_invalid_json(fresh_assets, caplog):
    _write(fresh_assets, "equip_allowlists.json", "{nope")
    ff7._load_equip_allowlists()
    assert ff7._EQUIP_ALLOW == {} and "unreadable" in caplog.text


def test_equip_allowlists_rules(fresh_assets):
    _write(fresh_assets, "equip_allowlists.json", {
        "0": {"weapon": [1, "2", "x", None], "armor": [], "accessory": None, "bad": "str"},
        "1": "notadict",
        "2": {"weapon": ["x"]},
    })
    assert ff7._allowed_ids_for_char_gear(0, "weapon") == {1, 2}
    assert ff7._allowed_ids_for_char_gear(0, "armor") == set()
    assert ff7._allowed_ids_for_char_gear(0, "accessory") is None
    assert ff7._allowed_ids_for_char_gear(0, "bad") is None
    assert ff7._allowed_ids_for_char_gear(0, "missing") is None
    assert ff7._allowed_ids_for_char_gear(1, "weapon") is None
    assert ff7._allowed_ids_for_char_gear(2, "weapon") == set()
    assert ff7._allowed_ids_for_char_gear(5, "weapon") is None
    # loaded once: later file changes are ignored
    _write(fresh_assets, "equip_allowlists.json", {})
    assert ff7._allowed_ids_for_char_gear(0, "weapon") == {1, 2}


def test_battle_log_assets_missing_and_invalid(fresh_assets, caplog):
    ff7._load_battle_log_name_assets()
    assert ff7._BATTLE_LOG_NAME_ASSETS_LOADED is True and ff7._BATTLE_LOG_CMD_EN == {}
    ff7._BATTLE_LOG_NAME_ASSETS_LOADED = False
    _write(fresh_assets, "battle_log_names_en.json", "[broken")
    ff7._load_battle_log_name_assets()
    assert "unreadable" in caplog.text
    ff7._BATTLE_LOG_NAME_ASSETS_LOADED = False
    _write(fresh_assets, "battle_log_names_en.json", [1, 2])
    ff7._load_battle_log_name_assets()
    assert ff7._BATTLE_LOG_CMD_EN == {}


def test_battle_log_assets_loaded(fresh_assets):
    _write(fresh_assets, "battle_log_names_en.json", {
        "commands": {"1": " Attack ", "2": "", "3": None},
        "attacks_128": {"0": "Cure", "5": "  "},
    })
    ff7._load_battle_log_name_assets()
    assert ff7._BATTLE_LOG_CMD_EN == {"1": "Attack"}
    assert ff7._BATTLE_LOG_ATK128_EN == {"0": "Cure"}
    ff7._load_battle_log_name_assets()  # second call: no-op


def test_battle_log_assets_non_dict_sections(fresh_assets):
    ff7._BATTLE_LOG_CMD_EN["9"] = "kept"
    _write(fresh_assets, "battle_log_names_en.json", {"commands": [], "attacks_128": "x"})
    ff7._load_battle_log_name_assets()
    assert ff7._BATTLE_LOG_CMD_EN == {"9": "kept"}


def test_gear_layout_assets_loaded(fresh_assets):
    _write(fresh_assets, "weapon_materia_slot_types.json", {"1": [1, 2]})
    _write(fresh_assets, "armor_materia_slot_types.json", {"2": [3]})
    _write(fresh_assets, "materia_names_en.json", {"0": "MP Plus"})
    _write(fresh_assets, "materia_orb_by_id.json", {"0": "materia_purple"})
    _write(fresh_assets, "weapon_names_en.json", {"0": "Buster Sword"})
    _write(fresh_assets, "armor_names_en.json", {"0": "Bronze Bangle"})
    _write(fresh_assets, "accessory_names_en.json", {"0": "Power Wrist"})
    _write(fresh_assets, "item_names_en.json", {"0": "Potion"})
    ff7._load_ff7_gear_layout_assets()
    assert ff7._WEAPON_MATERIA_SLOT_TYPES == {"1": [1, 2]}
    assert ff7._ARMOR_MATERIA_SLOT_TYPES == {"2": [3]}
    assert ff7._MATERIA_NAMES_EN == {"0": "MP Plus"} and ff7._MATERIA_ORB_BY_ID == {"0": "materia_purple"}
    assert ff7._WEAPON_NAMES_EN["0"] == "Buster Sword" and ff7._ARMOR_NAMES_EN["0"] == "Bronze Bangle"
    assert ff7._ACCESSORY_NAMES_EN["0"] == "Power Wrist" and ff7._ITEM_NAMES_EN["0"] == "Potion"
    assert ff7._BATTLE_LOG_NAME_ASSETS_LOADED is True
    ff7._WEAPON_NAMES_EN.clear()
    ff7._load_ff7_gear_layout_assets()  # already loaded: no reload
    assert ff7._WEAPON_NAMES_EN == {}


def test_gear_layout_assets_missing_files(fresh_assets):
    ff7._load_ff7_gear_layout_assets()
    assert ff7._WEAPON_NAMES_EN == {} and ff7._GEAR_LAYOUT_ASSETS_LOADED is True


def test_gear_layout_assets_bad_json(fresh_assets, caplog):
    _write(fresh_assets, "weapon_materia_slot_types.json", "{{")
    ff7._load_ff7_gear_layout_assets()
    assert "invalid JSON" in caplog.text


def test_gear_layout_assets_os_error(fresh_assets, monkeypatch, caplog):
    _write(fresh_assets, "weapon_materia_slot_types.json", {})
    real = Path.read_text

    def boom(self, *a, **k):
        if self.name == "weapon_materia_slot_types.json":
            raise OSError("locked")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", boom)
    ff7._load_ff7_gear_layout_assets()
    assert "unreadable" in caplog.text


def test_materia_slot_types(fresh_assets):
    _write(fresh_assets, "weapon_materia_slot_types.json", {"3": [6, 7, 0, 0, 0, 0, 0, 0]})
    _write(fresh_assets, "armor_materia_slot_types.json", {"4": [5, 5, 0, 0, 0, 0, 0, 0]})
    default = [0x05] * 8
    assert ff7._weapon_materia_slot_types(3) == [6, 7, 0, 0, 0, 0, 0, 0]
    assert ff7._weapon_materia_slot_types(9) == default
    assert ff7._weapon_materia_slot_types(-1) == default
    assert ff7._weapon_materia_slot_types(128) == default
    assert ff7._armor_materia_slot_types(4) == [5, 5, 0, 0, 0, 0, 0, 0]
    assert ff7._armor_materia_slot_types(32) == default
    assert ff7._armor_materia_slot_types(-1) == default
    out = ff7._weapon_materia_slot_types(9)
    out.append(1)  # callers get a copy
    assert ff7._DEFAULT_MATERIA_SLOT_TYPES == default


# --------------------------------------------------------------------------- status / row helpers


def test_status_name_map():
    assert ff7._STATUS_NAME_TO_MASK["fury"] == 0x20
    assert ff7._STATUS_NAME_TO_MASK["imprisoned"] == 0x80000000


def test_ailments_from_status():
    assert ff7._ailments_from_status(0) == []
    assert ff7._ailments_from_status(0x1 | 0x20 | 0x80000000) == ["Death", "Fury", "Imprisoned"]
    assert ff7._ailments_from_status(-1)[:2] == ["Death", "NearDeath"]


@pytest.mark.parametrize("sad,fury,out", [(False, False, ""), (True, False, "Sadness"), (True, True, "Fury"),
                                          (False, True, "Fury")])
def test_status_effect_from_bits(sad, fury, out):
    assert ff7._status_effect_from_bits(sad, fury) == out


def test_status_effect_from_bytes():
    assert ff7._status_effect_from_field_byte(0x110) == "Sadness"
    assert ff7._status_effect_from_field_byte(0x20) == "Fury"
    assert ff7._status_effect_from_battle_status(0x10) == "Sadness"
    assert ff7._status_effect_from_battle_status(0) == ""


def test_row_labels():
    assert ff7._row_label_from_savemap_order(0xFE) == "BR"
    assert ff7._row_label_from_savemap_order(0x1FE) == "BR"
    assert ff7._row_label_from_savemap_order(0xFF) == "FR"
    assert ff7._row_label_from_battle_raw(bytes([0, 0, 0, 0, 0x40])) == "BR"
    assert ff7._row_label_from_battle_raw(bytes([0, 0, 0, 0, 0x01])) == "FR"
    assert ff7._row_label_from_battle_raw(b"\x00\x00") == "FR"


def test_row_for_char_id():
    sm = blank_savemap()
    put_char(sm, 1, row=ff7.ROW_ORDER_BACK)
    assert ff7._row_for_char_id(bytes(sm), 1) == "BR"
    assert ff7._row_for_char_id(bytes(sm), 0) == "FR"
    assert ff7._row_for_char_id(bytes(sm), -1) == "FR"
    assert ff7._row_for_char_id(bytes(sm), 9) == "FR"
    assert ff7._row_for_char_id(b"\x00" * 10, 1) == "FR"


def test_materia_orb_stem(monkeypatch):
    assert ff7._materia_orb_stem(-1) == ""
    assert ff7._materia_orb_stem(0xFF) == ""
    assert ff7._materia_orb_stem(0) == "materia_purple"
    assert ff7._materia_orb_stem(250) == "materia_green"  # unknown id
    monkeypatch.setitem(ff7._MATERIA_ORB_BY_ID, "77", "materia_rainbow")
    assert ff7._materia_orb_stem(77) == "materia_green"  # unknown stem


def test_gear_display_name():
    table = {"1": " Sword ", "2": None}
    assert ff7._gear_display_name(table, 1, 127) == "Sword"
    assert ff7._gear_display_name(table, 2, 127) == ""
    assert ff7._gear_display_name(table, 3, 127) == ""
    assert ff7._gear_display_name(table, -1, 127) == ""
    assert ff7._gear_display_name(table, 128, 127) == ""
    assert ff7._gear_display_name({"255": "x"}, 0xFF, 300) == ""


def test_battle_log_name_lookups(monkeypatch):
    # Mark the assets as loaded, or the first lookup would refill the tables below.
    monkeypatch.setattr(ff7, "_BATTLE_LOG_NAME_ASSETS_LOADED", True)
    monkeypatch.setattr(ff7, "_BATTLE_LOG_CMD_EN", {"1": "Attack"})
    monkeypatch.setattr(ff7, "_BATTLE_LOG_ATK128_EN", {"0": "Cure"})
    assert ff7._battle_log_cmd_display(1, []) == "Attack"
    assert ff7._battle_log_cmd_display(0x102, ["", "", " Magic "]) == "Magic"
    assert ff7._battle_log_cmd_display(3, ["", "", "", "  "]) == ""
    assert ff7._battle_log_cmd_display(40, []) == ""
    assert ff7._battle_log_atk_display(0, ["ram"]) == "Cure"
    assert ff7._battle_log_atk_display(1, ["", " Fire "]) == "Fire"
    assert ff7._battle_log_atk_display(1, ["", None]) == ""
    assert ff7._battle_log_atk_display(-1, ["x"]) == ""


def test_unified_item_name_ranges():
    assert ff7._ff7_unified_item_name(0) == "Potion"
    assert ff7._ff7_unified_item_name(0x7F) == "Item 127"  # no consumable name there
    assert ff7._ff7_unified_item_name(0x80) == "Buster Sword"
    assert ff7._ff7_unified_item_name(0x100) == "Bronze Bangle"
    assert ff7._ff7_unified_item_name(0x120) == "Power Wrist"
    assert ff7._ff7_unified_item_name(0x1FF) == "Item 511"
    assert ff7._ff7_unified_item_name(0x200) == "Potion"  # masked to 9 bits


def test_unified_item_name_missing_tables(monkeypatch):
    for name in ("_WEAPON_NAMES_EN", "_ARMOR_NAMES_EN", "_ACCESSORY_NAMES_EN"):
        monkeypatch.setattr(ff7, name, {})
    assert ff7._ff7_unified_item_name(0x81) == "Item 129"
    assert ff7._ff7_unified_item_name(0x101) == "Item 257"
    assert ff7._ff7_unified_item_name(0x121) == "Item 289"


# --------------------------------------------------------------------------- equipped byte mapping


@pytest.mark.parametrize("raw,loc", [(0xFF, -1), (0x80, 0), (0xFE, 0x7E), (5, 5), (127, 127)])
def test_weapon_byte_to_local(raw, loc):
    assert ff7._equipped_weapon_byte_to_local(raw) == loc


@pytest.mark.parametrize("raw,loc", [(0xFF, -1), (3, 3), (31, 31), (32, -1), (0x80, -1)])
def test_armor_byte_to_local(raw, loc):
    assert ff7._equipped_armor_byte_to_local(raw) == loc


@pytest.mark.parametrize("raw,loc", [(0xFF, -1), (0, 0), (31, 31), (40, -1)])
def test_accessory_byte_to_local(raw, loc):
    assert ff7._equipped_accessory_byte_to_local(raw) == loc


def test_byte_to_local_wide_inputs():
    # Values >= 0x100 are masked to a byte first; the unified-id branches are unreachable for bytes
    assert ff7._equipped_armor_byte_to_local(0x103) == 3
    assert ff7._equipped_accessory_byte_to_local(0x121) == -1


def test_byte_to_unified():
    assert ff7._equipped_weapon_byte_to_unified(3) == 0x83
    assert ff7._equipped_weapon_byte_to_unified(0xFF) is None
    assert ff7._equipped_armor_byte_to_unified(2) == 0x102
    assert ff7._equipped_armor_byte_to_unified(0xFF) is None
    assert ff7._equipped_accessory_byte_to_unified(1) == 0x121
    assert ff7._equipped_accessory_byte_to_unified(0xFF) is None


def test_party_equipped_unified_counts():
    sm = blank_savemap()
    put_char(sm, 0, weapon=0, armor=1, acc=2)
    put_char(sm, 1, weapon=0, armor=0xFF, acc=0xFF)
    set_party(sm, [0, 1, 0xFF])
    assert ff7._party_equipped_unified_counts(bytes(sm)) == {0x80: 2, 0x101: 1, 0x122: 1}
    sm2 = bytearray(sm)
    sm2[ff7.SAVE_OFF_PARTY_SLOTS] = 20  # bad id
    assert 0x101 not in ff7._party_equipped_unified_counts(bytes(sm2))
    assert ff7._party_equipped_unified_counts(b"") == {}


def _short_savemap_records_out_of_range(monkeypatch):
    """Party slot 0 holds char 0 whose record is (patched) past the end of the savemap."""
    sm = bytearray(b"\xff" * (ff7.SAVE_OFF_PARTY_SLOTS + 3))
    sm[ff7.SAVE_OFF_PARTY_SLOTS] = 0
    monkeypatch.setattr(ff7, "_CHAR_BLOCK", [0x2000] * 9)
    return bytes(sm)


def test_party_counts_skip_records_past_savemap(monkeypatch):
    short = _short_savemap_records_out_of_range(monkeypatch)
    assert ff7._party_equipped_unified_counts(short) == {}
    assert ff7._party_materia_equipped_id_counts(short) == {}
    assert ff7._party_gear_from_savemap(short, 0) == {}
    assert ff7._savemap_party_slot_level(short, 0) == 0


def test_gain_explained_by_party_unequip():
    assert not ff7._gain_explained_by_party_unequip(1, 1, None, {})
    assert not ff7._gain_explained_by_party_unequip(1, 0, {1: 1}, {})
    assert not ff7._gain_explained_by_party_unequip(1, 1, {1: 1}, {1: 1})
    assert ff7._gain_explained_by_party_unequip(1, 1, {1: 1}, {})
    assert not ff7._gain_explained_by_party_unequip(1, 2, {1: 1}, {})


def test_materia_id_display_name():
    assert ff7._materia_id_display_name(0xFF) == ""
    assert ff7._materia_id_display_name(0) == "MP Plus"
    assert ff7._materia_id_display_name(0x1FE) == "Materia 254"
    assert ff7._materia_id_display_name(-1) == ""  # masked to 0xFF


def test_parse_inv_items_multiset():
    data = struct.pack("<HHHH", 0xFFFF, (3 << 9) | 5, (2 << 9) | 5, (1 << 9) | 0x80) + b"\x01"
    assert ff7._parse_inv_items_multiset(data) == {5: 5, 0x80: 1}
    assert ff7._parse_inv_items_multiset(b"") == {}


def test_parse_inv_materia_multiset():
    data = bytes([1, 0, 0, 0, 0xFF, 0, 0, 0, 1, 9, 9, 9, 7, 0, 0])
    assert ff7._parse_inv_materia_multiset(data) == {1: 2}


def test_party_materia_equipped_counts():
    sm = blank_savemap()
    put_char(sm, 0, materia_w=[(1, 0), (2, 5)], materia_a=[(1, 0)])
    put_char(sm, 2, materia_w=[(3, 0)])
    set_party(sm, [0, 0xFF, 2])
    assert ff7._party_materia_equipped_id_counts(bytes(sm)) == {1: 2, 2: 1, 3: 1}
    assert ff7._party_materia_equipped_id_counts(b"\x00" * 10) == {}
    sm2 = bytearray(sm)
    sm2[ff7.SAVE_OFF_PARTY_SLOTS] = 9  # >= len(_CHAR_BLOCK)
    assert ff7._party_materia_equipped_id_counts(bytes(sm2)) == {3: 1}


def test_gain_explained_by_materia_unequip():
    assert not ff7._gain_explained_by_materia_unequip(1, 1, None, {})
    assert not ff7._gain_explained_by_materia_unequip(1, -1, {}, {})
    assert not ff7._gain_explained_by_materia_unequip(1, 1, {1: 0}, {1: 0})
    assert ff7._gain_explained_by_materia_unequip(0x101, 1, {1: 2}, {1: 1})
    assert not ff7._gain_explained_by_materia_unequip(1, 3, {1: 2}, {1: 1})


# --------------------------------------------------------------------------- gear / materia blocks


def test_parse_materia_block():
    rec = bytearray(b"\xff" * 0x84)
    rec[0x40:0x44] = bytes([0, 0x10, 0x27, 0x00])  # MP Plus, 10000 AP
    slots = ff7._parse_materia_block(bytes(rec), 0x40, 8)
    assert slots[0] == {"id": 0, "ap": 10000, "empty": False, "orb": "materia_purple", "name": "MP Plus"}
    assert slots[1]["empty"] is True and slots[1]["orb"] == "" and slots[1]["name"] == ""
    assert len(slots) == 8
    assert len(ff7._parse_materia_block(bytes(rec[:0x46]), 0x40, 8)) == 1


def test_char_gear_materia_short_and_full():
    short = ff7._char_gear_materia(b"\x00" * 10)
    assert short == ff7._empty_gear_materia()
    sm = blank_savemap()
    off = put_char(sm, 0, weapon=0, armor=0, acc=0, materia_w=[(0, 0)])
    g = ff7._char_gear_materia(bytes(sm[off: off + 0x84]))
    assert g["weapon_name"] == "Buster Sword" and g["armor_name"] == "Bronze Bangle"
    assert g["accessory_name"] == "Power Wrist"
    assert g["materia_weapon"][0]["name"] == "MP Plus"
    assert len(g["materia_weapon_slot_types"]) == 8 and len(g["materia_armor_slot_types"]) == 8


def test_field_name_from_savemap():
    assert ff7._field_name_from_savemap(b"") == ""
    sm = blank_savemap()
    sm[ff7.SAVE_OFF_LOCATION_NAME: ff7.SAVE_OFF_LOCATION_NAME + 12] = ff7._encode_ff7_name("Midgar")
    assert ff7._field_name_from_savemap(bytes(sm)) == "Midgar"


def test_current_module_label():
    assert ff7._current_module_label(2) == "Battle"
    assert ff7._current_module_label(99) == "Module 99"


def test_party_gear_from_savemap():
    sm = blank_savemap()
    put_char(sm, 3, weapon=0)
    set_party(sm, [3, 0xFF, 20])
    assert ff7._party_gear_from_savemap(bytes(sm), 0)["weapon_name"] == "Buster Sword"
    assert ff7._party_gear_from_savemap(bytes(sm), 1) == {}
    assert ff7._party_gear_from_savemap(bytes(sm), 2) == {}
    assert ff7._party_gear_from_savemap(bytes(sm), 3) == {}
    assert ff7._party_gear_from_savemap(bytes(sm), -1) == {}
    assert ff7._party_gear_from_savemap(b"", 0) == {}


@pytest.mark.parametrize("row,out", [({"party_slot": 1}, 1), ({"slot": "2"}, 2), ({"slot": 5}, -1), ({}, -1),
                                     ({"party_slot": "x"}, -1), ({"party_slot": None}, -1)])
def test_party_row_party_slot(row, out):
    assert ff7._party_row_party_slot(row) == out


def test_count_equipped_materia_in_row():
    row = {"materia_weapon": [{"id": 1}, {"id": 0xFF}, {"empty": True, "id": 2}, "x", {"id": "bad"}, {"id": -3}],
           "materia_armor": [{"id": 4}], }
    assert ff7._count_equipped_materia_in_row(row) == 2
    assert ff7._count_equipped_materia_in_row({}) == 0


def test_savemap_party_slot_level_and_empty():
    sm = blank_savemap()
    put_char(sm, 0, level=42)
    set_party(sm, [0, 0xFF, 15])
    b = bytes(sm)
    assert ff7._savemap_party_slot_level(b, 0) == 42
    assert ff7._savemap_party_slot_level(b, 1) == 0
    assert ff7._savemap_party_slot_level(b, 2) == 0
    assert ff7._savemap_party_slot_level(b, 3) == 0
    assert ff7._savemap_party_slot_level(b"", 0) == 0
    assert ff7._savemap_party_slot_empty(b, 0) is False
    assert ff7._savemap_party_slot_empty(b, 1) is True
    assert ff7._savemap_party_slot_empty(b, 2) is True
    assert ff7._savemap_party_slot_empty(b, -1) is True
    assert ff7._savemap_party_slot_empty(b"", 0) is True


@pytest.mark.parametrize("b,mode", [(0x80, "wait"), (0xC0 & 0x40, "recommended"), (0x3F, "active"), (0xC0, "active")])
def test_decode_atb(b, mode):
    assert ff7._decode_savemap_atb_mode(b) == mode


@pytest.mark.parametrize("mode,b", [("Wait", 0x80), ("w", 0x80), ("recommend", 0x40), ("m", 0x40), ("mid", 0x40),
                                    ("recommended", 0x40), ("active", 0), ("", 0), (None, 0)])
def test_encode_atb(mode, b):
    assert ff7._encode_savemap_atb_mode(mode) == b


def test_records_party_aggregates_from_rows():
    party = [
        {"party_slot": 0, "level": 10, "materia_weapon": [{"id": 1}]},
        {"party_slot": 1, "level": 20},
        {"party_slot": 7, "level": 99},
    ]
    assert ff7._records_party_aggregates(party, None) == (15, 1)
    assert ff7._records_party_aggregates([], None) == (0, 0)


def test_records_party_aggregates_with_savemap():
    sm = blank_savemap()
    put_char(sm, 0, level=30)
    put_char(sm, 1, level=40, materia_w=[(1, 0), (2, 0)])
    put_char(sm, 2, level=50)
    set_party(sm, [0, 1, 0xFF])
    party = [{"party_slot": 0, "level": 0}, {"party_slot": 2, "level": 99}]
    # slot 0: level from savemap; slot 1: from savemap record incl. materia; slot 2 empty -> skipped
    assert ff7._records_party_aggregates(party, bytes(sm)) == (35, 2)


def test_records_party_aggregates_zero_levels():
    sm = blank_savemap()
    put_char(sm, 0, level=0)
    put_char(sm, 1, level=0)
    set_party(sm, [0, 1, 0xFF])
    assert ff7._records_party_aggregates([{"party_slot": 0, "level": 0}], bytes(sm)) == (0, 0)


# --------------------------------------------------------------------------- name codec


def test_rebase():
    assert ff7._rebase(0x1000000, 0x401234) == 0x1001234


def test_decode_ff7_name():
    assert ff7._decode_ff7_name(bytes([0x23, 0x4C, 0x4F, 0x55, 0x44, 0xFF, 0x21])) == "Cloud"
    assert ff7._decode_ff7_name(bytes([0x21, 0x00, 0x22])) == "A B"
    assert ff7._decode_ff7_name(bytes([0x60, 0x70])) == "?"  # non-printable dropped
    assert ff7._decode_ff7_name(b"") == "?"


def test_encode_ff7_name():
    assert ff7._encode_ff7_name("Cloud") == bytes([0x23, 0x4C, 0x4F, 0x55, 0x44]) + b"\xff" * 7
    assert ff7._encode_ff7_name(" A B ")[:3] == bytes([0x21, 0x00, 0x22])
    assert ff7._encode_ff7_name("ABCDEFGHIJKLMNOP") == ff7._encode_ff7_name("ABCDEFGHIJKL")
    assert ff7._encode_ff7_name("é")[0] == 0xC9
    assert ff7._encode_ff7_name("一")[0] == 0xFE
    assert ff7._encode_ff7_name(None) == b"\xff" * 12
    assert ff7._encode_ff7_name("ab", out_len=3) == bytes([0x41, 0x42, 0xFF])
    assert ff7._decode_ff7_name(ff7._encode_ff7_name("Tifa Lock")) == "Tifa Lock"


def test_norm_and_sanitize():
    assert ff7._norm_party_name("  Red   XIII ") == "red xiii"
    assert ff7._norm_party_name(None) == ""
    assert ff7._sanitize_enemy_token("") == ""
    assert ff7._sanitize_enemy_token(None) == ""
    assert ff7._sanitize_enemy_token(" Guard͏  Scorpion​ ") == "Guard Scorpion"


# --------------------------------------------------------------------------- menu colors


def test_menu_display_quad():
    assert ff7._menu_display_quad((1, 2, 3)) == bytes([3, 2, 1, 0x80])
    assert ff7._menu_display_quad((256, 0, 0)) == bytes([0, 0, 0, 0x80])


@pytest.mark.parametrize(
    "tok,rgb",
    [("Red", (255, 0, 0)), ("#0a0B0c", (10, 11, 12)), ("abcdef", (171, 205, 239)), ("#abc", (170, 187, 204)),
     ("rgb( 1, 2 ,300)", (1, 2, 255)), ("4,5,6", (4, 5, 6)), ("999, 0, 0", (255, 0, 0)), ("", None), ("  ", None),
     (None, None), ("chartreuse", None), ("#12345", None)],
)
def test_parse_menu_color(tok, rgb):
    assert ff7._parse_menu_color(tok) == rgb


def test_parse_menu_corners():
    assert ff7._parse_menu_corners(None) is None
    assert ff7._parse_menu_corners("Upper Right") == ["ur"]
    assert ff7._parse_menu_corners("ALL") == ["ul", "ur", "ll", "lr"]
    assert ff7._parse_menu_corners("middle") == []
    out = ff7._parse_menu_corners("top")
    out.append("x")
    assert ff7._MENU_CORNERS["top"] == ["ul", "ur"]


def test_rgb_blend_hex_darken():
    assert ff7._rgb_tuple(b"\x01\x02", 0) is None
    assert ff7._rgb_tuple(b"\x01\x02\x03\x04", 1) == (2, 3, 4)
    assert ff7._blend_rgb((255, 255, 0), (255, 1, 0)) == (255, 128, 0)
    assert ff7._hex_rgb((255, 0, 16)) == "#ff0010"
    assert ff7._darken((100, 200, 300), 0.5) == (50, 100, 150)
    assert ff7._darken((100, 200, 250), 2.0) == (200, 255, 255)
    assert ff7._darken((10, 10, 10), -1) == (0, 0, 0)


def test_menu_theme_dict():
    t = ff7._menu_theme_dict_from_rgbs((0, 0, 0), (100, 100, 100), (200, 200, 200), (250, 250, 250))
    assert t["bg_ul"] == "#000000" and t["bg_lr"] == "#fafafa"
    assert t["bg_center"] == "#898989"  # (0+100+200+250)//4 = 137
    assert t["bg_top"] == "#323232" and t["bg_bot"] == "#e1e1e1"
    assert t["border_outer"] == "#bbbbbb" and t["border_mid"] == "#a2a2a2"
    assert t["border_inner"] == "#656565"
    bright = ff7._menu_theme_dict_from_rgbs((250,) * 3, (250,) * 3, (250,) * 3, (250,) * 3)
    assert bright["border_outer"] == "#ffffff"


def test_menu_theme_from_savemap_and_live():
    sm = blank_savemap()
    sm[ff7.SAVE_OFF_WIN_UL: ff7.SAVE_OFF_WIN_UL + 3] = bytes([1, 2, 3])
    sm[ff7.SAVE_OFF_WIN_LR: ff7.SAVE_OFF_WIN_LR + 3] = bytes([4, 5, 6])
    t = ff7.menu_theme_from_savemap(bytes(sm))
    assert t["bg_ul"] == "#010203" and t["bg_lr"] == "#040506"
    assert ff7.menu_theme_from_savemap(b"\x00" * 0x50) is None
    raw = bytes([3, 2, 1, 0x80, 6, 5, 4, 0x80, 9, 8, 7, 0x80, 12, 11, 10, 0x80])
    live = ff7.menu_theme_from_live_display(raw)
    assert (live["bg_ul"], live["bg_ll"], live["bg_ur"], live["bg_lr"]) == ("#010203", "#040506", "#070809", "#0a0b0c")
    assert ff7.menu_theme_from_live_display(None) is None
    assert ff7.menu_theme_from_live_display(b"\x00" * 15) is None


# --------------------------------------------------------------------------- catalog + speed


def test_catalog_entries():
    ids = [e["id"] for e in ff7.FF7_CONNECTOR_CATALOG]
    assert len(ids) == len(set(ids))
    internal = {e["id"] for e in ff7.FF7_CONNECTOR_CATALOG if not ff7.catalog_entry_is_public(e)}
    assert internal == {"restore_menu_words", "restore_battle_speed", "restore_battle_atb_mode",
                        "restore_infinite_items", "restore_game_speed"}
    assert ff7.catalog_entry_is_public({"id": "x"}) is True
    assert ff7.catalog_entry_is_public({"internal": True}) is False


def test_speed_math():
    assert ff7.ffnx_fps_literal_f64(2) == (60.0, 30.0)
    assert ff7.vanilla_tick_interval_f64(30.0, 2.0) == pytest.approx(10000000.0 / 60.0)


def test_game_speed_options():
    opts = ff7.ff7_game_speed_select_options()
    assert len(opts) == 32
    assert opts["0.25"] == "0.25×" and opts["1.0"] == "1×" and opts["8.0"] == "8×" and opts["1.5"] == "1.5×"
    assert list(opts)[0] == "0.25" and list(opts)[-1] == "8.0"


# --------------------------------------------------------------------------- connector config -> kwargs


def cfg(op, **kw):
    return {"operation": op, **{f"arg_{k}": v for k, v in kw.items()}}


@pytest.mark.parametrize(
    "op,args,expected",
    [
        ("add_gil", {"amount": " -5 "}, {"amount": "0"}),
        ("remove_gil", {"amount": "{gil}"}, {"amount": "{gil}"}),
        ("add_gil", {"amount": "random:1-5"}, {"amount": "random:1-5"}),
        ("add_gil", {"amount": "Random"}, {"amount": "Random"}),
        ("add_gil", {"amount": "abc"}, {"amount": "abc"}),
        ("add_gil", {}, {"amount": "0"}),
        ("add_party_hp", {"character": " Cloud ", "amount": "50"}, {"character": "Cloud", "amount": "50"}),
        ("remove_party_hp", {"amount": "x"}, {"character": "", "amount": "x"}),
        ("remove_party_hp", {"amount": "random"}, {"character": "", "amount": "random"}),
        ("kill_party_member", {"character": "0"}, {"character": "0"}),
        ("damage_enemy", {"enemy": "Reno", "amount": "20000"}, {"enemy": "Reno", "amount": "9999"}),
        ("damage_enemy", {"amount": "0"}, {"enemy": "", "amount": "1"}),
        ("damage_enemy", {"amount": "lots"}, {"enemy": "", "amount": "lots"}),
        ("damage_enemy", {"amount": "random:1-3"}, {"enemy": "", "amount": "random:1-3"}),
        ("kill_enemy", {"enemy": "A"}, {"enemy": "A"}),
        ("kill_all_enemies", {}, {}),
        ("rename_character", {"current_name": "Cloud", "new_name": "Bob"}, {"current_name": "Cloud", "new_name": "Bob"}),
        ("set_battle_status", {"character": "1", "status_effect": "Fury"},
         {"character": "1", "status_effect": "Fury", "mode": "on"}),
        ("set_field_status_byte", {"character": "1", "value": -3}, {"character": "1", "value": 0}),
        ("set_field_status_byte", {"value": "16"}, {"character": "", "value": 16}),
        ("set_character_gear", {"gear_kind": " ", "gear": "Buster Sword"},
         {"character": "", "gear_kind": "weapon", "gear": "Buster Sword"}),
        ("set_character_gear", {"gear_kind": "armor"}, {"character": "", "gear_kind": "armor", "gear": ""}),
        ("set_menu_row_access", {"menu_name": "Save", "duration_sec": "5"},
         {"menu_name": "Save", "access": "allow", "duration_sec": 5}),
        ("set_menu_visibility", {"menu_name": "PHS", "duration_sec": -2},
         {"menu_name": "PHS", "enabled": "1", "duration_sec": 0}),
        ("set_menu_lock", {"menu_name": "Item"}, {"menu_name": "Item", "locked": "1", "duration_sec": 0}),
        ("set_field_menu_access", {}, {"enabled": "1"}),
        ("set_world_speed_multiplier", {"multiplier": 0}, {"multiplier": 1}),
        ("set_world_speed_multiplier", {"multiplier": "3"}, {"multiplier": 3}),
        ("set_game_speed", {"speed": "20"}, {"speed": "8.0", "duration_sec": 0}),
        ("set_game_speed", {"speed": "fast", "duration_sec": 3}, {"speed": "fast", "duration_sec": 3}),
        ("set_game_speed", {"speed": "{x}"}, {"speed": "{x}", "duration_sec": 0}),
        ("set_game_speed", {"speed": "random:1-2"}, {"speed": "random:1-2", "duration_sec": 0}),
        ("set_game_speed", {}, {"speed": "1.0", "duration_sec": 0}),
        ("restore_game_speed", {}, {}),
        ("set_battle_speed", {"speed": "300.7"}, {"speed": "255", "duration_sec": 0}),
        ("set_battle_speed", {"speed": "fast"}, {"speed": "fast", "duration_sec": 0}),
        ("set_battle_speed", {"speed": "{s}"}, {"speed": "{s}", "duration_sec": 0}),
        ("set_battle_atb_mode", {"duration_sec": 9}, {"mode": "active", "duration_sec": 9}),
        ("set_infinite_items", {"duration_sec": "none"}, {"enabled": "1", "duration_sec": "none"}),
        ("set_party_level", {"character": "Cloud", "level": "150"}, {"character": "Cloud", "level": "99"}),
        ("set_enemy_level", {"enemy": "Reno", "level": "0"}, {"enemy": "Reno", "level": "1"}),
        ("set_enemy_level", {"level": "high"}, {"enemy": "", "level": "high"}),
        ("set_party_level", {"level": "random"}, {"character": "", "level": "random"}),
        ("set_party_stat", {"character": "0", "stat": "str", "amount": "-4"},
         {"character": "0", "stat": "str", "amount": "0"}),
        ("set_enemy_stat", {"enemy": "1", "stat": "def", "amount": "x"}, {"enemy": "1", "stat": "def", "amount": "x"}),
        ("set_enemy_stat", {"amount": "{a}"}, {"enemy": "", "stat": "", "amount": "{a}"}),
        ("set_menu_colors", {"color": "red"}, {"target": "all", "color": "red"}),
        ("equip_materia", {"materia": "Fire"}, {"character": "", "gear_kind": "weapon", "slot": "0", "materia": "Fire"}),
        ("start_battle", {"battle_id": "70000"}, {"battle_id": "65535"}),
        ("start_battle", {"battle_id": "random:1-9"}, {"battle_id": "random:1-9"}),
        ("start_battle", {"battle_id": "boss"}, {"battle_id": "boss"}),
        ("add_item", {"item": "Potion", "quantity": "500"}, {"item": "Potion", "quantity": "99"}),
        ("add_item", {"quantity": "many"}, {"item": "", "quantity": "many"}),
        ("add_item", {"quantity": "random"}, {"item": "", "quantity": "random"}),
        ("add_materia", {"materia": "Fire", "materia_level": "-3"}, {"materia": "Fire", "materia_level": "0"}),
        ("add_materia", {"materia_level": "max"}, {"materia": "", "materia_level": "max"}),
        ("add_materia", {"materia_level": "{lv}"}, {"materia": "", "materia_level": "{lv}"}),
        ("add_gear", {"gear": "Buster Sword"}, {"gear": "Buster Sword"}),
        ("query_inventory", {"item_name": "Potion"}, {"item_name": "Potion"}),
        ("not_an_op", {}, {}),
    ],
)
def test_connector_config_to_kwargs(op, args, expected):
    assert ff7.ff7_connector_config_to_hook_kwargs(cfg(op, **args)) == expected


def test_connector_config_none_value_uses_default():
    out = ff7.ff7_connector_config_to_hook_kwargs({"operation": "add_gil", "arg_amount": None})
    assert out == {"amount": "0"}
    assert ff7.ff7_connector_config_to_hook_kwargs({}) == {}


# --------------------------------------------------------------------------- persistence helpers


def test_persist_allowlist_and_key():
    assert "set_game_speed" in ff7.FF7_CONNECTOR_PERSIST_ALLOWLIST
    assert ff7._persist_override_key("set_menu_lock", {"menu_name": " Save "}) == "set_menu_lock:save"
    assert ff7._persist_override_key("set_menu_lock", {}) == "set_menu_lock"
    assert ff7._persist_override_key("set_game_speed", {"menu_name": "x"}) == "set_game_speed"


def test_query_inventory_outputs():
    assert ff7._query_inventory_outputs(None) is None
    assert ff7._query_inventory_outputs({}) == {"item_name": "", "quantity": "", "resolved_name": "", "kind": "",
                                                "error": ""}
    full = {"item_name": "Potion", "quantity": 3, "resolved_name": "Potion", "kind": "unified_item", "error": "e"}
    assert ff7._query_inventory_outputs(full) == {"item_name": "Potion", "quantity": "3", "resolved_name": "Potion",
                                                  "kind": "unified_item", "error": "e"}


def test_json_safe_kwargs():
    out = ff7._json_safe_kwargs({"a": 1, "b": "x", "c": None, "d": 1.5, "e": True, "f": [1], "g": {"k": 1}})
    assert out == {"a": 1, "b": "x", "c": None, "d": 1.5, "e": True, "f": "[1]", "g": "{'k': 1}"}


def test_backcompat_alias():
    assert ff7.FF7Reader is ff7.FF7Hook
