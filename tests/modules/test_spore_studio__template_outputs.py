# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/template_outputs.py (template output discovery)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import path_utils
from modules.spore_studio import data_feed_registry, template_parser_back
from modules.spore_studio import template_outputs as to


@pytest.fixture
def tdir(tmp_path, monkeypatch):
    """A temp templates folder with the scan cache cleared before and after."""
    root = tmp_path / "templates"
    (root / "template_configs").mkdir(parents=True)
    monkeypatch.setattr(to, "_templates_dir", lambda: str(root))
    monkeypatch.setattr(to, "sidecar_widget_outputs", lambda name: [])
    to.invalidate_cache()
    yield root
    to.invalidate_cache()


def _write(root, name, html, config=None):
    (root / f"{name}.html").write_text(html, encoding="utf-8")
    if config is not None:
        (root / "template_configs" / f"{name}.json").write_text(json.dumps(config), encoding="utf-8")


# ---------------------------------------------------------------- is_relayable_event

@pytest.mark.parametrize("name", ["roulette_result", "boo-game", "A1", "x" * 64])
def test_relayable_good_names(name):
    assert to.is_relayable_event(name) is True


@pytest.mark.parametrize(
    "name",
    [None, 5, "", "has space", "x" * 65, "alert_complete", "connect", "get_data",
     "set-x", "update_thing", "streamdeck_press", "mycelian_x", "activity_feed_y",
     "spotify_oauth_done", "foo_request", "foo_request_state", "foo-request"],
)
def test_relayable_rejects(name):
    assert to.is_relayable_event(name) is False


# ---------------------------------------------------------------- scan_html_emits

def test_scan_html_emits_dedupes_filters_and_strips_runtime():
    html = (
        "socket.emit('roulette_result', {}); socket.emit(\"roulette_result\");"
        "socket.emit( 'alert_complete' ); socket.emit('a\" );"
        "// SPORE_STUDIO:data-runtime-begin\nsocket.emit('wheel_result')\n"
        "// SPORE_STUDIO:data-runtime-end\n"
        "socket.emit(\"boo_score\", 1)"
    )
    assert to.scan_html_emits(html) == ["roulette_result", "boo_score"]


def test_scan_html_emits_none():
    assert to.scan_html_emits(None) == []
    assert to.scan_html_emits("") == []


# ---------------------------------------------------------------- paths / json

def test_templates_dir_and_config_path(monkeypatch, tmp_path):
    monkeypatch.setattr(path_utils, "get_template_path", lambda rel="": str(tmp_path / "tpl"))
    assert to._templates_dir() == str(tmp_path / "tpl")
    assert to._config_path("x") == os.path.join(str(tmp_path / "tpl"), "template_configs", "x.json")


def test_read_json_variants(tmp_path):
    good = tmp_path / "g.json"
    good.write_text('{"a": 1}', encoding="utf-8")
    lst = tmp_path / "l.json"
    lst.write_text("[1]", encoding="utf-8")
    bad = tmp_path / "b.json"
    bad.write_text("{", encoding="utf-8")
    assert to._read_json(str(good)) == {"a": 1}
    assert to._read_json(str(lst)) == {}
    assert to._read_json(str(bad)) == {}
    assert to._read_json(str(tmp_path / "missing.json")) == {}


def test_humanize():
    assert to._humanize("roulette_result") == "Roulette result"
    assert to._humanize("--boo--game_") == "Boo game"


def test_normalize_payload():
    assert to._normalize_payload("x") == []
    rows = to._normalize_payload(["name", {"key": "n", "type": "number", "label": "N"},
                                  {"label": "no key"}, 7, {"key": 3}])
    assert rows == [
        {"key": "name", "label": "name", "type": "string"},
        {"key": "n", "label": "N", "type": "number"},
        {"key": "3", "label": "3", "type": "string"},
    ]


# ---------------------------------------------------------------- declared_outputs

def test_declared_outputs_dict_block():
    cfg = {"outputs": {
        "spin": {"event": "wheel_result", "label": "Spin", "description": "d",
                 "payload": ["winner"], "match": {"element_id": "w1"},
                 "element_id": "w1", "element_type": "wheel", "generated": 1},
        "bad_event": {"event": "has space"},
        "no_event": {"label": "x"},
        "not_dict": "x",
    }}
    rows = to.declared_outputs("wheel", cfg)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "wheel:spin"
    assert row["output_id"] == "spin"
    assert row["payload"] == [{"key": "winner", "label": "winner", "type": "string"}]
    assert row["match"] == {"element_id": "w1"}
    assert row["generated"] is True and row["origin"] == "declared"


