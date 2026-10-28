# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""uiwindows.connectors (part 1): the Connectors tab build, cards, folders, floaters, search and
status filters, drag and drop, folder dialogs and the card actions (NiceGUI user simulation)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from copy import deepcopy
from types import SimpleNamespace

import pytest
from nicegui import ui
from ui_sim import run_ui

from modules import connector_layout_store as ls
from modules import connector_manager as cm
from modules.connector_actions import create_action
from modules.connector_core import ActionType, Connector, TriggerType
from modules.connector_triggers import create_trigger
from modules.uiwindows import connectors as cx


class Hotkeys:
    def register_hotkey(self, *a, **k):
        pass

    def unregister_hotkey(self, *a, **k):
        pass


def conn(cid, name=None, enabled=True, trigger_type=TriggerType.TWITCH_BITS, actions=None, **kw):
    trigger = create_trigger(trigger_type, f"t-{cid}", "T")
    return Connector(connector_id=cid, name=name if name is not None else f"Conn {cid}", enabled=enabled,
                     trigger=trigger,
                     actions=actions if actions is not None else [
                         create_action(ActionType.SEND_CHAT_MESSAGE, f"a-{cid}", "Say", message="hi")],
                     **kw)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(cm, "get_listener", lambda: Hotkeys())
    mgr = cm.ConnectorManager()
    mgr.saves = 0

    def save():
        mgr.saves += 1
        mgr.last_save_ok = True
        return True

    monkeypatch.setattr(mgr, "save_connectors", save)
    monkeypatch.setattr(cm, "get_manager", lambda: mgr)
    store = {"layout": ls.default_layout(), "saves": []}

    def load_layout():
        return deepcopy(store["layout"])

    def save_layout(layout):
        store["layout"] = deepcopy(layout)
        store["saves"].append(deepcopy(layout))
        return True

    monkeypatch.setattr(ls, "load_layout", load_layout)
    monkeypatch.setattr(ls, "save_layout", save_layout)
    notes = []
    monkeypatch.setattr(cx, "notify", lambda msg="", **kw: notes.append((msg, kw)))
    for name, value in {
        "connectors_container": None, "search_input": None, "current_search": "", "current_status": "all",
        "_filter_chips": {}, "_bar_sub_label": None, "_no_match_label": None, "connector_cards": {},
        "folder_cards": {}, "connector_parent_folder": {}, "_client_drag_state": {}, "_folder_dialog_host": None,
        "_folder_floaters": {}, "folder_tile_title_labels": {}, "_active_editor": None,
    }.items():
        monkeypatch.setattr(cx, name, value)
    return SimpleNamespace(mgr=mgr, store=store, notes=notes, mp=monkeypatch)


def add(env, *connectors):
    for c in connectors:
        env.mgr.connectors[c.connector_id] = c


def folder_layout(root, folders):
    return {"version": 1, "root_items": root, "folders": folders}


def tab(env, check, prepare=None):
    def build():
        if prepare:
            prepare()
        cx.create_connectors_tab()

    async def wrapped(user):
        with user:
            result = check(user)
            if hasattr(result, "__await__"):
                await result

    run_ui(build, wrapped)


def hidden(el):
    return "hidden" in el._classes


# --------------------------------------------------------------------------- build


def test_empty_tab(env):
    async def check(user):
        await user.should_see("No connectors yet")
        await user.should_see("Create your first connector")
        assert cx._no_match_label is None
        assert cx._bar_sub_label.text == "When something happens, do something. 0 connectors."

    tab(env, check)


