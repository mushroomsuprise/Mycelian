# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Streamlabs / StreamElements marks on donation rows in the Activity Feed."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import donation_alerts  # noqa: E402
from modules.uiwindows import activity_feed as feed  # noqa: E402
from modules.uiwindows import activity_feed_model as model  # noqa: E402


def _payload(*args, **kwargs):
    with (
        patch.object(feed.alertutils.alert_state_manager, "initialize"),
        patch.object(feed.alertutils.alert_state_manager, "get_stored_alert_by_id", return_value=None),
        patch.object(feed.alertutils, "resolve_chat_alert_media", return_value=None),
    ):
        return feed.build_activity_feed_alert_payload(*args, **kwargs)


def _donation(**extra):
    return model.feed_row_model({"type": "Donation", "badge_type": "donation", "username": "Ann",
                                 "message": "Ann donated $5.00!", **extra})


def test_donation_rows_carry_the_tip_service():
    assert _donation(donation_source="streamlabs")["platform"] == "streamlabs"
    assert _donation(donation_source="StreamElements")["platform"] == "streamelements"
    assert _donation(source="streamlabs")["platform"] == "streamlabs"
    # history rows only have the stored alert
    assert _donation(stored_alert_data={"donation_source": "streamelements"})["platform"] == "streamelements"


def test_other_donation_sources_get_no_mark():
    assert _donation()["platform"] == ""
    assert _donation(donation_source="connector")["platform"] == ""
    sub = model.feed_row_model({"type": "Sub", "badge_type": "sub", "username": "A", "tier": 1})
    assert sub["platform"] == "twitch"


def test_payload_keeps_donation_source():
    payload = _payload("Donation", "Ann donated $5.00!", "donation", username="Ann", alert_id="D1",
                       donation_source="streamlabs")
    assert payload["donation_source"] == "streamlabs"
    assert payload["row"]["platform"] == "streamlabs"


def test_donation_alerts_pass_the_source_to_the_feed():
    alert = SimpleNamespace(username="Ann", timestamp=1.0, message="hi", alert_id="D1",
                            donation_source="streamelements")
    with patch.object(feed, "add_alert_to_feed") as add:
        donation_alerts._send_feed(alert, "$5.00")
    assert add.call_args.kwargs["donation_source"] == "streamelements"


_NODE_HARNESS = r"""
global.window = global;
global.document = { querySelectorAll: function () { return []; }, addEventListener: function () {} };
require(process.argv[2]);
var F = window.MycelianFeed;
function plat(extra) {
  var a = Object.assign({ type: 'Donation', badge_type: 'donation', username: 'ann', message: 'ann donated' }, extra);
  return F.rowFromAlert(a).platform;
}
console.log(JSON.stringify({
  sl: plat({ donation_source: 'streamlabs' }),
  se: plat({ source: 'streamelements' }),
  stored: plat({ stored_alert_data: { donation_source: 'streamlabs' } }),
  none: plat({}),
  sub: F.rowFromAlert({ type: 'Sub', badge_type: 'sub', username: 'a', message: 'a subscribed' }).platform
}));
process.exit(0);
"""


def test_renderer_marks_tip_services(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    harness = tmp_path / "h.js"
    harness.write_text(_NODE_HARNESS, encoding="utf-8")
    js = ROOT / "assets" / "default_assets" / "activity_feed.js"
    done = subprocess.run([node, str(harness), str(js)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"sl": "streamlabs", "se": "streamelements", "stored": "streamlabs",
                                       "none": "", "sub": "twitch"}
    source = js.read_text(encoding="utf-8")
    assert 'aria-label="Streamlabs"' in source and 'aria-label="StreamElements"' in source
    assert "streamlabs: SL, streamelements: SE" in source