def test_declared_outputs_list_block_and_defaults():
    cfg = {"outputs": [
        {"event": "boo_score", "match": "not-a-dict"},
        {"id": "custom", "event": "x_done"},
        "skip",
    ]}
    rows = to.declared_outputs("t", cfg)
    assert [r["id"] for r in rows] == ["t:boo_score", "t:custom"]
    assert rows[0]["label"] == "Boo score"
    assert rows[0]["match"] == {}
    assert rows[0]["generated"] is False


def test_declared_outputs_other_and_reads_config(tdir):
    assert to.declared_outputs("t", {"outputs": 5}) == []
    assert to.declared_outputs("t", {}) == []
    _write(tdir, "disk", "", {"outputs": {"o": {"event": "disk_evt"}}})
    assert [r["event"] for r in to.declared_outputs("disk")] == ["disk_evt"]


def test_row_from_built():
    row = to._row_from_built("t", "w1_result", {"event": "wheel_result", "payload": ["a"],
                                                "match": {"element_id": "w1"}}, "sidecar")
    assert row["id"] == "t:w1_result"
    assert row["label"] == "Wheel result"
    assert row["generated"] is True and row["origin"] == "sidecar"
    row2 = to._row_from_built("t", "oid", {"match": [1]}, "x")
    assert row2["label"] == "Oid" and row2["match"] == {} and row2["event"] == ""


# ---------------------------------------------------------------- sidecar_widget_outputs

def test_sidecar_widget_outputs_no_sidecar(monkeypatch):
    monkeypatch.setattr(template_parser_back, "load_sidecar", lambda name: None)
    assert to.sidecar_widget_outputs("t") == []


def test_sidecar_widget_outputs_builds_rows(monkeypatch):
    seen = {}

    def fake_build(elements, name):
        seen["args"] = (elements, name)
        return {"w_result": {"event": "wheel_result", "label": "Spin"}, "junk": "x"}

    monkeypatch.setattr(template_parser_back, "load_sidecar",
                        lambda name: {"elements": [{"id": "w", "type": "wheel"}]})
    monkeypatch.setattr(data_feed_registry, "build_widget_outputs", fake_build)
    rows = to.sidecar_widget_outputs("tpl")
    assert seen["args"] == ([{"id": "w", "type": "wheel"}], "tpl")
    assert len(rows) == 1 and rows[0]["origin"] == "sidecar" and rows[0]["label"] == "Spin"


# ---------------------------------------------------------------- scanning

def test_scan_templates_missing_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(to, "_templates_dir", lambda: str(tmp_path / "nope"))
    assert to._scan_templates() == []


def test_scan_templates_combines_sources(tdir, monkeypatch):
    _write(tdir, "wheel", "socket.emit('wheel_result'); socket.emit('spin_started'); "
                          "socket.emit('declared_evt')",
           {"outputs": {"d": {"event": "declared_evt"}}})
    _write(tdir, "plain", "socket.emit('plain_evt')")
    (tdir / "notes.txt").write_text("socket.emit('ignored')")
    monkeypatch.setattr(
        to, "sidecar_widget_outputs",
        lambda name: [
            to._row_from_built(name, "d", {"event": "other"}, "sidecar"),       # dup id
            to._row_from_built(name, "w2", {"event": "declared_evt"}, "sidecar"),  # dup event
            to._row_from_built(name, "w3", {"event": "sidecar_evt"}, "sidecar"),
        ] if name == "wheel" else [],
    )
    rows = to._scan_templates()
    pairs = [(r["template"], r["event"], r["origin"]) for r in rows]
    assert pairs == [
        ("plain", "plain_evt", "scanned"),
        ("wheel", "declared_evt", "declared"),
        ("wheel", "sidecar_evt", "sidecar"),
        ("wheel", "spin_started", "scanned"),
    ]
    scanned = rows[0]
    assert scanned["description"] == "Emitted by the plain template."


def test_scan_templates_unreadable_file_skipped(tdir, monkeypatch):
    _write(tdir, "a", "socket.emit('a_evt')")
    _write(tdir, "b", "socket.emit('b_evt')")
    real_open = open

    def fake_open(path, *a, **k):
        if str(path).endswith("a.html"):
            raise OSError("locked")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    assert [r["event"] for r in to._scan_templates()] == ["b_evt"]


def test_signature_lists_html_and_json(tdir, monkeypatch):
    _write(tdir, "a", "", {"x": 1})
    (tdir / "skip.txt").write_text("")
    sig = to._signature()
    assert sig[0] == str(tdir)
    assert [p[0] for p in sig[1]] == ["/a.html", "template_configs/a.json"]