def test_tab_with_cards_and_folder(env):
    add(env, conn("a", description="Says hi"), conn("b", enabled=False), conn("c"), conn("d"))
    env.mgr.connectors["a"].trigger_count = 2
    env.mgr.connectors["a"].last_triggered = 1_700_000_000
    env.store["layout"] = folder_layout(
        [{"kind": "connector", "id": "a"}, {"kind": "folder", "id": "f1"}, {"kind": "junk"}],
        {"f1": {"name": "Raids", "connector_ids": ["b", "c"]}},
    )

    async def check(user):
        await user.should_see("Conn a")
        await user.should_see("Says hi")
        await user.should_see("Raids")
        await user.should_see("2 connectors · 1 on")
        await user.should_see("Never run")
        await user.should_see("No description")
        assert set(cx.connector_cards) == {"a", "d"} and set(cx.folder_cards) == {"f1"}
        assert cx._bar_sub_label.text == "When something happens, do something. 4 connectors in 1 folder."
        counts = {k: pair[1].content for k, pair in cx._filter_chips.items()}
        assert counts == {"all": "<b>4</b>", "on": "<b>3</b>", "off": "<b>1</b>"}
        # reconciled layout (missing "d", junk dropped) was saved
        assert env.store["saves"][-1]["root_items"][-1] == {"kind": "connector", "id": "d"}

    tab(env, check)


def test_folder_preview_limit_and_empty_folder(env):
    ids = [f"m{i}" for i in range(7)]
    add(env, *[conn(i) for i in ids])
    env.mgr.connectors["m0"].trigger = None
    env.store["layout"] = folder_layout([{"kind": "folder", "id": "big"}],
                                        {"big": {"name": "", "connector_ids": ids}})

    async def check(user):
        await user.should_see("+ 2 more")
        await user.should_see("Folder")
        await user.should_see("No trigger")

    tab(env, check)


def test_unreconciled_layout_rows(env, monkeypatch):
    add(env, conn("a"))
    monkeypatch.setattr(ls, "reconcile_layout", lambda layout, ids: layout)
    env.store["layout"] = folder_layout(
        [{"kind": "connector", "id": "gone"}, {"kind": "", "id": "x"}, {"kind": "folder", "id": "e"}, {"kind": "folder", "id": "nofolder"},
         {"kind": "connector", "id": "a"}],
        {"e": {"name": "Empty", "connector_ids": []}},
    )

    async def check(user):
        await user.should_see("Empty folder. Drag connectors here.")
        assert set(cx.connector_cards) == {"a"}
        switch = [s for s in user.find(ui.switch).elements if not s.enabled]
        assert switch

    tab(env, check)


def test_folder_mixed_and_all_off_switch(env):
    add(env, conn("a"), conn("b", enabled=False), conn("c", enabled=False))
    env.store["layout"] = folder_layout(
        [{"kind": "folder", "id": "mix"}, {"kind": "folder", "id": "off"}],
        {"mix": {"name": "Mix", "connector_ids": ["a", "b"]}, "off": {"name": "Off", "connector_ids": ["c"]}},
    )

    async def check(user):
        switches = [s for s in user.find(ui.switch).elements]
        values = sorted(str(s.value) for s in switches)
        assert values == ["False", "None"]

    tab(env, check)


def test_load_connectors_without_container_and_error(env):
    cx.load_connectors()  # container missing: logged, no crash
    add(env, conn("a"))

    def boom():
        raise RuntimeError("broken manager")

    async def check(user):
        env.mp.setattr(env.mgr, "get_all_connectors", boom)
        cx.load_connectors()
        await user.should_see("Error loading connectors: broken manager")

    tab(env, check)


# --------------------------------------------------------------------------- search / filters


def test_search_and_status_filters(env):
    add(env, conn("a", name="Hype Cheers"), conn("b", name="Raid scene", enabled=False))

    async def check(user):
        cx.on_search_change(SimpleNamespace(value="HYPE"))
        assert cx.current_search == "hype"
        assert not hidden(cx.connector_cards["a"]) and hidden(cx.connector_cards["b"])
        cx.on_search_change(SimpleNamespace(value="nothing"))
        assert cx._no_match_label.text == "No connectors match “nothing”" and not hidden(cx._no_match_label)
        cx._set_status_filter("on")
        assert cx._no_match_label.text == "No connectors match “nothing” among the on ones"
        assert "is-on" in cx._filter_chips["on"][0]._classes
        assert "is-on" not in cx._filter_chips["all"][0]._classes
        cx.on_search_change(SimpleNamespace(value=None))
        assert not hidden(cx.connector_cards["a"]) and hidden(cx.connector_cards["b"])
        cx._set_status_filter("off")
        assert hidden(cx.connector_cards["a"]) and not hidden(cx.connector_cards["b"])
        env.mgr.connectors["b"].enabled = True
        cx.update_search_visibility()
        assert cx._no_match_label.text == "No connectors are off."
        env.mgr.connectors["a"].enabled = env.mgr.connectors["b"].enabled = False
        cx._set_status_filter("on")
        assert cx._no_match_label.text == "No connectors are on."
        cx._set_status_filter("all")
        assert hidden(cx._no_match_label)

    tab(env, check)


