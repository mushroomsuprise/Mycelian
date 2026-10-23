"""RT-08 / RT-13: hidden Chatbot panels skip refresh work and catch up when shown."""

from __future__ import annotations

from modules.help_system import contextual_help as ch
from modules.uiwindows import tab_visibility as tv


class FakeTabs:
    def __init__(self, value):
        self.value = value
        self.handlers = []

    def on_value_change(self, handler):
        self.handlers.append(handler)

    def set(self, value):
        self.value = value
        for handler in self.handlers:
            handler(None)


def _setup(monkeypatch, main="Chatbot", sub="Commands"):
    main_tabs, chatbot_tabs = FakeTabs(main), FakeTabs(sub)
    monkeypatch.setattr(ch, "_main_tabs", main_tabs)
    monkeypatch.setattr(ch, "_chatbot_tabs", chatbot_tabs)
    monkeypatch.setattr(tv, "_hooked_tabs", [])
    monkeypatch.setattr(tv, "_show_callbacks", {})
    return main_tabs, chatbot_tabs


def test_hidden_only_when_another_known_tab_shows(monkeypatch):
    main_tabs, chatbot_tabs = _setup(monkeypatch)
    assert tv.chatbot_subtab_hidden("Giveaways")
    chatbot_tabs.value = "Giveaways"
    assert not tv.chatbot_subtab_hidden("Giveaways")
    main_tabs.value = "Settings"
    assert tv.chatbot_subtab_hidden("Giveaways")
    # Unknown labels never hide a panel.
    main_tabs.value = "Something New"
    assert not tv.chatbot_subtab_hidden("Giveaways")
    main_tabs.value = "Chatbot"
    chatbot_tabs.value = "Unknown Sub"
    assert not tv.chatbot_subtab_hidden("Giveaways")


def test_no_tab_strips_means_visible(monkeypatch):
    monkeypatch.setattr(ch, "_main_tabs", None)
    monkeypatch.setattr(ch, "_chatbot_tabs", None)
    assert not tv.chatbot_subtab_hidden("Polls")


def test_shown_callback_runs_on_show_only(monkeypatch):
    main_tabs, chatbot_tabs = _setup(monkeypatch, main="Activity Feed", sub="Giveaways")
    calls = []
    tv.on_chatbot_subtab_shown("Giveaways", lambda: calls.append("g"))
    tv.on_chatbot_subtab_shown("Polls", lambda: calls.append("p"))
    main_tabs.set("Settings")
    assert calls == []
    main_tabs.set("Chatbot")
    assert calls == ["g"]
    chatbot_tabs.set("Polls")
    assert calls == ["g", "p"]
    # Re-registering replaces the callback and never double-hooks a strip.
    tv.on_chatbot_subtab_shown("Polls", lambda: calls.append("p2"))
    chatbot_tabs.set("Polls")
    assert calls == ["g", "p", "p2"]
    assert len(main_tabs.handlers) == 1 and len(chatbot_tabs.handlers) == 1
