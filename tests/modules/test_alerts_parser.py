# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/alerts_parser.py: loading, parsing, filtering, export and stats."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import alerts_parser as ap  # noqa: E402


SAMPLE = {
    "ToolData": {
        "alerts": {
            "bit_alerts": {
                "b1": {"alerttype": "bits", "audioname": "a.mp3", "audiodirectory": "dir",
                       "duration": "5", "volume": 50, "randomized": "True",
                       "randomizedchance": "10", "randomizeddirectory": "rd",
                       "randomizedextrachance": "None", "randomizedextradirectory": "N/A",
                       "gifname": "g.gif", "gifdirectory": "gd", "alertname": "Bits!"},
            },
            "point_alerts": {
                "p1": {"alerttype": "points", "audioname": "None", "twitchrewardid": "rw1",
                       "disabled": True},
                "p2": {"alerttype": "points", "twitchrewardid": "None", "gifname": "None"},
            },
            "follow_alerts": {"f1": {"alerttype": "follow", "randomized": "false"}},
            "unknown_alerts": {"x": {"alerttype": "nope"}},
        }
    }
}


def _write(tmp_path, data, name="alerts.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return p


@pytest.fixture
def parser(tmp_path):
    p = ap.AlertsParser(str(_write(tmp_path, SAMPLE)))
    assert p.load_file() is True
    p.parse_alerts()
    return p


def test_defaults_to_alerts_json():
    p = ap.AlertsParser()
    assert p.file_path == "alerts.json"
    assert p._raw_data == {} and p._alerts_data == []


def test_load_file_missing(tmp_path):
    p = ap.AlertsParser(str(tmp_path / "nope.json"))
    assert p.load_file() is False


def test_load_file_override_path(tmp_path):
    good = _write(tmp_path, SAMPLE, "other.json")
    p = ap.AlertsParser(str(tmp_path / "missing.json"))
    assert p.load_file(str(good)) is True
    assert p.file_path == str(good)
    assert "ToolData" in p._raw_data


def test_load_file_bad_json(tmp_path):
    p = ap.AlertsParser(str(_write(tmp_path, "{not json")))
    assert p.load_file() is False


def test_load_file_other_error(tmp_path, monkeypatch):
    f = _write(tmp_path, SAMPLE)

    def boom(*a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr("builtins.open", boom)
    assert ap.AlertsParser(str(f)).load_file() is False


def test_parse_without_load_returns_empty():
    assert ap.AlertsParser().parse_alerts() == []


def test_parse_bad_structure_returns_empty(tmp_path):
    p = ap.AlertsParser(str(_write(tmp_path, {"ToolData": ["not", "a", "dict"]})))
    assert p.load_file()
    assert p.parse_alerts() == []


def test_parse_missing_alerts_section(tmp_path):
    p = ap.AlertsParser(str(_write(tmp_path, {"Other": 1})))
    assert p.load_file()
    assert p.parse_alerts() == []


def test_parse_alerts_fields_and_none_cleaning(parser):
    alerts = parser._alerts_data
    ids = {a.alert_id for a in alerts}
    assert ids == {"b1", "p1", "p2", "f1"}  # unknown category ignored
    b1 = parser.get_alert_by_id("b1")
    assert b1.alerttype == "bits" and b1.alert_category == "bit_alerts"
    assert b1.audioname == "a.mp3" and b1.volume == 50 and b1.duration == "5"
    assert b1.randomizedextrachance is None and b1.randomizedextradirectory is None
    assert b1.alertname == "Bits!"
    p1 = parser.get_alert_by_id("p1")
    assert p1.audioname is None and p1.disabled is True and p1.twitchrewardid == "rw1"
    f1 = parser.get_alert_by_id("f1")
    assert f1.duration == "0" and f1.volume == 0 and f1.randomized == "false"
    assert f1.audioname == "" and f1.alertname is None


def test_parse_category_skips_bad_entries(parser, monkeypatch):
    parser._alerts_data = []
    parser._parse_alert_category({"ok": {"alerttype": "a"}, "bad": "not a dict"}, "bit_alerts")
    # "bad" -> extract returns None (AttributeError caught inside); only "ok" kept
    assert [a.alert_id for a in parser._alerts_data] == ["ok"]


def test_parse_category_logs_and_continues_on_exception(parser, monkeypatch):
    parser._alerts_data = []
    calls = []

    def fake_extract(cfg, aid, cat):
        calls.append(aid)
        if aid == "boom":
            raise RuntimeError("x")
        return ap.AlertData(alerttype="t", audioname="", audiodirectory="", duration=0, volume=0,
                            randomized="False", randomizedchance=None, randomizeddirectory=None,
                            randomizedextrachance=None, randomizedextradirectory=None,
                            twitchrewardid=None, gifname=None, gifdirectory=None, alert_id=aid)

    monkeypatch.setattr(parser, "_extract_alert_variables", fake_extract)
    parser._parse_alert_category({"boom": {}, "fine": {}}, "c")
    assert calls == ["boom", "fine"]
    assert [a.alert_id for a in parser._alerts_data] == ["fine"]


def test_extract_returns_none_on_bad_config(parser):
    assert parser._extract_alert_variables(None, "x", "c") is None


def test_filters(parser):
    assert [a.alert_id for a in parser.get_alerts_by_type("points")] == ["p1", "p2"]
    assert parser.get_alerts_by_type("zzz") == []
    assert [a.alert_id for a in parser.get_alerts_by_category("bit_alerts")] == ["b1"]
    assert parser.get_alert_by_id("missing") is None
    assert [a.alert_id for a in parser.get_alerts_with_audio()] == ["b1"]
    assert [a.alert_id for a in parser.get_randomized_alerts()] == ["b1"]
    assert [a.alert_id for a in parser.get_twitch_reward_alerts()] == ["p1"]
    assert [a.alert_id for a in parser.get_alerts_with_gifs()] == ["b1"]


def test_filters_respect_literal_none_strings(parser):
    a = parser.get_alert_by_id("p2")
    a.audioname = "None"
    a.twitchrewardid = "None"
    a.gifname = "None"
    assert a not in parser.get_alerts_with_audio()
    assert a not in parser.get_twitch_reward_alerts()
    assert a not in parser.get_alerts_with_gifs()


def test_export_dict_and_dataclass(parser):
    alerts = parser.get_alerts_by_category("bit_alerts")
    out = parser.export_filtered_data(alerts)
    assert out == [{
        "alert_id": "b1", "alert_category": "bit_alerts", "alerttype": "bits", "alertname": "Bits!",
        "audioname": "a.mp3", "audiodirectory": "dir", "duration": "5", "volume": 50,
        "randomized": "True", "randomizedchance": "10", "randomizeddirectory": "rd",
        "randomizedextrachance": None, "randomizedextradirectory": None, "twitchrewardid": None,
        "gifname": "g.gif", "gifdirectory": "gd", "disabled": False,
    }]
    assert parser.export_filtered_data(alerts, format="dataclass") is alerts


def test_summary_stats(parser):
    parser.get_alert_by_id("p2").gifname = "None"
    stats = parser.get_summary_stats()
    assert stats == {
        "total_alerts": 4,
        "alerts_by_type": {"bits": 1, "points": 2, "follow": 1},
        "alerts_with_audio": 1,
        "alerts_with_gifs": 1,
        "randomized_alerts": 1,
        "twitch_reward_alerts": 1,
        "disabled_alerts": 1,
    }


def test_summary_stats_empty():
    stats = ap.AlertsParser().get_summary_stats()
    assert stats["total_alerts"] == 0 and stats["alerts_by_type"] == {}


def test_main_with_file(tmp_path, monkeypatch, capsys):
    _write(tmp_path, SAMPLE)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ap.logging, "basicConfig", lambda **k: None)
    ap.main()
    out = capsys.readouterr().out
    assert "Successfully parsed 4 alerts" in out
    assert "Total alerts: 4" in out
    assert "bits: 1" in out
    assert "Found 2 point alerts" in out
    assert "Found 1 Twitch reward alerts" in out
    assert "GIF: g.gif" in out
    assert "Twitch Reward ID: rw1" in out
    assert "1. bits - Bits!" in out


def test_main_without_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ap.logging, "basicConfig", lambda **k: None)
    ap.main()
    assert capsys.readouterr().out == ""


def test_runs_as_script(tmp_path, monkeypatch, capsys):
    import logging
    import runpy
    import warnings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(logging, "basicConfig", lambda **k: None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        runpy.run_module("modules.alerts_parser", run_name="__main__")
    assert capsys.readouterr().out == ""  # no alerts.json in tmp_path