def test_status_filter_skips_deleted_chips(env):
    async def check(user):
        chip = cx._filter_chips["on"][0]
        chip.delete()
        cx._set_status_filter("on")
        cx._update_toolbar_counts({}, 0)
        cx._bar_sub_label.delete()
        cx._update_toolbar_counts({}, 2)

    tab(env, check)


def test_search_with_folders_and_open_floater(env):
    add(env, conn("a", name="Alpha"), conn("b", name="Beta"), conn("c", name="Gamma", enabled=False))
    env.store["layout"] = folder_layout(
        [{"kind": "folder", "id": "f"}, {"kind": "connector", "id": "c"}],
        {"f": {"name": "Greek letters", "connector_ids": ["a", "b"]}},
    )

    async def check(user):
        cx._open_folder_floating_window("f")
        assert set(cx.connector_cards) == {"a", "b", "c"}
        cx.on_search_change(SimpleNamespace(value="beta"))
        assert not hidden(cx.folder_cards["f"])
        assert hidden(cx.connector_cards["a"]) and not hidden(cx.connector_cards["b"])
        assert hidden(cx.connector_cards["c"])
        cx.on_search_change(SimpleNamespace(value="greek"))
        assert not hidden(cx.connector_cards["a"]) and not hidden(cx.connector_cards["b"])
        cx.on_search_change(SimpleNamespace(value="zzz"))
        assert hidden(cx.folder_cards["f"])
        cx.on_search_change(SimpleNamespace(value=""))
        assert not hidden(cx.connector_cards["a"])
        del cx.connector_cards["a"]  # a member card that is not rendered is skipped
        cx.update_search_visibility()

    tab(env, check)


def test_search_visibility_errors_show_elements(env):
    add(env, conn("a"))
    env.store["layout"] = folder_layout([{"kind": "folder", "id": "f"}],
                                        {"f": {"name": "F", "connector_ids": ["a"]}})

    async def check(user):
        add(env, conn("z"))
        cx.load_connectors()
        cx.folder_cards["f"].classes(add="hidden")

        def boom(cid):
            raise RuntimeError("x")

        env.mp.setattr(env.mgr, "get_connector", boom)
        cx.update_search_visibility()
        assert not hidden(cx.folder_cards["f"]) and not hidden(cx.connector_cards["z"])

    tab(env, check)


# --------------------------------------------------------------------------- floaters


def test_folder_floater_open_close_and_title(env):
    add(env, conn("a"), conn("b", enabled=False))
    env.store["layout"] = folder_layout([{"kind": "folder", "id": "f"}],
                                        {"f": {"name": "Box", "connector_ids": ["a", "b"]}})

    async def check(user):
        cx._open_folder_floating_window("f")
        await user.should_see("drag a card out to remove it")
        assert "f" in cx._folder_floaters and cx.connector_parent_folder["a"] == "f"
        cx._open_folder_floating_window("f")
        assert env.notes[-1][0] == "This folder is already open"
        cx._update_folder_floater_title("f", "Renamed")
        assert cx._folder_floaters["f"]["title_label"].text == "Renamed"
        cx._update_folder_floater_title("missing", "x")
        # reload keeps the floater open
        cx.load_connectors()
        assert "f" in cx._folder_floaters
        cx._close_folder_floater("f")
        assert "f" not in cx._folder_floaters and "a" not in cx.connector_cards
        cx._close_folder_floater("f")
        # a stale entry whose shell is gone is replaced
        cx._open_folder_floating_window("f")
        cx._folder_floaters["f"]["shell"].delete()
        cx._open_folder_floating_window("f")
        assert not cx._folder_floaters["f"]["shell"].is_deleted
        cx._close_all_folder_floaters()
        assert cx._folder_floaters == {}

    tab(env, check)


