# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""OBS connector action catalog — operations and argument metadata for the Connectors UI.

Scene/source names populate from :mod:`modules.obs_service` snapshots at UI time.
"""

from __future__ import annotations

from typing import Any, Dict, List

# OBS scene item transform — snake_case kwargs map to OBS camelCase in obs_service.

_TRANSFORM_FIELDS: List[Dict[str, Any]] = [
    {
        "name": "merge_with_current_transform",
        "type": "text",
        "label": "Merge with current transform",
        "control": "select",
        "options": {"1": "Yes (recommended)", "0": "No — only supplied fields"},
    },
    {"name": "position_x", "type": "text", "label": "positionX"},
    {"name": "position_y", "type": "text", "label": "positionY"},
    {"name": "rotation", "type": "text", "label": "rotation"},
    {"name": "scale_x", "type": "text", "label": "scaleX"},
    {"name": "scale_y", "type": "text", "label": "scaleY"},
    {"name": "alignment", "type": "text", "label": "alignment (OBS align int)"},
    {
        "name": "bounds_type",
        "type": "text",
        "label": "boundsType (e.g. OBS_BOUNDS_NONE)",
    },
    {
        "name": "bounds_alignment",
        "type": "text",
        "label": "boundsAlignment",
    },
    {"name": "bounds_width", "type": "text", "label": "boundsWidth"},
    {"name": "bounds_height", "type": "text", "label": "boundsHeight"},
    {"name": "crop_left", "type": "text", "label": "cropLeft"},
    {"name": "crop_top", "type": "text", "label": "cropTop"},
    {"name": "crop_right", "type": "text", "label": "cropRight"},
    {"name": "crop_bottom", "type": "text", "label": "cropBottom"},
    {"name": "width", "type": "text", "label": "width"},
    {"name": "height", "type": "text", "label": "height"},
]

OBS_CONNECTOR_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "set_program_scene",
        "label": "OBS — Set program scene",
        "description": "Switch OBS program (live) output scene.",
        "args": [
            {
                "name": "scene_name",
                "type": "text",
                "label": "Scene",
                "control": "select",
                "dynamic": "scene",
            },
        ],
    },
    {
        "id": "set_preview_scene",
        "label": "OBS — Set preview scene",
        "description": "Switch studio-mode preview scene.",
        "args": [
            {
                "name": "scene_name",
                "type": "text",
                "label": "Scene",
                "control": "select",
                "dynamic": "scene",
            },
        ],
    },
    {
        "id": "set_source_enabled",
        "label": "OBS — Show/hide scene item",
        "description": "Enable or disable a source within a scene.",
        "args": [
            {
                "name": "scene_name",
                "type": "text",
                "label": "Scene",
                "control": "select",
                "dynamic": "scene",
            },
            {
                "name": "source_name",
                "type": "text",
                "label": "Source",
                "control": "select",
                "dynamic": "scene_item",
                "scene_arg": "scene_name",
            },
            {
                "name": "enabled",
                "type": "text",
                "label": "Visible",
                "control": "select",
                "options": {"true": "Show", "false": "Hide"},
            },
            {
                "name": "search_offset",
                "type": "text",
                "label": "Item search offset",
                "hint": "Use when duplicate sources exist (usually 0 or empty).",
            },
        ],
    },
    {
        "id": "toggle_source",
        "label": "OBS — Toggle scene item visibility",
        "description": "Flip enabled state for a scene item.",
        "args": [
            {
                "name": "scene_name",
                "type": "text",
                "label": "Scene",
                "control": "select",
                "dynamic": "scene",
            },
            {
                "name": "source_name",
                "type": "text",
                "label": "Source",
                "control": "select",
                "dynamic": "scene_item",
                "scene_arg": "scene_name",
            },
            {
                "name": "search_offset",
                "type": "text",
                "label": "Item search offset",
            },
        ],
    },
    {
        "id": "set_source_transform",
        "label": "OBS — Set scene item transform",
        "description": "Updates transform/crop fields. Uses merge unless merge_with_current=no.",
        "args": [
            {
                "name": "scene_name",
                "type": "text",
                "label": "Scene",
                "control": "select",
                "dynamic": "scene",
            },
            {
                "name": "source_name",
                "type": "text",
                "label": "Source",
                "control": "select",
                "dynamic": "scene_item",
                "scene_arg": "scene_name",
            },
            {
                "name": "search_offset",
                "type": "text",
                "label": "Item search offset",
            },
            *_TRANSFORM_FIELDS,
        ],
    },
    {
        "id": "set_input_mute",
        "label": "OBS — Set input mute",
        "description": "Mute or unmute an audio input.",
        "args": [
            {
                "name": "input_name",
                "type": "text",
                "label": "Input",
                "control": "select",
                "dynamic": "input",
            },
            {
                "name": "muted",
                "type": "text",
                "label": "Muted",
                "control": "select",
                "options": {"true": "Muted", "false": "Unmuted"},
            },
        ],
    },
    {
        "id": "toggle_input_mute",
        "label": "OBS — Toggle input mute",
        "description": "Toggle mute for an audio input.",
        "args": [
            {
                "name": "input_name",
                "type": "text",
                "label": "Input",
                "control": "select",
                "dynamic": "input",
            },
        ],
    },
    {
        "id": "start_stream",
        "label": "OBS — Start stream",
        "description": "Start OBS streaming.",
        "args": [],
    },
    {
        "id": "stop_stream",
        "label": "OBS — Stop stream",
        "description": "Stop OBS streaming.",
        "args": [],
    },
    {
        "id": "toggle_stream",
        "label": "OBS — Toggle stream",
        "description": "Toggle OBS streaming output.",
        "args": [],
    },
    {
        "id": "start_record",
        "label": "OBS — Start recording",
        "description": "Start OBS recording.",
        "args": [],
    },
    {
        "id": "stop_record",
        "label": "OBS — Stop recording",
        "description": "Stop OBS recording.",
        "args": [],
    },
    {
        "id": "toggle_record",
        "label": "OBS — Toggle recording",
        "description": "Toggle OBS recording.",
        "args": [],
    },
]


def obs_connector_catalog_by_id() -> Dict[str, Dict[str, Any]]:
    return {entry["id"]: entry for entry in OBS_CONNECTOR_CATALOG}
