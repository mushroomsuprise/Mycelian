# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/customsources.py, part 3: save, discard and template file actions.

Save / reset, and duplicate / rename / delete with their background work. Every template
file lives under tmp_path (get_template_path / get_assets_path are redirected), background
threads run synchronously and the footer progress state is restored after each test.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HERE = str(Path(__file__).resolve().parent)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402

from _customsources_support import FakeParser, FakeSelect, fire, make_env  # noqa: E402
from modules import template_copy_progress as tcp  # noqa: E402
from modules.spore_studio import containers  # noqa: E402
from modules.spore_studio.save_pipeline import SporeStudioError  # noqa: E402
from modules.template_config_parser import TemplateConfigParser  # noqa: E402
from modules.uiwindows import customsources as cs  # noqa: E402
from ui_sim import run_ui  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    e = make_env(monkeypatch, tmp_path)
    monkeypatch.setattr(containers, "container_route_exists", lambda name: False)
    e.flushes = []
    monkeypatch.setattr(cs, "_flush_template_preview", lambda: e.flushes.append(1))
    return e


@pytest.fixture
def real_parser(env):
    return TemplateConfigParser(config_dir=str(env.templates / "template_configs"))


def write_template(env, name, *, config=None, html=None, sidecar=None, assets=None):
    cfg_path = env.templates / "template_configs" / f"{name}.json"
    cfg_path.write_text(json.dumps(config if config is not None else {"template_name": name, "elements": []}),
                        encoding="utf-8")
    if html is not None:
        (env.templates / f"{name}.html").write_text(html, encoding="utf-8")
    if sidecar is not None:
        (env.templates / "_spore" / f"{name}.spore.json").write_text(json.dumps(sidecar), encoding="utf-8")
    for rel, data in (assets or {}).items():
        p = env.assets / name / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return cfg_path


def everything(user):
    return list(user.client.layout.descendants())


def button(user, text):
    return [e for e in everything(user) if isinstance(e, ui.button) and e.text == text][-1]


# ---------------------------------------------------------------- save_config

SAVE_ELEMENTS = [
    {"type": "separator", "label": "Look"},
    {"type": "text", "id": "Title", "label": "Title", "value": "old"},
    {"type": "color", "id": "Bg", "value": "#000"},
    {"type": "select", "id": "Grid", "display": "color_grid", "value": "#fff"},
    {"type": "select", "id": "Plain", "value": "a"},
]


def test_save_config_no_template(env):
    cs.save_config(FakeParser(), FakeSelect(None), None)
    assert env.titles() == ["Choose a template first"]


def test_save_config_success(env, monkeypatch):
    parser = FakeParser({"tpl": {"template_name": "tpl", "elements": SAVE_ELEMENTS}})
    cs.form_data_store["tpl"] = {"Title": "new", "Bg": "transparent", "Grid": "rgba(0,0,0,0)", "Plain": "b"}
    cs._preview_route_cache["tpl"] = "old"
    labels = []
    monkeypatch.setattr(cs, "_refresh_preview_dirty_label", labels.append)
    cs.save_config(parser, FakeSelect("tpl"), None)
    saved = {e.get("id"): e for e in parser.saved[0][1]["elements"]}
    assert saved["Title"]["value"] == "new" and saved["Plain"]["value"] == "b"
    assert saved["Bg"]["transparent"] is True and saved["Grid"]["transparent"] is True
    assert "transparent" not in saved["Plain"]
    assert env.titles() == ["Saved tpl"]
    assert cs._disk_baselines["tpl"] == cs.form_data_store["tpl"]
    assert "tpl" not in cs._preview_route_cache and env.flushes == [1] and labels == ["tpl"]


def test_save_config_reports_missing_fields(env):
    elements = [{"type": "text", "id": f"F{i}", "label": f"Field {i}", "value": i} for i in range(5)]
    parser = FakeParser({"tpl": {"elements": elements}})
    cs.form_data_store["tpl"] = {"F0": "x"}
    cs.save_config(parser, FakeSelect("tpl"), None)
    msg, kw = env.notes[0]
    assert kw["title"] == "Saved tpl, but some fields weren't updated"
    assert msg == "These fields kept their previous value: Field 1, Field 2, Field 3…."
    assert kw["details"] == "F1, F2, F3, F4"
    env.notes.clear()
    parser.configs["tpl"]["elements"] = elements[:2]
    cs.form_data_store["tpl"] = {"F0": "x"}
    cs.save_config(parser, FakeSelect("tpl"), None)
    assert env.notes[0][0] == "These fields kept their previous value: Field 1."


def test_save_config_combobar_numbers(env):
    elements = [
        {"type": "number", "id": "Tier2EXP", "value": 1},
        {"type": "number", "id": "Tier3EXP", "value": 2},
        {"type": "number", "id": "TotalLevels", "value": 3},
        {"type": "number", "id": "ExpIncreasePerLevel", "value": 4},
        {"type": "text", "id": "DefaultGoalXP", "value": "keep-as-text"},
    ]
    parser = FakeParser({"combobar": {"elements": elements}})
    cs.form_data_store["combobar"] = {"Tier2EXP": "5", "Tier3EXP": "2.5", "TotalLevels": "abc",
                                      "ExpIncreasePerLevel": 7, "DefaultGoalXP": "9"}
    cs.save_config(parser, FakeSelect("combobar"), None)
    vals = {e["id"]: e["value"] for e in parser.saved[0][1]["elements"]}
    assert vals == {"Tier2EXP": 5, "Tier3EXP": 2.5, "TotalLevels": 3, "ExpIncreasePerLevel": 7, "DefaultGoalXP": "9"}