def test_folder_floater_missing_and_empty(env):
    add(env, conn("a"))

    async def check(user):
        cx._open_folder_floating_window("ghost")
        assert env.notes[-1][0].startswith("That folder no longer exists")
        # empty folder (no live members): reconcile drops it too
        env.store["layout"] = folder_layout([{"kind": "folder", "id": "e"}], {"e": {"name": "E", "connector_ids": []}})
        cx._open_folder_floating_window("e")
        assert "e" not in cx._folder_floaters

    tab(env, check)


def test_folder_floater_empty_members_label(env, monkeypatch):
    add(env, conn("a"))

    async def check(user):
        monkeypatch.setattr(ls, "reconcile_layout",
                            lambda layout, ids: folder_layout([{"kind": "folder", "id": "e"}],
                                                              {"e": {"name": "Empty", "connector_ids": ["gone"]}}))
        cx._open_folder_floating_window("e")
        await user.should_see("Drag connectors here to add them to this folder.")

    tab(env, check)


def test_close_floater_ignores_delete_errors(env):
    class Shell:
        is_deleted = False

        def delete(self):
            raise RuntimeError("gone")

    cx._folder_floaters["f"] = {"shell": Shell()}
    cx._close_folder_floater("f")
    cx._folder_floaters["g"] = {"shell": None}
    cx._close_folder_floater("g")
    assert cx._folder_floaters == {}


def test_folder_header_drag_js_quotes_id():
    js = cx._folder_header_drag_js('c"1')
    assert 'document.getElementById("c\\"1")' in js and js.startswith("(e) =>")


# --------------------------------------------------------------------------- drag and drop


def test_drop_on_card_creates_folder_and_names_it(env, monkeypatch):
    add(env, conn("a"), conn("b"))
    monkeypatch.setattr(ls, "uuid", SimpleNamespace(uuid4=lambda: "NEWF"))

    async def check(user):
        cx._handle_drop_on_card("a")  # nothing dragged
        cx._set_drag_source("a", None)
        cx._handle_drop_on_card("a")  # onto itself
        assert cx._peek_drag_source() is None
        cx._set_drag_source("b", None)
        cx._handle_drop_on_card("a")
        assert env.store["layout"]["folders"]["NEWF"]["connector_ids"] == ["a", "b"]
        await user.should_see("Name this folder")
        user.find(ui.input).elements  # dialog input exists
        inputs = [i for i in user.find(ui.input).elements if i.value == "New folder"]
        inputs[0].value = "  Combo  "
        user.find("Save").click()
        assert env.store["layout"]["folders"]["NEWF"]["name"] == "Combo"

    tab(env, check)


def test_drop_on_card_merge_failure(env, monkeypatch):
    add(env, conn("a"), conn("b"))
    monkeypatch.setattr(ls, "merge_into_new_folder", lambda layout, src, tgt: (layout, ""))

    async def check(user):
        cx._set_drag_source("b", None)
        saves = len(env.store["saves"])
        cx._handle_drop_on_card("a")
        assert len(env.store["saves"]) == saves

    tab(env, check)


def test_prompt_new_folder_name_blank_becomes_folder(env):
    add(env, conn("a"))
    env.store["layout"] = folder_layout([{"kind": "folder", "id": "f"}], {"f": {"name": "X", "connector_ids": ["a"]}})

    async def check(user):
        cx._prompt_new_folder_name("f")
        for i in user.find(ui.input).elements:
            if i.value == "X":
                i.value = "   "
        user.find("Save").click()
        assert env.store["layout"]["folders"]["f"]["name"] == "Folder"

    tab(env, check)


