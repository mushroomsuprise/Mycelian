#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Channel points currency icon selection from Twitch GQL settings."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.twitch import (  # noqa: E402
    _CHANNEL_POINTS_ICON_GQL,
    _icon_from_channel_points_gql_response,
    _icon_from_channel_points_settings,
)

CUSTOM = {
    "url": "https://static-cdn.jtvnw.net/channel-points-icons/custom-1.png",
    "url2x": "https://static-cdn.jtvnw.net/channel-points-icons/custom-2.png",
    "url4x": "https://static-cdn.jtvnw.net/channel-points-icons/custom-4.png",
}
GENERIC = {
    "url": "https://static-cdn.jtvnw.net/channel-points-icons/default-1.png",
    "url2x": "https://static-cdn.jtvnw.net/channel-points-icons/default-2.png",
    "url4x": "https://static-cdn.jtvnw.net/channel-points-icons/default-4.png",
}


def _payload(settings):
    return [{"data": {"channel": {"communityPointsSettings": settings}}}]


class ChannelPointsIconTests(unittest.TestCase):
    def test_query_reads_community_points_settings(self) -> None:
        self.assertIn("communityPointsSettings", _CHANNEL_POINTS_ICON_GQL)
        self.assertNotIn("communityPoints {", _CHANNEL_POINTS_ICON_GQL)

    def test_custom_icon_wins_over_default(self) -> None:
        icon = _icon_from_channel_points_settings(
            {"image": CUSTOM, "defaultImage": GENERIC}
        )
        self.assertEqual(icon["url_1x"], CUSTOM["url"])
        self.assertEqual(icon["url_4x"], CUSTOM["url4x"])

    def test_null_custom_icon_uses_default(self) -> None:
        icon = _icon_from_channel_points_gql_response(
            _payload({"image": None, "defaultImage": GENERIC})
        )
        self.assertEqual(icon["url_1x"], GENERIC["url"])
        self.assertEqual(icon["url_2x"], GENERIC["url2x"])

    def test_graphql_errors_with_empty_data_are_not_an_icon(self) -> None:
        icon = _icon_from_channel_points_gql_response(
            [
                {
                    "errors": [{"message": "Cannot query field 'communityPoints'"}],
                    "data": {"channel": None},
                }
            ]
        )
        self.assertIsNone(icon)

    def test_obsolete_settings_path_is_ignored(self) -> None:
        icon = _icon_from_channel_points_gql_response(
            [
                {
                    "data": {
                        "channel": {
                            "communityPoints": {
                                "settings": {"image": CUSTOM, "defaultImage": GENERIC}
                            }
                        }
                    }
                }
            ]
        )
        self.assertIsNone(icon)


if __name__ == "__main__":
    unittest.main()