@pytest.mark.parametrize("fail", [False, True])
def test_save_config_reloads_game_hook(env, monkeypatch, fail):
    from modules import game_hooks_service as ghs

    reloaded = []

    def reload(name):
        if fail:
            raise RuntimeError("hook busy")
        reloaded.append(name)

    monkeypatch.setattr(ghs.game_hooks_service, "reload_hook_config", reload)
    parser = FakeParser({"ff7": {"elements": []}})
    cs.form_data_store["ff7"] = {}
    cs.save_config(parser, FakeSelect("ff7"), None)
    if fail:
        assert env.titles() == ["Saved ff7", "Saved ff7, but the game hook didn't pick it up"]
        assert env.notes[1][1]["details"] == "RuntimeError: hook busy"
    else:
        assert reloaded == ["ff7"] and env.titles() == ["Saved ff7"]


def test_save_config_write_failure(env):
    parser = FakeParser({"tpl": {"elements": []}})
    parser.save_ok = False
    parser.last_error = "disk full"
    cs.form_data_store["tpl"] = {"x": 1}
    cs.save_config(parser, FakeSelect("tpl"), None)
    msg, kw = env.notes[0]
    assert kw["title"] == "Couldn't save tpl" and kw["details"] == "disk full" and kw["type"] == "error"
    assert "tpl" not in cs._disk_baselines and env.flushes == []
    parser.last_error = ""
    cs.save_config(parser, FakeSelect("tpl"), None)
    assert env.notes[1][1]["details"] is None


def test_save_config_exception(env):
    parser = FakeParser({"tpl": {}})
    parser.load_error = KeyError("boom")
    cs.save_config(parser, FakeSelect("tpl"), None)
    assert env.notes[0][1]["details"] == "KeyError: 'boom'"
    assert env.notes[0][1]["hint"].startswith("Try again.")


# ---------------------------------------------------------------- reset

def test_reset_config_no_template_and_clean(env, monkeypatch):
    resets = []
    monkeypatch.setattr(cs, "_perform_config_reset", lambda p, n, c: resets.append(n))
    cs.reset_config(None, FakeSelect(None), None)
    assert env.titles() == ["Choose a template first"]
    cs.reset_config(None, FakeSelect("tpl"), None)
    assert resets == ["tpl"]


def test_reset_config_dirty_asks_first(env, monkeypatch):
    resets = []
    monkeypatch.setattr(cs, "_perform_config_reset", lambda p, n, c: resets.append(n))
    cs._disk_baselines["tpl"] = {"a": 1}
    cs.form_data_store["tpl"] = {"a": 2}

    def build():
        ui.button("go", on_click=lambda: cs.reset_config(None, FakeSelect("tpl"), None))

    async def check(user):
        user.find("go").click()
        await user.should_see("Go back to the last saved version of tpl? Your unsaved edits will be lost.")
        fire(user, button(user, "Stay"))
        assert resets == []
        fire(user, button(user, "Discard changes"))
        assert resets == ["tpl"]

    run_ui(build, check)


def test_perform_config_reset(env, monkeypatch):
    renders = []
    monkeypatch.setattr(cs, "render_config_ui", lambda p, n, c: renders.append((n, n in cs.form_data_store)))
    cs.form_data_store["tpl"] = {"a": 2}
    cs._disk_baselines["tpl"] = {"a": 1}
    cs._preview_route_cache["tpl"] = "r"
    cs._perform_config_reset(None, "tpl", None)
    assert renders == [("tpl", False)] and "tpl" not in cs._disk_baselines and "tpl" not in cs._preview_route_cache
    assert env.flushes == [1] and env.titles() == ["Reverted tpl to the last saved version"]


# ---------------------------------------------------------------- small file helpers

def test_paths_and_name_taken(env, real_parser):
    assert cs._spore_sidecar_file("t") == str(env.templates / "_spore" / "t.spore.json")
    assert cs._legacy_sidecar_file("t") == str(env.templates / "template_configs" / "t.spore.json")
    assert cs._template_assets_dir("t") == str(env.assets / "t")
    assert cs._template_name_is_taken("free", real_parser) is False
    assert cs._template_name_is_taken("a/b", FakeParser(config_dir=str(env.templates))) is True
    (env.templates / "h.html").write_text("x")
    assert cs._template_name_is_taken("h", real_parser) is True
    write_template(env, "j")
    assert cs._template_name_is_taken("j", real_parser) is True
    (env.templates / "_spore" / "s.spore.json").write_text("{}")
    assert cs._template_name_is_taken("s", real_parser) is True
    (env.assets / "a").mkdir()
    assert cs._template_name_is_taken("a", real_parser) is True


