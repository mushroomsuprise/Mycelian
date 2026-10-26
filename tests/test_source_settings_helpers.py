# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Source Settings tab: sections, search, "Changed only", units, steps and file fields (no UI)."""

import glob
import json
import os

import pytest

from modules.uiwindows import customsources as cs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sep(label):
    return {"type": "separator", "label": label}


def _el(element_id, type_="text", **extra):
    return {"id": element_id, "type": type_, "label": extra.pop("label", element_id), "value": extra.pop("value", ""), **extra}


# --- sections -----------------------------------------------------------------


def test_settings_before_first_separator_are_general_and_keep_file_order():
    elements = [_el("A"), _el("B"), _sep("Colors"), _el("C"), _sep("Sizes"), _el("D")]
    sections = cs.build_sections("alerts", elements)
    assert [s.title for s in sections] == ["General", "Colors", "Sizes"]
    assert [[e["id"] for e in s.elements] for s in sections] == [["A", "B"], ["C"], ["D"]]
    assert len({s.key for s in sections}) == 3


def test_empty_separators_are_dropped_and_repeated_labels_share_a_section():
    elements = [_sep("Empty"), _sep("Text"), _el("A"), _sep("Other"), _el("B"), _sep("Text"), _el("C"), "junk"]
    sections = cs.build_sections("chat", elements)
    assert [s.title for s in sections] == ["Text", "Other"]
    assert [e["id"] for e in sections[0].elements] == ["A", "C"]


def test_roulette_options_get_one_section_each_with_their_name():
    elements = [
        _sep("Wheel"),
        _el("WheelSize", "number", value=500),
        _sep("Roulette Options Configuration"),
        _sep("Option 2"),
        _el("Option2Enabled", "checkbox", value=True),
        _el("Option2Name", value="Blue"),
        _sep("Option 10"),
        _el("Option10Name", value=""),
    ]
    sections = cs.build_sections("roulette", elements, {"Option2Name": "Red"})
    assert [s.title for s in sections] == ["Wheel", "Option 2 (Red)", "Option 10"]
    assert sections[1].option_num == 2
    assert [e["id"] for e in sections[1].elements] == ["Option2Enabled", "Option2Name"]
    # Other templates keep Option* settings under their separators.
    plain = cs.build_sections("alerts", elements)
    assert [s.title for s in plain] == ["Wheel", "Option 2", "Option 10"]


def test_roulette_option_helpers():
    assert cs.roulette_option_number("Option12TextColor") == 12
    assert cs.roulette_option_number("Options") is None
    assert cs.roulette_option_title(3, "Spin again") == "Option 3 (Spin again)"
    assert cs.roulette_option_title(3, "") == "Option 3"


def test_every_shipped_config_groups_without_losing_settings():
    for path in glob.glob(os.path.join(ROOT, "templates", "template_configs", "*.json")):
        name = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as fh:
            config = json.load(fh)
        elements = [e for e in config.get("elements", []) if isinstance(e, dict)]
        sections = cs.build_sections(name, elements)
        grouped = sum(len(s.elements) for s in sections)
        assert grouped == sum(1 for e in elements if e.get("type") != "separator"), name


# --- search and "Changed only" -------------------------------------------------


def test_search_matches_label_and_description_and_keeps_sections():
    elements = [
        _sep("Font"),
        _el("FontFile", label="Font File", description="Font file name"),
        _el("FontColor", "color", label="Font Color"),
        _sep("Background"),
        _el("BgColor", "color", label="Background Color", description="Colour behind the text"),
        _el("BgImage", label="Background Image"),
    ]
    sections = cs.build_sections("x", elements)
    result = cs.filter_sections(sections, "  TEXT ")
    assert [(s.title, [e["id"] for e in kept]) for s, kept in result] == [("Background", ["BgColor"])]
    result = cs.filter_sections(sections, "font")
    assert [(s.title, [e["id"] for e in kept]) for s, kept in result] == [("Font", ["FontFile", "FontColor"])]
    assert cs.filter_sections(sections, "nothing like this") == []
    assert len(cs.filter_sections(sections, "")) == 2


