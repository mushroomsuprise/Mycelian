# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/overlay_recovery_inject.py (script injection, io.connect rewrite)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.spore_studio import overlay_recovery_inject as inj  # noqa: E402

SIO = '<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>'
REC = inj._OVERLAY_RECOVERY_SCRIPT
AQ = inj._ALERT_QUEUE_SCRIPT
LOG = inj._TEMPLATE_LOGGER_SCRIPT


def test_injects_after_socket_io_tag_in_order():
    html = f"<html><head>{SIO}</head><body></body></html>"
    out = inj.inject_overlay_recovery(html)
    # The logger is inserted right after the recovery script, ahead of the queue script.
    assert out.index(SIO) < out.index(REC) < out.index(LOG) < out.index(AQ)
    assert out.count(REC) == 1 and out.count(AQ) == 1 and out.count(LOG) == 1


def test_socket_io_tag_match_is_case_insensitive_and_only_first():
    tag = SIO.replace("<script", "<SCRIPT").replace("</script>", "</SCRIPT>")
    out = inj.inject_overlay_recovery(f"<head>{tag}{tag}</head>")
    assert out.count(REC) == 1
    assert out.index(REC) > out.index(tag)


def test_falls_back_to_head_close():
    out = inj.inject_overlay_recovery("<html><head><title>x</title></head></html>")
    assert f"    {REC}" in out and AQ in out and LOG in out
    assert out.index(REC) < out.index("</head>")


def test_no_head_no_socket_tag_only_rewrites():
    html = "<div>plain</div>"
    assert inj.inject_overlay_recovery(html) == html


def test_existing_recovery_script_is_not_duplicated():
    html = f"<head>{REC}</head>"
    out = inj.inject_overlay_recovery(html)
    assert out.count(REC) == 1
    assert AQ in out and LOG in out


def test_custom_recovery_reference_still_gets_logger_in_head():
    # Mentions overlay_recovery.js but not the exact canonical tag: the alert-queue
    # replace finds nothing to anchor to and the logger goes before </head>.
    html = '<head><script src="/x/overlay_recovery.js?v=2"></script></head>'
    out = inj.inject_overlay_recovery(html)
    assert AQ not in out
    assert f"    {LOG}\n</head>" in out


def test_custom_recovery_without_head_gets_nothing_extra():
    html = '<script src="/x/overlay_recovery.js?v=2"></script>'
    assert inj.inject_overlay_recovery(html) == html


def test_already_complete_page_is_unchanged():
    html = f"<head>{REC}{AQ}{LOG}</head>"
    assert inj.inject_overlay_recovery(html) == html


def test_rewrites_io_connect_variants():
    html = (
        "<head></head><script>"
        "var socket = io.connect('http://' + document.domain + ':' + location.port);\n"
        "const socket = io.connect('http://' + document.domain + ':' + location.port, {transports: ['websocket']});\n"
        "broadcastSocket = io.connect('http://' + document.domain + ':' + location.port);\n"
        "let other = io.connect('http://' + document.domain + ':' + location.port);\n"
        "</script>"
    )
    out = inj.inject_overlay_recovery(html)
    target = "MycelianOverlay.connect('http://' + document.domain + ':' + location.port)"
    assert f"var socket = {target};" in out
    assert f"const socket = {target};" in out
    assert f"broadcastSocket = {target};" in out
    # Only `socket` variables are rewritten.
    assert "let other = io.connect(" in out
    assert "transports" not in out