def test_refresh_copy_footer_and_engine_cache(env, monkeypatch):
    from modules import notification_engine

    cs._refresh_template_copy_footer()
    assert env.footer == [1]
    monkeypatch.setattr(notification_engine, "refresh_template_copy_footer",
                        lambda: (_ for _ in ()).throw(RuntimeError()))
    cs._refresh_template_copy_footer()
    cs._invalidate_webengine_template_cache()
    assert env.engine.invalidated == 1
    monkeypatch.setattr(cs.web_engine_module, "web_engine_instance", SimpleNamespace())
    cs._invalidate_webengine_template_cache()
    monkeypatch.setattr(cs.web_engine_module, "web_engine_instance", SimpleNamespace(
        invalidate_all_template_configs_cache=lambda: (_ for _ in ()).throw(RuntimeError())))
    cs._invalidate_webengine_template_cache()
    monkeypatch.setattr(cs.web_engine_module, "web_engine_instance", None)
    cs._invalidate_webengine_template_cache()


def test_list_mirror_and_cleanup(env, monkeypatch):
    src = env.assets / "src"
    assert cs._list_asset_files(str(src)) == []
    (src / "a" / "b").mkdir(parents=True)
    (src / "x.png").write_bytes(b"1")
    (src / "a" / "b" / "y.mp3").write_bytes(b"2")
    files = cs._list_asset_files(str(src))
    assert files == sorted([str(src / "a" / "b" / "y.mp3"), str(src / "x.png")])
    dst = env.assets / "dst"
    cs._mirror_asset_dirs(str(src), str(dst))
    assert (dst / "a" / "b").is_dir()
    written = env.assets / "w.txt"
    written.write_text("x")
    cs._cleanup_partial_copy([str(written), str(env.assets / "missing")], str(dst), True)
    assert not written.exists() and not dst.exists()
    keep = env.assets / "keep"
    keep.mkdir()
    blocked = env.assets / "b.txt"
    blocked.write_text("x")
    monkeypatch.setattr(cs.os, "remove", lambda p: (_ for _ in ()).throw(OSError("locked")))
    cs._cleanup_partial_copy([str(blocked)], str(keep), False)
    assert blocked.exists() and keep.exists()


def test_same_file_and_blocks_rename(env, monkeypatch, tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("x")
    b = tmp_path / "b.txt"
    assert cs._same_existing_file(str(a), str(a)) is True
    assert cs._same_existing_file(str(a), str(b)) is False
    assert cs._path_blocks_rename(str(a), str(b)) is False
    b.write_text("y")
    assert cs._path_blocks_rename(str(a), str(b)) is True
    assert cs._path_blocks_rename(str(a), str(a)) is False
    monkeypatch.setattr(cs.os.path, "samefile", lambda x, y: (_ for _ in ()).throw(OSError()))
    assert cs._same_existing_file(str(a), str(b)) is False


def test_rewrite_tree_and_html():
    tree = {"a": ["/assets/old/x.png", {"b": "old/counters/c", "n": 3}], "c": None, "d": "/assets/older/y"}
    assert cs._rewrite_template_tree(tree, "old", "new") == {
        "a": ["/assets/new/x.png", {"b": "new/counters/c", "n": 3}], "c": None, "d": "/assets/older/y"}
    html = '<img src="/assets/old/a.png"> old/counters window.__sporeTemplateName = "old"'
    assert cs._rewrite_template_html(html, "old", "new") == \
        '<img src="/assets/new/a.png"> new/counters window.__sporeTemplateName = "new"'


def test_write_rename_text(env, monkeypatch, tmp_path):
    target = tmp_path / "deep" / "f.txt"
    cs._write_rename_text(str(target), "hello")
    assert target.read_text() == "hello" and not (tmp_path / "deep" / "f.txt.rename-tmp").exists()
    monkeypatch.chdir(tmp_path)
    cs._write_rename_text("plain.txt", "p")
    assert (tmp_path / "plain.txt").read_text() == "p"

    def bad_replace(s, d):
        raise OSError("locked")

    monkeypatch.setattr(cs.os, "replace", bad_replace)
    with pytest.raises(OSError):
        cs._write_rename_text(str(target), "new")
    assert not Path(str(target) + ".rename-tmp").exists() and target.read_text() == "hello"
    monkeypatch.setattr(cs.os, "remove", lambda p: (_ for _ in ()).throw(OSError("busy")))
    with pytest.raises(OSError):
        cs._write_rename_text(str(target), "new")


def test_publish_renamed_file(env, tmp_path):
    created = []
    src = tmp_path / "s.txt"
    src.write_text("s")
    cs._publish_renamed_file(str(src), str(tmp_path / "d.txt"), "d", created)
    assert created == [str(tmp_path / "d.txt")]
    cs._publish_renamed_file(str(src), str(src), "s2", created)
    assert len(created) == 1 and src.read_text() == "s2"


# ---------------------------------------------------------------- copy files

def test_copy_template_files_full(env, real_parser):
    write_template(env, "orig", config={"template_name": "orig", "elements": [{"id": "I", "value": "/assets/orig/a.png"}]},
                   html='<script>window.__sporeTemplateName = "orig"</script>',
                   sidecar={"template_name": "orig", "x": "orig/counters/1"},
                   assets={"a.png": b"img", "sub/b.mp3": b"snd"})
    progress = []
    cs._copy_template_files("orig", "copy", real_parser, lambda *a: progress.append(a))
    cfg = json.loads((env.templates / "template_configs" / "copy.json").read_text())
    assert cfg["template_name"] == "copy" and cfg["elements"][0]["value"] == "/assets/copy/a.png"
    assert 'window.__sporeTemplateName = "copy"' in (env.templates / "copy.html").read_text()
    side = json.loads((env.templates / "_spore" / "copy.spore.json").read_text())
    assert side == {"template_name": "copy", "x": "copy/counters/1"}
    assert (env.assets / "copy" / "sub" / "b.mp3").read_bytes() == b"snd"
    assert (env.templates / "orig.html").exists()  # source untouched
    assert progress[0] == ("preparing", 0, 0)
    assert progress[1] == ("copying", 0, 5) and progress[-1] == ("copying", 5, 5)


def test_copy_template_files_minimal_without_progress(env, real_parser):
    write_template(env, "orig")
    cs._copy_template_files("orig", "copy", real_parser)
    assert (env.templates / "template_configs" / "copy.json").exists()
    assert not (env.templates / "copy.html").exists() and not (env.assets / "copy").exists()


@pytest.mark.parametrize("content,msg", [(None, "No configuration file for 'orig'."), ("{bad", "Could not read 'orig'"),
                                         ("[1]", "Configuration for 'orig' is not an object.")])
def test_copy_template_files_bad_source(env, real_parser, content, msg):
    if content is not None:
        (env.templates / "template_configs" / "orig.json").write_text(content)
    with pytest.raises(SporeStudioError, match=msg.replace("(", r"\(").replace(".", r"\.")):
        cs._copy_template_files("orig", "copy", real_parser)


def test_copy_template_files_html_unreadable(env, real_parser, monkeypatch):
    write_template(env, "orig", html="x")
    import builtins

    real_open = builtins.open

    def fake_open(path, *a, **k):
        if str(path).endswith("orig.html"):
            raise OSError("denied")
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)
    with pytest.raises(SporeStudioError, match=r"Could not read 'orig\.html'"):
        cs._copy_template_files("orig", "copy", real_parser)