def test_changed_only_filter_and_changed_ids():
    baseline = {"A": 700, "B": True, "C": "x", "D": ""}
    current = {"A": 700.0, "B": "true", "C": "y", "D": None}
    changed = cs.changed_setting_ids(baseline, current)
    assert changed == {"C"}
    sections = cs.build_sections("x", [_el("A"), _sep("S"), _el("B"), _el("C", label="Colour")])
    result = cs.filter_sections(sections, "", changed_only=True, changed_ids=changed)
    assert [(s.title, [e["id"] for e in kept]) for s, kept in result] == [("S", ["C"])]
    assert cs.filter_sections(sections, "colour", changed_only=True, changed_ids={"A"}) == []
    assert cs.changed_setting_ids(None, {"A": 1}) == {"A"}


# --- units, steps, zoom, chips ------------------------------------------------


@pytest.mark.parametrize(
    "label, description, unit",
    [
        ("Alert Width", "Width of the alert (px)", "px"),
        ("Padding (pixels)", "", "px"),
        ("Fade time (ms)", "", "ms"),
        ("Delay (seconds)", "", "s"),
        ("Hold", "How long to hold (s)", "s"),
        ("Rotation (deg)", "", "deg"),
        ("Opacity (%)", "", "%"),
        ("Opacity (0–1)", "0 = transparent", ""),
        ("Gift subs", "gifted {gift_qty} sub(s)!", ""),
        ("Size", "Size of the wheel in pixels", ""),
    ],
)
def test_setting_unit(label, description, unit):
    assert cs.setting_unit({"label": label, "description": description}) == unit


def test_number_step_honours_json_step():
    assert cs.number_step({"step": 0.1}) == 0.1
    assert cs.number_step({"step": 1}) == 1
    assert isinstance(cs.number_step({"step": 5.0}), int)
    assert cs.number_step({"step": "0.25"}) == 0.25
    for bad in ({}, {"step": None}, {"step": 0}, {"step": -1}, {"step": "x"}, {"step": True}):
        assert cs.number_step(bad) is None


def test_zoom_steps_are_clamped():
    assert cs.next_preview_zoom(100, 1) == 110
    assert cs.next_preview_zoom(100, -1) == 90
    assert cs.next_preview_zoom(101, -1) == 100
    assert cs.next_preview_zoom(200, 1) == 200
    assert cs.next_preview_zoom(25, -1) == 25
    assert cs.next_preview_zoom("x", 1) == 110


def test_preview_live_state():
    kw = dict(server_running=True, has_template=True, dirty=False)
    assert cs.preview_live_state("chat", **kw) == ("live", "Live")
    assert cs.preview_live_state("chat", **{**kw, "dirty": True}) == ("live", "Live · shows unsaved changes")
    assert cs.preview_live_state("chat", **{**kw, "server_running": False})[0] == "off"
    assert cs.preview_live_state("chat", **{**kw, "has_template": False})[1] == "No preview for this template"
    assert cs.preview_live_state("", **kw) == ("off", "No template")


def test_preview_size_chip():
    assert "From OBS: Chat" in cs.preview_size_chip_html("obs", "Chat")
    assert "Saved size" in cs.preview_size_chip_html("stored")
    assert cs.preview_size_chip_html("estimate") == ""


def test_estimate_reads_spore_design_size():
    assert cs._estimate_design_size({"DesignWidth": 460, "DesignHeight": 480}) == (460, 480)
    assert cs._estimate_design_size({"AlertWidth": 700, "AlertHeight": 200}) == (700, 200)
    assert cs._estimate_design_size({}) == (1920, 1080)


# --- file settings ------------------------------------------------------------


@pytest.mark.parametrize(
    "element, kind, style",
    [
        (_el("X", "file", accept="audio/*"), "audio", "web"),
        (_el("Gif", value="/assets/follow/a.gif", label="GIF Path"), "image", "web"),
        (_el("Snd", value="", label="Death Sound File", description="Path to the death sound file"), "audio", "web"),
        (_el("BackgroundImageURL", label="Background Image URL", description="URL or path to background image"), "image", "web"),
        (_el("BannerBGImage", label="Alert Banner Background Image", description="Used only for alert banners."), "image", "web"),
        (_el("Video", label="Video Source", value="/assets/boo game/boo.mp4"), "video", "web"),
        (_el("Art", label="Default Album Art Image", value="assets/spotify/NoAlbumArt.jpeg"), "image", "assets"),
        (_el("FontName", label="Font File", value="Renogare.ttf", description="Font file name (must be in /assets/default_assets/fonts/ directory)"), "font", "filename"),
        (_el("keyword_font_family", label="keyword font family", value="Roboto-Bold.ttf"), "font", "filename"),
        (_el("FontName", label="Font File", value="default_assets/fonts/Renogare.ttf"), "font", "relative"),
        (_el("ClickSoundFile", label="Click Sound File", value="click.mp3"), "audio", "filename"),
    ],
)
def test_asset_spec_detects_file_settings(element, kind, style):
    spec = cs._asset_browse_spec(element, element.get("value"), "roulette")
    assert spec is not None
    assert (spec.kind, spec.style) == (kind, style)


