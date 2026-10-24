# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Connectors tab card helpers: relative times, When/If/Then summary, glyphs, stylesheet."""

from __future__ import annotations

import datetime
from types import SimpleNamespace as NS

from modules.connector_core import ActionType, ComparisonOperator, TriggerCondition, TriggerType
from modules.help_system.help_render import promote_color_importance, scope_selectors
from modules.uiwindows import connectors as cx


def test_relative_time():
    now = datetime.datetime(2026, 10, 24, 18, 0).timestamp()
    assert cx._relative_time(0, now) == ""
    assert cx._relative_time(now - 20, now) == "just now"
    assert cx._relative_time(now - 300, now) == "5m ago"
    assert cx._relative_time(now - 3 * 3600, now) == "3h ago"
    assert cx._relative_time(datetime.datetime(2026, 10, 23, 22, 0).timestamp(), now) == "Yesterday"
    assert cx._relative_time(datetime.datetime(2026, 10, 20, 9, 0).timestamp(), now) == "4 days ago"
    assert cx._relative_time(datetime.datetime(2026, 9, 3, 9, 0).timestamp(), now) == "Sep 3"
    assert cx._relative_time(datetime.datetime(2025, 9, 3, 9, 0).timestamp(), now) == "Sep 3, 2025"


def _connector(conditions=(), actions=None):
    actions = actions if actions is not None else [NS(action_type=ActionType.SEND_CHAT_MESSAGE, reply_targets=["twitch"])]
    return NS(trigger=NS(trigger_type=TriggerType.TWITCH_BITS, conditions=list(conditions)), actions=actions)


def test_flow_rows_and_limits():
    html = cx._connector_flow_html(_connector())
    assert ">When<" in html and ">Then<" in html and ">If<" not in html
    assert "Bits cheered" in html and "Send chat message → Twitch" in html

    conds = [TriggerCondition("amount", ComparisonOperator.GREATER_THAN, n) for n in (1, 2, 3)]
    acts = [NS(action_type=ActionType.DISPLAY_BANNER, permanent=False) for _ in range(5)]
    html = cx._connector_flow_html(_connector(conds, acts))
    assert ">If<" in html and "+1 more" in html and "+2 more" in html

    empty = cx._connector_flow_html(NS(trigger=None, actions=[]))
    assert "No trigger" in empty and "No actions" in empty


def test_flow_escapes_user_text():
    act = NS(action_type=ActionType.WRITE_FILE, file_path="<script>.log")
    assert "<script>" not in cx._connector_flow_html(_connector(actions=[act]))


def test_glyphs():
    assert 'class="cx-brand is-twitch"' in cx._trigger_glyph_html(TriggerType.TWITCH_RAID)
    assert 'class="cx-brand is-obs"' in cx._trigger_glyph_html(TriggerType.OBS_SCENE_CHANGED)
    assert ">timer<" in cx._trigger_glyph_html(TriggerType.TIMER)
    mic = NS(action_type=ActionType.AUDIO_CONTROL, control_type="microphone")
    assert ">mic<" in cx._action_glyph_html(mic)
    assert "is-discord" in cx._action_glyph_html(NS(action_type=ActionType.SEND_DISCORD_MESSAGE))


def test_connectors_stylesheet_is_well_formed():
    from modules.uiwindows.connectors_styles import CONNECTORS_CSS

    out = promote_color_importance(scope_selectors(CONNECTORS_CSS, ".cnx"))
    assert out.count("{") == out.count("}")
    assert ".cnx .cnx " not in out
    assert "body.cx-dragging .cnx .cx-root-drop" in out


def test_card_names_match_the_editor():
    assert cx.format_trigger_name(TriggerType.TWITCH_RAID) == "Raid"
    assert cx.format_trigger_name(TriggerType.DONATION) == "Tip / donation"
    both = NS(action_type=ActionType.SEND_CHAT_MESSAGE, reply_targets=["twitch", "youtube"])
    assert cx.get_action_display_name(both) == "Send chat message → Twitch + YouTube"
    alert = NS(action_type=ActionType.TRIGGER_ALERT, alert_type="raids")
    assert cx.get_action_display_name(alert) == "Play an alert: Raid"
    obs = NS(action_type=ActionType.OBS_CONTROL, operation="set_program_scene")
    assert "scene" in cx.get_action_display_name(obs).lower()