def test_copy_template_files_failure_cleans_up(env, real_parser, monkeypatch):
    write_template(env, "orig", html="h", assets={"a.png": b"1"})

    def bad_copy(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(cs.shutil, "copy2", bad_copy)
    with pytest.raises(SporeStudioError, match="Could not copy 'orig': disk full"):
        cs._copy_template_files("orig", "copy", real_parser)
    assert not (env.templates / "template_configs" / "copy.json").exists()
    assert not (env.templates / "copy.html").exists() and not (env.assets / "copy").exists()


# ---------------------------------------------------------------- rename files

@pytest.fixture
def routes(env, monkeypatch):
    calls = []
    from modules.spore_studio import save_pipeline

    monkeypatch.setattr(save_pipeline, "_refresh_web_engine_routes", lambda *a: calls.append(a) or True)
    return calls


def test_rename_template_files_full(env, real_parser, routes):
    write_template(env, "orig", config={"template_name": "orig", "elements": [{"id": "I", "value": "/assets/orig/a.png"}]},
                   html='window.__sporeTemplateName = "orig"', sidecar={"template_name": "orig"},
                   assets={"a.png": b"1"})
    cs._preview_route_cache.update(orig="o", renamed="r")
    assert cs._rename_template_files("orig", " renamed ", real_parser) == "renamed"
    assert not (env.templates / "template_configs" / "orig.json").exists()
    assert not (env.templates / "orig.html").exists() and not (env.templates / "_spore" / "orig.spore.json").exists()
    assert json.loads((env.templates / "template_configs" / "renamed.json").read_text())["elements"][0]["value"] == \
        "/assets/renamed/a.png"
    assert (env.assets / "renamed" / "a.png").exists() and not (env.assets / "orig").exists()
    assert json.loads((env.templates / "_spore" / "renamed.spore.json").read_text())["template_name"] == "renamed"
    assert cs._preview_route_cache == {} and routes == [()] and env.engine.invalidated >= 1
    assert env.notes == []


@pytest.mark.parametrize(
    "raw,msg",
    [("orig", "Choose a different name."), ("bad name!", "may only contain"), ("", "cannot be empty")],
)
def test_rename_template_files_name_errors(env, real_parser, raw, msg):
    write_template(env, "orig")
    with pytest.raises(SporeStudioError, match=msg):
        cs._rename_template_files("orig", raw, real_parser)


def test_rename_template_files_source_problems(env, real_parser):
    with pytest.raises(SporeStudioError, match="No configuration file for 'orig'"):
        cs._rename_template_files("orig", "new", real_parser)
    write_template(env, "orig")
    write_template(env, "taken")
    with pytest.raises(SporeStudioError, match="A template named 'taken' already exists."):
        cs._rename_template_files("orig", "taken", real_parser)
    (env.templates / "template_configs" / "orig.json").write_text("{bad")
    with pytest.raises(SporeStudioError, match="Could not read 'orig'"):
        cs._rename_template_files("orig", "new", real_parser)
    (env.templates / "template_configs" / "orig.json").write_text("[]")
    with pytest.raises(SporeStudioError, match="is not an object"):
        cs._rename_template_files("orig", "new", real_parser)


def test_rename_template_files_html_unreadable(env, real_parser, monkeypatch):
    write_template(env, "orig", html="x")
    import builtins

    real_open = builtins.open

    def fake_open(path, *a, **k):
        if str(path).endswith("orig.html"):
            raise OSError("denied")
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)
    with pytest.raises(SporeStudioError, match=r"Could not read 'orig\.html'"):
        cs._rename_template_files("orig", "new", real_parser)