def test_signature_missing_dirs_and_mtime_error(tmp_path, monkeypatch):
    monkeypatch.setattr(to, "_templates_dir", lambda: str(tmp_path / "none"))
    assert to._signature() == (str(tmp_path / "none"), ())
    root = tmp_path / "t"
    root.mkdir()
    (root / "a.html").write_text("")
    monkeypatch.setattr(to, "_templates_dir", lambda: str(root))

    def boom(_p):
        raise OSError

    monkeypatch.setattr(to.os.path, "getmtime", boom)
    assert to._signature() == (str(root), ())


def test_list_template_outputs_caches_and_refreshes(tdir, monkeypatch):
    _write(tdir, "a", "socket.emit('a_evt')")
    calls = []
    real = to._scan_templates
    monkeypatch.setattr(to, "_scan_templates", lambda: calls.append(1) or real())
    first = to.list_template_outputs()
    second = to.list_template_outputs()
    assert first == second and len(calls) == 1
    first[0]["event"] = "mutated"
    assert to.list_template_outputs()[0]["event"] == "a_evt"
    to.list_template_outputs(refresh=True)
    assert len(calls) == 2
    to.invalidate_cache()
    to.list_template_outputs()
    assert len(calls) == 3


def test_list_template_outputs_scan_failure(tdir, monkeypatch, caplog):
    def boom():
        raise RuntimeError("bad")

    monkeypatch.setattr(to, "_scan_templates", boom)
    assert to.list_template_outputs(refresh=True) == []
    assert "Template output scan failed" in caplog.text


def _rows(*specs):
    out = []
    for tpl, event, payload in specs:
        out.append({"template": tpl, "event": event, "label": to._humanize(event),
                    "description": "d", "payload": payload, "match": {}})
    return out


def test_known_output_events(monkeypatch):
    monkeypatch.setattr(to, "list_template_outputs",
                        lambda refresh=False: _rows(("a", "e", []), ("b", "e", []),
                                                    ("a", "e", []), ("c", "f", [])))
    assert to.known_output_events() == {"e": ["a", "b"], "f": ["c"]}


def test_merge_into_events(monkeypatch):
    monkeypatch.setattr(to, "list_template_outputs", lambda refresh=False: _rows(
        ("a", "next_alert", [{"key": "x", "label": "x", "type": "string"}]),
        ("w1", "wheel_result", [{"key": "winner", "label": "W", "type": "string"}]),
        ("w2", "wheel_result", [{"key": "winner", "label": "W", "type": "string"},
                                {"key": "index", "label": "I", "type": "number"}]),
        ("w1", "wheel_result", []),
    ))
    core = [{"event": "next_alert", "payload": []}]
    merged = to.merge_into_events(core)
    assert merged[0] is core[0] and core[0]["payload"] == []  # curated core event wins
    assert len(core) == 1  # input list not mutated
    wheel = merged[1]
    assert wheel["origin"] == "template"
    assert wheel["label"] == "Wheel result (w1)"
    assert wheel["templates"] == ["w1", "w2"]
    assert [p["key"] for p in wheel["payload"]] == ["winner", "index"]


def test_resolve_emitter(monkeypatch):
    monkeypatch.setattr(to, "known_output_events", lambda: {"one": ["a"], "two": ["a", "b"]})
    assert to.resolve_emitter("two", {"template": "  given "}) == "given"
    assert to.resolve_emitter("one", {"template": "  "}) == "a"
    assert to.resolve_emitter("one", "notdict") == "a"
    assert to.resolve_emitter("two", None) == ""
    assert to.resolve_emitter("zzz", {}) == ""


def test_sample_envelope(monkeypatch):
    rows = _rows(("w", "wheel_result", [
        {"key": "s", "type": "string"}, {"key": "n", "type": "number"},
        {"key": "b", "type": "boolean"}, {"key": "a", "type": "array"},
        {"key": "o", "type": "odd"}, {"key": "d"}]))
    rows[0]["match"] = {"element_id": "w1"}
    monkeypatch.setattr(to, "list_template_outputs", lambda refresh=False: _rows(("x", "other", [])) + rows)
    name, env = to.sample_envelope("wheel_result")
    assert name == "template_output"
    assert env["template"] == "w" and env["event"] == "wheel_result"
    assert env["data"] == {"s": "sample", "n": 1, "b": True, "a": ["one", "two"],
                           "o": "sample", "d": "sample", "element_id": "w1", "template": "w"}
    assert to.sample_envelope("missing") is None