def test_drop_on_folder_and_root(env):
    add(env, conn("a"), conn("b"), conn("c"))
    env.store["layout"] = folder_layout(
        [{"kind": "folder", "id": "f"}, {"kind": "connector", "id": "c"}],
        {"f": {"name": "F", "connector_ids": ["a", "b"]}},
    )

    async def check(user):
        cx._handle_drop_on_folder("f")  # nothing dragged
        cx._handle_drop_on_root()
        cx._set_drag_source("a", "f")
        cx._handle_drop_on_folder("f")  # same folder: ignored
        assert cx._peek_drag_source() is None
        cx._set_drag_source("c", None)
        cx._handle_drop_on_folder("f")
        assert env.store["layout"]["folders"]["f"]["connector_ids"] == ["a", "b", "c"]
        cx._set_drag_source("c", None)
        cx._handle_drop_on_root()  # not from a folder: ignored
        cx._set_drag_source("c", "f")
        cx._handle_drop_on_root()
        assert {"kind": "connector", "id": "c"} in env.store["layout"]["root_items"]
        cx._set_drag_source("a", "f")
        cx._clear_drag_source()
        assert cx._pop_drag_source() is None

    tab(env, check)


def test_card_drag_events_set_source(env):
    add(env, conn("a"))

    async def check(user):
        card = cx.connector_cards["a"]
        for listener in card._event_listeners.values():
            if listener.type == "dragstart":
                listener.handler(SimpleNamespace())
        assert cx._peek_drag_source() == {"connector_id": "a", "from_folder_id": None}
        for listener in card._event_listeners.values():
            if listener.type == "dragend":
                listener.handler(SimpleNamespace())
        assert cx._peek_drag_source() is None

    tab(env, check)


# --------------------------------------------------------------------------- folder dialogs


def test_rename_folder_dialog(env):
    add(env, conn("a"))
    env.store["layout"] = folder_layout([{"kind": "folder", "id": "f"}], {"f": {"name": "Old", "connector_ids": ["a"]}})

    async def check(user):
        cx._open_folder_floating_window("f")
        cx.show_rename_folder_dialog("f")
        await user.should_see("Rename folder")
        for i in user.find(ui.input).elements:
            if i.value == "Old":
                i.value = "Fresh"
        user.find("Save").click()
        assert env.store["layout"]["folders"]["f"]["name"] == "Fresh"
        assert cx.folder_tile_title_labels["f"].text == "Fresh"
        assert cx._folder_floaters["f"]["title_label"].text == "Fresh"

    tab(env, check)


def test_delete_folder_keep_connectors(env):
    add(env, conn("a"), conn("b"))
    env.store["layout"] = folder_layout([{"kind": "folder", "id": "f"}], {"f": {"name": "F", "connector_ids": ["a", "b"]}})

    async def check(user):
        cx.show_delete_folder_dialog("f")
        await user.should_see("Delete folder")
        user.find("Delete folder only — keep connectors").click()
        assert env.store["layout"]["folders"] == {}
        assert set(env.mgr.connectors) == {"a", "b"}

    tab(env, check)


def test_delete_folder_and_connectors(env):
    add(env, conn("a"), conn("b"), conn("c"))
    env.store["layout"] = folder_layout([{"kind": "folder", "id": "f"}, {"kind": "connector", "id": "c"}],
                                        {"f": {"name": "F", "connector_ids": ["a", "b"]}})

    async def check(user):
        cx.show_delete_folder_dialog("f")
        user.find("Delete folder and all connectors inside").click()
        assert set(env.mgr.connectors) == {"c"} and env.store["layout"]["folders"] == {}

    tab(env, check)


# --------------------------------------------------------------------------- folder enable


def test_folder_members_enabled_state():
    cons = {"a": SimpleNamespace(enabled=True), "b": SimpleNamespace(enabled=False)}
    assert cx._folder_members_enabled_state([], cons) == "all_off"
    assert cx._folder_members_enabled_state(["b", "zz"], cons) == "all_off"
    assert cx._folder_members_enabled_state(["a"], cons) == "all_on"
    assert cx._folder_members_enabled_state(["a", "b"], cons) == "mixed"