def test_rename_template_files_failure_restores(env, real_parser, monkeypatch, routes):
    write_template(env, "orig", html="h", sidecar={"a": 1}, assets={"a.png": b"1"})
    real_publish = cs._publish_renamed_file
    calls = []

    def flaky(src, dst, text, created):
        calls.append(dst)
        if dst.endswith(".html"):
            raise OSError("disk full")
        real_publish(src, dst, text, created)

    monkeypatch.setattr(cs, "_publish_renamed_file", flaky)
    with pytest.raises(SporeStudioError, match="Could not rename 'orig': disk full"):
        cs._rename_template_files("orig", "new", real_parser)
    assert (env.assets / "orig" / "a.png").exists() and not (env.assets / "new").exists()
    assert not (env.templates / "template_configs" / "new.json").exists()
    assert (env.templates / "template_configs" / "orig.json").exists()
    assert routes == []


def test_rename_template_files_failure_restore_errors(env, real_parser, monkeypatch, caplog):
    write_template(env, "orig", assets={"a.png": b"1"})
    real_move = cs.shutil.move
    moves = []

    def move(src, dst):
        moves.append((src, dst))
        if len(moves) > 1:
            raise OSError("restore failed")
        return real_move(src, dst)

    monkeypatch.setattr(cs.shutil, "move", move)
    monkeypatch.setattr(cs, "_publish_renamed_file", lambda *a: (_ for _ in ()).throw(OSError("nope")))
    with pytest.raises(SporeStudioError):
        cs._rename_template_files("orig", "new", real_parser)
    assert "Could not restore assets for orig" in caplog.text


def test_rename_template_files_cleanup_remove_error(env, real_parser, monkeypatch):
    write_template(env, "orig", html="h")
    real_publish = cs._publish_renamed_file

    def flaky(src, dst, text, created):
        if dst.endswith(".html"):
            raise OSError("disk full")
        real_publish(src, dst, text, created)

    monkeypatch.setattr(cs, "_publish_renamed_file", flaky)
    monkeypatch.setattr(cs.os, "remove", lambda p: (_ for _ in ()).throw(OSError("locked")))
    with pytest.raises(SporeStudioError):
        cs._rename_template_files("orig", "new", real_parser)


def test_rename_template_files_old_files_remain(env, real_parser, monkeypatch, routes):
    write_template(env, "orig", html="h")
    monkeypatch.setattr(cs.os, "remove", lambda p: (_ for _ in ()).throw(PermissionError("in use")))
    assert cs._rename_template_files("orig", "new", real_parser) == "new"
    msg, kw = env.notes[0]
    assert kw["title"] == "Renamed to new, but old files remain"
    assert "orig.json: in use" in kw["details"] and "orig.html: in use" in kw["details"]


# ---------------------------------------------------------------- show duplicated / duplicate dialog

def test_show_duplicated_config(env, monkeypatch):
    applied = []
    monkeypatch.setattr(cs, "_apply_config_selection", lambda n, p, c: applied.append(n))
    parser = FakeParser({"a": {}, "hid": {}}, hidden={"hid"})
    sel = FakeSelect("a")
    cs.form_data_store["hid"] = {}
    cs._disk_baselines["hid"] = {}
    cs._show_duplicated_config("hid", parser, sel, None, verb="Renamed")
    assert env.titles() == ["Renamed hid, but it's hidden"] and applied == []
    assert "hid" not in cs.form_data_store and "hid" not in cs._disk_baselines
    cs._show_duplicated_config("a", parser, sel, None)
    assert sel.options == ["a"] and sel.value == "a" and applied == ["a"]


def test_duplicate_config_guards(env):
    cs.duplicate_config(None, FakeSelect(None), None)
    cs._disk_baselines["t"] = {"a": 1}
    cs.form_data_store["t"] = {"a": 2}
    cs.duplicate_config(None, FakeSelect("t"), None)
    assert env.titles() == ["Choose a template first", "Save this template before duplicating"]


def test_duplicate_config_dialog(env, monkeypatch):
    actions = []
    monkeypatch.setattr(cs, "duplicate_config_action", lambda *a: actions.append(a))
    sel = FakeSelect("chat")

    def build():
        ui.button("go", on_click=lambda: cs.duplicate_config("parser", sel, "container"))

    async def check(user):
        user.find("go").click()
        await user.should_see("Copy of chat")
        inp = next(e for e in everything(user) if isinstance(e, ui.input) and e.value == "chat-copy")
        inp.value = "chat2"
        fire(user, button(user, "Duplicate"))
        assert actions[0][:5] == ("chat", "chat2", "parser", sel, "container")
        fire(user, button(user, "Cancel"))

    run_ui(build, check)


# ---------------------------------------------------------------- duplicate action