@pytest.mark.parametrize(
    "element",
    [
        _el("FontColor", "color", label="Font Color", value="#fff"),
        _el("TitleText", label="Title Text", value="Hello"),
        _el("Username", label="Username", value="zack"),
        _el("AvatarShadow", label="Avatar Shadow", value="0 4px 8px rgba(0,0,0,0.3)", description="CSS box shadow for avatar"),
        _el("IconFilter", label="Icon Filter", value="none"),
        _el("BossImageExtension", label="Boss Image Extension", value="png", description="File extension under /assets/raids/"),
        _el("FontFamily", label="Font Family", value="Renogare", description="Font family for all text"),
        _el("MusicTracks", label="Music Tracks", value="", description="Music files. Enter one filename, or several separated by commas."),
        _el("Size", "number", label="Image Size", value=10),
        _el("Pick", "select", label="Image", options=["a.png"], value="a.png"),
    ],
)
def test_asset_spec_leaves_ordinary_settings_alone(element):
    assert cs._asset_browse_spec(element, element.get("value"), "x") is None


def test_asset_spec_folders_and_base_dirs():
    folder = cs._asset_browse_spec(_el("RandomAudioFolder", label="Random Audio Folder", description="Path to folder"), "", "follow")
    assert (folder.kind, folder.mode) == ("audio", "folder")
    sounds = cs._asset_browse_spec(
        _el("UpdateSoundFile", label="Update Sound File", description="Sound file name (must be in /assets/sounds/ directory)"),
        "",
        "memecalc",
    )
    assert (sounds.style, sounds.base_dir) == ("filename", "/assets/sounds/")
    click = cs._asset_browse_spec(_el("ClickSoundFile", label="Click Sound File", value="click.mp3"), "click.mp3", "roulette")
    assert click.base_dir == "/assets/roulette/"
    font = cs._asset_browse_spec(_el("Font", label="Font File", value="R.ttf", description="from default_assets/fonts/"), "R.ttf", "follow")
    assert font.base_dir == cs.DEFAULT_FONTS_WEB_DIR


def test_asset_paths_round_trip():
    web = cs.AssetSpec(kind="image", extensions=(".png",))
    assert cs.asset_value_from_pick(web, "/assets/a/b.png") == "/assets/a/b.png"
    assert cs.asset_value_to_web_path(web, "/assets/a/b.png") == "/assets/a/b.png"
    rel = cs.AssetSpec(kind="font", extensions=(".ttf",), style="relative")
    assert cs.asset_value_from_pick(rel, "/assets/default_assets/fonts/X.ttf") == "default_assets/fonts/X.ttf"
    assert cs.asset_value_to_web_path(rel, "default_assets/fonts/X.ttf") == "/assets/default_assets/fonts/X.ttf"
    assets = cs.AssetSpec(kind="image", extensions=(".png",), style="assets")
    assert cs.asset_value_from_pick(assets, "/assets/spotify/a.png") == "assets/spotify/a.png"
    name = cs.AssetSpec(kind="audio", extensions=(".mp3",), style="filename", base_dir="/assets/roulette/")
    assert cs.asset_value_from_pick(name, "/assets/roulette/sub/click.mp3") == "sub/click.mp3"
    assert cs.asset_value_to_web_path(name, "click.mp3") == "/assets/roulette/click.mp3"
    assert cs.asset_value_to_web_path(name, "") == "/assets/roulette/"


def test_gradient_detection():
    assert cs.is_gradient_value("linear-gradient(90deg, #000, #fff)")
    assert not cs.is_gradient_value("#000")
