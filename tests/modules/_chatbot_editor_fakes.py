# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Shared fakes for the chatbot_editor test files (not collected: no ``test_`` prefix)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from nicegui import ui
from nicegui.testing.user_interaction import UserInteraction

from modules import chatbot_custom_variables as ccv
from modules import chatbot_variables as cv
from modules.chatbot_core import ChatCommand, ChatEvent
from modules.uiwindows import chatbot_editor as ce


class FakeManager:
    def __init__(self):
        self.commands = {}
        self.events = {}
        self.calls = []
        self.validate_message = None
        self.save_ok = True
        self.save_error = None
        self.reset_ok = True
        self.test_result = {"ok": True, "triggered": True, "response": "hi"}

    def get_command(self, cid):
        return self.commands.get(cid)

    def get_event(self, eid):
        return self.events.get(eid)

    def get_all_commands(self):
        return dict(self.commands)

    def validate_command(self, item):
        self.calls.append(("validate_command", item.command_name))
        if isinstance(self.validate_message, Exception):
            raise self.validate_message
        return self.validate_message

    def validate_event(self, item):
        self.calls.append(("validate_event", item.name))
        return self.validate_message

    def _save(self, name, *args, **kw):
        self.calls.append((name, args, kw))
        if self.save_error:
            raise self.save_error
        return self.save_ok

    def add_command(self, item):
        return self._save("add_command", item)

    def update_command(self, cid, item, keep_runtime=True):
        return self._save("update_command", cid, item, keep_runtime=keep_runtime)

    def add_event(self, item):
        return self._save("add_event", item)

    def update_event(self, eid, item):
        return self._save("update_event", eid, item)

    def reset_usage(self, cid):
        self.calls.append(("reset_usage", cid))
        return self.reset_ok

    def test_command(self, cid):
        self.calls.append(("test_command", cid))
        return self.test_result

    def test_event(self, eid):
        self.calls.append(("test_event", eid))
        return self.test_result


class Notes:
    def __init__(self):
        self.calls = []

    def __call__(self, message="", **kw):
        self.calls.append((message, kw))

    @property
    def titles(self):
        return [kw.get("title") for _m, kw in self.calls]

    @property
    def messages(self):
        return [m for m, _kw in self.calls]

    @property
    def types(self):
        return [kw.get("type") for _m, kw in self.calls]


@pytest.fixture
def ed_env(monkeypatch):
    mgr = FakeManager()
    notes = Notes()
    monkeypatch.setattr(ce, "get_manager", lambda: mgr)
    monkeypatch.setattr(ce, "notify", notes)
    defs = {}
    saved = []
    monkeypatch.setattr(ccv, "load_custom_definitions", lambda: dict(defs))
    monkeypatch.setattr(ccv, "load_custom_variables", lambda: dict(defs))
    # Real previews read live statistics; the editor only needs "text in, text out".
    previews = []

    def render_preview(text, kind, event_type=None, item=None):
        previews.append((text, kind, event_type))
        return text.replace("{username}", "MossyMika")

    monkeypatch.setattr(cv, "render_preview", render_preview)
    tasks = []
    monkeypatch.setattr(ce, "background_tasks", SimpleNamespace(create=lambda coro, name=None: tasks.append((name, coro))))
    yield SimpleNamespace(mgr=mgr, notes=notes, defs=defs, saved=saved, tasks=tasks, previews=previews)
    for _name, coro in tasks:
        coro.close()


def cmd(cid="c1", word="hello", **kw):
    kw.setdefault("response_text", "Hi {username}")
    return ChatCommand(command_id=cid, name=kw.pop("name", word.title()), command_name=word, **kw)


def evt(eid="e1", etype="follow", **kw):
    kw.setdefault("response_text", "Thanks {username}")
    return ChatEvent(event_id=eid, name=kw.pop("name", "Ev " + eid), event_type=etype, **kw)


def click(user, element):
    UserInteraction(user, {element}, None).click()


def buttons(user, icon=None, text=None):
    out = []
    for el in user.find(kind=ui.button).elements:
        if icon is not None and el.props.get("icon") != icon:
            continue
        if text is not None and getattr(el, "text", None) != text:
            continue
        out.append(el)
    return sorted(out, key=lambda e: e.id)


def labels(user):
    return [e.text for e in sorted(user.find(kind=ui.label).elements, key=lambda e: e.id)]


def label_click(user, text, index=0):
    """Click the clickable parent of the label showing *text* (segments, pickers, add rows)."""
    matches = [e for e in sorted(user.find(kind=ui.label).elements, key=lambda e: e.id) if e.text == text]
    target = matches[index]
    node = target
    while node is not None and not any(l.type == "click" for l in node._event_listeners.values()):
        node = node.parent_slot.parent if node.parent_slot else None
    assert node is not None, f"nothing clickable around {text!r}"
    click(user, node)


def set_value(user, element, value):
    with user.client:
        element.set_value(value)


def inputs(user):
    return sorted((e for e in user.find(kind=ui.input).elements if not isinstance(e, ui.textarea)), key=lambda e: e.id)


def htmls(user):
    return " ".join(str(e.content) for e in user.find(kind=ui.html).elements)