class Dlg:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


def test_duplicate_action_guards(env, real_parser):
    write_template(env, "orig")
    write_template(env, "taken")
    cs._disk_baselines["d"] = {"a": 1}
    cs.form_data_store["d"] = {"a": 2}
    cs.duplicate_config_action("d", "x", real_parser, None, None, Dlg())
    tcp.set_template_copy_state(active=True)
    cs.duplicate_config_action("orig", "x", real_parser, None, None, Dlg())
    tcp.mark_template_copy_idle()
    assert env.titles() == ["Save this template before duplicating", "A template copy or delete is already running"]
    env.notes.clear()
    for raw in ("orig", "taken", "bad name", ""):
        cs.duplicate_config_action("orig", raw, real_parser, None, None, Dlg())
    cs.duplicate_config_action("ghost", "fresh", real_parser, None, None, Dlg())
    assert [m for m, _ in env.notes] == [
        "Choose a different name for the copy.", "A template named 'taken' already exists.",
        "Template name may only contain letters, numbers, '_' and '-'.", "Template name cannot be empty.",
        "No configuration file for 'ghost'."]
    assert set(env.titles()) == {"Choose another name"} and env.threads == []


def test_duplicate_action_success(env, real_parser, monkeypatch, routes):
    write_template(env, "orig", html="h", assets={"a.png": b"1"})
    shown = []
    monkeypatch.setattr(cs, "_show_duplicated_config", lambda n, p, s, c, verb="Created": shown.append(n))
    states = []
    real_set = cs._set_template_copy_state
    monkeypatch.setattr(cs, "_set_template_copy_state", lambda **kw: states.append(kw) or real_set(**kw))
    dlg = Dlg()
    cs.duplicate_config_action("orig", "copy", real_parser, "sel", "cont", dlg)
    assert dlg.closed == 1 and env.threads == ["TemplateDuplicate"]
    assert (env.templates / "template_configs" / "copy.json").exists()
    assert (env.assets / "copy" / "a.png").exists()
    assert states[0]["phase"] == "preparing" and states[0]["label"] == "Copying orig to copy"
    assert states[-1]["phase"] == "copying" and states[-1]["copied"] == states[-1]["total"] == 3
    assert tcp.template_copy_is_active() is False
    assert env.titles() == ["Duplicated orig as copy"] and shown == ["copy"]
    assert len(env.footer) >= 2 and routes == [()]


def test_duplicate_action_copy_error_reported(env, real_parser, monkeypatch):
    write_template(env, "orig")
    finished = []
    monkeypatch.setattr(cs, "_finish_template_copy", lambda err, *a: finished.append(err))
    monkeypatch.setattr(cs, "_copy_template_files", lambda *a: (_ for _ in ()).throw(SporeStudioError("x")))
    cs.duplicate_config_action("orig", "copy", real_parser, None, None, Dlg())
    assert isinstance(finished[0], SporeStudioError)


def test_duplicate_action_result_lost(env, real_parser, monkeypatch):
    write_template(env, "orig")
    monkeypatch.setattr(cs, "_copy_template_files", lambda *a: None)
    monkeypatch.setattr(cs, "run_on_ui_loop", lambda fn: (_ for _ in ()).throw(RuntimeError("loop gone")))
    cs.duplicate_config_action("orig", "copy", real_parser, None, None, Dlg())
    assert tcp.template_copy_is_active() is False
    assert env.titles() == ["The template copy finished, but the screen didn't update"]


# ---------------------------------------------------------------- finish copy

def test_finish_template_copy_errors(env, monkeypatch):
    tcp.set_template_copy_state(active=True)
    cs._finish_template_copy(SporeStudioError("Name taken"), "src", "new", None, None, None)
    assert tcp.template_copy_is_active() is False
    msg, kw = env.notes[-1]
    assert (msg, kw["type"], kw["title"]) == ("Name taken", "warning", "Couldn't copy src")
    tech = SporeStudioError("Could not copy")
    tech.__cause__ = OSError("disk")
    cs._finish_template_copy(tech, "src", "new", None, None, None)
    assert env.notes[-1][1]["type"] == "error" and env.notes[-1][1]["details"] == "SporeStudioError: Could not copy"
    cs._finish_template_copy(RuntimeError("bug"), "src", "new", None, None, None)
    assert env.notes[-1][1]["details"] == "RuntimeError: bug"