def test_set_folder_connectors_enabled(env):
    add(env, conn("a"), conn("b", enabled=False))
    env.store["layout"] = folder_layout([{"kind": "folder", "id": "f"}], {"f": {"name": "Box", "connector_ids": ["a", "b"]}})

    async def check(user):
        cx.set_folder_connectors_enabled("f", True)
        assert env.notes[-1][0] == "Turned on 1 connector in “Box”" and env.mgr.connectors["b"].enabled
        cx.set_folder_connectors_enabled("f", True)
        assert env.notes[-1][0] == "All connectors in “Box” were already on"
        cx.set_folder_connectors_enabled("f", False)
        assert env.notes[-1][0] == "Turned off 2 connectors in “Box”"
        cx.set_folder_connectors_enabled("ghost", False)
        assert env.notes[-1][0] == "“Folder” has no connectors yet"

        def boom(ids, enabled):
            raise RuntimeError("db")

        env.mp.setattr(env.mgr, "set_connectors_enabled", boom)
        cx.set_folder_connectors_enabled("f", False)
        assert env.notes[-1][1]["title"] == "Couldn't turn the folder's connectors off"

    tab(env, check)


# --------------------------------------------------------------------------- card actions


def test_toggle_connector(env):
    add(env, conn("a"))

    async def check(user):
        cx.toggle_connector("a", False)
        assert not env.mgr.connectors["a"].enabled and env.notes[-1][0] == "“Conn a” turned off"
        cx.toggle_connector("a", False)  # already off: no flip, still confirms
        assert env.notes[-1][0] == "“Conn a” turned off"
        cx.toggle_connector("ghost", True)
        assert env.notes[-1][0].startswith("That connector no longer exists")
        env.mp.setattr(env.mgr, "toggle_connector", lambda cid: False)
        cx.toggle_connector("a", True)
        assert env.notes[-1][1]["title"] == "Couldn't turn “Conn a” on"
        env.mgr.last_save_ok = False
        env.mp.setattr(env.mgr, "toggle_connector", lambda cid: True)
        count = len(env.notes)
        cx.toggle_connector("a", True)
        assert len(env.notes) == count

        def boom(cid):
            raise RuntimeError("x")

        env.mp.setattr(env.mgr, "get_connector", boom)
        cx.toggle_connector("a", True)
        assert env.notes[-1][1]["title"] == "Couldn't change the connector"

    tab(env, check)


def test_delete_connector_paths(env):
    add(env, conn("a"))

    def confirm(cid):
        cx.delete_connector(cid)
        dialog = [d for d in cx.ui.context.client.layout.descendants() if isinstance(d, ui.dialog)][-1]
        button = [b for b in dialog.descendants() if isinstance(b, ui.button) and b.text == "Delete"][0]
        button._event_listeners  # noqa: B018
        for listener in button._event_listeners.values():
            if listener.type == "click":
                listener.handler(SimpleNamespace())

    async def check(user):
        add(env, conn("b"))
        cx.delete_connector("b")
        await user.should_see("Confirm Delete")
        cx.connector_cards["b"] = object()
        confirm("b")
        assert "b" not in env.mgr.connectors and env.notes[-1][0] == "Deleted “Conn b”"
        confirm("ghost")
        assert env.notes[-1][0].startswith("That connector no longer exists")
        env.mp.setattr(env.mgr, "remove_connector", lambda cid: False)
        confirm("a")
        assert env.notes[-1][1]["title"] == "Couldn't delete “Conn a”"
        env.mp.setattr(env.mgr, "remove_connector", lambda cid: True)
        env.mgr.last_save_ok = False
        cx.connector_cards["a"] = object()
        count = len(env.notes)
        confirm("a")
        assert len(env.notes) == count

        def boom(cid):
            raise RuntimeError("x")

        env.mp.setattr(env.mgr, "get_connector", boom)
        confirm("a")
        assert env.notes[-1][1]["title"] == "Couldn't delete the connector"

    tab(env, check)