def test_finish_template_copy_success_and_show_failure(env, monkeypatch, routes):
    parser = FakeParser()
    shown = []
    monkeypatch.setattr(cs, "_show_duplicated_config", lambda *a, **k: shown.append(a[0]))
    cs._preview_route_cache.update(src="s", new="n")
    cs._finish_template_copy(None, "src", "new", parser, None, None)
    assert parser.invalidations == 1 and cs._preview_route_cache == {} and shown == ["new"]
    assert env.titles() == ["Duplicated src as new"]
    monkeypatch.setattr(cs, "_show_duplicated_config", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ui gone")))
    cs._finish_template_copy(None, "src", "new", parser, None, None)
    assert env.titles()[-1] == "Couldn't open new"
    assert env.notes[-1][1]["details"] == "RuntimeError: ui gone"


# ---------------------------------------------------------------- rename dialog / action

def test_rename_config_guards_and_dialog(env, monkeypatch):
    cs.rename_config(None, FakeSelect(None), None)
    cs._disk_baselines["d"] = {"a": 1}
    cs.form_data_store["d"] = {"a": 2}
    cs.rename_config(None, FakeSelect("d"), None)
    assert env.titles() == ["Choose a template first", "Save this template before renaming"]
    actions = []
    monkeypatch.setattr(cs, "rename_config_action", lambda *a: actions.append(a))
    sel = FakeSelect("chat")

    def build():
        ui.button("go", on_click=lambda: cs.rename_config("parser", sel, "container"))

    async def check(user):
        user.find("go").click()
        await user.should_see("Rename Template")
        inp = next(e for e in everything(user) if isinstance(e, ui.input) and e.value == "chat")
        inp.value = "talk"
        fire(user, button(user, "Rename"))
        assert actions[0][:2] == ("chat", "talk")
        fire(user, button(user, "Cancel"))

    run_ui(build, check)


def test_rename_action_paths(env, monkeypatch):
    cs._disk_baselines["d"] = {"a": 1}
    cs.form_data_store["d"] = {"a": 2}
    cs.rename_config_action("d", "x", None, None, None, Dlg())
    assert env.titles() == ["Save this template before renaming"]

    def raiser(exc):
        def f(*a):
            raise exc
        return f

    monkeypatch.setattr(cs, "_rename_template_files", raiser(SporeStudioError("Choose a different name.")))
    cs.rename_config_action("a", "a", None, None, None, Dlg())
    assert env.notes[-1][0] == "Choose a different name." and env.titles()[-1] == "Choose another name"
    tech = SporeStudioError("[Errno 13] denied")
    monkeypatch.setattr(cs, "_rename_template_files", raiser(tech))
    cs.rename_config_action("a", "b", None, None, None, Dlg())
    assert env.titles()[-1] == "Couldn't rename a" and env.notes[-1][1]["details"] == "SporeStudioError: [Errno 13] denied"
    monkeypatch.setattr(cs, "_rename_template_files", raiser(RuntimeError("bug")))
    cs.rename_config_action("a", "b", None, None, None, Dlg())
    assert env.notes[-1][1]["details"] == "RuntimeError: bug"


def test_rename_action_success(env, monkeypatch):
    monkeypatch.setattr(cs, "_rename_template_files", lambda s, r, p: "b")
    shown = []
    monkeypatch.setattr(cs, "_show_duplicated_config", lambda *a, **k: shown.append((a[0], k)))
    monkeypatch.setattr(cs, "_active_config_name", "a")
    cs.form_data_store["a"] = {}
    cs._disk_baselines["a"] = {}
    dlg = Dlg()
    cs.rename_config_action("a", "b", None, None, None, dlg)
    assert cs._active_config_name is None and "a" not in cs.form_data_store and "a" not in cs._disk_baselines
    assert env.titles() == ["Renamed a to b"] and dlg.closed == 1 and shown == [("b", {"verb": "Renamed"})]
    monkeypatch.setattr(cs, "_active_config_name", "other")
    cs.rename_config_action("a", "b", None, None, None, Dlg())
    assert cs._active_config_name == "other"


# ---------------------------------------------------------------- delete

def test_delete_template_files(env, real_parser):
    write_template(env, "t", html="h", sidecar={}, assets={"a.png": b"1", "s/b.mp3": b"2"})
    (env.templates / "template_configs" / "t.spore.json").write_text("{}")
    progress = []
    cs._delete_template_files("t", real_parser, lambda *a: progress.append(a))
    assert not (env.assets / "t").exists() and not (env.templates / "t.html").exists()
    assert not (env.templates / "template_configs" / "t.json").exists()
    assert not (env.templates / "_spore" / "t.spore.json").exists()
    assert not (env.templates / "template_configs" / "t.spore.json").exists()
    assert progress[:2] == [("preparing", 0, 7), ("deleting", 0, 7)] and progress[-1] == ("deleting", 7, 7)
    cs._delete_template_files("t", real_parser)  # nothing left: no error


def test_delete_template_files_errors(env, real_parser, monkeypatch):
    write_template(env, "t", assets={"a.png": b"1"})
    monkeypatch.setattr(cs.os, "remove", lambda p: (_ for _ in ()).throw(PermissionError("in use")))
    monkeypatch.setattr(cs.shutil, "rmtree", lambda p: (_ for _ in ()).throw(OSError("busy")))
    with pytest.raises(SporeStudioError, match="Could not fully delete 't': .*a.png: in use"):
        cs._delete_template_files("t", real_parser)


def test_delete_config_dialog(env, monkeypatch):
    cs.delete_config(None, FakeSelect(None), None)
    assert env.titles() == ["Choose a template first"]
    actions = []
    monkeypatch.setattr(cs, "delete_config_action", lambda *a: actions.append(a))
    sel = FakeSelect("chat")

    def build():
        ui.button("go", on_click=lambda: cs.delete_config("parser", sel, "container"))

    async def check(user):
        user.find("go").click()
        await user.should_see("Confirm Deletion")
        fire(user, button(user, "Delete"))
        assert actions[0][:4] == ("chat", "parser", sel, "container")
        fire(user, button(user, "Cancel"))

    run_ui(build, check)


def test_refresh_config_dropdown(env, monkeypatch):
    applied, empties = [], []
    monkeypatch.setattr(cs, "_apply_config_selection", lambda n, p, c: applied.append(n))
    monkeypatch.setattr(cs, "_show_no_configs", lambda c: empties.append(c))
    parser = FakeParser({"a": {}, "b": {}})
    sel = FakeSelect("b")
    cs._refresh_config_dropdown(parser, sel, "c")
    assert sel.options == ["a", "b"] and applied == [] and parser.invalidations == 1
    cs._refresh_config_dropdown(parser, sel, "c", skip="b")
    assert sel.options == ["a"] and sel.value == "a" and applied == ["a"]
    monkeypatch.setattr(cs, "_active_config_name", "a")
    cs._refresh_config_dropdown(FakeParser({"a": {}}), sel, "c", skip="a")
    assert sel.value is None and cs._active_config_name is None and empties == ["c"] and env.flushes == [1]


def test_finish_template_delete(env, monkeypatch, routes):
    refreshes = []
    monkeypatch.setattr(cs, "_refresh_config_dropdown", lambda p, s, c: refreshes.append(1))
    tcp.set_template_copy_state(active=True)
    cs._finish_template_delete(None, "t", None, None, None)
    assert tcp.template_copy_is_active() is False and env.titles() == ["Deleted t"] and refreshes == [1]
    cs._finish_template_delete(SporeStudioError("partial"), "t", None, None, None)
    assert env.titles()[-1] == "Couldn't fully delete t" and env.notes[-1][1]["details"] == "SporeStudioError: partial"
    monkeypatch.setattr(cs, "_refresh_config_dropdown", lambda p, s, c: (_ for _ in ()).throw(RuntimeError("ui")))
    cs._finish_template_delete(RuntimeError("bug"), "t", None, None, None)
    assert env.notes[-1][1]["details"] == "RuntimeError: bug"
    assert len(routes) == 3


def test_delete_action_busy(env):
    tcp.set_template_copy_state(active=True)
    dlg = Dlg()
    cs.delete_config_action("t", None, None, None, dlg)
    assert dlg.closed == 0 and env.titles() == ["A template copy or delete is already running"]


def test_delete_action_success(env, real_parser, monkeypatch, routes):
    write_template(env, "t", html="h", assets={"a.png": b"1"})
    write_template(env, "u")
    monkeypatch.setattr(cs, "_apply_config_selection", lambda n, p, c: None)
    monkeypatch.setattr(cs, "_active_config_name", "t")
    cs.form_data_store["t"] = {}
    cs._disk_baselines["t"] = {}
    states = []
    real_set = cs._set_template_copy_state
    monkeypatch.setattr(cs, "_set_template_copy_state", lambda **kw: states.append(kw) or real_set(**kw))
    sel = FakeSelect("t")
    dlg = Dlg()
    cs.delete_config_action("t", real_parser, sel, None, dlg)
    assert dlg.closed == 1 and env.threads == ["TemplateDelete"]
    assert cs._active_config_name is None and "t" not in cs.form_data_store
    assert sel.value == "u" and sel.options == ["u"]
    assert not (env.assets / "t").exists() and not (env.templates / "t.html").exists()
    assert states[0]["title"] == "Deleting" and states[0]["phase"] == "preparing"
    assert states[-1]["phase"] == "deleting" and states[-1]["copied"] == states[-1]["total"]
    assert env.titles() == ["Deleted t"] and tcp.template_copy_is_active() is False


def test_delete_action_keeps_other_active_and_reports_errors(env, monkeypatch):
    monkeypatch.setattr(cs, "_refresh_config_dropdown", lambda *a, **k: None)
    monkeypatch.setattr(cs, "_active_config_name", "other")
    finished = []
    monkeypatch.setattr(cs, "_finish_template_delete", lambda err, *a: finished.append(err))
    monkeypatch.setattr(cs, "_delete_template_files", lambda *a: (_ for _ in ()).throw(SporeStudioError("x")))
    cs.delete_config_action("t", None, None, None, Dlg())
    assert cs._active_config_name == "other" and isinstance(finished[0], SporeStudioError)


def test_delete_action_result_lost(env, monkeypatch):
    monkeypatch.setattr(cs, "_refresh_config_dropdown", lambda *a, **k: None)
    monkeypatch.setattr(cs, "_delete_template_files", lambda *a: None)
    monkeypatch.setattr(cs, "run_on_ui_loop", lambda fn: (_ for _ in ()).throw(RuntimeError("loop gone")))
    cs.delete_config_action("t", None, None, None, Dlg())
    assert tcp.template_copy_is_active() is False
    assert env.titles() == ["The template delete finished, but the screen didn't update"]


def test_notify_helpers(env):
    cs._notify_save_first("renaming")
    cs._notify_copy_busy()
    cs._notify_result_lost("copy")
    cs._notify_rename_failed("t", OSError("x"))
    assert env.titles() == ["Save this template before renaming", "A template copy or delete is already running",
                            "The template copy finished, but the screen didn't update", "Couldn't rename t"]
    assert env.notes[-1][1]["details"] == "OSError: x"