def test_duplicate_connector(env):
    hk = conn("h", name="Hotkey thing", trigger_type=TriggerType.STREAMDECK)
    hk.trigger.connector_id = "h"
    add(env, conn("a", name="Base"), conn("b", name="Base copy"), hk)

    async def check(user):
        cx.duplicate_connector("a")
        names = sorted(c.name for c in env.mgr.connectors.values())
        assert "Base copy 2" in names and env.notes[-1][0] == "Duplicated as “Base copy 2”"
        clone = next(c for c in env.mgr.connectors.values() if c.name == "Base copy 2")
        assert clone.trigger_count == 0 and clone.actions[0].action_id != "a-a"
        cx.duplicate_connector("h")
        clone = next(c for c in env.mgr.connectors.values() if c.name == "Hotkey thing copy")
        assert clone.trigger.connector_id == clone.connector_id
        cx.duplicate_connector("ghost")
        assert env.notes[-1][0].startswith("That connector no longer exists")
        env.mp.setattr(env.mgr, "add_connector", lambda c: False)
        cx.duplicate_connector("a")
        assert env.notes[-1][1]["title"] == "Couldn't duplicate the connector"
        env.mp.setattr(env.mgr, "_serialize_connector", lambda c: (_ for _ in ()).throw(ValueError("x")))
        cx.duplicate_connector("a")
        assert "ValueError" in env.notes[-1][1]["details"]

    tab(env, check)


def test_duplicate_without_saved_ok_is_quiet(env):
    add(env, conn("a"))

    async def check(user):
        def add_connector(c):
            env.mgr.connectors[c.connector_id] = c
            env.mgr.last_save_ok = False
            return True

        env.mp.setattr(env.mgr, "add_connector", add_connector)
        count = len(env.notes)
        cx.duplicate_connector("a")
        assert len(env.notes) == count and len(env.mgr.connectors) == 2

    tab(env, check)


def test_unique_connector_name():
    assert cx._unique_connector_name("", set()) == "Connector copy"
    assert cx._unique_connector_name(" X ", {"x COPY", None, "X copy 2"}) == "X copy 3"


def test_show_connector_dialog_and_test(env, monkeypatch):
    from modules.uiwindows import connector_editor as ce

    opened, tested = [], []

    class Editor:
        def __init__(self):
            self.dialog = SimpleNamespace(delete=lambda: opened.append("deleted"))

    monkeypatch.setattr(ce, "open_connector_editor", lambda cid, on_saved: opened.append((cid, on_saved)) or Editor())
    monkeypatch.setattr(ce, "run_connector_test", lambda cid: tested.append(cid))
    cx.show_create_connector_dialog()
    cx.show_edit_connector_dialog("a")
    assert opened == [(None, cx.refresh_connectors), "deleted", ("a", cx.refresh_connectors)]
    cx._active_editor = SimpleNamespace(dialog=SimpleNamespace(delete=lambda: (_ for _ in ()).throw(RuntimeError())))
    cx.show_connector_dialog("b")

    def boom(cid, on_saved):
        raise RuntimeError("editor broke")

    monkeypatch.setattr(ce, "open_connector_editor", boom)
    cx.show_connector_dialog("c")
    assert env.notes[-1][1]["title"] == "Couldn't open the connector editor"
    cx.test_connector("a")
    assert tested == ["a"]


def test_card_buttons_dispatch(env, monkeypatch):
    add(env, conn("a"))
    calls = []
    monkeypatch.setattr(cx, "test_connector", lambda cid: calls.append(("test", cid)))
    monkeypatch.setattr(cx, "show_edit_connector_dialog", lambda cid: calls.append(("edit", cid)))
    monkeypatch.setattr(cx, "duplicate_connector", lambda cid: calls.append(("dup", cid)))
    monkeypatch.setattr(cx, "delete_connector", lambda cid: calls.append(("del", cid)))

    async def check(user):
        user.find(marker=None, kind=ui.button).elements  # noqa: B018
        for b in user.find(ui.button).elements:
            if b._props.get("icon") in ("play_arrow", "edit"):
                b.run_method  # noqa: B018
                for listener in b._event_listeners.values():
                    listener.handler(SimpleNamespace())
        for item in user.find(ui.menu_item).elements:
            for listener in item._event_listeners.values():
                if listener.type == "click":
                    listener.handler(SimpleNamespace())
        assert ("test", "a") in calls and ("edit", "a") in calls
        assert ("dup", "a") in calls and ("del", "a") in calls

    tab(env, check)


# --------------------------------------------------------------------------- display names


def test_get_action_display_name_variants():
    def act(t, **kw):
        return SimpleNamespace(action_type=ActionType(t), **kw)

    g = cx.get_action_display_name
    assert g(act("send_chat_message")) == "Send chat message → Twitch"
    assert g(act("send_discord_message", channels=[{"channel_name": "gen"}])) == "Send Discord message → #gen"
    assert g(act("send_discord_message", channels=[{"channel_id": "1"}, {"channel_id": "2"}, "x"])) == \
        "Send Discord message → 2 channels"
    assert g(act("trigger_alert", alert_type="")) == "Play an alert"
    assert g(act("template_control", template_name="spin_wheel", control_action="spin_now")) == \
        "Spin Wheel → spin now"
    assert g(act("template_control", template_name="", control_action="")) == "Control an overlay"
    assert g(act("display_banner", permanent=True)) == "Show a banner (until replaced)"
    assert g(act("obs_control", operation="toggle_record")) == "OBS — Toggle recording"
    assert g(act("obs_control", operation="made_up_op")) == "made up op"
    assert g(act("game_hook", operation="nope_op")).startswith("FF7: ")
    assert g(act("api_call", url="https://example.com/x")) == "Web request → example.com"
    assert g(act("api_call", url="ftp://x")) == "Web request"
    assert g(act("write_file", file_path="C:\\logs\\out.txt")) == "Write to a file: out.txt"
    assert g(act("write_file", file_path="")) == "Write to a file"
    assert g(act("key_press", input_type="macro")) == "Press keys / click: macro"
    assert g(act("key_press", key_sequence="left_click")) == "Press keys / click: left click"
    assert g(act("key_press", key_sequence="")) == "Press keys / click"
    assert g(act("websocket_emit", event_name="ev")) == "Send overlay event: ev"
    assert g(act("websocket_emit", event_name="")) == "Send overlay event"
    assert g(act("execute_command")) == "Run a command"
    assert g(SimpleNamespace(action_type=None, name="Fallback")) == "Fallback"
    assert g(SimpleNamespace(action_type=None)) == "Unknown action"


def test_get_action_display_name_unknown_spec(monkeypatch):
    from modules.uiwindows import connector_action_schemas as S

    monkeypatch.setattr(S, "spec_for", lambda t: None)
    assert cx.get_action_display_name(SimpleNamespace(action_type=ActionType.WRITE_FILE, file_path="")) == "Write file"


def test_format_condition_and_trigger_names():
    from modules.connector_core import ComparisonOperator, TriggerCondition

    c = TriggerCondition("amount", ComparisonOperator.GREATER_THAN, 100)
    assert cx._format_condition_for_card(c, TriggerType.TWITCH_BITS).endswith("> 100")
    flag = TriggerCondition("is_mod", ComparisonOperator.EQUAL, True)
    assert cx._format_condition_for_card(flag, TriggerType.TWITCH_CHAT_MESSAGE).endswith("= true")
    odd = SimpleNamespace(field="", operator="weird_op", value=False)
    assert cx._format_condition_for_card(odd, None) == " weird op false"
    fake = SimpleNamespace(value="made_up_type")
    assert cx.format_trigger_name(fake) == "Made Up Type"


def test_glyph_fallbacks(monkeypatch):
    monkeypatch.setattr(cx, "SERVICE_BRAND_SVG", {})
    assert cx._trigger_glyph_html(TriggerType.TWITCH_BITS) == cx._icon_html("bolt")
    assert cx._action_glyph_html(SimpleNamespace(action_type=ActionType.OBS_CONTROL)) == cx._icon_html("play_arrow")
    assert cx._trigger_glyph_html(None) == cx._icon_html("bolt")
    assert cx._action_glyph_html(SimpleNamespace()) == cx._icon_html("play_arrow")
    assert cx._brand_html("nope") == ""


def test_connector_search_blob():
    c = conn("a", name="Name", description="Desc")
    blob = cx._connector_search_blob(c)
    assert "name" in blob and "desc" in blob and "send chat message" in blob
    c.trigger = None
    c.name = None
    c.description = None
    c.actions = None
    assert cx._connector_search_blob(c).strip() == ""
